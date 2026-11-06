# CrashLab

CrashLab is a Hermes plugin for reproducing and repairing a failure that normal
browser tests miss: the server commits a write, the response is lost, the user
retries, and the application ends up with two records for one intent.

It is deliberately narrow. One contract describes the journey, the fault, the
state probe and the expected result. The same contract must fail on the broken
application and pass on the repaired one. A control journey checks that the
repair did not simply block legitimate later purchases.

> CrashLab is a working vertical slice, not a finished testing product. The
> fixtures are small, deliberately buggy applications. It proves one failure
> class across two example applications; it does not discover arbitrary bugs or
> guarantee general autonomous repair.

## What is in the box

- `crashlab_validate` — validate a saved contract without running it.
- `crashlab_run` — run a contract and read persisted state through `/state`.
- `crashlab_compare` — run the same contract against a buggy and a repaired URL.
- `crashlab_list` — list bundled contracts.
- `crashlab_runner.js` — Playwright runner. It suppresses the response after the
  upstream server has processed the request, then records screenshots and the
  persisted records.
- `contracts/checkout-lost-response.json` — checkout fixture.
- `contracts/booking-lost-response.json` — booking fixture.
- `server.py` and `booking.py` — the two deliberately buggy fixture apps.
- `desktop/plugin.js` — a native Hermes Desktop pane that displays saved
  evidence through `ctx.rest`.
- `dashboard/plugin_api.py` — the pane's evidence endpoint.

## Evidence from the development run

The checkout comparison produced:

- Buggy application: 2 persisted orders; expected 1; fail.
- Hermes-repaired application: 1 persisted order; expected 1; pass.
- Control: 2 distinct purchases; 2 orders; pass.

The booking fixture reproduced the same pattern:

- Buggy: 2 bookings; expected 1; fail.
- Fixed: 1 booking; expected 1; pass.
- Control: 2 distinct bookings; 2 bookings; pass.

See `DAY2.md`, `DAY3.md` and `DAY4.md` for the runs, and `docs/repair-report.png` and `docs/booking-report.png` for the rendered evidence. The checked-in HTML/PNG evidence is
outside the plugin runtime; generated evidence is ignored by git.

## Install

Requirements:

- Hermes Agent v0.23 or newer.
- Node.js, `npm`, and Chromium available locally.
- `playwright-core` installed for the runner.

```bash
npm install
# Chromium: the runner uses CHROME_PATH or /usr/bin/chromium.
```

Copy or link this directory into `$HERMES_HOME/plugins/crashlab`, then:

```bash
hermes plugins enable crashlab
hermes plugins validate "$HERMES_HOME/plugins/crashlab" --install-deps
hermes plugins doctor "$HERMES_HOME/plugins/crashlab"
```

The Desktop half is opt-in and appears in **Capabilities → Plugins**. Its
evidence endpoint reads `CRASHLAB_EVIDENCE_DIR` when set; otherwise it reads
`evidence/` beside the plugin. The endpoint returns saved runs only. It does not
run tests itself.

## Run a fixture

Start the buggy and repaired checkout servers:

```bash
python3 server.py                       # 127.0.0.1:8765, buggy
PORT=8766 BUGGY=0 python3 server.py     # 127.0.0.1:8766, repaired fixture
```

Run the saved contract against each:

```bash
node crashlab_runner.js contracts/checkout-lost-response.json
CHROME_PATH=/usr/bin/chromium node crashlab_runner.js /tmp/checkout-fixed.json
```

For the second fixture:

```bash
PORT=8785 BUGGY=1 python3 booking.py
PORT=8786 BUGGY=0 python3 booking.py
node crashlab_runner.js contracts/booking-lost-response.json
```

`BUGGY=0` is a prepared repaired fixture for comparison. The Day 3 gate is
different: a real Hermes agent edited the failing application and the unchanged
contract was rerun against that edit. `DAY3.md` records that run.

## Contract format

A contract has:

- `app.base_url` and optional `app.reset_url`.
- `fault.route`, `fault.mode` and `fault.occurrence`.
- `journey` — browser steps such as `goto`, `click`, `wait_for_text`, `text`
  and `screenshot`.
- `state` — the `/state` URL, the array path and the expected length.
- `control` — a separate journey and state expectation used to catch an
  over-restrictive repair.

The runner exits non-zero when the primary or control assertion fails. A failed
check is evidence, not a pass.

## Demo

`demo/crashlab-demo.mp4` is an 88-second terminal replay built from the real
buggy run, the Hermes repair summary and the repaired run. It is a replay, not
a live screen capture; the opening frame says so. The source is in
`demo/index.html`; `demo/record_demo.js` records it with Playwright.

## Scope and limitations

- Two local fixtures only; no external application has been integrated yet.
- The Desktop pane has been launched in the Hermes Desktop app against the saved
  run. See `docs/desktop-pane.png`.
- The runner requires a local Chromium and `playwright-core`.
- The fault is a suppressed response after commit; this is not a general chaos
  testing engine.
- The repair evidence covers the checkout fixture. It is one known defect class,
  not a claim of general repair.

## Disclosure

CrashLab starts local fixture servers, launches headless Chromium, writes
SQLite data and screenshots under `data/` and `evidence/`, and reads the saved
evidence file through the Desktop backend. It makes no network calls to third
parties and stores no credentials.
