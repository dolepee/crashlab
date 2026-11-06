# Kist external application trial

This directory contains an external application check, not a bundled fixture.
It is here to make the provenance of the Kist result reviewable and replayable.

## What is proven

- Upstream: `grzella/kist-finance` at `7a196af9550378d24d196e666ac0a0f6dd37cb48`.
- The upstream checkout is unmodified in the before run.
- A browser creates a goal, the server commits it, the POST response is
  suppressed, the form remains populated and the Add goal button remains
  enabled, and clicking it again creates a second goal.
- CrashLab observes the SQLite `goals` table directly. The before run reports
  `observed: 2`, `expected: 1`; the control reports two deliberate goals.
- The repaired checkout was produced by a Hermes one-shot agent in
  `/workspace/tmp/kist-hermes`; the patch is in `repair.patch`.
- The same contract reports `observed: 1`, `expected: 1`, and the same control
  still reports two goals.

The before/after summaries are in `evidence/result-summary.json`; the
screenshots are `buggy-before-retry.png`, `buggy-after-retry.png`,
`repaired-after-retry.png` and `repaired-two-intents.png`.

## What is not proven

- This is not a general autonomous repair claim. The repair was performed by
  one Hermes one-shot run, and the patch is included for review.
- Only the single-intent lost-response path is covered. The control allows two
  deliberate goals, including two goals with the same name and fields.
- Kist stores savings-goal records. This is not a duplicate payment or a money
  movement test.
- The browser run used Chromium 155 headless in this environment. Kist is
  configured through `/api/app-config` so the run starts directly in Goals;
  the contract waits on observable conditions rather than fixed sleeps.

## Prerequisites

- A Kist checkout at the pinned commit.
- Python 3 with the upstream `requirements.txt`, plus Playwright 1.62 and
  Chromium at `/usr/bin/chromium`.
- Node with the CrashLab repository's `playwright-core` dependency.

## Run the contract

From the CrashLab repository:

```bash
python external/kist/run_kist.py \
  --app /path/to/kist-finance-7a196af9550378d24d196e666ac0a0f6dd37cb48 \
  --output /tmp/kist-run
```

The script starts Kist with an isolated `FINANCE_PROJECT_DIR`, completes the
app configuration through `/api/app-config`, writes the contract into the run
directory, invokes the registered `crashlab_run` path, and prints the result.
The run is also persisted as `latest-run.json` for the Desktop pane. A fresh
data directory is required for each run; Kist has no CrashLab-specific reset
endpoint.

To apply the Hermes repair to a separate copy:

```bash
git -C /path/to/copy apply /path/to/CrashLab/external/kist/repair.patch
```

Do not apply the patch to the before-run checkout and then present that run as
unmodified upstream evidence.
