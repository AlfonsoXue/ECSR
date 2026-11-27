"""批次流程：逐筆查詢 → 摘要 → 存檔。GUI 與命令列共用。"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Callable

from . import excel_io
from .automation import EcsrBot, FlowError, StopRequested, _short
from .config import Config
from .models import ST_DONE, ST_ERROR, Case, CaseResult
from .summarizer import Summarizer


class Runner:
    def __init__(self, cfg: Config, bot: EcsrBot, xlsx_path: str,
                 log: Callable[[str], None] = print,
                 on_case: Callable[[str, CaseResult], None] = lambda phase, res: None):
        self.cfg, self.bot, self.log, self.on_case = cfg, bot, log, on_case
        self.xlsx_path = xlsx_path
        self.out_xlsx, self.progress_path = excel_io.output_paths(xlsx_path)
        self.results = excel_io.load_progress(self.progress_path)
        self.summarizer = Summarizer(cfg, log)

    def save(self) -> bool:
        excel_io.save_progress(self.progress_path, self.results)
        try:
            excel_io.write_results(self.xlsx_path, self.out_xlsx, self.cfg.sheet_name,
                                   self.results, self.cfg.target_station, self.cfg.address)
            return True
        except PermissionError:
            self.log("⚠ 結果檔正被 Excel 開啟，無法寫入；請關閉後按「重新輸出結果」。（進度已另外保存）")
            return False

    def run(self, cases: list) -> dict:
        """回傳各狀態筆數。被停止時也會把已完成的部分存檔。"""
        counts: dict = {}
        total = len(cases)
        fails_in_a_row = 0
        try:
            for n, case in enumerate(cases, 1):
                self.bot._check()
                self.log(f"[{n}/{total}] {case.ref}")
                self.on_case("start", CaseResult(row=case.row, ref=case.ref))
                res = self._one(case)
                res.processed_at = datetime.now().strftime("%Y/%m/%d %H:%M")
                self.results[case.ref] = res
                counts[res.state] = counts.get(res.state, 0) + 1
                self.save()
                self.on_case("done", res)
                self.log(f"   → {res.state}" + (f"：{res.note}" if res.note else ""))
                fails_in_a_row = fails_in_a_row + 1 if res.state == ST_ERROR else 0
                if fails_in_a_row >= 3:
                    self.log("✖ 連續 3 筆失敗，自動停止。請確認瀏覽器仍在登入狀態、畫面是否改版。")
                    break
                if n < total:
                    self.bot._sleep(self.cfg.delay_sec)
        except StopRequested:
            self.log("■ 已停止。")
        return counts

    def _one(self, case: Case) -> CaseResult:
        try:
            res = self.bot.process_case(case.row, case.ref)
        except StopRequested:
            raise
        except FlowError as e:
            return CaseResult(row=case.row, ref=case.ref, state=ST_ERROR, note=str(e))
        except Exception as e:
            return CaseResult(row=case.row, ref=case.ref, state=ST_ERROR, note=_short(e))
        if res.state == ST_DONE:
            t0 = time.time()
            if res.outline_text:
                res.outline_summary = self.summarizer.outline(res.ref, res.outline_text)
            if res.ir_text:
                res.ir_summary = self.summarizer.ir_status(
                    res.ref, res.ir_text, res.ir_no, res.ir_date, res.ir_from, res.reply_status)
            if res.outline_text or res.ir_text:
                self.log(f"   摘要完成（{time.time() - t0:.1f} 秒）")
        return res


def pending_cases(cases: list, results: dict, only_unfinished: bool = True) -> list:
    if not only_unfinished:
        return list(cases)
    return [c for c in cases if c.ref not in results or results[c.ref].state == ST_ERROR]
