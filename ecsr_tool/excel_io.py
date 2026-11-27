"""讀取 ECSR 頁籤、把結果另存成新的 xlsx（不會改動原始檔）。"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .models import Case, CaseResult

RESULT_HEADERS = [
    ("處理結果", 12),
    ("Related Station 有無目標單位", 22),
    ("案件摘要 (Outline)", 60),
    ("最新 IR", 30),
    ("回覆狀態", 34),
    ("最新處理狀態摘要", 60),
    ("備註", 30),
    ("處理時間", 18),
]
RAW_SHEET = "ECSR原文"
CELL_MAX = 32000


def _norm(s) -> str:
    return re.sub(r"[\s.]+", "", str(s or "")).upper()


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (datetime, date)):
        return v.strftime("%Y/%m/%d")
    return str(v).strip()


def list_sheets(path: str) -> list:
    wb = load_workbook(path, read_only=True)
    try:
        return wb.sheetnames
    finally:
        wb.close()


def find_sheet(names: list, wanted: str) -> str:
    for n in names:
        if n.strip().upper() == wanted.strip().upper():
            return n
    raise ValueError(f"找不到「{wanted}」頁籤（檔案內有：{'、'.join(names)}）")


def _header_map(ws) -> tuple:
    """回傳 (標題列號, {正規化標題: 欄號})。標題列 = 前 10 列中含 'CSR NO' 的那列。"""
    for r in range(1, min(ws.max_row, 10) + 1):
        heads = {_norm(c.value): c.column for c in ws[r] if c.value is not None}
        if "CSRNO" in heads:
            return r, heads
    raise ValueError("ECSR 頁籤找不到「CSR NO.」欄位標題")


FMT_ECSR, FMT_MINET = "ecsr", "minet"
MINET_HEADS = ("SUBJECT", "SENDTO", "RECEIVEADDRESS")
ADDR_COLS = ("RECEIVEADDRESS", "SENDTO", "COPYTO", "BLINDCOPY")


def _minet_sheet(wb):
    """MINet II 電報匯出：第一列標題含 Subject / Send To / Receive Address。"""
    for ws in wb.worksheets:
        first = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
        heads = {_norm(v): i for i, v in enumerate(first, 1) if v is not None}
        if all(h in heads for h in MINET_HEADS):
            return ws, heads
    return None, None


def detect_format(path: str) -> str:
    wb = load_workbook(path, read_only=True)
    try:
        return FMT_MINET if _minet_sheet(wb)[0] is not None else FMT_ECSR
    finally:
        wb.close()


def _load_minet(ws, heads, address: str) -> list:
    """從 Subject（K 欄）解析案號，只留收件地址含 address（如 TPEWTBR）的電報；同案號合併成一筆。

    催辦、Category Advice 這類主旨沒有案號的通知，改從內文（Text）取案號。
    """
    from . import parsing

    address = address.upper()
    c_subj, c_text, c_dt = heads["SUBJECT"], heads.get("TEXT"), heads.get("DATETIME")
    addr_cols = [heads[k] for k in ADDR_COLS if k in heads]
    cases: dict = {}
    for r, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
        cell = lambda c: "" if not c or row[c - 1] is None else str(row[c - 1])  # noqa: E731
        subject = cell(c_subj)
        addrs = {a.strip().upper() for c in addr_cols for a in cell(c).split(",")}
        if address not in addrs:
            continue
        hits = parsing.MSG_REF_RE.findall(subject.upper())
        if not hits:
            hits = parsing.MSG_REF_RE.findall(cell(c_text).upper())
        when = row[c_dt - 1] if c_dt else None
        when_s = when.strftime("%Y/%m/%d %H:%M") if isinstance(when, datetime) else _fmt(when)
        for ref, ir in dict.fromkeys(hits):
            case = cases.get(ref)
            if case is None:
                case = cases[ref] = Case(row=r, ref=ref, _kinds=[], _irs=[])
            case.msg_count += 1
            if when_s > case.date:
                case.date = when_s
            case.flight = case.flight or parsing.subject_flight(subject)
            for k in parsing.classify_subject(subject, bool(ir)).split():
                if k not in case._kinds:
                    case._kinds.append(k)
            if ir and ir not in case._irs:
                case._irs.append(ir)
    out = list(cases.values())
    for c in out:
        c.kinds, c.irs = " ".join(c._kinds), " ".join(sorted(c._irs))
    return out


def load_cases(path: str, sheet: str = "ECSR", address: str = "TPEWTBR") -> list:
    """自動判斷格式：MINet II 電報匯出，或含 ECSR 頁籤的月報。"""
    wb = load_workbook(path, data_only=True)
    try:
        ws, heads = _minet_sheet(wb)
        if ws is not None:
            return _load_minet(ws, heads, address)
        ws = wb[find_sheet(wb.sheetnames, sheet)]
        hrow, heads = _header_map(ws)
        col = lambda key: heads.get(_norm(key))  # noqa: E731
        c_ref, c_date, c_pic, c_stn, c_st = (
            col("CSR NO."), col("電報日期"), col("PIC"), col("場站"), col("STATUS"))
        cases = []
        for r in range(hrow + 1, ws.max_row + 1):
            ref = _fmt(ws.cell(r, c_ref).value)
            if not ref:
                continue
            get = lambda c: _fmt(ws.cell(r, c).value) if c else ""  # noqa: E731
            cases.append(Case(row=r, ref=ref, date=get(c_date), pic=get(c_pic),
                              station=get(c_stn), status=get(c_st)))
        return cases
    finally:
        wb.close()


def output_paths(src: str) -> tuple:
    p = Path(src)
    return (str(p.with_name(p.stem + "_摘要結果.xlsx")),
            str(p.with_name(p.stem + "_摘要進度.json")))


# ------------------------------------------------------------------ 進度檔
def load_progress(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for d in data.get("results", []):
        try:
            r = CaseResult.from_dict(d)
            out[r.ref] = r
        except TypeError:
            continue
    return out


def save_progress(path: str, results: dict) -> None:
    tmp = Path(path).with_suffix(".tmp")
    tmp.write_text(json.dumps({"results": [r.to_dict() for r in results.values()]},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


# ------------------------------------------------------------------ 結果檔
def _yn(v) -> str:
    return "" if v is None else ("有" if v else "無")


def _result_values(res: CaseResult, target: str) -> list:
    ir = ""
    if res.ir_no:
        ir = f"{res.ir_no}　{res.ir_date}\n{res.ir_from} → {target}"
    elif res.ir_found is False:
        ir = f"無指定 {target} 的 IR"
    related = _yn(res.related_found)
    if res.related_where:
        related += f"（{res.related_where}）"
    note = "；".join([x for x in [res.note] + list(res.warnings) if x])
    return [res.state, related, res.outline_summary, ir, res.reply_status,
            res.ir_summary, note, res.processed_at]


def write_results(src: str, dst: str, sheet: str, results: dict, target: str = "TPEWT",
                  address: str = "") -> None:
    """另存結果檔（不改動原始檔），原文另放一個頁籤。

    月報格式：以原檔為底，在 ECSR 頁籤右側加上結果欄位。
    電報匯出格式：新建活頁簿，每個案號一列。
    """
    head_fill = PatternFill("solid", fgColor="DDEBF7")
    wrap = Alignment(wrap_text=True, vertical="top")
    rows = []                                   # (ws 列號, 案號)
    if detect_format(src) == FMT_MINET:
        cases = load_cases(src, sheet, address or target + "BR")
        wb = Workbook()
        ws = wb.active
        ws.title = "ECSR"
        hrow = 1
        info = [("CSR NO.", 24), ("最新電報時間", 18), ("電報數", 8), ("航班", 16),
                ("電報類型", 18), ("電報提到的 IR", 16)]
        for i, (title, width) in enumerate(info, 1):
            ws.cell(1, i, title).font = Font(bold=True)
            ws.cell(1, i).fill = head_fill
            ws.column_dimensions[get_column_letter(i)].width = width
        for n, c in enumerate(cases, 2):
            for i, v in enumerate([c.ref, c.date, c.msg_count, c.flight, c.kinds, c.irs], 1):
                ws.cell(n, i, v).alignment = wrap
            rows.append((n, c.ref))
        first = len(info) + 1
        ws.freeze_panes = "B2"
    else:
        wb = load_workbook(src)
        ws = wb[find_sheet(wb.sheetnames, sheet)]
        hrow, heads = _header_map(ws)
        first = max(heads.values()) + 2          # 與原有欄位隔一欄
        for r in range(hrow + 1, ws.max_row + 1):
            rows.append((r, _fmt(ws.cell(r, heads["CSRNO"]).value)))

    for i, (title, width) in enumerate(RESULT_HEADERS):
        c = ws.cell(hrow, first + i, title.replace("目標單位", target))
        c.font, c.fill, c.alignment = Font(bold=True), head_fill, wrap
        ws.column_dimensions[get_column_letter(first + i)].width = width

    if RAW_SHEET in wb.sheetnames:
        del wb[RAW_SHEET]
    raw = wb.create_sheet(RAW_SHEET)
    raw.append(["CSR NO.", "Outline 原文", "IR No.", "IR 原文"])
    for c in raw[1]:
        c.font, c.fill = Font(bold=True), head_fill
    for col, w in zip("ABCD", (24, 100, 10, 100)):
        raw.column_dimensions[col].width = w

    for r, ref in rows:
        res = results.get(ref)
        if not res:
            continue
        for i, v in enumerate(_result_values(res, target)):
            ws.cell(r, first + i, (v or "")[:CELL_MAX]).alignment = wrap
        if res.outline_text or res.ir_text:
            raw.append([res.ref, res.outline_text[:CELL_MAX], res.ir_no, res.ir_text[:CELL_MAX]])
            for c in raw[raw.max_row]:
                c.alignment = wrap
    wb.save(dst)
