# CrashLab repair proof

This directory preserves the application and raw results behind the Day 3
hermes repair claim, so a reviewer can inspect the code and the measurements
together.

- `app-repaired.py` — the application Hermes edited. It uses an immediate
  transaction and a unique intent-key index; `/state` is unchanged.
- `repair-agent.out` — the agent's own summary and extra checks.
- `repair-buggy.json` — the current runner against the pre-repair app.
- `repair-fixed.json` — the current runner against the repaired app.
- `SHA256SUMS` — hashes for the repaired app, the contract and both raw runs.

The raw runs were produced with the runner in this repository:

```bash
PORT=8775 BUGGY=1 CRASHLAB_DB=data/orders-8775.db python3 server.py
PORT=8776 BUGGY=0 CRASHLAB_DB=data/orders-8776.db python3 server.py
node crashlab_runner.js contracts/checkout-lost-response.json --base-url http://127.0.0.1:8775
node crashlab_runner.js contracts/checkout-lost-response.json --base-url http://127.0.0.1:8776
```

The current contract uses an independent SQLite observer. The historical
repair was made before that observer was added; these raw runs were regenerated
with the current harness so the counts are comparable, but they are not claimed
to be the untouched historical files.
