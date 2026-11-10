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

import contextvars
import copy
import hashlib
import json
import os
import re
import shlex
import sys
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
_REQUESTS: dict[tuple[str, str], float] = {}
# A once-only human decision is a single grant, indexed by every scope alias it
# was issued for (a host may supply both a session id and a session key).
# Consuming it removes every alias, so one "once" cannot authorize two runs.
_ONCE_GRANTS: dict[int, tuple[str, float, tuple[str, ...], str]] = {}
_ONCE_INDEX: dict[tuple[str, str, str], int] = {}
_ONCE_SEQ = 0
_SESSION_APPROVALS: dict[tuple[str, str], float] = {}
_PERMANENT_APPROVALS: set[str] = set()
_CASE_SNAPSHOTS: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}
_EXPERIMENT_SNAPSHOTS: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_CAMPAIGN_SNAPSHOTS: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
LEASE_SECONDS = 300


class ApprovalRequired(PermissionError):
    """Raised when an execution has no human approval for its exact plan."""


def _canonical(value: Any) -> str:
    # Must stay byte-identical to ``repair_workflow.digest``: the workflow recomputes
    # the experiment metadata digest reported here, and a different escape policy
    # (``ensure_ascii=False``) made every non-ASCII intent or path look edited.
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


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


def _clean_summary(value: Any, limit: int = 1200) -> str:
    """Keep an approval prompt on one bounded line without env values."""
    text = str(value if value is not None else "")
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text.replace("\r", " ").replace("\n", " "))
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return text


def _approval_summary(tool_name: str, plan: dict[str, Any]) -> str:
    """Describe the complete material execution plan, or fail closed.

    A human cannot approve a command, environment or target they cannot see.
    Fields are therefore shown in full (after control-character sanitising).
    If the complete plan does not fit the bounded prompt, raise instead of
    approving a truncated prefix.
    """
    plan = plan if isinstance(plan, dict) else {}
    start = plan.get("app_start")
    start = start if isinstance(start, dict) else {}
    parts = [f"CrashLab will execute {tool_name} for this frozen plan."]
    if plan.get("buggy_url"):
        parts.append("buggy=" + _clean_summary(plan["buggy_url"], 100000))
    if plan.get("fixed_url"):
        parts.append("fixed=" + _clean_summary(plan["fixed_url"], 100000))
    if plan.get("target"):
        parts.append("target=" + _clean_summary(plan["target"], 100000))
    elif plan.get("targets"):
        parts.append("targets=" + ", ".join(_clean_summary(item, 100000) for item in plan["targets"]))
    else:
        parts.append("target=unresolved; determined when the app starts")
    if "base_url" in plan:
        parts.append("base_url=" + (_clean_summary(plan["base_url"], 100000) if plan.get("base_url") else "none"))
    if plan.get("reset_url"):
        parts.append("reset=" + _clean_summary(plan["reset_url"], 100000))
    elif plan.get("reset_urls"):
        parts.append("reset=" + ", ".join(_clean_summary(item, 100000) for item in plan["reset_urls"]))
    env = start.get("env")
    if isinstance(env, dict):
        parts.append("env=" + (", ".join(sorted(_clean_summary(key, 100000) for key in env)) or "(none)"))
    if plan.get("contract_path"):
        parts.append("case=" + _clean_summary(plan["contract_path"], 100000))
    if plan.get("experiment_id"):
        parts.append("experiment=" + _clean_summary(plan["experiment_id"], 100000))
    if start:
        app_python = str(start.get("python") or sys.executable)
        if app_python == "{PYTHON}":
            app_python = sys.executable
        parts.append("python=" + _clean_summary(app_python, 100000))
    command = start.get("command")
    if isinstance(command, list):
        try:
            argv = shlex.join(str(item) for item in command)
        except (TypeError, ValueError):
            argv = " ".join(str(item) for item in command)
        if argv:
            parts.append("argv=" + _clean_summary(argv, 100000))
    elif command:
        parts.append("argv=" + _clean_summary(command, 100000))
    if start.get("cwd"):
        parts.append("cwd=" + _clean_summary(start["cwd"], 100000))
    summary = _clean_summary(" · ".join(parts), 100000)
    if len(summary) > 1200:
        raise ValueError("the complete approval plan exceeds the 1,200-character prompt; split or shorten the command, environment or paths before requesting approval")
    return summary

def _case_plan(tool: str, args: dict[str, Any], *, raw: bytes | None = None,
               contract: dict | None = None) -> dict[str, Any]:
    path = Path(args["contract_path"]).expanduser().resolve()
    raw = path.read_bytes() if raw is None else raw
    contract = json.loads(raw.decode("utf-8")) if contract is None else contract
    _validate_contract(contract)
    app = contract.get("app") if isinstance(contract, dict) else None
    app = app if isinstance(app, dict) else {}
    start = app.get("start")
    try:
        from . import crashlab_core as core
    except ImportError:
        import crashlab_core as core
    override = args.get("base_url") if tool == "crashlab_run" else None
    target = core.effective_base_url(contract, override)
    for candidate in (target, args.get("base_url"), args.get("buggy_url"), args.get("fixed_url")):
        if candidate:
            core.valid_base(candidate)
    plan: dict[str, Any] = {
        "tool": tool,
        "contract_path": str(path),
        "contract_sha256": _sha256(raw),
        "contract": _sha256(_canonical(contract)),
        "app_start": start,
        "base_url": args.get("base_url"),
        # The target execution will actually use. This follows the managed
        # app.start target before an external app.base_url, rather than showing
        # a URL the runner will overwrite after starting the app.
        "target": target,
        "reset_url": core.effective_reset_url(contract),
    }
    if tool == "crashlab_compare":
        plan.update(
            buggy_url=args.get("buggy_url"),
            fixed_url=args.get("fixed_url"),
            targets=[args.get("buggy_url"), args.get("fixed_url")],
        )
        plan["target"] = args.get("buggy_url") or target
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
    _validate_contract(case)
    try:
        from . import crashlab_core as core
    except ImportError:
        import crashlab_core as core
    return {
        "tool": tool,
        "experiment_id": experiment_id,
        "experiment_sha256": _sha256(experiment_raw),
        "case_sha256": _sha256(case_raw),
        "case": _sha256(_canonical(case)),
        "project_path": experiment.get("project_path") if isinstance(experiment, dict) else None,
        "app_start": ((case.get("app") or {}).get("start") if isinstance(case, dict) else None),
        "target": core.effective_base_url(case),
        "reset_url": core.effective_reset_url(case),
    }


def _campaign_plan(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    try:
        from . import campaign_plugin
    except ImportError:
        import campaign_plugin
    try:
        return campaign_plugin.capture(args)["plan"]
    except Exception as exc:
        raise ValueError(str(exc)) from exc


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


def _snapshot_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Canonical arguments that identify one invocation, including every URL."""
    value = copy.deepcopy(args)
    if tool_name in {"crashlab_run", "crashlab_compare"}:
        value["contract_path"] = _resolved(value["contract_path"])
    return value


def _snapshot_key(tool_name: str, args: dict[str, Any], session_id: str = "",
                  task_id: str = "", tool_call_id: str = "") -> tuple[str, str]:
    scope = session_id or task_id or ""
    args_digest = _sha256(_canonical(_snapshot_args(tool_name, args)))
    return (f"{tool_name}\0{args_digest}", f"{scope}\0{tool_call_id or ''}")


def _find_snapshot(store: dict, tool_name: str, args: dict[str, Any], *,
                   session_id: str = "", task_id: str = "", tool_call_id: str = ""):
    key = _snapshot_key(tool_name, args, session_id, task_id, tool_call_id)
    with _LOCK:
        entry = store.get(key)
        if entry is not None and entry[0] > time.time():
            return entry[1]
        if entry is not None:
            store.pop(key, None)
        if tool_call_id:
            return None
        scope = session_id or task_id or ""
        args_digest = key[0]
        candidates = [(item_key, item) for item_key, item in store.items()
                      if item_key[0] == args_digest and item_key[1].split("\0", 1)[0] == scope
                      and item[0] > time.time()]
        if len(candidates) == 1:
            return candidates[0][1][1]
    return None


def _store_snapshot(store: dict, tool_name: str, args: dict[str, Any], snapshot: dict[str, Any],
                    *, session_id: str = "", task_id: str = "", tool_call_id: str = "",
                    replace: bool = False) -> None:
    key = _snapshot_key(tool_name, args, session_id, task_id, tool_call_id)
    with _LOCK:
        current = store.get(key)
        if replace or current is None or current[0] <= time.time():
            store[key] = (time.time() + LEASE_SECONDS, snapshot)


def _retire_snapshot(store: dict, tool_name: str, args: dict[str, Any], *,
                     session_id: str = "", task_id: str = "", tool_call_id: str = "") -> None:
    """A frozen plan is spent by the execution it authorised.

    Without an originating call id a later, identical invocation would otherwise
    find the completed snapshot and check an already-consumed grant instead of
    the plan its own pre-hook captured and the human just approved.
    """
    key = _snapshot_key(tool_name, args, session_id, task_id, tool_call_id)
    with _LOCK:
        store.pop(key, None)


# The host's public tool-execution middleware seam supplies the trusted call id
# around the real dispatch. Keep that identity in this plugin, not in a private
# Hermes module: the catalog only permits public plugin surfaces.
_TOOL_CALL_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "crashlab_tool_call_id", default="")


def tool_execution_middleware(*, args: dict[str, Any], next_call, tool_call_id: str = "", **_kwargs: Any):
    """Bind the host-supplied call id around the downstream tool dispatch.

    ``tool_call_id`` arrives from Hermes' trusted ids, never from model arguments.
    The token is reset even when the tool raises, and an absent id deliberately
    clears the value so a nested call cannot inherit an unrelated identity.
    """
    token = _TOOL_CALL_ID.set(str(tool_call_id or ""))
    try:
        return next_call(args)
    finally:
        _TOOL_CALL_ID.reset(token)


def host_call_id(context: dict[str, Any] | None = None) -> str:
    """The originating tool call id the host exposes to a handler.

    Hermes passes ``task_id`` and ``session_id`` to a tool handler but not the
    call id; the public tool-execution middleware binds it for the duration of
    the real dispatch. Missing identity stays empty so callers fall back to the
    unambiguous-single-grant rule instead of guessing between concurrent calls.
    """
    values: list[Any] = []
    if isinstance(context, dict):
        values.append(context.get("tool_call_id"))
    try:
        values.append(_TOOL_CALL_ID.get())
    except Exception:
        pass
    for value in values:
        if isinstance(value, str) and value:
            return value
    return ""


def _grant_once(scopes: list[str], digest: str, now: float, tool_call_id: str = "") -> None:
    """Record one single-use grant, reachable through all of its aliases."""
    global _ONCE_SEQ
    _ONCE_SEQ += 1
    grant = _ONCE_SEQ
    aliases = tuple(dict.fromkeys(scopes)) or ("",)
    call = tool_call_id or ""
    _ONCE_GRANTS[grant] = (digest, now + LEASE_SECONDS, aliases, call)
    for scope in aliases:
        _ONCE_INDEX[(scope, digest, call)] = grant


def _drop_once(grant: int) -> None:
    entry = _ONCE_GRANTS.pop(grant, None)
    if entry is None:
        return
    digest, _expiry, aliases, call = entry
    for scope in aliases:
        if _ONCE_INDEX.get((scope, digest, call)) == grant:
            _ONCE_INDEX.pop((scope, digest, call), None)


def _consume_once(digest: str, scopes: list[str], now: float, tool_call_id: str = "") -> bool:
    """Spend a single-use grant atomically, whichever alias is presented."""
    grant = None
    call = tool_call_id or ""
    for scope in scopes:
        grant = _ONCE_INDEX.get((scope, digest, call))
        if grant is not None:
            break
    if grant is None:
        # A decision recorded without a call id (an older host, or one that reports
        # the answer outside the dispatch context) leaves the grant unbound. Spend
        # an unbound grant only when it is the single live one for these scopes;
        # a call-bound grant is never handed to a different call.
        matches = [gid for gid, entry in _ONCE_GRANTS.items()
                   if entry[0] == digest and entry[1] > now and any(scope in entry[2] for scope in scopes)
                   and (not call or not entry[3])]
        if len(matches) == 1:
            grant = matches[0]
    if grant is None:
        return False
    entry = _ONCE_GRANTS.get(grant)
    _drop_once(grant)
    return entry is not None and entry[1] > now


def _purge(now: float) -> None:
    for grant, (_digest, expiry, _aliases, _call) in list(_ONCE_GRANTS.items()):
        if expiry <= now:
            _drop_once(grant)
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
    for key, (expiry, _snapshot) in list(_CAMPAIGN_SNAPSHOTS.items()):
        if expiry <= now:
            _CAMPAIGN_SNAPSHOTS.pop(key, None)


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
        "tool": tool_name,
        "args": _snapshot_args(tool_name, args),
        "path": str(path),
        "raw": raw,
        "contract": contract,
        "sha256": _sha256(raw),
        "digest": _sha256(_canonical(plan)),
        "plan": plan,
    }


def _experiment_snapshot(experiment_id: str) -> dict[str, Any]:
    args = {"experiment_id": experiment_id}
    folder = _data_dir() / "experiments" / experiment_id
    experiment_raw = (folder / "experiment.json").read_bytes()
    case_raw = (folder / "case.json").read_bytes()
    experiment = json.loads(experiment_raw.decode("utf-8"))
    case = json.loads(case_raw.decode("utf-8"))
    case = _validate_contract(case)
    plan = _experiment_plan("crashlab_experiment_run", args,
                            experiment_raw=experiment_raw, case_raw=case_raw, case=case)
    return {
        "tool": "crashlab_experiment_run",
        "args": args,
        "folder": str(folder),
        "contract": case,
        "experiment": experiment,
        "experiment_digest": _sha256(_canonical(experiment)),
        "experiment_sha256": _sha256(experiment_raw),
        "case_sha256": _sha256(case_raw),
        "digest": _sha256(_canonical(plan)),
        "plan": plan,
    }


def _campaign_snapshot(args: dict[str, Any]) -> dict[str, Any]:
    try:
        from . import campaign_plugin
    except ImportError:
        import campaign_plugin
    bundle = campaign_plugin.capture(args)
    return {
        "tool": "crashlab_campaign_run",
        "args": _snapshot_args("crashlab_campaign_run", args),
        "digest": bundle["digest"],
        "plan": bundle["plan"],
        "bundle": bundle,
    }


def pre_tool_call(tool_name: str, args: dict[str, Any] | None, *,
                  task_id: str = "", session_id: str = "", tool_call_id: str = "", **_: Any):
    """Hermes policy hook: escalate an execution plan to the human approval gate."""
    if tool_name not in EXECUTION_TOOLS:
        return None
    scope = session_id or task_id or ""
    try:
        if tool_name in {"crashlab_run", "crashlab_compare"}:
            snapshot = _case_snapshot(tool_name, args)
            _store_snapshot(_CASE_SNAPSHOTS, tool_name, args, snapshot,
                            session_id=session_id, task_id=task_id, tool_call_id=tool_call_id,
                            replace=True)
        elif tool_name == "crashlab_experiment_run":
            snapshot = _experiment_snapshot(args["experiment_id"])
            _store_snapshot(_EXPERIMENT_SNAPSHOTS, tool_name, args, snapshot,
                            session_id=session_id, task_id=task_id, tool_call_id=tool_call_id,
                            replace=True)
        else:
            snapshot = _campaign_snapshot(args)
            _store_snapshot(_CAMPAIGN_SNAPSHOTS, tool_name, args, snapshot,
                            session_id=session_id, task_id=task_id, tool_call_id=tool_call_id,
                            replace=True)
        digest = snapshot["digest"]
    except Exception as exc:
        return {"action": "block",
                "message": f"CrashLab cannot approve this execution plan: {exc}"}
    scopes = _scopes(session_id=session_id, task_id=task_id)
    if _has_approval(digest, scopes=scopes):
        return None
    with _LOCK:
        _REQUESTS[(digest, tool_call_id or "")] = time.time()
    try:
        message = _approval_summary(tool_name, snapshot.get("plan", snapshot))
    except Exception as exc:
        return {"action": "block",
                "message": f"CrashLab cannot approve this execution plan without complete disclosure: {exc}"}
    return {
        "action": "approve",
        "message": message,
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
        request_key = (digest, tool_call_id or "")
        if request_key not in _REQUESTS and tool_call_id:
            request_key = (digest, "")
        if request_key not in _REQUESTS and not tool_call_id:
            # The decision arrived without a call id; accept it only when one
            # request for this plan is pending, never to settle a race.
            pending = [key for key in _REQUESTS if key[0] == digest]
            if len(pending) == 1:
                request_key = pending[0]
        if request_key not in _REQUESTS:
            return
        _REQUESTS.pop(request_key, None)
        # The request identifies the invocation the human approved, so a response
        # that did not repeat the call id is still bound to that call.
        approved_call = request_key[1]
        if choice == "always":
            _PERMANENT_APPROVALS.add(digest)
            return
        scopes = _scopes(session_id=session_id, session_key=session_key)
        if not scopes:
            scopes = [""]
        if choice == "once":
            _grant_once(scopes, digest, now, approved_call)
        else:
            for scope in scopes:
                _SESSION_APPROVALS[(scope, digest)] = now + LEASE_SECONDS


def _require_digest(digest: str, *, scopes: list[str], tool_call_id: str = "") -> None:
    if _has_approval(digest, scopes=scopes):
        return
    now = time.time()
    with _LOCK:
        _purge(now)
        if _consume_once(digest, scopes, now, tool_call_id):
            return
    raise ApprovalRequired(
        "CrashLab execution requires a human approval for this exact case, command, "
        "path and target. Run it through the Hermes crashlab tool or the explicit "
        "CrashLab operator approval flow.")


def require_approval(tool_name: str, args: dict[str, Any] | None, *,
                     session_id: str = "", task_id: str = "", session_key: str = "",
                     tool_call_id: str = "") -> None:
    """Fail closed unless this exact plan has a live human-approval lease."""
    if tool_name not in EXECUTION_TOOLS:
        return
    digest = plan_digest(tool_name, args)
    scopes = _scopes(session_id=session_id, task_id=task_id, session_key=session_key)
    _require_digest(digest, scopes=scopes or [""], tool_call_id=tool_call_id)


def capture_plan(tool_name: str, args: dict[str, Any], *, session_id: str = "",
                 task_id: str = "", tool_call_id: str = "") -> dict[str, Any]:
    """Read and freeze one execution plan without granting it."""
    if tool_name in {"crashlab_run", "crashlab_compare"}:
        snapshot = _case_snapshot(tool_name, args)
        store = _CASE_SNAPSHOTS
    elif tool_name == "crashlab_experiment_run":
        snapshot = _experiment_snapshot(args["experiment_id"])
        store = _EXPERIMENT_SNAPSHOTS
    elif tool_name == "crashlab_campaign_run":
        snapshot = _campaign_snapshot(args)
        store = _CAMPAIGN_SNAPSHOTS
    else:
        raise ValueError(f"not an execution tool: {tool_name}")
    _store_snapshot(store, tool_name, args, snapshot, session_id=session_id,
                    task_id=task_id, tool_call_id=tool_call_id, replace=True)
    return snapshot


def prepare_approved_case(tool_name: str, args: dict[str, Any], *,
                          session_id: str = "", task_id: str = "",
                          session_key: str = "", tool_call_id: str = "",
                          snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the exact frozen case, never a later replacement file."""
    if tool_name not in {"crashlab_run", "crashlab_compare"}:
        raise ValueError(f"not a case tool: {tool_name}")
    scopes = _scopes(session_id=session_id, task_id=task_id, session_key=session_key) or [""]
    if snapshot is None:
        snapshot = _find_snapshot(_CASE_SNAPSHOTS, tool_name, args,
                                  session_id=session_id, task_id=task_id,
                                  tool_call_id=tool_call_id)
    if snapshot is not None:
        if snapshot.get("tool") != tool_name or snapshot.get("args") != _snapshot_args(tool_name, args):
            raise ApprovalRequired("CrashLab approval belongs to a different invocation")
        _require_digest(snapshot["digest"], scopes=scopes, tool_call_id=tool_call_id)
        _retire_snapshot(_CASE_SNAPSHOTS, tool_name, args, session_id=session_id,
                         task_id=task_id, tool_call_id=tool_call_id)
        return snapshot
    path = Path(args["contract_path"]).expanduser().resolve()
    raw = path.read_bytes()
    contract = json.loads(raw.decode("utf-8"))
    contract = _validate_contract(contract)
    plan = _case_plan(tool_name, args, raw=raw, contract=contract)
    digest = _sha256(_canonical(plan))
    _require_digest(digest, scopes=scopes, tool_call_id=tool_call_id)
    return {"tool": tool_name, "args": _snapshot_args(tool_name, args), "path": str(path),
            "contract": contract, "sha256": _sha256(raw), "digest": digest}


def prepare_approved_experiment(experiment_id: str, *, session_id: str = "",
                                task_id: str = "", session_key: str = "",
                                tool_call_id: str = "",
                                snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the frozen experiment contract and metadata for one execution."""
    args = {"experiment_id": experiment_id}
    scopes = _scopes(session_id=session_id, task_id=task_id, session_key=session_key) or [""]
    if snapshot is None:
        snapshot = _find_snapshot(_EXPERIMENT_SNAPSHOTS, "crashlab_experiment_run", args,
                                  session_id=session_id, task_id=task_id,
                                  tool_call_id=tool_call_id)
    if snapshot is not None:
        if snapshot.get("tool") != "crashlab_experiment_run" or snapshot.get("args") != args:
            raise ApprovalRequired("CrashLab approval belongs to a different invocation")
        _require_digest(snapshot["digest"], scopes=scopes, tool_call_id=tool_call_id)
        _retire_snapshot(_EXPERIMENT_SNAPSHOTS, "crashlab_experiment_run", args,
                         session_id=session_id, task_id=task_id, tool_call_id=tool_call_id)
        return snapshot
    snapshot = _experiment_snapshot(experiment_id)
    _require_digest(snapshot["digest"], scopes=scopes, tool_call_id=tool_call_id)
    return snapshot


def prepare_approved_campaign(args: dict[str, Any], *, session_id: str = "",
                              task_id: str = "", session_key: str = "",
                              tool_call_id: str = "",
                              snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the exact campaign contracts/target captured for this approval."""
    scopes = _scopes(session_id=session_id, task_id=task_id, session_key=session_key) or [""]
    if snapshot is None:
        snapshot = _find_snapshot(_CAMPAIGN_SNAPSHOTS, "crashlab_campaign_run", args,
                                  session_id=session_id, task_id=task_id,
                                  tool_call_id=tool_call_id)
    if snapshot is None:
        snapshot = _campaign_snapshot(args)
    if snapshot.get("tool") != "crashlab_campaign_run" or snapshot.get("args") != _snapshot_args("crashlab_campaign_run", args):
        raise ApprovalRequired("CrashLab approval belongs to a different invocation")
    _require_digest(snapshot["digest"], scopes=scopes, tool_call_id=tool_call_id)
    _retire_snapshot(_CAMPAIGN_SNAPSHOTS, "crashlab_campaign_run", args,
                     session_id=session_id, task_id=task_id, tool_call_id=tool_call_id)
    return snapshot


def reset_for_tests() -> None:
    with _LOCK:
        _REQUESTS.clear()
        _ONCE_GRANTS.clear()
        _ONCE_INDEX.clear()
        _SESSION_APPROVALS.clear()
        _PERMANENT_APPROVALS.clear()
        _CASE_SNAPSHOTS.clear()
        _EXPERIMENT_SNAPSHOTS.clear()
        _CAMPAIGN_SNAPSHOTS.clear()


def grant_for_operator(tool_name: str, args: dict[str, Any], *, session_id: str = "operator",
                       tool_call_id: str = "", snapshot: dict[str, Any] | None = None) -> str:
    """Create a short-lived grant after an explicit local operator confirmation."""
    if snapshot is None:
        snapshot = capture_plan(tool_name, args, session_id=session_id,
                                tool_call_id=tool_call_id)
    if tool_name in {"crashlab_run", "crashlab_compare"}:
        store = _CASE_SNAPSHOTS
    elif tool_name == "crashlab_experiment_run":
        store = _EXPERIMENT_SNAPSHOTS
    elif tool_name == "crashlab_campaign_run":
        store = _CAMPAIGN_SNAPSHOTS
    else:
        raise ValueError(f"not an execution tool: {tool_name}")
    digest = snapshot["digest"]
    now = time.time()
    _store_snapshot(store, tool_name, args, snapshot, session_id=session_id,
                    tool_call_id=tool_call_id, replace=True)
    with _LOCK:
        _REQUESTS[(digest, tool_call_id or "")] = now
        _grant_once([session_id], digest, now, tool_call_id)
    return digest


def grant_for_tests(tool_name: str, args: dict[str, Any], *, scope: str = "session") -> str:
    """Test helper mimicking a human approval response, never used in production."""
    if tool_name == "crashlab_campaign_run":
        try:
            snapshot = _campaign_snapshot(args)
            digest = snapshot["digest"]
            _store_snapshot(_CAMPAIGN_SNAPSHOTS, tool_name, args, snapshot,
                            session_id="test-session", replace=True)
        except Exception:
            digest = _sha256(_canonical({"tool": tool_name, "args": args}))
    else:
        digest = plan_digest(tool_name, args)
    now = time.time()
    with _LOCK:
        _REQUESTS[(digest, "")] = now
        if scope == "always":
            _PERMANENT_APPROVALS.add(digest)
        elif scope == "session":
            _SESSION_APPROVALS[("test-session", digest)] = now + LEASE_SECONDS
        else:
            _grant_once(["test-session"], digest, now)
    return digest
