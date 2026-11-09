# CrashLab 0.5.9 final operator replay — 17b8f34

This archive is evidence for the exact reviewed source merged as `17b8f34944ff9c06bb0d0cd67569605150c90f1d` (reviewed head `93e2f1aa1b666df5a018f18e79fa9cbba449dfca`, identical tree). It is not a new model repair and does not change the catalog.

## Replay scope
Produced through the supported `repair_workflow.prepare -> run -> export -> import -> run` path with operator grants and local environment bindings. The source behavior is the published native frozen case, not scratch input and not the old bundle. Baseline app checkout: `7a196af9550378d24d196e666ac0a0f6dd37cb48`; exact retained repair: `patch/model-repair.patch`. A fresh checkout was used for the import baseline and repair.

## Three boundaries
- `before_delivery`: baseline FAIL 1/1, repaired PASS 1/1.
- `after_commit`: baseline FAIL 2/1, repaired PASS 1/1.
- `restart_after_commit`: baseline FAIL 2/1, repaired PASS 1/1.
Controls were configured and passed in every case.

## Files
- `source/frozen-case.json` — published native frozen case.
- `case/contract.json` — actual saved primary experiment case.
- `case/import-contract.json` — actual saved import experiment case.
- `patch/model-repair.patch` — exact retained app repair, explicitly labeled.
- `raw/experiments/*.json` — primary and import experiment receipts.
- `raw/runs/*.json` — raw baseline, repaired, import-baseline and import-repair receipts.
- `export/regression.json` — new exported regression bundle.
- `export/bindings.example.json` — bindings example; no secrets.
- `source/source.json` — candidate, review, CI, app, evaluator and receipt hashes.
- `raw/workflow.log` — supported workflow output.

Primary `43bf08306c8d4fd1bc0b5eef652b8cf4`: baseline REPRODUCED, repair VERIFIED.
Import `f25120e2266849c3a8e969ac866aa1d2`: baseline REPRODUCED, repair VERIFIED.

Old evidence, bundles and pins are untouched.
