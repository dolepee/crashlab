import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import repair_workflow as workflow
import approval

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.project = self.root / 'app'; self.project.mkdir()
        subprocess.run(['git','init','-q',str(self.project)],check=True)
        (self.project/'app.py').write_text('broken')
        self.env=patch.dict(os.environ,CRASHLAB_DATA_DIR=str(self.root/'evidence'));self.env.start();self.addCleanup(self.env.stop)
        self.case=json.loads((workflow.core.ROOT/'cases/checkout-local.json').read_text())
        self.case['app']['start']['cwd']=str(self.project)
    def run_case(self, eid):
        approval.grant_for_tests('crashlab_experiment_run', {'experiment_id': eid})
        return workflow.run(eid, approval_context={'session_id': 'test-session'})
    def prepare(self):
        return workflow.prepare(str(self.project),'Retry saves one order',self.case)['experiment_id']
    def result(self, passed):
        return {'ok':passed,'primary_ok':passed,'returncode':0 if passed else 1,'scenario':{'valid':True},'observed':1 if passed else 2,'expected':1,'status':'PASS' if passed else 'FAIL','control':{'ok':True,'observed':2,'expected':2}}
    def test_repair_then_edit_makes_evidence_stale(self):
        eid=self.prepare()
        with patch.object(workflow.core,'run_and_persist',return_value=self.result(False)):
            self.assertEqual(self.run_case(eid)['status'],'REPRODUCED')
        (self.project/'app.py').write_text('repaired')
        self.assertEqual(workflow.status(eid)['status'],'STALE')
        with patch.object(workflow.core,'run_and_persist',return_value=self.result(True)):
            self.assertEqual(self.run_case(eid)['status'],'VERIFIED')
        (self.project/'app.py').unlink()
        self.assertEqual(workflow.status(eid)['status'],'STALE')
    def test_initial_pass_is_not_repair(self):
        eid=self.prepare()
        with patch.object(workflow.core,'run_and_persist',return_value=self.result(True)):
            self.assertEqual(self.run_case(eid)['status'],'PASS_WITHOUT_REPAIR_PROOF')
    def test_case_drift_is_rejected(self):
        eid=self.prepare(); path=workflow.directory(eid)/'case.json'; c=json.loads(path.read_text());c['state']['expected_count']=123;path.write_text(json.dumps(c))
        with self.assertRaisesRegex(ValueError,'changed'):workflow.status(eid)
    def test_edit_during_run_cannot_verify(self):
        eid=self.prepare()
        def execute(*args, **kwargs):
            (self.project/'app.py').write_text('changed during test');return self.result(False)
        with patch.object(workflow.core,'run_and_persist',side_effect=execute):
            self.assertEqual(self.run_case(eid)['status'],'STALE')
    def test_control_failure_does_not_establish_baseline(self):
        eid=self.prepare();r=self.result(False);r['control']['ok']=False
        with patch.object(workflow.core,'run_and_persist',return_value=r):
            self.assertNotEqual(self.run_case(eid)['status'],'REPRODUCED')
    def test_evidence_inside_app_is_rejected(self):
        with patch.dict(os.environ,CRASHLAB_DATA_DIR=str(self.project/'evidence')):
            with self.assertRaisesRegex(ValueError,'outside'):self.prepare()

    def test_executable_permission_change_invalidates_evidence(self):
        script=self.project/'start.sh';script.write_text('#!/bin/sh\n');script.chmod(0o755)
        eid=self.prepare()
        with patch.object(workflow.core,'run_and_persist',return_value=self.result(False)):
            self.run_case(eid)
        (self.project/'app.py').write_text('fixed')
        with patch.object(workflow.core,'run_and_persist',return_value=self.result(True)):
            self.assertEqual(self.run_case(eid)['status'],'VERIFIED')
        script.chmod(0o644)
        self.assertEqual(workflow.status(eid)['status'],'STALE')

    def test_relative_launch_directory_is_rejected(self):
        self.case['app']['start']['cwd']='.'
        with self.assertRaisesRegex(ValueError,'absolute'):self.prepare()

    def test_status_keeps_verdict_visible_and_bounds_history(self):
        eid=self.prepare();folder=workflow.directory(eid)
        value=json.loads((folder/'experiment.json').read_text())
        value['runs']=[{'project_sha256':workflow.fingerprint(self.project), 'workflow_status':'REPRODUCED', 'result':{**self.result(False),'records':['x'*10000]}} for _ in range(30)]
        workflow.save(folder/'experiment.json',value)
        result=workflow.status(eid)
        self.assertEqual(result['status'],'REPRODUCED')
        self.assertEqual(result['run_count'],30)
        self.assertEqual(len(result['runs']),3)
        self.assertLess(len(json.dumps(result)),5000)

    def test_structurally_invalid_metadata_is_rejected(self):
        eid=self.prepare();path=workflow.directory(eid)/'experiment.json'
        original=json.loads(path.read_text())
        for value in [None, [], {**original,'runs':[{'project_sha256':'x','workflow_status':'VERIFIED','result':{'diagnosis':{'broken':'label'}}}]}, {**original,'runs':[{'project_sha256':'x','workflow_status':'VERIFIED','result':{'control':{'ok':True,'observed':[]}}}]}, {**original,'runs':None}, {**original,'runs':[None]}, {**original,'runs':[{'project_sha256':'x','workflow_status':'VERIFIED','result':{'control':[]}}]}]:
            with self.subTest(value=value):
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):workflow.status(eid)
