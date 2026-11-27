from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Optional

ST_PENDING = "待處理"
ST_DONE = "完成"
ST_NOT_FOUND = "查無案件"
ST_SKIPPED = "略過"
ST_ERROR = "錯誤"


@dataclass
class Case:
    row: int                 # Excel 列號
    ref: str                 # 例 ECSR/TPEWS/26008261
    date: str = ""
    pic: str = ""
    station: str = ""
    status: str = ""
    # MINet II 電報匯出格式才有
    msg_count: int = 0       # 相關電報數
    flight: str = ""         # 例 BR0018 TPESFO
    kinds: str = ""          # 電報類型：新案 / IR / Urgent / MODIFIED / 催辦…
    irs: str = ""            # 電報中提到的 IR 編號
    _kinds: list = field(default_factory=list, repr=False)
    _irs: list = field(default_factory=list, repr=False)


@dataclass
class IRRow:
    ir_no: str
    creator: str = ""
    approved: str = ""       # 原字串，例 2026/11/05 07:25
    create_dept: str = ""
    pax: str = ""
    report_dept: str = ""    # Investigate & Report Dept.（可能多個，以空白分隔）


@dataclass
class CaseResult:
    row: int
    ref: str
    state: str = ST_PENDING
    note: str = ""                    # 錯誤 / 略過原因
    processed_at: str = ""
    # Related Station
    related_found: Optional[bool] = None
    related_where: str = ""           # TPEWT 所在區塊
    # Outline
    outline_text: str = ""
    outline_summary: str = ""
    # IR/CPN
    ir_found: Optional[bool] = None
    ir_no: str = ""
    ir_date: str = ""
    ir_from: str = ""
    ir_text: str = ""
    ir_summary: str = ""
    reply_status: str = ""            # 目標單位是否已回覆（由 IR 清單推定）
    ir_count: int = 0
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CaseResult":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})
