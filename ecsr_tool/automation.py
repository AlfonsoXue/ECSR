"""瀏覽器自動化：照影片流程操作 Customer Service Report Information System。

  查詢畫面輸入案號 → Search → 點案號連結 → Related Station → Outline → IR/CPN → 開 IR

本工具「只讀取」畫面：只會點頁籤、Search、案號連結與 IR 列，
不會點 Del（垃圾桶）、Forward、Comment、CPN、Read 等會異動資料的按鈕。
"""
from __future__ import annotations

import re
import threading
import time
from typing import Callable, Optional

from . import parsing
from .config import Config
from .models import ST_DONE, ST_NOT_FOUND, ST_SKIPPED, CaseResult


class StopRequested(Exception):
    pass


class FlowError(Exception):
    """流程走不下去（畫面與預期不同、逾時…）。"""


# 找出「CSR Ref No」標籤後面的輸入框並做記號，Python 端再逐格填入
_MARK_SEARCH_JS = r"""
() => {
  const norm = s => (s || '').replace(/[\s*:：]+/g, ' ').trim().toLowerCase();
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
                     return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const leaf = t => [...document.querySelectorAll('body *')].filter(e =>
      norm(e.textContent) === t && vis(e) && ![...e.children].some(c => norm(c.textContent) === t));
  document.querySelectorAll('[data-ecsr-f]').forEach(e => e.removeAttribute('data-ecsr-f'));
  const a = leaf('csr ref no')[0];
  if (!a) return {ok: false, reason: 'no-label'};
  const b = leaf('received date period')[0];
  const inputs = [...document.querySelectorAll('input')].filter(i =>
      vis(i) && ['text', 'search', 'tel', 'number', ''].includes((i.getAttribute('type') || '').toLowerCase()));
  let got = inputs.filter(i => (a.compareDocumentPosition(i) & 4) && (!b || (b.compareDocumentPosition(i) & 2)));
  if (got.length < 2) got = inputs.filter(i => a.compareDocumentPosition(i) & 4).slice(0, 3);
  if (got.length < 2) return {ok: false, reason: 'no-inputs'};
  let prefix = null;
  if (got.length >= 3) { prefix = got[0]; got = got.slice(1, 3); }
  const locked = e => e.readOnly || e.disabled;
  if (prefix) prefix.setAttribute('data-ecsr-f', 'prefix');
  got[0].setAttribute('data-ecsr-f', 'station');
  got[1].setAttribute('data-ecsr-f', 'number');
  return {ok: true, hasPrefix: !!prefix, prefixValue: prefix ? prefix.value : '',
          prefixLocked: prefix ? locked(prefix) : true};
}
"""

_IR_ROWS_JS = r"""
() => {
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
                     return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const rows = [];
  for (const tr of document.querySelectorAll('tr')) {
    const cells = [...tr.children].filter(c => /^(TD|TH)$/.test(c.tagName) && vis(c)).map(c => c.innerText.trim());
    if (cells.length >= 3 && /^IR\d{2,4}$/i.test(cells[0])) rows.push(cells);
  }
  return rows;
}
"""

_TEXTAREAS_JS = r"""
() => [...document.querySelectorAll('textarea')].filter(e => {
  const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; }).map(e => e.value)
"""


def _exact(name: str):
    return re.compile(rf"^\s*{re.escape(name)}\s*$", re.I)


class EcsrBot:
    def __init__(self, cfg: Config, log: Callable[[str], None] = print,
                 stop_event: Optional[threading.Event] = None,
                 pause_event: Optional[threading.Event] = None):
        self.cfg = cfg
        self.log = log
        self.stop_event = stop_event or threading.Event()
        self.pause_event = pause_event or threading.Event()
        self._pw = None
        self._browser = None
        self.context = None
        self.page = None
        self.search_url = ""

    # ------------------------------------------------------------ 瀏覽器
    def open(self) -> None:
        from playwright.sync_api import sync_playwright

        if self.context is not None and self._alive():
            self.page.bring_to_front()
            return
        self.close()
        self._pw = sync_playwright().start()
        cfg = self.cfg
        if cfg.cdp_url.strip():
            self._browser = self._pw.chromium.connect_over_cdp(cfg.cdp_url.strip())
            self.context = self._browser.contexts[0]
            self.page = self._find_search_page() or self.context.new_page()
            self.log(f"已連接到現有瀏覽器（{cfg.cdp_url}）")
        else:
            kw = dict(user_data_dir=cfg.profile_dir, headless=cfg.headless)
            if cfg.browser_executable.strip():
                kw["executable_path"] = cfg.browser_executable.strip()
            elif cfg.browser_channel.strip():
                kw["channel"] = cfg.browser_channel.strip()
            if cfg.headless:
                kw["viewport"] = {"width": 1600, "height": 900}
            else:
                kw["no_viewport"] = True
                kw["args"] = ["--start-maximized"]
            self.context = self._pw.chromium.launch_persistent_context(**kw)
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.context.set_default_timeout(self.cfg.timeout_sec * 1000)
        if not self.is_search_page() and cfg.start_url.strip():
            try:
                self.page.goto(cfg.start_url.strip(), wait_until="domcontentloaded")
            except Exception as e:
                self.log(f"⚠ 無法開啟 {cfg.start_url}：{_short(e)}")

    def _alive(self) -> bool:
        try:
            return self.page is not None and not self.page.is_closed() and bool(self.context.pages)
        except Exception:
            return False

    def close(self) -> None:
        for fn in (lambda: self.context.close() if self.context and not self.cfg.cdp_url.strip() else None,
                   lambda: self._pw.stop() if self._pw else None):
            try:
                fn()
            except Exception:
                pass
        self._pw = self._browser = self.context = self.page = None

    # ------------------------------------------------------------ 小工具
    def _check(self) -> None:
        if self.stop_event.is_set():
            raise StopRequested()
        while self.pause_event.is_set():
            if self.stop_event.is_set():
                raise StopRequested()
            time.sleep(0.2)

    def _sleep(self, sec: float) -> None:
        end = time.time() + sec
        while time.time() < end:
            self._check()
            time.sleep(min(0.1, max(0.0, end - time.time())))

    def _named(self, page, name: str):
        # 註：名稱含「/」（IR/CPN、案號）時 Playwright 的正規表示式選擇器會解析失敗，一律用字串精確比對
        loc = (page.get_by_role("tab", name=name, exact=True)
               .or_(page.get_by_role("button", name=name, exact=True))
               .or_(page.get_by_role("link", name=name, exact=True))
               .or_(page.get_by_text(name, exact=True)))
        return loc.filter(visible=True)

    def _has(self, page, text: str) -> bool:
        try:
            return page.get_by_text(text, exact=True).filter(visible=True).count() > 0
        except Exception:
            return False

    def _busy(self, page) -> bool:
        try:
            return page.get_by_text(re.compile(r"please\s+wait", re.I)).filter(visible=True).count() > 0
        except Exception:
            return True   # 頁面跳轉中

    def _settle(self, page, first_wait: float = 0.4) -> None:
        """等「Please wait...」遮罩消失且穩定。"""
        self._sleep(first_wait)
        end = time.time() + self.cfg.timeout_sec
        calm = 0
        while time.time() < end:
            self._check()
            calm = 0 if self._busy(page) else calm + 1
            if calm >= 2:
                return
            time.sleep(0.25)
        raise FlowError("畫面一直停在 Please wait…，逾時")

    def _wait_for(self, cond: Callable[[], object], what: str, timeout: Optional[float] = None):
        end = time.time() + (timeout or self.cfg.timeout_sec)
        while time.time() < end:
            self._check()
            try:
                v = cond()
                if v:
                    return v
            except Exception:
                pass
            time.sleep(0.25)
        raise FlowError(f"等不到{what}（逾時）")

    def _click_tab(self, page, name: str, expect_text: Optional[str] = None) -> None:
        tab = self._named(page, name)
        self._wait_for(lambda: tab.count() > 0, f"「{name}」頁籤")
        tab.first.click()
        self._settle(page)
        if expect_text:
            try:
                self._wait_for(lambda: page.get_by_text(expect_text).filter(visible=True).count() > 0,
                               f"「{expect_text}」", timeout=min(10, self.cfg.timeout_sec))
            except FlowError:
                pass   # 沒看到預期標題也繼續，由後續解析決定

    def _texts(self, page) -> tuple:
        """回傳 (主畫面文字, [可見 iframe 內文字…], [textarea 內容…])。"""
        main = page.evaluate("() => document.body.innerText")
        frames = []
        for f in page.frames:
            if f == page.main_frame:
                continue
            try:
                if f.frame_element().is_visible():
                    frames.append(f.evaluate("() => document.body ? document.body.innerText : ''"))
            except Exception:
                continue
        try:
            areas = page.evaluate(_TEXTAREAS_JS)
        except Exception:
            areas = []
        return main, frames, areas

    # ------------------------------------------------------------ 查詢畫面
    def is_search_page(self, page=None) -> bool:
        page = page or self.page
        try:
            return bool(page.evaluate(_MARK_SEARCH_JS).get("ok"))
        except Exception:
            return False

    def _find_search_page(self):
        for pg in self.context.pages:
            if self.is_search_page(pg):
                return pg
        return None

    def confirm_search_page(self) -> bool:
        """開始前確認：使用者已登入並停在 CSR 查詢畫面；記下網址供每筆案件返回。"""
        if not self._alive():
            return False
        pg = self.page if self.is_search_page(self.page) else self._find_search_page()
        if not pg:
            return False
        self.page = pg
        self.search_url = pg.url
        return True

    def _goto_search(self) -> None:
        page = self.page
        if page.is_closed():
            page = self.page = self.context.new_page()
        for pg in list(self.context.pages):      # 關掉上一筆開的案件 / IR 分頁
            if pg is not page:
                try:
                    pg.close()
                except Exception:
                    pass
        page.bring_to_front()
        if self.is_search_page(page):
            return
        page.goto(self.search_url, wait_until="domcontentloaded")
        try:
            self._wait_for(lambda: self.is_search_page(page), "CSR 查詢畫面", timeout=15)
        except FlowError:
            tab = self._named(page, "CSR")
            if tab.count():
                tab.first.click()
            self._wait_for(lambda: self.is_search_page(page), "CSR 查詢畫面（登入是否已逾時？）")

    # ------------------------------------------------------------ 單筆案件
    def process_case(self, row: int, ref: str) -> CaseResult:
        res = CaseResult(row=row, ref=ref)
        parts = parsing.parse_ref(ref)
        if not parts:
            res.state, res.note = ST_SKIPPED, "案號格式不完整（應為 ECSR/XXXXX/8碼數字），請人工確認"
            return res
        prefix, station, number = parts
        target = self.cfg.target_station
        ref = f"{prefix}/{station}/{number}"

        # 1. 查詢
        self._check()
        self._goto_search()
        page = self.page
        info = page.evaluate(_MARK_SEARCH_JS)
        if not info.get("ok"):
            raise FlowError("找不到 CSR Ref No 輸入框")
        if info["hasPrefix"] and info["prefixValue"].strip().upper() != prefix:
            if info["prefixLocked"]:
                res.state = ST_SKIPPED
                res.note = f"案號前綴 {prefix} 與查詢畫面固定的 {info['prefixValue']} 不同，請人工查詢"
                return res
            page.locator('[data-ecsr-f="prefix"]').fill(prefix)
        elif not info["hasPrefix"] and prefix != "ECSR":
            res.state, res.note = ST_SKIPPED, f"案號前綴為 {prefix}（非 ECSR），請人工查詢"
            return res
        page.locator('[data-ecsr-f="station"]').fill(station)
        page.locator('[data-ecsr-f="number"]').fill(number)
        self._named(page, "Search").first.click()
        self._settle(page)

        link = (page.get_by_role("link", name=ref, exact=True)
                .or_(page.get_by_text(ref, exact=True))).filter(visible=True)
        grid_seen = None
        end = time.time() + self.cfg.timeout_sec
        while link.count() == 0:
            self._check()
            now = time.time()
            if not self._busy(page) and self._has(page, "Handle By"):
                grid_seen = grid_seen or now
                if now - grid_seen > 4:          # 清單出來了但沒有這個案號
                    res.state, res.note = ST_NOT_FOUND, "查詢結果沒有此案號"
                    return res
            if now > end:
                res.state, res.note = ST_NOT_FOUND, "查詢後沒有出現此案號（逾時）"
                return res
            time.sleep(0.3)

        # 2. 開啟案件（可能開新分頁，也可能在原分頁跳轉）
        link.first.click()
        case = self._wait_for(lambda: self._page_with("Related Station", "Outline"), "案件畫面")
        case.bring_to_front()
        self._settle(case)

        # 3. Related Station
        self._click_tab(case, "Related Station", "Investigate & Report Dept")
        main, _, _ = self._texts(case)
        res.related_found, res.related_where = parsing.related_station_info(main, target)
        self.log(f"   Related Station：{'有' if res.related_found else '無'} {target}"
                 + (f"（{res.related_where}）" if res.related_where else ""))

        # 4. Outline（Related Station 有目標單位才摘要）
        if res.related_found:
            self._click_tab(case, "Outline", "Outline & Causes")
            main, frames, areas = self._texts(case)
            res.outline_text = parsing.pick_longest(frames + areas) or parsing.cut_outline(main)
            if not res.outline_text:
                res.warnings.append("Outline 內容讀不到")
            self.log(f"   Outline：讀到 {len(res.outline_text)} 字")

        # 5. IR/CPN
        self._click_tab(case, "IR/CPN", "IR Ref No")
        rows = [r for r in (parsing.ir_row_from_cells(c) for c in case.evaluate(_IR_ROWS_JS)) if r]
        if not rows:
            rows = parsing.ir_rows_from_text(parsing.after_tab_bar(self._texts(case)[0]))
        res.ir_count = len(rows)
        req = parsing.latest_request(rows, target)
        res.ir_found = req is not None
        if req is None:
            self.log(f"   IR/CPN：共 {len(rows)} 筆，沒有指定 {target} 的 IR")
        else:
            res.ir_no, res.ir_date, res.ir_from = req.ir_no, req.approved, req.create_dept
            res.reply_status = parsing.reply_status(rows, target, req)
            self.log(f"   IR/CPN：共 {len(rows)} 筆，最新指定 {target} 的是 {req.ir_no}（{req.approved}）→ {res.reply_status}")
            try:
                res.ir_text = self._read_ir(case, req.ir_no)
            except FlowError as e:
                res.warnings.append(f"{req.ir_no} 內容讀不到：{e}")
        res.state = ST_DONE
        return res

    def _page_with(self, *texts):
        for pg in reversed(self.context.pages):
            if not pg.is_closed() and all(self._has(pg, t) for t in texts):
                return pg
        return None

    def _read_ir(self, case, ir_no: str) -> str:
        cell = case.locator("td").filter(has_text=_exact(ir_no)).filter(visible=True)
        if cell.count() == 0:
            cell = case.get_by_text(ir_no, exact=True).filter(visible=True)
        if cell.count() == 0:
            raise FlowError("找不到該列")
        cell.last.scroll_into_view_if_needed()
        cell.last.click()
        find = lambda: self._page_with("Investigation Result")  # noqa: E731
        try:
            ir = self._wait_for(find, "IR 畫面", timeout=6)
        except FlowError:
            cell.last.dblclick()                 # 有些清單要雙擊才會開
            ir = self._wait_for(find, "IR 畫面")
        ir.bring_to_front()
        self._settle(ir)
        self._click_tab(ir, "Investigation Result", "PAX Info")
        main, frames, areas = self._texts(ir)
        text = parsing.pick_longest(frames + areas) or parsing.cut_ir_result(main)
        if not text:
            raise FlowError("Investigation Result 是空的")
        self.log(f"   {ir_no}：讀到 {len(text)} 字")
        return text


def _short(e: Exception) -> str:
    return str(e).strip().split("\n")[0][:200]
