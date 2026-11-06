"""CrashLab contract runner core."""
from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
RUNNER = ROOT / "crashlab_runner.js"

def _data_dir() -> Path:
    return Path(os.environ.get("CRASHLAB_DATA_DIR", Path.home() / ".local" / "share" / "crashlab"))


def _persist(result: dict) -> dict:
    """Save a completed comparison outside the plugin directory and expose its id."""
    run_id = str(result.get("run_id") or f"compare-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}")
    result["run_id"] = run_id
    data = _data_dir()
    runs = data / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    path = runs / f"{run_id}.json"
    path.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    (data / "latest.json").write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    result["evidence_path"] = str(path)
    return result


def load_contract(path: str | Path) -> dict:
    contract = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    return validate_contract(contract)


def _validate_state(state: dict, where: str) -> None:
    if not isinstance(state, dict):
        raise ValueError(f"{where} must be an object")
    expected = state.get("expect")
    if type(expected) is not int or expected < 0:
        raise ValueError(f"{where}.expect must be a non-negative integer")
    mode = state.get("mode", "http")
    if mode not in ("http", "sqlite"):
        raise ValueError(f"{where}.mode must be http or sqlite")
    if mode == "http":
        if not isinstance(state.get("url"), str) or not state["url"]:
            raise ValueError(f"{where}.url is required")
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
        if action not in ("goto", "click", "wait_for_selector", "wait_for_text", "reload", "text", "screenshot"):
            raise ValueError(f"{name}.action is unsupported")
        if action in ("click", "wait_for_selector", "text"):
            if not isinstance(step.get("selector"), str) or not step["selector"]:
                raise ValueError(f"{name}.selector is required")
        if action == "wait_for_text" and not isinstance(step.get("text"), str):
            raise ValueError(f"{name}.text is required")
        if action == "goto" and step.get("path") is not None and not isinstance(step["path"], str):
            raise ValueError(f"{name}.path must be a string")


def validate_contract(contract: dict) -> dict:
    if not isinstance(contract, dict):
        raise ValueError("contract must be an object")
    for key in ("id", "app", "journey", "state"):
        if key not in contract:
            raise ValueError(f"contract missing {key}")
    if not isinstance(contract["app"], dict):
        raise ValueError("app must be an object")
    base = contract["app"].get("base_url")
    if not isinstance(base, str) or not base.startswith(("http://", "https://")):
        raise ValueError("app.base_url must be an http(s) URL")
    _validate_journey(contract["journey"], "journey")
    _validate_state(contract["state"], "state")
    fault = contract.get("fault")
    if fault is not None:
        if not isinstance(fault, dict):
            raise ValueError("fault must be an object")
        if fault.get("mode") != "suppress_after_upstream":
            raise ValueError("fault.mode must be suppress_after_upstream")
        if not isinstance(fault.get("route"), str) or not fault["route"]:
            raise ValueError("fault.route is required")
        if fault.get("occurrence", 1) != 1:
            raise ValueError("only fault.occurrence=1 is supported")
    control = contract.get("control")
    if control is not None:
        if not isinstance(control, dict):
            raise ValueError("control must be an object")
        _validate_journey(control.get("journey"), "control.journey")
        _validate_state(control.get("state"), "control.state")
    return contract


def _node() -> str:
    node = os.environ.get("NODE_BINARY") or "node"
    return node


def run_contract(contract_path: str | Path, base_url: str | None = None,
                 timeout: int = 120) -> dict:
    contract = load_contract(contract_path)
    if base_url:
        contract["app"]["base_url"] = base_url
    # The runner receives a temporary contract; make sqlite observer paths
    # relative to the contract the caller supplied, not to /tmp.
    contract_dir = Path(contract_path).expanduser().resolve().parent
    for state in (contract.get("state"), (contract.get("control") or {}).get("state")):
        if isinstance(state, dict) and state.get("mode") == "sqlite" and state.get("db"):
            db = Path(state["db"]).expanduser()
            if not db.is_absolute():
                state["db"] = str((contract_dir / db).resolve())
    fd, temp_path = tempfile.mkstemp(prefix="crashlab-contract-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(contract, fh)
        proc = subprocess.run(
            [_node(), str(RUNNER), temp_path],
            cwd=ROOT, capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "CHROME_PATH": os.environ.get("CHROME_PATH", "/usr/bin/chromium")},
        )
    finally:
        Path(temp_path).unlink(missing_ok=True)
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {
            "ok": False, "contract": contract.get("id"), "returncode": proc.returncode,
            "error": "runner did not return JSON",
            "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:],
        }
    result["returncode"] = proc.returncode
    if proc.returncode == 2:
        result["ok"] = False
    return result


def _completed_assertion(run: dict, *, passed: bool) -> bool:
    """Require a measured outcome from a fault that actually committed."""
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
    if (counts[0] == counts[1]) is not passed:
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
            timeout: int = 120) -> dict:
    buggy = run_contract(contract_path, base_url=buggy_url, timeout=timeout)
    fixed = run_contract(contract_path, base_url=fixed_url, timeout=timeout)
    result = {
        "contract": buggy.get("contract"),
        "run_id": f"compare-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}",
        "buggy": buggy,
        "fixed": fixed,
        "ok": _completed_assertion(buggy, passed=False) and _completed_assertion(fixed, passed=True),
    }
    return _persist(result)


def list_contracts() -> list[dict]:
    rows = []
    for path in sorted((ROOT / "contracts").glob("*.json")):
        try:
            contract = load_contract(path)
            rows.append({"id": contract["id"], "path": str(path)})
        except Exception as exc:
            rows.append({"path": str(path), "error": str(exc)})
    return rows
