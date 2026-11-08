# Shiori: a saved bookmark whose retry fails

Shiori is a public Go/SQLite bookmark manager, rather than a tutorial app. This
example pins upstream `da56f82faac5bf350fc4175e5ff50726cb10d956` (v1.8.0). Its
existing URL uniqueness rule allows one bookmark per URL. The legitimate-use
control therefore creates **two distinct URLs**; it does not force duplicate
URLs into a domain that disallows them.

The initial bookmark commits, CrashLab suppresses its acknowledgement, and the
browser submits the same URL and payload again. Unmodified Shiori panics on the
unique constraint and closes the retry connection. One row remains, but the
user has not recovered successfully. CrashLab 0.5.6 reports a valid reproduced
**FAIL**, including the failed recovery transport, rather than accepting row
count alone or misclassifying the measured app failure as a runner startup error.

## Measured guided repair

The [recorded experiment, exact patch and selected real tool events](evidence/2026-11-08-guided-repair/summary.json)
come from a fresh Hermes 0.23 session using `openai/gpt-6-astra` through
OpenRouter. The model received a supplied diagnosis and intended behavior, with
no supplied repair patch, and edited a separate pristine checkout. The recorded
session uses automatic approvals (`--yolo`) and has **zero CrashLab tool calls**.
The operator prepared and ran CrashLab before and after it. This is guided
model-produced application repair, not an end-to-end interactive plugin session
or autonomous bug discovery.

| Same frozen case | Unmodified app | Model-produced repair |
| --- | --- | --- |
| Original bookmark saved | 1 | 1 |
| Matching retry | Connection reset / FAIL | Successful response / PASS |
| Two distinct permitted URLs | PASS 2/2 | PASS 2/2 |
| Workflow | REPRODUCED | VERIFIED |

The model changed only `internal/webserver/handler-api.go`: when saving fails,
it returns the existing bookmark for that URL if lookup succeeds, and preserves
the original failure otherwise. A separate HTTP/SQLite supplement checked the
same returned ID, URL and title on retry, exactly one primary row, and distinct
IDs for the two controls. The supplied build and uncached application test logs
are retained. These measurements cover the configured behavior, not every save
error or interaction with concurrent edits.

The case SHA-256 is
`0c0dde8002ec94adde19af56301f4ab9f96e81c181cf9bed9fad9c635ab1ca09`;
both recorded runs retain contract digest
`b93c472e1c880c3f88c8998f35aab304bdf903ad577190c66140ccfafb052fec`.
The fixed binary path, launch command and assertions are unchanged. Per-run
ports/data roots are disposable bindings; the application binary changes after
the model edits and rebuilds its source. Authentication values in shared run
records are redacted. The local default credentials and explicit local test
secret in the case are disposable test inputs.

## Replay boundary

Use trusted disposable data, the pinned upstream checkout and the recorded
[case](evidence/2026-11-08-guided-repair/experiment-case.json). Build unmodified
upstream first. Bind `app.start.cwd` and the first `app.start.command` entry to
your checkout and built executable. Keep every other assertion and journey step
unchanged. Install the reviewed 0.5.6 evaluator and Node/browser dependencies,
then ask Hermes to prepare and run this explicit case through its normal
approval path. After a valid reproduced baseline, apply the retained exact patch,
rebuild the **same binary path**, and rerun the same experiment.

This is an ordinary owned-launch Go experiment. Generic portable regression
export/import currently supports the documented Python binding shape; this
example does not add a Go portable binder. Use the [Kist replay](../kist/CURRENT_REPLAY.md)
for the measured separate-checkout portable workflow. External binary bytes and
ignored dependencies are outside source freshness; record their checksums.

An independent Mac Chrome run on the official checksum-verified Darwin release
also reproduced the same valid recovery failure with a passing 2/2 control.
An independently built exact model patch also passes the same behavior in Mac
Chrome, the uncached application tests and a direct HTTP/SQLite identity check.
The [independent Mac receipts](evidence/2026-11-08-guided-repair/independent-mac/)
are separate from the supplied Linux records. This is not an upstream
Shiori release, unrelated-user trial, payment operation or production safety claim.
