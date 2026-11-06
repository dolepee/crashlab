#!/usr/bin/env python3
"""Start a local Kist checkout and run the frozen CrashLab contract.

This is an external-app example, not a Hermes tool. It starts the pinned app
with an isolated data directory, writes the contract with that run's URL and
SQLite path, and calls CrashLab's browser runner through crashlab_core.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import crashlab_core as core  # noqa: E402
import plugin  # noqa: E402

NAME = "CRASHLAB_KIST_ONE_INTENT"
CONTROL = "CRASHLAB_KIST_TWO_INTENTS"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_ready(base: str, proc: subprocess.Popen, log: Path) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"Kist exited {proc.returncode}:\n{log.read_text()[-4000:]}")
        try:
            with urllib.request.urlopen(base + "/", timeout=0.25) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("Kist did not start within 15 seconds")


def configure_app(base: str) -> None:
    body = json.dumps({"modules": {}, "wizard_completed": True, "base_currency": "USD"}).encode()
    request = urllib.request.Request(
        base + "/api/app-config", data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(f"Kist app-config HTTP {response.status}")


def contract_for(base: str, db: Path, shots: Path) -> dict:
    def goal_steps(prefix: str) -> list[dict]:
        return [
            {"action": "fill", "selector": "#gName", "value": prefix},
            {"action": "fill", "selector": "#gTarget", "value": "1000"},
            {"action": "fill", "selector": "#gCurrent", "value": "100"},
            {"action": "fill", "selector": "#gMonthly", "value": "50"},
        ]

    return {
        "id": "kist-one-intent",
        "app": {"base_url": base},
        "journey": [
            {"action": "goto", "path": "/#goals"},
            {"action": "wait_for_selector", "selector": "#gAdd"},
            *goal_steps(NAME),
            {"action": "click", "selector": "#gAdd"},
            {"action": "wait_for_function", "expression": "() => document.querySelector('#gName')?.value === 'CRASHLAB_KIST_ONE_INTENT'"},
            {"action": "screenshot", "as": "lost-response"},
            {"action": "click", "selector": "#gAdd"},
            {"action": "wait_for_function", "expression": "() => Array.from(document.querySelectorAll('#gList h3')).filter(x => x.textContent === 'CRASHLAB_KIST_ONE_INTENT').length === 2"},
            {"action": "screenshot", "as": "after-retry"},
        ],
        "fault": {
            "route": "**/api/goals",
            "method": "POST",
            "mode": "suppress_after_upstream",
            "occurrence": 1,
        },
        "state": {
            "mode": "sqlite",
            "db": str(db),
            "table": "goals",
            "where": f"name='{NAME}'",
            "expect": 1,
        },
        "evidence": {"screenshot_dir": str(shots)},
        "control": {
            "journey": [
                {"action": "goto", "path": "/#goals"},
                {"action": "wait_for_selector", "selector": "#gAdd"},
                *goal_steps(CONTROL),
                {"action": "click", "selector": "#gAdd"},
                {"action": "wait_for_function", "expression": "() => Array.from(document.querySelectorAll('#gList h3')).filter(x => x.textContent === 'CRASHLAB_KIST_TWO_INTENTS').length === 1"},
                {"action": "reload"},
                {"action": "wait_for_selector", "selector": "#gAdd"},
                *goal_steps(CONTROL),
                {"action": "click", "selector": "#gAdd"},
                {"action": "wait_for_function", "expression": "() => Array.from(document.querySelectorAll('#gList h3')).filter(x => x.textContent === 'CRASHLAB_KIST_TWO_INTENTS').length === 2"},
                {"action": "screenshot", "as": "two-intents"},
            ],
            "state": {
                "mode": "sqlite",
                "db": str(db),
                "table": "goals",
                "where": f"name='{CONTROL}'",
                "expect": 2,
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, type=Path, help="Kist checkout at 7a196af9")
    parser.add_argument("--python", default=sys.executable, help="Python used to run Kist")
    parser.add_argument("--output", type=Path, default=Path("kist-run"))
    args = parser.parse_args()
    app = args.app.expanduser().resolve()
    if not (app / "server/app.py").is_file():
        parser.error(f"not a Kist checkout: {app}")

    run = args.output.expanduser().resolve()
    run.mkdir(parents=True, exist_ok=True)
    (run / "home").mkdir(exist_ok=True)
    data = run / "data"
    data.mkdir(exist_ok=True)
    db = data / ".finance/finance.db"
    base = f"http://127.0.0.1:{free_port()}"
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(run / "home"),
        "FINANCE_PROJECT_DIR": str(data),
        "PORT": base.rsplit(":", 1)[1],
        "PYTHONUNBUFFERED": "1",
        "LANG": "C.UTF-8",
        "TZ": "UTC",
    }
    log = (run / "server.log").open("w")
    proc = subprocess.Popen(
        [args.python, str(app / "server/app.py")],
        cwd=app, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    contract_path = run / "contract.json"
    try:
        wait_ready(base, proc, run / "server.log")
        configure_app(base)
        contract = contract_for(base, db, run / "shots")
        contract_path.write_text(json.dumps(contract, indent=2) + "\n")
        # Exercise the registered crashlab_run path, not just the core helper.
        data_dir = run / "crashlab-data"
        data_dir.mkdir(exist_ok=True)
        os.environ["CRASHLAB_DATA_DIR"] = str(data_dir)
        raw = plugin._handler("crashlab_run")({"contract_path": str(contract_path)})
        result = json.loads(raw)
        (run / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        summary = {
            "run": str(run),
            "ok": result.get("ok"),
            "observed": result.get("observed"),
            "expected": result.get("expected"),
            "scenario": result.get("scenario"),
            "control": result.get("control"),
            "error": result.get("error"),
        }
        print(json.dumps(summary, indent=2))
        return 0 if result.get("ok") else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
