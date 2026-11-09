# First run and your own app

This is the short path for a disposable local Git application. CrashLab checks a
saved recovery behaviour; it does not discover all application semantics.

## Prerequisites

- Git available on `PATH`, and the app directory is the **Git repository root**.
  A nested directory is rejected with the root to use. CrashLab never initialises
  an app for you.
- Python 3.11+ and the app's own runtime/dependencies.
- Node with `playwright-core`, plus a Chromium/Chrome executable. Install a
  browser with `npx playwright-core install chromium`, or set **`CHROME_PATH`**
  to its executable. The older `CHROME` spelling is not the documented variable.
- A writable `CRASHLAB_DATA_DIR` outside the editable app.
- The chat route needs Hermes's configured model. "No model key" applies only to
  a genuine standalone interactive replay, not to asking Hermes in chat.

## Check Git before preparing

```bash
git -C /path/to/your/app rev-parse --show-toplevel
```

If this fails, stop and use a Git repository root. Do not run `git init` on an
existing app just to make the check pass unless that is what you intend. The
installed entry point returns an actionable Git-prerequisite error for a non-Git
directory, a missing `git` executable, and a nested path; it creates no app or
experiment evidence on those failures.

## First prompt

```text
Use CrashLab on /path/to/your/app. The intended behaviour is that one interrupted
checkout retry creates exactly one saved record, while two deliberate orders
remain possible. Inspect the app, build a fault/control/recovery contract, and
stop after crashlab_prepare so I can review the plan.
```

Then:

1. `crashlab_prepare` freezes the case and evaluator and stores them outside the
   app. Review the proposed interruption, assertions and control.
2. `crashlab_experiment_run` — approve **Allow once**. A valid failing baseline
   is `REPRODUCED`.
3. Edit only the application source in that checkout. Do not change the frozen
   case, bindings, evaluator or experiment.
4. Rerun `crashlab_experiment_run` by the same experiment ID — approve **Allow
   once**. `VERIFIED` requires the changed source, a passing primary case and a
   passing legitimate-use control.
5. `crashlab_experiment_report` for the measured boundaries, then
   `crashlab_experiment_export` for a reusable bundle.

## Fixtures and external proof

`cases/checkout-local.json`, `cases/checkout-recovery.json` and their `-fixed`
variants are explicit fixtures. `BUGGY=0` is a prepared repaired fixture for
comparison; it is not new repair logic or a model repair. The main external
application proof remains the [Kist replay](../external/kist/CURRENT_REPLAY.md),
not the bundled fixture.

## Replay and campaigns

The `crashlab_replay.py run` path is an **interactive terminal** operator path:
it shows the exact frozen plan and requires a human approval, and piped or other
non-interactive use fails closed. It is not an unattended CI runner or a bypass.

For a recovery campaign, the supported boundaries are `before_delivery`,
`after_commit` and `restart_after_commit`. The restart boundary stops and
restarts the owned app while retaining that scenario's database and records. It
does not preserve process memory and is not a fresh-database restart. Use
record-level observations for restart preservation.
