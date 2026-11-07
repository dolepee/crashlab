"""Persistent, project-scoped repair experiments for the Hermes tools."""
from __future__ import annotations
import fcntl
import hashlib
import json
import re
import subprocess
import uuid
from pathlib import Path
try:
    from . import crashlab_core as core
except ImportError:
    import crashlab_core as core


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def evaluator():
    return {name: hashlib.sha256((core.ROOT / name).read_bytes()).hexdigest()
            for name in ('crashlab_core.py', 'crashlab_runner.js', 'crashlab_probe.py', 'repair_workflow.py')}


def fingerprint(project):
    """Cover Git's tracked and nonignored untracked files, including deletions."""
    project = Path(project).resolve()
    top = subprocess.check_output(['git', '-C', str(project), 'rev-parse', '--show-toplevel'], text=True, timeout=15).strip()
    if Path(top).resolve() != project:
        raise ValueError('project_path must be the Git repository root')
    names = subprocess.check_output(['git', '-C', str(project), 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], timeout=15).split(b'\0')
    names = sorted(set(name.decode() for name in names if name))
    if len(names) > 5000:
        raise ValueError('Project exceeds 5000 files; automatic verification scope is unsupported')
    rows = {}; total = 0
    for name in names:
        path = project / name
        if path.is_symlink():
            raise ValueError(f'Symlink in verification scope is unsupported: {name}')
        if not path.exists():
            rows[name] = 'deleted'; continue
        if not path.is_file():
            raise ValueError(f'Unsupported project entry: {name}')
        total += path.stat().st_size
        if total > 100_000_000:
            raise ValueError('Project exceeds 100 MB verification scope')
        rows[name] = {'content': hashlib.sha256(path.read_bytes()).hexdigest(),
                      'mode': path.stat().st_mode & 0o777}
    return digest(rows)


def directory(experiment_id):
    if not re.fullmatch(r'[a-f0-9]{32}', experiment_id):
        raise ValueError('Invalid experiment_id')
    return core._data_dir() / 'experiments' / experiment_id


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def prepare(project_path, intent, contract):
    project = Path(project_path).expanduser().resolve()
    fingerprint(project)
    contract = core.validate_contract(contract)
    if not intent.strip():
        raise ValueError('Describe the intended user behavior')
    if not contract.get('control') or not contract.get('fault'):
        raise ValueError('Repair experiments require a fault and legitimate-use control')
    start = contract['app'].get('start') or {}
    raw_cwd = Path(start.get('cwd', '')).expanduser()
    if not raw_cwd.is_absolute():
        raise ValueError('app.start.cwd must be an absolute path')
    cwd = raw_cwd.resolve()
    if not start or not cwd.is_relative_to(project):
        raise ValueError('Use app.start with an explicit cwd inside the project')
    experiment_id = uuid.uuid4().hex
    folder = directory(experiment_id).resolve()
    if folder.is_relative_to(project):
        raise ValueError('CRASHLAB_DATA_DIR must be outside the editable project')
    folder.mkdir(parents=True)
    save(folder / 'case.json', contract)
    value = {'experiment_id': experiment_id, 'project_path': str(project), 'intent': intent,
             'contract_sha256': digest(contract), 'evaluator_sha256': evaluator(), 'runs': []}
    save(folder / 'experiment.json', value)
    return {**value, 'status': 'PREPARED', 'case': contract,
            'next_action': 'Inspect the intended behavior and assertions, then run crashlab_experiment_run. The first valid failing run establishes the baseline.'}


def load(experiment_id):
    folder = directory(experiment_id)
    value = json.loads((folder / 'experiment.json').read_text())
    case = core.load_contract(folder / 'case.json')
    if digest(case) != value['contract_sha256'] or evaluator() != value['evaluator_sha256']:
        raise ValueError('Frozen case or evaluator changed; prepare a new experiment')
    return folder, value, case


def status(experiment_id):
    _, value, _ = load(experiment_id)
    current = fingerprint(value['project_path'])
    runs = value['runs']
    if not runs:
        state = 'PREPARED'
    elif runs[-1]['project_sha256'] != current:
        state = 'STALE'
    else:
        state = runs[-1]['workflow_status']
    summaries = []
    for item in runs[-3:]:
        result = item['result']
        compact = {key: result.get(key) for key in
                   ('status', 'observed', 'expected', 'diagnosis', 'error', 'evidence_path')}
        control = result.get('control')
        if control is not None:
            compact['control'] = {key: control.get(key) for key in ('ok', 'observed', 'expected')}
        summaries.append({'workflow_status': item['workflow_status'], 'result': compact})
    return {'status': state, 'experiment_id': experiment_id, 'intent': value['intent'],
            'project_path': value['project_path'], 'contract_sha256': value['contract_sha256'],
            'history_path': str(directory(experiment_id) / 'experiment.json'),
            'run_count': len(runs), 'runs': summaries, 'current_project_sha256': current,
            'scope': 'Tracked and nonignored untracked project files. Ignored files, dependencies and runtime environment are outside this freshness check. Same-user hashes are not a security boundary.'}


def _run(experiment_id):
    folder, value, case = load(experiment_id)
    before = fingerprint(value['project_path'])
    result = core.run_and_persist(folder / 'case.json')
    after = fingerprint(value['project_path'])
    # Recheck identity after execution, before accepting evidence.
    load(experiment_id)
    valid_failure = core._completed_assertion(result, passed=False)
    valid_pass = core._completed_assertion(result, passed=True)
    baseline = next((r for r in value['runs'] if r['workflow_status'] == 'REPRODUCED'), None)
    if before != after:
        state = 'STALE'
    elif valid_failure:
        state = 'REPRODUCED'
    elif valid_pass and baseline and baseline['project_sha256'] != after:
        state = 'VERIFIED'
    elif valid_pass:
        state = 'PASS_WITHOUT_REPAIR_PROOF'
    else:
        state = result.get('status', 'ERROR')
    value['runs'].append({'project_sha256': after, 'workflow_status': state, 'result': result})
    save(folder / 'experiment.json', value)
    return status(experiment_id)


def schemas():
    return [
        {'name': 'crashlab_prepare', 'description': 'Prepare a frozen repair experiment outside a local Git app. Inspect the app and construct the case from the user intent; no manual case file is needed. Requires auto-start, fault, state assertions and legitimate-use control.', 'parameters': {'type':'object','properties':{'project_path':{'type':'string'},'intent':{'type':'string'},'contract':{'type':'object'}},'required':['project_path','intent','contract'],'additionalProperties':False}},
        *[{'name': name, 'description': description, 'parameters': {'type':'object','properties':{'experiment_id':{'type':'string'}},'required':['experiment_id'],'additionalProperties':False}} for name, description in [
            ('crashlab_experiment_run','Run the frozen case. Establish a failing baseline before repair; rerun after edits to verify the repair. A passing case alone is not repair proof.'),
            ('crashlab_experiment_status','Check whether experiment evidence is current for the app source. Edits make old evidence STALE; rerun before claiming verification.')]]]


def invoke(name, args):
    if name == 'crashlab_prepare': return prepare(**args)
    if name == 'crashlab_experiment_run': return run(**args)
    if name == 'crashlab_experiment_status': return status(**args)
    raise ValueError(name)


def run(experiment_id):
    # One test process owns an experiment; avoid overlapping app executions and lost history.
    with (directory(experiment_id) / '.run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Experiment is already running; check status before retrying')
        return _run(experiment_id)
