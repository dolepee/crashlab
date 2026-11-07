import asyncio
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
try:
    import fastapi
except ImportError:
    fastapi = None

@unittest.skipUnless(fastapi, 'FastAPI is supplied by the Hermes host')
class ExperimentApiTests(unittest.TestCase):
    def test_unavailable_watched_experiment_can_still_be_stopped(self):
        from dashboard import plugin_api as api
        import repair_workflow as w
        import completion_reminder as reminders
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,CRASHLAB_DATA_DIR=tmp):
            project=Path(tmp)/'app';project.mkdir();subprocess.run(['git','init','-q',str(project)],check=True)
            (project/'app.py').write_text('# app\n')
            case=json.loads((w.core.ROOT/'cases/checkout-local.json').read_text());case['app']['start']['cwd']=str(project)
            e=w.prepare(str(project),'One write per retry',case);eid=e['experiment_id']
            reminders.watch(eid,True)
            (project/'.git').rename(project/'unavailable-git')
            row=asyncio.run(api.experiments())['experiments'][0]
            self.assertEqual(row['status'],'UNAVAILABLE');self.assertTrue(row['watching'])
            result=asyncio.run(api.experiment_watch(eid,api.WatchRequest(enabled=False)))
            self.assertFalse(result['watching'])
    def test_run_rejects_invalid_identity(self):
        from dashboard import plugin_api as api
        with self.assertRaises(fastapi.HTTPException) as exc:
            asyncio.run(api.experiment_run('../outside'))
        self.assertEqual(exc.exception.status_code,400)

    def test_corrupt_metadata_does_not_hide_other_experiments_or_opt_out(self):
        from dashboard import plugin_api as api
        import repair_workflow as w
        import completion_reminder as reminders
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,CRASHLAB_DATA_DIR=tmp):
            project=Path(tmp)/'app';project.mkdir();subprocess.run(['git','init','-q',str(project)],check=True)
            (project/'app.py').write_text('# app\n')
            case=json.loads((w.core.ROOT/'cases/checkout-local.json').read_text());case['app']['start']['cwd']=str(project)
            bad=w.prepare(str(project),'first case',case)['experiment_id'];reminders.watch(bad,True)
            good=w.prepare(str(project),'second case',case)['experiment_id']
            (w.directory(bad)/'experiment.json').write_text('null')
            rows=asyncio.run(api.experiments())['experiments'];byid={r['experiment_id']:r for r in rows}
            self.assertEqual(byid[bad]['status'],'UNAVAILABLE');self.assertTrue(byid[bad]['watching'])
            self.assertEqual(byid[good]['status'],'PREPARED')
            self.assertFalse(asyncio.run(api.experiment_watch(bad,api.WatchRequest(enabled=False)))['watching'])

    def test_corrupt_labels_are_not_forwarded_as_react_children(self):
        from dashboard import plugin_api as api
        import repair_workflow as w
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,CRASHLAB_DATA_DIR=tmp):
            eid='b'*32;folder=w.directory(eid);folder.mkdir(parents=True)
            (folder/'experiment.json').write_text(json.dumps({'intent':{'broken':'label'},'project_path':['broken']}))
            row=asyncio.run(api.experiments())['experiments'][0]
            self.assertEqual(row['status'],'UNAVAILABLE')
            self.assertIsNone(row['intent']);self.assertIsNone(row['project_path'])
