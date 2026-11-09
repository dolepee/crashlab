# CrashLab 0.5.10 operator replay — 83992f2

This archive is candidate evaluator evidence for `83992f22a4a5ef168636a3b4676244b353968073` (0.5.10). The exact-head review, official installed replay and catalog repin are owner-owned; none is claimed here. It is not a new model repair. The 0.5.9 evidence remains at `../2026-11-09-v059-replay/`.

## Replay scope
Produced through the supported `repair_workflow.prepare -> run -> export -> import -> run` path with operator grants and local environment bindings. The source behavior is the published native frozen case, not scratch input and not an older bundle. Baseline app checkout: `7a196af9550378d24d196e666ac0a0f6dd37cb48`; exact retained repair: `patch/model-repair.patch`. A fresh checkout was used for the import baseline and repair.

The new evaluator rejects repeated selected reconciliation form identities and requires exactly one selected readback query value. The Kist JSON retry case is unchanged at the source behavior boundary; the compatible bundle records the new evaluator pin.

## Three boundaries
- `before_delivery`: baseline FAIL 1/1, repaired PASS 1/1.
- `after_commit`: baseline FAIL 2/1, repaired PASS 1/1.
- `restart_after_commit`: baseline FAIL 2/1, repaired PASS 1/1.
Controls were configured and passed in every case (6/6 aggregate).

## Files
- `source/frozen-case.json` — published native frozen case.
- `case/contract.json` — actual saved primary experiment case.
- `case/import-contract.json` — actual saved import experiment case, including local bindings.
- `patch/model-repair.patch` — exact retained app repair, explicitly labeled.
- `raw/experiments/*.json` — primary and import experiment receipts.
- `raw/runs/*.json` — raw baseline, repaired, import-baseline and import-repair receipts.
- `export/regression.json` — new exported regression bundle pinned to this evaluator.
- `export/bindings.example.json` — bindings example; no secrets.
- `source/source.json` — candidate, app, evaluator and receipt hashes.
- `raw/workflow.log` — raw supported-workflow output.

Primary `e4f60e43b7a741be957e8440f8190949`: baseline REPRODUCED, repair VERIFIED.
Import `22a181a6953e4a7d925eb30d152cc642`: baseline REPRODUCED, repair VERIFIED.

Old 0.5.9 instructions, bundles, evidence and pins are preserved unchanged.
