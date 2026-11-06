"""CrashLab contract runner core."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
RUNNER = ROOT / "crashlab_runner.js"


def load_contract(path: str | Path) -> dict:
    contract = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    return validate_contract(contract)


def validate_contract(contract: dict) -> dict:
    if not isinstance(contract, dict):
        raise ValueError("contract must be an object")
    for key in ("id", "app", "journey", "state"):
        if key not in contract:
            raise ValueError(f"contract missing {key}")
    if not isinstance(contract["journey"], list) or not contract["journey"]:
        raise ValueError("journey must be a non-empty list")
    if not isinstance(contract["state"].get("expect"), (int, float)):
        raise ValueError("state.expect must be a number")
    if not contract["app"].get("base_url"):
        raise ValueError("app.base_url is required")
    return contract


def _node() -> str:
    node = os.environ.get("NODE_BINARY") or "node"
    return node


def run_contract(contract_path: str | Path, base_url: str | None = None,
                 timeout: int = 120) -> dict:
    contract = load_contract(contract_path)
    if base_url:
        contract["app"]["base_url"] = base_url
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


def compare(contract_path: str | Path, buggy_url: str, fixed_url: str,
            timeout: int = 120) -> dict:
    buggy = run_contract(contract_path, base_url=buggy_url, timeout=timeout)
    fixed = run_contract(contract_path, base_url=fixed_url, timeout=timeout)
    return {
        "contract": buggy.get("contract"),
        "buggy": buggy,
        "fixed": fixed,
        "ok": (not buggy.get("ok")) and bool(fixed.get("ok")),
    }


def list_contracts() -> list[dict]:
    rows = []
    for path in sorted((ROOT / "contracts").glob("*.json")):
        try:
            contract = load_contract(path)
            rows.append({"id": contract["id"], "path": str(path)})
        except Exception as exc:
            rows.append({"path": str(path), "error": str(exc)})
    return rows
