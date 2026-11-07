"""Disposable native-POST fixture for browser retry acceptance tests."""
import os
import sqlite3
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

DB = os.environ['CRASHLAB_DB']
BUGGY = os.environ.get('BUGGY', '1') == '1'
with sqlite3.connect(DB) as db:
    db.execute('CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, intent_key TEXT, item TEXT)')

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            body = ('<form method="post" action="/create"><input name="intent_key" type="hidden" value="'+str(uuid.uuid4())+'"><input id="item" name="item"><button id="submit">Create</button></form>')
        else:
            body = '<p>Saved</p>'
        self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(body.encode())
    def do_POST(self):
        fields=parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode())
        key,item=fields['intent_key'][0],fields['item'][0]
        with sqlite3.connect(DB) as db:
            if BUGGY or not db.execute('SELECT 1 FROM orders WHERE intent_key=?',(key,)).fetchone():
                db.execute('INSERT INTO orders(intent_key,item) VALUES (?,?)',(key,item))
        self.send_response(302);self.send_header('Location','/done');self.end_headers()
    def log_message(self,*args):pass

if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1',int(os.environ['PORT'])),Handler).serve_forever()
