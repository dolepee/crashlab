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
- The repaired checkout was produced by a fresh Hermes one-shot agent run in
  `/tmp/kist-repair-live`; the source diff is `repair.patch` and the agent’s
  final report is `repair-live.log`. The full conversational transcript was not
  captured; the diff and report are retained.
- The same contract reports `observed: 1`, `expected: 1`, and the same control
  still reports two goals.

The before/after summaries are in `evidence/result-summary.json`; the
screenshots are `buggy-before-retry.png`, `buggy-after-retry.png`,
`repaired-before-retry.png`, `repaired-after-retry.png` and the two `two-intents`
controls.

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

The script writes an `app.start` contract, then invokes the registered
`crashlab_run` path. CrashLab starts Kist, completes `/api/app-config`, runs
the browser and stops the app. The run is persisted as `latest-run.json` for
the Desktop pane. A fresh data directory is required for each run; Kist has no
CrashLab-specific reset endpoint.

To apply the Hermes repair to a separate copy:

```bash
git -C /path/to/copy apply /path/to/CrashLab/external/kist/repair.patch
```

Do not apply the patch to the before-run checkout and then present that run as
unmodified upstream evidence.

## Record the browser session

The 26-second `demo/crashlab-kist-live.mp4` was recorded from the two real
browser sessions above. It is a live recording, not a terminal replay:

```bash
KIST_PYTHON=/path/to/python \
node external/kist/record_session.js \
  /path/to/kist-finance-7a196af9550378d24d196e666ac0a0f6dd37cb48 \
  /path/to/repaired-kist \
  /tmp/kist-recordings
```

The script starts each app with an isolated data directory, writes
`/api/app-config`, suppresses the first POST response only in the upstream run,
and records Chromium video. The fixed waits in this recording script are for
readable pacing; the contract run itself uses observable conditions only.
