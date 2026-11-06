# CrashLab — Day 1 gate result

Run: 2026-11-06

## What was tested

A tiny checkout app persists an order to SQLite before responding.

The saved case interrupts the first response after the server has committed:

1. Click Place order.
2. Playwright sends the request upstream and receives the server response.
3. Playwright aborts the browser-facing response.
4. The app displays “Could not confirm the order. Try again.”
5. Click Try again.

The application database is then queried independently through `/state`.

## Results

### Buggy version

- First message: “Could not confirm the order. Try again”
- Second message: “Order confirmed: #2”
- Persisted orders: **2**
- Both orders share the same intent key.
- Expected for the defect: 2.

Evidence: `evidence/buggy.json`.

### Repaired version

- First message: “Could not confirm the order. Try again”
- Second message: “Order confirmed: #1”
- Persisted orders: **1**
- Same saved case; expected: 1.

Evidence: `evidence/fixed-lost.json`.

### Control: separate purchase

- Two distinct purchases on a fresh database.
- Persisted orders: **2**
- Expected: 2.

Evidence: `evidence/fixed-control.json`.

## What this proves

- A real browser interaction can be interrupted after the server commits.
- The application can appear to fail while the data is already written.
- Retrying can create a second record.
- The same saved case distinguishes the buggy and repaired versions.
- The repair does not simply block later legitimate purchases.

## What this does not prove

- Hermes has not yet made the repair; the repaired version is a separate server mode.
- There is no native evidence viewer yet.
- There is no reusable test contract yet.
- There is no catalog plugin yet.
- This is a Day 1 engine proof, not a finished CrashLab entry.

## Next gate

Turn this into a reusable contract:

- journey
- fault trigger
- state probe
- expected result
- saved case ID

Then wire the contract into a Hermes tool and a native evidence view.
