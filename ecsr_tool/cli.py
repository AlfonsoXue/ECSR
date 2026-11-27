"""命令列模式（測試 / 排程用）：python -m ecsr_tool.cli 202611.xlsx"""
from __future__ import annotations

import argparse
import sys

from . import excel_io
from .automation import EcsrBot
from .config import Config
from .runner import Runner, pending_cases


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ECSR 案件自動查詢與摘要（命令列模式）")
    ap.add_argument("xlsx")
    ap.add_argument("--start-url")
    ap.add_argument("--channel")
    ap.add_argument("--executable")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--target")
    ap.add_argument("--limit", type=int, default=0, help="只處理前 N 筆")
    ap.add_argument("--all", action="store_true", help="已完成的也重跑")
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--timeout", type=int)
    ap.add_argument("--delay", type=float)
    a = ap.parse_args(argv)

    cfg = Config.load()
    if a.start_url:
        cfg.start_url = a.start_url
    if a.channel is not None:
        cfg.browser_channel = a.channel
    if a.executable:
        cfg.browser_executable = a.executable
    if a.target:
        cfg.target_station = a.target.upper()
    if a.timeout:
        cfg.timeout_sec = a.timeout
    if a.delay is not None:
        cfg.delay_sec = a.delay
    cfg.headless = a.headless or cfg.headless
    if a.no_ai:
        cfg.ai_enabled = False

    cases = excel_io.load_cases(a.xlsx, cfg.sheet_name, cfg.address)
    bot = EcsrBot(cfg)
    try:
        bot.open()
        if not bot.confirm_search_page():
            if cfg.headless:
                print("✖ 目前不在 CSR 查詢畫面（需要登入？請先用視窗模式登入一次）")
                return 2
            input("請在瀏覽器登入並停在 CSR 查詢畫面，然後按 Enter…")
            if not bot.confirm_search_page():
                print("✖ 仍找不到 CSR Ref No 查詢欄位")
                return 2
        runner = Runner(cfg, bot, a.xlsx)
        todo = pending_cases(cases, runner.results, not a.all)
        if a.limit:
            todo = todo[: a.limit]
        print(f"共 {len(cases)} 筆，本次處理 {len(todo)} 筆")
        counts = runner.run(todo)
        print("結果：", "、".join(f"{k} {v}" for k, v in counts.items()) or "無")
        print("輸出：", runner.out_xlsx)
        return 0
    finally:
        bot.close()


if __name__ == "__main__":
    sys.exit(main())
