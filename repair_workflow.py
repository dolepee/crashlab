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
    if not isinstance(value, dict) or value.get('experiment_id') != experiment_id:
        raise ValueError('Invalid experiment metadata identity')
    if not isinstance(value.get('project_path'), str) or not Path(value['project_path']).is_absolute():
        raise ValueError('Invalid experiment project path')
    if not isinstance(value.get('intent'), str) or not isinstance(value.get('runs'), list):
        raise ValueError('Invalid experiment intent or run history')
    for item in value['runs']:
        if (not isinstance(item, dict) or not isinstance(item.get('project_sha256'), str)
                or not isinstance(item.get('workflow_status'), str) or not isinstance(item.get('result'), dict)):
            raise ValueError('Invalid experiment run record')
        result = item['result']
        for key in ('status', 'diagnosis', 'error', 'evidence_path'):
            if result.get(key) is not None and not isinstance(result[key], str):
                raise ValueError('Invalid experiment result label')
        for key in ('observed', 'expected'):
            if result.get(key) is not None and type(result[key]) not in (int, float):
                raise ValueError('Invalid experiment result count')
        control = result.get('control')
        if control is not None and not isinstance(control, dict):
            raise ValueError('Invalid experiment control record')
        if isinstance(control, dict):
            if type(control.get('ok')) is not bool:
                raise ValueError('Invalid experiment control verdict')
            for key in ('observed', 'expected'):
                if control.get(key) is not None and type(control[key]) not in (int, float):
                    raise ValueError('Invalid experiment control count')
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


def contract_schema():
    state = {'type': 'object', 'description': 'Prefer SQLite for independent observation. Scope primary and control to different marker values; the control runs in the same app/browser after the primary. Assert content and captured intent, not only counts.',
             'properties': {'mode': {'type':'string','enum':['sqlite','http']}, 'db': {'type':'string','description':'SQLite path; use {DATA_DIR}/app.sqlite and configure app.start to use that same disposable database.'}, 'table': {'type':'string'}, 'where': {'type':'string','description':'SQLite WHERE expression scoping this experiment to its test records.'}, 'expect': {'type':'integer','minimum':0}, 'record_fields': {'type':'object','description':'Exact database field values expected on every scoped record.'}, 'request_fields': {'type':'object','additionalProperties':{'type':'string'},'description':'Database column -> captured top-level JSON or URL-encoded form field. One row is required per distinct mapped signature. Include a stable per-intent key where available. If two legitimate identical control payloads have no distinct intent key, omit control request_fields and assert cardinality/content only.'}}, 'required':['mode','expect']}
    selector = {'type': 'string', 'minLength': 1}
    value_match = [
        {'required': ['empty'], 'properties': {'empty': {'const': True}}},
        {'required': ['equals']},
    ]
    condition = {
        'type': 'object',
        'properties': {
            'kind': {'type': 'string', 'enum': ['count', 'value']},
            'selector': selector,
            'min': {'type': 'integer', 'minimum': 0},
            'max': {'type': 'integer', 'minimum': 0},
            'text': {'type': 'string'},
            'empty': {'type': 'boolean'},
            'equals': {'type': 'string'},
        },
        'required': ['kind', 'selector'],
        'anyOf': [
            {'properties': {'kind': {'const': 'count'}}},
            {'properties': {'kind': {'const': 'value'}}, 'anyOf': value_match},
        ],
    }
    action_requirements = []
    for actions, required in [
        (['goto', 'reload', 'wait_for_fault', 'screenshot'], []),
        (['click', 'check', 'uncheck', 'wait_for_selector', 'text', 'wait_for_count'], ['selector']),
        (['fill', 'select_option'], ['selector', 'value']),
        (['press'], ['selector', 'key']),
        (['wait_for_text'], ['text']),
        (['wait_for_url'], ['pattern']),
        (['wait_for_any'], ['conditions']),
        (['wait_for_load_state'], ['state']),
        (['wait_for_timeout'], ['ms']),
        (['wait_for_value'], ['selector']),
    ]:
        branch = {'properties': {'action': {'enum': actions}}, 'required': ['action'] + required}
        if actions == ['wait_for_value']:
            branch['anyOf'] = value_match
        action_requirements.append(branch)
    journey = {
        'type': 'array', 'minItems': 1,
        'items': {
            'type': 'object',
            'properties': {
                'action': {'type': 'string'},
                'path': {'type': 'string'}, 'selector': selector,
                'value': {'type': 'string'}, 'text': {'type': 'string'},
                'pattern': {'type': 'string', 'minLength': 1},
                'as': {'type': 'string'}, 'key': {'type': 'string', 'minLength': 1},
                'min': {'type': 'integer', 'minimum': 0},
                'max': {'type': 'integer', 'minimum': 0},
                'empty': {'type': 'boolean'}, 'equals': {'type': 'string'},
                'conditions': {'type': 'array', 'minItems': 1, 'items': condition},
                'state': {'type': 'string', 'enum': ['load', 'domcontentloaded', 'networkidle']},
                'ms': {'type': 'integer', 'minimum': 0, 'maximum': 60000},
                'timeout': {'type': 'integer', 'minimum': 1},
            },
            'required': ['action'], 'anyOf': action_requirements,
        },
        'description': 'Declarative browser steps with action-specific arguments. Native HTML POST retry: submit, wait_for_fault, reload, wait_for_url. JSON fetch: submit, wait_for_fault, click retry. Prefer observable waits; no supplied JavaScript.',
    }
    return {'type':'object','description':'Construct from the inspected app and user intent. One suppressed response after commit, one retried intent, and two deliberate control operations. Review assertions before preparing.',
            'properties': {'id':{'type':'string'}, 'app':{'type':'object','properties':{'start':{'type':'object','properties':{'cwd':{'type':'string','description':'Absolute app directory inside project_path.'},'command':{'type':'array','items':{'type':'string'},'description':'App argv; placeholders {PYTHON}, {PORT}, {DATA_DIR} are expanded. Use shell explicitly only when required.'},'python':{'type':'string','description':'Installed application interpreter, not necessarily Hermes Python.'},'env':{'type':'object','additionalProperties':{'type':'string'}},'base_url':{'type':'string','description':'Normally http://127.0.0.1:{PORT}.'},'health_path':{'type':'string'}},'required':['cwd','command','base_url']}}}, 'journey':journey, 'fault':{'type':'object','properties':{'route':{'type':'string','description':'Playwright URL glob for the write endpoint, e.g. **/create.'},'method':{'type':'string','enum':['POST','PUT','PATCH','DELETE']},'mode':{'type':'string','enum':['suppress_after_upstream']},'occurrence':{'type':'integer','enum':[1]}},'required':['route','method','mode']}, 'state':state, 'control':{'type':'object','properties':{'journey':journey,'state':state},'required':['journey','state']}}, 'required':['id','app','journey','fault','state','control']}


def schemas():
    return [
        {'name': 'crashlab_prepare', 'description': 'Prepare a frozen repair experiment outside a local Git app. Inspect the app and construct the case from the user intent; no manual case file is needed. Requires auto-start, fault, state assertions and legitimate-use control.', 'parameters': {'type':'object','properties':{'project_path':{'type':'string'},'intent':{'type':'string'},'contract':contract_schema()},'required':['project_path','intent','contract'],'additionalProperties':False}},
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
