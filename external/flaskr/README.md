# Flaskr guided repair

This example uses Pallets' Flask tutorial at
`49943b5a94413a530761350f04e896f76e8d620e`. It is a disposable tutorial
application, with generated test users and posts, not an adoption or production
safety claim.

## November 7 repair acceptance

A real Hermes Agent v0.23.0 session using `gpt-6.1-sol` received the local
project path and intended behavior: one post after retrying an unconfirmed
submission, while two deliberate identical submissions remain possible. The
operator supplied the installed application interpreter but no case JSON.
Hermes inspected the application, called `crashlab_prepare` to freeze its own
case, reproduced a valid failure, edited the app and reran the same experiment.
The verifier candidate was `06e338889d470c02559d2e8c09340832e905fbe1`.

| Check | Original app | Repaired app |
| --- | --- | --- |
| One native POST, committed before response suppression | Valid fault | Valid fault |
| Same POST resubmitted by browser reload | 2 posts / expected 1, FAIL | 1 / expected 1, PASS |
| Two deliberate identical submissions | 2 / expected 2, PASS | 2 / expected 2, PASS |
| Workflow status | REPRODUCED | VERIFIED |

The primary assertion observes SQLite directly and checks submitted title/body
content. The control checks cardinality and exact content, not per-intent
identity: the original app has no persisted submission key for that mapping.
The patch adds a per-form key, author/key uniqueness and payload-conflict checks.
Legacy tokenless POSTs remain accepted and are not retry-protected. Existing
databases require migration; these results cover newly initialized databases.

The model accurately reported that pytest was missing from its interpreter.
Its five focused standard-library tests passed. The operator independently ran
all 29 tutorial tests using an existing compatible test environment. The
retained patch matches the primary session's final source fingerprint; later
comment-only reminder probes are excluded from that patch.

A prior guided trial ended in ERROR, with no repair claimed. It exposed a race
between native navigation failure and browser reload, plus a contradictory
control signature. Those issues were corrected in source PR #9. This record is
subsequent acceptance, not a claim of first-attempt reliability. A separate
ordinary Hermes + Playwright/SQLite baseline also completed a repair; no general
speed or cost advantage is established.

## Inspect the evidence

- [Repair patch](repair-2026-11-07.patch), including regression tests.
- [Summary](evidence/2026-11-07/summary.json), with case/evaluator digests and
  measured before/after results.
- [Selected actual tool events](evidence/2026-11-07/selected-tool-events.jsonl).
  Workspace/profile paths are normalized. Stream outputs marked
  `stream_excerpt_truncated` were truncated by the host's event stream and are
  excerpts, not complete JSON tool responses. Full local trace and persisted
  experiment records are retained privately.
- [Configurable case template](contract.template.json). Its paths differ from
  the executed case, so do not recompute its digest as the original receipt.

## Reproduce

Clone the pinned Flask source into a disposable Git checkout. Install the
`examples/tutorial` application into an application virtual environment, and
install CrashLab's documented Node/browser dependencies.

Ask Hermes to check that local project with the intended behavior above. It can
prepare its own case using the installed skill and tool schema. Alternatively,
configure `contract.template.json`: set the absolute app directory and Python
interpreter, then remove `setup_required`. Ask Hermes to pass that case object,
the Git root and intended behavior to `crashlab_prepare`.

Run the returned experiment ID before changing the application. After a valid
REPRODUCED result, either let Hermes repair the app or apply the retained patch
to that disposable checkout:

```bash
git -C /absolute/path/to/flask-checkout apply /absolute/path/to/crashlab/external/flaskr/repair-2026-11-07.patch
```

Rerun the same experiment ID. Inspect the primary and control assertions and
call `crashlab_experiment_status` before relying on VERIFIED. Editing the app
again makes evidence STALE. Evaluator updates require a new experiment; do not
rewrite frozen identity values to retain an old verdict.

## Historical passing workflow

`cases/flaskr-create.json` and `evidence/created.png` record an earlier register,
login and create workflow without injected failure. That passing transfer is
separate from the native POST repair above. To use it, configure its app checkout
and interpreter and call the installed `crashlab_run` tool; `crashlab_core.py`
is a library, not an executable command-line entry point.


## Recovery replay on the corrected evaluator

The [three-boundary replay](evidence/2026-11-07-recovery/summary.json) uses the
reviewed `6eaf34f` evaluator through native Hermes registry dispatch. The original
retained Flaskr patch reproduces the upstream failure and passes before-delivery,
after-commit and restart-before-retry cases, each with its 2/2 deliberate-use
control. This is a retained-patch replay, not a new model repair or end-user trial.
The launch template initializes a missing disposable database and preserves it
on restart; restarting against a fresh empty database would not test retention.
Existing production databases still need a migration, and legacy tokenless POSTs
remain outside the retry guarantee.
