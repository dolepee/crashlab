---
name: crashlab
description: Verify a lost-response repair on a disposable local browser app, preserving the intended behavior and current source evidence.
---

Start from the user's local project and intended behavior. Inspect the app's launch instructions, browser form, write endpoint, SQLite schema and retry behavior. Explain missing dependencies or unsupported storage before starting. Never change the evaluator to accommodate an app.

Build a case using the schema in the bundled cases and external Kist example. Map captured request fields to persisted intent and content fields; include two deliberate operations as a control. Use an explicit absolute app.start.cwd in the Git project. App startup must use disposable data outside tracked source. Keep contracts and evidence outside the app checkout.

Call crashlab_prepare with project_path, the user's intent and the case object. Show the proposed behavior, interruption and assertions. Do not claim the user approved an assertion they did not provide or confirm. Resolve ambiguity with the user before testing a guessed requirement.

Call crashlab_experiment_run to establish a valid failing baseline. ERROR and INVALID require setup repair, not application repair claims. A passing initial case means no repair was reproduced. After a valid failing baseline, edit only the application; rerun the experiment by ID. Do not alter its frozen case or the evaluator. VERIFIED requires a changed source revision, passing primary assertion and passing legitimate-use control after a reproduced failure.

Before reporting completion, call crashlab_experiment_status. STALE means project files changed since the latest run; rerun. Report the experiment ID, current status, configured scope, failed/passed behavior and control result. Ignored files, dependencies and runtime environment are outside source freshness coverage. Never describe these same-user hashes as adversarial isolation or all-purpose correctness proof.
