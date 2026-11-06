# CrashLab — Day 4 packaging and Desktop

Run: 2026-11-06

## Second fixture

A booking application was added with the same lost-response defect:

- Buggy: 2 bookings; expected 1; fail.
- Fixed: 1 booking; expected 1; pass.
- Control: 2 distinct bookings; 2 bookings; pass.

The `crashlab_run` tool was called through the real Hermes registry against
the fixed booking app and returned `ok: true`, observed 1, control 2.

## Native Desktop pane

The repository now contains:

- `desktop/plugin.js` — a pane that calls `ctx.rest('/evidence')` and renders
  the saved checkout result.
- `dashboard/manifest.json` and `dashboard/plugin_api.py` — the scoped backend
  route `/api/plugins/crashlab/evidence`.

The pane was launched inside the real Hermes Desktop app under a virtual
display. It rendered:

- contract: `checkout-lost-response`
- overall: PASS
- buggy: FAIL, 2 observed / 1 expected
- repaired: PASS, 1 observed / 1 expected
- control: PASS, 2 observed / 2 expected

Screenshot: `docs/desktop-pane.png`.

## Demo

An 88-second terminal replay was generated from the real runs and the Desktop
screenshot. It is explicitly labelled as a replay, not a live capture.
File: `demo/crashlab-demo.mp4` (kept out of git).

## Packaging

The final repository is:

https://github.com/dolepee/crashlab

The clean checkout passes `hermes plugins validate`:

- manifest, loadable entry, capability probe
- declared tools match
- security scan: safe
- no core override
- desktop surface: inside the SDK

The repository is owned by `dolepee`; the catalog PR must come from that account.
