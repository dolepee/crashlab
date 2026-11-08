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
never receive the post-approval hook, so they fail closed.  They must execute
through the Hermes tool path, where the human gate is available.
"""
from __future__ import annotations

import contextvars
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

_PENDING: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "crashlab_approval_pending", default=None
)
_LOCK = threading.RLock()
_LEASES: dict[tuple[str, str], float] = {}
_SESSION_APPROVALS: dict[tuple[str, str], float] = {}
_PERMANENT_APPROVALS: set[str] = set()
LEASE_SECONDS = 300


class ApprovalRequired(PermissionError):
    """Raised when an execution has no human approval for its exact plan."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _resolved(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _data_dir() -> Path:
    return Path(os.environ.get("CRASHLAB_DATA_DIR", Path.home() / ".local/share/crashlab")).expanduser().resolve()


def _case_plan(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    path = Path(args["contract_path"]).expanduser().resolve()
    raw = path.read_bytes()
    contract = json.loads(raw.decode("utf-8"))
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


def _experiment_plan(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    experiment_id = args.get("experiment_id")
    if not isinstance(experiment_id, str) or not re.fullmatch(r"[a-f0-9]{32}", experiment_id):
        raise ValueError("invalid experiment_id")
    folder = _data_dir() / "experiments" / experiment_id
    experiment_raw = (folder / "experiment.json").read_bytes()
    case_raw = (folder / "case.json").read_bytes()
    experiment = json.loads(experiment_raw.decode("utf-8"))
    case = json.loads(case_raw.decode("utf-8"))
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


def _call_key(tool_call_id: str, digest: str) -> tuple[str, str]:
    return (tool_call_id or "", digest)


def _session_key(session_id: str, session_key: str) -> str:
    return session_id or session_key or ""


def _purge(now: float) -> None:
    for key, expiry in list(_LEASES.items()):
        if expiry <= now:
            _LEASES.pop(key, None)
    for key, expiry in list(_SESSION_APPROVALS.items()):
        if expiry <= now:
            _SESSION_APPROVALS.pop(key, None)


def _has_approval(digest: str, *, session_id: str = "", session_key: str = "") -> bool:
    now = time.time()
    with _LOCK:
        _purge(now)
        if digest in _PERMANENT_APPROVALS:
            return True
        scope = _session_key(session_id, session_key)
        return bool(scope and (scope, digest) in _SESSION_APPROVALS)


def pre_tool_call(tool_name: str, args: dict[str, Any] | None, *,
                  task_id: str = "", session_id: str = "", tool_call_id: str = "", **_: Any):
    """Hermes policy hook: escalate an execution plan to the human approval gate."""
    if tool_name not in EXECUTION_TOOLS:
        return None
    try:
        digest = plan_digest(tool_name, args)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {"action": "block",
                "message": f"CrashLab cannot approve this execution plan: {exc}"}
    _PENDING.set({"digest": digest, "tool": tool_name,
                  "tool_call_id": tool_call_id or task_id or "",
                  "session_id": session_id or ""})
    if _has_approval(digest, session_id=session_id):
        return None
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
    if digest is None:
        return
    if choice not in {"once", "session", "always"}:
        return
    now = time.time()
    with _LOCK:
        _purge(now)
        if choice == "once":
            _LEASES[_call_key(tool_call_id, digest)] = now + LEASE_SECONDS
        elif choice == "always":
            _PERMANENT_APPROVALS.add(digest)
        else:
            scope = _session_key(session_id, session_key)
            if scope:
                _SESSION_APPROVALS[(scope, digest)] = now + LEASE_SECONDS


def require_approval(tool_name: str, args: dict[str, Any] | None) -> None:
    """Fail closed unless this exact plan has a live human-approval lease."""
    if tool_name not in EXECUTION_TOOLS:
        return
    digest = plan_digest(tool_name, args)
    pending = _PENDING.get() or {}
    session_id = pending.get("session_id", "")
    if _has_approval(digest, session_id=session_id):
        return
    call_id = pending.get("tool_call_id", "")
    with _LOCK:
        _purge(time.time())
        expiry = _LEASES.pop(_call_key(call_id, digest), None)
    if expiry is not None and expiry > time.time():
        return
    raise ApprovalRequired(
        "CrashLab execution requires a human approval for this exact case, command, "
        "path and target. Run it through the Hermes crashlab tool.")


def reset_for_tests() -> None:
    with _LOCK:
        _LEASES.clear()
        _SESSION_APPROVALS.clear()
        _PERMANENT_APPROVALS.clear()
    _PENDING.set(None)


def grant_for_tests(tool_name: str, args: dict[str, Any], *, scope: str = "session") -> str:
    """Test helper mimicking a human approval response, never used in production."""
    digest = plan_digest(tool_name, args)
    now = time.time()
    with _LOCK:
        if scope == "always":
            _PERMANENT_APPROVALS.add(digest)
        elif scope == "session":
            _SESSION_APPROVALS[("test-session", digest)] = now + LEASE_SECONDS
        else:
            _LEASES[_call_key("", digest)] = now + LEASE_SECONDS
    _PENDING.set({"digest": digest, "tool": tool_name, "tool_call_id": "", "session_id": "test-session"})
    return digest
