"""CrashLab contract runner core."""
from __future__ import annotations

import json
import math
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
RUNNER = ROOT / "crashlab_runner.js"

def _data_dir() -> Path:
    return Path(os.environ.get("CRASHLAB_DATA_DIR", Path.home() / ".local" / "share" / "crashlab"))


def persist_run(result: dict) -> dict:
    """Save one completed run outside the plugin directory for the Desktop pane."""
    run_id = str(result.get("run_id") or f"run-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}")
    result["run_id"] = run_id
    data = _data_dir()
    runs = data / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    path = runs / f"{run_id}.json"
    raw = json.dumps(result, indent=2, default=str) + "\n"
    path.write_text(raw, encoding="utf-8")
    (data / "latest-run.json").write_text(raw, encoding="utf-8")
    result["evidence_path"] = str(path)
    return result


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
        if action not in ("goto", "click", "fill", "check", "uncheck", "select_option",
                         "press", "wait_for_selector", "wait_for_text", "wait_for_url",
                         "wait_for_fault", "wait_for_count", "wait_for_value", "wait_for_any",
                         "wait_for_load_state", "wait_for_timeout",
                         "reload", "text", "screenshot"):
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
        if action == "wait_for_load_state" and step.get("state") not in ("load", "domcontentloaded", "networkidle"):
            raise ValueError(f"{name}.state must be load, domcontentloaded or networkidle")
        if action == "wait_for_timeout":
            ms = step.get("ms")
            if type(ms) is not int or not 0 <= ms <= 60000:
                raise ValueError(f"{name}.ms must be an integer from 0 to 60000")
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
    start = contract["app"].get("start")
    if base is None and start is None:
        raise ValueError("app.base_url or app.start is required")
    if base is not None and (not isinstance(base, str) or not base.startswith(("http://", "https://"))):
        raise ValueError("app.base_url must be an http(s) URL")
    if start is not None and not isinstance(start, dict):
        raise ValueError("app.start must be an object")
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
        if fault.get("method") is not None and fault["method"] not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            raise ValueError("fault.method must be an HTTP method")
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


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _expand_start(value: Any, *, contract_dir: Path, data: Path, port: int) -> Any:
    """Expand the small, documented local-app placeholders."""
    if isinstance(value, str):
        return (value.replace("{APP_DIR}", str(contract_dir))
                .replace("{ROOT}", str(ROOT))
                .replace("{DATA_DIR}", str(data))
                .replace("{PORT}", str(port))
                .replace("{PYTHON}", sys.executable)
                .replace("{HOME}", str(data / "home")))
    if isinstance(value, list):
        return [_expand_start(item, contract_dir=contract_dir, data=data, port=port) for item in value]
    if isinstance(value, dict):
        return {key: _expand_start(item, contract_dir=contract_dir, data=data, port=port)
                for key, item in value.items()}
    return value


def _start_app(contract: dict, contract_dir: Path) -> tuple[subprocess.Popen, str, Path, Any, Path]:
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
    command = [item.replace("{PYTHON}", app_python) for item in command]
    port = start.get("port")
    if port is None:
        port = _free_port()
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("app.start.port must be an integer port")
    data = Path(tempfile.mkdtemp(prefix="crashlab-app-"))
    cwd = Path(_expand_start(start.get("cwd", "."), contract_dir=contract_dir, data=data, port=port)).expanduser()
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
    log_handle = log.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [_expand_start(item, contract_dir=contract_dir, data=data, port=port) for item in command],
        cwd=cwd, env=env, stdout=log_handle, stderr=subprocess.STDOUT,
    )
    base = str(_expand_start(start.get("base_url") or f"http://127.0.0.1:{port}",
                             contract_dir=contract_dir, data=data, port=port))
    health = str(start.get("health_path", "/"))
    timeout_s = float(start.get("ready_timeout", 30))
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log_handle.close()
            text = log.read_text(encoding="utf-8", errors="replace")
            raise RuntimeError(f"app exited before readiness ({proc.returncode}): {text[-2000:]}")
        try:
            with urllib.request.urlopen(base.rstrip("/") + health, timeout=0.5) as response:
                if response.status < 500:
                    for step in start.get("setup", []):
                        if not isinstance(step, dict) or not isinstance(step.get("path"), str):
                            raise ValueError("app.start.setup steps need a path")
                        payload = None
                        headers = {}
                        if "json" in step:
                            payload = json.dumps(step["json"]).encode("utf-8")
                            headers["Content-Type"] = "application/json"
                        elif "body" in step:
                            payload = str(step["body"]).encode("utf-8")
                        request = urllib.request.Request(
                            base.rstrip("/") + step["path"], data=payload,
                            headers=headers, method=str(step.get("method", "POST")),
                        )
                        with urllib.request.urlopen(request, timeout=5) as setup:
                            if not 200 <= setup.status < 300:
                                raise RuntimeError(f"app setup returned {setup.status}")
                    return proc, base, log, log_handle, data
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.1)
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    log_handle.close()
    raise RuntimeError(f"app did not become ready within {timeout_s}s: {log.read_text(encoding='utf-8', errors='replace')[-2000:]}")


def run_contract(contract_path: str | Path, base_url: str | None = None,
                 timeout: int = 120) -> dict:
    contract = load_contract(contract_path)
    contract_dir = Path(contract_path).expanduser().resolve().parent
    started = None
    try:
        if base_url:
            contract["app"]["base_url"] = base_url
        elif contract["app"].get("start"):
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
        evidence = contract.get("evidence")
        if started is not None and isinstance(evidence, dict) and evidence.get("screenshot_dir"):
            evidence["screenshot_dir"] = _expand_start(
                evidence["screenshot_dir"], contract_dir=contract_dir,
                data=app_data, port=0)
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
    finally:
        if started is not None:
            started.terminate()
            try:
                started.wait(timeout=5)
            except subprocess.TimeoutExpired:
                started.kill()
                started.wait()
            log_handle.close()
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
