# CrashLab — Day 2 reusable contract + Hermes tool

Run: 2026-11-06

## What exists now

- A JSON contract: `contracts/checkout-lost-response.json`
- A reusable Playwright runner: `crashlab_runner.js`
- A local checkout fixture: `server.py`
- A Hermes plugin with four tools:
  - `crashlab_validate`
  - `crashlab_run`
  - `crashlab_compare`
  - `crashlab_list`
- A reset endpoint so the same contract runs from a clean fixture state.
- A control journey: two distinct purchases must create two records.

## Actual Hermes tool result

`crashlab_compare` was called through the real Hermes v0.23 tool registry.

- Contract: `checkout-lost-response`
- Buggy URL: `http://127.0.0.1:8765`
- Repaired URL: `http://127.0.0.1:8766`
- Buggy observed: **2** records. Expected: **1**. Result: fail — correct.
- Repaired observed: **1** record. Expected: **1**. Result: pass.
- Control: two distinct purchases created **2** records. Result: pass.
- Overall comparison: **true**.

The buggy run's browser displayed “Could not confirm the order. Try again”, then the retry displayed “Order confirmed: #2”. The repaired run displayed “Order confirmed: #1”.

Evidence: `evidence/day2-compare.json`; visual report: `evidence/day2-compare.html`.

## Validator

- `hermes plugins validate` passes for the CrashLab plugin.
- No warnings after clearing Chromium's local profile.
- `hermes plugins doctor` passes: 4 tools, 0 hooks.
- The plugin loads in a fresh v0.23 runtime.
- Evidence report: `evidence/day2-compare.html` and `evidence/day2-report.png`.

## What this proves

- One saved contract can be run repeatedly from a clean state.
- The same contract fails on the buggy app and passes on the repaired app.
- The check is based on persisted records, not a success message or screenshot.
- The control prevents a repair that simply blocks every later purchase.

## What this does not prove

- Hermes has not yet made the repair; the repaired app is a separate server mode.
- There is no native Desktop evidence viewer yet.
- The second application is not wired in.
- The plugin needs a local Chromium and `playwright-core`.
- The screenshot path currently lives inside the plugin directory and must be moved for clean catalog validation.
- No catalog PR for CrashLab yet.

## Next gate

The next gate is the Hermes repair handoff:

1. Take the saved failure and the actual records.
2. Let Hermes change the fixture's checkout code.
3. Rerun the unchanged contract.
4. Require the control to pass as well.
5. Only then show the native evidence view.
