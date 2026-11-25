"""讀取 ECSR 頁籤、把結果另存成新的 xlsx（不會改動原始檔）。"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook
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


def load_cases(path: str, sheet: str = "ECSR") -> list:
    wb = load_workbook(path, data_only=True)
    try:
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


def write_results(src: str, dst: str, sheet: str, results: dict, target: str = "TPEWT") -> None:
    """以原始檔為底，在 ECSR 頁籤右側加上結果欄位後另存；原文另放一個頁籤。"""
    wb = load_workbook(src)
    ws = wb[find_sheet(wb.sheetnames, sheet)]
    hrow, heads = _header_map(ws)
    c_ref = heads["CSRNO"]
    first = max(heads.values()) + 2          # 與原有欄位隔一欄
    head_fill = PatternFill("solid", fgColor="DDEBF7")
    wrap = Alignment(wrap_text=True, vertical="top")
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

    for r in range(hrow + 1, ws.max_row + 1):
        res = results.get(_fmt(ws.cell(r, c_ref).value))
        if not res:
            continue
        ir = ""
        if res.ir_no:
            ir = f"{res.ir_no}　{res.ir_date}\n{res.ir_from} → {target}"
        elif res.ir_found is False:
            ir = f"無指定 {target} 的 IR"
        related = _yn(res.related_found)
        if res.related_where:
            related += f"（{res.related_where}）"
        note = "；".join([x for x in [res.note] + list(res.warnings) if x])
        values = [res.state, related, res.outline_summary, ir, res.reply_status,
                  res.ir_summary, note, res.processed_at]
        for i, v in enumerate(values):
            c = ws.cell(r, first + i, (v or "")[:CELL_MAX])
            c.alignment = wrap
        if res.outline_text or res.ir_text:
            raw.append([res.ref, res.outline_text[:CELL_MAX], res.ir_no, res.ir_text[:CELL_MAX]])
            for c in raw[raw.max_row]:
                c.alignment = wrap
    wb.save(dst)
