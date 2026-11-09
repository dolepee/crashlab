"""A terminal retry response is only recovery when its body transport completes."""
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import crashlab_core as core

BROWSER = os.environ.get('CRASHLAB_BROWSER_TESTS') == '1'

HTML = '''<!doctype html><html><body>
<button id="send">Send</button><div id="status">Ready</div>
<button id="retry" style="display:none" onclick="send(true)">Retry</button>
<script>
const params = new URLSearchParams(location.search);
const key = 'stable';
const intent = params.has('control') ? 'control-' + crypto.randomUUID() : 'intent';
async function send(retry) {
  const status = document.getElementById('status');
  status.textContent = 'Sending';
  try {
    const response = await fetch('/create' + location.search, {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify({intent_key: intent, item: 'widget'}),
    });
    if (params.has('html')) {
      const text = await response.text();
      if (!response.ok || !text.includes('Order confirmed')) throw new Error('not complete');
    } else {
      const value = await response.json();
      if (!response.ok || value.id === undefined) throw new Error('not complete');
    }
    status.textContent = 'Order confirmed';
    document.getElementById('retry').style.display = 'none';
  } catch (error) {
    status.textContent = 'Could not confirm';
    document.getElementById('retry').style.display = 'inline-block';
  }
}
document.getElementById('send').onclick = () => send(false);
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    db = None
    seen = None

    def log_message(self, *args):
        pass

    def _json(self, code, value):
        raw = json.dumps(value).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/':
            body = HTML.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == '/state':
            with sqlite3.connect(self.db) as con:
                rows = con.execute('SELECT id,intent_key,item FROM orders ORDER BY id').fetchall()
            self._json(200, {'orders': [dict(zip(('id', 'intent_key', 'item'), row)) for row in rows]})
        elif parsed.path == '/recovery':
            body = b'Order confirmed by the server.'
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == '/truncated':
            body = b'Recovered'
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', '100')
            self.end_headers()
            self.wfile.write(body[:9])
            self.wfile.flush()
            self.close_connection = True
        else:
            self._json(404, {'error': 'not found'})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == '/reset':
            with sqlite3.connect(self.db) as con:
                con.execute('DELETE FROM orders')
            self.seen.clear()
            self._json(200, {'ok': True})
            return
        if parsed.path != '/create':
            self._json(404, {'error': 'not found'})
            return
        length = int(self.headers.get('content-length', '0'))
        body = json.loads(self.rfile.read(length) or b'{}')
        intent = str(body.get('intent_key') or '')
        item = str(body.get('item') or 'widget')
        mode = parse_qs(parsed.query).get('mode', [''])[0]
        with sqlite3.connect(self.db) as con:
            con.execute('INSERT OR IGNORE INTO orders(intent_key,item) VALUES (?,?)', (intent, item))
            row = con.execute('SELECT id FROM orders WHERE intent_key=?', (intent,)).fetchone()
        record_id = row[0] if row else 1
        if mode in ('complete', 'truncated'):
            marker = (intent, mode)
            if marker not in self.seen:
                self.seen.add(marker)
                self._json(201, {'id': record_id})
                return
            self.send_response(302)
            self.send_header('Location', '/recovery' if mode == 'complete' else '/truncated')
            self.end_headers()
            return
        self._json(201, {'id': record_id})


@unittest.skipUnless(BROWSER, 'browser terminal-transport tests are opt-in')
class TerminalTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.temp.name) / 'orders.db'
        Handler.db = str(cls.db)
        Handler.seen = set()
        with sqlite3.connect(cls.db) as con:
            con.execute('CREATE TABLE IF NOT EXISTS orders '
                        '(id INTEGER PRIMARY KEY, intent_key TEXT UNIQUE, item TEXT)')
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:%s' % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def case(self, mode):
        path = '/?html=1&mode=' + mode
        state = {
            'mode': 'sqlite', 'db': str(self.db), 'table': 'orders',
            'where': "intent_key <> ''", 'expect': 1,
            'record_fields': {'item': 'widget'},
            'request_fields': {'intent_key': 'intent_key', 'item': 'item'},
        }
        return {
            'id': 'terminal-transport-' + mode,
            'app': {'base_url': self.base, 'reset_url': '/reset'},
            'journey': [
                {'action': 'goto', 'path': path},
                {'action': 'click', 'selector': '#send'},
                {'action': 'wait_for_fault'},
                {'action': 'wait_for_selector', 'selector': '#retry'},
                {'action': 'click', 'selector': '#retry'},
                {'action': 'wait_for_timeout', 'ms': 600},
                {'action': 'text', 'selector': '#status', 'as': 'recovery_ui'},
            ],
            'fault': {'route': '**/create*', 'mode': 'suppress_after_upstream',
                      'occurrence': 1, 'method': 'POST'},
            'state': state,
            'control': {
                'journey': [
                    {'action': 'goto', 'path': '/?control=1'},
                    {'action': 'click', 'selector': '#send'},
                    {'action': 'wait_for_text', 'text': 'Order confirmed'},
                    {'action': 'reload'},
                    {'action': 'click', 'selector': '#send'},
                    {'action': 'wait_for_text', 'text': 'Order confirmed'},
                ],
                'state': dict(state, expect=2),
            },
            'recovery': {'mode': 'retry', 'success_statuses': [200]},
        }

    def run_case(self, mode):
        path = Path(self.temp.name) / ('case-' + mode + '.json')
        path.write_text(json.dumps(self.case(mode)))
        return core.run_contract(path)

    def test_complete_html_terminal_response_passes(self):
        result = self.run_case('complete')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['status'], 'PASS')
        terminal = result['recovery']['terminal_response']
        self.assertEqual(terminal['status'], 200)
        self.assertTrue(terminal['body_complete'], terminal)
        self.assertIsNone(terminal['body_error'])
        self.assertEqual(result['observations']['recovery_ui'], 'Order confirmed')

    def test_truncated_html_terminal_response_is_rejected(self):
        result = self.run_case('truncated')
        self.assertFalse(result['ok'], result)
        self.assertFalse(result['recovery']['ok'], result)
        self.assertFalse(result['recovery']['terminal_transport_complete'], result)
        terminal = result['recovery']['terminal_response']
        self.assertEqual(terminal['status'], 200)
        self.assertFalse(terminal['body_complete'], terminal)
        self.assertTrue(terminal['body_error'], terminal)
        self.assertIn('transport did not complete', result['recovery']['reason'])
        self.assertEqual(result['observations']['recovery_ui'], 'Could not confirm')
        self.assertFalse(core._completed_assertion(result, passed=True), result)


if __name__ == '__main__':
    unittest.main()
