"""Real recovery boundaries, including loss of process memory with durable DB retained."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
import crashlab_core as core

@unittest.skipUnless(os.environ.get('CRASHLAB_BROWSER_TESTS')=='1','browser acceptance opt-in')
class RecoveryCampaignTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.case=json.loads((core.ROOT/'cases/checkout-local-fixed.json').read_text())
        self.case['recovery_campaign']=['before_delivery','after_commit','restart_after_commit']
        self.case['journey'].insert(2,{'action':'wait_for_fault'})
        self.source=(core.ROOT/'server.py').read_text()
    def run_case(self,source=None,buggy='0'):
        case=copy.deepcopy(self.case);case['app']['start']['env']['BUGGY']=buggy
        if source is not None:
            app=self.root/'app.py';app.write_text(source)
            case['app']['start']['command'][1]=str(app)
        path=self.root/'case.json';path.write_text(json.dumps(case))
        return core.run_and_persist(path)
    def test_durable_repair_passes_all_boundaries(self):
        result=self.run_case();self.assertTrue(result['ok'],result)
        cases={c['name']:c['result'] for c in result['cases']}
        self.assertEqual(cases['before_delivery']['scenario']['committed_after_fault'],0)
        self.assertTrue(cases['before_delivery']['scenario']['valid'])
        self.assertEqual(cases['after_commit']['scenario']['committed_after_fault'],1)
        restarted=cases['restart_after_commit']
        self.assertTrue(restarted['app_lifecycle'][0]['same_data_directory'])
        self.assertNotEqual(restarted['app_lifecycle'][0]['old_pid'],restarted['app_lifecycle'][0]['new_pid'])
        self.assertFalse(restarted['app_lifecycle'][0]['setup_replayed'])
        self.assertTrue(next(e for e in restarted['events'] if e['kind']=='restart')['scoped_records_preserved'])
        self.assertEqual(result['control']['observed'],6)
        self.assertTrue(all(c['control']['ok'] and c['control']['observed']==2 for c in cases.values()))
    def test_memory_only_dedup_passes_ack_loss_but_fails_restart(self):
        source=self.source.replace('FAILED_FIRST = False','FAILED_FIRST = False\nSEEN = {}')
        source=source.replace('global FAILED_FIRST','global FAILED_FIRST\n        if intent in SEEN:\n            self._send(200, {"id": SEEN[intent]})\n            return')
        source=source.replace('self._send(200, {"id": cur.lastrowid, "deduplicated": False})','SEEN[intent] = cur.lastrowid\n            self._send(200, {"id": cur.lastrowid, "deduplicated": False})')
        result=self.run_case(source,buggy='1');self.assertFalse(result['ok'],result)
        cases={c['name']:c['result'] for c in result['cases']}
        self.assertTrue(cases['before_delivery']['ok'],result)
        self.assertTrue(cases['after_commit']['ok'],result)
        self.assertEqual(cases['restart_after_commit']['observed'],2,result)
        self.assertTrue(cases['restart_after_commit']['control']['ok'])
        self.assertEqual(result['status'],'FAIL',result)
    def test_local_success_only_fails_before_delivery_too(self):
        source=self.source.replace("document.getElementById('retry').onclick = order;", "document.getElementById('retry').onclick = () => { status.textContent = 'Order confirmed'; };")
        result=self.run_case(source,buggy='1');self.assertFalse(result['ok'],result)
        self.assertEqual(result['cases'][0]['result']['observed'],0,result)
        self.assertTrue(result['cases'][0]['result']['scenario']['valid'])
        self.assertTrue(all(not c['result']['recovery']['ok'] for c in result['cases']))
    def test_restart_must_preserve_the_observed_records(self):
        source=self.source.replace('if __name__ == "__main__":','if __name__ == "__main__":\n    with connect() as con: con.execute("DELETE FROM orders")')
        result=self.run_case(source);self.assertFalse(result['ok'],result)
        self.assertEqual(result['status'],'ERROR',result)
        self.assertIn('did not preserve',result['cases'][-1]['result']['error'])
        self.assertTrue(result['cases'][-1]['result']['events'])
    def test_numeric_only_http_count_cannot_hide_restart_data_loss(self):
        self.case['state']={'mode':'http','url':'/state','path':'orders','expect':1}
        self.case['control']['state']={**self.case['state'],'expect':2}
        source=self.source.replace('[dict(zip(("id", "intent_key", "item", "created_at"), r)) for r in rows]', 'len(rows)')
        source=source.replace('if __name__ == "__main__":','if __name__ == "__main__":\n    with connect() as con: con.execute("DELETE FROM orders")')
        result=self.run_case(source)
        self.assertFalse(result['ok'],result)
        self.assertEqual(result['status'],'ERROR',result)
        self.assertIn('record-level',result['cases'][-1]['result']['error'])
    def test_scalar_record_ids_must_remain_the_same_after_restart(self):
        self.case['state']={'mode':'http','url':'/state','path':'orders','expect':1}
        self.case['control']['state']={**self.case['state'],'expect':2}
        source=self.source.replace('[dict(zip(("id", "intent_key", "item", "created_at"), r)) for r in rows]', '[r[0] for r in rows]')
        source=source.replace('if __name__ == "__main__":','''if __name__ == "__main__":
    if DB.exists():
        with connect() as con:
            rows=con.execute("SELECT intent_key,item FROM orders").fetchall()
            con.execute("DELETE FROM orders")
            for row in rows: con.execute("INSERT INTO orders(intent_key,item) VALUES (?,?)",row)''')
        result=self.run_case(source)
        self.assertFalse(result['ok'],result)
        self.assertEqual(result['status'],'ERROR',result)
        self.assertIn('did not preserve',result['cases'][-1]['result']['error'])
    def test_native_form_campaign_retains_database_and_redirects(self):
        case=self.case
        case['app'].pop('reset_url',None)
        case['app']['start']['command']=['{PYTHON}',str(core.ROOT/'native_form_fixture.py')]
        case['fault']['route']='**/create'
        case['journey']=[{'action':'goto','path':'/'},{'action':'fill','selector':'#item','value':'primary'},{'action':'click','selector':'#submit'},{'action':'wait_for_fault'},{'action':'reload'},{'action':'wait_for_url','pattern':'**/done'}]
        case['recovery'].pop('response_id_path', None)
        case['recovery'].pop('record_id_field', None)
        case['state'].pop('request_fields', None)
        case['control']['state'].pop('request_fields', None)
        case['state'].update(where="item='primary'",record_fields={'item':'primary'})
        create=[{'action':'goto','path':'/'},{'action':'fill','selector':'#item','value':'control'},{'action':'click','selector':'#submit'},{'action':'wait_for_url','pattern':'**/done'}]
        case['control']['journey']=create+copy.deepcopy(create)
        case['control']['state'].update(where="item='control'",record_fields={'item':'control'})
        result=self.run_case()
        self.assertTrue(result['ok'],result)
        self.assertTrue(all(c['result']['recovery']['ok'] for c in result['cases']))
