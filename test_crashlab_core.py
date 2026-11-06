"""Comparison verdict regressions; no browser or fixture server is required."""
import copy
import unittest
from unittest.mock import patch

import crashlab_core as core


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.buggy = {
            "contract": "checkout-lost-response", "ok": False, "returncode": 1,
            "observed": 2, "expected": 1,
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

    def test_real_primary_failure_then_repair_passes(self):
        self.assertTrue(self.compare())

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
