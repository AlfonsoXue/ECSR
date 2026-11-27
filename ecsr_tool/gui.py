"""Windows 視窗介面（Tkinter）。瀏覽器操作在背景執行緒進行，介面不會卡住。"""
from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, __version__, excel_io
from .automation import EcsrBot, _short
from .config import Config
from .models import ST_DONE, ST_ERROR, ST_NOT_FOUND, ST_PENDING, ST_SKIPPED, CaseResult
from .runner import Runner, pending_cases

COLS = [("no", "#", 45), ("ref", "CSR NO.", 170), ("date", "電報日期", 90), ("pic", "PIC", 70),
        ("stn", "場站", 55), ("state", "處理結果", 80), ("rel", "Related Station", 110),
        ("ir", "最新 IR", 150), ("reply", "回覆狀態", 260)]
TAG_COLORS = {ST_DONE: "#e8f5e9", ST_ERROR: "#ffebee", ST_NOT_FOUND: "#fff8e1",
              ST_SKIPPED: "#f3f3f3", "處理中": "#e3f2fd"}


class Worker(threading.Thread):
    """唯一會碰 Playwright 的執行緒。"""

    def __init__(self, cfg: Config, ui: "queue.Queue"):
        super().__init__(daemon=True)
        self.cfg, self.ui = cfg, ui
        self.cmds: "queue.Queue" = queue.Queue()
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.bot = EcsrBot(cfg, self.log, self.stop_event, self.pause_event)

    def log(self, msg: str) -> None:
        self.ui.put(("log", msg))

    def run(self) -> None:
        while True:
            cmd, *args = self.cmds.get()
            try:
                if cmd == "quit":
                    self.bot.close()
                    return
                if cmd == "open":
                    self._open()
                elif cmd == "run":
                    self._run(*args)
            except Exception as e:
                self.log(f"✖ {_short(e)}")
                self.ui.put(("idle", None))

    def _open(self) -> bool:
        self.ui.put(("busy", "正在開啟瀏覽器…"))
        try:
            self.bot.open()
        except Exception as e:
            msg = _short(e)
            hint = ""
            if "Executable doesn't exist" in msg or "is not found" in msg:
                hint = "\n找不到瀏覽器，請到「設定」確認瀏覽器類型（msedge / chrome）。"
            elif "ECONNREFUSED" in msg:
                hint = "\n連不上已開啟的 Edge，請確認是用「啟動Edge偵錯模式.bat」開啟的。"
            self.ui.put(("error", f"瀏覽器開啟失敗：{msg}{hint}"))
            self.ui.put(("idle", None))
            return False
        self.log("瀏覽器已開啟。請登入系統並停在 CSR 查詢畫面（看得到 CSR Ref No 欄位），再按「開始」。")
        self.ui.put(("idle", None))
        return True

    def _run(self, xlsx: str, cases: list) -> None:
        self.stop_event.clear()
        self.pause_event.clear()
        if not self.bot._alive() and not self._open():
            return
        if not self.bot.confirm_search_page():
            self.ui.put(("error", "瀏覽器目前不在 CSR 查詢畫面。\n\n請先在瀏覽器登入，並切到看得到"
                                  "「CSR Ref No」欄位的查詢畫面，再按一次「開始」。"))
            self.ui.put(("idle", None))
            return
        self.ui.put(("running", len(cases)))
        runner = Runner(self.cfg, self.bot, xlsx, self.log,
                        on_case=lambda phase, res: self.ui.put(("case", phase, res)))
        counts = runner.run(cases)
        self.ui.put(("finished", counts, runner.out_xlsx))
        self.ui.put(("idle", None))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{__version__}")
        self.geometry("1180x760")
        self.minsize(960, 600)
        self.cfg = Config.load()
        self.ui: "queue.Queue" = queue.Queue()
        self.worker = Worker(self.cfg, self.ui)
        self.worker.start()
        self.xlsx = ""
        self.fmt = excel_io.FMT_ECSR
        self.cases: list = []
        self.results: dict = {}
        self.done_count = self.total = 0
        self.busy = False
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._pump)
        self._log(f"目標單位：{self.cfg.target_station}　｜　AI 摘要："
                  + ("啟用" if self.cfg.ai_enabled else "未啟用（只節錄原文，資料不外送）"))
        self._log("步驟：① 匯入 Excel（MINet 電報匯出檔）　② 開啟瀏覽器並登入、停在 CSR 查詢畫面　③ 開始")

    # ------------------------------------------------------------ 版面
    def _build(self) -> None:
        pad = dict(padx=6, pady=4)
        top = ttk.Frame(self)
        top.pack(fill="x", **pad)
        ttk.Button(top, text="① 匯入 Excel…", command=self.import_xlsx).pack(side="left")
        self.lbl_file = ttk.Label(top, text="尚未匯入檔案", foreground="#666")
        self.lbl_file.pack(side="left", padx=10)
        ttk.Button(top, text="設定…", command=self.open_settings).pack(side="right")

        bar = ttk.Frame(self)
        bar.pack(fill="x", **pad)
        self.btn_open = ttk.Button(bar, text="② 開啟瀏覽器", command=lambda: self.worker.cmds.put(("open",)))
        self.btn_open.pack(side="left")
        self.btn_start = ttk.Button(bar, text="③ 開始", command=self.start)
        self.btn_start.pack(side="left", padx=(6, 0))
        self.btn_pause = ttk.Button(bar, text="暫停", command=self.toggle_pause, state="disabled")
        self.btn_pause.pack(side="left", padx=(6, 0))
        self.btn_stop = ttk.Button(bar, text="停止", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left", padx=(6, 0))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(bar, text="範圍：第").pack(side="left")
        self.var_from, self.var_to = tk.StringVar(value="1"), tk.StringVar(value="")
        ttk.Entry(bar, width=5, textvariable=self.var_from).pack(side="left")
        ttk.Label(bar, text="～").pack(side="left")
        ttk.Entry(bar, width=5, textvariable=self.var_to).pack(side="left")
        ttk.Label(bar, text="筆").pack(side="left")
        self.var_unfinished = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="只處理未完成的", variable=self.var_unfinished).pack(side="left", padx=10)
        ttk.Button(bar, text="開啟結果檔", command=self.open_output).pack(side="right")
        ttk.Button(bar, text="重新輸出結果", command=self.rewrite_output).pack(side="right", padx=6)

        paned = ttk.PanedWindow(self, orient="vertical")
        paned.pack(fill="both", expand=True, **pad)
        tf = ttk.Frame(paned)
        self.tree = ttk.Treeview(tf, columns=[c[0] for c in COLS], show="headings", selectmode="browse")
        for key, title, width in COLS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="w", stretch=key == "reply")
        for tag, color in TAG_COLORS.items():
            self.tree.tag_configure(tag, background=color)
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        paned.add(tf, weight=3)

        nb = ttk.Notebook(paned)
        self.txt_detail = self._text(nb)
        self.txt_log = self._text(nb)
        nb.add(self.txt_detail.master, text="選取案件的摘要")
        nb.add(self.txt_log.master, text="執行紀錄")
        nb.select(1)
        self.nb = nb
        paned.add(nb, weight=2)

        st = ttk.Frame(self)
        st.pack(fill="x", **pad)
        self.progress = ttk.Progressbar(st, length=260)
        self.progress.pack(side="left")
        self.lbl_status = ttk.Label(st, text="就緒")
        self.lbl_status.pack(side="left", padx=10)

    @staticmethod
    def _text(parent) -> tk.Text:
        f = ttk.Frame(parent)
        t = tk.Text(f, wrap="word", height=10, state="disabled", font=("Microsoft JhengHei UI", 10))
        sb = ttk.Scrollbar(f, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        t.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return t

    # ------------------------------------------------------------ 動作
    def import_xlsx(self) -> None:
        path = filedialog.askopenfilename(title="選擇 MINet 電報匯出檔，或含 ECSR 頁籤的 Excel",
                                          filetypes=[("Excel", "*.xlsx *.xlsm")])
        if not path:
            return
        try:
            fmt = excel_io.detect_format(path)
            cases = excel_io.load_cases(path, self.cfg.sheet_name, self.cfg.address)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"讀取失敗：{e}")
            return
        if not cases:
            messagebox.showinfo(APP_NAME, f"檔案裡找不到與 {self.cfg.address} 有關的 ECSR 案號。"
                                if fmt == excel_io.FMT_MINET else "ECSR 頁籤沒有案號。")
            return
        self.xlsx, self.cases, self.fmt = path, cases, fmt
        minet = fmt == excel_io.FMT_MINET
        for key, title in (("date", "最新電報時間" if minet else "電報日期"),
                           ("pic", "電報數" if minet else "PIC"), ("stn", "航班" if minet else "場站")):
            self.tree.heading(key, text=title)
        self.tree.column("date", width=125 if minet else 90)
        self.tree.column("stn", width=115 if minet else 55)
        self.results = excel_io.load_progress(excel_io.output_paths(path)[1])
        self.tree.delete(*self.tree.get_children())
        for n, c in enumerate(cases, 1):
            self.tree.insert("", "end", iid=c.ref, values=self._row(n, c, self.results.get(c.ref)),
                             tags=(self._tag(self.results.get(c.ref)),))
        done = sum(1 for c in cases if c.ref in self.results)
        self.lbl_file.configure(text=f"{os.path.basename(path)}　共 {len(cases)} 筆"
                                     + (f"（先前已處理 {done} 筆）" if done else ""), foreground="#000")
        self.var_from.set("1")
        self.var_to.set(str(len(cases)))
        if minet:
            self._log(f"已匯入 {path}（MINet 電報匯出）：收件含 {self.cfg.address} 的電報共 "
                      f"{sum(c.msg_count for c in cases)} 封，整理出 {len(cases)} 個 ECSR 案號。")
        else:
            self._log(f"已匯入 {path}：{self.cfg.sheet_name} 頁籤 {len(cases)} 筆。")

    def _row(self, n: int, c, res: "CaseResult | None") -> tuple:
        state = rel = ir = reply = ""
        if res:
            state = res.state
            rel = "" if res.related_found is None else ("有" if res.related_found else "無")
            ir = f"{res.ir_no} {res.ir_date}" if res.ir_no else ("無" if res.ir_found is False else "")
            reply = res.reply_status or res.note
        if c.msg_count:
            return (n, c.ref, c.date, c.msg_count, c.flight, state or ST_PENDING, rel, ir, reply)
        return (n, c.ref, c.date, c.pic, c.station, state or ST_PENDING, rel, ir, reply)

    @staticmethod
    def _tag(res) -> str:
        return res.state if res else ""

    def start(self) -> None:
        if self.busy:
            return
        if not self.cases:
            messagebox.showinfo(APP_NAME, "請先匯入 Excel。")
            return
        try:
            a = max(1, int(self.var_from.get() or 1))
            b = min(len(self.cases), int(self.var_to.get() or len(self.cases)))
        except ValueError:
            messagebox.showerror(APP_NAME, "範圍請輸入數字。")
            return
        todo = pending_cases(self.cases[a - 1:b], self.results, self.var_unfinished.get())
        if not todo:
            messagebox.showinfo(APP_NAME, "這個範圍內沒有待處理的案件。\n（要重跑請取消勾選「只處理未完成的」）")
            return
        self.nb.select(1)
        self.worker.cmds.put(("run", self.xlsx, todo))
        self._set_busy(True, "準備中…")

    def toggle_pause(self) -> None:
        ev = self.worker.pause_event
        if ev.is_set():
            ev.clear()
            self.btn_pause.configure(text="暫停")
            self._log("▶ 繼續")
        else:
            ev.set()
            self.btn_pause.configure(text="繼續")
            self._log("⏸ 已暫停（目前這一步做完後停住）")

    def stop(self) -> None:
        self.worker.stop_event.set()
        self.worker.pause_event.clear()
        self.lbl_status.configure(text="停止中…")

    def rewrite_output(self) -> None:
        if not self.xlsx:
            return
        out = excel_io.output_paths(self.xlsx)[0]
        try:
            excel_io.write_results(self.xlsx, out, self.cfg.sheet_name, self.results,
                                   self.cfg.target_station, self.cfg.address)
            self._log(f"已輸出：{out}")
        except PermissionError:
            messagebox.showwarning(APP_NAME, "結果檔正被 Excel 開啟，請先關閉。")
        except Exception as e:
            messagebox.showerror(APP_NAME, f"輸出失敗：{e}")

    def open_output(self) -> None:
        if not self.xlsx:
            return
        out = excel_io.output_paths(self.xlsx)[0]
        if not os.path.exists(out):
            messagebox.showinfo(APP_NAME, "還沒有結果檔。")
            return
        try:
            os.startfile(out)  # type: ignore[attr-defined]
        except Exception as e:
            messagebox.showinfo(APP_NAME, f"結果檔位置：\n{out}\n\n({e})")

    def open_settings(self) -> None:
        if self.busy:
            messagebox.showinfo(APP_NAME, "執行中無法修改設定。")
            return
        SettingsDialog(self, self.cfg)

    # ------------------------------------------------------------ 背景事件
    def _pump(self) -> None:
        try:
            while True:
                kind, *args = self.ui.get_nowait()
                getattr(self, "_ev_" + kind)(*args)
        except queue.Empty:
            pass
        self.after(100, self._pump)

    def _ev_log(self, msg: str) -> None:
        self._log(msg)

    def _ev_error(self, msg: str) -> None:
        self._log("✖ " + msg.replace("\n", " "))
        messagebox.showwarning(APP_NAME, msg)

    def _ev_busy(self, msg: str) -> None:
        self._set_busy(True, msg, can_stop=False)

    def _ev_idle(self, _=None) -> None:
        self._set_busy(False, "就緒")

    def _ev_running(self, total: int) -> None:
        self.total, self.done_count = total, 0
        self.progress.configure(maximum=total, value=0)
        self._set_busy(True, f"執行中 0/{total}")

    def _ev_case(self, phase: str, res: CaseResult) -> None:
        if not self.tree.exists(res.ref):
            return
        idx = self.tree.index(res.ref) + 1
        case = self.cases[idx - 1]
        if phase == "start":
            vals = list(self._row(idx, case, None))
            vals[5] = "處理中"
            self.tree.item(res.ref, values=vals, tags=("處理中",))
            self.tree.see(res.ref)
            return
        self.results[res.ref] = res
        self.tree.item(res.ref, values=self._row(idx, case, res), tags=(self._tag(res),))
        self.done_count += 1
        self.progress.configure(value=self.done_count)
        self.lbl_status.configure(text=f"執行中 {self.done_count}/{self.total}")
        if self.tree.selection() == (res.ref,):
            self._on_select()

    def _ev_finished(self, counts: dict, out: str) -> None:
        summary = "、".join(f"{k} {v} 筆" for k, v in counts.items()) or "沒有處理任何案件"
        self._log(f"✔ 本次結束：{summary}")
        self._log(f"   結果檔：{out}")

    # ------------------------------------------------------------ 其他
    def _set_busy(self, busy: bool, status: str, can_stop: bool = True) -> None:
        self.busy = busy
        self.btn_start.configure(state="disabled" if busy else "normal")
        self.btn_open.configure(state="disabled" if busy else "normal")
        on = "normal" if busy and can_stop else "disabled"
        self.btn_pause.configure(state=on, text="暫停")
        self.btn_stop.configure(state=on)
        self.lbl_status.configure(text=status)

    def _log(self, msg: str) -> None:
        t = self.txt_log
        t.configure(state="normal")
        t.insert("end", msg + "\n")
        t.see("end")
        t.configure(state="disabled")

    def _on_select(self, _=None) -> None:
        sel = self.tree.selection()
        res = self.results.get(sel[0]) if sel else None
        t = self.txt_detail
        t.configure(state="normal")
        t.delete("1.0", "end")
        if res:
            tgt = self.cfg.target_station
            lines = [f"{res.ref}　〔{res.state}〕　{res.processed_at}"]
            if res.note:
                lines.append(f"備註：{res.note}")
            for w in res.warnings:
                lines.append(f"⚠ {w}")
            if res.related_found is not None:
                lines.append(f"\nRelated Station：{'有' if res.related_found else '無'} {tgt}"
                             + (f"（{res.related_where}）" if res.related_where else ""))
            if res.outline_summary:
                lines.append(f"\n【案件摘要】\n{res.outline_summary}")
            if res.ir_no:
                lines.append(f"\n【最新處理狀態】{res.ir_no}　{res.ir_date}　{res.ir_from} → {tgt}"
                             f"\n回覆狀態：{res.reply_status}\n{res.ir_summary}")
            elif res.ir_found is False:
                lines.append(f"\nIR/CPN：共 {res.ir_count} 筆，沒有指定 {tgt} 的 IR")
            t.insert("end", "\n".join(lines))
            self.nb.select(0)
        t.configure(state="disabled")

    def _on_close(self) -> None:
        if self.busy and not messagebox.askyesno(APP_NAME, "正在執行中，確定要結束嗎？\n（已完成的案件都已存檔）"):
            return
        self.worker.stop_event.set()
        self.worker.pause_event.clear()
        self.worker.cmds.put(("quit",))
        self.worker.join(timeout=8)
        self.destroy()


class SettingsDialog(tk.Toplevel):
    FIELDS = [("start_url", "系統網址（開啟瀏覽器時前往）", 52),
              ("sheet_name", "Excel 頁籤名稱", 14),
              ("target_station", "目標單位代碼", 14),
              ("target_address", "電報地址（留白 = 單位代碼 + BR）", 14),
              ("timeout_sec", "每步等待上限（秒）", 8),
              ("delay_sec", "每筆之間停頓（秒）", 8),
              ("browser_channel", "瀏覽器（msedge / chrome）", 14),
              ("cdp_url", "連接已開啟的 Edge（選填，例 http://127.0.0.1:9222）", 40)]

    def __init__(self, master: App, cfg: Config):
        super().__init__(master)
        self.title("設定")
        self.cfg = cfg
        self.transient(master)
        self.resizable(False, False)
        self.vars = {}
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        r = 0
        for key, label, width in self.FIELDS:
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=3)
            v = tk.StringVar(value=str(getattr(cfg, key)))
            ttk.Entry(f, textvariable=v, width=width).grid(row=r, column=1, sticky="w", padx=8)
            self.vars[key] = v
            r += 1
        ttk.Separator(f).grid(row=r, column=0, columnspan=2, sticky="ew", pady=10)
        r += 1
        self.var_ai = tk.BooleanVar(value=cfg.ai_enabled)
        ttk.Checkbutton(f, text="啟用 AI 摘要（Claude，輸出繁體中文摘要）", variable=self.var_ai
                        ).grid(row=r, column=0, columnspan=2, sticky="w")
        r += 1
        ttk.Label(f, foreground="#b71c1c", wraplength=560, justify="left",
                  text="注意：啟用後，案件的 Outline 與 IR 內容（含旅客資料）會傳送到 Anthropic 的 API 進行摘要。"
                       "請先確認符合公司資安與個資規定。未啟用時所有資料只留在本機，摘要欄為原文節錄。"
                  ).grid(row=r, column=0, columnspan=2, sticky="w", pady=4)
        r += 1
        ttk.Label(f, text="API 金鑰（留白則讀 ANTHROPIC_API_KEY）").grid(row=r, column=0, sticky="w", pady=3)
        self.var_key = tk.StringVar(value=cfg.api_key)
        ttk.Entry(f, textvariable=self.var_key, width=52, show="•").grid(row=r, column=1, sticky="w", padx=8)
        r += 1
        ttk.Label(f, text="模型").grid(row=r, column=0, sticky="w", pady=3)
        self.var_model = tk.StringVar(value=cfg.model)
        ttk.Combobox(f, textvariable=self.var_model, width=24,
                     values=["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
                     ).grid(row=r, column=1, sticky="w", padx=8)
        r += 1
        b = ttk.Frame(f)
        b.grid(row=r, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(b, text="儲存", command=self._save).pack(side="left", padx=6)
        ttk.Button(b, text="取消", command=self.destroy).pack(side="left")
        self.grab_set()

    def _save(self) -> None:
        c = self.cfg
        try:
            c.timeout_sec = max(5, int(float(self.vars["timeout_sec"].get())))
            c.delay_sec = max(0.0, float(self.vars["delay_sec"].get()))
        except ValueError:
            messagebox.showerror("設定", "秒數請輸入數字。", parent=self)
            return
        c.start_url = self.vars["start_url"].get().strip()
        c.sheet_name = self.vars["sheet_name"].get().strip() or "ECSR"
        c.target_station = self.vars["target_station"].get().strip().upper() or "TPEWT"
        c.target_address = self.vars["target_address"].get().strip().upper()
        c.browser_channel = self.vars["browser_channel"].get().strip()
        c.cdp_url = self.vars["cdp_url"].get().strip()
        c.ai_enabled = self.var_ai.get()
        c.api_key = self.var_key.get().strip()
        c.model = self.var_model.get().strip() or "claude-opus-5-5"
        c.save()
        self.master._log(f"設定已儲存。目標單位：{c.target_station}　AI 摘要：{'啟用' if c.ai_enabled else '未啟用'}")
        self.destroy()


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    app = App()
    if "--selftest" in argv:        # 建置後的冒煙測試：視窗能建立、Playwright 能載入
        import playwright.sync_api  # noqa: F401
        import anthropic  # noqa: F401
        app.update()
        app.worker.cmds.put(("quit",))
        app.destroy()
        return 0
    app.mainloop()
    return 0
