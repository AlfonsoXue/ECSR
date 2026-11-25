"""純文字解析：不依賴瀏覽器，方便單元測試。

系統畫面的 HTML 結構無法事先得知，因此這裡一律以「畫面上看得到的文字」
（頁籤名稱、欄位標題、案號格式）為依據，而不是 CSS 選擇器。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable, Optional

from .models import IRRow

REF_RE = re.compile(r"^([A-Z0-9]+)/([A-Z0-9]{3,6})/(\d{8})$")
DATE_RE = re.compile(r"\d{4}/\d{1,2}/\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?")
DEPT_RE = re.compile(r"^[A-Z]{3}[A-Z0-9]{2}$")
IR_NO_RE = re.compile(r"^IR\d{2,4}$", re.I)
PAX_LINE_RE = re.compile(r"^\d+\.\s*[A-Z][A-Z \-'*]*/\s*[A-Za-z]+\.?$")

RELATED_LABELS = [
    ("Investigate & Report Dept", "Investigate & Report Dept."),
    ("Handle & Reply Dept", "Handle & Reply Dept."),
    ("For Reference Only", "For Reference Only"),
]


def parse_ref(ref: str) -> Optional[tuple]:
    """'ECSR/TPEWS/26008261' -> ('ECSR', 'TPEWS', '26008261')；格式不符回傳 None。"""
    m = REF_RE.match((ref or "").strip().upper())
    return m.groups() if m else None


def parse_dt(s: str) -> Optional[datetime]:
    m = DATE_RE.search(s or "")
    if not m:
        return None
    raw = re.sub(r"\s+", " ", m.group(0))
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _clean(text: str) -> str:
    text = (text or "").replace("\r", "").replace("\xa0", " ")
    lines = [ln.strip() for ln in text.split("\n")]
    out, blank = [], 0
    for ln in lines:
        if ln:
            blank = 0
            out.append(ln)
        else:
            blank += 1
            if blank == 1 and out:
                out.append("")
    return "\n".join(out).strip()


def _has_token(text: str, token: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", text or "") is not None


def after_tab_bar(text: str, last_tab: str = "Approval") -> str:
    """去掉頁首（案號、按鈕、頁籤列），頁籤列最後一個是 Approval。"""
    i = text.find(last_tab)
    return text[i + len(last_tab):] if 0 <= i < 1500 else text


# ---------------------------------------------------------------- Related Station
def related_station_info(text: str, target: str) -> tuple:
    """回傳 (是否出現 target, 所在區塊說明)。"""
    body = after_tab_bar(text)
    if not _has_token(body, target):
        return False, ""
    marks = []
    for key, label in RELATED_LABELS:
        i = body.find(key)
        if i >= 0:
            marks.append((i, label))
    marks.sort()
    where = []
    for n, (pos, label) in enumerate(marks):
        end = marks[n + 1][0] if n + 1 < len(marks) else len(body)
        if _has_token(body[pos:end], target):
            where.append(label)
    return True, "、".join(where)


# ---------------------------------------------------------------- Outline
def cut_outline(main_text: str) -> str:
    body = after_tab_bar(main_text)
    m = re.search(r"^\s*CSR Ref\.?\s*$", body, re.M)
    if m:
        body = body[: m.start()]
    lines = [ln for ln in body.split("\n")
             if not re.fullmatch(r"\s*Outline\s*&\s*Causes\s*\*?\s*|\s*\*\s*", ln)]
    return _clean("\n".join(lines))


# ---------------------------------------------------------------- IR/CPN 清單
def _is_depts(cell: str) -> bool:
    toks = re.split(r"[\s,;/]+", cell.strip())
    return bool(toks) and all(DEPT_RE.match(t) for t in toks if t) and any(toks)


def ir_row_from_cells(cells: Iterable[str]) -> Optional[IRRow]:
    cells = [re.sub(r"[ \t]+", " ", (c or "").replace("\xa0", " ")).strip() for c in cells]
    if not cells or not IR_NO_RE.match(cells[0]):
        return None
    row = IRRow(ir_no=cells[0].upper())
    d = next((i for i, c in enumerate(cells) if i > 0 and DATE_RE.fullmatch(c)), None)
    if d is None:
        row.creator = " ".join(c.replace("\n", " ") for c in cells[1:2])
        return row
    row.creator = " ".join(c.replace("\n", " ") for c in cells[1:d] if c)
    row.approved = cells[d]
    rest = cells[d + 1:]
    if rest and _is_depts(rest[0]):
        row.create_dept = rest[0]
        rest = rest[1:]
    if rest and _is_depts(rest[-1]):
        row.report_dept = " ".join(re.split(r"[\s,;/]+", rest[-1]))
        rest = rest[:-1]
    row.pax = "\n".join(c for c in rest if c)
    return row


def ir_rows_from_text(text: str) -> list:
    """備援：畫面不是 <table> 時，從純文字切出每一列。"""
    cells = [c.strip() for c in re.split(r"[\t\n]", text or "")]
    cells = [c for c in cells if c]
    starts = [i for i, c in enumerate(cells) if IR_NO_RE.match(c)]
    rows = []
    for n, s in enumerate(starts):
        e = starts[n + 1] if n + 1 < len(starts) else len(cells)
        chunk = cells[s:e]
        d = next((i for i, c in enumerate(chunk) if i > 0 and DATE_RE.fullmatch(c)), None)
        if d is None:
            continue
        # 最後一列之後可能接著其他區塊文字：只保留姓名與單位代碼
        tail = []
        for c in chunk[d + 1:]:
            if _is_depts(c) or PAX_LINE_RE.match(c):
                tail.append(c)
            else:
                break
        merged = [chunk[0], " ".join(chunk[1:d]), chunk[d]] + tail
        row = ir_row_from_cells(merged)
        if row:
            rows.append(row)
    return rows


def latest_request(rows: list, target: str) -> Optional[IRRow]:
    """Investigate & Report Dept. 含 target 的最新一筆 IR。"""
    hits = [(n, r) for n, r in enumerate(rows) if target in r.report_dept.split()]
    if not hits:
        return None
    return max(hits, key=lambda t: (parse_dt(t[1].approved) or datetime.min, t[0]))[1]


def reply_status(rows: list, target: str, request: IRRow) -> str:
    """由清單推定：target 在該筆 IR 之後是否有建立回覆 IR。"""
    req_dt = parse_dt(request.approved)
    replies = []
    for r in rows:
        if r.create_dept != target:
            continue
        dt = parse_dt(r.approved)
        if req_dt and dt and dt > req_dt:
            replies.append((dt, r))
    if not replies:
        return f"待回覆（{request.ir_no} 之後尚無 {target} 建立的 IR）"
    dt, r = min(replies, key=lambda t: t[0])
    return f"已回覆（{r.ir_no}，{r.approved}，{r.creator}）"


# ---------------------------------------------------------------- IR 內容
def cut_ir_result(main_text: str) -> str:
    body = after_tab_bar(main_text)
    m = re.search(r"^\s*Source No Ref\.?\s*$", body, re.M)
    if m:
        body = body[: m.start()]
    lines = body.split("\n")
    out, head = [], True
    for ln in lines:
        s = ln.strip()
        if head and (not s or re.fullmatch(r"PAX Info\.?", s) or PAX_LINE_RE.match(s)):
            continue
        head = False
        out.append(ln)
    return _clean("\n".join(out))


def pick_longest(candidates: Iterable[str], min_len: int = 20) -> str:
    best = ""
    for c in candidates:
        c = _clean(c)
        if len(c) >= min_len and len(c) > len(best):
            best = c
    return best
