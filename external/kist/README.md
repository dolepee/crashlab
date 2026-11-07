# Kist external repair example

This example targets unmodified `grzella/kist-finance` at
`7a196af9550378d24d196e666ac0a0f6dd37cb48`. Kist stores savings-goal records;
this is not a payment or money-movement experiment.

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
