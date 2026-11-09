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
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import crashlab_core as core  # noqa: E402

NAME = "CRASHLAB_KIST_ONE_INTENT"
CONTROL = "CRASHLAB_KIST_TWO_INTENTS"


def contract_for(app: Path, python: str) -> dict:
    def goal_steps(prefix: str) -> list[dict]:
        return [
            {"action": "fill", "selector": "#gName", "value": prefix},
            {"action": "fill", "selector": "#gTarget", "value": "1000"},
            {"action": "fill", "selector": "#gCurrent", "value": "100"},
            {"action": "fill", "selector": "#gMonthly", "value": "50"},
        ]

    return {
        "id": "kist-one-intent",
        "app": {
            "start": {
                "command": ["{PYTHON}", "server/app.py"],
                "python": python,
                "cwd": str(app),
                "env": {"FINANCE_PROJECT_DIR": "{DATA_DIR}"},
                "base_url": "http://127.0.0.1:{PORT}",
                "health_path": "/",
                "ready_timeout": 30,
                "setup": [{"method": "POST", "path": "/api/app-config", "json": {
                    "modules": {}, "wizard_completed": True, "base_currency": "USD"}}],
            }
        },
        "journey": [
            {"action": "goto", "path": "/#goals"},
            {"action": "wait_for_selector", "selector": "#gAdd"},
            *goal_steps(NAME),
            {"action": "click", "selector": "#gAdd"},
            {"action": "wait_for_fault"},
            {"action": "screenshot", "as": "lost-response"},
            {"action": "click", "selector": "#gAdd"},
            {"action": "wait_for_any", "conditions": [
                {"kind": "count", "selector": "#gList h3", "text": NAME, "min": 2},
                {"kind": "value", "selector": "#gName", "empty": True},
            ]},
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
            "db": "{DATA_DIR}/.finance/finance.db",
            "table": "goals",
            "where": f"name='{NAME}'",
            "expect": 1,
            "record_fields": {"target_amount": 1000, "current_amount": 100, "status": "active"},
        },
        "evidence": {"screenshot_dir": "{DATA_DIR}/shots"},
        "control": {
            "journey": [
                {"action": "goto", "path": "/#goals"},
                {"action": "wait_for_selector", "selector": "#gAdd"},
                *goal_steps(CONTROL),
                {"action": "click", "selector": "#gAdd"},
                {"action": "wait_for_count", "selector": "#gList h3", "text": CONTROL, "min": 1, "max": 1},
                {"action": "reload"},
                {"action": "wait_for_selector", "selector": "#gAdd"},
                *goal_steps(CONTROL),
                {"action": "click", "selector": "#gAdd"},
                {"action": "wait_for_count", "selector": "#gList h3", "text": CONTROL, "min": 2},
                {"action": "screenshot", "as": "two-intents"},
            ],
            "state": {
                "mode": "sqlite",
                "db": "{DATA_DIR}/.finance/finance.db",
                "table": "goals",
                "where": f"name='{CONTROL}'",
                "expect": 2,
                "record_fields": {"target_amount": 1000, "current_amount": 100, "status": "active"},
            },
        },
        "recovery": {
            "mode": "retry",
            "success_statuses": [201],
            "response_id_path": "id",
            "record_id_field": "id",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, type=Path, help="Kist checkout at 7a196af9")
    parser.add_argument("--python", default=sys.executable, help="Python used to run Kist")
    parser.add_argument("--output", type=Path, default=Path("kist-run"))
    parser.add_argument("--write-contract-only", action="store_true", help="Write the case for execution by the Hermes crashlab_run tool")
    parser.add_argument('--recovery-campaign', action='store_true', help='Test before delivery, after commit and restart with the database retained')
    args = parser.parse_args()
    app = args.app.expanduser().resolve()
    if not (app / "server/app.py").is_file():
        parser.error(f"not a Kist checkout: {app}")

    run = args.output.expanduser().resolve()
    run.mkdir(parents=True, exist_ok=True)
    contract_path = run / "contract.json"
    contract = contract_for(app, args.python)
    if args.recovery_campaign:
        contract['recovery_campaign'] = ['before_delivery','after_commit','restart_after_commit']
    contract_path.write_text(json.dumps(contract, indent=2) + "\n")
    if args.write_contract_only:
        print(str(contract_path))
        return 0
    data_dir = run / "crashlab-data"
    data_dir.mkdir(exist_ok=True)
    os.environ["CRASHLAB_DATA_DIR"] = str(data_dir)
    result = core.persist_run(core.run_contract(contract_path))
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


if __name__ == "__main__":
    raise SystemExit(main())
