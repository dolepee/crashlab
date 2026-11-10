"""Local-only reliability campaigns. Python standard library; no repair claims."""
from __future__ import annotations

import argparse
import concurrent.futures
import copy
import hashlib
import importlib.util
import json
import math
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

ROOT = Path(__file__).resolve().parent
STATUSES = ("PASS", "FAIL", "INVALID", "ERROR")


class InvalidRun(Exception):
    """The requested experiment did not execute or its baseline was not clean."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def valid_base(value):
    if not isinstance(value, str) or not value or any(ch in value for ch in ("\\", "%", "@")) or any(ord(ch) < 33 or ord(ch) == 127 for ch in value):
        raise ValueError("prototype targets must be loopback HTTP origins without credentials")
    parsed = urlparse(value)
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/")):
        raise ValueError("prototype targets must be loopback HTTP origins without credentials")
    return value.rstrip("/")


def valid_path(value):
    if (not isinstance(value, str) or not value.startswith("/") or value.startswith("//")
            or any(ch in value for ch in ("\\", "%", "@"))
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        raise ValueError("request paths must be relative to the target origin")
    return value


def resolved_url(base, path):
    base = valid_base(base)
    path = valid_path(path)
    resolved = base + path
    parsed = urlparse(resolved)
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
            or parsed.username or parsed.password):
        raise ValueError("request path resolves outside the loopback target")
    return resolved


def validate_request(spec):
    if not isinstance(spec, dict) or spec.get("method") != "POST":
        raise ValueError("only explicit POST requests are supported")
    valid_path(spec.get("path"))
    if not isinstance(spec.get("json"), dict):
        raise ValueError("request.json must be an object")


def validate_assertions(items, baseline=False):
    if not isinstance(items, list) or not items:
        raise ValueError("assertions must be a nonempty list")
    ids = []
    for spec in items:
        if not isinstance(spec, dict) or not isinstance(spec.get("id"), str) or not spec["id"]:
            raise ValueError("assertions need nonempty IDs")
        ids.append(spec["id"])
        expected = spec.get("equals")
        if type(expected) not in (int, float) or not math.isfinite(expected):
            raise ValueError("assertion.equals must be a finite number, not a boolean")
        if spec.get("source") == "sqlite":
            if not isinstance(spec.get("query"), str) or not spec["query"].strip():
                raise ValueError("sqlite assertion.query is required")
        elif spec.get("source") == "responses" and not baseline:
            if type(spec.get("status")) is not int or not 100 <= spec["status"] <= 599:
                raise ValueError("response assertions require an HTTP status")
        else:
            raise ValueError("unknown assertion source")
    if len(ids) != len(set(ids)):
        raise ValueError("assertion IDs must be unique within a phase")


def validate_http_contract(contract):
    if not isinstance(contract, dict) or type(contract.get("schema_version")) is not int or contract["schema_version"] != 1:
        raise ValueError("contract.schema_version must be 1")
    if not isinstance(contract.get("id"), str) or not contract["id"]:
        raise ValueError("contract.id is required")
    valid_base(contract["app"]["base_url"])
    valid_path(contract["app"]["reset_url"])
    if contract["state"].get("mode") != "sqlite" or not isinstance(contract["state"].get("db"), str):
        raise ValueError("an independent SQLite observer is required")
    if not contract["state"]["db"]:
        raise ValueError("state.db cannot be empty")
    fault = contract["fault"]
    if fault.get("mode") != "concurrent_requests":
        raise ValueError("unsupported fault.mode")
    requests = fault.get("requests")
    if not isinstance(requests, list) or not 2 <= len(requests) <= 16:
        raise ValueError("concurrent_requests requires 2 to 16 requests")
    for request in requests:
        validate_request(request)
    fields = fault.get("required_response_fields", {})
    if not isinstance(fields, dict):
        raise ValueError("fault.required_response_fields must be an object")
    for path, expected in fields.items():
        if not isinstance(path, str) or not all(path.split(".")):
            raise ValueError("required response fields need nonempty dotted paths")
        if type(expected) not in (str, bool, int, float) or (type(expected) in (int, float) and not math.isfinite(expected)):
            raise ValueError("required response fields need finite scalar values")
    allowed = contract.get("allowed_statuses")
    if (not isinstance(allowed, list) or not allowed or
            any(type(s) is not int or not 100 <= s <= 599 for s in allowed)):
        raise ValueError("allowed_statuses must be explicit HTTP status integers")
    timeout = contract.get("timeout_seconds", 5)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise ValueError("timeout_seconds must be in (0, 60]")
    validate_assertions(contract["baseline"], baseline=True)
    validate_assertions(contract["invariants"])
    control = contract.get("control")
    if not isinstance(control, dict) or not isinstance(control.get("requests"), list) or not control["requests"]:
        raise ValueError("a nonempty sequential control is required")
    for request in control["requests"]:
        validate_request(request)
    validate_assertions(control["invariants"])
    return contract


def request(base, spec, timeout):
    raw = json.dumps(spec["json"], allow_nan=False).encode()
    started = time.monotonic_ns()
    req = Request(resolved_url(base, spec["path"]), data=raw, method=spec["method"],
                  headers={"Content-Type": "application/json"})
    # No redirects or environment-configured proxies: requests stay on the local target.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        try:
            response = opener.open(req, timeout=timeout)
        except HTTPError as exc:
            response = exc
        with response:
            status = response.code
            body = response.read(1048577)
        if len(body) > 1048576:
            raise ValueError("response exceeds 1 MiB")
        ended = time.monotonic_ns()
        try:
            body = json.loads(body)
        except (ValueError, UnicodeError):
            body = {"raw": body.decode(errors="replace")[:2000]}
        return {"status": status, "body": body, "started_ns": started, "ended_ns": ended}
    except Exception as exc:
        return {"status": None, "error": f"{type(exc).__name__}: {exc}",
                "started_ns": started, "ended_ns": time.monotonic_ns()}


def observe(db, assertions, responses=()):
    db = Path(db).resolve()
    con = sqlite3.connect("file:" + quote(str(db)) + "?mode=ro", uri=True, timeout=2)
    try:
        con.execute("PRAGMA query_only=ON")
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                   sqlite3.SQLITE_RECURSIVE}
        con.set_authorizer(lambda action, *args: sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
        # Bound expensive/recursive SELECTs; observer errors cannot become invariant passes.
        deadline = time.monotonic() + 2
        con.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        results = []
        for spec in assertions:
            if spec["source"] == "sqlite":
                rows = con.execute(spec["query"]).fetchmany(2)
                if len(rows) != 1 or len(rows[0]) != 1:
                    raise ValueError(f"{spec['id']}: observer query must return exactly one scalar")
                value = rows[0][0]
            else:
                value = sum(r.get("status") == spec["status"] for r in responses)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"{spec['id']}: observer must return a finite numeric scalar")
            results.append({**spec, "observed": value, "ok": value == spec["equals"]})
        return results
    finally:
        con.close()


def snapshot_db(db, destination):
    with sqlite3.connect("file:" + quote(str(db)) + "?mode=ro", uri=True) as source:
        with sqlite3.connect(destination) as target:
            source.backup(target)


def run_http(contract, contract_dir, artifact_dir=None):
    result = {"contract": contract.get("id"), "engine": "http_concurrency",
              "status": "ERROR", "scenario": {"valid": False}}
    try:
        validate_http_contract(contract)
        base = valid_base(contract["app"]["base_url"])
        db_text = contract["state"]["db"].replace("{port}", str(urlparse(base).port or 80))
        db = (Path(contract_dir) / db_text).resolve()
        result["target"] = base
        result["observer"] = {"mode": "sqlite", "db": str(db), "independent": True}
        timeout = contract.get("timeout_seconds", 5)

        def reset_and_check():
            reset = request(base, {"method": "POST", "path": contract["app"]["reset_url"], "json": {}}, timeout)
            if reset.get("error") or not 200 <= (reset["status"] or 0) < 300:
                raise RuntimeError(f"reset failed: {reset}")
            values = observe(db, contract["baseline"])
            if not all(v["ok"] for v in values):
                raise InvalidRun(f"baseline not clean: {values}")
            return values

        result["baseline"] = reset_and_check()
        requests = contract["fault"]["requests"]
        barrier = threading.Barrier(len(requests), timeout=timeout)

        def send(spec):
            barrier.wait()
            return request(base, spec, timeout)

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(requests)) as executor:
            responses = list(executor.map(send, requests))
        result["responses"] = responses
        overlapping = max(r["started_ns"] for r in responses) < min(r["ended_ns"] for r in responses)
        result["scenario"] = {"mode": "concurrent_requests", "requested": len(requests),
                              "completed": sum(r["status"] is not None for r in responses),
                              "client_intervals_overlap": overlapping, "valid": False}
        if any(r.get("error") for r in responses):
            raise RuntimeError("one or more concurrent requests failed at transport level")
        if not overlapping:
            raise InvalidRun("client requests did not overlap; concurrency was not exercised")
        checkpoints = []
        for index, response in enumerate(responses):
            for path, expected in contract["fault"].get("required_response_fields", {}).items():
                value = response.get("body")
                for part in path.split("."):
                    value = value.get(part) if isinstance(value, dict) else None
                checkpoints.append({"request": index, "path": path, "expected": expected,
                                    "observed": value, "ok": type(value) is type(expected) and value == expected})
        result["scenario"]["response_checkpoints"] = checkpoints
        if not all(check["ok"] for check in checkpoints):
            raise InvalidRun("required response checkpoint was not reached; fault schedule not established")
        # Completed HTTP errors are SUT failures, not transport or observer errors.
        result["scenario"]["valid"] = True
        result["primary"] = observe(db, contract["invariants"], responses)
        if artifact_dir is not None:
            primary_db = Path(artifact_dir) / "primary.sqlite"
            primary_db.parent.mkdir(parents=True, exist_ok=True)
            snapshot_db(db, primary_db)
            result["primary_snapshot"] = str(primary_db)
        unexpected = [r["status"] for r in responses if r["status"] not in contract["allowed_statuses"]]
        result["unexpected_statuses"] = unexpected
        result["control_baseline"] = reset_and_check()
        control_responses = [request(base, spec, timeout) for spec in contract["control"]["requests"]]
        result["control_responses"] = control_responses
        if any(r.get("error") for r in control_responses):
            raise RuntimeError("one or more control requests failed at transport level")
        result["control"] = observe(db, contract["control"]["invariants"], control_responses)
        if artifact_dir is not None:
            control_db = Path(artifact_dir) / "control.sqlite"
            snapshot_db(db, control_db)
            result["control_snapshot"] = str(control_db)
        control_unexpected = [r["status"] for r in control_responses if r["status"] not in contract["allowed_statuses"]]
        result["control_unexpected_statuses"] = control_unexpected
        passed = all(v["ok"] for v in result["primary"] + result["control"])
        result["status"] = "PASS" if passed and not unexpected and not control_unexpected else "FAIL"
    except InvalidRun as exc:
        result.update(status="INVALID", error=str(exc))
    except Exception as exc:
        result.update(status="ERROR", error=f"{type(exc).__name__}: {exc}")
    return result


def classify_legacy(run):
    """Do not reinterpret an old runner crash or unexecuted fault as a finding."""
    if run.get("error") or run.get("returncode") not in (0, 1):
        return "ERROR"
    if run.get("scenario", {}).get("valid") is not True:
        return "INVALID"
    for phase in (run, run.get("control")):
        if phase is None:
            continue
        for key in ("observed", "expected"):
            value = phase.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                return "ERROR"
    passed = run["observed"] == run["expected"]
    if run.get("control") is not None:
        passed = passed and run["control"]["observed"] == run["control"]["expected"]
    if run.get("ok") is not passed or run["returncode"] != (0 if passed else 1):
        return "ERROR"
    return "PASS" if passed else "FAIL"


def run_legacy(contract, contract_dir, legacy_root, artifact_dir=None):
    import tempfile
    spec = importlib.util.spec_from_file_location("legacy_crashlab_core", Path(legacy_root) / "crashlab_core.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    valid_base(contract["app"]["base_url"])
    # Freeze SQLite paths before writing the temporary contract.
    for state in (contract.get("state"), (contract.get("control") or {}).get("state")):
        if state and state.get("mode") == "sqlite":
            state["db"] = str((Path(contract_dir) / state["db"]).resolve())
    holder = None
    temporary = None
    try:
        if artifact_dir is None:
            temporary = tempfile.TemporaryDirectory(prefix="crashlab-legacy-")
            holder = Path(temporary.name) / "contract.json"
        else:
            # The legacy runner's contract directory is its owned root. Keep the
            # temporary contract and its screenshot directory under the same
            # campaign output tree so the evidence guard can accept both.
            holder = Path(artifact_dir) / f".legacy-contract-{uuid.uuid4().hex}.json"
        holder.write_text(json.dumps(contract))
        run = module.run_contract(holder, timeout=60)
    finally:
        if holder is not None and artifact_dir is not None:
            holder.unlink(missing_ok=True)
        if temporary is not None:
            temporary.cleanup()
    return {"engine": "legacy_browser", "contract": contract["id"],
            "status": classify_legacy(run), "legacy": run}


def write_json(path, value):
    path = Path(path)
    raw = json.dumps(value, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(raw)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def run_campaign(manifest_path, output_dir, legacy_root=None):
    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text())
    if (type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1
            or not isinstance(manifest.get("cases"), list) or not manifest["cases"]):
        raise ValueError("campaign requires schema_version 1 and nonempty cases")
    seen = set()
    # Validate the entire manifest before starting any destructive fixture resets.
    for case in manifest["cases"]:
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError("case IDs must be nonempty and unique")
        seen.add(case_id)
        if case.get("engine") not in ("http_concurrency", "legacy_browser"):
            raise ValueError("unsupported case engine")
        repeats = case.get("repetitions", 1)
        if type(repeats) is not int or not 1 <= repeats <= 100:
            raise ValueError("case.repetitions must be an integer from 1 to 100")
        if not isinstance(case.get("contract"), str) or not case["contract"]:
            raise ValueError("case.contract is required")
        if case.get("base_url") is not None:
            valid_base(case["base_url"])
    campaign_id = str(manifest.get("id", "campaign"))
    run_id = "campaign-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
    out = Path(output_dir).resolve() / run_id
    out.mkdir(parents=True)
    saved_manifest = copy.deepcopy(manifest)
    for index, case in enumerate(saved_manifest["cases"]):
        case["contract"] = f"contract-{index}.json"
    write_json(out / "manifest.json", saved_manifest)
    rows = []
    for index, case in enumerate(manifest["cases"]):
        for repetition in range(case.get("repetitions", 1)):
            contract_path = (manifest_path.parent / case["contract"]).resolve()
            try:
                raw = contract_path.read_bytes()
                contract = json.loads(raw)
                if case.get("base_url"):
                    contract["app"]["base_url"] = case["base_url"]
                if case.get("db"):
                    contract["state"]["db"] = str((manifest_path.parent / case["db"]).resolve())
                    if contract.get("control", {}).get("state"):
                        contract["control"]["state"]["db"] = contract["state"]["db"]
                for state in (contract.get("state"), contract.get("control", {}).get("state")):
                    if state and state.get("mode") == "sqlite":
                        state["db"] = str((contract_path.parent / state["db"]).resolve())
                contract["evidence"] = {"screenshot_dir": str(out / f"screenshots-{index}-{repetition}")}
                effective_path = out / f"contract-{index}-{repetition}.json"
                write_json(effective_path, contract)
                # The manifest refers to this canonical path. Persist it beside the
                # manifest so a saved campaign can be replayed after the temporary
                # input directory is gone.
                canonical_path = out / f"contract-{index}.json"
                if not canonical_path.exists():
                    write_json(canonical_path, contract)
                contract_sha = hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()
                if case["engine"] == "http_concurrency":
                    run = run_http(contract, contract_path.parent, out / f"state-{index}-{repetition}")
                else:
                    run = run_legacy(contract, contract_path.parent, legacy_root or ROOT, out)
                run.update(source_contract_sha256=hashlib.sha256(raw).hexdigest(),
                           effective_contract_sha256=contract_sha,
                           effective_contract_path=str(effective_path))
            except Exception as exc:
                run = {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
            run.update(case=case["id"], repetition=repetition + 1)
            evidence_path = out / f"run-{index}-{repetition}.json"
            write_json(evidence_path, run)
            rows.append({"case": case["id"], "repetition": repetition + 1,
                         "status": run["status"], "evidence_path": str(evidence_path)})
    counts = {s: sum(r["status"] == s for r in rows) for s in STATUSES}
    status = next((s for s in ("ERROR", "INVALID", "FAIL") if counts[s]), "PASS")
    result = {"schema_version": 1, "campaign": campaign_id, "run_id": run_id,
              "status": status, "counts": counts, "runs": rows,
              "evidence_dir": str(out), "complete": True}
    result["runner_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    write_json(out / "summary.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    parser.add_argument("--output", default="campaign-evidence")
    parser.add_argument("--legacy-root", default=str(ROOT))
    args = parser.parse_args()
    try:
        result = run_campaign(args.manifest, args.output, args.legacy_root)
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}))
        return 2
    print(json.dumps(result, indent=2))
    return {"PASS": 0, "FAIL": 1, "INVALID": 2, "ERROR": 2}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
