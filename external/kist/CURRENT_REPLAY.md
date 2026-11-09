# Replay Kist recovery with CrashLab 0.5.7

Reproduce an interrupted savings-goal create, inspect the saved records, apply a
retained model repair and rerun the same regression. The
[current installed-session record](evidence/2026-11-09-native/README.md) includes
a genuine model-constructed case and repair, followed by a separate Mac/operator
replay. Savings goals are records, not payments or money movement.

The [portable regression](recovery-regression-v057.json) records evaluator
`529c22f9c77f6b65b5432c24e9f0aa3b8731cfa4` (0.5.7). Import requires identical
evaluator hashes. Older 0.5.6 and 0.5.4 bundles remain historical; do not rewrite
their hashes to use them with this evaluator. The
[0.5.6 instructions](REPLAY_V056.md) are preserved separately.

## Prepare the disposable application

Clone [Kist Finance](https://github.com/grzella/kist-finance) into a new local
Git checkout at `7a196af9550378d24d196e666ac0a0f6dd37cb48`. Create an application
virtual environment and install its `requirements.txt`. Keep source pristine
for the baseline. To run the full application tests as well, install its
`requirements-dev.txt`, including Hypothesis.

Install the recorded evaluator using Hermes v0.23:

```bash
hermes plugins install dolepee/crashlab --ref 529c22f9c77f6b65b5432c24e9f0aa3b8731cfa4 --enable
```

For an intentional replacement of an existing same-source installation, add
Hermes' documented `--force` option. Complete Node sidecar installation when
asked, or run `npm ci` in the installed plugin directory. Have Chrome/Chromium
available; set `CHROME_PATH` if automatic discovery cannot locate it. Follow the
root README's installed validation and doctor checks. The bundle does not
package application or browser dependencies.

## Import the frozen regression

Save the raw linked [regression JSON](recovery-regression-v057.json) and
[model repair](evidence/2026-11-09-native/model-repair.patch) as separate local
files. These evidence files were added after the recorded code pin, so that
pinned installation does not contain their new filenames. Verify the downloads
are JSON/patch data, not GitHub HTML pages:

| File | SHA-256 |
| --- | --- |
| Regression JSON | `4a4a3e2008c2585c2c5c5cc1d5dae879261428a65c2609768efb2f34b11606de` |
| Model repair | `f86a44ad078529136c45ca11ea1fea7958900130bfeabb77ee9626337da1dfa3` |

Create a bindings JSON file containing only your local project root and absolute
executable application interpreter:

```json
{
  "project_path": "/absolute/path/to/disposable-kist",
  "app_python": "/absolute/path/to/kist-venv/bin/python"
}
```

From the installed plugin directory, choose a new evidence directory outside the
editable application and import:

```bash
export CRASHLAB_DATA_DIR=/absolute/path/to/new-crashlab-evidence
python crashlab_replay.py import --bundle /absolute/path/to/recovery-regression-v057.json --bindings /absolute/path/to/bindings.json
```

Import starts **PREPARED**. No prior VERIFIED status transfers. Paths bind the
launch; the declared journeys, assertions and behavior hash stay unchanged.

## Reproduce, repair and rerun

The CLI displays the captured execution plan and requires a real interactive
terminal approval. Piped/unattended runs fail closed. The same experiment tools
are available through Hermes' normal approval path.

```bash
python crashlab_replay.py run --experiment NEW_EXPERIMENT_ID
python crashlab_replay.py report --experiment NEW_EXPERIMENT_ID
```

On pristine source the measured result is **REPRODUCED**, returning exit code 1.
After-commit and restart each save two goals instead of one; each control saves
two. Before delivery saves one goal but fails this case's explicitly selected
Idempotency-Key requirement because the original app sends no such header.
Do not interpret that first boundary as duplicate state or a missing real retry.
An ERROR or missing valid fault is not a reproduced application defect.

Apply the downloaded retained patch to the same disposable checkout, then rerun
the same experiment ID:

```bash
git -C /absolute/path/to/disposable-kist apply /absolute/path/to/model-repair.patch
python crashlab_replay.py run --experiment NEW_EXPERIMENT_ID
python crashlab_replay.py report --experiment NEW_EXPERIMENT_ID
```

The measured repair is **VERIFIED**: each boundary saves one goal and each
legitimate-use control saves two, with a matching terminal reply/record identity.
The restart retains its database and records; it does not create a fresh database
or repeat setup. Inspect current source status before relying on the result.

For a new model repair, give Hermes the pristine app path and intended behavior
and let it inspect, prepare and edit the app itself. Applying this retained patch
is a replay, not a new model repair. The [native acceptance](evidence/2026-11-09-native/README.md)
records the actual model journey separately from reuse.

The [receipts](evidence/2026-11-09-native/summary.json) cover this configured
operation, not all application behavior. Legacy keyless callers and recovery
after client-state loss are outside the demonstrated repair. This is not a
production-safety, adoption or speed/cost claim. Use trusted disposable apps and
keep runtime/dependency responsibilities separate from frozen expectations.
