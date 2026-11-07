import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import completion_reminder as reminder

class ReminderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.data=Path(self.tmp.name);self.project=self.data/'app';self.project.mkdir()
        self.env=patch.dict(os.environ,CRASHLAB_DATA_DIR=str(self.data/'evidence'));self.env.start();self.addCleanup(self.env.stop)
        self.eid='a'*32
        self.load=patch.object(reminder.workflow,'load',return_value=(None,{'project_path':str(self.project)},None));self.load.start();self.addCleanup(self.load.stop)
    def hook(self,state='STALE',paths=None):
        with patch.object(reminder.workflow,'status',return_value={'status':state}):
            return reminder.pre_verify(changed_paths=paths or [str(self.project/'app.py')])
    def test_opt_in_stale_continue_and_verified_finish(self):
        self.assertIsNone(self.hook())
        reminder.watch(self.eid,True)
        self.assertEqual(self.hook()['action'],'continue')
        self.assertIsNone(self.hook('VERIFIED'))
        reminder.watch(self.eid,False)
        self.assertIsNone(self.hook())
    def test_unrelated_and_relative_changes_are_unaffected(self):
        reminder.watch(self.eid,True)
        self.assertIsNone(self.hook(paths=[str(self.data/'other/app.py')]))
        self.assertIsNone(self.hook(paths=['app.py']))
    def test_unavailable_matching_evidence_requests_honest_recovery(self):
        reminder.watch(self.eid,True)
        with patch.object(reminder.workflow,'status',side_effect=ValueError('case changed')):
            self.assertIn('UNAVAILABLE',reminder.pre_verify(changed_paths=[str(self.project/'app.py')])['message'])
    def test_new_watch_replaces_previous_for_project(self):
        reminder.watch(self.eid,True);reminder.watch('b'*32,True);reminder.watch(self.eid,False)
        paths=list((self.data/'evidence/watches').glob('*.json'))
        self.assertEqual(len(paths),1);self.assertEqual(json.loads(paths[0].read_text())['experiment_id'],'b'*32)

    def test_git_failure_and_timeout_request_unavailable_recovery(self):
        import subprocess
        reminder.watch(self.eid,True)
        for error in [subprocess.CalledProcessError(128,['git']), subprocess.TimeoutExpired(['git'],15)]:
            with self.subTest(error=type(error).__name__), patch.object(reminder.workflow,'status',side_effect=error):
                result=reminder.pre_verify(changed_paths=[str(self.project/'app.py')])
                self.assertEqual(result['action'],'continue')
                self.assertIn('UNAVAILABLE',result['message'])
