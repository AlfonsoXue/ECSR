"""模擬 Customer Service Report Information System 的本機網站（僅供測試）。

依照操作錄影的畫面仿做，資料全為虛構。兩種版面用來驗證程式不依賴特定 HTML：
  /a/  標準 <table>、同分頁跳轉、Outline 放在 <div>、單擊開 IR
  /b/  表頭與內容分離的格線、開新分頁、Outline 放在 <iframe>、雙擊開 IR
"""
from __future__ import annotations

import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PAX = "1.TESTER ALPHA/Mrs.\n2.TESTER BRAVO/Mr."
OUTLINE_1 = """<p><b><u>12AUG-</u></b><u> Received passenger's comments through TEFB/0000001:</u></p>
<p>I am submitting a formal complaint regarding my family's experience on August 11, 2026, involving BR384 (Da Nang-Taipei) and BR018 (Taipei-San Francisco).</p>
<p>At the transfer counter in Taipei we were quoted USD 1,300 per person for a Premium Economy upgrade and agreed to pay, but the agent did not process it and directed us to the gate.</p>
<p>I respectfully request a specific review and a formal response.</p><p><i>//Unquote//</i></p>"""
IR_BODY = """<p>Dear TPEWT Team,</p><p>Many thanks for your reply in IR003.</p>
<ol><li><b>Processing deadlines</b>: Specify the sales cutoff time for BR18/11AUG.</li>
<li><b>Referral to the boarding gate</b>: Explain when staff should direct a PAX to the gate.</li></ol>
<p>Thank you for your assistance.</p><p>Mtks/Brgds</p>"""

CASES = {
    "ECSR/TPEWS/26000001": {
        "investigate": [("DADKP", "2026/08/13 19:30", "2026/09/21 10:04"),
                        ("LAXSR", "2026/09/19 04:03", "2026/11/14 09:32")],
        "handle": ["LAXSR"], "reference": ["TPEWS", "TPEWT"], "outline": OUTLINE_1,
        "irs": [("IR001", "王小明\nTest Wang", "2026/08/14 14:58", "TPEHE", "", ""),
                ("IR002", "陳小華\nTest Chen", "2026/10/27 08:34", "LAXSR", PAX, "TPEWT"),
                ("IR003", "李小漢\nTest Lee", "2026/11/03 18:21", "TPEWT", "", ""),
                ("IR004", "陳小華\nTest Chen", "2026/11/05 07:25", "LAXSR", PAX, "TPEWT"),
                ("IR005", "李小漢\nTest Lee", "2026/11/09 17:48", "TPEWT", "", ""),
                ("IR006", "陳小華\nTest Chen", "2026/11/14 09:32", "LAXSR", PAX, "")],
    },
    "ECSR/TPEWS/26000002": {
        "investigate": [("TPEKP", "2026/08/14 09:45", "2026/08/20 10:44")],
        "handle": ["TPEKP"], "reference": ["TPEWS"], "outline": "<p>Baggage delay on BR0012.</p>",
        "irs": [("IR001", "王小明\nTest Wang", "2026/08/14 14:58", "TPEKP", "", "")],
    },
    "ECSR/BKKKP/26000003": {   # Related Station 沒有 TPEWT，但有待 TPEWT 回覆的 IR
        "investigate": [("BKKKP", "2026/11/01 09:00", "")],
        "handle": ["BKKKP"], "reference": [], "outline": "<p>Seat issue on BR0068.</p>",
        "irs": [("IR001", "王小明\nTest Wang", "2026/11/02 10:00", "BKKKP", PAX, "TPEKP TPEWT")],
    },
}

CSS = """body{font-family:sans-serif;background:#d4f1f4} .tab{display:inline-block;padding:6px 14px;cursor:pointer}
table{border-collapse:collapse} td,th{border:1px solid #ccc;padding:4px 10px;white-space:pre-line}
.lbl{display:inline-block;background:#eee;padding:6px;width:220px} #wait{display:none;position:fixed;
top:40%;left:35%;background:#333;color:#fff;font-size:30px;padding:20px 80px} .btn{cursor:pointer;
background:#29b6d8;color:#fff;padding:5px 12px;border-radius:3px;border:0}"""


def page(title: str, body: str, script: str = "") -> bytes:
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title>"
            f"<style>{CSS}</style></head><body>{body}<div id='wait'>Please wait...</div>"
            f"<script>{script}</script></body></html>").encode("utf-8")


def search_page(v: str) -> bytes:
    body = """<h2>Customer Service Report Information System</h2>
<div><span class=tab>CSR</span><span class=tab>IR</span><span class=tab>DCA</span></div>
<div id=form>
 <div><span class=lbl>CSR Ref No</span> <input type=text value="ECSR" readonly> / <input type=text id=s> / <input type=text id=n></div>
 <div><span class=lbl>Received Date Period</span> <input type=text value="2024/11/25"> ~ <input type=text value="2026/11/25"></div>
 <div><span class=lbl>PAX Name</span> <input type=text></div>
 <a class=btn>Advance</a> <a class=btn id=go>Search</a> <a class=btn>Clear</a>
</div><div id=result></div>"""
    script = f"""
const CASES = {json.dumps(sorted(CASES))}, V = {json.dumps(v)};
document.getElementById('go').onclick = () => {{
  const ref = 'ECSR/' + s.value.trim().toUpperCase() + '/' + n.value.trim();
  wait.style.display = 'block';
  setTimeout(() => {{
    wait.style.display = 'none'; form.style.display = 'none';
    const hit = CASES.filter(c => c === ref);
    const tgt = V === 'b' ? " target='_blank'" : '';
    result.innerHTML = "<a class=btn>Renew</a><table><tr><th>CSR Ref No</th><th>PAX Name</th><th>Handle By</th><th>Status</th></tr>" +
      hit.map(c => `<tr><td><a href='/${{V}}/case?CSRRefNo=${{c}}'${{tgt}}>${{c}}</a></td><td>TESTER/A****</td><td>LAXSR</td><td>Handling</td></tr>`).join('') + "</table>";
  }}, 500);
}};"""
    return page("CSR", body, script)


def _table(heads, rows, split: bool) -> str:
    th = "".join(f"<th>{h}</th>" for h in heads)
    tr = "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in r) + "</tr>" for r in rows)
    if split:   # 表頭、內容各一張 table（jqGrid / Kendo 類格線的做法）
        return f"<div><table><tr>{th}</tr></table></div><div><table>{tr}</table></div>"
    return f"<table><tr>{th}</tr>{tr}</table>"


def case_page(v: str, ref: str) -> bytes:
    c = CASES[ref]
    split = v == "b"
    related = ("<div><span class=lbl>Investigate &amp; Report Dept.</span>"
               + _table(["Station", "Investigation Date", "Reply Date", "Del"],
                        [r + ("🗑",) for r in c["investigate"]], split)
               + "</div><div><span class=lbl>Handle &amp; Reply Dept.</span>"
               + _table(["Station", "Del"], [(s, "🗑") for s in c["handle"]], split)
               + "</div><div><span class=lbl>For Reference Only.</span>"
               + _table(["Station", "Del"], [(s, "🗑") for s in c["reference"]], split) + "</div>")
    if v == "b":
        box = f"<iframe id=ed style='width:900px;height:200px' srcdoc=\"{html.escape(c['outline'])}\"></iframe>"
    else:
        box = f"<div style='height:200px;overflow:auto;background:#e4e4ee'>{c['outline']}</div>"
    outline = (f"<div><span class=lbl>Outline &amp; Causes<span style='color:red'>*</span></span>{box}</div>"
               "<div style='background:#f37043;color:#fff'>CSR Ref.</div>"
               "<table><tr><th>CSR Ref No</th></tr></table>"
               "<div><span class=lbl>Attachment</span><table><tr><th>File Name</th><th>Creator</th></tr></table></div>")
    ir = ("<div><span class=lbl>Investigate &amp; Reply</span></div>"
          + _table(["IR Ref No.", "Creator", "ApprovedDate", "Create Dept.", "Pax Name",
                    "Investigate & Report Dept."], c["irs"], split)
          + "<div><span class=lbl>Legal &amp; Insurance Div. Comment:</span></div>"
            "<div><span class=lbl>Compensation</span></div>"
            "<table><tr><th>CPN Ref No.</th><th>CPN Items</th><th>Station</th><th>Status</th></tr></table>"
            "<div><span class=lbl>Closure Remark</span></div>")
    panels = {"Creator": "<div><span class=lbl>Creator</span> Test User</div>", "Related Station": related,
              "PAX Info": "", "Itinerary": "", "Outline": outline, "IR/CPN": ir,
              "Combination": "", "Category": "", "Approval": ""}
    tabs = "".join(f"<span class=tab data-t='{html.escape(t)}'>{html.escape(t)}</span>" for t in panels)
    body = (f"<div><b>CSR</b> {ref} <a>Home</a> | <a>My Draft</a> | <a>My Task</a> "
            "<a class=btn>Comment</a> <a class=btn>CPN</a> <a class=btn>Preview/Print</a> "
            "<a class=btn>Tracing</a> <a class=btn>Forward</a></div>"
            f"<div>{tabs}</div><div id=panel></div>")
    script = f"""
const P = {json.dumps(panels)}, V = {json.dumps(v)}, REF = {json.dumps(ref)};
function show(t) {{
  wait.style.display = 'block'; panel.innerHTML = '';
  setTimeout(() => {{ wait.style.display = 'none'; panel.innerHTML = P[t];
    panel.querySelectorAll('tr').forEach(tr => {{
      const first = tr.children[0] && tr.children[0].innerText.trim();
      if (!/^IR\\d+$/.test(first)) return;
      const open = () => {{ const u = `/${{V}}/ir?no=${{REF}}-${{first}}`;
                            V === 'b' ? window.open(u, '_blank') : (location.href = u); }};
      V === 'b' ? (tr.ondblclick = open) : (tr.onclick = open);
    }});
  }}, 400);
}}
document.querySelectorAll('.tab').forEach(e => e.onclick = () => show(e.dataset.t));
show('Creator');"""
    return page("CSR " + ref, body, script)


def ir_page(v: str, no: str) -> bytes:
    box = (f"<iframe style='width:900px;height:200px' srcdoc=\"{html.escape(IR_BODY)}\"></iframe>"
           if v == "b" else f"<div style='height:200px;overflow:auto;background:#e4e4ee'>{IR_BODY}</div>")
    result = (f"<div><span class=lbl>PAX Info.</span><div style='white-space:pre-line'>{PAX}</div></div>{box}"
              "<div style='background:#f37043;color:#fff'>Source No Ref.</div>"
              "<div><span class=lbl>Attachment</span></div><div style='background:#f37043;color:#fff'>PNR Link</div>")
    body = (f"<div><b>IR</b> {no} <a class=btn>Open CSR Case</a> <a class=btn>Read</a> <a class=btn>Print</a></div>"
            "<div><span class=tab>Creator</span><span class=tab>Related Station</span>"
            "<span class=tab id=t>Investigation Result</span><span class=tab>Approval</span></div><div id=panel></div>")
    script = f"""const R = {json.dumps(result)};
t.onclick = () => {{ wait.style.display='block'; setTimeout(() => {{ wait.style.display='none'; panel.innerHTML = R; }}, 300); }};
t.onclick();"""
    return page("IR " + no, body, script)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        parts = [p for p in u.path.split("/") if p]
        v = parts[0] if parts and parts[0] in ("a", "b") else "a"
        kind = parts[1] if len(parts) > 1 else "search"
        try:
            if kind == "case":
                body = case_page(v, q["CSRRefNo"][0])
            elif kind == "ir":
                body = ir_page(v, q["no"][0])
            else:
                body = search_page(v)
            code = 200
        except KeyError:
            body, code = b"not found", 404
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(port: int = 0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    s = serve(8765)
    print("http://127.0.0.1:8765/a/  或  /b/")
    threading.Event().wait()
