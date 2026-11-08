"""Replay frozen local-app behavior in a fresh session with explicit environment bindings."""
import argparse
import json
import sys
from pathlib import Path
import repair_workflow as workflow
import approval

def _operator_approval(experiment):
    """Run requires a real terminal confirmation; piped/non-interactive use fails closed."""
    if not sys.stdin.isatty():
        raise PermissionError('CrashLab run requires an interactive terminal approval')
    args = {'experiment_id': experiment}
    snapshot = approval.capture_plan('crashlab_experiment_run', args)
    print(json.dumps({'tool': 'crashlab_experiment_run', 'plan': snapshot['plan']}, indent=2), file=sys.stderr)
    answer = input('Approve this exact frozen execution plan? [y/N] ')
    if answer.strip().lower() not in ('y', 'yes'):
        raise PermissionError('CrashLab execution was not approved')
    approval.grant_for_operator('crashlab_experiment_run', args, session_id='cli-operator',
                                snapshot=snapshot)
    return snapshot


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='action',required=True)
    for action in ('run','report','export'):
        command=commands.add_parser(action);command.add_argument('--experiment',required=True)
        if action=='report':command.add_argument('--run-index',type=int)
    command=commands.add_parser('import');command.add_argument('--bundle',required=True);command.add_argument('--bindings',required=True,type=Path)
    args=parser.parse_args()
    try:
        if args.action=='import':
            bindings=json.loads(args.bindings.expanduser().read_text())
            result=workflow.import_experiment(args.bundle,bindings['project_path'],bindings['app_python'])
        elif args.action=='run':
            snapshot=_operator_approval(args.experiment)
            result=workflow.run(args.experiment, approval_context={'session_id':'cli-operator'},
                                approved=snapshot)
        elif args.action=='report':result=workflow.report(args.experiment,args.run_index)
        else:result=workflow.export_experiment(args.experiment)
        print(json.dumps(result,indent=2))
        if args.action=='run':
            if result['status'] in ('ERROR','INVALID','UNAVAILABLE'):return 2
            if result['status'] not in ('VERIFIED','PASS_WITHOUT_REPAIR_PROOF'):return 1
        return 0
    except (OSError,ValueError,KeyError) as exc:
        print(json.dumps({'status':'ERROR','error':str(exc)},indent=2));return 2

if __name__=='__main__':sys.exit(main())
