"""Real loopback HTTP + independent SQLite regression checks, no mocks for findings."""
import copy
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import campaign
from inventory_fixture import make_server

ROOT = Path(__file__).resolve().parent


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.contract = json.loads((ROOT / "campaign_contracts/inventory-contention.json").read_text())

    def test_valid_contract(self):
        self.assertEqual(campaign.validate_http_contract(self.contract), self.contract)

    def test_unknown_mode_rejected(self):
        self.contract["fault"]["mode"] = "typo"
        with self.assertRaises(ValueError):
            campaign.validate_http_contract(self.contract)

    def test_one_request_is_not_concurrency(self):
        self.contract["fault"]["requests"].pop()
        with self.assertRaises(ValueError):
            campaign.validate_http_contract(self.contract)

    def test_boolean_or_nonfinite_expectations_rejected(self):
        for value in (True, None, float("nan"), float("inf"), "1"):
            with self.subTest(value=value):
                self.contract["invariants"][0]["equals"] = value
                with self.assertRaises(ValueError):
                    campaign.validate_http_contract(self.contract)

    def test_external_origins_credentials_and_paths_rejected(self):
        for base in ("http://example.com", "https://127.0.0.1", "http://user:secret@127.0.0.1", "http://127.0.0.1/x"):
            with self.subTest(base=base), self.assertRaises(ValueError):
                campaign.valid_base(base)
        for path in ("http://example.com", "//example.com", ""):
            with self.subTest(path=path), self.assertRaises(ValueError):
                campaign.valid_path(path)

    def test_control_is_required(self):
        self.contract.pop("control")
        with self.assertRaises(ValueError):
            campaign.validate_http_contract(self.contract)

    def test_duplicate_invariant_names_rejected(self):
        self.contract["invariants"].append(copy.deepcopy(self.contract["invariants"][0]))
        with self.assertRaises(ValueError):
            campaign.validate_http_contract(self.contract)

    def test_timeout_boolean_and_unbounded_values_rejected(self):
        for value in (True, 0, -1, 10000, float("nan")):
            self.contract["timeout_seconds"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                campaign.validate_http_contract(self.contract)


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "state.db"
        with sqlite3.connect(self.db) as con:
            con.execute("CREATE TABLE state (value INTEGER)")
            con.executemany("INSERT INTO state VALUES (?)", [(1,), (2,)])

    def query(self, query, expected=2):
        return campaign.observe(self.db, [{"id": "check", "source": "sqlite", "query": query, "equals": expected}])

    def test_real_read_only_scalar(self):
        self.assertTrue(self.query("SELECT COUNT(*) FROM state")[0]["ok"])

    def test_missing_database_cannot_be_created_or_pass(self):
        missing = Path(self.temp.name) / "missing.db"
        with self.assertRaises(sqlite3.OperationalError):
            campaign.observe(missing, [])
        self.assertFalse(missing.exists())

    def test_observer_cannot_mutate_attach_or_pragma(self):
        for query in ("DELETE FROM state", "UPDATE state SET value=0", "ATTACH DATABASE ':memory:' AS evil", "PRAGMA journal_mode=DELETE"):
            with self.subTest(query=query), self.assertRaises(sqlite3.DatabaseError):
                self.query(query)
        self.assertTrue(self.query("SELECT COUNT(*) FROM state")[0]["ok"])

    def test_wrong_shape_and_null_cannot_pass(self):
        for query in ("SELECT value FROM state", "SELECT 1,2", "SELECT NULL", "SELECT '2'", "SELECT value FROM state WHERE 0"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.query(query)


class HttpIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.contract = json.loads((ROOT / "campaign_contracts/inventory-contention.json").read_text())
        self.root = Path(self.temp.name)

    def target(self, mode):
        db = self.root / (mode + ".db")
        server = make_server(db, mode)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.contract["app"]["base_url"] = f"http://127.0.0.1:{server.server_port}"
        self.contract["state"]["db"] = str(db)
        return db

    def run_case(self):
        return campaign.run_http(self.contract, self.root)

    def test_buggy_oversells_while_control_still_passes(self):
        self.target("buggy")
        result = self.run_case()
        self.assertEqual(result["status"], "FAIL", result)
        self.assertTrue(result["scenario"]["valid"])
        values = {v["id"]: v for v in result["primary"]}
        self.assertEqual(values["one-reservation"]["observed"], 2)
        self.assertEqual(values["stock-conservation"]["observed"], 3)
        self.assertTrue(all(v["ok"] for v in result["control"]))

    def test_fixed_serializes_last_unit_without_rejecting_legitimate_orders(self):
        self.target("fixed")
        result = self.run_case()
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(sorted(r["status"] for r in result["responses"]), [201, 409])
        self.assertTrue(all(v["ok"] for v in result["control"]))

    def test_reject_all_repair_fails_control(self):
        self.target("reject_all")
        result = self.run_case()
        self.assertEqual(result["status"], "FAIL", result)
        self.assertFalse(all(v["ok"] for v in result["control"]))

    def test_ten_repeated_buggy_and_fixed_experiments(self):
        self.target("buggy")
        buggy_contract = copy.deepcopy(self.contract)
        self.target("fixed")
        for index in range(10):
            with self.subTest(repetition=index):
                self.assertEqual(campaign.run_http(buggy_contract, self.root)["status"], "FAIL")
                self.assertEqual(self.run_case()["status"], "PASS")

    def test_dirty_baseline_is_invalid_not_application_failure(self):
        self.target("fixed")
        self.contract["baseline"][0]["equals"] = 1
        result = self.run_case()
        self.assertEqual(result["status"], "INVALID", result)
        self.assertNotIn("responses", result)

    def test_failed_reset_is_error(self):
        self.target("fixed")
        self.contract["app"]["reset_url"] = "/missing"
        self.assertEqual(self.run_case()["status"], "ERROR")

    def test_unreleased_fixture_gate_is_invalid_not_a_reproduced_bug(self):
        self.target("buggy")
        for request in self.contract["fault"]["requests"]:
            request["json"]["test_participants"] = 3
        self.contract["fault"]["required_response_fields"]["_crashlab_gate.participants"] = 3
        result = self.run_case()
        self.assertEqual(result["status"], "INVALID", result)
        self.assertFalse(result["scenario"]["valid"])
        self.assertEqual([r["status"] for r in result["responses"]], [503, 503])
        self.assertTrue(any(not c["ok"] for c in result["scenario"]["response_checkpoints"]))

    def test_missing_fixture_gate_evidence_is_invalid(self):
        self.target("fixed")
        self.contract["fault"]["required_response_fields"]["missing.proof"] = True
        result = self.run_case()
        self.assertEqual(result["status"], "INVALID", result)
        self.assertFalse(result["scenario"]["valid"])

    def test_wrong_database_is_error_not_pass(self):
        self.target("fixed")
        self.contract["state"]["db"] = str(self.root / "absent.db")
        self.assertEqual(self.run_case()["status"], "ERROR")

    def test_broken_observer_is_error_not_failed_invariant(self):
        self.target("fixed")
        self.contract["invariants"][0]["query"] = "SELECT missing_column FROM inventory"
        self.assertEqual(self.run_case()["status"], "ERROR")

    def test_transport_failure_is_error(self):
        self.target("fixed")
        actual = campaign.request

        def broken(base, spec, timeout):
            if spec["path"] == "/reserve":
                return {"status": None, "error": "simulated transport failure", "started_ns": 0, "ended_ns": 1}
            return actual(base, spec, timeout)

        with patch.object(campaign, "request", side_effect=broken):
            self.assertEqual(self.run_case()["status"], "ERROR")

    def test_nonoverlapping_requests_are_invalid(self):
        self.target("fixed")
        actual = campaign.request
        index = iter(range(10))

        def serialized(base, spec, timeout):
            if spec["path"] == "/reserve":
                n = next(index)
                return {"status": 201, "body": {}, "started_ns": 2 * n, "ended_ns": 2 * n + 1}
            return actual(base, spec, timeout)

        with patch.object(campaign, "request", side_effect=serialized):
            self.assertEqual(self.run_case()["status"], "INVALID")

    def test_primary_database_survives_control_reset(self):
        self.target("buggy")
        result = campaign.run_http(self.contract, self.root, self.root / "snapshots")
        self.assertEqual(result["status"], "FAIL", result)
        with sqlite3.connect(result["primary_snapshot"]) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM reservations WHERE sku='widget'").fetchone()[0], 2)
        with sqlite3.connect(result["control_snapshot"]) as con:
            self.assertEqual(con.execute("SELECT COUNT(DISTINCT sku) FROM reservations").fetchone()[0], 2)

    def test_campaign_continues_after_error_and_saves_each_result(self):
        self.target("fixed")
        good = self.root / "good.json"
        good.write_text(json.dumps(self.contract))
        manifest = {"schema_version": 1, "id": "mixed-validity", "cases": [
            {"id": "missing", "contract": "absent.json", "engine": "http_concurrency"},
            {"id": "fixed", "contract": "good.json", "engine": "http_concurrency", "repetitions": 2}]}
        path = self.root / "manifest.json"
        path.write_text(json.dumps(manifest))
        result = campaign.run_campaign(path, self.root / "output")
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["counts"], {"PASS": 2, "FAIL": 0, "INVALID": 0, "ERROR": 1})
        self.assertEqual(len(result["runs"]), 3)
        for row in result["runs"]:
            self.assertTrue(Path(row["evidence_path"]).exists())

    def test_manifest_rejects_invalid_repetition_before_reset(self):
        path = self.root / "bad.json"
        path.write_text(json.dumps({"schema_version": 1, "cases": [
            {"id": "case", "engine": "http_concurrency", "contract": "any.json", "repetitions": True}]}))
        with patch.object(campaign, "request") as request:
            with self.assertRaises(ValueError):
                campaign.run_campaign(path, self.root / "output")
            request.assert_not_called()


class LegacyBridgeTests(unittest.TestCase):
    def setUp(self):
        self.run = {"ok": True, "observed": 1, "expected": 1, "returncode": 0,
                    "scenario": {"valid": True}, "control": {"ok": True, "observed": 2, "expected": 2}}

    def test_pass_and_measured_failure(self):
        self.assertEqual(campaign.classify_legacy(self.run), "PASS")
        self.run.update(ok=False, observed=2, returncode=1)
        self.assertEqual(campaign.classify_legacy(self.run), "FAIL")

    def test_crash_and_invalid_fault_are_not_failures(self):
        self.run["returncode"] = 2
        self.assertEqual(campaign.classify_legacy(self.run), "ERROR")
        self.run["returncode"] = 0
        self.run["scenario"]["valid"] = False
        self.assertEqual(campaign.classify_legacy(self.run), "INVALID")

    def test_fabricated_success_flag_cannot_hide_mismatch(self):
        self.run["observed"] = 2
        self.assertEqual(campaign.classify_legacy(self.run), "ERROR")

    def test_nonfinite_and_boolean_measurements_are_errors(self):
        for value in (True, float("nan"), float("inf"), "1", None):
            self.run["observed"] = value
            with self.subTest(value=value):
                self.assertEqual(campaign.classify_legacy(self.run), "ERROR")


class DemoVerdictTests(unittest.TestCase):
    def results(self, legacy=False):
        result = {}
        for mode, status in (("buggy", "FAIL"), ("fixed", "PASS"), ("reject_all", "FAIL")):
            rows = [{"case": "inventory-last-unit", "repetition": n, "status": status} for n in (1, 2)]
            if legacy and mode != "reject_all":
                rows.append({"case": "checkout-lost-response", "repetition": 1, "status": status})
            result[mode] = {"status": status, "runs": rows}
        return result

    def test_every_case_and_repetition_must_meet_expectations(self):
        from demo_campaign import verify_demo
        results = self.results()
        self.assertEqual(verify_demo(results, 2, False), [])
        results["buggy"]["runs"][1]["status"] = "PASS"
        self.assertEqual(len(verify_demo(results, 2, False)), 1)

    def test_inventory_failure_cannot_hide_unreproduced_legacy_case(self):
        from demo_campaign import verify_demo
        results = self.results(legacy=True)
        results["buggy"]["runs"][-1]["status"] = "PASS"
        self.assertEqual(len(verify_demo(results, 2, True)), 1)

    def test_missing_or_duplicate_repetition_is_rejected(self):
        from demo_campaign import verify_demo
        results = self.results()
        results["fixed"]["runs"].pop()
        self.assertEqual(len(verify_demo(results, 2, False)), 1)
        results = self.results()
        results["fixed"]["runs"].append(results["fixed"]["runs"][0])
        self.assertEqual(len(verify_demo(results, 2, False)), 1)


if __name__ == "__main__":
    unittest.main()
