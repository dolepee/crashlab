# Kist external repair example

This example targets unmodified `grzella/kist-finance` at
`7a196af9550378d24d196e666ac0a0f6dd37cb48`. Kist stores savings-goal records;
this is not a payment or money-movement experiment.

## Current 0.5.7 installed workflow

Follow the [current replay instructions](CURRENT_REPLAY.md) and use
[`recovery-regression-v057.json`](recovery-regression-v057.json). The
[November 9 native record](evidence/2026-11-09-native/README.md) starts with a
pristine app path, intended behavior and runtime bindings: Hermes constructs
its own case, establishes a valid baseline, edits the application and verifies
the same frozen experiment with normal approvals. It also records an initial
control-wait correction before that baseline.

Acknowledgement loss and server restart produce two goals before repair and one
afterward, with each deliberate-use control at 2/2. The before-delivery baseline
saves one goal but fails the configured Idempotency-Key requirement; do not
interpret that as duplicate state. A separate Mac/operator replays the portable
regression on new bindings. The native session's own clean-checkout reuse occurs
inside the same conversation. The actual new model patch passes 274 application
tests independently, including the quality module. Earlier patches have their
own test counts and verifier identities.

The remaining sections preserve older executions and reproduction examples.

## November 7, 2026 execution

A fresh Hermes Agent v0.23.0 session using `gpt-6.1-sol` called the installed
`crashlab_run` tool, reproduced the defect, edited a disposable application
checkout, and called the exact same case again. It did not receive the earlier
repair patch. The verifier was release `38eed000297a3652cc4a8215f099b6960e42dd1c`;
a separate replay used theme-update release
`96f2f62935159158d05a13604f33a876eeca1c99`, with unchanged evaluator files.

| Check | Unmodified upstream | Fresh model repair |
| --- | --- | --- |
| Lost response after committed create | Valid fault | Valid fault |
| Goals for one retried intent | 2 / expected 1, FAIL | 1 / expected 1, PASS |
| Two deliberate identical goals | 2 / expected 2, PASS | 2 / expected 2, PASS |
| Frozen-contract comparison | Reproduction established | Repair verified |

The repair adds a durable create-intent receipt and atomically commits the goal,
metadata and audit record. The browser retains the same intent after an
unconfirmed response. Legacy clients still create independently, and existing
databases receive an additive table. See [repair-2026-11-07.patch](repair-2026-11-07.patch).

Evidence is in [evidence/2026-11-07](evidence/2026-11-07):

- `summary.json`: independent replay results, matching case digests, evaluator
  hashes, repair patch hash and test results.
- `selected-tool-events.jsonl`: the actual CrashLab calls/results, application
  edit events and final agent result. This is a selected excerpt, not the whole
  transcript; the full transcript is retained privately. Local audit-root paths
  are normalized to `$AUDIT_WORKSPACE` in this excerpt.
- `before-*.png` and `after-*.png`: direct browser screenshots from independent
  replay, including the two-intent controls.

The agent accurately reported that its new tests could not run because `pytest`
was missing. The operator subsequently installed the upstream development test
requirements and ran the tests independently; the summary records those results.

## Reproduce locally

Use disposable copies of the pinned upstream application. Install its
`requirements.txt` into an application virtual environment. Install CrashLab's
Node dependency and a browser as described in the root README. Set `CHROME_PATH`
when using an existing Chrome/Chromium executable.

Generate a case for your checkout and application interpreter:

```bash
python external/kist/run_kist.py \
  --app /absolute/path/to/kist-checkout \
  --python /absolute/path/to/kist-venv/bin/python \
  --output /tmp/kist-case \
  --write-contract-only
```

In Hermes, ask it to call `crashlab_run` with the generated
`/tmp/kist-case/contract.json`. Let it inspect and repair the disposable app, then
run that exact same case again. Keep the evaluator and case outside the app's
editable checkout. The result must include a valid delivered fault, primary
assertion and passing legitimate-use control; an error is not a reproduction.

To replay the included patch instead of generating a new one, apply it only to
a separate copy of the pinned upstream checkout:

```bash
git -C /absolute/path/to/repaired-copy apply /absolute/path/to/crashlab/external/kist/repair-2026-11-07.patch
```

Omitting `--write-contract-only` runs the case directly through `crashlab_core`
and saves its result. That standalone helper is not a Hermes agent execution.
Each run starts the app with fresh disposable data; Kist has no CrashLab-specific
reset endpoint. The case checks stored goal content as well as counts.

## Limits and historical evidence

This is one measured repair of one lost-response path. It does not establish
arbitrary bug discovery, production safety, or all possible idempotency behavior.
The Python/Node/browser dependencies and local SQLite access must be configured.

The earlier `repair.patch`, `repair-live.log`, top-level `evidence/` summaries and
`demo/crashlab-kist-live.mp4` remain historical artifacts from a different repair.
They are not the November 7 model trace or a final submission video. The new
patch and evidence above are explicitly named to keep those releases distinct.

## Recovery campaign and fresh-session regression

For the historical reviewed 0.5.6 evaluator, follow the
[0.5.6 replay instructions](REPLAY_V056.md) and use
[`recovery-regression-v056.json`](recovery-regression-v056.json). Its separate
process import, pristine baseline and retained-patch verification were measured
with that evaluator. The older bundle below remains historical and may be
rejected by newer evaluators, as intended.

The latest recovery examples extend the same create operation across delivery
uncertainty and process restart; they do not claim unrelated bug families.

- [Native registry comparison](evidence/2026-11-07-recovery/summary.json): unmodified
  upstream, deliberately injected memory-only negative control, and the older
  retained real-Hermes durable patch. The memory-only variant passes acknowledgement
  loss but duplicates after actual process restart. It is explicitly synthetic.
- [New Hermes-constructed case and repair](evidence/2026-11-07-hermes-recovery/summary.json):
  a fresh model session built its own case, found the restart failure, edited the
  application and reran the same case. This record includes actual selected stream
  events, complete normalized measurements and the full patch including six new
  tests. The seeded frontend retry code is identified separately from model edits.
  279 application tests pass independently; the older 281-test result concerns a
  different patch with a different set of added tests.
- [Reusable regression bundle](recovery-regression.json): derived from the
  model-constructed behavior, explicitly prepared and replayed with the corrected
  evaluator. Its assertions and journey are preserved; application paths are bindings.
  The historical model run names its own verifier, and does not pretend to have used
  the later release. Install the evaluator source pin recorded in this bundle, or a
  release with identical evaluator hashes. Import refuses a mismatch.

Start from the same pinned public upstream checkout and the documented Kist
application environment. From the installed plugin directory, create a local
bindings file containing `project_path` and absolute executable `app_python`, then:

```bash
python crashlab_replay.py import --bundle external/kist/recovery-regression.json --bindings /path/to/bindings.json
python crashlab_replay.py run --experiment RETURNED_EXPERIMENT_ID
# The reproduced failure is expected to return exit code 1.
git -C /path/to/kist apply /path/to/installed/crashlab/external/kist/evidence/2026-11-07-hermes-recovery/repair-from-upstream.patch
python crashlab_replay.py run --experiment RETURNED_EXPERIMENT_ID
python crashlab_replay.py report --experiment RETURNED_EXPERIMENT_ID
```

The fresh upstream baseline here is unmodified Kist, rather than the injected
memory-only source used in the model session. The same frozen behavior reproduces
its after-commit and restart failures; the retained model patch passes all three
boundaries with each legitimate-use control at 2/2. A separate interpreter process
runs each command. This is operator-controlled replay, not an independent end-user
trial. Application dependencies are preinstalled and not packaged in the bundle.

For a case generated from this repository's developer example instead, add
`--recovery-campaign` to `run_kist.py`. Do not modify its assertions after preparing
an experiment. A restart is owned by CrashLab, uses the same port/data directory,
retains the observed records and does not repeat setup or reset. External URL
overrides cannot own that restart. A startup command that wipes the records fails.

The new repair's legacy tokenless callers remain unprotected. The additive schema
upgrade has focused tests; these examples do not establish a generally safe
production migration or correctness outside the configured operation.
