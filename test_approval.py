"""Approval policy tests for every CrashLab execution route."""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import approval
import plugin
import repair_workflow as workflow


CASE = Path('cases/checkout-local.json').resolve()


def approve(tool, args, *, choice='once', tool_call_id='call-1', session_id='session-1'):
    digest = approval.plan_digest(tool, args)
    approval.post_approval_response(
        pattern_key='plugin_rule:' + approval._rule_key(tool, digest),
        choice=choice,
        tool_call_id=tool_call_id,
        session_id=session_id,
    )
    return digest


class ApprovalPolicyTests(unittest.TestCase):
    def setUp(self):
        approval.reset_for_tests()

    def tearDown(self):
        approval.reset_for_tests()

    def args(self, path=CASE):
        return {'contract_path': str(path)}

    def test_execution_tools_escalate_and_read_only_tools_do_not(self):
        for name in sorted(approval.EXECUTION_TOOLS):
            with self.subTest(name=name):
                if name == 'crashlab_compare':
                    args = {**self.args(), 'buggy_url': 'http://127.0.0.1:1', 'fixed_url': 'http://127.0.0.1:2'}
                elif name == 'crashlab_experiment_run':
                    args = {'experiment_id': 'a' * 32}
                elif name == 'crashlab_campaign_run':
                    args = {'campaign_id': 'inventory-contention', 'target_set': 'local', 'repetitions': 1}
                else:
                    args = self.args()
                directive = approval.pre_tool_call(name, args, tool_call_id='call-1', session_id='session-1')
                if name == 'crashlab_experiment_run':
                    # Missing experiment metadata is a plan-resolution error, not a bypass.
                    self.assertEqual(directive['action'], 'block')
                elif name == 'crashlab_campaign_run':
                    self.assertEqual(directive['action'], 'approve')
                else:
                    self.assertEqual(directive['action'], 'approve')
        self.assertIsNone(approval.pre_tool_call('crashlab_validate', self.args()))
        self.assertIsNone(approval.pre_tool_call('crashlab_list', {}))

    def blocked(self, result):
        self.assertFalse(result['ok'])
        self.assertTrue(result['needs_approval'])

    def test_pre_hook_alone_is_not_approval(self):
        approval.pre_tool_call('crashlab_run', self.args(), tool_call_id='call-1', session_id='session-1')
        self.blocked(json.loads(plugin._handler('crashlab_run')(self.args(), session_id='session-1')))

    def test_once_approval_is_consumed(self):
        args = self.args()
        directive = approval.pre_tool_call('crashlab_run', args, tool_call_id='call-1', session_id='session-1')
        self.assertEqual(directive['action'], 'approve')
        approve('crashlab_run', args)
        with patch.object(plugin.core, 'run_and_persist', return_value={'ok': True}) as run:
            self.assertEqual(json.loads(plugin._handler('crashlab_run')(args, session_id='session-1')), {'ok': True})
            run.assert_called_once()
        self.blocked(json.loads(plugin._handler('crashlab_run')(args, session_id='session-1')))

    def test_denial_does_not_grant(self):
        args = self.args()
        approval.pre_tool_call('crashlab_run', args, tool_call_id='call-1', session_id='session-1')
        approve('crashlab_run', args, choice='deny')
        self.blocked(json.loads(plugin._handler('crashlab_run')(args, session_id='session-1')))

    def test_smart_approval_is_not_human_approval(self):
        args = self.args()
        approval.pre_tool_call('crashlab_run', args, tool_call_id='call-1', session_id='session-1')
        approve('crashlab_run', args, choice='smart_approve')
        self.blocked(json.loads(plugin._handler('crashlab_run')(args, session_id='session-1')))

    def test_changed_case_bytes_are_denied(self):
        args = self.args()
        approval.pre_tool_call('crashlab_run', args, tool_call_id='call-1', session_id='session-1')
        approve('crashlab_run', args)
        changed = json.loads(CASE.read_text())
        changed['journey'][0]['path'] = '/changed'
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'case.json'
            path.write_text(json.dumps(changed))
            self.blocked(json.loads(plugin._handler('crashlab_run')(self.args(path), session_id='session-1')))

    def test_changed_command_and_path_are_denied(self):
        original = self.args()
        approval.pre_tool_call('crashlab_run', original, tool_call_id='call-1', session_id='session-1')
        approve('crashlab_run', original)
        changed = copy.deepcopy(json.loads(CASE.read_text()))
        changed['app']['start']['command'] = ['python3', 'different.py']
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'case.json'
            path.write_text(json.dumps(changed))
            self.blocked(json.loads(plugin._handler('crashlab_run')(self.args(path), session_id='session-1')))

    def test_path_change_with_identical_bytes_is_denied(self):
        original = self.args()
        approval.pre_tool_call('crashlab_run', original, tool_call_id='call-1', session_id='session-1')
        approve('crashlab_run', original)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'case.json'
            path.write_bytes(CASE.read_bytes())
            self.blocked(json.loads(plugin._handler('crashlab_run')(self.args(path), session_id='session-1')))

    def test_dashboard_route_rejects_direct_execution(self):
        try:
            from dashboard import plugin_api as api
            from fastapi import HTTPException
        except ImportError:
            self.skipTest('dashboard dependencies are not installed')
        with self.assertRaises(HTTPException) as exc:
            asyncio.run(api.run(api.RunRequest(contract_path=str(CASE))))
        self.assertEqual(exc.exception.status_code, 403)

    def test_dashboard_operator_ticket_is_single_use_and_plan_bound(self):
        try:
            from dashboard import plugin_api as api
            from fastapi import HTTPException
        except ImportError:
            self.skipTest('dashboard dependencies are not installed')
        with patch('crashlab_core.run_and_persist', return_value={'ok': True}) as run:
            approval_doc = asyncio.run(api.create_approval(
                api.ApprovalRequest(kind='run', contract_path=str(CASE))))
            result = asyncio.run(api.run(api.RunRequest(contract_path=str(CASE)),
                                         x_crashlab_approval=approval_doc['token']))
            self.assertEqual(result, {'ok': True})
            run.assert_called_once()
            with self.assertRaises(HTTPException) as exc:
                asyncio.run(api.run(api.RunRequest(contract_path=str(CASE)),
                                    x_crashlab_approval=approval_doc['token']))
            self.assertEqual(exc.exception.status_code, 403)

    def test_dashboard_ticket_does_not_cover_a_different_plan(self):
        try:
            from dashboard import plugin_api as api
            from fastapi import HTTPException
        except ImportError:
            self.skipTest('dashboard dependencies are not installed')
        approval_doc = asyncio.run(api.create_approval(
            api.ApprovalRequest(kind='run', contract_path=str(CASE))))
        with self.assertRaises(HTTPException) as exc:
            asyncio.run(api.run(api.RunRequest(contract_path=str(Path('cases/checkout-recovery.json').resolve())),
                                x_crashlab_approval=approval_doc['token']))
        self.assertEqual(exc.exception.status_code, 403)

    def test_slash_route_requires_tool_approval(self):
        message = plugin._slash('run "cases/checkout-local.json"')
        self.assertIn('human approval', message)

    def test_cli_operator_flow_requires_an_interactive_confirmation(self):
        import crashlab_replay
        with patch('sys.stdin.isatty', return_value=False):
            with self.assertRaises(PermissionError):
                crashlab_replay._operator_approval('a' * 32)
        with patch('sys.stdin.isatty', return_value=True), \
             patch('builtins.input', return_value='yes'), \
             patch.object(approval, 'execution_plan', return_value={'tool': 'crashlab_experiment_run'}), \
             patch.object(approval, 'grant_for_operator') as grant:
            crashlab_replay._operator_approval('a' * 32)
        grant.assert_called_once()

    def test_hooks_are_registered_and_declared(self):
        calls = []
        class Context:
            def register_hook(self, name, callback): calls.append(name)
            def register_tool(self, **kwargs): pass
            def register_command(self, *args, **kwargs): pass
        plugin.register(Context())
        self.assertIn('pre_tool_call', calls)
        self.assertIn('post_approval_response', calls)
        declared = (Path('plugin.yaml').read_text())
        self.assertIn('  - pre_tool_call', declared)
        self.assertIn('  - post_approval_response', declared)


if __name__ == '__main__':
    unittest.main()
