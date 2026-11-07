"""Comparison verdict regressions; no browser or fixture server is required."""
import copy
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import crashlab_core as core


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.buggy = {
            "contract": "checkout-lost-response", "ok": False, "returncode": 1,
            "observed": 2, "expected": 1, "primary_ok": False,
            "scenario": {"valid": True},
            "control": {"ok": True, "observed": 2, "expected": 2},
        }
        self.fixed = copy.deepcopy(self.buggy)
        self.fixed.update(ok=True, returncode=0, observed=1, primary_ok=True)

    def compare(self, buggy=None, fixed=None):
        runs = [self.buggy if buggy is None else buggy,
                self.fixed if fixed is None else fixed]
        with patch.object(core, "load_contract", return_value={"id": "test"}), patch.object(core, "run_contract", side_effect=runs) as runner:
            result = core.compare("case.json", "http://buggy", "http://fixed")
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(result["buggy"], runs[0])
        self.assertEqual(result["fixed"], runs[1])
        return result["ok"]

    def test_contract_is_loaded_once_and_shared_across_pair(self):
        contract = {"id": "frozen", "state": {"expect": 1}}
        with patch.object(core, "load_contract", return_value=contract) as load, \
             patch.object(core, "run_contract", side_effect=[self.buggy, self.fixed]) as run:
            result = core.compare("case.json", "http://buggy", "http://fixed")
        load.assert_called_once_with("case.json")
        self.assertEqual(run.call_args_list[0].kwargs["frozen_contract"], contract)
        self.assertEqual(run.call_args_list[1].kwargs["frozen_contract"], contract)
        self.assertEqual(result["buggy"]["contract_sha256"], result["fixed"]["contract_sha256"])
        self.assertEqual(len(result["contract_sha256"]), 64)

    def test_expand_start_uses_configured_python(self):
        value = core._expand_start(
            "{PYTHON} -m flask",
            contract_dir=Path("/app"),
            data=Path("/data"),
            port=1234,
            python="/opt/app/bin/python",
        )
        self.assertEqual(value, "/opt/app/bin/python -m flask")

    def test_real_primary_failure_then_repair_passes(self):
        self.assertTrue(self.compare())

    def test_content_only_failure_then_repair_passes(self):
        self.buggy["observed"] = 1
        self.assertTrue(self.compare())

    def test_missing_primary_verdict_is_not_a_reproduction(self):
        self.buggy.pop("primary_ok")
        self.assertFalse(self.compare())

    def test_invalid_scenario_does_not_pass(self):
        for target in ("buggy", "fixed"):
            with self.subTest(target=target):
                self.setUp()
                getattr(self, target)["scenario"] = {"valid": False}
                self.assertFalse(self.compare())

    def test_missing_scenario_does_not_pass(self):
        self.buggy.pop("scenario")
        self.assertFalse(self.compare())

    def test_runner_errors_do_not_count_as_reproduction(self):
        for code in (1, 2, 3, -9):
            with self.subTest(returncode=code):
                self.assertFalse(self.compare(buggy={
                    "ok": False, "returncode": code,
                    "error": "runner did not return JSON",
                }))

    def test_runner_error_with_counts_is_not_reproduction(self):
        self.buggy["error"] = "state probe failed"
        self.assertFalse(self.compare())

    def test_fixed_infrastructure_failure_does_not_pass(self):
        for code in (1, 2, 3, -9):
            with self.subTest(returncode=code):
                self.fixed["returncode"] = code
                self.assertFalse(self.compare())

    def test_control_only_failure_is_not_reproduction(self):
        self.buggy["observed"] = 1
        self.buggy["primary_ok"] = True
        self.buggy["control"].update(ok=False, observed=1)
        self.assertFalse(self.compare())

    def test_failed_controls_do_not_pass(self):
        for target in ("buggy", "fixed"):
            with self.subTest(target=target):
                self.setUp()
                getattr(self, target)["control"].update(ok=False, observed=1)
                self.assertFalse(self.compare())

    def test_control_status_alone_is_insufficient(self):
        for control in ({"ok": True}, {"ok": True, "observed": 1, "expected": 2}):
            with self.subTest(control=control):
                self.fixed["control"] = control
                self.assertFalse(self.compare())

    def test_both_pass_is_not_a_repair(self):
        self.assertFalse(self.compare(buggy=copy.deepcopy(self.fixed)))

    def test_both_fail_is_not_a_repair(self):
        self.assertFalse(self.compare(fixed=copy.deepcopy(self.buggy)))

    def test_missing_or_invalid_measurements_do_not_pass(self):
        for target in ("buggy", "fixed"):
            for value in (None, "1", True, float("nan"), float("inf"), -1):
                with self.subTest(target=target, value=value):
                    self.setUp()
                    getattr(self, target)["observed"] = value
                    self.assertFalse(self.compare())

    def test_fixed_flag_cannot_hide_primary_mismatch(self):
        self.fixed["observed"] = 2
        self.assertFalse(self.compare())

    def test_contract_without_control_is_supported(self):
        self.buggy["control"] = self.fixed["control"] = None
        self.assertTrue(self.compare())


class ContractValidationTests(unittest.TestCase):
    def base(self):
        return {
            "id": "case", "app": {"base_url": "http://127.0.0.1:1"},
            "journey": [{"action": "goto", "path": "/"}],
            "state": {"url": "/state", "path": "rows", "expect": 1},
        }

    def test_rejects_unknown_fault_mode(self):
        value = self.base(); value["fault"] = {"route": "**/x", "mode": "typo", "occurrence": 1}
        with self.assertRaises(ValueError): core.validate_contract(value)

    def test_rejects_unsupported_occurrence(self):
        value = self.base(); value["fault"] = {"route": "**/x", "mode": "suppress_after_upstream", "occurrence": 2}
        with self.assertRaises(ValueError): core.validate_contract(value)

    def test_fault_method_is_optional_but_typed(self):
        value = self.base(); value["fault"] = {"route": "**/x", "method": "POST", "mode": "suppress_after_upstream"}
        core.validate_contract(value)
        value["fault"]["method"] = "TRACE"
        with self.assertRaises(ValueError): core.validate_contract(value)

    def test_rejects_unknown_action(self):
        value = self.base(); value["journey"] = [{"action": "teleport"}]
        with self.assertRaises(ValueError): core.validate_contract(value)

    def test_rejects_removed_wait_for_function(self):
        value = self.base()
        value["journey"] = [{"action": "wait_for_function", "expression": "() => true"}]
        with self.assertRaises(ValueError): core.validate_contract(value)

    def test_accepts_declarative_waits(self):
        value = self.base()
        value["journey"] = [
            {"action": "wait_for_fault"},
            {"action": "wait_for_count", "selector": "#list h3", "text": "x", "min": 1, "max": 2},
            {"action": "wait_for_value", "selector": "#name", "empty": True},
            {"action": "wait_for_any", "conditions": [
                {"kind": "count", "selector": "#list h3", "min": 2},
                {"kind": "value", "selector": "#name", "equals": ""},
            ]},
        ]
        core.validate_contract(value)

    def test_rejects_bad_declarative_wait(self):
        value = self.base()
        value["journey"] = [{"action": "wait_for_any", "conditions": []}]
        with self.assertRaises(ValueError): core.validate_contract(value)

    def test_accepts_supported_interaction_actions(self):
        value = self.base()
        value["journey"] = [
            {"action": "fill", "selector": "#name", "value": "x"},
            {"action": "check", "selector": "#agree"},
            {"action": "uncheck", "selector": "#extra"},
            {"action": "select_option", "selector": "#currency", "value": "USD"},
            {"action": "press", "selector": "#name", "key": "Enter"},
            {"action": "wait_for_timeout", "ms": 100},
            {"action": "wait_for_url", "pattern": "**/#done"},
        ]
        core.validate_contract(value)

    def test_rejects_incomplete_interaction_actions(self):
        for step in (
            {"action": "fill", "selector": "#name"},
            {"action": "select_option", "selector": "#currency"},
            {"action": "press", "selector": "#name"},
            {"action": "wait_for_timeout", "ms": True},
            {"action": "wait_for_url"},
        ):
            with self.subTest(step=step):
                value = self.base(); value["journey"] = [step]
                with self.assertRaises(ValueError): core.validate_contract(value)

    def test_rejects_bad_expected_counts(self):
        for expected in (-1, True, 1.5, "1"):
            with self.subTest(expected=expected):
                value = self.base(); value["state"]["expect"] = expected
                with self.assertRaises(ValueError): core.validate_contract(value)

    def test_sqlite_requires_db(self):
        value = self.base(); value["state"] = {"mode": "sqlite", "table": "orders", "expect": 1}
        with self.assertRaises(ValueError): core.validate_contract(value)

    def test_accepts_strict_contract(self):
        core.validate_contract(self.base())

    def test_accepts_local_app_start_without_base_url(self):
        value = self.base()
        del value["app"]["base_url"]
        value["app"]["start"] = {
            "command": ["{PYTHON}", "server.py"],
            "cwd": "{APP_DIR}",
            "base_url": "http://127.0.0.1:{PORT}",
            "setup": [{"method": "POST", "path": "/api/config", "json": {"ok": True}}],
        }
        core.validate_contract(value)

    def test_requires_base_url_or_start(self):
        value = self.base()
        del value["app"]["base_url"]
        with self.assertRaises(ValueError):
            core.validate_contract(value)

    def test_rejects_non_object_start(self):
        value = self.base()
        value["app"]["start"] = "python app.py"
        with self.assertRaises(ValueError):
            core.validate_contract(value)


class AppCleanupTests(unittest.TestCase):
    """The app process must not survive a malformed setup or a completed run."""

    APP = """\
import http.server, os, socketserver

class Handler(http.server.BaseHTTPRequestHandler):
    def _ok(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")
    def do_GET(self):
        self._ok()
    def do_POST(self):
        self._ok()
    def log_message(self, *args):
        pass

with open(os.environ["PID_FILE"], "w", encoding="utf-8") as handle:
    handle.write(str(os.getpid()))
with socketserver.TCPServer(("127.0.0.1", int(os.environ["PORT"])), Handler) as server:
    server.serve_forever()
"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="crashlab-cleanup-"))
        self.script = self.tmp / "app.py"
        self.script.write_text(self.APP, encoding="utf-8")
        self.pid_file = self.tmp / "pid.txt"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, extra=None):
        start = {
            "command": [sys.executable, str(self.script)],
            "python": sys.executable,
            "cwd": str(self.tmp),
            "health_path": "/",
            "ready_timeout": 5,
            "env": {"PID_FILE": str(self.pid_file)},
        }
        start.update(extra or {})
        return {"id": "cleanup", "app": {"start": start}, "journey": [{"action": "wait_for_timeout", "ms": 0}], "state": {"mode": "http", "url": "http://127.0.0.1:1", "path": "/state", "expect": 1}}

    def wait_gone(self):
        pid = int(self.pid_file.read_text(encoding="utf-8"))
        for _ in range(100):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        self.fail(f"app process {pid} survived cleanup")

    def test_malformed_setup_stops_the_started_app(self):
        with self.assertRaises(ValueError):
            core._start_app(self.start({"setup": [{}]}), self.tmp)
        self.wait_gone()

    def test_completed_run_stops_the_started_app(self):
        runner = self.tmp / "runner.js"
        runner.write_text(
            "process.stdout.write(JSON.stringify({ok:true,contract:'cleanup',"
            "observed:1,expected:1,scenario:{valid:true}}))\n",
            encoding="utf-8",
        )
        contract_path = self.tmp / "case.json"
        contract_path.write_text(json.dumps(self.start({"setup": [{"path": "/setup", "method": "POST"}]})), encoding="utf-8")
        with patch.object(core, "RUNNER", runner):
            result = core.run_contract(contract_path)
        self.assertTrue(result["ok"], result)
        self.wait_gone()

    CHILD = """\
import os, signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN if sys.argv[2] == "1" else signal.SIG_DFL)
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    handle.write(str(os.getpid()))
while True:
    time.sleep(0.1)
"""

    SPAWN_EXIT = """\
import os, subprocess, sys, time
subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2], sys.argv[3]])
while not os.path.exists(sys.argv[2]):
    time.sleep(0.01)
"""

    SPAWN_SERVE = """\
import http.server, os, socketserver, subprocess, sys, time

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")
    def log_message(self, *args):
        pass

subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2], sys.argv[3]])
while not os.path.exists(sys.argv[2]):
    time.sleep(0.01)
with socketserver.TCPServer(("127.0.0.1", int(os.environ["PORT"])), Handler) as server:
    server.serve_forever()
"""

    def _script(self, name, text):
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_child_outliving_parent_is_stopped(self):
        child = self._script("child.py", self.CHILD)
        parent = self._script("parent_exit.py", self.SPAWN_EXIT)
        start = self.start({"command": [sys.executable, str(parent), str(child), str(self.pid_file), "0"]})
        with self.assertRaises(RuntimeError):
            core._start_app(start, self.tmp)
        self.wait_gone()

    def test_child_ignoring_sigterm_is_stopped(self):
        child = self._script("child.py", self.CHILD)
        parent = self._script("parent_serve.py", self.SPAWN_SERVE)
        start = self.start({"command": [sys.executable, str(parent), str(child), str(self.pid_file), "1"]})
        proc, base, log, handle, data = core._start_app(start, self.tmp)
        try:
            core._stop_process(proc)
        finally:
            handle.close()
        self.wait_gone()

    def test_cleanup_without_children_is_fast(self):
        start = self.start()
        proc, base, log, handle, data = core._start_app(start, self.tmp)
        started = time.monotonic()
        try:
            core._stop_process(proc)
        finally:
            handle.close()
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 3.0)
        self.wait_gone()


if __name__ == "__main__":
    unittest.main()


class ControlIdentityValidationTests(unittest.TestCase):
    def test_fixed_payload_cannot_be_a_two_intent_identity_signature(self):
        case = json.loads((core.ROOT / 'cases/checkout-local-fixed.json').read_text())
        case['control']['state']['request_fields'] = {'item':'item'}
        with self.assertRaisesRegex(ValueError, 'fixed identity signature'):
            core.validate_contract(case)
        # A cardinality/content control without per-intent identity remains explicit and valid.
        case['control']['state'].pop('request_fields')
        core.validate_contract(case)
