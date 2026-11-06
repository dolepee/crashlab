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
        with patch.object(plugin.core, 'run_contract', return_value={'ok': False}) as run, \
             patch.object(plugin.core, 'persist_run', side_effect=lambda value: value) as persist:
            result = plugin._handler('crashlab_run')({'contract_path': 'case.json', 'base_url': 'http://localhost:8765'})
            self.assertEqual(json.loads(result), {'ok': False})
            run.assert_called_once_with('case.json', base_url='http://localhost:8765')
            persist.assert_called_once_with({'ok': False})

    def test_compare_forwards_args_and_serializes_result(self):
        with patch.object(plugin.core, 'compare', return_value={'ok': True}) as compare:
            result = plugin._handler('crashlab_compare')({'contract_path': 'case.json', 'buggy_url': 'http://localhost:8765', 'fixed_url': 'http://localhost:8766'})
            self.assertEqual(json.loads(result), {'ok': True})
            compare.assert_called_once_with('case.json', 'http://localhost:8765', 'http://localhost:8766')


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
