"""Second CrashLab fixture: a booking flow with the same lost-response defect."""
from __future__ import annotations

import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get("CRASHLAB_DB", ROOT / "data" / "bookings.db"))
BUGGY = os.environ.get("BUGGY", "1") == "1"
PORT = int(os.environ.get("PORT", "8785"))


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute(
        "CREATE TABLE IF NOT EXISTS bookings ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "intent_key TEXT NOT NULL,"
        "slot TEXT NOT NULL,"
        "created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    if not BUGGY:
        try:
            con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS bookings_intent_key_uq "
                "ON bookings(intent_key) WHERE intent_key <> ''"
            )
            con.commit()
        except sqlite3.IntegrityError:
            con.rollback()
    return con


HTML = """<!doctype html><html><head><meta charset='utf-8'><title>CrashLab Booking</title>
<style>body{font-family:system-ui;max-width:430px;margin:70px auto;padding:20px}button{font-size:18px;padding:12px 18px;margin-top:16px}#status{margin-top:18px;padding:12px;background:#eee}.error{background:#ffe0e0!important;color:#8a1c1c}</style>
</head><body><h1>Book a slot</h1><p>Thursday, 10:00 — 30 minutes</p><button id='book'>Book this slot</button><div id='status'>Ready.</div>
<script>let key=crypto.randomUUID();const status=document.getElementById('status');async function book(){status.className='';status.textContent='Booking…';try{const r=await fetch('/book',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({intent_key:key,slot:'Thu 10:00'})});if(!r.ok)throw new Error('HTTP '+r.status);const b=await r.json();status.textContent='Booking confirmed: #'+b.id}catch(e){status.className='error';status.innerHTML='Could not confirm the booking. <button id=\"retry\">Try again</button>';document.getElementById('retry').onclick=book}}document.getElementById('book').onclick=book;</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def sendj(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/":
            raw = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        elif self.path == "/state":
            con = connect()
            rows = con.execute("SELECT id,intent_key,slot,created_at FROM bookings ORDER BY id").fetchall()
            con.close()
            self.sendj(200, {"bookings": [dict(zip(("id", "intent_key", "slot", "created_at"), r)) for r in rows]})
        else:
            self.sendj(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/reset":
            con = connect()
            con.execute("DELETE FROM bookings")
            con.commit()
            con.close()
            self.sendj(200, {"ok": True})
            return
        if self.path != "/book":
            self.sendj(404, {"error": "not found"})
            return
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        intent = str(body.get("intent_key") or "")
        slot = str(body.get("slot") or "Unknown")
        con = connect()
        try:
            if not BUGGY:
                con.execute("BEGIN IMMEDIATE")
                row = con.execute("SELECT id FROM bookings WHERE intent_key=?", (intent,)).fetchone()
                if row:
                    con.commit()
                    self.sendj(200, {"id": row[0], "deduplicated": True})
                    return
            try:
                cur = con.execute("INSERT INTO bookings(intent_key,slot) VALUES(?,?)", (intent, slot))
                con.commit()
            except sqlite3.IntegrityError:
                con.rollback()
                row = con.execute("SELECT id FROM bookings WHERE intent_key=?", (intent,)).fetchone()
                if not row:
                    raise
                self.sendj(200, {"id": row[0], "deduplicated": True})
                return
            self.sendj(200, {"id": cur.lastrowid, "deduplicated": False})
        finally:
            con.close()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"CrashLab booking fixture on {PORT} buggy={BUGGY} db={DB}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
