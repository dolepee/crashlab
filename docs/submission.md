# CrashLab — submission packet

## One line

CrashLab is a repair lab for agent-built apps: it reproduces a real failure under a controlled condition, proves the repair with durable state, and catches repairs that only block legitimate use.

## Links

- Repository: https://github.com/dolepee/crashlab
- Install: `hermes plugins install dolepee/crashlab`
- Catalog PR: https://github.com/NousResearch/hermes-agent/pull/150164
- Demo (live Kist run, 26.5s): `demo/crashlab-kist-live.mp4`
- Demo (88s evidence replay): `demo/crashlab-demo.mp4`
- Dashboard evidence: `docs/dashboard-pane.png`

## What the demo shows

1. A real Kist checkout accepts a goal.
2. The server commits the goal and the response is dropped.
3. The user retries the same intent.
4. On the unmodified app, SQLite contains two goals for one intent (2 observed / 1 expected).
5. Hermes repairs the app.
6. The same contract and the same dropped response now produce one goal (1 observed / 1 expected).
7. A separate two-goal control still produces two goals, so the repair did not simply block legitimate use.

The before/after pair is checked in under `external/kist/`. The live recording is not a terminal replay.

## Portability evidence

Flaskr, the public Pallets tutorial app at commit `49943b5a94413a530761350f04e896f76e8d620e`, runs through the same `app.start` and browser-contract path. It registers a user, logs in, creates one post and observes one SQLite row. That is a valid passing workflow, not a second repair case.

## Verification at the current pin

- Plugin pin: `8a5eb29134d2e9745031b68cfd963f1e3eb28da5`
- 93 tests pass
- Hermes plugin admission validator: clean
- Catalog structural validation: `1065` files valid
- Fresh `hermes plugins install` plus `npm ci`: Flaskr `1/1` PASS
- Kist: before `2/1`, after `1/1`, control `2/2`

## Honest limits

- One Hermes one-shot repair, on Kist. This is not broad autonomous repair.
- The Kist patch and the agent's final report are checked in; the full conversational transcript is not.
- Flaskr is a portability check, not a duplicate-write repair.
- The actual dashboard tab is captured. The separate Desktop pane is not captured because no working Desktop session was available.
- The catalog CI is awaiting maintainer approval and has no jobs, so it cannot be cited as passing CI.
- Browser campaigns are validation-only.
