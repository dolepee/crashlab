"""Comparison verdict regressions; no browser or fixture server is required."""
import copy
import unittest
from pathlib import Path
from unittest.mock import patch

import crashlab_core as core


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.buggy = {
            "contract": "checkout-lost-response", "ok": False, "returncode": 1,
            "observed": 2, "expected": 1,
            "scenario": {"valid": True},
            "control": {"ok": True, "observed": 2, "expected": 2},
        }
        self.fixed = copy.deepcopy(self.buggy)
        self.fixed.update(ok=True, returncode=0, observed=1)

    def compare(self, buggy=None, fixed=None):
        runs = [self.buggy if buggy is None else buggy,
                self.fixed if fixed is None else fixed]
        with patch.object(core, "run_contract", side_effect=runs) as runner:
            result = core.compare("case.json", "http://buggy", "http://fixed")
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(result["buggy"], runs[0])
        self.assertEqual(result["fixed"], runs[1])
        return result["ok"]

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


if __name__ == "__main__":
    unittest.main()

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
