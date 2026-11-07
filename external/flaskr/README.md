# Flaskr transfer check

This is the second independent application check, using Pallets' Flaskr
tutorial app at commit `49943b5a94413a530761350f04e896f76e8d620e`.

It is intentionally a valid workflow, not a repair demo: register a user,
log in, create one post, and observe one row in the SQLite `post` table. It
proves that the same `app.start` and contract path transfers beyond Kist.

Setup on the Flaskr checkout:

```bash
cd examples/tutorial
. /path/to/venv/bin/activate
pip install -e .
cp /path/to/crashlab/cases/flaskr-create.json case.json
# set app.start.python to this interpreter, then:
/path/to/crashlab/crashlab_core.py  # or use the installed plugin's crashlab_run
```

The checked-in screenshot is `evidence/created.png`. No fault is configured;
a passing result is expected.
