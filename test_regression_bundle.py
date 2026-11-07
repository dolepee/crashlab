import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import shlex
import tempfile
import unittest
from unittest.mock import patch
import repair_workflow as w

class RegressionBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.env=patch.dict(os.environ,CRASHLAB_DATA_DIR=str(self.root/'evidence'));self.env.start();self.addCleanup(self.env.stop)
        self.a=self.project('app-a');self.b=self.project('app-b')
        self.case=json.loads((w.core.ROOT/'cases/checkout-local-fixed.json').read_text())
        self.case['app']['start']['cwd']=str(self.a)
        self.case['app']['start']['python']=sys.executable
        self.case['state']['record_fields']['item']='literal {PROJECT} is business data'
        self.case['journey'].insert(0,{'action':'text','selector':'body','as':'literal {APP_PYTHON}'})
        self.e=w.prepare(str(self.a),'One correct recovery, two deliberate operations',self.case)
    def project(self,name):
        p=self.root/name;p.mkdir();subprocess.run(['git','init','-q',str(p)],check=True);(p/'app.py').write_text('# app\n');return p
    def test_relocation_preserves_frozen_behavior_and_never_inherits_verified(self):
        exported=w.export_experiment(self.e['experiment_id'])
        bundle=json.loads(Path(exported['bundle_path']).read_text())
        self.assertIn('{PROJECT}',bundle['launch']['start']['cwd'])
        self.assertNotIn(str(self.a),json.dumps(bundle))
        imported=w.import_experiment(exported['bundle_path'],str(self.b),sys.executable)
        self.assertEqual(imported['status'],'PREPARED')
        self.assertEqual(imported['runs'],[])
        self.assertEqual(imported['replay']['behavior_sha256'],bundle['behavior_sha256'])
        self.assertEqual(imported['case']['journey'],self.case['journey'])
        self.assertEqual(imported['case']['state'],self.case['state'])
        self.assertEqual(imported['case']['control'],self.case['control'])
        self.assertEqual(imported['case']['app']['start']['cwd'],str(self.b.resolve()))
        self.assertEqual(w.status(imported['experiment_id'])['status'],'PREPARED')
    def test_changed_assertions_or_evaluator_are_rejected(self):
        exported=w.export_experiment(self.e['experiment_id']);path=Path(exported['bundle_path']);original=json.loads(path.read_text())
        for change in ('behavior','launch','evaluator_sha256'):
            bundle=copy.deepcopy(original)
            if change=='behavior':bundle[change]['state']['expect']=99
            elif change=='launch':bundle[change]['start']['command']=['unrelated']
            else:bundle[change]['crashlab_runner.js']='0'*64
            path.write_text(json.dumps(bundle))
            with self.subTest(change=change),self.assertRaises(ValueError):w.import_experiment(str(path),str(self.b),sys.executable)
    def test_explicit_command_interpreter_is_rebound_and_actually_executed(self):
        case=copy.deepcopy(self.case);case['app']['start'].pop('python')
        case['app']['start']['command']=[sys.executable,'app.py']
        prepared=w.prepare(str(self.a),'Explicit Python launch',case)
        exported=w.export_experiment(prepared['experiment_id'])
        marker=self.root/'bound-interpreter-used'
        wrapper=self.root/'application-python'
        wrapper.write_text('#!/bin/sh\necho used >> '+shlex.quote(str(marker))+'\nexec '+shlex.quote(sys.executable)+' "$@"\n');wrapper.chmod(0o755)
        (self.b/'app.py').write_text('import os\nfrom http.server import HTTPServer, SimpleHTTPRequestHandler\nHTTPServer(("127.0.0.1",int(os.environ["PORT"])),SimpleHTTPRequestHandler).serve_forever()\n')
        imported=w.import_experiment(exported['bundle_path'],str(self.b),str(wrapper))
        process,_,_,handle,_=w.core._start_app(imported['case'],self.root)
        try:self.assertTrue(marker.exists(),'The requested interpreter was ignored')
        finally:w.core._stop_process(process);handle.close()
    def test_unbindable_runtime_launch_is_rejected(self):
        case=copy.deepcopy(self.case);case['app']['start']['command']=['node','server.js']
        prepared=w.prepare(str(self.a),'Another runtime',case)
        with self.assertRaisesRegex(ValueError,'direct Python'):w.export_experiment(prepared['experiment_id'])
    def test_historical_report_does_not_promote_old_evaluator(self):
        eid=self.e['experiment_id'];folder,value,_=w.load(eid)
        value['runs']=[{'project_sha256':w.fingerprint(self.a),'workflow_status':'VERIFIED','result':{'status':'PASS','ok':True,'observed':1,'expected':1,'primary_ok':True,'control':{'ok':True,'observed':2,'expected':2}}}]
        w.save(folder/'experiment.json',value)
        with patch.object(w,'evaluator',return_value={'changed':'0'*64}):
            report=w.report(eid)
            self.assertTrue(report['historical_only'])
            self.assertEqual(report['current_status'],'UNAVAILABLE')
            self.assertEqual(report['measurement']['workflow_status'],'VERIFIED')
            with self.assertRaises(ValueError):w.export_experiment(eid)
    def test_corrupt_nested_report_is_actionable(self):
        eid=self.e['experiment_id'];folder,value,_=w.load(eid)
        value['runs']=[{'project_sha256':w.fingerprint(self.a),'workflow_status':'FAIL','result':{'status':'FAIL','cases':[{'name':{},'result':{}}]}}]
        w.save(folder/'experiment.json',value)
        with self.assertRaisesRegex(ValueError,'scenario label'):w.report(eid)
