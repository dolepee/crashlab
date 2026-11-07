"""Registered-tool integration and trust/scope tests; never launches a browser."""
import copy
import hashlib
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import campaign_plugin as bridge
import plugin
from inventory_fixture import make_server


class RegistrationTests(unittest.TestCase):
    def test_all_tools_register_as_json_handlers(self):
        registered = {}
        class Context:
            def register_tool(self, **kwargs):
                registered[kwargs["name"]] = kwargs
        plugin.register(Context())
        expected = {"crashlab_validate", "crashlab_run", "crashlab_compare", "crashlab_list",
                    "crashlab_campaign_list", "crashlab_campaign_validate", "crashlab_campaign_run",
                    "crashlab_prepare", "crashlab_experiment_run", "crashlab_experiment_status", "crashlab_experiment_watch",
                    "crashlab_experiment_report", "crashlab_experiment_export", "crashlab_experiment_import"}
        self.assertEqual(set(registered), expected)
        for record in registered.values():
            self.assertEqual(record["toolset"], "crashlab")
            self.assertEqual(record["schema"]["name"], record["name"])
        listed = json.loads(registered["crashlab_campaign_list"]["handler"]({}))
        self.assertTrue(listed["ok"])
        self.assertEqual(len(listed["campaigns"]), 2)

    def test_yaml_matches_registered_tools(self):
        yaml = (bridge.ROOT / "plugin.yaml").read_text()
        declared = {line.strip()[2:] for line in yaml.splitlines() if line.startswith("  - crashlab_")}
        self.assertEqual(declared, {s["name"] for s in plugin.TOOL_SCHEMAS})

    def test_original_contract_listing_stays_valid(self):
        listed = plugin.core.list_contracts()
        self.assertTrue({"booking-lost-response", "checkout-lost-response"} <= {item["id"] for item in listed})
        self.assertTrue(any(item.get("auto_start") for item in listed))
        self.assertTrue(all("error" not in item for item in listed), listed)

    def test_campaign_schemas_expose_no_arbitrary_paths_or_sql(self):
        for schema in bridge.schemas():
            self.assertFalse(schema["parameters"]["additionalProperties"])
            self.assertLessEqual(set(schema["parameters"]["properties"]), {"campaign_id", "target_set", "repetitions"})


class CampaignPluginTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / "tool-data"
        self.data.mkdir()
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"CRASHLAB_DATA_DIR": str(self.data)}).start()

    def target(self, mode="fixed"):
        workspace = self.root / (mode + "-app")
        workspace.mkdir(exist_ok=True)
        server = make_server(workspace / "inventory.db", mode)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return {"base_url": f"http://127.0.0.1:{server.server_port}", "workspace": str(workspace),
                "db": "inventory.db", "disposable": True, "allow_reset": True}

    def configure(self, target, cases=None):
        value = {"schema_version": 1, "target_sets": {"local": {"cases": cases or {"inventory-last-unit": target}}}}
        (self.data / "campaign-targets.json").write_text(json.dumps(value))

    def invoke(self, name="crashlab_campaign_run", **extra):
        args = {"campaign_id": "inventory-contention", "target_set": "local", **extra}
        return json.loads(plugin._handler(name)(args))

    def test_read_only_validation_does_not_reset_or_create_evidence(self):
        self.configure(self.target())
        with patch.object(bridge.engine, "request") as request, patch.object(bridge.engine, "run_legacy") as legacy:
            result = self.invoke("crashlab_campaign_validate")
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["executed"])
        request.assert_not_called()
        legacy.assert_not_called()
        self.assertFalse((self.data / "campaign-results").exists())

    def test_validate_without_target_is_read_only(self):
        result = bridge.invoke("crashlab_campaign_validate", {"campaign_id": "inventory-contention"})
        self.assertTrue(result["valid"], result)
        self.assertIsNone(result["target_set"])

    def test_registered_handler_runs_fixed_real_http_and_keeps_old_latest(self):
        self.configure(self.target("fixed"))
        (self.data / "latest.json").write_text('{"old":"comparison"}')
        result = self.invoke(repetitions=3)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["counts"]["PASS"], 3)
        self.assertEqual(json.loads((self.data / "latest.json").read_text()), {"old": "comparison"})
        self.assertEqual(json.loads((self.data / "campaign-latest.json").read_text()), result)
        for row in result["runs"]:
            evidence = json.loads(Path(row["evidence_path"]).read_text())
            self.assertTrue(Path(evidence["primary_snapshot"]).is_file())
            self.assertTrue(Path(evidence["control_snapshot"]).is_file())
        self.assertEqual(set(result["evaluator_sha256"]), set(bridge.TRUSTED_FILES))
        self.assertEqual(list(self.data.glob("campaign-input-*")), [])
        self.assertFalse((self.data / "campaign-run.lock").exists())

    def test_buggy_and_reject_all_are_measured_failures(self):
        for mode in ("buggy", "reject_all"):
            with self.subTest(mode=mode):
                self.configure(self.target(mode))
                result = self.invoke(repetitions=2)
                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], "FAIL", result)
                self.assertEqual(result["counts"]["FAIL"], 2)
                evidence = json.loads(Path(result["runs"][0]["evidence_path"]).read_text())
                self.assertTrue(evidence["scenario"]["valid"])
                self.assertEqual(all(v["ok"] for v in evidence["control"]), mode == "buggy")

    def test_unknown_or_arbitrary_input_is_rejected_without_requests(self):
        for args in ({"campaign_id": "../../custom"}, {"manifest_path": "/tmp/evil.json"},
                     {"campaign_id": "inventory-contention", "legacy_root": "/tmp/evil"},
                     {"campaign_id": "inventory-contention", "repetitions": True},
                     {"campaign_id": "inventory-contention", "repetitions": 21}):
            with self.subTest(args=args), patch.object(bridge.engine, "request") as request:
                result = bridge.invoke("crashlab_campaign_run", args)
                self.assertEqual(result["error"]["code"], "INVALID_ARGUMENT")
                request.assert_not_called()

    def test_missing_target_configuration_is_error(self):
        result = self.invoke()
        self.assertEqual(result["status"], "ERROR")
        self.assertFalse(result["complete"])
        self.assertEqual(result["error"]["code"], "CONFIGURATION_ERROR")

    def test_no_implicit_reset_permission_or_external_target(self):
        good = self.target()
        for updates in ({"allow_reset": False}, {"disposable": False}, {"base_url": "http://example.com"},
                        {"base_url": "http://127.0.0.1:bad"}, {"base_url": "http://127.0.0.1:0"}):
            with self.subTest(updates=updates):
                self.configure({**good, **updates})
                with patch.object(bridge.engine, "request") as request:
                    result = self.invoke()
                self.assertEqual(result["error"]["code"], "UNSAFE_TARGET", result)
                request.assert_not_called()

    def test_database_escape_and_evaluator_workspace_rejected(self):
        good = self.target()
        outside = self.root / "outside.db"
        outside.touch()
        symlink = Path(good["workspace"]) / "linked.db"
        symlink.symlink_to(outside)
        for updates in ({"db": "../outside.db"}, {"db": str(outside)}, {"db": "linked.db"},
                        {"workspace": str(bridge.ROOT), "db": "README.md"}):
            with self.subTest(updates=updates), patch.object(bridge.engine, "request") as request:
                self.configure({**good, **updates})
                result = self.invoke()
                self.assertEqual(result["error"]["code"], "UNSAFE_TARGET", result)
                request.assert_not_called()

    def test_every_mixed_case_is_preflighted_before_any_reset(self):
        target = self.target()
        self.configure(target, cases={"checkout-lost-response": target})
        with patch.object(bridge.engine, "request") as request, patch.object(bridge.engine, "run_legacy") as legacy:
            result = self.invoke(campaign_id="checkout-and-inventory")
        self.assertEqual(result["error"]["code"], "CONFIGURATION_ERROR", result)
        self.assertIn("inventory-last-unit", result["error"]["message"])
        request.assert_not_called()
        legacy.assert_not_called()

    def test_browser_campaign_cannot_run_without_network_confinement(self):
        target = self.target()
        self.configure(target, cases={"checkout-lost-response": target, "inventory-last-unit": target})
        with patch.object(bridge.engine, "request") as request, patch.object(bridge.engine, "run_legacy") as legacy:
            result = self.invoke(campaign_id="checkout-and-inventory")
        self.assertEqual(result["error"]["code"], "UNSUPPORTED_ENGINE", result)
        request.assert_not_called()
        legacy.assert_not_called()

    def test_redirected_output_paths_are_rejected(self):
        self.configure(self.target())
        for name, destination in (("campaign-results", bridge.ROOT), ("campaign-latest.json", self.root / "outside.json")):
            link = self.data / name
            link.symlink_to(destination)
            with self.subTest(name=name), patch.object(bridge.engine, "request") as request:
                result = self.invoke()
                self.assertEqual(result["error"]["code"], "UNSAFE_TARGET", result)
                request.assert_not_called()
            link.unlink()

    def test_atomic_writer_does_not_follow_predictable_temp_symlink(self):
        victim = self.root / "victim.json"
        victim.write_text("must remain unchanged")
        (self.data / "campaign-latest.json.tmp").symlink_to(victim)
        bridge.engine.write_json(self.data / "campaign-latest.json", {"safe": True})
        self.assertEqual(victim.read_text(), "must remain unchanged")
        self.assertEqual(json.loads((self.data / "campaign-latest.json").read_text()), {"safe": True})

    def test_concurrent_campaign_lock_blocks_resets(self):
        self.configure(self.target())
        (self.data / "campaign-run.lock").write_text('{"pid":12345}')
        with patch.object(bridge.engine, "request") as request:
            result = self.invoke()
        self.assertEqual(result["error"]["code"], "CAMPAIGN_BUSY", result)
        request.assert_not_called()
        self.assertTrue((self.data / "campaign-run.lock").exists())

    def test_changed_invariant_is_rejected_before_requests(self):
        fake = self.root / "copied-evaluator"
        for name in (*bridge.TRUSTED_FILES, "trusted_campaign_files.json"):
            destination = fake / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bridge.ROOT / name, destination)
        path = fake / "campaign_contracts/inventory-contention.json"
        contract = json.loads(path.read_text())
        contract["invariants"][0]["equals"] = 2
        path.write_text(json.dumps(contract))
        with patch.object(bridge, "ROOT", fake), patch.object(bridge.engine, "request") as request:
            result = bridge.invoke("crashlab_campaign_validate", {"campaign_id": "inventory-contention"})
        self.assertEqual(result["error"]["code"], "EVALUATOR_CHANGED", result)
        request.assert_not_called()

    def test_runner_exception_is_structured_error(self):
        self.configure(self.target())
        with patch.object(bridge.engine, "run_campaign", side_effect=RuntimeError("fixture runtime unavailable")):
            result = self.invoke()
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error"]["code"], "RUNNER_ERROR")
        self.assertFalse(result["ok"])
        self.assertFalse((self.data / "campaign-run.lock").exists())

    def test_integrity_change_during_run_invalidates_success(self):
        self.configure(self.target())
        hashes = bridge.verify_integrity()
        with patch.object(bridge, "verify_integrity", side_effect=[hashes, bridge.CampaignError("EVALUATOR_CHANGED", "changed during test")]):
            result = self.invoke()
        self.assertEqual(result["status"], "ERROR", result)
        self.assertEqual(result["measurement_status"], "PASS")
        self.assertFalse(result["ok"])
        saved = json.loads((self.data / "campaign-latest.json").read_text())
        self.assertEqual(saved["status"], "ERROR")

    def test_plugin_data_cannot_be_written_into_evaluator(self):
        with patch.dict(os.environ, {"CRASHLAB_DATA_DIR": str(bridge.ROOT / "unsafe-data")}):
            result = self.invoke()
        self.assertEqual(result["error"]["code"], "UNSAFE_TARGET")

    def test_mixed_validation_does_not_launch_browser(self):
        target = self.target()
        self.configure(target, cases={"checkout-lost-response": target, "inventory-last-unit": target})
        with patch.object(bridge.engine, "run_legacy") as legacy:
            result = self.invoke("crashlab_campaign_validate", campaign_id="checkout-and-inventory")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["cases"], 2)
        legacy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
