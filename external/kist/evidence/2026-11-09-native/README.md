# Kist: installed repair and portable regression

This record uses public Kist Finance at `7a196af9550378d24d196e666ac0a0f6dd37cb48`
and the installed CrashLab `529c22f9c77f6b65b5432c24e9f0aa3b8731cfa4` evaluator
(version 0.5.7). Source merge `944c5de75518987b9a78d04b03d90979a7ddb4c9` has an
identical tracked tree. Kist savings goals are records, not payments.

## Native model repair

Saint operated one normal interactive Hermes v0.23 session. The model received
the pristine app path, intended behavior and runtime bindings. It inspected the
app, constructed the case, called CrashLab's installed tools, edited the app and
reran the frozen experiment. No prepared case, repair patch or implementation
diagnosis was supplied. The session includes 21 actual Allow-once approvals;
it used neither `--yolo` nor mocked approval callbacks.

| Boundary | Original application | Model repair | Legitimate-use control |
| --- | --- | --- | --- |
| Before delivery | 1/1 saved; configured header-identity check **FAIL** | 1/1, **PASS** | 2/2 in each run |
| After commit | 2/1 saved, **FAIL** | 1/1, **PASS** | 2/2 in each run |
| Restart after commit | 2/1 saved, **FAIL** | 1/1, **PASS** | 2/2 in each run |

The before-delivery baseline made two POST requests and saved one goal. It failed
because the case explicitly selected `Idempotency-Key`, which the original app
did not send. That is a configured protocol-identity failure, not a duplicate,
lost saved record or absence of a real retry. After commit and restart genuinely
produced duplicate records. The repaired run supplies and preserves the selected
header and matches its successful reply ID to the independently observed row.

The first prepared case encountered a control re-render race (4/6 controls).
The agent revised the wait while app source was still pristine, before the
reported baseline. The final experiment `3f4b87aa1f714475b603f6e156f8e0fb` then
moved from REPRODUCED to VERIFIED without changing its case or evaluator.
Its canonical case hash is
`51771858c168a80c4d2d6d81641a73820b104893e62536daadcf6cf8738a6968`.

The model patch stores intent keys durably with a unique index, keeps a client
key through an unconfirmed retry, and permits a new key after a confirmed create.
The new patch independently passes **274 application tests, no skips**, including
the quality module after installing upstream runtime and development requirements.
The earlier 279-test result belongs to a different retained patch.

## Reuse on a separate machine

Within the same Hermes session, the model exported the case, imported it into a
clean Linux checkout and replayed it before and after applying its retained patch.
This is fresh-checkout reuse, not a separate fresh conversation or second repair.

A separate Codex operator then used the documented installed CLI on a Mac, with
new application/interpreter bindings and real interactive plan approvals. Import
started PREPARED. It earned REPRODUCED on pristine source, then VERIFIED after
applying the exact retained model patch. All three repaired boundaries saved 1/1,
with 6/6 controls. Re-export preserved both behavior and launch-template hashes.
This is an independent operator replay, not unrelated-human adoption.

Follow the [current replay instructions](../../CURRENT_REPLAY.md).

## Inspect the records

- [Summary](summary.json): measured identities, results and qualifications.
- [Original frozen case](frozen-case.json): unmodified executed JSON; its local
  Linux paths are historical bindings, not portable setup instructions.
- [Original baseline](baseline.json) and [repaired result](repaired.json): retained
  runner receipts with actual request, record and restart observations.
- [Selected actual tool events](selected-tool-events.jsonl): preparation, runs,
  application patches and export/import calls with their returned results.
  Workspace/runtime path prefixes are normalized. This is a selected excerpt,
  not the full transcript. Embedded recorded hashes identify original executed
  bytes, not this normalized JSONL. Internal model reasoning is not included.
- [Actual model patch](model-repair.patch), SHA-256
  `f86a44ad078529136c45ca11ea1fea7958900130bfeabb77ee9626337da1dfa3`.
- [Independent application test log](independent-app-tests.txt).
- [Separate Mac replay](independent-mac-replay.json): bindings changed; frozen
  behavior and launch hashes did not. Unmodified local approval transcripts and
  experiment records are retained privately.
- [Portable regression](../../recovery-regression-v057.json): use this for import.

Restart preserves that scenario's database and port; other campaign boundaries
start with their own disposable data. The record does not establish whole-app
correctness, production migration safety, browser-state-loss recovery, adoption,
or a speed/cost advantage. Legacy callers without an intent key remain outside
retry protection. Source freshness excludes dependencies and ignored runtime
configuration. Full native PTY/session records are retained privately; the
selected records here do not reconstruct missing events.
