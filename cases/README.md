# Cases

A case is the frozen contract CrashLab runs. `cases/` is scanned by the
Desktop pane and by `crashlab_list`/the pane's `Run case` button.

The checkout cases are a transfer check for the local-app path:

- `checkout-local-start.json` runs the deliberately buggy checkout fixture.
- `checkout-local-fixed.json` runs the same fixture with idempotency enabled.

Both start their own app process through `app.start`; neither needs a
specialised launcher. The app process gets a fresh temporary data directory,
and the SQLite observer reads that directory directly.

`flaskr-create.json` is a second, independent application check against
Pallets' Flaskr tutorial app at Flask commit
`49943b5a94413a530761350f04e896f76e8d620e`. Copy it into the tutorial root
(`examples/tutorial`), set `app.start.python` to the interpreter that has
Flask installed, and run it. It registers a user, creates one post and checks
the `post` table. It is a valid passing workflow, not a repair case.

To use a case in another local app, copy it into the app directory (or set
`CRASHLAB_CASE_DIR`) and set `app.start.command`, `app.start.cwd`,
`app.start.python` and `app.start.env` for that app. `{APP_DIR}` is the case
directory, `{ROOT}` is the CrashLab repository root, `{DATA_DIR}` is the
per-run temporary data directory and `{PORT}` is the assigned port.
