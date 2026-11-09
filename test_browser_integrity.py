"""Real-browser acceptance tests. Set CRASHLAB_BROWSER_TESTS=1 to run."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import crashlab_core as core

@unittest.skipUnless(os.environ.get('CRASHLAB_BROWSER_TESTS') == '1', 'browser acceptance opt-in')
class BrowserIntegrity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.case = json.loads((core.ROOT / 'cases/checkout-local-fixed.json').read_text())
        self.source = (core.ROOT / 'server.py').read_text()

    def run_case(self, source=None, case=None):
        case = copy.deepcopy(case or self.case)
        if source is not None:
            app = self.root / 'app.py'
            app.write_text(source)
            case['app']['start']['command'][1] = str(app)
        path = self.root / 'case.json'
        path.write_text(json.dumps(case))
        return core.run_contract(path)

    def test_delivered_cases_and_controls(self):
        for name, count, ok in [('checkout-local', 2, False), ('checkout-local-fixed', 1, True)]:
            result = core.run_contract(core.ROOT / 'cases' / (name + '.json'))
            self.assertEqual(result.get('observed'), count, result)
            self.assertIs(result['ok'], ok)
            self.assertTrue(result['control']['ok'])


    def test_retry_cannot_be_replaced_with_local_success_text(self):
        case=copy.deepcopy(self.case)
        case['app']['start']['env']['BUGGY']='1'
        source=self.source.replace("document.getElementById('retry').onclick = order;", "document.getElementById('retry').onclick = () => { status.textContent = 'Order confirmed'; };")
        result=self.run_case(source,case)
        self.assertEqual(result.get('observed'),1,result)
        self.assertTrue(result['control']['ok'],result)
        self.assertFalse(result['ok'],result)
        self.assertFalse(result['recovery']['ok'],result)

    def test_retry_identity_must_match_even_when_db_mapping_omits_intent_key(self):
        case=copy.deepcopy(self.case)
        case['state']['request_fields']={'item':'item'}
        source=self.source.replace("document.getElementById('retry').onclick = order;", "document.getElementById('retry').onclick = () => { key=crypto.randomUUID(); order(); };")
        result=self.run_case(source,case)
        self.assertFalse(result['recovery']['ok'],result)
        self.assertFalse(result['ok'],result)

    def test_count_wait_default_and_upper_only_bounds(self):
        for step in [
            {'action':'wait_for_count','selector':'#status'},
            {'action':'wait_for_count','selector':'#status','max':1},
            {'action':'wait_for_count','selector':'#status','min':1},
            {'action':'wait_for_count','selector':'#absent','max':0},
        ]:
            with self.subTest(step=step):
                case=copy.deepcopy(self.case)
                case['journey'].insert(1,{**step,'timeout':800})
                result=self.run_case(case=case)
                self.assertTrue(result['ok'],result)

    def test_declared_reconciliation_requires_original_intent_and_observed_record_id(self):
        case=copy.deepcopy(self.case)
        case['recovery']={'mode':'reconcile','route':'**/receipt?*','initial_field':'intent_key',
                          'query_parameter':'intent_key','response_id_path':'order.id','record_id_field':'id',
                          'success_statuses':[200]}
        case['app']['start']['env']['BUGGY']='1'
        source=self.source.replace('import sqlite3', 'import sqlite3\nfrom urllib.parse import urlparse, parse_qs')
        source=source.replace('elif self.path == "/state":', '''elif self.path.startswith('/receipt?'):
            key=parse_qs(urlparse(self.path).query).get('intent_key',[''])[0]
            with connect() as con:
                row=con.execute('SELECT id FROM orders WHERE intent_key=?',(key,)).fetchone()
            self._send(200, {'order': {'id': row[0] if row else None}})
        elif self.path == "/state":''')
        source=source.replace("document.getElementById('retry').onclick = order;", "document.getElementById('retry').onclick = async () => { await (await fetch('/receipt?intent_key='+encodeURIComponent(key))).json(); status.textContent = 'Order confirmed'; };")
        for variant,ok in [(source,True),(source.replace("'id': row[0] if row else None", "'id': -1"),False),
                           (source.replace('encodeURIComponent(key)','encodeURIComponent(\"unrelated\")'),False)]:
            with self.subTest(ok=ok):
                result=self.run_case(variant,case)
                self.assertIs(result['ok'],ok,result)
                self.assertIs(result['recovery']['ok'],ok,result)
                self.assertTrue(result['control']['ok'],result)

        # A broad write-route glob must not bypass the earlier GET readback handler.
        case['fault']['route']='**/*'
        result=self.run_case(source,case)
        self.assertTrue(result['ok'],result)
        self.assertTrue(result['recovery']['ok'],result)

    def test_browser_launch_error_is_actionable(self):
        with patch.dict(os.environ,CHROME_PATH=str(self.root/'missing-chrome')):
            result=self.run_case()
        self.assertFalse(result['ok'],result)
        self.assertEqual(result['status'],'ERROR',result)
        self.assertIn('CHROME_PATH',result['error'])
        self.assertIn('missing-chrome',result['error'])

    def test_retry_transport_failure_is_measured_failure_not_error(self):
        # The retry reaches the server, commits no response, and the browser
        # sees a reset. The original commit is observed; the failed retry must
        # remain a measured recovery failure, not a runner exception.
        case = copy.deepcopy(self.case)
        case['id'] = 'retry-transport-failure'
        case['journey'] = [
            {'action': 'goto', 'path': '/'},
            {'action': 'click', 'selector': '#buy'},
            {'action': 'wait_for_selector', 'selector': '#retry'},
            {'action': 'click', 'selector': '#retry'},
            {'action': 'wait_for_timeout', 'ms': 1200},
        ]
        source = self.source.replace(
            'if row:\n                    con.commit()\n                    self._send(200, {"id": row[0], "deduplicated": True})\n                    return',
            'if row:\n                    con.commit()\n                    self.connection.close()\n                    return',
        )
        result = self.run_case(source, case)
        self.assertEqual(result['status'], 'FAIL', result)
        self.assertFalse(result['ok'], result)
        self.assertTrue(result['scenario']['valid'], result)
        self.assertEqual((result['observed'], result['expected']), (1, 1), result)
        self.assertFalse(result['recovery']['ok'], result)
        self.assertEqual(result['recovery']['failed_recovery_transports'], 1, result)
        self.assertTrue(result['control']['ok'], result)
        self.assertTrue(core._completed_assertion(result, passed=False), result)

    def test_retry_timeout_with_transport_keyword_in_url_is_error(self):
        # A timeout whose request URL contains a transport keyword is still a
        # timeout. Only the leading cause line/structured code may classify a
        # recovery transport failure.
        case = copy.deepcopy(self.case)
        case['id'] = 'retry-timeout-keyword-url'
        case['journey'] = [
            {'action': 'goto', 'path': '/'},
            {'action': 'click', 'selector': '#buy'},
            {'action': 'wait_for_selector', 'selector': '#retry'},
            {'action': 'click', 'selector': '#retry'},
            {'action': 'wait_for_timeout', 'ms': 1200},
        ]
        source = self.source.replace(
            "fetch('/order', {method:'POST'",
            "fetch('/ECONNRESET/order', {method:'POST'",
        )
        source = source.replace('if self.path != "/order":', 'if not self.path.endswith("/order"):')
        source = source.replace('import sqlite3', 'import sqlite3\nimport time')
        source = source.replace(
            'if row:\n                    con.commit()\n                    self._send(200, {"id": row[0], "deduplicated": True})\n                    return',
            'if row:\n                    con.commit()\n                    time.sleep(2)\n                    self.connection.close()\n                    return',
        )
        with patch.dict(os.environ, {'CRASHLAB_RETRY_FETCH_TIMEOUT_MS': '300'}):
            result = self.run_case(source, case)
        self.assertEqual(result['status'], 'ERROR', result)
        self.assertFalse(result['ok'], result)
        retry_events = [e for e in result.get('network', []) if e.get('after_interruption')]
        self.assertEqual(len(retry_events), 1, result)
        event = retry_events[0]
        self.assertTrue(event.get('unclassified_recovery_transport'), result)
        self.assertNotIn('failed_recovery_transport', event)
        self.assertTrue(event.get('transport_cause', '').startswith('route.fetch: Timeout'), event)
        self.assertIn('/ECONNRESET/order', event.get('transport_error', ''), event)

    def test_initial_transport_failure_is_not_reproduced(self):
        case = copy.deepcopy(self.case)
        case['id'] = 'initial-transport-failure'
        case['journey'] = [
            {'action': 'goto', 'path': '/'},
            {'action': 'click', 'selector': '#buy'},
            {'action': 'wait_for_timeout', 'ms': 1000},
        ]
        source = self.source.replace(
            'if self.path != "/order":',
            'if self.path == "/order":\n            self.connection.close()\n            return\n        if self.path != "/order":',
        )
        result = self.run_case(source, case)
        self.assertFalse(result['ok'], result)
        self.assertNotEqual(result['status'], 'FAIL', result)
        self.assertFalse(result.get('scenario', {}).get('valid', False), result)

    def test_unrelated_post_retry_selector_is_error(self):
        case = copy.deepcopy(self.case)
        case['id'] = 'wrong-post-retry-selector'
        case['journey'] = [
            {'action': 'goto', 'path': '/'},
            {'action': 'click', 'selector': '#buy'},
            {'action': 'wait_for_selector', 'selector': '#retry'},
            {'action': 'click', 'selector': '#retry'},
            {'action': 'wait_for_selector', 'selector': '#not-present', 'timeout': 300},
        ]
        source = self.source.replace(
            'if row:\n                    con.commit()\n                    self._send(200, {"id": row[0], "deduplicated": True})\n                    return',
            'if row:\n                    con.commit()\n                    self.connection.close()\n                    return',
        )
        result = self.run_case(source, case)
        self.assertEqual(result['status'], 'ERROR', result)
        self.assertFalse(result['ok'], result)

    def test_wrong_payload_rejected(self):
        result = self.run_case(self.source.replace('item = str(body.get("item") or "Unknown item")', 'item = "Wrong item"'))
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['observed'], 1)

    def test_wrong_intent_rejected(self):
        result = self.run_case(self.source.replace('intent = str(body.get("intent_key") or "")', 'intent = "unrelated-intent"'))
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['observed'], 1)

    def test_dirty_baseline_is_invalid(self):
        source = self.source.replace('con.execute("DELETE FROM orders")', 'con.execute("DELETE FROM orders")\n                con.execute("INSERT INTO orders(intent_key,item) VALUES (?,?)", ("old", "old item"))')
        result = self.run_case(source)
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'INVALID', result)
        self.assertIn('baseline', result['error'])

    def test_duplicate_first_commit_is_valid_failure(self):
        case = copy.deepcopy(self.case)
        case['app']['start']['env']['BUGGY'] = '1'
        case.pop('control')
        # Duplicate each insert, including the first intercepted request.
        source = self.source
        source = source.replace('cur = con.execute("INSERT INTO orders(intent_key,item) VALUES(?,?)", (intent, item))', 'cur = con.execute("INSERT INTO orders(intent_key,item) VALUES(?,?)", (intent, item))\n                con.execute("INSERT INTO orders(intent_key,item) VALUES(?,?)", (intent, item))')
        result = self.run_case(source, case)
        self.assertFalse(result['ok'])
        self.assertTrue(result['scenario']['valid'], result)
        self.assertEqual(result['scenario']['committed_after_fault'], 2)
        self.assertEqual(result['status'], 'FAIL')

    def test_retry_content_corruption_is_valid_primary_failure(self):
        source = self.source.replace('if row:\n                    con.commit()', 'if row:\n                    con.execute("UPDATE orders SET item=? WHERE id=?", ("Corrupted", row[0]))\n                    con.commit()')
        result = self.run_case(source)
        self.assertEqual(result["observed"], result["expected"], result)
        self.assertTrue(result["scenario"]["valid"], result)
        self.assertTrue(result["control"]["ok"], result)
        self.assertFalse(result["primary_ok"], result)
        self.assertTrue(core._completed_assertion(result, passed=False), result)

    def test_control_cannot_supply_missing_primary_fault(self):
        case = copy.deepcopy(self.case)
        case.pop('control')
        case['fault']['route'] = '**/not-used'
        case['state'].pop('request_fields')
        case['journey'] = [
            {'action': 'goto', 'path': '/'},
            {'action': 'click', 'selector': '#buy'},
            {'action': 'wait_for_text', 'text': 'Order confirmed'},
        ]
        # Control uses matching endpoint; it must not trigger the primary fault.
        case['control'] = {'journey': copy.deepcopy(case['journey']), 'state': copy.deepcopy(case['state'])}
        case['control']['journey'][0]['path'] = '/control'
        source = self.source.replace('elif self.path == "/state":', 'elif self.path == "/control":\n            self._send(200, HTML.replace("/order", "/not-used"), "text/html")\n        elif self.path == "/state":').replace('if self.path != "/order":', 'if self.path not in ("/order", "/not-used"):')
        result = self.run_case(source, case)
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'INVALID', result)
        self.assertFalse(result['scenario']['matched'])
        self.assertTrue(result['control']['ok'])

    def test_frozen_contract_survives_disk_change(self):
        path = self.root / 'case.json'
        path.write_text(json.dumps(self.case))
        frozen = core.load_contract(path)
        changed = copy.deepcopy(self.case)
        changed['state']['expect'] = 42
        path.write_text(json.dumps(changed))
        result = core.run_contract(path, frozen_contract=frozen)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['expected'], 1)


    def test_native_post_reload_waits_for_failed_navigation(self):
        for buggy, observed, ok in [('1',2,False),('0',1,True)]:
            with self.subTest(buggy=buggy):
                case = copy.deepcopy(self.case)
                case['id']='native-form-retry'
                case['app'].pop('reset_url',None)
                case['app']['start']['command']=['{PYTHON}',str(core.ROOT/'native_form_fixture.py')]
                case['app']['start']['env']['BUGGY']=buggy
                case['fault']['route']='**/create'
                case['recovery'].pop('response_id_path', None)
                case['recovery'].pop('record_id_field', None)
                case['journey']=[{'action':'goto','path':'/'},{'action':'fill','selector':'#item','value':'primary'},{'action':'click','selector':'#submit'},{'action':'wait_for_fault'},{'action':'reload'},{'action':'wait_for_url','pattern':'**/done'}]
                case['state'].update(where="item='primary'",record_fields={'item':'primary'})
                create=[{'action':'goto','path':'/'},{'action':'fill','selector':'#item','value':'control'},{'action':'click','selector':'#submit'},{'action':'wait_for_url','pattern':'**/done'}]
                case['control']['journey']=create+copy.deepcopy(create)
                case['control']['state'].update(where="item='control'",record_fields={'item':'control'})
                result=self.run_case(case=case)
                self.assertEqual(result.get('observed'),observed,result)
                self.assertIs(result['ok'],ok,result)
                self.assertTrue(result['scenario']['valid'],result)
                self.assertTrue(result['control']['ok'],result)

if __name__ == '__main__':
    unittest.main()
