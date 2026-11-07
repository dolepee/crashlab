"""Opt-in, project-scoped Hermes completion reminders for repair experiments."""
from pathlib import Path
import json
import subprocess
import logging
try:
    from . import repair_workflow as workflow
except ImportError:
    import repair_workflow as workflow


def watch(experiment_id, enabled):
    workflow.directory(experiment_id)  # validate identity even when disabling
    root = workflow.core._data_dir() / 'watches'
    root.mkdir(parents=True, exist_ok=True)
    if not enabled:
        for path in root.glob('*.json'):
            try:
                if json.loads(path.read_text()).get('experiment_id') == experiment_id:
                    path.unlink()
            except (OSError, ValueError):
                continue
        return {'experiment_id': experiment_id, 'watching': False}
    _, experiment, _ = workflow.load(experiment_id)
    project = experiment['project_path']
    path = root / (workflow.digest(project) + '.json')
    if enabled:
        workflow.save(path, {'project_path': project, 'experiment_id': experiment_id})
    return {'experiment_id': experiment_id, 'watching': enabled,
            'scope': 'Completion reminder for edits with absolute paths in this project; Hermes caps continuation nudges. This is not an execution or security barrier.'}


def pre_verify(changed_paths=None, **kwargs):
    """No effects on other projects or on experiments the user has not selected."""
    paths = [Path(path).resolve() for path in (changed_paths or [])
             if isinstance(path, str) and Path(path).is_absolute()]
    if not paths:
        return None
    root = workflow.core._data_dir() / 'watches'
    for path in sorted(root.glob('*.json')):
        try:
            item = json.loads(path.read_text())
            project = Path(item['project_path']).resolve()
            if not project.is_absolute() or not any(p.is_relative_to(project) for p in paths):
                continue
            eid = item['experiment_id']
            try:
                state = workflow.status(eid)['status']
            except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                state = 'UNAVAILABLE'
            if state != 'VERIFIED':
                logging.getLogger('crashlab.completion').info(
                    'Completion reminder: experiment=%s session=%s status=%s',
                    eid, kwargs.get('session_id', ''), state)
                return {'action': 'continue', 'message': (
                    f'CrashLab experiment {eid} is {state} for an edited project. '
                    'Run crashlab_experiment_run and inspect the primary assertion and control '
                    'before reporting this repair verified. If blocked, report the limitation; '
                    'use crashlab_experiment_watch(enabled=false) to stop watching this experiment.')}
        except (OSError, ValueError, KeyError):
            # A malformed watch without a usable project cannot scope a reminder safely.
            continue
    return None


def schema():
    return {'name': 'crashlab_experiment_watch', 'description': 'Opt in or out of a project-scoped completion reminder for a selected repair experiment. After app edits Hermes requests current verification before finishing, within its nudge limit. Only absolute changed paths are supported.', 'parameters': {'type':'object','properties':{'experiment_id':{'type':'string'},'enabled':{'type':'boolean'}},'required':['experiment_id','enabled'],'additionalProperties':False}}
