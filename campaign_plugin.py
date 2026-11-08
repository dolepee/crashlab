"""Hermes adapter for trusted local CrashLab campaign definitions.

Tool callers select a bundled campaign and a preconfigured disposable target
set. They cannot replace contracts, SQL, evaluator code, output paths or Python
modules. Hash verification detects accidental/tampered evaluator files; it is
not a security boundary against an actor that can rewrite this plugin itself.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

try:
    from . import campaign as engine
    from . import crashlab_core as core
    from . import approval
except ImportError:
    import campaign as engine
    import crashlab_core as core
    import approval

ROOT = Path(__file__).resolve().parent
CATALOG = {
    "inventory-contention": "campaigns/inventory.json",
    "checkout-and-inventory": "campaigns/mixed.json",
}
TRUSTED_FILES = (
    "__init__.py", "plugin.py", "plugin.yaml", "campaign_plugin.py", "campaign.py",
    "crashlab_core.py", "crashlab_runner.js", "crashlab_probe.py",
    "approval.py", "repair_workflow.py", "crashlab_replay.py", "completion_reminder.py", "skills/crashlab/SKILL.md",
    "contracts/checkout-lost-response.json", "contracts/booking-lost-response.json",
    "campaign_contracts/inventory-contention.json", "campaigns/inventory.json", "campaigns/mixed.json",
)


class CampaignError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def failure(code, message):
    return {"ok": False, "status": "ERROR", "complete": False,
            "error": {"code": code, "message": message}}


def _read_json(path, label):
    try:
        if path.stat().st_size > 1048576:
            raise ValueError("file exceeds 1 MiB")
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CampaignError("CONFIGURATION_ERROR", f"Cannot read {label}: {exc}") from exc


def verify_integrity():
    pin = _read_json(ROOT / "trusted_campaign_files.json", "trusted evaluator manifest")
    if not isinstance(pin, dict) or set(pin.get("files", {})) != set(TRUSTED_FILES):
        raise CampaignError("EVALUATOR_CHANGED", "Trusted evaluator file set differs from this plugin release")
    observed = {}
    for name in TRUSTED_FILES:
        path = ROOT / name
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()):
            raise CampaignError("EVALUATOR_CHANGED", f"Trusted file is redirected: {name}")
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise CampaignError("EVALUATOR_CHANGED", f"Trusted file is unavailable: {name}") from exc
        if digest != pin["files"][name]:
            raise CampaignError("EVALUATOR_CHANGED", f"Trusted evaluator file changed: {name}")
        observed[name] = digest
    return observed


def data_dir():
    path = Path(os.environ.get("CRASHLAB_DATA_DIR", Path.home() / ".local/share/crashlab")).expanduser().resolve()
    if path.is_relative_to(ROOT.resolve()):
        raise CampaignError("UNSAFE_TARGET", "CRASHLAB_DATA_DIR must be outside the trusted plugin directory")
    return path


def target_sets():
    value = _read_json(data_dir() / "campaign-targets.json", "campaign target configuration")
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise CampaignError("CONFIGURATION_ERROR", "Target configuration requires schema_version 1")
    sets = value.get("target_sets")
    if not isinstance(sets, dict):
        raise CampaignError("CONFIGURATION_ERROR", "target_sets must be an object")
    return sets


def check_output_paths(data):
    for name in ("campaign-results", "campaign-latest.json"):
        path = data / name
        if path.is_symlink() or not path.resolve().is_relative_to(data):
            raise CampaignError("UNSAFE_TARGET", f"Campaign output is redirected: {name}")
    output = data / "campaign-results"
    if output.exists() and not output.is_dir():
        raise CampaignError("UNSAFE_TARGET", "campaign-results must be a directory")


@contextmanager
def run_lock(data):
    """Do not let concurrent tool calls reset each other's fixture state."""
    path = data / "campaign-run.lock"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except FileExistsError as exc:
        raise CampaignError("CAMPAIGN_BUSY", "A campaign lock already exists. Do not remove it until the owning run has stopped") from exc
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump({"pid": os.getpid()}, stream)
        yield
    finally:
        path.unlink(missing_ok=True)


def _args(args, require_target=False):
    if not isinstance(args, dict) or set(args) - {"campaign_id", "target_set", "repetitions"}:
        raise CampaignError("INVALID_ARGUMENT", "Only campaign_id, target_set and repetitions are accepted")
    campaign_id = args.get("campaign_id")
    if not isinstance(campaign_id, str) or campaign_id not in CATALOG:
        raise CampaignError("INVALID_ARGUMENT", "Select a bundled campaign_id from crashlab_campaign_list")
    repetitions = args.get("repetitions", 1)
    if type(repetitions) is not int or not 1 <= repetitions <= 20:
        raise CampaignError("INVALID_ARGUMENT", "repetitions must be an integer from 1 to 20")
    target = args.get("target_set")
    if require_target and target is None:
        raise CampaignError("INVALID_ARGUMENT", "target_set is required; configure a disposable local fixture first")
    if target is not None and (not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", target)):
        raise CampaignError("INVALID_ARGUMENT", "target_set must be a configured short identifier")
    return campaign_id, target, repetitions


def _bind_target(contract, target):
    if not isinstance(target, dict) or target.get("allow_reset") is not True or target.get("disposable") is not True:
        raise CampaignError("UNSAFE_TARGET", "Each target must explicitly set disposable=true and allow_reset=true")
    try:
        base = engine.valid_base(target.get("base_url"))
        port = urlparse(base).port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("Target port must be between 1 and 65535")
    except ValueError as exc:
        raise CampaignError("UNSAFE_TARGET", str(exc)) from exc
    workspace_value = target.get("workspace")
    if not isinstance(workspace_value, str) or not Path(workspace_value).expanduser().is_absolute():
        raise CampaignError("UNSAFE_TARGET", "Target workspace must be an absolute disposable directory")
    workspace = Path(workspace_value).expanduser().resolve()
    if not workspace.is_dir() or workspace.is_relative_to(ROOT.resolve()) or ROOT.resolve().is_relative_to(workspace):
        raise CampaignError("UNSAFE_TARGET", "Target workspace must exist and be separate from trusted evaluator files")
    database = target.get("db")
    if not isinstance(database, str) or not database or Path(database).is_absolute():
        raise CampaignError("UNSAFE_TARGET", "Target db must be a relative path inside its workspace")
    db = (workspace / database).resolve()
    if not db.is_relative_to(workspace) or not db.is_file():
        raise CampaignError("UNSAFE_TARGET", "Target database must exist inside the disposable workspace")
    contract["app"]["base_url"] = base
    for state in (contract.get("state"), contract.get("control", {}).get("state")):
        if state is not None:
            if state.get("mode") != "sqlite":
                raise CampaignError("UNSAFE_TARGET", "Campaign tools require an independent SQLite observer")
            state["db"] = str(db)


def prepare(args, require_target=False):
    campaign_id, target_name, repetitions = _args(args, require_target)
    hashes = verify_integrity()
    source_path = ROOT / CATALOG[campaign_id]
    manifest = copy.deepcopy(_read_json(source_path, "bundled campaign"))
    selected = None
    if target_name is not None:
        sets = target_sets()
        selected = sets.get(target_name)
        if not isinstance(selected, dict) or not isinstance(selected.get("cases"), dict):
            raise CampaignError("CONFIGURATION_ERROR", "target_set does not exist or has no cases mapping")
    contracts = []
    # Preflight and snapshot EVERY case before the first reset/request.
    for case in manifest["cases"]:
        source = (source_path.parent / case["contract"]).resolve()
        if not source.is_relative_to(ROOT.resolve()) or str(source.relative_to(ROOT.resolve())) not in hashes:
            raise CampaignError("EVALUATOR_CHANGED", "Campaign references an untrusted contract")
        contract = copy.deepcopy(_read_json(source, "bundled contract"))
        if selected is not None:
            target = selected["cases"].get(case["id"])
            if target is None:
                raise CampaignError("CONFIGURATION_ERROR", f"Target set has no binding for case {case['id']}")
            _bind_target(contract, target)
        try:
            if case["engine"] == "http_concurrency":
                engine.validate_http_contract(contract)
            elif case["engine"] == "legacy_browser":
                core.validate_contract(contract)
                engine.valid_base(contract["app"]["base_url"])
            else:
                raise ValueError("unsupported campaign engine")
        except (ValueError, TypeError, KeyError) as exc:
            raise CampaignError("CONFIGURATION_ERROR", f"Invalid bundled case {case['id']}: {exc}") from exc
        case["repetitions"] = repetitions
        case["contract"] = f"contract-{len(contracts)}.json"
        contracts.append(contract)
    return campaign_id, target_name, hashes, manifest, contracts


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def capture(args):
    """Freeze the exact target-bound contracts that an approval will cover."""
    campaign_id, target_name, hashes, manifest, contracts = prepare(args, require_target=True)
    plan = {
        "tool": "crashlab_campaign_run",
        "campaign_id": campaign_id,
        "target_set": target_name,
        "repetitions": args.get("repetitions", 1),
        "evaluator_sha256": hashes,
        "manifest_sha256": _digest(manifest),
        "contract_sha256": [_digest(contract) for contract in contracts],
    }
    return {"digest": _digest(plan), "plan": plan, "campaign_id": campaign_id,
            "target_name": target_name, "hashes": hashes, "manifest": manifest,
            "contracts": contracts}


def invoke(name, args, approval_context=None):
    """Hermes JSON-compatible outcomes; never leak an exception as a finding."""
    try:
        if name == "crashlab_campaign_list":
            if not isinstance(args, dict) or args:
                raise CampaignError("INVALID_ARGUMENT", "crashlab_campaign_list accepts no arguments")
            verify_integrity()
            return {"ok": True, "campaigns": [
                {"id": "inventory-contention", "engines": ["http_concurrency"], "browser_required": False, "run_supported": True},
                {"id": "checkout-and-inventory", "engines": ["legacy_browser", "http_concurrency"], "browser_required": True, "run_supported": False,
                 "limitation": "Validation-only until the legacy browser driver enforces local redirects/subresources"}],
                "scope": "Preconfigured disposable loopback fixtures only; no arbitrary contract/evaluator paths"}
        if name not in ("crashlab_campaign_validate", "crashlab_campaign_run"):
            raise CampaignError("INVALID_ARGUMENT", "Unknown campaign tool")
        if name == "crashlab_campaign_run":
            context = approval_context or {}
            approved = approval.prepare_approved_campaign(
                args,
                session_id=str(context.get('session_id') or ''),
                task_id=str(context.get('task_id') or ''),
                tool_call_id=str(context.get('tool_call_id') or ''))
            bundle = approved["bundle"]
            campaign_id = bundle["campaign_id"]
            target_name = bundle["target_name"]
            hashes = bundle["hashes"]
            manifest = bundle["manifest"]
            contracts = bundle["contracts"]
        else:
            campaign_id, target_name, hashes, manifest, contracts = prepare(args, False)
        if name == "crashlab_campaign_validate":
            return {"ok": True, "valid": True, "executed": False, "campaign": campaign_id,
                    "target_set": target_name, "cases": len(contracts),
                    "runs": sum(c["repetitions"] for c in manifest["cases"]), "evaluator_sha256": hashes}
        if any(case["engine"] == "legacy_browser" for case in manifest["cases"]):
            raise CampaignError("UNSUPPORTED_ENGINE", "Browser campaigns are validation-only in this integration; legacy redirects and subresources are not yet loopback-confined")
        data = data_dir()
        check_output_paths(data)
        data.mkdir(parents=True, exist_ok=True)
        with run_lock(data), tempfile.TemporaryDirectory(prefix="campaign-input-", dir=data) as temporary:
            frozen = Path(temporary)
            for index, contract in enumerate(contracts):
                engine.write_json(frozen / f"contract-{index}.json", contract)
            engine.write_json(frozen / "manifest.json", manifest)
            result = engine.run_campaign(frozen / "manifest.json", data / "campaign-results", legacy_root=ROOT)
        result.update(ok=result["status"] == "PASS", campaign=campaign_id, target_set=target_name,
                      evaluator_sha256=hashes, integration="hermes-campaign-v1")
        try:
            if verify_integrity() != hashes:
                raise CampaignError("EVALUATOR_CHANGED", "Evaluator changed during the run")
        except CampaignError as exc:
            result.update(ok=False, measurement_status=result["status"], status="ERROR",
                          error={"code": exc.code, "message": str(exc)})
        engine.write_json(Path(result["evidence_dir"]) / "summary.json", result)
        # Do not overwrite the old single-case/compare latest.json contract.
        engine.write_json(data / "campaign-latest.json", result)
        return result
    except approval.ApprovalRequired:
        raise
    except CampaignError as exc:
        return failure(exc.code, str(exc))
    except Exception as exc:
        return failure("RUNNER_ERROR", f"{type(exc).__name__}: {exc}")


def schemas():
    properties = {
        "campaign_id": {"type": "string", "enum": list(CATALOG)},
        "target_set": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,80}$"},
        "repetitions": {"type": "integer", "minimum": 1, "maximum": 20, "default": 1},
    }
    return [
        {"name": "crashlab_campaign_list", "description": "List trusted bundled reliability campaigns. Does not run or reset fixtures.",
         "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
        {"name": "crashlab_campaign_validate", "description": "Read-only validation of a trusted campaign and optional preconfigured target set. Checks evaluator integrity. Does not run requests or reset fixtures.",
         "parameters": {"type": "object", "properties": properties, "required": ["campaign_id"], "additionalProperties": False}},
        {"name": "crashlab_campaign_run", "description": "Run the trusted inventory reliability campaign on a preconfigured disposable loopback target set. Browser campaigns are currently validation-only. Resets fixture state per trial, independently checks SQLite, and saves evidence. PASS/FAIL/INVALID/ERROR stay distinct. Never use a production tunnel.",
         "parameters": {"type": "object", "properties": properties, "required": ["campaign_id", "target_set"], "additionalProperties": False}},
    ]
