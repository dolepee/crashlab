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

if __name__ == '__main__':
    unittest.main()
