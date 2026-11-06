"""Tiny checkout fixture for CrashLab Day 1.

POST /order persists before responding. With BUGGY=1 it ignores the idempotency
key, so a retry after a lost response creates a second order. With BUGGY=0 the
key is unique and the retry returns the original order.
"""
from __future__ import annotations

import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get("CRASHLAB_DB", ROOT / "data" / "orders.db"))
BUGGY = os.environ.get("BUGGY", "1") == "1"
FAIL_FIRST = os.environ.get("FAIL_FIRST", "0") == "1"
FAILED_FIRST = False
PORT = int(os.environ.get("PORT", "8765"))


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute(
        "CREATE TABLE IF NOT EXISTS orders ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "intent_key TEXT NOT NULL,"
        "item TEXT NOT NULL,"
        "created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    if not BUGGY:
        try:
            con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS orders_intent_key_uq "
                "ON orders(intent_key) WHERE intent_key <> ''"
            )
            con.commit()
        except sqlite3.IntegrityError:
            # A legacy database can contain duplicates; the transactional
            # lookup below still handles new writes.
            con.rollback()
    return con


HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>CrashLab Checkout</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:420px;margin:70px auto;padding:20px}
 button{font-size:18px;padding:12px 18px;margin-top:16px;cursor:pointer}
 #status{margin-top:18px;padding:12px;border-radius:6px;background:#eee}
 .error{background:#ffe0e0!important;color:#8a1c1c}
</style></head>
<body>
<h1>Checkout</h1>
<p>One item: CrashLab Test Widget — $19.00</p>
<button id="buy">Place order</button>
<div id="status">Ready.</div>
<script>
let key = crypto.randomUUID();
const status = document.getElementById('status');
async function order() {
  status.className = '';
  status.textContent = 'Placing order…';
  try {
    const r = await fetch('/order', {method:'POST', headers:{'content-type':'application/json'},
      body: JSON.stringify({intent_key:key, item:'CrashLab Test Widget'})});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const body = await r.json();
    status.textContent = 'Order confirmed: #' + body.id;
  } catch (e) {
    status.className = 'error';
    status.innerHTML = 'Could not confirm the order. <button id="retry">Try again</button>';
    document.getElementById('retry').onclick = order;
  }
}
document.getElementById('buy').onclick = order;
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, content_type="application/json"):
        raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/":
            self._send(200, HTML, "text/html")
        elif self.path == "/state":
            con = connect()
            rows = con.execute("SELECT id,intent_key,item,created_at FROM orders ORDER BY id").fetchall()
            con.close()
            self._send(200, {"orders": [dict(zip(("id", "intent_key", "item", "created_at"), r)) for r in rows]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/reset":
            con = connect()
            try:
                con.execute("DELETE FROM orders")
                con.commit()
            finally:
                con.close()
            self._send(200, {"ok": True})
            return
        if self.path != "/order":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("content-length", "0"))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid json"})
            return
        intent = str(body.get("intent_key") or "")
        item = str(body.get("item") or "Unknown item")
        global FAILED_FIRST
        if FAIL_FIRST and not FAILED_FIRST:
            FAILED_FIRST = True
            self._send(503, {"error": "temporary upstream failure"})
            return
        con = connect()
        try:
            if not BUGGY:
                con.execute("BEGIN IMMEDIATE")
                row = con.execute("SELECT id FROM orders WHERE intent_key=?", (intent,)).fetchone()
                if row:
                    con.commit()
                    self._send(200, {"id": row[0], "deduplicated": True})
                    return
            try:
                cur = con.execute("INSERT INTO orders(intent_key,item) VALUES(?,?)", (intent, item))
                con.commit()
            except sqlite3.IntegrityError:
                con.rollback()
                row = con.execute("SELECT id FROM orders WHERE intent_key=?", (intent,)).fetchone()
                if not row:
                    raise
                self._send(200, {"id": row[0], "deduplicated": True})
                return
            self._send(200, {"id": cur.lastrowid, "deduplicated": False})
        finally:
            con.close()

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))


if __name__ == "__main__":
    print(f"CrashLab fixture on http://127.0.0.1:{PORT} buggy={BUGGY} db={DB}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
