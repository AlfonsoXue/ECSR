"""端對端測試：用模擬網站跑完整流程（查詢→Related Station→Outline→IR/CPN→IR）。"""
import glob
import os
import shutil

import pytest
from openpyxl import Workbook, load_workbook

from ecsr_tool import excel_io
from ecsr_tool.automation import EcsrBot
from ecsr_tool.config import Config
from ecsr_tool.models import ST_DONE, ST_NOT_FOUND, ST_SKIPPED
from ecsr_tool.runner import Runner, pending_cases

from . import mock_site

REFS = ["ECSR/TPEWS/26000001", "ECSR/TPEWS/26000002", "ECSR/BKKKP/26000003",
        "ECSR/TPEWS/26999999", "B7ECSR/TPEWS/260118", "ECSR/BAD"]


def _chromium():
    exe = os.environ.get("ECSR_TEST_BROWSER")
    if exe:
        return exe
    hits = glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome")
    return hits[0] if hits else ""


@pytest.fixture(scope="module")
def server():
    srv = mock_site.serve()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _xlsx(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "ECSR"
    ws.append(["CSR NO.", "電報日期", "PIC", "場站", "STATUS"])
    for r in REFS:
        ws.append([r, "2026/11/12", None, None, None])
    wb.create_sheet("A-01")
    p = tmp_path / "202611.xlsx"
    wb.save(p)
    return str(p)


@pytest.mark.parametrize("variant", ["a", "b"])
def test_full_flow(server, tmp_path, variant, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    exe = _chromium()      # Linux 用內建 Chromium；Windows（CI）直接用系統的 Edge
    cfg = Config(start_url=f"{server}/{variant}/", browser_channel="" if exe else "msedge",
                 headless=True, browser_executable=exe, timeout_sec=15, delay_sec=0.0)
    path = _xlsx(tmp_path)
    logs = []
    bot = EcsrBot(cfg, log=logs.append)
    try:
        bot.open()
        assert bot.confirm_search_page()
        runner = Runner(cfg, bot, path, log=logs.append)
        cases = excel_io.load_cases(path)
        assert [c.ref for c in cases] == REFS
        runner.run(pending_cases(cases, runner.results))
    finally:
        bot.close()
        shutil.rmtree(cfg.profile_dir, ignore_errors=True)
    res = runner.results
    print("\n".join(logs))

    r1 = res[REFS[0]]
    assert r1.state == ST_DONE and r1.related_found is True
    assert r1.related_where == "For Reference Only"
    assert "formal complaint" in r1.outline_text and "//Unquote//" in r1.outline_text
    assert "Outline & Causes" not in r1.outline_text and "Forward" not in r1.outline_text
    assert r1.ir_count == 6 and r1.ir_no == "IR004" and r1.ir_from == "LAXSR"
    assert r1.reply_status.startswith("已回覆（IR005")
    assert r1.ir_text.startswith("Dear TPEWT Team") and "Mtks/Brgds" in r1.ir_text
    assert "TESTER" not in r1.ir_text and "Source No Ref" not in r1.ir_text
    assert r1.outline_summary.startswith("〔原文節錄〕") and r1.ir_summary

    r2 = res[REFS[1]]
    assert r2.state == ST_DONE and r2.related_found is False
    assert r2.outline_text == "" and r2.ir_found is False and r2.ir_count == 1

    r3 = res[REFS[2]]
    assert r3.related_found is False and r3.outline_text == ""
    assert r3.ir_no == "IR001" and r3.reply_status.startswith("待回覆") and r3.ir_text

    assert res[REFS[3]].state == ST_NOT_FOUND
    assert res[REFS[4]].state == ST_SKIPPED and "前綴" in res[REFS[4]].note   # 立榮 B7ECSR
    assert res[REFS[5]].state == ST_SKIPPED

    wb = load_workbook(runner.out_xlsx)
    ws = wb["ECSR"]
    heads = [c.value for c in ws[1]]
    col = heads.index("處理結果") + 1
    assert ws.cell(2, col).value == ST_DONE and ws.cell(2, col + 1).value.startswith("有")
    assert "IR004" in ws.cell(2, col + 3).value
    assert wb["ECSR原文"].max_row == 3 and "A-01" in wb.sheetnames
    # 續跑：全部完成後不應再有待處理
    assert pending_cases(cases, excel_io.load_progress(runner.progress_path)) == []


def test_minet_export(tmp_path):
    """MINet II 電報匯出：由 Subject 解析與 TPEWTBR 有關的案號並合併。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "MINetII_Export"
    ws.append(["Type", "MSG ID", "SITA ID", "DateTime", "Send User", "Send Address", "Receive Address",
               "Send To", "Copy To", "Blind Copy", "Subject", "Text", "Priority"])
    def msg(when, rcv, to, subj, text=""):
        ws.append(["RCV", "1", "1", when, "APECSR", "TPEWSBR", rcv, to, None, None, subj, text, "QD"])
    msg("2026/11/27 11:39", "TPEWTBR", "TPEWSBR,TPEWTBR",
        "ECSR/TPEWS/26000001-IR004 IS WAITING FOR UR HANDLING -BR0056 TPEORD\n")
    msg("2026/11/26 09:00", "TPELZBR", "TPEWSBR, TPEWTBR,TPELZBR",
        "Urgent ECSR/TPEWS/26000001 IS WAITING FOR UR HANDLING -BR0056\nTPEORD\n")
    msg("2026/11/27 08:00", "TPELZBR", "TPEWSBR,TPELZBR",                    # 與 TPEWTBR 無關
        "ECSR/TPEWS/26000002 IS WAITING FOR UR HANDLING -BR0012 TPELAX\n")
    msg("2026/11/27 07:32", "TPEWTBR", "TPEWTBR",                            # 催辦：案號在內文
        "Customer Service Report is waiting for your handling. (IR_NotReplyIR)\n",
        "ATTN TPEWT\nRE ECSR/BKKKP/26000003-IR001 - BR0068 BKKTPE\nTHIS IR IS STILL WAITING")
    msg("2026/11/24 10:42", "TPEWTBR", "TPEWSBR,XMNKPB7,TPEWTBR",
        "Urgent B7ECSR/TPEWS/260119 IS WAITING FOR UR HANDLING -B70512 XMNTSA\n")
    msg("2026/11/27 12:19", "TPEWTBR", "TPEWTBR", "MVT BR708 27NOV CAN TPE DEP")
    msg("2026/11/27 12:20", "TPEWTBR", "TPEWTBR", None, "MVT")
    p = str(tmp_path / "MINetII_Export.xlsx")
    wb.save(p)

    assert excel_io.detect_format(p) == excel_io.FMT_MINET
    cases = excel_io.load_cases(p, address="TPEWTBR")
    assert [c.ref for c in cases] == ["ECSR/TPEWS/26000001", "ECSR/BKKKP/26000003", "B7ECSR/TPEWS/260119"]
    c = cases[0]
    assert (c.msg_count, c.date, c.flight, c.irs) == (2, "2026/11/27 11:39", "BR0056 TPEORD", "IR004")
    assert set(c.kinds.split()) == {"IR", "Urgent", "CSR"}
    assert cases[1].kinds == "催辦" and cases[1].irs == "IR001"
    assert cases[2].flight == "B70512 XMNTSA"

    from ecsr_tool.models import CaseResult
    out, _ = excel_io.output_paths(p)
    res = CaseResult(row=2, ref=cases[1].ref, state=ST_DONE, ir_no="IR001", ir_text="t", ir_summary="s")
    excel_io.write_results(p, out, "ECSR", {res.ref: res}, "TPEWT", "TPEWTBR")
    ws = load_workbook(out)["ECSR"]
    assert ws.max_row == 4 and ws.cell(3, 1).value == cases[1].ref and ws.cell(3, 7).value == ST_DONE
