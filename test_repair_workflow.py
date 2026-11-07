import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import repair_workflow as workflow

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.project = self.root / 'app'; self.project.mkdir()
        subprocess.run(['git','init','-q',str(self.project)],check=True)
        (self.project/'app.py').write_text('broken')
        self.env=patch.dict(os.environ,CRASHLAB_DATA_DIR=str(self.root/'evidence'));self.env.start();self.addCleanup(self.env.stop)
        self.case=json.loads((workflow.core.ROOT/'cases/checkout-local.json').read_text())
        self.case['app']['start']['cwd']=str(self.project)
    def prepare(self):
        return workflow.prepare(str(self.project),'Retry saves one order',self.case)['experiment_id']
    def result(self, passed):
        return {'ok':passed,'primary_ok':passed,'returncode':0 if passed else 1,'scenario':{'valid':True},'observed':1 if passed else 2,'expected':1,'status':'PASS' if passed else 'FAIL','control':{'ok':True,'observed':2,'expected':2}}
    def test_repair_then_edit_makes_evidence_stale(self):
        eid=self.prepare()
        with patch.object(workflow.core,'run_and_persist',return_value=self.result(False)):
            self.assertEqual(workflow.run(eid)['status'],'REPRODUCED')
        (self.project/'app.py').write_text('repaired')
        self.assertEqual(workflow.status(eid)['status'],'STALE')
        with patch.object(workflow.core,'run_and_persist',return_value=self.result(True)):
            self.assertEqual(workflow.run(eid)['status'],'VERIFIED')
        (self.project/'app.py').unlink()
        self.assertEqual(workflow.status(eid)['status'],'STALE')
    def test_initial_pass_is_not_repair(self):
        eid=self.prepare()
        with patch.object(workflow.core,'run_and_persist',return_value=self.result(True)):
            self.assertEqual(workflow.run(eid)['status'],'PASS_WITHOUT_REPAIR_PROOF')
    def test_case_drift_is_rejected(self):
        eid=self.prepare(); path=workflow.directory(eid)/'case.json'; c=json.loads(path.read_text());c['state']['expected_count']=123;path.write_text(json.dumps(c))
        with self.assertRaisesRegex(ValueError,'changed'):workflow.status(eid)
    def test_edit_during_run_cannot_verify(self):
        eid=self.prepare()
        def execute(*args):
            (self.project/'app.py').write_text('changed during test');return self.result(False)
        with patch.object(workflow.core,'run_and_persist',side_effect=execute):
            self.assertEqual(workflow.run(eid)['status'],'STALE')
    def test_control_failure_does_not_establish_baseline(self):
        eid=self.prepare();r=self.result(False);r['control']['ok']=False
        with patch.object(workflow.core,'run_and_persist',return_value=r):
            self.assertNotEqual(workflow.run(eid)['status'],'REPRODUCED')
    def test_evidence_inside_app_is_rejected(self):
        with patch.dict(os.environ,CRASHLAB_DATA_DIR=str(self.project/'evidence')):
            with self.assertRaisesRegex(ValueError,'outside'):self.prepare()
