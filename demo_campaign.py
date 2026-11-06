"""Start isolated fixtures and run reproducible buggy/fixed/reject-all campaigns."""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import ExitStack
from pathlib import Path

from campaign import ROOT, request, run_campaign, write_json
from inventory_fixture import make_server


def start_inventory(stack, folder, mode):
    db = folder / "inventory.db"
    server = make_server(db, mode)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stack.callback(server.server_close)
    stack.callback(server.shutdown)
    return f"http://127.0.0.1:{server.server_port}", str(db)


def start_checkout(stack, folder, buggy):
    db = folder / "checkout.db"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    log = stack.enter_context((folder / "checkout-server.log").open("w"))
    process = subprocess.Popen([sys.executable, str(ROOT / "server.py")],
        cwd=folder, env={**os.environ, "PORT": str(port), "BUGGY": str(int(buggy)), "CRASHLAB_DB": str(db)},
        stdout=log, stderr=subprocess.STDOUT)

    def stop():
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    stack.callback(stop)
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"checkout exited {process.returncode}: {folder / 'checkout-server.log'}")
        check = request(base, {"method": "POST", "path": "/reset", "json": {}}, 0.5)
        if check.get("status") == 200:
            return base, str(db)
        time.sleep(0.05)
    raise RuntimeError("checkout fixture readiness timed out")


def verify_demo(results, repetitions, with_legacy):
    expected_status = {"buggy": "FAIL", "fixed": "PASS", "reject_all": "FAIL"}
    mismatches = []
    for mode, status in expected_status.items():
        expected = {("inventory-last-unit", n): status for n in range(1, repetitions + 1)}
        if with_legacy and mode != "reject_all":
            expected[("checkout-lost-response", 1)] = status
        rows = results[mode]["runs"]
        actual = {}
        for row in rows:
            key = (row["case"], row["repetition"])
            if key in actual:
                mismatches.append({"mode": mode, "case": key[0], "repetition": key[1], "error": "duplicate result"})
            actual[key] = row["status"]
        for key in expected.keys() | actual.keys():
            if expected.get(key) != actual.get(key):
                mismatches.append({"mode": mode, "case": key[0], "repetition": key[1],
                                   "expected": expected.get(key), "actual": actual.get(key)})
    return mismatches


def demonstrate(output, repetitions=5, with_legacy=False):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    results = {}
    for mode in ("buggy", "fixed", "reject_all"):
        folder = output / mode
        folder.mkdir(parents=True, exist_ok=True)
        with ExitStack() as stack:
            base, db = start_inventory(stack, folder, mode)
            cases = [{"id": "inventory-last-unit", "engine": "http_concurrency",
                      "contract": str(ROOT / "campaign_contracts/inventory-contention.json"),
                      "base_url": base, "db": db, "repetitions": repetitions}]
            if with_legacy and mode != "reject_all":
                checkout_base, checkout_db = start_checkout(stack, folder, mode == "buggy")
                cases.insert(0, {"id": "checkout-lost-response", "engine": "legacy_browser",
                                 "contract": str(ROOT / "contracts/checkout-lost-response.json"),
                                 "base_url": checkout_base, "db": checkout_db, "repetitions": 1})
            manifest = folder / "campaign.json"
            write_json(manifest, {"schema_version": 1, "id": "reliability-" + mode, "cases": cases})
            results[mode] = run_campaign(manifest, folder / "evidence")
    # This checks the demonstration expectations; it is not the campaign's CI exit code.
    mismatches = verify_demo(results, repetitions, with_legacy)
    summary = {"demo_expectations_met": not mismatches, "mismatches": mismatches,
               "with_legacy": with_legacy, "repetitions": repetitions,
               "campaigns": {mode: {"status": r["status"], "counts": r["counts"],
                                    "summary": str(Path(r["evidence_dir"]) / "summary.json")}
                             for mode, r in results.items()}}
    write_json(output / "demo-summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="demo-evidence")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--with-legacy", action="store_true", help="requires Node, playwright-core and Chromium")
    args = parser.parse_args()
    result = demonstrate(args.output, args.repetitions, args.with_legacy)
    print(json.dumps(result, indent=2))
    return 0 if result["demo_expectations_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
