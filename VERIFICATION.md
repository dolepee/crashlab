# Recorded integration verification

Final checks completed against this isolated integration:

- **80 tests passed**: 22 campaign-tool integration/scope tests, 34 campaign
  engine/fixture tests, and 24 unchanged original core/plugin tests.
- Python compilation passed for the new/changed Python modules and tests.
- **Real Hermes discovery and dispatcher passed**, using an isolated Hermes
  home and the existing local Hermes runtime. The registered run tool returned
  three buggy FAILs, three fixed PASSes and three reject-all FAILs. Every
  individual repetition was checked, not only aggregate status.
- Registered campaign list/validate and original list/validate worked. Missing
  targets and invalid arguments returned ERROR. The mixed browser campaign
  returned ERROR / UNSUPPORTED_ENGINE without launching a browser.
- **Hermes plugin admission validation passed** all reported checks, with zero
  warnings and host-ready isolation classification.
- **No browser or model-inference execution**, live installation update,
  repository push or PR modification was performed.

The archive's sibling `verification/` directory contains the exact test log,
Hermes host log, host dispatch JSON, replayable host-test script, per-run evidence
and primary/control SQLite snapshots. Historical absolute paths and dynamic
ports in saved evidence are not live endpoints; rerun the test harness for a
fresh environment.

`BASELINE.json` documents original GitHub blob hashes at commit
`683e82e86691390e7fde899c4ccd75850e72dbb2`. Only four original files were changed:
plugin.py (additive registration), plugin.yaml (version/tool declarations),
README.md and SKILL.md (guidance). Original core/runner/probe, fixture apps,
contracts, core/plugin tests and Desktop files remain byte-for-byte unchanged.

See CAMPAIGNS.md for the deliberate execution limits and trust assumptions.
