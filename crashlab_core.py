"""CrashLab contract runner core."""
from __future__ import annotations

import json
import copy
import hashlib
import math
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
RUNNER = ROOT / "crashlab_runner.js"
MAX_SAFE_INTEGER = 9_007_199_254_740_991
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,219}\Z")
SAFE_ARTIFACT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,239}\Z")
SAFE_SCREENSHOT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}\Z")
FIXED_RUN_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{3}Z\Z")


def valid_base(value):
    """Only loopback HTTP origins may be used as execution targets.

    Reject parser-differential spellings explicitly: a backslash, percent
    escape, whitespace, userinfo or control character can make one URL parser
    see a local host while a browser resolves another authority.
    """
    if not isinstance(value, str) or not value or any(ch in value for ch in ("\\", "%", "@")) or re.search(r"[\x00-\x20\x7f]", value):
        raise ValueError("prototype targets must be loopback HTTP origins without credentials")
    parsed = urllib.parse.urlparse(re.sub(r"\{[A-Z_]+\}", "1", value))
    try:
        port_ok = parsed.port is None or 1 <= parsed.port <= 65535
    except ValueError:
        port_ok = False
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1", "[::1]")
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/") or not port_ok
            or "\\" in parsed.netloc or "%" in parsed.netloc or "@" in parsed.netloc):
        raise ValueError("prototype targets must be loopback HTTP origins without credentials")
    return value.rstrip("/")


def _runner_env():
    """The runner gets only the small environment it needs; never the agent's secrets."""
    keys = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "SYSTEMROOT", "PATHEXT",
            "TMPDIR", "TEMP", "TMP", "CHROME_PATH", "CRASHLAB_RETRY_FETCH_TIMEOUT_MS")
    env = {key: os.environ[key] for key in keys if key in os.environ}
    env.setdefault("HOME", str(Path.home()))
    env["PYTHON_BINARY"] = sys.executable
    return env


def _safe_id(value: Any, field: str = "id") -> str:
    """Reject names that can escape a persisted artifact path or filename."""
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise ValueError(f"{field} must be a safe identifier: letters, digits, dot, dash or underscore")
    return value


def _safe_run_id(value: Any) -> str:
    if not isinstance(value, str) or not SAFE_RUN_ID.fullmatch(value):
        raise ValueError("run_id must be a safe identifier within the generated run budget")
    return value


def _artifact_path(directory: Path, name: str) -> Path:
    """Resolve a persisted artifact name and keep it directly inside directory."""
    if not isinstance(name, str) or not SAFE_ARTIFACT.fullmatch(name):
        raise ValueError("artifact name must fit the bounded filename budget")
    root = directory.resolve()
    path = (root / name).resolve()
    if path.parent != root:
        raise ValueError("artifact path escapes its configured directory")
    return path


def _campaign_case_id(base: str, boundary: str) -> str:
    """Derive a campaign case ID without exceeding the case-ID budget."""
    _safe_id(base, "contract.id")
    _safe_id(boundary, "recovery_campaign boundary")
    suffix = "-" + boundary
    if len(base) + len(suffix) <= 128:
        return base + suffix
    if len(suffix) > 96:
        raise ValueError("recovery_campaign boundary name is too long for a case ID")
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:8]
    prefix = base[:128 - len(suffix) - 9]
    return f"{prefix}-{digest}{suffix}"


def _data_dir() -> Path:
    return Path(os.environ.get("CRASHLAB_DATA_DIR", Path.home() / ".local" / "share" / "crashlab"))


def explain_run(result: dict) -> str:
    """Turn the measured result into the sentence a person needs next."""
    if result.get("error"):
        return f"Run failed before a verdict: {result['error']}"
    scenario = result.get("scenario") or {}
    if scenario.get("valid") is False:
        return scenario.get("invalid_reason") or (
            "The configured fault boundary was not established, so this run is not evidence.")
    if (result.get('recovery') or {}).get('ok') is False:
        return 'Recovery was not established: ' + result['recovery'].get('reason', 'Inspect the captured requests.')
    observed, expected = result.get("observed"), result.get("expected")
    if not isinstance(observed, (int, float)) or not isinstance(expected, (int, float)):
        return "No persisted state was measured."
    if observed > expected:
        return f"Duplicate write: one intent produced {observed:g} persisted records, expected {expected:g}."
    if observed < expected:
        return f"Missing write: observed {observed:g} persisted records, expected {expected:g}."
    control = result.get("control")
    if control is not None and not control.get("ok"):
        return "The primary state matched, but the legitimate-use control failed; the change may block normal use."
    if result.get("ok"):
        return (f"Persisted state is correct ({observed:g}/{expected:g})"
                + (" and the control passed." if control is not None
                   else "; no control was configured."))
    return "Record count matched, but the configured content or intent assertions did not pass."


def explain_comparison(result: dict) -> str:
    buggy, fixed = result.get("buggy") or {}, result.get("fixed") or {}
    if result.get("ok"):
        return "Buggy build reproduced the defect; repaired build passed with the same contract."
    if buggy.get("ok") and fixed.get("ok"):
        return "Both builds passed; this is not a reproduction."
    if not buggy.get("ok") and not fixed.get("ok"):
        return "Both builds failed; inspect the scenario and control before treating this as repair evidence."
    return "The comparison is incomplete; inspect the buggy and fixed runs."


def persist_run(result: dict) -> dict:
    """Save one completed run outside the plugin directory for the Desktop pane."""
    run_id = result.get("run_id") or f"run-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
    run_id = _safe_run_id(run_id)
    result["run_id"] = run_id
    data = _data_dir()
    runs = data / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    result["diagnosis"] = explain_run(result)
    path = _artifact_path(runs, f"{run_id}.json")
    result["evidence_path"] = str(path)
    raw = json.dumps(result, indent=2, default=str) + "\n"
    path.write_text(raw, encoding="utf-8")
    (data / "latest-run.json").write_text(raw, encoding="utf-8")
    result["evidence_path"] = str(path)
    return result


def _persist(result: dict) -> dict:
    """Save a completed comparison outside the plugin directory and expose its id."""
    run_id = result.get("run_id") or f"compare-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
    run_id = _safe_run_id(run_id)
    result["run_id"] = run_id
    data = _data_dir()
    runs = data / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    result["diagnosis"] = explain_comparison(result)
    path = _artifact_path(runs, f"{run_id}.json")
    result["evidence_path"] = str(path)
    path.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    (data / "latest.json").write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    result["evidence_path"] = str(path)
    return result


def latest_run() -> dict | None:
    path = _data_dir() / "latest-run.json"
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def list_runs(limit: int = 10) -> list[dict]:
    directory = _data_dir() / "runs"
    if not directory.is_dir():
        return []
    rows = []
    for path in sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict):
            rows.append({key: value.get(key) for key in
                         ("run_id", "contract", "ok", "status", "error", "observed", "expected", "diagnosis", "evidence_path")})
    return rows


def load_contract(path: str | Path) -> dict:
    contract = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    return validate_contract(contract)


def valid_relative_path(value: object, label: str = "path") -> str:
    """A path appended to an approved origin must remain an absolute path there."""
    if (not isinstance(value, str) or not value.startswith("/") or value.startswith("//")
            or any(ch in value for ch in ("\\", "%", "@"))
            or re.search(r"[\x00-\x20\x7f]", value)):
        raise ValueError(f"{label} must be an absolute local path")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme or parsed.netloc:
        raise ValueError(f"{label} must be an absolute local path")
    return value


def valid_state_url(value: object) -> str:
    """State probes are paths; accept an absolute loopback URL only by normalising it."""
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        if any(ch in value for ch in ("\\", "%", "@")) or re.search(r"[\x00-\x20\x7f]", value):
            raise ValueError("state.url must stay on the loopback origin")
        parsed = urllib.parse.urlsplit(value)
        if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1", "[::1]")
                or parsed.username or parsed.password):
            raise ValueError("state.url must stay on the loopback origin")
        path = parsed.path or "/"
        return path + (("?" + parsed.query) if parsed.query else "") + (("#" + parsed.fragment) if parsed.fragment else "")
    return valid_relative_path(value, "state.url")


def resolved_loopback_url(base: str, path: object, label: str = "path") -> str:
    """Join a relative path to a loopback origin and verify the final host."""
    base = valid_base(base)
    path = valid_relative_path(path, label)
    resolved = base.rstrip("/") + path
    parsed = urllib.parse.urlsplit(resolved)
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1", "[::1]")
            or parsed.username or parsed.password):
        raise ValueError(f"{label} resolves outside the loopback origin")
    return resolved


def _validate_state(state: dict, where: str) -> None:
    if not isinstance(state, dict):
        raise ValueError(f"{where} must be an object")
    expected = state.get("expect")
    if type(expected) is not int or expected < 0:
        raise ValueError(f"{where}.expect must be a non-negative integer")
    for field in ("record_fields", "request_fields"):
        if field in state:
            mapping = state[field]
            if not isinstance(mapping, dict) or not mapping:
                raise ValueError(f"{where}.{field} must be a non-empty object")
            for key, value in mapping.items():
                if not isinstance(key, str) or not key:
                    raise ValueError(f"{where}.{field} needs non-empty column names")
                if field == "request_fields" and (not isinstance(value, str) or not value):
                    raise ValueError(f"{where}.{field} needs request field names")
                if field == "record_fields" and (value is None or type(value) not in (str, int, float, bool)):
                    raise ValueError(f"{where}.{field} values must be non-null scalars")
    mode = state.get("mode", "http")
    if mode not in ("http", "sqlite"):
        raise ValueError(f"{where}.mode must be http or sqlite")
    if mode == "http":
        if not isinstance(state.get("url"), str) or not state["url"]:
            raise ValueError(f"{where}.url is required")
        state["url"] = valid_state_url(state["url"])
        if not isinstance(state.get("path"), str) or not state["path"]:
            raise ValueError(f"{where}.path is required")
    else:
        if not isinstance(state.get("db"), str) or not state["db"]:
            raise ValueError(f"{where}.db is required for sqlite mode")
        table = state.get("table", "orders")
        if not isinstance(table, str) or not table.replace("_", "").isalnum():
            raise ValueError(f"{where}.table is invalid")
        if "where" in state and not isinstance(state["where"], str):
            raise ValueError(f"{where}.where must be a string")


def _validate_journey(journey: list, where: str) -> None:
    if not isinstance(journey, list) or not journey:
        raise ValueError(f"{where} must be a non-empty list")
    for i, step in enumerate(journey):
        name = f"{where}[{i}]"
        if not isinstance(step, dict):
            raise ValueError(f"{name} must be an object")
        action = step.get("action")
        if action not in ("goto", "click", "fill", "check", "uncheck", "select_option",
                         "press", "wait_for_selector", "wait_for_text", "wait_for_url",
                         "wait_for_fault", "wait_for_count", "wait_for_value", "wait_for_any",
                         "wait_for_load_state", "wait_for_timeout",
                         "restart_app", "reload", "text", "screenshot"):
            raise ValueError(f"{name}.action is unsupported")
        if action in ("click", "fill", "check", "uncheck", "select_option", "press",
                      "wait_for_selector", "text"):
            if not isinstance(step.get("selector"), str) or not step["selector"]:
                raise ValueError(f"{name}.selector is required")
        if action in ("fill", "select_option"):
            if not isinstance(step.get("value"), str):
                raise ValueError(f"{name}.value must be a string")
        if action == "press" and not isinstance(step.get("key"), str):
            raise ValueError(f"{name}.key must be a string")
        if action == "wait_for_text" and not isinstance(step.get("text"), str):
            raise ValueError(f"{name}.text is required")
        if action == "wait_for_url" and not isinstance(step.get("pattern"), str):
            raise ValueError(f"{name}.pattern must be a string")
        if action in ("wait_for_count", "wait_for_value"):
            if not isinstance(step.get("selector"), str) or not step["selector"]:
                raise ValueError(f"{name}.selector is required")
            if action == "wait_for_count":
                if "min" in step and (type(step["min"]) is not int or step["min"] < 0):
                    raise ValueError(f"{name}.min must be a non-negative integer")
                if "max" in step and (type(step["max"]) is not int or step["max"] < 0):
                    raise ValueError(f"{name}.max must be a non-negative integer")
                if "text" in step and not isinstance(step["text"], str):
                    raise ValueError(f"{name}.text must be a string")
                if step.get('max', float('inf')) < step.get('min', 0):
                    raise ValueError(f"{name}.max must be at least min")
            else:
                if "empty" in step and not isinstance(step["empty"], bool):
                    raise ValueError(f"{name}.empty must be a boolean")
                if "equals" in step and not isinstance(step["equals"], str):
                    raise ValueError(f"{name}.equals must be a string")
        if action == "wait_for_any":
            conditions = step.get("conditions")
            if not isinstance(conditions, list) or not conditions:
                raise ValueError(f"{name}.conditions must be a non-empty list")
            for condition in conditions:
                if not isinstance(condition, dict) or condition.get("kind") not in ("count", "value"):
                    raise ValueError(f"{name}.conditions must contain count/value objects")
                if not isinstance(condition.get("selector"), str) or not condition["selector"]:
                    raise ValueError(f"{name}.conditions selector is required")
                _validate_journey([{**condition, 'action': 'wait_for_' + condition['kind']}], name + '.conditions')
        if action == "wait_for_load_state" and step.get("state") not in ("load", "domcontentloaded", "networkidle"):
            raise ValueError(f"{name}.state must be load, domcontentloaded or networkidle")
        if action == "wait_for_timeout":
            ms = step.get("ms")
            if type(ms) is not int or not 0 <= ms <= 60000:
                raise ValueError(f"{name}.ms must be an integer from 0 to 60000")
        if action == "screenshot" and "as" in step:
            if not isinstance(step["as"], str) or not SAFE_SCREENSHOT.fullmatch(step["as"]):
                raise ValueError(f"{name}.as must be a bounded screenshot slug")
        if action == "goto" and step.get("path") is not None:
            if not isinstance(step["path"], str):
                raise ValueError(f"{name}.path must be a string")
            valid_relative_path(step["path"], f"{name}.path")


def _reject_unsafe_integers(value: Any, where: str = "value") -> None:
    """Reject integer identities before JavaScript can round them.

    Decimal strings remain valid inputs. This is intentionally a rejection,
    not a lossless bigint adapter: an unsafe JSON number is ambiguous once it
    has been parsed by JavaScript, so it must never become a comparison input.
    """
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise ValueError(f"{where} is outside the safe integer range; use a decimal string")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{where} must be a finite number")
        if value.is_integer() and abs(value) > MAX_SAFE_INTEGER:
            raise ValueError(f"{where} is outside the safe integer range; use a decimal string")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            _reject_unsafe_integers(child, f"{where}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_unsafe_integers(child, f"{where}[{index}]")


def validate_contract(contract: dict) -> dict:
    if not isinstance(contract, dict):
        raise ValueError("contract must be an object")
    for key in ("id", "app", "journey", "state"):
        if key not in contract:
            raise ValueError(f"contract missing {key}")
    _safe_id(contract["id"], "contract.id")
    if not isinstance(contract["app"], dict):
        raise ValueError("app must be an object")
    if "setup_required" in contract and not isinstance(contract["setup_required"], str):
        raise ValueError("setup_required must be a setup instruction string")
    base = contract["app"].get("base_url")
    start = contract["app"].get("start")
    if base is None and start is None:
        raise ValueError("app.base_url or app.start is required")
    if base is not None:
        if not isinstance(base, str) or not base.startswith(("http://", "https://")):
            raise ValueError("app.base_url must be an http(s) URL")
        valid_base(base)
    if start is not None and not isinstance(start, dict):
        raise ValueError("app.start must be an object")
    _validate_journey(contract["journey"], "journey")
    _validate_state(contract["state"], "state")
    fault = contract.get("fault")
    if fault is not None:
        if not isinstance(fault, dict):
            raise ValueError("fault must be an object")
        if fault.get("mode") not in ('suppress_after_upstream', 'prevent_before_delivery'):
            raise ValueError("fault.mode must be suppress_after_upstream or prevent_before_delivery")
        if not isinstance(fault.get("route"), str) or not fault["route"]:
            raise ValueError("fault.route is required")
        if fault.get("method") is not None and fault["method"] not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            raise ValueError("fault.method must be an HTTP method")
        if fault.get("occurrence", 1) != 1:
            raise ValueError("only fault.occurrence=1 is supported")
    recovery = contract.get('recovery')
    if fault is not None and recovery is None:
        raise ValueError('fault cases require recovery.success_statuses so success is explicit')
    if recovery is not None:
        if not fault or not isinstance(recovery, dict) or recovery.get('mode') not in ('retry', 'reconcile'):
            raise ValueError('recovery requires a fault and mode retry or reconcile')
        allowed = {'mode', 'route', 'initial_field', 'query_parameter', 'response_id_path',
                   'record_id_field', 'success_statuses', 'identity_headers'}
        unknown = sorted(set(recovery) - allowed)
        if unknown:
            raise ValueError(f"recovery has unsupported fields: {', '.join(unknown)}")
        statuses = recovery.get('success_statuses')
        if not isinstance(statuses, list) or not statuses:
            raise ValueError('recovery.success_statuses must be a non-empty list')
        if any(type(status) is not int or not 200 <= status <= 299 or status == 202 for status in statuses):
            raise ValueError('recovery.success_statuses must be terminal 2xx statuses, excluding 202')
        if len(set(statuses)) != len(statuses):
            raise ValueError('recovery.success_statuses must not repeat')
        headers = recovery.get('identity_headers')
        if 'identity_headers' in recovery and headers is not None and not isinstance(headers, list):
            raise ValueError('recovery.identity_headers must be a non-empty list')
        if headers is not None:
            if not isinstance(headers, list) or not headers:
                raise ValueError('recovery.identity_headers must be a non-empty list')
            if any(not isinstance(name, str) or not name or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name)
                   for name in headers):
                raise ValueError('recovery.identity_headers must contain valid HTTP header names')
            if len({name.lower() for name in headers}) != len(headers):
                raise ValueError('recovery.identity_headers must not repeat names')
        if recovery['mode'] == 'reconcile':
            for field in ('route', 'initial_field', 'query_parameter', 'response_id_path', 'record_id_field'):
                if not isinstance(recovery.get(field), str) or not recovery[field]:
                    raise ValueError(f'recovery.{field} is required for reconciliation')
            if recovery['route'] == fault['route']:
                raise ValueError('recovery.route must differ from the interrupted write route')
            if headers is not None:
                raise ValueError('recovery.identity_headers is only supported for retry recovery')
        else:
            response_path = recovery.get('response_id_path')
            record_field = recovery.get('record_id_field')
            if ('response_id_path' in recovery and not isinstance(response_path, str)) or (
                    'record_id_field' in recovery and not isinstance(record_field, str)):
                raise ValueError('recovery.response_id_path and recovery.record_id_field must be non-empty strings')
            if (response_path is None) != (record_field is None):
                raise ValueError('recovery.response_id_path and recovery.record_id_field must be provided together')
            for field, value in (('response_id_path', response_path), ('record_id_field', record_field)):
                if value is not None and (not isinstance(value, str) or not value):
                    raise ValueError(f'recovery.{field} must be a non-empty string')
    campaign = contract.get('recovery_campaign')
    if campaign is not None:
        if not contract.get('control'):
            raise ValueError('Recovery campaigns require a legitimate-use control')
        allowed = {'before_delivery', 'after_commit', 'restart_after_commit'}
        if (not fault or not isinstance(campaign, list) or not campaign
                or any(not isinstance(name, str) or name not in allowed for name in campaign)
                or len(set(campaign)) != len(campaign)):
            raise ValueError('recovery_campaign must contain unique before_delivery, after_commit or restart_after_commit scenarios')
        if recovery and recovery['mode'] != 'retry':
            raise ValueError('Recovery campaigns require retry recovery; run reconciliation as a separate post-commit case')
        if 'restart_after_commit' in campaign and sum(s['action']=='wait_for_fault' for s in contract['journey']) != 1:
            raise ValueError('Restart campaign needs one wait_for_fault recovery boundary in journey')
        if 'restart_after_commit' in campaign and any(s['action']=='restart_app' for s in contract['journey']):
            raise ValueError('Restart campaign inserts restart_app; do not also add a manual restart')
    if any(s['action']=='restart_app' for s in contract['journey']) or campaign and 'restart_after_commit' in campaign:
        if not start:
            raise ValueError('restart_app requires a managed app.start')
    if sum(s['action']=='restart_app' for s in contract['journey']) > 1:
        raise ValueError('Only one app restart per scenario is supported')
    control = contract.get("control")
    if control is not None:
        if not isinstance(control, dict):
            raise ValueError("control must be an object")
        _validate_journey(control.get("journey"), "control.journey")
        _validate_state(control.get("state"), "control.state")
        control_state = control['state']
        mapping = control_state.get('request_fields') or {}
        fields = control_state.get('record_fields') or {}
        if control_state['expect'] > 1 and mapping and set(mapping) <= set(fields):
            raise ValueError('Control request_fields collapse every record to one fixed identity signature. Include a per-intent key, or omit control request_fields and assert counts/content only.')
    specs = [contract["state"]] + ([control["state"]] if control else [])
    if not fault and any(spec.get("request_fields") for spec in specs):
        raise ValueError("request_fields requires a fault route for request capture")
    _reject_unsafe_integers(contract, 'contract')
    return contract


def _node() -> str:
    node = os.environ.get("NODE_BINARY") or "node"
    return node


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _expand_start(value: Any, *, contract_dir: Path, data: Path, port: int, python: str | None = None) -> Any:
    """Expand the small, documented local-app placeholders."""
    if isinstance(value, str):
        return (value.replace("{APP_DIR}", str(contract_dir))
                .replace("{ROOT}", str(ROOT))
                .replace("{DATA_DIR}", str(data))
                .replace("{PORT}", str(port))
                .replace("{PYTHON}", python or sys.executable)
                .replace("{HOME}", str(data / "home")))
    if isinstance(value, list):
        return [_expand_start(item, contract_dir=contract_dir, data=data, port=port, python=python) for item in value]
    if isinstance(value, dict):
        return {key: _expand_start(item, contract_dir=contract_dir, data=data, port=port, python=python)
                for key, item in value.items()}
    return value


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _wait_group_gone(pgid: int, timeout: float = 5.0, proc: subprocess.Popen | None = None) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None:
            # Reap the group leader while polling: a zombie leader keeps the
            # process group visible and made ordinary cleanup wait seconds.
            proc.poll()
        if not _group_alive(pgid):
            return True
        time.sleep(0.05)
    if proc is not None:
        proc.poll()
    return not _group_alive(pgid)


def _stop_process(proc: subprocess.Popen) -> None:
    """Stop the app's whole process group, including children that outlive it.

    The group id is captured when the app starts, so cleanup still works when
    the group leader exits first. A child that ignores SIGTERM is escalated to
    SIGKILL for the whole group.
    """
    pgid = getattr(proc, "_crashlab_pgid", None)
    if pgid is None and os.name == "posix":
        if proc.poll() is None:
            try:
                pgid = os.getpgid(proc.pid)
            except ProcessLookupError:
                pgid = None
    if pgid is None:
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        return
    if os.name != "posix":
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    if _wait_group_gone(pgid, proc=proc):
        if proc.poll() is None:
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    _wait_group_gone(pgid, proc=proc)
    if proc.poll() is None:
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


def effective_base_url(contract: dict, override: str | None = None, *,
                        contract_dir: Path | None = None, data: Path | None = None,
                        port: int | None = None) -> str | None:
    """The URL execution will actually use for a contract.

    Precedence is the one ``run_contract`` and ``_start_app`` implement:
    an explicit tool override, then a managed ``app.start`` target, then an
    unmanaged external ``app.base_url``. A managed start with a fixed port and
    no URL uses the loopback URL on that port; a dynamic port is unknowable
    until the process starts, so it is not represented as a stale contract URL.
    """
    app = contract.get("app") if isinstance(contract, dict) else None
    app = app if isinstance(app, dict) else {}
    start = app.get("start")
    start = start if isinstance(start, dict) else None
    value = None
    if override:
        value = str(override)
    elif start is not None:
        value = start.get("base_url")
        if not value and port is not None:
            value = f"http://127.0.0.1:{port}"
        elif not value and type(start.get("port")) is int:
            value = f"http://127.0.0.1:{start['port']}"
    else:
        value = app.get("base_url")
    if value is None:
        return None
    value = str(value)
    if contract_dir is not None and data is not None and port is not None:
        value = str(_expand_start(value, contract_dir=contract_dir, data=data, port=port))
    return value


def valid_reset_url(value: object) -> str:
    """A reset target must stay an absolute path on the approved origin."""
    return valid_relative_path(value, "reset_url")


def effective_reset_url(contract: dict) -> str | None:
    """The reset endpoint the browser runner will call, if the contract has one."""
    app = contract.get("app") if isinstance(contract, dict) else None
    app = app if isinstance(app, dict) else {}
    value = app.get("reset_url")
    return valid_reset_url(value) if value else None


def _start_app(contract: dict, contract_dir: Path, *, data_dir: Path | None = None,
               port_override: int | None = None, initialize: bool = True) -> tuple[subprocess.Popen, str, Path, Any, Path]:
    app = contract["app"]
    start = app.get("start")
    if not isinstance(start, dict):
        raise ValueError("app.start must be an object")
    command = start.get("command")
    if isinstance(command, str):
        command = shlex.split(command)
    if not isinstance(command, list) or not all(isinstance(item, str) and item for item in command):
        raise ValueError("app.start.command must be a non-empty string list")
    app_python = str(start.get("python") or sys.executable)
    if app_python == "{PYTHON}":
        app_python = sys.executable
    port = port_override if port_override is not None else start.get("port")
    if port is None:
        port = _free_port()
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("app.start.port must be an integer port")
    data = data_dir if data_dir is not None else Path(tempfile.mkdtemp(prefix="crashlab-app-"))
    command = [_expand_start(item, contract_dir=contract_dir, data=data, port=port, python=app_python)
               for item in command]
    cwd = Path(_expand_start(start.get("cwd", "."), contract_dir=contract_dir, data=data, port=port)).expanduser()
    raw_start_base = start.get("base_url")
    if raw_start_base and "{" not in str(raw_start_base):
        valid_base(str(raw_start_base))
    # Local apps get a small, explicit environment. In particular, do not pass
    # the agent's provider/API credentials through to app.start commands.
    env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TZ",
                                            "SYSTEMROOT", "PATHEXT")
           if key in os.environ}
    env.update({"PYTHONUNBUFFERED": "1", "PORT": str(port),
                "HOME": str(data / "home")})
    for key, value in (start.get("env") or {}).items():
        if not isinstance(key, str) or not key:
            raise ValueError("app.start.env keys must be non-empty strings")
        env[key] = str(_expand_start(value, contract_dir=contract_dir, data=data, port=port))
    (data / "home").mkdir(parents=True, exist_ok=True)
    log = data / "app.log"
    log_handle = log.open("a" if data_dir is not None else "w", encoding="utf-8")
    proc = subprocess.Popen(
        command,
        cwd=cwd, env=env, stdout=log_handle, stderr=subprocess.STDOUT,
        start_new_session=(os.name == "posix"),
    )
    if os.name == "posix":
        # start_new_session makes the process group id equal to the child pid.
        proc._crashlab_pgid = proc.pid
    proc._crashlab_port = port
    try:
        base = effective_base_url(
            contract, contract_dir=contract_dir, data=data, port=port)
        if base is None:
            raise ValueError("app.start has no usable base URL")
        base = valid_base(base)
        health = str(start.get("health_path", "/"))
        health_url = resolved_loopback_url(base, health, "app.start.health_path")
        timeout_s = float(start.get("ready_timeout", 30))
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                text = log.read_text(encoding="utf-8", errors="replace")
                raise RuntimeError(f"app exited before readiness ({proc.returncode}): {text[-2000:]}")
            try:
                with urllib.request.urlopen(health_url, timeout=0.5) as response:
                    if response.status < 500:
                        for step in start.get("setup", []) if initialize else []:
                            if not isinstance(step, dict) or not isinstance(step.get("path"), str):
                                raise ValueError("app.start.setup steps need a path")
                            valid_relative_path(step["path"], "app.start.setup.path")
                            payload = None
                            headers = {}
                            if "json" in step:
                                payload = json.dumps(step["json"]).encode("utf-8")
                                headers["Content-Type"] = "application/json"
                            elif "body" in step:
                                payload = str(step["body"]).encode("utf-8")
                            setup_url = resolved_loopback_url(base, step["path"], "app.start.setup.path")
                            request = urllib.request.Request(
                                setup_url, data=payload,
                                headers=headers, method=str(step.get("method", "POST")),
                            )
                            with urllib.request.urlopen(request, timeout=5) as setup:
                                if not 200 <= setup.status < 300:
                                    raise RuntimeError(f"app setup returned {setup.status}")
                        return proc, base, log, log_handle, data
            except (urllib.error.URLError, TimeoutError, OSError):
                time.sleep(0.1)
        raise RuntimeError(f"app did not become ready within {timeout_s}s: {log.read_text(encoding='utf-8', errors='replace')[-2000:]}")
    except BaseException:
        _stop_process(proc)
        log_handle.close()
        raise


def _run_with_restart(argv, env, timeout, restart):
    """One bounded file-IPC request; app receives no control path or credentials."""
    with tempfile.TemporaryDirectory(prefix='crashlab-control-') as temporary:
        directory = Path(temporary)
        runner = subprocess.Popen(argv, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, env={**env, 'CRASHLAB_CONTROL_DIR': temporary},
                                  start_new_session=(os.name == 'posix'))
        if os.name == 'posix':
            runner._crashlab_pgid = runner.pid
        deadline = time.monotonic() + timeout
        handled = False
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, timeout)
                try:
                    stdout, stderr = runner.communicate(timeout=min(.1, remaining))
                    return subprocess.CompletedProcess(argv, runner.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    request = directory / 'restart.request.json'
                    if request.exists() and not handled:
                        handled = True
                        try:
                            if json.loads(request.read_text()) != {'action': 'restart'}:
                                raise ValueError('Invalid restart request')
                            reply = {'ok': True, **restart()}
                        except Exception as exc:
                            reply = {'ok': False, 'error': str(exc)}
                        pending = directory / 'reply.tmp'
                        pending.write_text(json.dumps(reply))
                        pending.replace(directory / 'restart.reply.json')
        finally:
            # Reap the owned group even when its leader has already exited: a
            # child that inherited the pipes would otherwise keep the drain
            # below blocked and the helper would never unwind. The group id is
            # captured at spawn, so this reaches children that outlived the
            # leader, as the ordinary runner route does.
            _stop_process(runner)
            try:
                runner.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                runner.kill()
                try:
                    runner.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    pass


def _contained_evidence_dir(contract: dict, contract_dir: Path, data: Path, managed: bool) -> None:
    """Keep screenshot output inside the disposable run directory or case directory."""
    evidence = contract.get("evidence")
    if not isinstance(evidence, dict):
        evidence = {}
        contract["evidence"] = evidence
    raw = evidence.get("screenshot_dir")
    if raw is None:
        raw = str(data / "shots") if managed else str(contract_dir / "evidence")
    stamp = evidence.get("run_stamp")
    if stamp is not None and (not isinstance(stamp, str) or not FIXED_RUN_STAMP.match(stamp)):
        raise ValueError("evidence.run_stamp must be a fixed UTC stamp such as 2026-11-09T17-00-00-000Z")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("evidence.screenshot_dir must be a non-empty string")
    expanded = _expand_start(raw, contract_dir=contract_dir, data=data, port=0)
    root = (data if managed else contract_dir).resolve()
    candidate = Path(expanded).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    path = candidate.resolve()
    if not path.is_relative_to(root):
        raise ValueError("evidence.screenshot_dir must stay inside the disposable run directory")
    evidence["screenshot_dir"] = str(path)


def run_contract(contract_path: str | Path, base_url: str | None = None,
                 timeout: int = 120, *, frozen_contract: dict | None = None) -> dict:
    contract = copy.deepcopy(frozen_contract) if frozen_contract is not None else load_contract(contract_path)
    validate_contract(contract)
    if contract.get('recovery_campaign'):
        return run_recovery_campaign(contract_path, contract, base_url=base_url, timeout=timeout)
    if contract.get("setup_required"):
        raise ValueError(f"Case setup required: {contract['setup_required']}")
    contract_dir = Path(contract_path).expanduser().resolve().parent
    started = None
    lifecycle = []
    try:
        if base_url:
            valid_base(base_url)
            contract["app"]["base_url"] = base_url
        elif contract["app"].get("base_url"):
            valid_base(contract["app"]["base_url"])
        if not base_url and contract["app"].get("start"):
            started, contract["app"]["base_url"], log_path, log_handle, app_data = _start_app(contract, contract_dir)
        # The runner receives a temporary contract; make sqlite observer paths
        # relative to the contract the caller supplied, not to /tmp.
        for state in (contract.get("state"), (contract.get("control") or {}).get("state")):
            if isinstance(state, dict) and state.get("mode") == "sqlite" and state.get("db"):
                raw = str(state["db"])
                if started is not None:
                    raw = (raw.replace("{DATA_DIR}", str(app_data))
                              .replace("{APP_DIR}", str(contract_dir)))
                db = Path(raw).expanduser()
                if not db.is_absolute():
                    db = (contract_dir / db).resolve()
                state["db"] = str(db)
        _contained_evidence_dir(
            contract, contract_dir, app_data if started is not None else contract_dir,
            started is not None)
        fd, temp_path = tempfile.mkstemp(prefix="crashlab-contract-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(contract, fh)
            argv = [_node(), str(RUNNER), temp_path]
            env = _runner_env()
            if any(s['action']=='restart_app' for s in contract['journey']):
                if started is None:
                    raise ValueError('Restart requires the app to be started and owned by CrashLab')
                def restart():
                    nonlocal started, log_handle
                    old_pid, port = started.pid, started._crashlab_port
                    _stop_process(started)
                    log_handle.close()
                    started, restarted_base, _, log_handle, reused_data = _start_app(
                        contract, contract_dir, data_dir=app_data, port_override=port, initialize=False)
                    if restarted_base != contract['app']['base_url'] or reused_data != app_data:
                        raise RuntimeError('Restart changed the app URL or data directory')
                    event = {'old_pid': old_pid, 'new_pid': started.pid,
                             'same_port': True, 'same_data_directory': True,
                             'setup_replayed': False, 'reset_replayed': False}
                    lifecycle.append(event)
                    return event
                proc = _run_with_restart(argv, env, timeout, restart)
            else:
                runner = subprocess.Popen(
                    argv, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, env=env, start_new_session=(os.name == "posix"))
                if os.name == "posix":
                    runner._crashlab_pgid = runner.pid
                try:
                    stdout, stderr = runner.communicate(timeout=timeout)
                finally:
                    # Reap the owned group even when the direct runner exits
                    # first and leaves a browser child behind.
                    _stop_process(runner)
                proc = subprocess.CompletedProcess(argv, runner.returncode, stdout, stderr)
        finally:
            Path(temp_path).unlink(missing_ok=True)
    finally:
        if started is not None:
            _stop_process(started)
            log_handle.close()
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {
            "ok": False, "status": "ERROR", "contract": contract.get("id"), "returncode": proc.returncode,
            "error": "Browser runner could not start or return a result. Check npm ci, Chrome/Chromium and CHROME_PATH. " + proc.stderr[-1200:],
            "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:],
        }
    result["returncode"] = proc.returncode
    result['app_lifecycle'] = lifecycle
    if proc.returncode == 2:
        result["ok"] = False
    return result


def _completed_assertion(run: dict, *, passed: bool) -> bool:
    """Require a measured outcome at the configured, valid recovery boundary."""
    scenario = run.get("scenario")
    if not isinstance(scenario, dict) or scenario.get("valid") is not True:
        return False
    if run.get("error") or run.get("returncode") != (0 if passed else 1):
        return False
    if run.get("ok") is not passed:
        return False
    counts = (run.get("observed"), run.get("expected"))
    if not all(type(value) in (int, float) and math.isfinite(value) and value >= 0
               for value in counts):
        return False
    if run.get("primary_ok") is not passed:
        return False
    if passed and counts[0] != counts[1]:
        return False
    control = run.get("control")
    if control is not None:
        if not isinstance(control, dict) or control.get("ok") is not True:
            return False
        counts = (control.get("observed"), control.get("expected"))
        if not all(type(value) in (int, float) and math.isfinite(value) and value >= 0
                   for value in counts) or counts[0] != counts[1]:
            return False
    return True


def compare(contract_path: str | Path, buggy_url: str, fixed_url: str,
            timeout: int = 120, *, frozen_contract: dict | None = None) -> dict:
    frozen = copy.deepcopy(frozen_contract) if frozen_contract is not None else load_contract(contract_path)
    digest = hashlib.sha256(json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    evaluator_files = (Path(__file__), RUNNER, ROOT / "crashlab_probe.py")
    evaluator = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in evaluator_files}
    buggy = run_contract(contract_path, base_url=buggy_url, timeout=timeout, frozen_contract=frozen)
    fixed = run_contract(contract_path, base_url=fixed_url, timeout=timeout, frozen_contract=frozen)
    buggy["contract_sha256"] = digest
    fixed["contract_sha256"] = digest
    result = {
        "contract": buggy.get("contract"),
        "contract_sha256": digest,
        "evaluator_sha256": evaluator,
        "run_id": f"compare-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}",
        "buggy": buggy,
        "fixed": fixed,
        "ok": _completed_assertion(buggy, passed=False) and _completed_assertion(fixed, passed=True),
    }
    if evaluator != {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in evaluator_files}:
        result.update(ok=False, error="Evaluator changed during comparison", status="INVALID")
    return _persist(result)


def run_and_persist(contract_path: str | Path, base_url: str | None = None,
                    *, frozen_contract: dict | None = None) -> dict:
    """Keep setup errors visible as the latest result across host surfaces."""
    try:
        result = run_contract(contract_path, base_url=base_url, frozen_contract=frozen_contract)
    except Exception as exc:
        result = {"ok": False, "status": "ERROR", "contract": Path(contract_path).stem,
                  "error": f"{type(exc).__name__}: {exc}"}
    return persist_run(result)


def run_recovery_campaign(contract_path, frozen, *, base_url=None, timeout=120):
    """Derive each recovery boundary from one frozen operation, without editing it."""
    if base_url and 'restart_after_commit' in frozen['recovery_campaign']:
        raise ValueError('Restart campaign requires its managed app.start, not an external base_url override')
    prepared = []
    for name in frozen['recovery_campaign']:
        case = copy.deepcopy(frozen)
        case.pop('recovery_campaign')
        case['id'] = _campaign_case_id(frozen['id'], name)
        case['fault']['mode'] = 'prevent_before_delivery' if name == 'before_delivery' else 'suppress_after_upstream'
        if name == 'restart_after_commit':
            boundary = next(i for i, step in enumerate(case['journey']) if step['action']=='wait_for_fault')
            case['journey'].insert(boundary + 1, {'action':'restart_app'})
        validate_contract(case)
        prepared.append((name,case))
    outcomes = []
    for name,case in prepared:
        result = run_contract(contract_path, base_url=base_url, timeout=timeout, frozen_contract=case)
        result['diagnosis'] = explain_run(result)
        result['case_sha256'] = hashlib.sha256(json.dumps(case,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        outcomes.append({'name':name, 'result':result})
    results = [item['result'] for item in outcomes]
    valid = all((r.get('scenario') or {}).get('valid') is True and not r.get('error') for r in results)
    passed = valid and all(r.get('ok') is True for r in results)
    representative = next((r for r in results if not r.get('ok')), results[-1])
    controls = [r.get('control') or {} for r in results]
    control_ok = all(c.get('ok') is True for c in controls)
    return {'contract':frozen['id'], 'kind':'recovery_campaign', 'cases':outcomes,
            'ok':passed, 'primary_ok':all(r.get('primary_ok') is True for r in results),
            'status':'PASS' if passed else 'FAIL' if valid else 'ERROR',
            'error':None if valid else 'One or more recovery scenarios did not complete a valid measurement; inspect cases.',
            'returncode':0 if passed else 1 if valid else 2,
            'observed':representative.get('observed'), 'expected':representative.get('expected'),
            'representative_scenario':next(item['name'] for item in outcomes if item['result'] is representative),
            'scenario':{'valid':valid},
            'recovery':{'ok':all((r.get('recovery') or {}).get('ok') is True for r in results),
                        'reason':'Inspect each recorded recovery boundary.'},
            'control':{'ok':control_ok, 'observed':sum(c.get('observed',0) for c in controls),
                       'expected':sum(c.get('expected',0) for c in controls)}}


def case_directory() -> Path:
    return Path(os.environ.get('CRASHLAB_CASE_DIR', ROOT / 'cases')).expanduser().resolve()


def list_contracts(case_dir: str | Path | None = None) -> list[dict]:
    rows = []
    directories = (ROOT / "contracts", Path(case_dir) if case_dir is not None else case_directory())
    paths = sorted({path.resolve() for directory in directories for path in directory.glob("*.json")})
    for path in paths:
        row = {"id": path.stem, "path": str(path),
               "source": "bundled" if path.is_relative_to(ROOT) else "workspace"}
        try:
            contract = load_contract(path)
            setup = contract.get("setup_required") or ""
            auto_start = bool(contract["app"].get("start")) and not setup
            if not setup and not auto_start:
                # A case without app.start still needs the app running somewhere.
                # Say where, rather than showing an empty setup status.
                base_url = str(contract["app"].get("base_url") or "").strip()
                if base_url:
                    setup = (f"Start the application at {base_url} before running this case, "
                             "or add an app.start block to the case file.")
                else:
                    setup = "Start the application before running this case, or add an app.start block."
            row.update(id=contract["id"], setup_required=setup, auto_start=auto_start)
        except Exception as exc:
            row["error"] = str(exc)
        rows.append(row)
    # Lead a first run with a reproduced failure, rather than a pre-fixed example.
    return sorted(rows, key=lambda row: (row['id'] != 'checkout-local-start', row['id']))
