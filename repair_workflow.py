"""Persistent, project-scoped repair experiments for the Hermes tools."""
from __future__ import annotations
import fcntl
import hashlib
import json
import re
import subprocess
import uuid
import copy
import os
import sys
import tempfile
from datetime import datetime, timezone
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


def load(experiment_id, *, historical=False):
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
    if digest(case) != value['contract_sha256'] or (not historical and evaluator() != value['evaluator_sha256']):
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
    value['runs'].append({'project_sha256': after, 'workflow_status': state, 'result': result,
                          'recorded_at': datetime.now(timezone.utc).isoformat(),
                          'source_review': source_review(value['project_path'])})
    save(folder / 'experiment.json', value)
    return status(experiment_id)


def source_review(project):
    """Bounded actual Git output, labeled relative to HEAD rather than invented repair attribution."""
    try:
        head = subprocess.check_output(['git','rev-parse','HEAD'],cwd=project,text=True,timeout=10).strip()
        with tempfile.TemporaryFile() as output:
            subprocess.run(['git','diff','--no-ext-diff','--no-textconv','--no-color','HEAD'],
                           cwd=project,stdout=output,stderr=subprocess.DEVNULL,check=True,timeout=15)
            output.seek(0);raw=output.read(65537)
        untracked = subprocess.check_output(['git','ls-files','--others','--exclude-standard'],cwd=project,text=True,timeout=10).splitlines()
        return {'git_head':head,'diff_against_head':raw[:65536].decode('utf-8',errors='replace'),
                'truncated':len(raw)>65536,'untracked_files':untracked,
                'basis':'Actual tracked-file diff against this run\'s Git HEAD; may include pre-existing edits. Untracked content is not included. This is not an attributed baseline-to-repair patch.'}
    except (OSError, subprocess.SubprocessError) as exc:
        return {'unavailable':str(exc)}


def report(experiment_id, run_index=None):
    _, value, case = load(experiment_id, historical=True)
    try:
        current = status(experiment_id)['status']
        current_error = None
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        current, current_error = 'UNAVAILABLE', str(exc)
    runs = value['runs']
    if run_index is None:
        run_index = len(runs)-1 if runs else None
    if run_index is not None and (type(run_index) is not int or not 0 <= run_index < len(runs)):
        raise ValueError('run_index is outside the recorded history')
    if run_index is not None:
        _validate_report_result(runs[run_index]['result'])
    return {'experiment_id':experiment_id,'intent':value['intent'],'current_status':current,
            'current_error':current_error,'historical_only':value['evaluator_sha256']!=evaluator(),
            'contract_sha256':value['contract_sha256'],'evaluator_sha256':value['evaluator_sha256'],
            'frozen_case':case,'replay':value.get('replay'),'run_index':run_index,
            'runs':[{'index':i,'workflow_status':r['workflow_status'],'recorded_at':r.get('recorded_at'),
                     'project_sha256':r['project_sha256']} for i,r in enumerate(runs)],
            'measurement':runs[run_index] if run_index is not None else None,
            'scope':'Recorded requests, observed records and process lifecycle only. Historical run status is separate from current project freshness; absent events are not reconstructed.'}


def _validate_report_result(result):
    if not isinstance(result,dict):raise ValueError('Invalid recorded result')
    for key in ('status','error','diagnosis'):
        if result.get(key) is not None and not isinstance(result[key],str):raise ValueError('Invalid recorded result label')
    recovery=result.get('recovery')
    if recovery is not None and (not isinstance(recovery,dict) or not isinstance(recovery.get('reason',''),str)):
        raise ValueError('Invalid recorded recovery')
    events=result.get('events',[])
    if not isinstance(events,list):raise ValueError('Invalid recorded events')
    for event in events:
        if (not isinstance(event,dict) or not isinstance(event.get('kind'),str)
                or not isinstance(event.get('phase'),str) or type(event.get('sequence')) is not int):
            raise ValueError('Invalid recorded event')
        if event.get('state') is not None and not isinstance(event['state'],dict):raise ValueError('Invalid event state')
        if event['kind']=='restart' and event.get('completed') and not isinstance(event.get('lifecycle'),dict):
            raise ValueError('Invalid restart lifecycle')
    cases=result.get('cases',[])
    if not isinstance(cases,list) or len(cases)>3:raise ValueError('Invalid recorded campaign cases')
    for item in cases:
        if not isinstance(item,dict) or not isinstance(item.get('name'),str):raise ValueError('Invalid recorded scenario label')
        child=item.get('result')
        if isinstance(child,dict) and child.get('cases'):raise ValueError('Nested recovery campaigns are unsupported')
        _validate_report_result(child)


def _runtime_fields(case, transform):
    """Only environment paths change; never substitute values inside assertions/journeys."""
    start=case['app']['start']
    for key in ('cwd','python','command','env'):
        if key in start:start[key]=transform(start[key])
    for state in (case['state'],case['control']['state']):
        if state.get('mode')=='sqlite':state['db']=transform(state['db'])
    if case.get('evidence',{}).get('screenshot_dir'):
        case['evidence']['screenshot_dir']=transform(case['evidence']['screenshot_dir'])
    return case


def _walk_strings(value, transform):
    if isinstance(value,str):return transform(value)
    if isinstance(value,list):return [_walk_strings(v,transform) for v in value]
    if isinstance(value,dict):return {k:_walk_strings(v,transform) for k,v in value.items()}
    return value


def export_experiment(experiment_id):
    _, value, case=load(experiment_id)
    portable=copy.deepcopy(case)
    project=value['project_path']
    relative=Path(portable['app']['start']['cwd']).expanduser().resolve().relative_to(Path(project))
    alias=Path(portable['app']['start']['cwd']).expanduser()
    for _ in relative.parts:alias=alias.parent
    python=portable['app']['start'].get('python')
    def token(value):
        if python and python!='{PYTHON}' and value==python:return '{APP_PYTHON}'
        for prefix in sorted({project,str(alias)},key=len,reverse=True):value=value.replace(prefix,'{PROJECT}')
        return value
    _runtime_fields(portable,lambda v:_walk_strings(v,token))
    portable['app']['start']['cwd']='{PROJECT}' + ('/'+relative.as_posix() if relative.parts else '')
    portable['app'].pop('base_url',None)
    portable['app']['start']['python']='{APP_PYTHON}'
    portable.setdefault('evidence',{})['screenshot_dir']='{DATA_DIR}/shots'
    launch=portable.pop('app')
    # Assertions retain their exact text, fields, counts and ordering. Only the
    # named runtime path positions above may contain binding placeholders.
    bundle={'format':'crashlab-regression.v1','intent':value['intent'],'behavior':portable,
            'launch':launch,'behavior_sha256':digest(portable),'launch_sha256':digest(launch),
            'evaluator_sha256':value['evaluator_sha256'],
            'binding_fields':['project_path','app_python'],
            'notes':'Behavior and launch template are frozen independently. Bind paths to a disposable checkout; dependencies and runtime remain your responsibility. This bundle contains declared setup/environment values and may contain application data. It does not transfer verified status or include a source repair.'}
    try:
        clean=not subprocess.check_output(['git','status','--porcelain'],cwd=core.ROOT,text=True,timeout=10).strip()
        bundle['source_pin']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=core.ROOT,text=True,timeout=10).strip() if clean else None
    except (OSError,subprocess.SubprocessError):bundle['source_pin']=None
    folder=core._data_dir()/'exports'/experiment_id;folder.mkdir(parents=True,exist_ok=True)
    path=folder/'regression.json';save(path,bundle)
    bindings=folder/'bindings.example.json';save(bindings,{'project_path':'/path/to/disposable-checkout','app_python':'/absolute/path/to/application-python'})
    return {'bundle_path':str(path),'bindings_example_path':str(bindings),
            'behavior_sha256':bundle['behavior_sha256'],'source_pin':bundle['source_pin'],
            'next_action':'Use the same evaluator release, import with project_path and app_python, then run the new experiment. A new checkout starts PREPARED; no prior verification is inherited.'}


def import_experiment(bundle_path, project_path, app_python):
    path=Path(bundle_path).expanduser().resolve()
    if path.stat().st_size>2_000_000:raise ValueError('Regression bundle exceeds 2 MB')
    bundle=json.loads(path.read_text())
    if not isinstance(bundle,dict) or bundle.get('format')!='crashlab-regression.v1':
        raise ValueError('Unsupported regression bundle format')
    if (not isinstance(bundle.get('intent'),str) or not bundle['intent'].strip()
            or not isinstance(bundle.get('behavior'),dict) or not isinstance(bundle.get('launch'),dict)):
        raise ValueError('Regression bundle needs an intent, behavior and launch object')
    if (digest(bundle.get('behavior'))!=bundle.get('behavior_sha256')
            or digest(bundle.get('launch'))!=bundle.get('launch_sha256')):
        raise ValueError('Frozen behavior or launch template changed')
    if bundle.get('evaluator_sha256')!=evaluator():
        raise ValueError('Evaluator differs from exported experiment; install the recorded source pin or explicitly prepare a new experiment')
    project=Path(project_path).expanduser().resolve();python=Path(app_python).expanduser()
    if not python.is_absolute() or not python.is_file() or not os.access(python,os.X_OK):
        raise ValueError('app_python must be an absolute executable application interpreter')
    case=copy.deepcopy(bundle['behavior']);case['app']=copy.deepcopy(bundle['launch'])
    bindings={'PROJECT':str(project),'APP_PYTHON':str(python)}
    _runtime_fields(case,lambda v:_walk_strings(v,lambda s:re.sub(r'\{(PROJECT|APP_PYTHON)\}',lambda m:bindings[m[1]],s)))
    prepared=prepare(str(project),bundle['intent'],case)
    folder,value,_=load(prepared['experiment_id'])
    value['replay']={'behavior_sha256':bundle['behavior_sha256'],'launch_sha256':bundle['launch_sha256'],
                     'bundle_path':str(path),'source_pin':bundle.get('source_pin'),
                     'bindings':{'project_path':str(project),'app_python':str(python)}}
    save(folder/'experiment.json',value)
    return {**prepared,'replay':value['replay']}


def contract_schema():
    state = {'type': 'object', 'description': 'Prefer SQLite for independent observation. Scope primary and control to different marker values; the control runs in the same app/browser after the primary. Assert content and captured intent, not only counts.',
             'properties': {'mode': {'type':'string','enum':['sqlite','http']}, 'db': {'type':'string','description':'SQLite path; use {DATA_DIR}/app.sqlite and configure app.start to use that same disposable database.'}, 'table': {'type':'string'}, 'where': {'type':'string','description':'SQLite WHERE expression scoping this experiment to its test records.'}, 'expect': {'type':'integer','minimum':0}, 'record_fields': {'type':'object','description':'Exact database field values expected on every scoped record.'}, 'request_fields': {'type':'object','additionalProperties':{'type':'string'},'description':'Database column -> captured top-level JSON or URL-encoded form field. One row is required per distinct mapped signature. Include a stable per-intent key where available. If two legitimate identical control payloads have no distinct intent key, omit control request_fields and assert cardinality/content only.'}}, 'required':['mode','expect']}
    state['properties'].update({
        'url': {'type': 'string', 'minLength': 1, 'description': 'HTTP observer path relative to the app base URL, e.g. /state. Its response is supplied by the app, not an independent observer.'},
        'path': {'type': 'string', 'minLength': 1, 'description': 'Dot-separated JSON path to the records array, e.g. data.orders; a numeric count is usable only without content/identity assertions.'},
    })
    state['properties']['db']['minLength'] = 1
    state['anyOf'] = [
        {'properties': {'mode': {'const': 'http'}}, 'required': ['url', 'path']},
        {'properties': {'mode': {'const': 'sqlite'}}, 'required': ['db']},
    ]
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
        (['goto', 'reload', 'restart_app', 'wait_for_fault', 'screenshot'], []),
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
            'properties': {'recovery_campaign': {'type':'array','minItems':1,'uniqueItems':True,'items':{'enum':['before_delivery','after_commit','restart_after_commit']},'description':'Repeat the same operation at each selected recovery boundary. Restart requires one explicit wait_for_fault marker, a managed app and a startup command that preserves an existing database.'}, 'recovery': {'type':'object','description':'Default retry requires a real repeated write with the same identity/payload after interruption. Reconcile requires a GET readback for the original intent and the observed durable record ID.','properties':{'mode':{'enum':['retry','reconcile']},**{key:{'type':'string','minLength':1} for key in ('route','initial_field','query_parameter','response_id_path','record_id_field')}},'required':['mode'],'anyOf':[{'properties':{'mode':{'const':'retry'}}},{'properties':{'mode':{'const':'reconcile'}},'required':['route','initial_field','query_parameter','response_id_path','record_id_field']}]}, 'id':{'type':'string'}, 'app':{'type':'object','properties':{'start':{'type':'object','properties':{'cwd':{'type':'string','description':'Absolute app directory inside project_path.'},'command':{'type':'array','minItems':1,'items':{'type':'string'},'description':'App argv; placeholders {PYTHON}, {PORT}, {DATA_DIR} are expanded. Use shell explicitly only when required.'},'python':{'type':'string','description':'Installed application interpreter, not necessarily Hermes Python.'},'env':{'type':'object','additionalProperties':{'type':'string'}},'base_url':{'type':'string','description':'Normally http://127.0.0.1:{PORT}.'},'health_path':{'type':'string'},'port':{'type':'integer','minimum':1,'maximum':65535},'ready_timeout':{'type':'number','exclusiveMinimum':0},'setup':{'type':'array','items':{'type':'object','properties':{'path':{'type':'string','minLength':1},'method':{'type':'string','enum':['GET','POST','PUT','PATCH','DELETE','HEAD']},'json':{'type':'object'},'body':{'type':'string'}},'required':['path']}}},'required':['cwd','command','base_url']}},'required':['start']}, 'journey':journey, 'fault':{'type':'object','properties':{'route':{'type':'string','description':'Playwright URL glob for the write endpoint, e.g. **/create.'},'method':{'type':'string','enum':['POST','PUT','PATCH','DELETE']},'mode':{'type':'string','enum':['suppress_after_upstream','prevent_before_delivery']},'occurrence':{'type':'integer','enum':[1]}},'required':['route','method','mode']}, 'state':state, 'control':{'type':'object','properties':{'journey':journey,'state':state},'required':['journey','state']}}, 'required':['id','app','journey','fault','state','control']}


def schemas():
    return [
        {'name':'crashlab_experiment_report','description':'Inspect recorded requests, durable records, recovery, restart and source diff for one measured run. Historical evidence is separate from current source status.','parameters':{'type':'object','properties':{'experiment_id':{'type':'string'},'run_index':{'type':'integer','minimum':0}},'required':['experiment_id'],'additionalProperties':False}},
        {'name':'crashlab_experiment_export','description':'Export frozen behavior and launch template separately from local path bindings. Does not export verified status or repair source.','parameters':{'type':'object','properties':{'experiment_id':{'type':'string'}},'required':['experiment_id'],'additionalProperties':False}},
        {'name':'crashlab_experiment_import','description':'Bind a saved regression bundle to a disposable checkout with the same evaluator. Creates a PREPARED experiment, requiring fresh baseline/repair measurements.','parameters':{'type':'object','properties':{'bundle_path':{'type':'string'},'project_path':{'type':'string'},'app_python':{'type':'string'}},'required':['bundle_path','project_path','app_python'],'additionalProperties':False}},
        {'name': 'crashlab_prepare', 'description': 'Prepare a frozen repair experiment outside a local Git app. Inspect the app and construct the case from the user intent; no manual case file is needed. Requires auto-start, fault, state assertions and legitimate-use control.', 'parameters': {'type':'object','properties':{'project_path':{'type':'string'},'intent':{'type':'string'},'contract':contract_schema()},'required':['project_path','intent','contract'],'additionalProperties':False}},
        *[{'name': name, 'description': description, 'parameters': {'type':'object','properties':{'experiment_id':{'type':'string'}},'required':['experiment_id'],'additionalProperties':False}} for name, description in [
            ('crashlab_experiment_run','Run the frozen case. Establish a failing baseline before repair; rerun after edits to verify the repair. A passing case alone is not repair proof.'),
            ('crashlab_experiment_status','Check whether experiment evidence is current for the app source. Edits make old evidence STALE; rerun before claiming verification.')]]]


def invoke(name, args):
    if name == 'crashlab_prepare': return prepare(**args)
    if name == 'crashlab_experiment_run': return run(**args)
    if name == 'crashlab_experiment_status': return status(**args)
    if name == 'crashlab_experiment_report': return report(**args)
    if name == 'crashlab_experiment_export': return export_experiment(**args)
    if name == 'crashlab_experiment_import': return import_experiment(**args)
    raise ValueError(name)


def run(experiment_id):
    # One test process owns an experiment; avoid overlapping app executions and lost history.
    with (directory(experiment_id) / '.run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Experiment is already running; check status before retrying')
        return _run(experiment_id)
