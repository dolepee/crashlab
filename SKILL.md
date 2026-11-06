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

A failed check is the finding. An inconclusive run is not a pass. The control
journey must still pass; a repair that blocks all later purchases is not a fix.

The current evidence includes a real Hermes edit to the checkout fixture, but
the plugin does not claim general autonomous repair or discovery of unknown
defects.
