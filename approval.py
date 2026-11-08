"""Human approval policy for CrashLab execution routes.

Hermes' documented plugin contract is a ``pre_tool_call`` directive of
``{"action": "approve", ...}``.  The host then runs its normal human approval
gate and only calls the tool handler after a real decision.  A pre-hook request
is *not* proof of that decision, so the execution paths also require a lease
created from the host's ``post_approval_response`` observer hook.

The lease is bound to the exact execution plan: the case bytes (and therefore
its ``app.start`` command, cwd, environment and URLs), the frozen experiment
identity, or the trusted campaign target.  Editing a case, changing a command
or path, or reusing an approval for another plan produces a different digest and
is denied.

Direct callers (the dashboard backend, a slash command, or an imported function)
never receive the post-approval hook, so they fail closed unless a human has
used an explicit operator path. The standalone CLI prompts at a terminal and the
Desktop dashboard issues a plan-bound, single-use ticket after an operator
confirmation; neither path silently grants execution.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

EXECUTION_TOOLS = {
    "crashlab_run",
    "crashlab_compare",
    "crashlab_experiment_run",
    "crashlab_campaign_run",
}
_READ_ONLY_TOOLS = {
    "crashlab_validate",
    "crashlab_list",
    "crashlab_campaign_list",
    "crashlab_campaign_validate",
    "crashlab_prepare",
    "crashlab_experiment_status",
    "crashlab_experiment_watch",
    "crashlab_experiment_report",
    "crashlab_experiment_export",
    "crashlab_experiment_import",
}

_LOCK = threading.RLock()
_REQUESTS: dict[str, float] = {}
_ONCE_APPROVALS: dict[tuple[str, str], float] = {}
_SESSION_APPROVALS: dict[tuple[str, str], float] = {}
_PERMANENT_APPROVALS: set[str] = set()
_CASE_SNAPSHOTS: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}
_EXPERIMENT_SNAPSHOTS: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
LEASE_SECONDS = 300


class ApprovalRequired(PermissionError):
    """Raised when an execution has no human approval for its exact plan."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _validate_contract(contract: dict[str, Any]) -> dict[str, Any]:
    try:
        from .crashlab_core import validate_contract
    except ImportError:
        from crashlab_core import validate_contract
    return validate_contract(contract)


def _resolved(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _data_dir() -> Path:
    return Path(os.environ.get("CRASHLAB_DATA_DIR", Path.home() / ".local/share/crashlab")).expanduser().resolve()


def _case_plan(tool: str, args: dict[str, Any], *, raw: bytes | None = None,
               contract: dict | None = None) -> dict[str, Any]:
    path = Path(args["contract_path"]).expanduser().resolve()
    raw = path.read_bytes() if raw is None else raw
    contract = json.loads(raw.decode("utf-8")) if contract is None else contract
    start = ((contract.get("app") or {}).get("start") if isinstance(contract, dict) else None)
    plan: dict[str, Any] = {
        "tool": tool,
        "contract_path": str(path),
        "contract_sha256": _sha256(raw),
        "contract": _sha256(_canonical(contract)),
        "app_start": start,
        "base_url": args.get("base_url"),
    }
    if tool == "crashlab_compare":
        plan.update(buggy_url=args.get("buggy_url"), fixed_url=args.get("fixed_url"))
    return plan


def _experiment_plan(tool: str, args: dict[str, Any], *, experiment_raw: bytes | None = None,
                     case_raw: bytes | None = None, case: dict | None = None) -> dict[str, Any]:
    experiment_id = args.get("experiment_id")
    if not isinstance(experiment_id, str) or not re.fullmatch(r"[a-f0-9]{32}", experiment_id):
        raise ValueError("invalid experiment_id")
    folder = _data_dir() / "experiments" / experiment_id
    experiment_raw = (folder / "experiment.json").read_bytes() if experiment_raw is None else experiment_raw
    case_raw = (folder / "case.json").read_bytes() if case_raw is None else case_raw
    experiment = json.loads(experiment_raw.decode("utf-8"))
    case = json.loads(case_raw.decode("utf-8")) if case is None else case
    return {
        "tool": tool,
        "experiment_id": experiment_id,
        "experiment_sha256": _sha256(experiment_raw),
        "case_sha256": _sha256(case_raw),
        "case": _sha256(_canonical(case)),
        "project_path": experiment.get("project_path") if isinstance(experiment, dict) else None,
        "app_start": ((case.get("app") or {}).get("start") if isinstance(case, dict) else None),
    }


def _campaign_plan(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    campaign_id = args.get("campaign_id")
    catalog = {
        "inventory-contention": "campaigns/inventory.json",
        "checkout-and-inventory": "campaigns/mixed.json",
    }
    if campaign_id not in catalog:
        raise ValueError("unknown campaign_id")
    root = Path(__file__).resolve().parent
    campaign_path = root / catalog[campaign_id]
    target_path = _data_dir() / "campaign-targets.json"
    plan = {
        "tool": tool,
        "campaign_id": campaign_id,
        "target_set": args.get("target_set"),
        "repetitions": args.get("repetitions", 1),
        "campaign_sha256": _sha256(campaign_path.read_bytes()),
    }
    if target_path.is_file():
        plan["target_sha256"] = _sha256(target_path.read_bytes())
    return plan


def execution_plan(tool_name: str, args: dict[str, Any] | None) -> dict[str, Any]:
    """Return the exact execution plan that a human is being asked to approve."""
    if not isinstance(args, dict):
        raise ValueError("CrashLab tool arguments must be an object")
    if tool_name in {"crashlab_run", "crashlab_compare"}:
        return _case_plan(tool_name, args)
    if tool_name == "crashlab_experiment_run":
        return _experiment_plan(tool_name, args)
    if tool_name == "crashlab_campaign_run":
        return _campaign_plan(tool_name, args)
    raise ValueError(f"not an execution tool: {tool_name}")


def plan_digest(tool_name: str, args: dict[str, Any] | None) -> str:
    return _sha256(_canonical(execution_plan(tool_name, args)))


def _rule_key(tool_name: str, digest: str) -> str:
    return f"crashlab:{tool_name}:{digest}"


def _digest_from_pattern(pattern_key: str) -> str | None:
    prefix = "plugin_rule:crashlab:"
    if not isinstance(pattern_key, str) or not pattern_key.startswith(prefix):
        return None
    rest = pattern_key[len(prefix):]
    if ":" not in rest:
        return None
    _tool, digest = rest.split(":", 1)
    return digest if len(digest) == 64 and all(c in "0123456789abcdef" for c in digest) else None


def _scopes(*, session_id: str = "", task_id: str = "", session_key: str = "") -> list[str]:
    values = []
    for value in (session_id, task_id, session_key):
        if isinstance(value, str) and value and value not in values:
            values.append(value)
    return values


def _purge(now: float) -> None:
    for key, expiry in list(_ONCE_APPROVALS.items()):
        if expiry <= now:
            _ONCE_APPROVALS.pop(key, None)
    for key, expiry in list(_SESSION_APPROVALS.items()):
        if expiry <= now:
            _SESSION_APPROVALS.pop(key, None)
    for digest, seen in list(_REQUESTS.items()):
        if now - seen > LEASE_SECONDS:
            _REQUESTS.pop(digest, None)
    for key, (expiry, _snapshot) in list(_CASE_SNAPSHOTS.items()):
        if expiry <= now:
            _CASE_SNAPSHOTS.pop(key, None)
    for key, (expiry, _snapshot) in list(_EXPERIMENT_SNAPSHOTS.items()):
        if expiry <= now:
            _EXPERIMENT_SNAPSHOTS.pop(key, None)


def _has_approval(digest: str, *, scopes: list[str]) -> bool:
    now = time.time()
    with _LOCK:
        _purge(now)
        if digest in _PERMANENT_APPROVALS:
            return True
        return any((scope, digest) in _SESSION_APPROVALS for scope in scopes)


def _case_snapshot(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    path = Path(args["contract_path"]).expanduser().resolve()
    raw = path.read_bytes()
    contract = json.loads(raw.decode("utf-8"))
    contract = _validate_contract(contract)
    plan = _case_plan(tool_name, args, raw=raw, contract=contract)
    return {
        "path": str(path),
        "raw": raw,
        "contract": contract,
        "sha256": _sha256(raw),
        "digest": _sha256(_canonical(plan)),
    }


def _experiment_snapshot(experiment_id: str) -> dict[str, Any]:
    args = {"experiment_id": experiment_id}
    folder = _data_dir() / "experiments" / experiment_id
    experiment_raw = (folder / "experiment.json").read_bytes()
    case_raw = (folder / "case.json").read_bytes()
    case = json.loads(case_raw.decode("utf-8"))
    case = _validate_contract(case)
    plan = _experiment_plan("crashlab_experiment_run", args,
                            experiment_raw=experiment_raw, case_raw=case_raw, case=case)
    return {"folder": str(folder), "contract": case, "digest": _sha256(_canonical(plan))}


def _snapshot_scope(tool_name: str, args: dict[str, Any], session_id: str) -> tuple[str, str]:
    if tool_name in {"crashlab_run", "crashlab_compare"}:
        return ("case", str(Path(args["contract_path"]).expanduser().resolve()), session_id or "")
    return ("experiment", str(args.get("experiment_id") or ""), session_id or "")


def pre_tool_call(tool_name: str, args: dict[str, Any] | None, *,
                  task_id: str = "", session_id: str = "", tool_call_id: str = "", **_: Any):
    """Hermes policy hook: escalate an execution plan to the human approval gate."""
    if tool_name not in EXECUTION_TOOLS:
        return None
    scope = session_id or task_id or ""
    try:
        if tool_name in {"crashlab_run", "crashlab_compare"}:
            snapshot = _case_snapshot(tool_name, args)
            digest = snapshot["digest"]
            with _LOCK:
                _CASE_SNAPSHOTS[_snapshot_scope(tool_name, args, scope)] = (
                    time.time() + LEASE_SECONDS, snapshot)
        elif tool_name == "crashlab_experiment_run":
            snapshot = _experiment_snapshot(args["experiment_id"])
            digest = snapshot["digest"]
            with _LOCK:
                _EXPERIMENT_SNAPSHOTS[(args["experiment_id"], scope)] = (
                    time.time() + LEASE_SECONDS, snapshot)
        else:
            digest = plan_digest(tool_name, args)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {"action": "block",
                "message": f"CrashLab cannot approve this execution plan: {exc}"}
    scopes = _scopes(session_id=session_id, task_id=task_id)
    if _has_approval(digest, scopes=scopes):
        return None
    with _LOCK:
        _REQUESTS[digest] = time.time()
    return {
        "action": "approve",
        "message": f"CrashLab will execute {tool_name} for this exact frozen plan.",
        "rule_key": _rule_key(tool_name, digest),
    }


def post_approval_response(command: str = "", description: str = "", pattern_key: str = "",
                           pattern_keys: list[str] | None = None, session_key: str = "",
                           surface: str = "", choice: str = "", tool_call_id: str = "",
                           session_id: str = "", **_: Any) -> None:
    """Record a real host decision; only a human allow choice creates a lease."""
    digest = _digest_from_pattern(pattern_key)
    if digest is None or choice not in {"once", "session", "always"}:
        return
    now = time.time()
    with _LOCK:
        _purge(now)
        if digest not in _REQUESTS:
            return
        if choice == "always":
            _PERMANENT_APPROVALS.add(digest)
            return
        scopes = _scopes(session_id=session_id, session_key=session_key)
        if not scopes:
            scopes = [""]
        target = _ONCE_APPROVALS if choice == "once" else _SESSION_APPROVALS
        for scope in scopes:
            target[(scope, digest)] = now + LEASE_SECONDS


def _require_digest(digest: str, *, scopes: list[str]) -> None:
    if _has_approval(digest, scopes=scopes):
        return
    now = time.time()
    with _LOCK:
        _purge(now)
        for scope in scopes:
            expiry = _ONCE_APPROVALS.pop((scope, digest), None)
            if expiry is not None and expiry > now:
                return
    raise ApprovalRequired(
        "CrashLab execution requires a human approval for this exact case, command, "
        "path and target. Run it through the Hermes crashlab tool or the explicit "
        "CrashLab operator approval flow.")


def require_approval(tool_name: str, args: dict[str, Any] | None, *,
                     session_id: str = "", task_id: str = "", session_key: str = "") -> None:
    """Fail closed unless this exact plan has a live human-approval lease."""
    if tool_name not in EXECUTION_TOOLS:
        return
    digest = plan_digest(tool_name, args)
    scopes = _scopes(session_id=session_id, task_id=task_id, session_key=session_key)
    _require_digest(digest, scopes=scopes or [""])


def prepare_approved_case(tool_name: str, args: dict[str, Any], *,
                          session_id: str = "", task_id: str = "",
                          session_key: str = "") -> dict[str, Any]:
    """Read the approved case once and return the exact contract to execute.

    The case is not reopened after this point. A replacement file after the
    approval cannot change the command that runs.
    """
    if tool_name not in {"crashlab_run", "crashlab_compare"}:
        raise ValueError(f"not a case tool: {tool_name}")
    scope = session_id or task_id or ""
    key = _snapshot_scope(tool_name, args, scope)
    with _LOCK:
        cached = _CASE_SNAPSHOTS.get(key)
        if cached is not None and cached[0] <= time.time():
            _CASE_SNAPSHOTS.pop(key, None)
            cached = None
    if cached is not None:
        snapshot = cached[1]
        _require_digest(snapshot["digest"], scopes=_scopes(session_id=session_id, task_id=task_id,
                                                           session_key=session_key) or [""])
        return snapshot
    path = Path(args["contract_path"]).expanduser().resolve()
    raw = path.read_bytes()
    contract = json.loads(raw.decode("utf-8"))
    contract = _validate_contract(contract)
    plan = _case_plan(tool_name, args, raw=raw, contract=contract)
    digest = _sha256(_canonical(plan))
    _require_digest(digest, scopes=_scopes(session_id=session_id, task_id=task_id,
                                            session_key=session_key) or [""])
    return {"path": str(path), "contract": contract, "sha256": _sha256(raw), "digest": digest}


def prepare_approved_experiment(experiment_id: str, *, session_id: str = "",
                                 task_id: str = "", session_key: str = "") -> dict[str, Any]:
    """Read the approved experiment case once and return it for execution."""
    scope = session_id or task_id or ""
    key = (experiment_id, scope)
    with _LOCK:
        cached = _EXPERIMENT_SNAPSHOTS.get(key)
        if cached is not None and cached[0] <= time.time():
            _EXPERIMENT_SNAPSHOTS.pop(key, None)
            cached = None
    if cached is not None:
        snapshot = cached[1]
        _require_digest(snapshot["digest"], scopes=_scopes(session_id=session_id, task_id=task_id,
                                                           session_key=session_key) or [""])
        return snapshot
    snapshot = _experiment_snapshot(experiment_id)
    _require_digest(snapshot["digest"], scopes=_scopes(session_id=session_id, task_id=task_id,
                                                       session_key=session_key) or [""])
    return snapshot


def reset_for_tests() -> None:
    with _LOCK:
        _REQUESTS.clear()
        _ONCE_APPROVALS.clear()
        _SESSION_APPROVALS.clear()
        _PERMANENT_APPROVALS.clear()
        _CASE_SNAPSHOTS.clear()
        _EXPERIMENT_SNAPSHOTS.clear()


def grant_for_operator(tool_name: str, args: dict[str, Any], *, session_id: str = "operator") -> str:
    """Create a short-lived grant after an explicit local operator confirmation."""
    if tool_name in {"crashlab_run", "crashlab_compare"}:
        snapshot = _case_snapshot(tool_name, args)
    elif tool_name == "crashlab_experiment_run":
        snapshot = _experiment_snapshot(args["experiment_id"])
    else:
        snapshot = None
    digest = snapshot["digest"] if snapshot else plan_digest(tool_name, args)
    now = time.time()
    with _LOCK:
        _REQUESTS[digest] = now
        _ONCE_APPROVALS[(session_id, digest)] = now + LEASE_SECONDS
        if snapshot is not None:
            if tool_name in {"crashlab_run", "crashlab_compare"}:
                _CASE_SNAPSHOTS[_snapshot_scope(tool_name, args, session_id)] = (now + LEASE_SECONDS, snapshot)
            else:
                _EXPERIMENT_SNAPSHOTS[(args["experiment_id"], session_id)] = (now + LEASE_SECONDS, snapshot)
    return digest


def grant_for_tests(tool_name: str, args: dict[str, Any], *, scope: str = "session") -> str:
    """Test helper mimicking a human approval response, never used in production."""
    digest = plan_digest(tool_name, args)
    now = time.time()
    with _LOCK:
        _REQUESTS[digest] = now
        if scope == "always":
            _PERMANENT_APPROVALS.add(digest)
        elif scope == "session":
            _SESSION_APPROVALS[("test-session", digest)] = now + LEASE_SECONDS
        else:
            _ONCE_APPROVALS[("test-session", digest)] = now + LEASE_SECONDS
    return digest
