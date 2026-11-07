---
name: crashlab
description: Verify a lost-response repair on a disposable local browser app, preserving the intended behavior and current source evidence.
---

Start from the user's local project and intended behavior. Inspect the app's launch instructions, browser form, write endpoint, SQLite schema and retry behavior. Explain missing dependencies or unsupported storage before starting. Never change the evaluator to accommodate an app.

Use the structured contract schema exposed by crashlab_prepare. The quick reference below covers the supported path; inspect a bundled example only for an unclear detail, rather than reading evaluator internals. Map captured request fields to persisted intent and content fields; include two deliberate operations as a control. Use an explicit absolute app.start.cwd in the Git project. App startup must use disposable data outside tracked source. Keep contracts and evidence outside the app checkout.

Call crashlab_prepare with project_path, the user's intent and the case object. Show the proposed behavior, interruption and assertions. Do not claim the user approved an assertion they did not provide or confirm. Resolve ambiguity with the user before testing a guessed requirement.

Call crashlab_experiment_run to establish a valid failing baseline. ERROR and INVALID require setup repair, not application repair claims. A passing initial case means no repair was reproduced. After a valid failing baseline, edit only the application; rerun the experiment by ID. Do not alter its frozen case or the evaluator. VERIFIED requires a changed source revision, passing primary assertion and passing legitimate-use control after a reproduced failure.

Before reporting completion, call crashlab_experiment_status. STALE means project files changed since the latest run; rerun. Report the experiment ID, current status, configured scope, failed/passed behavior and control result. Ignored files, dependencies and runtime environment are outside source freshness coverage. Never describe these same-user hashes as adversarial isolation or all-purpose correctness proof.

When the user wants ongoing verification during edits, call crashlab_experiment_watch
with the experiment ID and enabled=true. The project-scoped pre_verify hook asks
for another turn when matching absolute-path edits lack current VERIFIED evidence.
Hermes caps these reminders; this does not guarantee execution or prevent a final
answer. Disable with enabled=false when the user stops that experiment.

## Case construction quick reference

- `app.start`: installed interpreter (`python`), argv (`command`), absolute `cwd`,
  loopback `base_url` using `{PORT}`, optional `env`, `health_path` and `setup`.
  `{DATA_DIR}` is a fresh disposable directory for each run; configure the app
  and the SQLite observer to use the same `{DATA_DIR}/database.sqlite` path.
  Initialize the application only when its disposable database is missing; preserve an existing database on restart. Existing source data must
  not be overwritten. `{ROOT}` means the plugin root, not the app root.
- `journey`: `goto` with `path`; `fill` with `selector` and `value`; `click` with
  `selector`; `wait_for_url` with glob `pattern`; `wait_for_fault`; `reload`;
  `wait_for_text` with `text`; `screenshot` with `as`. Additional supported steps
  are listed in the prepare schema. Use observable waits rather than sleeps.
- `fault`: endpoint URL glob `route`, HTTP `method`,
  `mode: suppress_after_upstream`, `occurrence: 1`.
- Native HTML forms: submit, wait for fault delivery, then `reload` resubmits the
  original POST. JSON/fetch apps: submit, wait for fault, then click the app's
  retry button. Capture is supported for both JSON and URL-encoded form fields.
- `state`: prefer `mode: sqlite`, `db`, `table`, a scoped `where`, `expect: 1`,
  `record_fields` for exact content, and `request_fields` mapping database columns
  to top-level submitted field names. This mapping defines an identity signature:
  one record per distinct signature is required. Include a stable per-intent key
  where available. Primary fault baselines must be empty.
- `control`: a second `journey` and `state`. It runs in the same app/browser
  session after the primary. Use a distinct marker for its record scope and
  create two deliberate identical records, `expect: 2`; exclude the primary row.
  When identical control payloads have no per-intent identifier, omit control
  `request_fields` and use counts plus `record_fields`; explicitly state that
  this control verifies cardinality/content, not per-intent identity.
  Authentication can be performed declaratively at the start of the main journey.

Preserve existing database compatibility when repairing schema. If migration is
not implemented and tested, explicitly limit the result to fresh databases. Run
available app regression tests and report unavailable dependencies accurately.

A recovery check is required, not just a final row count. Default retry needs a successful repeated write after interruption with the same request identity/payload. For server reconciliation, declare recovery mode reconcile plus route, initial_field, query_parameter, response_id_path and record_id_field; the GET must confirm the original intent and observed record ID. Never repair a retry by merely showing local success. Count waits default to at least one element; max-only waits have an implicit minimum of zero.


## Recovery campaigns and reusable regressions

When the user needs recovery across delivery uncertainty or process restart, set
`recovery_campaign` to `before_delivery`, `after_commit` and `restart_after_commit`.
These are three boundaries of one operation, each with the same journey,
assertions and deliberate-use control. Include exactly one `wait_for_fault`
step before the recovery action. CrashLab inserts its managed restart there.
The restart reuses port and data directory without replaying setup or reset;
observed counts and record identities must survive before the retry. Numeric-only HTTP count observers are unsupported for restart preservation; use records or direct SQLite. Do not recreate the database in
an unconditional startup command. This is not a fresh-database restart test.

Inspect `crashlab_experiment_report` for each measured scenario, including
captured recovery requests, actual record IDs, control and process lifecycle.
Source output is a bounded Git diff against that run's HEAD, may contain earlier
edits and excludes untracked content; do not attribute it as an exact repair patch.

Use `crashlab_experiment_export` to keep the regression after this conversation.
The bundle freezes behavior and launch template separately from project/interpreter
bindings. `crashlab_experiment_import` requires that same evaluator and concrete
local paths, creates a new PREPARED experiment and never carries VERIFIED forward.
Run a fresh baseline and the repair. Do not silently update exported assertions or
evaluator hashes. Old evaluator reports remain historical, with current status unavailable.
