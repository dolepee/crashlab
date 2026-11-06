"""Checkout fixture for CrashLab repair handoff (idempotent order creation)."""
from __future__ import annotations
import json, os, sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DB=Path(os.environ.get('CRASHLAB_DB', ROOT/'orders.db'))
PORT=int(os.environ.get('PORT','8775'))
def connect():
    con=sqlite3.connect(DB, timeout=10)
    con.execute('CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, intent_key TEXT NOT NULL, item TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime(\'now\')))')
    try:
        # Enforce one order per non-empty intent key. Legacy databases may already
        # contain duplicates, in which case the transactional check below still
        # provides idempotency until those rows are cleaned up.
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS orders_intent_key_uq ON orders(intent_key) WHERE intent_key <> ''")
        con.commit()
    except sqlite3.IntegrityError:
        con.rollback()
    return con
def create_order(intent_key, item):
    """Return (order_id, created) for the intent key, creating at most one order."""
    key=str(intent_key or '').strip()
    con=sqlite3.connect(DB, timeout=10, isolation_level=None)
    try:
        # IMMEDIATE takes the write lock before the lookup so a retry racing the
        # original insert can never observe "no row" twice.
        con.execute('BEGIN IMMEDIATE')
        if key:
            row=con.execute('SELECT id FROM orders WHERE intent_key=? ORDER BY id LIMIT 1',(key,)).fetchone()
            if row:
                con.execute('COMMIT')
                return int(row[0]), False
        cur=con.execute('INSERT INTO orders(intent_key,item) VALUES(?,?)',(key,item))
        order_id=int(cur.lastrowid)
        con.execute('COMMIT')
        return order_id, True
    except sqlite3.IntegrityError:
        con.execute('ROLLBACK')
        if key:
            row=con.execute('SELECT id FROM orders WHERE intent_key=? ORDER BY id LIMIT 1',(key,)).fetchone()
            if row:
                return int(row[0]), False
        raise
    finally:
        con.close()
HTML="""<!doctype html><html><head><meta charset='utf-8'><title>Checkout</title><style>body{font-family:system-ui;max-width:420px;margin:70px auto}button{font-size:18px;padding:12px 18px;margin-top:16px}#status{margin-top:18px;padding:12px;background:#eee}.error{background:#ffe0e0!important;color:#8a1c1c}</style></head><body><h1>Checkout</h1><p>One item: CrashLab Test Widget — $19.00</p><button id='buy'>Place order</button><div id='status'>Ready.</div><script>let key=crypto.randomUUID();const status=document.getElementById('status');async function order(){status.className='';status.textContent='Placing order…';try{const r=await fetch('/order',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({intent_key:key,item:'CrashLab Test Widget'})});if(!r.ok)throw new Error('HTTP '+r.status);const b=await r.json();status.textContent='Order confirmed: #'+b.id}catch(e){status.className='error';status.innerHTML='Could not confirm the order. <button id=\\"retry\\">Try again</button>';document.getElementById('retry').onclick=order}}document.getElementById('buy').onclick=order;</script></body></html>"""
class H(BaseHTTPRequestHandler):
    def sendj(self,code,obj):
        raw=json.dumps(obj).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        if self.path=='/': raw=HTML.encode(); self.send_response(200); self.send_header('Content-Type','text/html'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
        elif self.path=='/state':
            con=connect(); rows=con.execute('SELECT id,intent_key,item,created_at FROM orders ORDER BY id').fetchall(); con.close(); self.sendj(200,{'orders':[dict(zip(('id','intent_key','item','created_at'),r)) for r in rows]})
        else: self.sendj(404,{'error':'not found'})
    def do_POST(self):
        if self.path=='/reset':
            con=connect(); con.execute('DELETE FROM orders'); con.commit(); con.close(); self.sendj(200,{'ok':True}); return
        if self.path!='/order': self.sendj(404,{'error':'not found'}); return
        n=int(self.headers.get('content-length','0')); body=json.loads(self.rfile.read(n) or b'{}')
        key=str(body.get('intent_key') or '').strip()
        item=str(body.get('item') or 'Unknown')
        order_id, created = create_order(key, item)
        self.sendj(200,{'id':order_id,'created':created,'intent_key':key})
    def log_message(self,*a): pass
if __name__=='__main__': print('checkout app',PORT); ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
