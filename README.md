# CrashLab

CrashLab is a Hermes plugin that checks what an application saved after a
response was lost. A browser can show an error even after the server committed:
retrying may create a duplicate record. CrashLab reproduces that interruption,
reads the persisted state, and verifies the repair against the same case.

A legitimate-use control checks that fixing a retry still allows two deliberate
operations. The comparison freezes the contract and checks evaluator identity
across the before/after runs. Results cover the configured assertions, not every
possible application failure.

## A reproduced external repair

On November 7, 2026, a fresh Hermes 0.23 session used CrashLab on the unmodified
[Kist Finance](https://github.com/grzella/kist-finance) savings-goal application,
then wrote a repair in a separate checkout:

- Before: retrying one interrupted create saved **two goals**, expected one.
- After: the same saved case produced **one goal**, and the control still
  created **two deliberate goals**, including identical values.
- A separate Hermes registry comparison reproduced both results using one frozen
  contract. The evaluator and the agent's case remained unchanged.

The [patch, selected tool events, screenshots and replay summary](external/kist/README.md)
are included. This is one measured application repair, not a general autonomous
repair claim. Kist goals are savings records, not payments or money movement.

## Use it in Hermes

After installation, ask Hermes to run a saved case with `crashlab_run`. For a
bundled first run, use `cases/checkout-local.json` to see the duplicate and
`cases/checkout-local-fixed.json` to see the passing implementation. These cases
start disposable local fixture apps automatically. The dashboard also lets you
select and run a saved case, inspect its result, and retry after setup errors.

For your own app, configure its launch command or loopback URL, browser journey,
write endpoint and state assertion. SQLite observers read the database directly;
HTTP observers trust the state returned by the app. JSON request-field mappings
can bind stored rows to the submitted intent and payload. See the external Kist
example for a real application and the contract notes below for the supported
surface.

## Included tools

- `crashlab_validate`: validate a case without running it.
- `crashlab_run`: execute a saved browser case and persist its result.
- `crashlab_compare`: run one frozen case against broken and repaired URLs.
- `crashlab_list`: list bundled contracts.
- Campaign list/validate/run tools: an optional, separate inventory-contention
  experiment. See [CAMPAIGNS.md](CAMPAIGNS.md); mixed browser campaigns remain
  validation-only pending network confinement.

The checkout and booking apps are deliberately buggy fixtures. Historical
fixture reports remain in `proof/` and the day-by-day development notes; they
should not be confused with the external Kist application.

## Install

Requirements:

- Hermes Agent v0.23 or newer.
- Node.js, `npm`, and Chromium available locally.
- `playwright-core` installed for the runner.

Fresh install from the public repository:

```bash
hermes plugins install dolepee/crashlab --enable
# When Hermes asks to install the Node sidecar, answer yes.
# If installed non-interactively, finish the sidecar once:
cd "$HERMES_HOME/plugins/crashlab" && npm ci
```

Then confirm:

```bash
hermes plugins validate "$HERMES_HOME/plugins/crashlab" --install-deps
hermes plugins doctor "$HERMES_HOME/plugins/crashlab"
```

The Desktop half is opt-in and appears in **Capabilities → Plugins**. It reads
`CRASHLAB_DATA_DIR` (default `~/.local/share/crashlab`) and scans the bundled
`contracts/` plus `CRASHLAB_CASE_DIR` (default `cases/`). Select a case and press
**Run case**; the run starts the app when the case has an `app.start` block and
persists `latest-run.json` for the pane. The same evidence is available from
`/crashlab`, `/crashlab runs` and `/crashlab run <case.json>` in a Hermes chat.

## Run a fixture

Start the buggy and repaired checkout servers:

```bash
PORT=8765 BUGGY=1 CRASHLAB_DB=data/orders-8765.db python3 server.py
PORT=8766 BUGGY=0 CRASHLAB_DB=data/orders-8766.db python3 server.py
```

Run the saved contract against each:

```bash
CHROME_PATH=/usr/bin/chromium node crashlab_runner.js contracts/checkout-lost-response.json
CHROME_PATH=/usr/bin/chromium node crashlab_runner.js contracts/checkout-lost-response.json --base-url http://127.0.0.1:8766
```

For the second fixture:

```bash
PORT=8785 BUGGY=1 CRASHLAB_DB=data/bookings-8785.db python3 booking.py
PORT=8786 BUGGY=0 CRASHLAB_DB=data/bookings-8786.db python3 booking.py
CHROME_PATH=/usr/bin/chromium node crashlab_runner.js contracts/booking-lost-response.json
CHROME_PATH=/usr/bin/chromium node crashlab_runner.js contracts/booking-lost-response.json --base-url http://127.0.0.1:8786
```

`BUGGY=0` is a prepared repaired fixture for comparison. The Day 3 gate is
different: a real Hermes agent edited the failing application and the unchanged
contract was rerun against that edit. `DAY3.md` records that run.

## Contract format

A contract has:

- `app.base_url` or `app.start` (command, cwd, Python, setup requests), and
  optional `app.reset_url`.
- `fault.route`, `fault.mode` and `fault.occurrence`.
- `journey` — browser steps such as `goto`, `click`, `text` and `screenshot`.
  Waits are declarative (`wait_for_fault`, `wait_for_count`, `wait_for_value`,
  `wait_for_any`); contracts do not execute JavaScript on the host or in the
  page.
- `state` — the expected length and the observer. `mode: "sqlite"` uses a
  read-only database probe outside the app; `mode: "http"` uses the app's
  `/state` endpoint and is not independent of the application under repair.
- `control` — a separate journey and state expectation used to catch an
  over-restrictive repair.

The runner exits non-zero when the primary or control assertion fails, or when
the configured fault did not match and commit a write. A failed check is
evidence, not a pass.

## Demo

`demo/crashlab-demo.mp4` is an 88-second terminal replay built from the real
buggy run, the Hermes repair summary and the repaired run. It is a replay, not
a live screen capture; the opening frame says so. Recording source is maintained privately.

`demo/kist-demo.mp4` is an 88-second replay of the external Kist run: the
unmodified before run, the frozen contract, the Hermes patch and the repaired
run. Recording source is maintained privately.

`demo/crashlab-kist-live.mp4` is a 26.5-second **live browser recording**, not
an evidence replay. It shows the unmodified Kist checkout creating a second
goal after a dropped response, then the same intent against the Hermes-repaired
checkout creating one. Recording source is maintained privately.

## External application example

The `external/kist` directory contains a browser contract against an
unmodified Kist checkout, the Hermes repair patch, before/after screenshots and
the exact replay command. It is an external adoption example, not a bundled
fixture. The contract checks one recovery intent against one goal and two
deliberate goals against two goals; the Kist database is observed directly.

The contract now carries an `app.start` block. `crashlab_run` starts Kist
through the same local-app path as every other case; `run_kist.py` builds that
block and no longer runs a separate application launcher.

`cases/checkout-local.json` and `cases/checkout-local-fixed.json` are the
fixture transfer check. `cases/flaskr-create.json` is the independent application
check against Pallets' Flaskr tutorial at commit
`49943b5a94413a530761350f04e896f76e8d620e`; it registers a user, creates one
post and observes one row in SQLite through the same `app.start` path. See
[external/flaskr/README.md](external/flaskr/README.md).

See [external/kist/README.md](external/kist/README.md). Do not describe the
before run as repaired or the patch as a general autonomous repair.

![CrashLab dashboard tab showing one passing Flaskr run and the Kist before/after pair](docs/dashboard-pane.png)

The image above is the actual Hermes dashboard tab, reading saved evidence. The
Desktop pane is a separate surface and needs a running Hermes Desktop session to
capture.

## Scope and limitations

- Two local fixtures plus the external Kist example; Kist is one app and one
  Hermes one-shot repair, not proof of broad compatibility.
- The second application check is Flaskr, a public tutorial app; the checkout
  cases remain bundled fixtures.
- The Desktop pane was launched in Hermes Desktop for the earlier evidence view
  in `docs/desktop-pane.png`; the newest run/comparison view is wired to the
  persisted run but has not been visually recaptured.
- The runner requires a local Chromium and `playwright-core`.
- The fault is a suppressed response after commit; this is not a general chaos
  testing engine. `mode: "http"` is app-supplied and cannot prove the app did not
  tamper with its own state; use `sqlite` for the bundled fixtures.
- The repair evidence covers Kist and the bundled checkout fixture. It is one
  known defect class, not a claim of general repair.
- `app.start` runs a local command by design. It receives only PATH, LANG, TZ,
  HOME, PORT and contract-declared environment values; it does not inherit the
  agent's provider or API credentials.

## Disclosure

CrashLab starts local fixture servers, launches headless Chromium, writes
SQLite data and screenshots under `data/` and `evidence/`, and reads the saved
evidence file through the Desktop backend. It makes no network calls to third
parties and stores no credentials.

## Browser verification guarantees

Fault cases require an empty independently observed scoped baseline. A dirty
baseline is INVALID, not a passing repair. `record_fields` checks exact stored
values; `request_fields` maps database columns to top-level JSON request fields
on the fault route, binding every observed row to a distinct captured request.
Checkout cases check both the intent key and item. These checks are configured
per application; cardinality alone does not establish full business correctness.

Comparisons snapshot the contract once, use it for both runs, record its SHA-256
and evaluator hashes, and reject evaluator changes during the pair. These are
integrity checks in a trusted local installation, not an operating-system sandbox.

Install a browser with `npx playwright-core install chromium`, or set
`CHROME_PATH` to an existing Chromium/Chrome executable. Run real-browser
regressions with `CRASHLAB_BROWSER_TESTS=1 python -m unittest test_browser_integrity`.
