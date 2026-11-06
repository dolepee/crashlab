# Hermes campaign integration

This is an isolated integration based on `dolepee/crashlab` commit
`683e82e86691390e7fde899c4ccd75850e72dbb2`. It does not modify the live repository,
PR, normal Hermes installation, or the original review snapshot.

## Added tools

- `crashlab_campaign_list`: list the two bundled campaign definitions and whether
  execution is supported.
- `crashlab_campaign_validate`: read-only validation of a campaign, the trusted
  evaluator files, and an optional configured target set. No reset or HTTP call.
- `crashlab_campaign_run`: run the inventory contention campaign, with 1–20
  repetitions, against a configured disposable loopback fixture.

The four existing tools and their argument/result shapes are preserved. The new
inventory contract lives outside the old `contracts/` directory, so old
`crashlab_list` still returns two valid legacy contracts. Existing comparison
evidence uses `latest.json`; campaigns use `campaign-latest.json` separately.

## Safe, explicit configuration

The tool API accepts only `campaign_id`, `target_set` and `repetitions`. It does
not accept arbitrary manifest files, evaluator paths, SQL, output directories,
URLs or database overrides. An owner must configure target sets in
`$CRASHLAB_DATA_DIR/campaign-targets.json` before running them.

Example, with an already started fixture:

```json
{
  "schema_version": 1,
  "target_sets": {
    "local-fixed": {
      "cases": {
        "inventory-last-unit": {
          "base_url": "http://127.0.0.1:8795",
          "workspace": "/tmp/my-disposable-inventory",
          "db": "inventory.db",
          "disposable": true,
          "allow_reset": true
        }
      }
    }
  }
}
```

Start that fixture separately from this directory:

```bash
mkdir -p /tmp/my-disposable-inventory
python3 inventory_fixture.py --mode fixed --port 8795 \
  --db /tmp/my-disposable-inventory/inventory.db
```

The workspace must exist, be separate from the evaluator directory, and contain
the database. Absolute database overrides, traversal and symlink escapes are
rejected. The two explicit booleans authorize destructive **fixture resets**.
They are owner declarations, not automated proof that a service is disposable.
Never configure a local proxy/tunnel to production. Run the plugin and target
under separate OS permissions/containers if the target is untrusted.

Set `CRASHLAB_DATA_DIR` outside the plugin directory in the environment that
launches Hermes. Then invoke through Hermes:

```json
{"campaign_id":"inventory-contention","target_set":"local-fixed","repetitions":3}
```

Use that mapping first with `crashlab_campaign_validate`, then with
`crashlab_campaign_run`. Validation without `target_set` checks only the bundled
definition and evaluator, not a live fixture or its readiness.

## Trusted evaluator and explicit failures

`trusted_campaign_files.json` records SHA-256 hashes of evaluator modules,
registered entrypoints, campaign manifests and contracts. The adapter verifies
them before a run and after completion, preflights every case before any reset,
then runs frozen copies of the approved inputs. Changing an expected invariant
to make the buggy app pass produces `EVALUATOR_CHANGED`, not PASS. App code and
app databases are not included in the evaluator pins and can be repaired.

The pins must themselves remain in a trusted, owner-controlled installation.
They are **not signed release verification or an adversarial sandbox**. Anyone
able to rewrite both code and pins can defeat these checks. Freeze the evaluator
outside a repair agent's writable application workspace; do not regenerate pins
as part of an application repair.

Campaign statuses remain PASS, FAIL, INVALID and ERROR. Setup, missing targets,
observer failures, changed evaluator files and unsupported engines cannot count
as reproduced bugs. A failed scheduling checkpoint is INVALID. The buggy case
must show the actual failed invariant; FAIL alone does not prove a repair.

Outputs have atomic exclusive temporary writes; redirected result/latest paths
are rejected. A per-data-directory lock prevents simultaneous tool calls from
resetting one another's fixtures. All runs for a fixture must use the same data
directory. A crash may leave a lock: inspect its PID and ensure the old run has
stopped before manually removing it. Tools do not break stale locks themselves.

## What is implemented versus deliberately blocked

The inventory campaign runs through the **real Hermes registry/dispatcher**. It
uses HTTP requests and independent read-only SQLite observations, not a browser.
It checks distinct intents competing for the last unit, stock conservation,
response counts and legitimate-purchase controls. The known race is made
deterministic with an explicit fixture-only scheduling barrier. This is not an
external-app defect discovery or autonomous repair claim.

`checkout-and-inventory` can be validated but **cannot run through the new
campaign tool yet**. The legacy browser runner can follow redirects and load
non-loopback subresources. Rather than claiming network confinement it does not
provide, the adapter returns `ERROR / UNSUPPORTED_ENGINE` before any requests.
The original single-case browser tools remain unchanged, with their original
scope and requirements. No browser or model-inference run was performed for this
integration. Guarding the legacy driver's network traffic and then testing it
on a working Chromium executor is an explicit remaining integration gate.

The Desktop pane is unchanged and still displays old comparison evidence. A
campaign matrix UI, remote targets, authentication, PostgreSQL and campaign
repair-comparison API are not implemented.

## Tests and reproducible host verification

Standard library tests, no browser needed:

```bash
export CRASHLAB_DATA_DIR="$(mktemp -d)/crashlab-data"
python3 -m unittest -v test_campaign_plugin test_plugin test_crashlab_core test_campaign
```

The deliverable's separate `verification/hermes_dispatch_smoke.py` exercises
actual Hermes discovery, registration and dispatch without enabling anything in
the user's normal Hermes home. Use a Python interpreter with the selected
Hermes checkout's dependencies installed:

```bash
/path/to/hermes/python verification/hermes_dispatch_smoke.py \
  --plugin /path/to/crashlab_campaign_integration \
  --hermes-source /path/to/hermes-agent \
  --output /tmp/crashlab-hermes-verification
```

The smoke test starts disposable buggy/fixed/reject-all inventory fixtures,
dispatches each three times, checks every result, exercises the original
list/validate tools, and verifies missing targets/invalid arguments return
errors. It validates plugin admission **before** loading its tools into the same
registry, avoiding a spurious self-collision. The plugin's deployed files do not
import private Hermes internals; only this separate test harness does.

For installation outside this test environment, follow the original README's
Hermes plugin steps. Do not replace an existing installation without reviewing
this isolated package and its limitations first.
