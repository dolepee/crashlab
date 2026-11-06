# CrashLab — Day 3 Hermes repair

Run: 2026-11-06

## What was tested

A local checkout app (`/tmp/crashlab-repair/app.py`) was seeded with the
lost-response defect. The saved contract was run against the buggy app.

Then Hermes Agent was given the contract, the failure evidence and the app
source, with one instruction: fix the duplicate-order behavior without
blocking legitimate new purchases.

## Hermes repair

Hermes changed `app.py` only.

The repair:

- Adds a transactional order creation path keyed by `intent_key`.
- Takes a write lock before looking up the existing order.
- Adds a partial unique index on non-empty intent keys as a database-level
  guarantee.
- Returns the original order for a retry.
- Leaves new intent keys as new orders.
- Leaves `/state` and `/reset` unchanged.

The agent also ran its own checks: same key twice returned the same id, a new
key created a new id, and 20 concurrent posts with one key produced one row.
`python3 -m py_compile app.py` passed.

## Independent result

The same contract was run again against the buggy app and the repaired app.

- Buggy: 2 persisted orders; expected 1; fail.
- Repaired: 1 persisted order; expected 1; pass.
- Control: 2 distinct purchases; 2 orders; pass.

The `crashlab_compare` tool was also called through the real Hermes v0.23 tool
registry against the same two URLs: overall `true`.

Evidence:

- `evidence/repair-compare.json`
- `evidence/repair-compare.html`
- `evidence/repair-report.png`
- `evidence/repair-agent.out`

## What this proves

- A real agent edited the application after seeing a reproducible failure.
- The unchanged contract distinguishes the buggy and repaired versions.
- The control rejects a repair that blocks legitimate later purchases.
- The result is based on persisted records, not the success banner.

## What it does not prove

- There is no native Desktop evidence viewer yet; the report is HTML.
- The second application is not wired in.
- The plugin still needs a local Chromium and `playwright-core`.
- The fixture app is intentionally small and intentionally buggy.
- The agent's repair is one known defect, not a general repair guarantee.
