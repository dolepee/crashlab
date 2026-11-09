"""The documented Kist generator must emit contracts the current validator accepts."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
RUN_KIST_PATH = ROOT / 'external' / 'kist' / 'run_kist.py'
_spec = importlib.util.spec_from_file_location('run_kist_under_test', RUN_KIST_PATH)
run_kist = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_kist)
core = run_kist.core


class KistGeneratorTests(unittest.TestCase):
    def test_generated_contract_has_recovery_for_ordinary_and_campaign(self):
        for campaign in (False, True):
            with self.subTest(campaign=campaign):
                contract = run_kist.contract_for(Path('/tmp/kist-checkout'), sys.executable)
                if campaign:
                    contract['recovery_campaign'] = [
                        'before_delivery', 'after_commit', 'restart_after_commit']
                core.validate_contract(contract)
                self.assertEqual(contract['recovery'], {
                    'mode': 'retry',
                    'success_statuses': [201],
                    'response_id_path': 'id',
                    'record_id_field': 'id',
                })

    def test_write_contract_only_emits_validate_contract_output(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp) / 'kist'
            (app / 'server').mkdir(parents=True)
            (app / 'server' / 'app.py').write_text('')
            for campaign in (False, True):
                with self.subTest(campaign=campaign):
                    output = Path(temp) / ('run-campaign' if campaign else 'run-ordinary')
                    argv = ['run_kist.py', '--app', str(app), '--output', str(output),
                            '--write-contract-only']
                    if campaign:
                        argv.append('--recovery-campaign')
                    with mock.patch.object(sys, 'argv', argv):
                        self.assertEqual(run_kist.main(), 0)
                    contract = json.loads((output / 'contract.json').read_text())
                    core.validate_contract(contract)
                    self.assertEqual(contract['recovery']['mode'], 'retry')
                    self.assertEqual(contract['recovery']['success_statuses'], [201])
                    self.assertEqual(contract['recovery']['response_id_path'], 'id')
                    self.assertEqual(contract['recovery']['record_id_field'], 'id')


if __name__ == '__main__':
    unittest.main()
