# Official installation on a case-sensitive Linux filesystem

If Hermes managed-Python staging fails with `ELOOP` / `Errno 40` at a terminfo
path, check whether the target filesystem distinguishes directory names by case.
In one supplied Linux container record, `/workspace` was a case-insensitive
virtiofs mount: `n` and `N` resolved to the same directory. Case-distinct terminfo
entries collided there during official runtime staging.

Choose a **persistent, case-sensitive** filesystem for `HERMES_HOME`, then use
the official installer:

```bash
export HERMES_HOME=/absolute/path/on/persistent-case-sensitive-storage/hermes
hermes plugins install dolepee/crashlab --ref 4851dc9a65201e51cdd4b0a86ce66e801f8c13cc --enable --yes-deps
```

Complete the README's Node sidecar setup, plugin doctor and validation steps.
Install Chrome/Chromium and set `CHROME_PATH` if automatic discovery needs it.
The recorded `/tmp` home was a disposable diagnostic choice, not a recommendation
for permanent storage.

The supplied Linux record reports successful official installation at merge
`4851dc9a65201e51cdd4b0a86ce66e801f8c13cc` (0.5.6), Node setup, doctor registration
of 14 tools / 3 hooks, and plugin validation on Hermes 0.23. The source tree of
that merge equals reviewed `1d8acf8ea7a5d90904c26a0f9887f83abb3af6cc`.
An independent Mac installation and browser replay use the latter exact pin.

The Linux installer logs were supplied as PDF excerpts, rather than independently
executed on the Mac. This is a diagnosis of the observed environment and a working
official-installer route, not a claim that every Linux filesystem is supported.
An earlier 0.5.4 observation is historical; it is not relabelled as 0.5.6 evidence.
Do not substitute an extracted source archive for official installation acceptance.
