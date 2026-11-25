"""使用者設定：存放於 %APPDATA%\\ECSRTool\\config.json。"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

DEFAULT_MODEL = "claude-opus-5-5"


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    d = Path(base) / "ECSRTool"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class Config:
    # --- 系統 / 瀏覽器 ---
    start_url: str = "https://myflow3.evaair.com/FMECSRWeb/"
    browser_channel: str = "msedge"      # msedge / chrome / 空白 = Playwright 內建 Chromium
    browser_executable: str = ""         # 指定瀏覽器執行檔（通常留白）
    cdp_url: str = ""                    # 例 http://127.0.0.1:9222：改為連接已開啟的 Edge
    headless: bool = False
    # --- 流程 ---
    sheet_name: str = "ECSR"
    target_station: str = "TPEWT"
    timeout_sec: int = 40                # 每一步等待畫面的上限
    delay_sec: float = 1.0               # 每筆案件之間的停頓
    # --- 摘要 ---
    ai_enabled: bool = False             # False = 不外送資料，只節錄原文
    api_key: str = ""                    # 留白則讀環境變數 ANTHROPIC_API_KEY
    model: str = DEFAULT_MODEL

    @staticmethod
    def path() -> Path:
        return app_dir() / "config.json"

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        try:
            data = json.loads(cls.path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cfg
        for f in fields(cls):
            if f.name in data and isinstance(data[f.name], type(getattr(cfg, f.name))):
                setattr(cfg, f.name, data[f.name])
        return cfg

    def save(self) -> None:
        self.path().write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @property
    def profile_dir(self) -> str:
        """工具專用的瀏覽器設定檔（登入狀態會保留，下次不用重登）。"""
        d = app_dir() / "browser-profile"
        d.mkdir(parents=True, exist_ok=True)
        return str(d)
