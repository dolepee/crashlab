"""Host-dispatch regression for CrashLab approval.

This test is intentionally opt-in: it loads the pinned Hermes agent source and
exercises its real PluginManager hook registry and tool dispatcher. Set
HERMES_AGENT_SOURCE to a Hermes checkout to run it; CI without Hermes skips it
rather than replacing the host with a direct callback probe.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

CASE = Path(__file__).resolve().parent / 'cases' / 'checkout-local.json'
SOURCE = os.environ.get('HERMES_AGENT_SOURCE', '')


@unittest.skipUnless(SOURCE and (Path(SOURCE) / 'hermes_cli' / 'plugins.py').is_file()
                     and importlib.util.find_spec('ruamel.yaml') is not None,
                     'set HERMES_AGENT_SOURCE to a Hermes checkout with runtime dependencies')
class HermesHostDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name) / 'hermes-home'
        plugins = home / 'plugins'
        plugins.mkdir(parents=True)
        shutil.copytree(Path(__file__).resolve().parent, plugins / 'crashlab',
                        ignore=shutil.ignore_patterns('.git', '.cache', '.local', '__pycache__',
                                                      'node_modules', 'out', 'test_*.py'))
        (home / 'config.yaml').write_text('plugins:\n  enabled: [crashlab]\n')
        self.env = patch.dict(os.environ, {
            'HERMES_HOME': str(home),
            'CRASHLAB_DATA_DIR': str(Path(self.tmp.name) / 'data'),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        sys.path.insert(0, SOURCE)
        from hermes_cli import plugins as lifecycle
        from hermes_cli.plugins import PluginManager
        from tools import approval as approval_mod
        from tools.registry import registry

        self.lifecycle = lifecycle
        self.approval_mod = approval_mod
        self.registry = registry
        self.manager = PluginManager()
        # Each test loads its own copy of the plugin; use the host's test-only
        # clean slate so the handler under test is the one just loaded.
        lifecycle._reset_plugin_managers_for_tests()
        self.manager.discover_and_load()
        lifecycle._plugin_manager = self.manager
        lifecycle._plugin_managers_by_home[Path(self.manager.scope_key)] = self.manager
        loaded = self.manager._plugins['crashlab']
        self.assertFalse(loaded.error, loaded.error)
        self.assertIn('pre_tool_call', loaded.hooks_registered)
        self.assertIn('post_approval_response', loaded.hooks_registered)
        self.assertIn('tool_execution', loaded.middleware_registered)
        self.module = sys.modules['hermes_plugins.crashlab.plugin']
        self.session = 'host-session'
        self.call = 'native-call-1'
        self.args = {'contract_path': str(CASE)}

    def dispatch(self, session=None):
        session = session or self.session
        return json.loads(self.registry.dispatch(
            'crashlab_run', self.args, scope=self.manager.scope_key,
            task_id=session, session_id=session, tool_call_id=self.call))

    def run_with_decision(self, decision, *, session_key=None):
        session_key = session_key or self.session
        if decision == 'deny':
            def fake(tool_name, reason, *, rule_key='', **kwargs):
                return {'approved': False, 'message': 'human denied'}
        else:
            def fake(tool_name, reason, *, rule_key='', **kwargs):
                self.manager.invoke_hook(
                    'post_approval_response', command='<crashlab_run>', description=reason,
                    pattern_key='plugin_rule:' + rule_key,
                    pattern_keys=['plugin_rule:' + rule_key],
                    session_key=session_key, session_id=self.session, surface='cli',
                    tool_call_id=self.call, choice='once')
                return {'approved': True, 'message': None}
        with patch.object(self.approval_mod, 'request_tool_approval', side_effect=fake):
            from hermes_cli.plugins import _dispatch_pre_tool_call_hooks
            block, _ = _dispatch_pre_tool_call_hooks(
                'crashlab_run', self.args, task_id=self.session,
                session_id=self.session, tool_call_id=self.call)
            return block, self.dispatch()

    def test_denied_direct_dispatch_stays_blocked(self):
        block, result = self.run_with_decision('deny')
        self.assertTrue(block)
        self.assertFalse(result['ok'])
        self.assertTrue(result['needs_approval'])
        self.assertFalse(self.run_with_decision('deny')[1]['ok'])

    def test_approved_once_reaches_the_handler_once(self):
        with patch.object(self.module.core, 'run_and_persist', return_value={'ok': True}) as run:
            block, result = self.run_with_decision('approve')
        self.assertIsNone(block)
        self.assertEqual(result, {'ok': True})
        self.assertEqual(run.call_count, 1)
        self.assertIn('frozen_contract', run.call_args.kwargs)

    def test_once_decision_with_a_distinct_session_key_authorises_one_dispatch(self):
        with patch.object(self.module.core, 'run_and_persist', return_value={'ok': True}) as run:
            block, result = self.run_with_decision('approve', session_key='host-key')
            self.assertIsNone(block)
            self.assertEqual(result, {'ok': True})
            # The host reports two scope aliases for one decision; neither may
            # spend it again, in either order.
            second = self.dispatch()
            third = self.dispatch('host-key')
        self.assertEqual(run.call_count, 1)
        for replay in (second, third):
            self.assertFalse(replay['ok'], replay)
            self.assertTrue(replay['needs_approval'], replay)

    def test_overlapping_calls_in_one_session_keep_their_own_approvals(self):
        """Two live calls in one session must not have to guess which approval is theirs.

        The host binds each call's originating id for the duration of the dispatch
        but passes only session/task ids to the handler, so both calls previously
        landed on the ambiguous-single-grant rule and were rejected. The identity
        used here comes from the host; nothing is supplied by the caller.
        """
        import model_tools
        from tools import approval as host_approval
        from tools.approval_context import _fire_approval_hook

        executed = []
        barrier = threading.Barrier(2)

        def execute(*args, **kwargs):
            executed.append(1)
            # Both approvals must be complete before either plan executes.
            barrier.wait(timeout=20)
            return {'ok': True}

        def decide(tool_name, reason, *, rule_key='', **kwargs):
            _fire_approval_hook(
                'post_approval_response', command=f'<{tool_name}>', description=reason,
                pattern_key='plugin_rule:' + rule_key, pattern_keys=['plugin_rule:' + rule_key],
                session_key='host-key', surface='cli', choice='once')
            return {'approved': True, 'message': None}

        results = {}

        def call(call_id):
            results[call_id] = model_tools.handle_function_call(
                'crashlab_run', self.args, task_id=self.session, session_id=self.session,
                tool_call_id=call_id, user_task='run the case',
                skip_tool_execution_middleware=False)

        with patch.object(host_approval, 'request_tool_approval', side_effect=decide), \
             patch.object(self.module.core, 'run_and_persist', side_effect=execute):
            threads = [threading.Thread(target=call, args=(call_id,))
                       for call_id in ('native-call-A', 'native-call-B')]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
        self.assertEqual(len(executed), 2, results)
        for call_id, payload in results.items():
            self.assertEqual(json.loads(payload), {'ok': True}, f'{call_id}: {payload}')


if __name__ == '__main__':
    unittest.main()
