"""Recovery-contract regressions: terminal outcomes, selected headers and unsafe IDs."""
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


class Handler(BaseHTTPRequestHandler):
    db = None

    def log_message(self, *args):
        pass

    def _json(self, code, value):
        raw = value if isinstance(value, bytes) else json.dumps(value).encode()
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
            query = parse_qs(parsed.query)
            if query.get('unsafe'):
                self._json(200, b'{"orders":[{"id":9007199254740993,"intent_key":"intent","item":"widget"}]}')
                return
            with sqlite3.connect(self.db) as con:
                rows = con.execute('SELECT id,intent_key,item FROM orders ORDER BY id').fetchall()
            self._json(200, {'orders': [dict(zip(('id', 'intent_key', 'item'), row)) for row in rows]})
        elif parsed.path == '/done':
            query = parse_qs(parsed.query)
            self._json(200, {'id': int(query.get('id', ['1'])[0])})
        elif parsed.path == '/receipt':
            query = parse_qs(parsed.query)
            if query.get('unsafe'):
                self._json(200, b'{"order":{"id":9007199254740993}}')
            else:
                self._json(200, {'order': {'id': 1}})
        elif parsed.path == '/failed':
            self._json(404, {'error': 'not found'})
        else:
            self._json(404, {'error': 'not found'})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == '/reset':
            with sqlite3.connect(self.db) as con:
                con.execute('DELETE FROM orders')
            self._json(200, {'ok': True})
            return
        if parsed.path != '/create':
            self._json(404, {'error': 'not found'})
            return
        length = int(self.headers.get('content-length', '0'))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw or b'{}')
        except ValueError:
            self._json(400, {'error': 'invalid json'})
            return
        intent = str(body.get('intent_key') or 'intent')
        item = str(body.get('item') or 'widget')
        mode = parse_qs(parsed.query).get('mode', [body.get('mode') or ''])[0]
        with sqlite3.connect(self.db) as con:
            con.execute('CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, intent_key TEXT UNIQUE, item TEXT)')
            con.execute('INSERT OR IGNORE INTO orders(intent_key,item) VALUES (?,?)', (intent, item))
            row = con.execute('SELECT id FROM orders WHERE intent_key=?', (intent,)).fetchone()
            record_id = row[0] if row else 1
        if mode == '202':
            self._json(202, {'error': 'pending'})
        elif mode == '200':
            self._json(200, {'id': record_id})
        elif mode.startswith('302'):
            self.send_response(302)
            self.send_header('Location', '/done?id=%s' % record_id if 'bad' not in parsed.query else '/failed')
            self.end_headers()
        elif mode == '399':
            self._json(399, {'error': 'not terminal'})
        elif mode == 'wrongid':
            self._json(201, {'id': record_id + 1})
        elif mode == 'unsafe':
            self._json(201, b'{"id":9007199254740993}')
        else:
            self._json(201, {'id': record_id})


HTML = '''<!doctype html><html><body>
<button id="send">Send</button><div id="status">Ready</div>
<button id="retry" style="display:none" onclick="send(true)">Retry</button>
<script>
let sent = false;
async function send(retry=false) {
  const params = new URLSearchParams(location.search);
  const key = retry && params.has('change') ? 'changed' : 'stable';
  const intent = params.has('control') ? 'control-' + crypto.randomUUID() : 'intent';
  const raw = params.has('raw')
    ? '{"intent_key":"intent","item":"widget","id":9007199254740993}'
    : JSON.stringify({intent_key:intent,item:'widget',mode:params.get('mode') || ''});
  try {
    const response = await fetch('/create' + location.search, {
      method: 'POST',
      headers: {'content-type':'application/json','Idempotency-Key':key},
      body: raw,
    });
    const value = await response.json();
    if (!response.ok || value.id === undefined) throw new Error('not complete');
    document.getElementById('status').textContent = 'Saved';
    document.getElementById('retry').style.display = 'none';
  } catch (error) {
    document.getElementById('status').textContent = 'Could not confirm';
    document.getElementById('retry').style.display = 'inline-block';
  }
}
document.getElementById('send').onclick = () => send(false);
</script></body></html>'''


@unittest.skipUnless(BROWSER, 'browser recovery-contract tests are opt-in')
class RecoveryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get('CHROME_PATH'):
            raise unittest.SkipTest('CHROME_PATH is required')
        cls.temp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.temp.name) / 'orders.db'
        Handler.db = str(cls.db)
        with sqlite3.connect(cls.db) as con:
            con.execute('CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, intent_key TEXT UNIQUE, item TEXT)')
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:%s' % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def case(self, *, mode='', statuses=(201,), headers=None, response_id=True,
             unsafe_state=False, control_state=None, raw=False, change=False):
        query = []
        if mode:
            query.append('mode=' + mode)
        if unsafe_state:
            query.append('unsafe=1')
        if raw:
            query.append('raw=1')
        if change:
            query.append('change=1')
        state_url = '/state?unsafe=1' if unsafe_state else '/state'
        path = '/' + ('?' + '&'.join(query) if query else '')
        case = {
            'id': 'contract-' + mode + ('-unsafe' if unsafe_state else '') + ('-change' if change else ''),
            'app': {'base_url': self.base, 'reset_url': '/reset'},
            'journey': [
                {'action': 'goto', 'path': path},
                {'action': 'click', 'selector': '#send'},
                {'action': 'wait_for_fault'},
                {'action': 'wait_for_selector', 'selector': '#retry'},
                {'action': 'click', 'selector': '#retry'},
                {'action': 'wait_for_timeout', 'ms': 800},
            ],
            'fault': {'route': '**/create*', 'mode': 'suppress_after_upstream', 'occurrence': 1, 'method': 'POST'},
            'state': {'mode': 'http', 'url': state_url, 'path': 'orders', 'expect': 1,
                      'record_fields': {'item': 'widget'}},
            'recovery': {'mode': 'retry', 'success_statuses': list(statuses)},
            'evidence': {'screenshot_dir': str(Path(self.temp.name) / 'shots')},
        }
        if response_id:
            case['recovery'].update(response_id_path='id', record_id_field='id')
        if headers is not None:
            case['recovery']['identity_headers'] = headers
        if control_state:
            case['control'] = {
                'journey': [
                    {'action': 'goto', 'path': '/?control=1'},
                    {'action': 'click', 'selector': '#send'},
                    {'action': 'wait_for_text', 'text': 'Saved'},
                    {'action': 'reload'},
                    {'action': 'click', 'selector': '#send'},
                    {'action': 'wait_for_text', 'text': 'Saved'},
                ],
                'state': {'mode': 'http', 'url': control_state, 'path': 'orders', 'expect': 2,
                          'record_fields': {'item': 'widget'}},
            }
        return case

    def run_case(self, **kwargs):
        path = Path(self.temp.name) / 'case.json'
        path.write_text(json.dumps(self.case(**kwargs)))
        return core.run_contract(path)

    def test_json_reply_identity_passes(self):
        result = self.run_case()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['recovery']['terminal_response']['status'], 201)
        self.assertTrue(result['recovery']['response_identity']['ok'], result)

    def test_no_response_predicate_states_its_boundary(self):
        result = self.run_case(response_id=False)
        self.assertTrue(result['ok'], result)
        self.assertFalse(result['recovery']['response_identity']['configured'])
        self.assertIn('no response identity predicate was configured', result['recovery']['reason'])

    def test_json_reply_identity_200_passes(self):
        result = self.run_case(mode='200', statuses=(200,))
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['recovery']['terminal_response']['status'], 200)
        self.assertTrue(result['recovery']['response_identity']['ok'], result)

    def test_wrong_json_reply_identity_fails(self):
        result = self.run_case(mode='wrongid')
        self.assertFalse(result['ok'], result)
        self.assertFalse(result['recovery']['response_identity']['ok'], result)
        self.assertEqual(result['status'], 'FAIL')

    def test_202_is_not_recovery(self):
        result = self.run_case(mode='202')
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['recovery']['terminal_response']['status'], 202)

    def test_399_is_not_recovery(self):
        result = self.run_case(mode='399')
        self.assertFalse(result['ok'], result)

    def test_followed_redirect_uses_terminal_response(self):
        result = self.run_case(mode='302', statuses=(200,))
        self.assertTrue(result['ok'], result)
        terminal = result['recovery']['terminal_response']
        self.assertEqual(terminal['status'], 200)
        self.assertIn('/create', terminal['redirected_from'])
        statuses = [event['status'] for event in result['events'] if event['kind'] == 'response']
        self.assertEqual(statuses, [302, 200])

    def test_redirect_to_failure_is_not_recovery(self):
        result = self.run_case(mode='302bad', statuses=(200,))
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['recovery']['terminal_response']['status'], 404)

    def test_selected_identity_header_stable_passes(self):
        result = self.run_case(headers=['Idempotency-Key'])
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['recovery']['identity_headers_match'], result)

    def test_selected_identity_header_change_fails(self):
        result = self.run_case(headers=['Idempotency-Key'], change=True)
        self.assertFalse(result['ok'], result)
        self.assertFalse(result['recovery']['identity_headers_match'], result)

    def test_unsafe_primary_response_is_rejected(self):
        result = self.run_case(mode='unsafe')
        self.assertFalse(result['ok'], result)
        self.assertIn('unsafe integer', result['recovery']['reason'])

    def test_unsafe_observed_state_is_rejected(self):
        result = self.run_case(unsafe_state=True)
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['status'], 'ERROR')
        self.assertIn('unsafe integer', result.get('error', ''))

    def test_unsafe_observed_control_is_rejected(self):
        result = self.run_case(control_state='/state?unsafe=1')
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['status'], 'ERROR')

    def test_unsafe_request_body_is_rejected(self):
        result = self.run_case(raw=True)
        self.assertFalse(result['ok'], result)
        self.assertIn('unsafe integer', result['recovery']['reason'])

    def test_reconciliation_uses_configured_success_status(self):
        case = {
            'id': 'reconcile-test',
            'app': {'base_url': self.base, 'reset_url': '/reset'},
            'journey': [
                {'action': 'goto', 'path': '/'},
                {'action': 'click', 'selector': '#send'},
                {'action': 'wait_for_fault'},
                {'action': 'goto', 'path': '/receipt?intent_key=intent'},
                {'action': 'wait_for_timeout', 'ms': 300},
            ],
            'fault': {'route': '**/create*', 'mode': 'suppress_after_upstream', 'occurrence': 1, 'method': 'POST'},
            'state': {'mode': 'http', 'url': '/state', 'path': 'orders', 'expect': 1,
                      'record_fields': {'item': 'widget'}},
            'recovery': {'mode': 'reconcile', 'route': '**/receipt?*', 'initial_field': 'intent_key',
                         'query_parameter': 'intent_key', 'response_id_path': 'order.id',
                         'record_id_field': 'id', 'success_statuses': [200]},
        }
        path = Path(self.temp.name) / 'reconcile.json'
        path.write_text(json.dumps(case))
        result = core.run_contract(path)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['recovery']['confirmations'], 1)

    def test_unsafe_reconciliation_response_is_rejected(self):
        case = {
            'id': 'reconcile-unsafe',
            'app': {'base_url': self.base, 'reset_url': '/reset'},
            'journey': [
                {'action': 'goto', 'path': '/'},
                {'action': 'click', 'selector': '#send'},
                {'action': 'wait_for_fault'},
                {'action': 'goto', 'path': '/receipt?intent_key=intent&unsafe=1'},
                {'action': 'wait_for_timeout', 'ms': 300},
            ],
            'fault': {'route': '**/create*', 'mode': 'suppress_after_upstream', 'occurrence': 1, 'method': 'POST'},
            'state': {'mode': 'http', 'url': '/state', 'path': 'orders', 'expect': 1,
                      'record_fields': {'item': 'widget'}},
            'recovery': {'mode': 'reconcile', 'route': '**/receipt?*', 'initial_field': 'intent_key',
                         'query_parameter': 'intent_key', 'response_id_path': 'order.id',
                         'record_id_field': 'id', 'success_statuses': [200]},
        }
        path = Path(self.temp.name) / 'reconcile-unsafe.json'
        path.write_text(json.dumps(case))
        result = core.run_contract(path)
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['recovery']['confirmations'], 0)
        self.assertIn('unsafe integer', result['recovery']['responses'][0]['response_error'])


class SafeIntegerValidationTests(unittest.TestCase):
    def case(self):
        return {
            'id': 'case', 'app': {'base_url': 'http://127.0.0.1:1'},
            'journey': [{'action': 'goto', 'path': '/'}],
            'fault': {'route': '**/create*', 'mode': 'suppress_after_upstream', 'occurrence': 1, 'method': 'POST'},
            'recovery': {'mode': 'retry', 'success_statuses': [200]},
            'state': {'url': '/state', 'path': 'rows', 'expect': 1},
        }

    def test_unsafe_case_integer_is_rejected_globally(self):
        value = self.case()
        value['state']['record_fields'] = {'id': 9007199254740993}
        with self.assertRaisesRegex(ValueError, 'outside the safe integer range'):
            core.validate_contract(value)

    def test_decimal_string_identity_is_supported(self):
        value = self.case()
        value['state']['record_fields'] = {'id': '9007199254740993'}
        core.validate_contract(value)

    def test_explicit_success_statuses_are_required(self):
        value = self.case()
        value['recovery'].pop('success_statuses')
        with self.assertRaisesRegex(ValueError, 'success_statuses'):
            core.validate_contract(value)

    def test_non_terminal_success_statuses_are_rejected(self):
        for status in (202, 302, 399):
            with self.subTest(status=status):
                value = self.case()
                value['recovery']['success_statuses'] = [status]
                with self.assertRaisesRegex(ValueError, 'terminal 2xx'):
                    core.validate_contract(value)

    def test_malformed_recovery_options_are_rejected(self):
        for update in (
            {'success_statuses': [200, 200]},
            {'success_statuses': [True]},
            {'identity_headers': ['Idempotency-Key', 'idempotency-key']},
            {'identity_headers': []},
            {'unknown_option': True},
            {'response_id_path': 'id'},
            {'response_id_path': None, 'record_id_field': 'id'},
        ):
            with self.subTest(update=update):
                value = self.case()
                value['recovery'].update(update)
                with self.assertRaises(ValueError):
                    core.validate_contract(value)

    def test_selected_identity_headers_are_configurable(self):
        value = self.case()
        value['recovery']['identity_headers'] = ['Idempotency-Key', 'X-Request-Id']
        core.validate_contract(value)


if __name__ == '__main__':
    unittest.main()
