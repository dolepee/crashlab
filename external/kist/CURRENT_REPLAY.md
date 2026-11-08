# Replay Kist recovery with CrashLab 0.5.6

Use this example to reproduce an interrupted savings-goal create, inspect the
saved records, apply a retained repair and rerun the same regression. It uses a
public application and disposable local data. Savings goals are records, not
payments or money movement.

The exported [regression](recovery-regression-v056.json) belongs to reviewed
CrashLab `1d8acf8ea7a5d90904c26a0f9887f83abb3af6cc` (0.5.6). The
[measured results](evidence/2026-11-08-v056-replay/summary.json) include a fresh
official Mac installation and a separate-process replay on Hermes 0.23 with
native Chrome. The operator applied an earlier model-produced repair; this
replay is not a new model repair or an unrelated developer's trial. These Mac records do not independently establish Linux execution. See the
[Linux installation note](../../docs/LINUX_INSTALL.md) for the separately supplied
official-installer observation.

## Prepare the disposable application

Clone [Kist Finance](https://github.com/grzella/kist-finance) into a new local
directory and check out `7a196af9550378d24d196e666ac0a0f6dd37cb48`. Create an
application virtual environment and install that checkout's `requirements.txt`.
Leave the application source unmodified for the initial run.

Install the exact CrashLab evaluator with Hermes 0.23 or newer:

```bash
hermes plugins install dolepee/crashlab --ref 1d8acf8ea7a5d90904c26a0f9887f83abb3af6cc --enable
```

If replacing an existing installation from the same source, Hermes requires its
documented `--force` reinstall option. Review the replacement before doing so.
Complete the documented Node sidecar setup (`npm ci` in the installed plugin
directory) and have Chrome/Chromium available. Set `CHROME_PATH` to its executable
if automatic discovery cannot find it. Dependencies are installed locally;
the regression bundle does not package them. Follow the root README's doctor
and validation steps before running the application.

## Import the current regression

Download the raw linked `recovery-regression-v056.json` into a separate local
file. The original pinned 0.5.6 installation predates this documentation package
and does not contain that new filename. The bundle's SHA-256 is
`77cadd748cf35a01d2b028b5deb06898a01138a7ea040ab7b034a1e26a2ac84e`;
verify you saved JSON, rather than an HTML page.

In a bindings JSON file, supply the disposable Git root and the absolute
executable for its application environment:

```json
{
  "project_path": "/absolute/path/to/disposable-kist",
  "app_python": "/absolute/path/to/kist-venv/bin/python"
}
```

From the installed plugin directory, choose a new evidence directory and import:

```bash
export CRASHLAB_DATA_DIR=/absolute/path/to/new-crashlab-evidence
python crashlab_replay.py import --bundle /absolute/path/to/recovery-regression-v056.json --bindings /absolute/path/to/bindings.json
```

Save the returned experiment ID. Import creates a **PREPARED** experiment; it
does not transfer a prior VERIFIED verdict. The behavior and launch-template
hashes remain unchanged when local paths are bound.

If the evaluator differs, import refuses it. Install the recorded pin or
explicitly prepare a new experiment; do not rewrite the recorded hashes. The
older `recovery-regression.json` and `recovery-regression-v054.json` are retained
for their historical evaluators and should not replace this 0.5.6 bundle. The
[0.5.4 replay instructions](REPLAY_V054.md) remain available.

## Reproduce, repair and rerun

The CLI requires a human approval in an interactive terminal for the displayed
execution plan. An unattended or piped invocation fails closed. The same
import/run/report tools are available through Hermes with its native approval
path.

```bash
python crashlab_replay.py run --experiment NEW_EXPERIMENT_ID
python crashlab_replay.py report --experiment NEW_EXPERIMENT_ID
```

A valid pristine baseline is **REPRODUCED**: before-delivery recovery saves one
goal, acknowledgement loss and restart recovery each save two instead of one,
and each legitimate-use control saves two. A measured failing baseline returns
exit code 1. An ERROR, missing fault or failed startup is not a reproduced bug.

After inspecting that baseline, apply the retained patch to the same disposable
application. Replace the paths below with your checkout and installed plugin:

```bash
git -C /absolute/path/to/disposable-kist apply /absolute/path/to/crashlab/external/kist/evidence/2026-11-07-hermes-recovery/repair-from-upstream.patch
python crashlab_replay.py run --experiment NEW_EXPERIMENT_ID
python crashlab_replay.py report --experiment NEW_EXPERIMENT_ID
```

The measured repaired result is **VERIFIED**: each of the three boundaries
saves one goal, and each two-intent control saves two. Keep the same experiment
ID and assertions. Inspect the recorded requests, saved record identities,
restart lifecycle and current source status in the report. Each boundary starts
with separate disposable data; the restart within its boundary preserves that
boundary's database.

For a fresh repair instead of this operator replay, give Hermes the pristine
application and intended behavior, let it inspect and edit the application, and
keep the frozen experiment unchanged. That becomes new model-repair evidence
only if those actions actually occur and are retained.

## What these records establish

The receipts record real installed Hermes dispatch with execution middleware
enabled, native Chrome and direct SQLite observations. Automated operator tests
returned the native approval decision `once`; they are not recordings of a
person answering every prompt. Private audit-root paths in the recorded JSON
are replaced with `$AUDIT_WORKSPACE`. Embedded case/source digests refer to the
original executed bytes, not the normalized receipt text. Use the unchanged
export bundle for import, rather than treating a receipt as executable input.

This is a bounded recovery check. It does not establish production safety,
arbitrary defect discovery, application adoption or a speed/cost advantage.
Legacy tokenless callers remain outside retry protection. Dependencies and
ignored configuration are outside source freshness coverage. The application
and configured local commands are trusted, disposable operator inputs.
