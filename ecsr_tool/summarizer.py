"""摘要：預設只在本機節錄原文；啟用 AI 後改由 Claude 產生繁體中文摘要。"""
from __future__ import annotations

import os
import re
from typing import Callable

from .config import Config

SYSTEM_PROMPT = """你是航空公司旅客服務案件（CSR / ECSR）的資深承辦人員，負責替同仁把案件內容整理成摘要，\
讓主管不必打開系統就能掌握案情。

撰寫原則：
- 一律使用繁體中文；航班號、日期、場站或單位代碼（如 TPEWT、LAXSR）、IR 編號、金額與專有名詞保留原文。
- 只根據提供的文字整理，不要推測或加入原文沒有的資訊；原文沒提到的事項就不要寫。
- 直接輸出摘要本身，不要開場白、不要標題、不要 Markdown 符號。
- 旅客姓名以「旅客」代稱即可，不需列出全名。"""

OUTLINE_TASK = """以下是案件 {ref} 的 Outline & Causes（旅客申訴或事件原文）。
請用 3 到 5 句話摘要：發生什麼事（航班、日期、地點）、旅客的主要不滿、旅客提出的訴求。
其中與 {target} 有關的部分請優先說明。

<outline>
{text}
</outline>"""

IR_TASK = """以下是案件 {ref} 目前最新一筆指定 {target} 調查回覆的 IR（{ir_no}，{ir_date}，由 {ir_from} 發出）。
IR 清單顯示的回覆情形：{reply}

請用 3 到 5 句話說明最新處理狀態：對方要求 {target} 釐清或提供什麼（有條列時請逐項簡述重點）、\
目前進度到哪裡、{target} 接下來需要做什麼。

<ir>
{text}
</ir>"""

# 支援伺服器端 refusal fallback 的模型
_FALLBACK_MODELS = ("claude-opus-5", "claude-sonnet-5-5", "claude-fable-5")


def extractive(text: str, limit: int = 320) -> str:
    """不使用 AI 時的節錄：取開頭幾句，原文完整保留在「ECSR原文」頁籤。"""
    t = re.sub(r"\s+", " ", text or "").strip()
    if not t:
        return ""
    if len(t) <= limit:
        return "〔原文節錄〕" + t
    cut = t[:limit]
    m = list(re.finditer(r"[.!?。！？](?=\s|$)", cut))
    if m and m[-1].end() > limit * 0.5:
        cut = cut[: m[-1].end()]
    return "〔原文節錄〕" + cut.rstrip() + " …"


class Summarizer:
    def __init__(self, cfg: Config, log: Callable[[str], None] = print):
        self.cfg = cfg
        self.log = log
        self._client = None
        self._disabled = not cfg.ai_enabled
        self._net_failures = 0

    # -- public --------------------------------------------------------
    def outline(self, ref: str, text: str) -> str:
        if not text.strip():
            return ""
        prompt = OUTLINE_TASK.format(ref=ref, target=self.cfg.target_station, text=text)
        return self._ask(prompt) or extractive(text)

    def ir_status(self, ref: str, text: str, ir_no: str, ir_date: str,
                  ir_from: str, reply: str) -> str:
        if not text.strip():
            return ""
        prompt = IR_TASK.format(ref=ref, target=self.cfg.target_station, text=text,
                                ir_no=ir_no, ir_date=ir_date, ir_from=ir_from or "不明",
                                reply=reply or "不明")
        return self._ask(prompt) or extractive(text)

    # -- internals -----------------------------------------------------
    def _ask(self, prompt: str) -> str:
        if self._disabled:
            return ""
        import anthropic

        try:
            if self._client is None:
                key = self.cfg.api_key.strip() or os.environ.get("ANTHROPIC_API_KEY", "")
                self._client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
            kwargs = dict(
                model=self.cfg.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                output_config={"effort": "low"},
                messages=[{"role": "user", "content": prompt}],
            )
            if self.cfg.model.startswith(_FALLBACK_MODELS):
                # 模型婉拒時由伺服器自動改用備援模型重跑
                resp = self._client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
            else:
                resp = self._client.messages.create(**kwargs)
        except anthropic.AuthenticationError:
            return self._off("API 金鑰無效，改用原文節錄。請到「設定」檢查金鑰。")
        except anthropic.PermissionDeniedError:
            return self._off("API 金鑰沒有使用此模型的權限，改用原文節錄。")
        except anthropic.NotFoundError:
            return self._off(f"找不到模型 {self.cfg.model}，改用原文節錄。請到「設定」檢查模型名稱。")
        except anthropic.BadRequestError as e:
            self.log(f"⚠ AI 摘要請求被拒（{e.message}），本筆改用原文節錄。")
            return ""
        except anthropic.RateLimitError:
            self.log("⚠ AI 服務忙碌（已自動重試仍失敗），本筆改用原文節錄。")
            return ""
        except anthropic.APIStatusError as e:
            self.log(f"⚠ AI 服務錯誤 {e.status_code}，本筆改用原文節錄。")
            return ""
        except anthropic.APIConnectionError:
            self._net_failures += 1
            if self._net_failures >= 3:
                return self._off("連續 3 次連不上 AI 服務（公司網路可能擋住 api.anthropic.com），"
                                 "之後改用原文節錄。")
            self.log("⚠ 連不上 AI 服務，本筆改用原文節錄。")
            return ""
        except Exception as e:  # 例如沒有設定任何金鑰
            return self._off(f"AI 摘要無法啟用（{e}），改用原文節錄。")

        self._net_failures = 0
        if resp.stop_reason == "refusal":
            self.log("⚠ AI 婉拒摘要此內容，本筆改用原文節錄。")
            return ""
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        if resp.stop_reason == "max_tokens":
            text += " …（摘要過長被截斷）"
        return text

    def _off(self, msg: str) -> str:
        self.log("⚠ " + msg)
        self._disabled = True
        return ""
