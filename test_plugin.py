"""Hermes handler contract: positional argument mapping and JSON result."""
import json
import unittest
from unittest.mock import patch
import plugin


class PluginHandlerTests(unittest.TestCase):
    def test_list_accepts_mapping_and_returns_json(self):
        with patch.object(plugin.core, 'list_contracts', return_value=['checkout']):
            self.assertEqual(json.loads(plugin._handler('crashlab_list')({})),
                             {'contracts': ['checkout']})

    def test_validate_accepts_mapping_and_returns_json(self):
        with patch.object(plugin.core, 'load_contract', return_value={'id': 'test'}) as load:
            result = plugin._handler('crashlab_validate')({'contract_path': 'case.json'})
            self.assertEqual(json.loads(result), {'ok': True, 'contract': {'id': 'test'}})
            load.assert_called_once_with('case.json')

    def test_run_forwards_args_and_serializes_result(self):
        with patch.object(plugin.approval, 'prepare_approved_case', return_value={'path': 'case.json', 'contract': {'id': 'test'}}), \
             patch.object(plugin.core, 'run_contract', return_value={'ok': False}) as run, \
             patch.object(plugin.core, 'persist_run', side_effect=lambda value: value) as persist:
            result = plugin._handler('crashlab_run')({'contract_path': 'case.json', 'base_url': 'http://localhost:8765'})
            self.assertEqual(json.loads(result), {'ok': False})
            run.assert_called_once_with('case.json', base_url='http://localhost:8765', frozen_contract={'id': 'test'})
            persist.assert_called_once_with({'ok': False})

    def test_compare_forwards_args_and_serializes_result(self):
        with patch.object(plugin.approval, 'prepare_approved_case', return_value={'path': 'case.json', 'contract': {'id': 'test'}}), \
             patch.object(plugin.core, 'compare', return_value={'ok': True}) as compare:
            result = plugin._handler('crashlab_compare')({'contract_path': 'case.json', 'buggy_url': 'http://localhost:8765', 'fixed_url': 'http://localhost:8766'})
            self.assertEqual(json.loads(result), {'ok': True})
            compare.assert_called_once_with('case.json', 'http://localhost:8765', 'http://localhost:8766', frozen_contract={'id': 'test'})


class FirstUseTests(unittest.TestCase):
    def test_setup_error_is_persisted_and_reported_as_error(self):
        with patch.object(plugin.approval, 'prepare_approved_case', return_value={'path': 'case.json', 'contract': {'id': 'test'}}), \
             patch.object(plugin.core, 'run_contract', side_effect=RuntimeError('interpreter missing')), \
             patch.object(plugin.core, 'persist_run', side_effect=lambda value: value) as persist:
            result = json.loads(plugin._handler('crashlab_run')({'contract_path': 'case.json'}))
        self.assertEqual(result['status'], 'ERROR')
        self.assertIn('interpreter missing', result['error'])
        self.assertFalse(result['ok'])
        persist.assert_called_once_with(result)

    def test_slash_accepts_quoted_case_path(self):
        result = {'status': 'PASS', 'ok': True, 'contract': 'test'}
        approved = {'path': '/my project/case.json', 'contract': {'id': 'test'}}
        with patch.object(plugin.approval, 'prepare_approved_case', return_value=approved), \
             patch.object(plugin.core, 'run_and_persist', return_value=result) as run:
            self.assertIn('PASS', plugin._slash('run "/my project/case.json"'))
        run.assert_called_once_with('/my project/case.json', frozen_contract={'id': 'test'})
        self.assertIn('Usage:', plugin._slash('run "unclosed'))
        self.assertIn('Usage:', plugin._slash('run /my project/case.json'))

    def test_slash_help_does_not_advertise_approval_acquisition(self):
        text = plugin._slash('help')
        self.assertIn('existing approval grant', text)
        self.assertIn('crashlab_run', text)
        self.assertNotIn('request the approval-gated run', text)

    def test_slash_empty_status_directs_to_the_approval_tool(self):
        with patch.object(plugin.core, 'latest_run', return_value=None):
            text = plugin._slash('status')
        self.assertIn('crashlab_run', text)
        self.assertNotIn('/crashlab run <case.json>', text)

    def test_slash_preserves_error_and_invalid_statuses(self):
        with patch.object(plugin.core, 'latest_run', return_value={'ok': False, 'error': 'setup failed'}):
            self.assertIn('ERROR', plugin._slash('status'))
            self.assertIn('setup failed', plugin._slash('status'))
        with patch.object(plugin.core, 'list_runs', return_value=[{'status': 'INVALID', 'ok': False}]):
            self.assertIn('INVALID', plugin._slash('runs'))

    def test_list_includes_starting_cases_and_marks_external_setup(self):
        rows = plugin.core.list_contracts()
        fixed = next(row for row in rows if row['id'] == 'checkout-local-fixed')
        flaskr = next(row for row in rows if row['id'] == 'flaskr-create')
        booking = next(row for row in rows if row['id'] == 'booking-lost-response')
        self.assertTrue(fixed['auto_start'])
        self.assertFalse(flaskr['auto_start'])
        self.assertIn('Flask tutorial', flaskr['setup_required'])
        self.assertFalse(booking['auto_start'])
        self.assertIn('http://127.0.0.1:8785', booking['setup_required'])
        with patch.object(plugin.core, '_start_app') as start:
            with self.assertRaisesRegex(ValueError, 'Case setup required'):
                plugin.core.run_contract(flaskr['path'])
        start.assert_not_called()


class RegisterTests(unittest.TestCase):
    def test_registers_tools_and_slash_command(self):
        calls = []
        class Ctx:
            def register_tool(self, **kwargs): calls.append(('tool', kwargs['name']))
            def register_command(self, name, handler, **kwargs): calls.append(('command', name))
        plugin.register(Ctx())
        self.assertIn(('command', 'crashlab'), calls)
        self.assertIn(('tool', 'crashlab_run'), calls)


if __name__ == '__main__':
    unittest.main()
