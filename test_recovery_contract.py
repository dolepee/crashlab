"""Recovery-contract regressions: terminal outcomes, selected headers and unsafe IDs."""
import json
import os
import sqlite3
import subprocess
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
    seen_reset = None
    seen_redirects = None

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
            table = 'orders_uuid' if query.get('uuid') else 'orders_zero' if query.get('zero') else 'orders'
            with sqlite3.connect(self.db) as con:
                rows = con.execute(f'SELECT id,intent_key,item FROM {table} ORDER BY id').fetchall()
            self._json(200, {'orders': [dict(zip(('id', 'intent_key', 'item'), row)) for row in rows]})
        elif parsed.path == '/done':
            query = parse_qs(parsed.query)
            self._json(200, {'id': int(query.get('id', ['1'])[0])})
        elif parsed.path == '/done-html':
            body = b'<html><body>Order confirmed by the server.</body></html>'
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == '/truncated':
            body = b'<html><body>Order confirmed by the server.</body></html>'
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(body) + 100))
            self.end_headers()
            self.wfile.write(body[:9])
            self.close_connection = True
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
            self.seen_reset.clear()
            self.seen_redirects.clear()
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
        query = parse_qs(parsed.query)
        table = 'orders_uuid' if query.get('uuid') else 'orders_zero' if query.get('zero') else 'orders'
        with sqlite3.connect(self.db) as con:
            if table == 'orders':
                con.execute('INSERT OR IGNORE INTO orders(intent_key,item) VALUES (?,?)', (intent, item))
                row = con.execute('SELECT id FROM orders WHERE intent_key=?', (intent,)).fetchone()
                record_id = row[0] if row else 1
            elif table == 'orders_uuid':
                record_id = '123e4567-e89b-12d3-a456-426614174000'
                con.execute('INSERT OR IGNORE INTO orders_uuid(id,intent_key,item) VALUES (?,?,?)', (record_id, intent, item))
            else:
                record_id = '0001'
                con.execute('INSERT OR IGNORE INTO orders_zero(id,intent_key,item) VALUES (?,?,?)', (record_id, intent, item))
        reply_id = '1' if table == 'orders_zero' else record_id
        if mode == 'reset':
            marker = (intent, 'reset')
            closed = (intent, 'reset-closed')
            if marker not in self.seen_reset:
                self.seen_reset.add(marker)
                self._json(201, {'id': reply_id})
            elif closed not in self.seen_reset:
                self.seen_reset.add(closed)
                self.connection.close()
            else:
                self._json(201, {'id': reply_id})
            return
        if mode == '202':
            self._json(202, {'error': 'pending'})
        elif mode == '200':
            self._json(200, {'id': reply_id})
        if mode in ('302html', '302truncated'):
            marker = (intent, mode)
            if marker not in self.seen_redirects:
                self.seen_redirects.add(marker)
                self._json(201, {'id': reply_id})
                return
            target = '/done-html' if mode == '302html' else '/truncated'
            self.send_response(302)
            self.send_header('Location', target)
            self.end_headers()
        elif mode.startswith('302'):
            self.send_response(302)
            self.send_header('Location', '/done?id=%s' % record_id if 'bad' not in parsed.query else '/failed')
            self.end_headers()
        elif mode == '399':
            self._json(399, {'error': 'not terminal'})
        elif mode == '503':
            self._json(503, {'error': 'unavailable'})
        elif mode in ('204', '205', '205json'):
            self.send_response(204 if mode == '204' else 205)
            if mode != '205':
                self.send_header('Content-Type', 'application/json')
            self.end_headers()
        elif mode == 'wrongid':
            self._json(201, {'id': record_id + 1})
        elif mode == 'unsafe':
            self._json(201, b'{"id":9007199254740993}')
        else:
            self._json(201, {'id': reply_id})


HTML = '''<!doctype html><html><body>
<button id="send">Send</button><div id="status">Ready</div>
<button id="retry" style="display:none" onclick="send(true)">Retry</button>
<script>
let sent = false;
async function send(retry=false) {
  const params = new URLSearchParams(location.search);
  const key = retry && params.has('change') ? 'changed' : 'stable';
  const intent = params.has('control') ? 'control-' + crypto.randomUUID() : params.has('numeric') ? 1 : 'intent';
  const payload = {intent_key:intent, item:'widget', mode:params.get('mode') || ''};
  if (params.has('unsafeid')) payload.id = 9007199254740993;
  if (params.has('stringid')) payload.id = '9007199254740993';
  const raw = params.has('raw')
    ? '{"intent_key":"intent","item":"widget","id":9007199254740993}'
    : JSON.stringify(payload);
  try {
    const response = await fetch('/create' + location.search, {
      method: 'POST',
      headers: {'content-type': params.has('formjson') ? 'application/x-www-form-urlencoded' : 'application/json', 'Idempotency-Key':key},
      body: raw,
    });
    if (response.status === 204 || response.status === 205) {
      document.getElementById('status').textContent = 'Saved';
      document.getElementById('retry').style.display = 'none';
      return;
    }
    if (params.has('html')) {
      const text = await response.text();
      if (!response.ok || !text.includes('Order confirmed')) throw new Error('not complete');
      document.getElementById('status').textContent = 'Saved';
      document.getElementById('retry').style.display = 'none';
      return;
    }
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
        cls.temp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.temp.name) / 'orders.db'
        Handler.db = str(cls.db)
        Handler.seen_reset = set()
        Handler.seen_redirects = set()
        with sqlite3.connect(cls.db) as con:
            con.execute('CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, intent_key TEXT UNIQUE, item TEXT)')
            con.execute('CREATE TABLE IF NOT EXISTS orders_uuid (id TEXT PRIMARY KEY, intent_key TEXT UNIQUE, item TEXT)')
            con.execute('CREATE TABLE IF NOT EXISTS orders_zero (id TEXT PRIMARY KEY, intent_key TEXT UNIQUE, item TEXT)')
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
             unsafe_state=False, control_state=None, control_unsafe=False, control_string=False,
             raw=False, change=False,
             uuid=False, zero=False, after_retry_goto=False, after_retry_post=False,
             formjson=False, html=False):
        query = []
        if mode:
            query.append('mode=' + mode)
        if unsafe_state:
            query.append('unsafe=1')
        if raw:
            query.append('raw=1')
        if formjson:
            query.append('formjson=1')
        if html:
            query.append('html=1')
        if change:
            query.append('change=1')
        if uuid:
            query.append('uuid=1')
        if zero:
            query.append('zero=1')
        state_url = '/state?unsafe=1' if unsafe_state else '/state?uuid=1' if uuid else '/state?zero=1' if zero else '/state'
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
            ] + ([{'action': 'goto', 'path': '/'}, {'action': 'wait_for_timeout', 'ms': 100}] if after_retry_goto else [])
              + ([{'action': 'goto', 'path': '/'}, {'action': 'click', 'selector': '#send'}, {'action': 'wait_for_text', 'text': 'Saved'}] if after_retry_post else []),
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
            control_path = '/?control=1'
            if control_unsafe:
                control_path += '&unsafeid=1'
            elif control_string:
                control_path += '&stringid=1'
            case['control'] = {
                'journey': [
                    {'action': 'goto', 'path': control_path},
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

    def test_uuid_string_identity_passes(self):
        result = self.run_case(uuid=True)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['recovery']['response_identity']['response_id'],
                         '123e4567-e89b-12d3-a456-426614174000')

    def test_zero_padded_string_identity_does_not_coerce(self):
        result = self.run_case(zero=True)
        self.assertFalse(result['ok'], result)
        self.assertFalse(result['recovery']['response_identity']['ok'], result)

    def test_inherited_request_field_mapping_is_rejected(self):
        case = self.case()
        case['state']['request_fields'] = {'constructor': 'constructor'}
        path = Path(self.temp.name) / 'inherited.json'
        path.write_text(json.dumps(case))
        result = core.run_contract(path)
        self.assertFalse(result['ok'], result)

    def test_inherited_request_field_mapping_is_rejected_with_sqlite_state(self):
        case = self.case()
        case['state'] = {
            'mode': 'sqlite', 'db': str(self.db), 'table': 'orders',
            'where': "item='widget'", 'expect': 1,
            'record_fields': {'item': 'widget'},
            'request_fields': {'constructor': 'constructor'},
        }
        path = Path(self.temp.name) / 'inherited-sqlite.json'
        path.write_text(json.dumps(case))
        result = core.run_contract(path)
        self.assertFalse(result['ok'], result)

    def test_inherited_reconciliation_initial_field_is_rejected(self):
        case = self.reconciliation_case()
        case['recovery']['initial_field'] = 'constructor'
        path = Path(self.temp.name) / 'reconcile-initial-constructor.json'
        path.write_text(json.dumps(case))
        result = core.run_contract(path)
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['recovery']['confirmations'], 0)

    def test_unrelated_navigation_cannot_rescue_retry(self):
        for mode, failed in (('503', 0), ('reset', 1)):
            with self.subTest(mode=mode):
                result = self.run_case(mode=mode, after_retry_goto=True)
                self.assertFalse(result['ok'], result)
                self.assertFalse(result['recovery']['ok'], result)
                if failed:
                    self.assertEqual(result['recovery']['matching_retries'], 0)
                    self.assertEqual(result['recovery']['failed_recovery_transports'], failed)

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

    def test_control_request_unsafe_number_invalidates_measurement(self):
        # Primary request and retry are safe and pass their paired reply/record
        # identity; only the two control writes carry an unsafe numeric value.
        # Count/content control assertions must not turn that into PASS.
        result = self.run_case(control_state='/state', control_unsafe=True)
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['status'], 'INVALID')
        self.assertFalse(result['scenario']['valid'], result)
        self.assertIn('unsupported numeric data', result['scenario']['invalid_reason'])
        control = result['control']
        self.assertTrue(control['ok'], 'the control count/content assertions still pass')
        self.assertTrue(control['invalid_measurement'], control)
        unsafe_writes = [event for event in result['network']
                         if event['phase'] == 'control' and event['kind'] == 'write'
                         and event.get('body_error')]
        self.assertEqual(len(unsafe_writes), 2, unsafe_writes)
        self.assertIn('unsafe integer', unsafe_writes[0]['body_error'])
        self.assertFalse(core._completed_assertion(result, passed=False), result)

    def test_control_safe_and_string_identifiers_still_pass(self):
        for kwargs, label in (({}, 'safe numeric control ids'),
                              ({'control_string': True}, 'string control ids')):
            with self.subTest(label):
                result = self.run_case(control_state='/state', **kwargs)
                self.assertTrue(result['ok'], result)
                self.assertEqual(result['status'], 'PASS')
                self.assertTrue(result['scenario']['valid'], result)
                control = result['control']
                self.assertFalse(control['invalid_measurement'], control)
                self.assertTrue(control['ok'], control)

    def test_unsafe_request_body_is_rejected(self):
        result = self.run_case(raw=True)
        self.assertFalse(result['ok'], result)
        self.assertIn('unsafe integer', result['recovery']['reason'])
        write = next(event for event in result['network'] if event['kind'] == 'write')
        self.assertIsNone(write['body'], 'an unsafe JSON body must not fall back to form parsing')
        self.assertIn('unsafe integer', write['body_error'])
        self.assertFalse(core._completed_assertion(result, passed=False), result)

    def test_unsafe_json_body_does_not_fall_back_to_form_parsing(self):
        result = self.run_case(raw=True, formjson=True)
        self.assertFalse(result['ok'], result)
        write = next(event for event in result['network'] if event['kind'] == 'write')
        self.assertIsNone(write['body'], 'form-looking content type must not rescue unsafe JSON')
        self.assertIn('unsafe integer', write['body_error'])

    def reconciliation_case(self, response_id_path='order.id', record_id_field='id'):
        return {
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
                         'query_parameter': 'intent_key', 'response_id_path': response_id_path,
                         'record_id_field': record_id_field, 'success_statuses': [200]},
        }

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

    def test_numeric_reconciliation_intent_survives_query_encoding(self):
        case = self.reconciliation_case()
        case['journey'][0]['path'] = '/?numeric=1'
        case['journey'][3]['path'] = '/receipt?intent_key=1'
        path = Path(self.temp.name) / 'reconcile-numeric.json'
        path.write_text(json.dumps(case))
        result = core.run_contract(path)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['recovery']['confirmations'], 1)

    def reconciliation_secondary_case(self, query=''):
        case = self.reconciliation_case()
        case['journey'] = [
            {'action': 'goto', 'path': '/'},
            {'action': 'click', 'selector': '#send'},
            {'action': 'wait_for_fault'},
            {'action': 'goto', 'path': '/' + ('?' + query if query else '')},
            {'action': 'click', 'selector': '#send'},
            {'action': 'wait_for_text', 'text': 'Saved'},
            {'action': 'goto', 'path': '/receipt?intent_key=intent'},
            {'action': 'wait_for_timeout', 'ms': 300},
        ]
        # A second safe write is a legitimate same-intent operation; keep the
        # normal two-record control in scope while testing the primary writes.
        case['control'] = self.case(control_state='/state')['control']
        return case

    def test_reconciliation_secondary_unsafe_write_invalidates_measurement(self):
        case = self.reconciliation_secondary_case('unsafeid=1')
        path = Path(self.temp.name) / 'reconcile-secondary-unsafe.json'
        path.write_text(json.dumps(case))
        result = core.run_contract(path)
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['status'], 'INVALID')
        self.assertFalse(result['scenario']['valid'], result)
        self.assertTrue(result['recovery']['invalid_measurement'], result)
        self.assertIn('unsafe integer', result['recovery']['reason'])
        self.assertFalse(core._completed_assertion(result, passed=False), result)

    def test_reconciliation_secondary_supported_writes_still_pass(self):
        for query, label in (('', 'safe numeric secondary id'),
                             ('stringid=1', 'string secondary id')):
            with self.subTest(label):
                case = self.reconciliation_secondary_case(query)
                path = Path(self.temp.name) / ('reconcile-secondary-' + (query or 'safe') + '.json')
                path.write_text(json.dumps(case))
                result = core.run_contract(path)
                self.assertTrue(result['ok'], result)
                self.assertEqual(result['status'], 'PASS')
                self.assertTrue(result['scenario']['valid'], result)
                self.assertFalse(result['recovery']['invalid_measurement'], result)
                self.assertEqual(result['recovery']['confirmations'], 1)

    def test_204_recovery_with_json_content_type_passes(self):
        result = self.run_case(mode='204', statuses=(204,), response_id=False)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['recovery']['terminal_response']['status'], 204)
        self.assertIsNone(result['recovery']['terminal_response']['json'])
        self.assertEqual(result['recovery']['terminal_response']['body_sha256'],
                         'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')

    def test_complete_html_terminal_response_passes(self):
        result = self.run_case(mode='302html', statuses=(200,), response_id=False,
                               html=True, control_state='/state')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['status'], 'PASS')
        terminal = result['recovery']['terminal_response']
        self.assertEqual(terminal['status'], 200)
        self.assertTrue(terminal['body_complete'], terminal)
        self.assertIsNone(terminal['body_error'])

    def test_truncated_html_terminal_response_is_rejected(self):
        result = self.run_case(mode='302truncated', statuses=(200,), response_id=False,
                               html=True, control_state='/state')
        self.assertFalse(result['ok'], result)
        self.assertFalse(result['recovery']['ok'], result)
        self.assertFalse(result['recovery']['terminal_transport_complete'], result)
        terminal = result['recovery']['terminal_response']
        self.assertEqual(terminal['status'], 200)
        self.assertFalse(terminal['body_complete'], terminal)
        self.assertTrue(terminal['body_error'], terminal)
        self.assertIn('transport did not complete', result['recovery']['reason'])
        self.assertFalse(core._completed_assertion(result, passed=True), result)

    def test_empty_205_body_passes_without_parsing(self):
        for mode in ('205', '205json'):
            with self.subTest(mode=mode):
                result = self.run_case(mode=mode, statuses=(205,), response_id=False)
                self.assertTrue(result['ok'], result)
                terminal = result['recovery']['terminal_response']
                self.assertEqual(terminal['status'], 205)
                self.assertIsNone(terminal['json'])
                self.assertIsNone(terminal['json_error'])
                self.assertEqual(terminal['body_sha256'],
                                 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')

    def test_absent_reply_id_on_empty_205_still_fails(self):
        result = self.run_case(mode='205json', statuses=(205,), response_id=True)
        self.assertFalse(result['ok'], result)
        self.assertFalse(result['recovery']['response_identity']['ok'], result)
        self.assertIsNone(result['recovery']['terminal_response']['json'])

    def test_unrelated_post_cannot_rescue_retry(self):
        for mode in ('503', 'reset'):
            with self.subTest(mode=mode):
                result = self.run_case(mode=mode, after_retry_post=True)
                self.assertFalse(result['ok'], result)
                self.assertFalse(result['recovery']['ok'], result)

    def test_reconciliation_inherited_identity_fields_are_rejected(self):
        for field in ('constructor', '__proto__'):
            with self.subTest(field=field):
                case = self.reconciliation_case(field, field)
                path = Path(self.temp.name) / f'reconcile-{field}.json'
                path.write_text(json.dumps(case))
                result = core.run_contract(path)
                self.assertFalse(result['ok'], result)
                self.assertEqual(result['recovery']['confirmations'], 0, result)

    def test_retry_inherited_identity_fields_are_rejected(self):
        for field in ('constructor', '__proto__'):
            with self.subTest(field=field):
                case = self.case()
                case['recovery']['response_id_path'] = field
                case['recovery']['record_id_field'] = field
                path = Path(self.temp.name) / f'retry-{field}.json'
                path.write_text(json.dumps(case))
                result = core.run_contract(path)
                self.assertFalse(result['ok'], result)
                self.assertFalse(result['recovery']['response_identity']['ok'], result)

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

    def test_overflowing_exponent_is_not_accepted_as_infinity(self):
        script = (
            "const {parseJson}=require('./crashlab_runner.js');"
            "try { parseJson('{\"id\":1e400}','probe'); console.log('ACCEPTED'); }"
            "catch (e) { console.log(e.message); }"
        )
        result = subprocess.run(['node', '-e', script], cwd=Path(__file__).parent,
                                capture_output=True, text=True, check=True)
        self.assertIn('non-finite number 1e400', result.stdout)
        self.assertNotIn('ACCEPTED', result.stdout)

    def test_selected_identity_headers_are_configurable(self):
        value = self.case()
        value['recovery']['identity_headers'] = ['Idempotency-Key', 'X-Request-Id']
        core.validate_contract(value)


if __name__ == '__main__':
    unittest.main()
