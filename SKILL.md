---
name: crashlab
description: Run saved browser-failure contracts, inspect persisted state, and compare a buggy app with a repaired app.
---

# CrashLab

Use CrashLab when a web app looks successful but may have written the wrong
state after a failure or retry.

1. Validate the contract with `crashlab_validate`.
2. Run it against the app with `crashlab_run`.
3. For a repair, run `crashlab_compare` with the original and repaired URLs.
4. Read the records, not the success banner.

A failed check is the finding. An inconclusive run is not a pass. Fault cases require explicit terminal `recovery.success_statuses`; selected identity headers and optional JSON reply/record ID pairs are checked when declared. The control
journey must still pass; a repair that blocks all later purchases is not a fix.

The current evidence includes a real Hermes edit to the checkout fixture, but
the plugin does not claim general autonomous repair or discovery of unknown
defects.

## External application example

`external/kist` contains a real browser contract against an unmodified Kist
checkout and the Hermes repair patch. The before run observes two goals for one
recovery intent; the repaired run observes one, while two deliberate goals
still create two. Use it as the external-adoption example, not as a bundled
fixture or a general autonomous-repair claim. See `external/kist/README.md`.

## Reliability campaigns

Use `crashlab_campaign_list` to inspect bundled campaigns and their execution
support. Validate with `crashlab_campaign_validate`; run inventory contention
with `crashlab_campaign_run` only after an owner configures a disposable local
target set with reset permission. Tool arguments are campaign_id, target_set,
and optional repetitions (1–20). See CAMPAIGNS.md for setup.

Keep evaluator code, contracts and hash pins outside any application repair
workspace. Never change expected values or regenerate pins to make an application
pass. Preserve ERROR and INVALID outcomes; neither is a reproduced bug. Browser
campaigns are validation-only in this integration. Do not claim browser or
external-application verification from the inventory fixture results.
