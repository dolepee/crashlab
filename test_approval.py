"""Approval policy tests for every CrashLab execution route."""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import approval
import crashlab_core as core
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
                    # No disposable target is configured in this fixture, so
                    # the campaign plan cannot be frozen and must block.
                    self.assertEqual(directive['action'], 'block')
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

    def test_once_approval_is_single_use_across_host_aliases(self):
        args = self.args()
        approval.pre_tool_call('crashlab_run', args, tool_call_id='call-1',
                               session_id='session-1', task_id='session-key-1')
        digest = approval.plan_digest('crashlab_run', args)
        # The host reports both a session id and a session key for one decision.
        approval.post_approval_response(
            pattern_key='plugin_rule:' + approval._rule_key('crashlab_run', digest),
            choice='once', tool_call_id='call-1',
            session_id='session-1', session_key='session-key-1')
        with patch.object(plugin.core, 'run_and_persist', return_value={'ok': True}) as run:
            self.assertEqual(
                json.loads(plugin._handler('crashlab_run')(args, session_id='session-1',
                                                          task_id='session-key-1')),
                {'ok': True})
            # Neither alias may spend the same once decision a second time.
            self.blocked(json.loads(plugin._handler('crashlab_run')(args, session_id='session-1',
                                                                   task_id='session-key-1')))
            self.blocked(json.loads(plugin._handler('crashlab_run')(args, session_id='session-key-1',
                                                                   task_id='session-1')))
            run.assert_called_once()

    def test_approved_case_runs_the_frozen_snapshot_after_a_file_swap(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'case.json'
            original = json.loads(CASE.read_text())
            path.write_text(json.dumps(original))
            args = self.args(path)
            directive = approval.pre_tool_call('crashlab_run', args, tool_call_id='call-1',
                                               session_id='session-1')
            self.assertEqual(directive['action'], 'approve')
            approve('crashlab_run', args)
            changed = copy.deepcopy(original)
            changed['app']['start']['command'] = ['/bin/sh', '-c', 'touch should-not-run']
            path.write_text(json.dumps(changed))
            with patch.object(plugin.core, 'run_and_persist', return_value={'ok': True}) as run:
                result = json.loads(plugin._handler('crashlab_run')(args, session_id='session-1'))
            self.assertTrue(result['ok'], result)
            frozen = run.call_args.kwargs['frozen_contract']
            self.assertEqual(frozen['app']['start'], original['app']['start'])

    def test_compare_executes_the_approved_resolved_path_after_a_symlink_swap(self):
        """A symlink retargeted after the guard must not redirect comparison observers."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            approved_dir = root / 'approved'
            replacement_dir = root / 'replacement'
            approved_dir.mkdir()
            replacement_dir.mkdir()
            original = json.loads(CASE.read_text())
            (approved_dir / 'case.json').write_text(json.dumps(original))
            (replacement_dir / 'case.json').write_text(json.dumps(original))
            link = root / 'case.json'
            link.symlink_to(approved_dir / 'case.json')
            args = {'contract_path': str(link), 'buggy_url': 'http://127.0.0.1:3101',
                    'fixed_url': 'http://127.0.0.1:3102'}
            approval.pre_tool_call('crashlab_compare', args, tool_call_id='call-1',
                                   session_id='session-1')
            approve('crashlab_compare', args)
            real_prepare = approval.prepare_approved_case

            def prepare_then_retarget(*arguments, **keywords):
                approved = real_prepare(*arguments, **keywords)
                link.unlink()
                link.symlink_to(replacement_dir / 'case.json')
                return approved

            with patch.object(plugin.core, 'compare', return_value={'ok': True}) as compare, \
                 patch.object(plugin.approval, 'prepare_approved_case',
                              side_effect=prepare_then_retarget):
                result = json.loads(plugin._handler('crashlab_compare')(args, session_id='session-1'))
            self.assertTrue(result['ok'], result)
            self.assertEqual(Path(compare.call_args.args[0]),
                             (approved_dir / 'case.json').resolve())

    def test_approval_binds_full_urls_and_tool(self):
        args = {**self.args(), 'base_url': 'http://127.0.0.1:3101'}
        approval.pre_tool_call('crashlab_run', args, tool_call_id='call-1', session_id='session-1')
        approve('crashlab_run', args)
        with patch.object(plugin.core, 'run_and_persist', return_value={'ok': True}) as run:
            changed = {**args, 'base_url': 'http://127.0.0.1:3102'}
            self.blocked(json.loads(plugin._handler('crashlab_run')(changed, session_id='session-1')))
            compare = {'contract_path': str(CASE), 'buggy_url': 'http://127.0.0.1:3101',
                       'fixed_url': 'http://127.0.0.1:3102'}
            self.blocked(json.loads(plugin._handler('crashlab_compare')(compare, session_id='session-1')))
        run.assert_not_called()

    def test_approval_binds_originating_call(self):
        args = self.args()
        approval.pre_tool_call('crashlab_run', args, tool_call_id='call-A', session_id='session-1')
        approval.post_approval_response(
            pattern_key='plugin_rule:' + approval._rule_key('crashlab_run', approval.plan_digest('crashlab_run', args)),
            choice='once', tool_call_id='call-A', session_id='session-1')
        with patch.object(plugin.core, 'run_and_persist', return_value={'ok': True}) as run:
            self.blocked(json.loads(plugin._handler('crashlab_run')(
                args, session_id='session-1', tool_call_id='call-B')))
            self.assertEqual(json.loads(plugin._handler('crashlab_run')(
                args, session_id='session-1', tool_call_id='call-A')), {'ok': True})
        run.assert_called_once()

    def test_operator_snapshot_survives_source_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'case.json'
            original = json.loads(CASE.read_text())
            path.write_text(json.dumps(original))
            args = self.args(path)
            snapshot = approval.capture_plan('crashlab_run', args)
            approval.grant_for_operator('crashlab_run', args, session_id='operator', snapshot=snapshot)
            changed = copy.deepcopy(original)
            changed['app']['start']['command'] = ['/bin/sh', '-c', 'touch should-not-run']
            path.write_text(json.dumps(changed))
            approved = approval.prepare_approved_case('crashlab_run', args,
                                                       session_id='operator', snapshot=snapshot)
            self.assertEqual(approved['contract']['app']['start'], original['app']['start'])

    def test_dashboard_ticket_executes_the_snapshot_operator_saw(self):
        try:
            from dashboard import plugin_api as api
        except ImportError:
            self.skipTest('dashboard dependencies are not installed')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'case.json'
            original = json.loads(CASE.read_text())
            path.write_text(json.dumps(original))
            with patch.object(api, '_resolve_case', return_value=path), \
                 patch('crashlab_core.run_and_persist', return_value={'ok': True}) as run:
                ticket = asyncio.run(api.create_approval(
                    api.ApprovalRequest(kind='run', contract_path=str(path))))
                changed = copy.deepcopy(original)
                changed['app']['start']['command'] = ['/bin/sh', '-c', 'touch should-not-run']
                path.write_text(json.dumps(changed))
                self.assertEqual(asyncio.run(api.run(
                    api.RunRequest(contract_path=str(path)),
                    x_crashlab_approval=ticket['token'])), {'ok': True})
            frozen = run.call_args.kwargs['frozen_contract']
            self.assertEqual(frozen['app']['start'], original['app']['start'])

    def test_decision_without_a_call_id_still_reaches_one_bound_dispatch(self):
        """A host that reports the decision without a call id is not left unusable."""
        args = self.args()
        approval.pre_tool_call('crashlab_run', args, tool_call_id='call-1', session_id='session-1')
        approve('crashlab_run', args, tool_call_id='')
        with patch.object(plugin.core, 'run_and_persist', return_value={'ok': True}) as run:
            self.assertEqual(json.loads(plugin._handler('crashlab_run')(
                args, session_id='session-1', tool_call_id='call-1')), {'ok': True})
            self.blocked(json.loads(plugin._handler('crashlab_run')(
                args, session_id='session-1', tool_call_id='call-1')))
        run.assert_called_once()

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

    def test_slash_route_executes_the_bytes_it_validated(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'case.json'
            original = json.loads(CASE.read_text())
            path.write_text(json.dumps(original))
            args = self.args(path)
            approval.pre_tool_call('crashlab_run', args, tool_call_id='call-1', session_id='session-1')
            approve('crashlab_run', args, choice='always')
            with patch.object(plugin.core, 'run_and_persist', return_value={'ok': True}) as run:
                self.assertIn('PASS', plugin._slash(f'run "{path}"'))
            frozen = run.call_args.kwargs['frozen_contract']
            self.assertEqual(frozen['app']['start'], original['app']['start'])

    def test_repeated_identical_invocation_without_a_call_id_uses_its_own_snapshot(self):
        """A host that reports no call id must still run the plan it just approved."""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'case.json'
            original = json.loads(CASE.read_text())
            path.write_text(json.dumps(original))
            args = self.args(path)
            approval.pre_tool_call('crashlab_run', args, session_id='session-1')
            approve('crashlab_run', args, tool_call_id='')
            with patch.object(plugin.core, 'run_and_persist', return_value={'ok': True}) as run:
                self.assertEqual(json.loads(plugin._handler('crashlab_run')(args, session_id='session-1')),
                                 {'ok': True})
            changed = copy.deepcopy(original)
            changed['app']['start']['command'] = ['/bin/sh', '-c', 'exit 0']
            path.write_text(json.dumps(changed))
            approval.pre_tool_call('crashlab_run', args, session_id='session-1')
            approve('crashlab_run', args, tool_call_id='')
            with patch.object(plugin.core, 'run_and_persist', return_value={'ok': True}) as run:
                self.assertEqual(json.loads(plugin._handler('crashlab_run')(args, session_id='session-1')),
                                 {'ok': True})
            self.assertEqual(run.call_args.kwargs['frozen_contract']['app']['start'],
                             changed['app']['start'])

    def test_cli_operator_flow_requires_an_interactive_confirmation(self):
        import crashlab_replay
        with patch('sys.stdin.isatty', return_value=False):
            with self.assertRaises(PermissionError):
                crashlab_replay._operator_approval('a' * 32)
        snapshot = {'tool': 'crashlab_experiment_run', 'plan': {'tool': 'crashlab_experiment_run'}}
        with patch('sys.stdin.isatty', return_value=True), \
             patch('builtins.input', return_value='yes'), \
             patch.object(approval, 'capture_plan', return_value=snapshot), \
             patch.object(approval, 'grant_for_operator') as grant:
            self.assertIs(crashlab_replay._operator_approval('a' * 32), snapshot)
        grant.assert_called_once()
        self.assertIs(grant.call_args.kwargs['snapshot'], snapshot)

    def _prepared_experiment(self, temporary, *, intent='Reproduce the lost response'):
        folder = Path(temporary) / 'experiments' / ('c' * 32)
        folder.mkdir(parents=True)
        (folder / 'case.json').write_text(CASE.read_text(encoding='utf-8'))
        (folder / 'experiment.json').write_text(json.dumps({
            'experiment_id': 'c' * 32, 'project_path': str(Path(temporary) / 'project'),
            'intent': intent, 'runs': []}, ensure_ascii=False), encoding='utf-8')
        return 'c' * 32

    def test_tool_adapter_rejects_a_caller_supplied_approval_snapshot(self):
        """Model/API arguments cannot hand the run route its own approval object."""
        with tempfile.TemporaryDirectory() as temporary, \
             patch.dict('os.environ', {'CRASHLAB_DATA_DIR': temporary}):
            experiment = self._prepared_experiment(temporary)
            snapshot = approval.capture_plan('crashlab_experiment_run', {'experiment_id': experiment})
        for extra in ('approved', 'approved_snapshot', 'snapshot'):
            with self.subTest(argument=extra):
                with patch.object(workflow, '_run') as run:
                    with self.assertRaises(ValueError):
                        plugin._handler('crashlab_experiment_run')(
                            {'experiment_id': experiment, extra: snapshot}, session_id='session-1')
                    run.assert_not_called()

    def test_invented_snapshot_without_a_grant_does_not_execute(self):
        """Even an internal caller that builds the snapshot itself gets no lease."""
        case = json.loads(CASE.read_text())
        value = {'experiment_id': 'd' * 32, 'project_path': '/tmp/project', 'intent': 'x', 'runs': []}
        forged = {'tool': 'crashlab_experiment_run', 'args': {'experiment_id': 'd' * 32},
                  'contract': case, 'experiment': value,
                  'experiment_digest': workflow.digest(value),
                  'digest': approval._sha256(approval._canonical({'tool': 'crashlab_experiment_run'}))}
        with patch.object(workflow, '_run') as run:
            with self.assertRaises(approval.ApprovalRequired):
                workflow.run('d' * 32, approval_context={'session_id': 'session-1'},
                             approved_snapshot=forged)
            run.assert_not_called()

    def test_unicode_experiment_metadata_matches_the_workflow_digest(self):
        value = {'intent': 'Vérifier la récupération', 'project_path': '/tmp/proj-ü', 'runs': []}
        self.assertEqual(approval._sha256(approval._canonical(value)), workflow.digest(value))

    def test_dashboard_approval_shows_the_plan_it_will_execute(self):
        try:
            from dashboard import plugin_api as api
        except ImportError:
            self.skipTest('dashboard dependencies are not installed')
        approval_doc = asyncio.run(api.create_approval(
            api.ApprovalRequest(kind='run', contract_path=str(CASE))))
        plan = approval_doc.get('plan') or {}
        self.assertEqual(plan.get('contract_path'), str(CASE))
        self.assertEqual(plan.get('app_start'), json.loads(CASE.read_text())['app']['start'])
        self.assertEqual(plan.get('contract_sha256'), approval._sha256(CASE.read_bytes()))
        with api._TICKET_LOCK:
            self.assertIn(approval_doc['token'], api._TICKETS)
        self.assertEqual(asyncio.run(api.cancel_approval(api.CancelRequest(token=approval_doc['token']))),
                         {'ok': True})
        with api._TICKET_LOCK:
            self.assertNotIn(approval_doc['token'], api._TICKETS)

    def test_effective_target_precedence_matches_the_execution_resolver(self):
        base_case = json.loads(CASE.read_text())
        cases = [
            ('managed start wins over a conflicting app URL',
             {'base_url': 'http://127.0.0.1:3999',
              'start': {'base_url': 'http://127.0.0.1:4011'}},
             {}, 'http://127.0.0.1:4011'),
            ('managed start fixed port without a URL',
             {'start': {'port': 4012}},
             {}, 'http://127.0.0.1:4012'),
            ('unmanaged external fallback',
             {'base_url': 'http://127.0.0.1:4013'},
             {}, 'http://127.0.0.1:4013'),
            ('tool override wins over managed and external URLs',
             {'base_url': 'http://127.0.0.1:3999',
              'start': {'base_url': 'http://127.0.0.1:4014'}},
             {'base_url': 'http://127.0.0.1:4015'}, 'http://127.0.0.1:4015'),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'case.json'
            for label, app, args, expected in cases:
                with self.subTest(label=label):
                    case = copy.deepcopy(base_case)
                    case['app'] = app
                    path.write_text(json.dumps(case))
                    plan = approval.execution_plan(
                        'crashlab_run', {'contract_path': str(path), **args})
                    self.assertEqual(plan['target'], expected)
                    self.assertEqual(plan['target'], core.effective_base_url(case, args.get('base_url')))

    def test_compare_target_uses_both_tool_overrides(self):
        case = json.loads(CASE.read_text())
        case['app'] = {'base_url': 'http://127.0.0.1:3999',
                       'start': {'base_url': 'http://127.0.0.1:4016'}}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'case.json'
            path.write_text(json.dumps(case))
            plan = approval.execution_plan('crashlab_compare', {
                'contract_path': str(path),
                'buggy_url': 'http://127.0.0.1:4017',
                'fixed_url': 'http://127.0.0.1:4018',
            })
        self.assertEqual(plan['target'], 'http://127.0.0.1:4017')
        self.assertEqual(plan['targets'], ['http://127.0.0.1:4017', 'http://127.0.0.1:4018'])

    def test_experiment_plan_uses_the_shared_target_and_reset_resolver(self):
        base_case = json.loads(CASE.read_text())
        cases = [
            ('managed fixed port without a URL',
             {'base_url': 'http://127.0.0.1:3999',
              'start': {'port': 4012, 'command': ['{PYTHON}', 'server.py'],
                        'cwd': '/tmp', 'base_url': None}},
             'http://127.0.0.1:4012'),
            ('managed URL wins over a conflicting app URL',
             {'base_url': 'http://127.0.0.1:3999',
              'start': {'port': 4012, 'command': ['{PYTHON}', 'server.py'],
                        'cwd': '/tmp', 'base_url': 'http://127.0.0.1:4013'}},
             'http://127.0.0.1:4013'),
        ]
        with tempfile.TemporaryDirectory() as temporary,              patch.dict('os.environ', {'CRASHLAB_DATA_DIR': temporary}):
            folder = Path(temporary) / 'experiments' / ('e' * 32)
            folder.mkdir(parents=True)
            (folder / 'experiment.json').write_text(json.dumps({
                'experiment_id': 'e' * 32,
                'project_path': str(Path(temporary) / 'project'),
                'intent': 'prepared experiment',
                'runs': [],
            }))
            for label, app, expected in cases:
                with self.subTest(label=label):
                    case = copy.deepcopy(base_case)
                    case['app'] = {**app, 'reset_url': '/reset'}
                    (folder / 'case.json').write_text(json.dumps(case))
                    snapshot = approval.capture_plan(
                        'crashlab_experiment_run', {'experiment_id': 'e' * 32})
                    plan = snapshot['plan']
                    self.assertEqual(plan['target'], expected)
                    self.assertEqual(plan['target'], core.effective_base_url(case))
                    self.assertEqual(plan['reset_url'], '/reset')

    def test_dashboard_shows_the_effective_contract_target(self):
        try:
            from dashboard import plugin_api as api
        except ImportError:
            self.skipTest('dashboard dependencies are not installed')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'case.json'
            case = json.loads(CASE.read_text())
            case['app'] = {'base_url': 'http://127.0.0.1:3999', 'reset_url': '/reset'}
            path.write_text(json.dumps(case))
            with patch.object(api, '_resolve_case', return_value=path):
                document = asyncio.run(api.create_approval(
                    api.ApprovalRequest(kind='run', contract_path=str(path))))
            plan = document.get('plan') or {}
            self.assertEqual(plan.get('target'), 'http://127.0.0.1:3999')
            self.assertEqual(plan.get('reset_url'), '/reset')

    def test_dashboard_captures_the_plan_before_asking_the_operator(self):
        source = Path('dashboard/dist/index.js').read_text(encoding='utf-8')
        self.assertEqual(source.count('window.confirm'), 1,
                         'every operator confirmation must show the captured plan')
        self.assertLess(source.index("api('/approval'"), source.index('window.confirm'))
        self.assertIn('planSummary(ticket.plan)', source)

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
