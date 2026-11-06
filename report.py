"""Render a local CrashLab evidence report from a crashlab_compare result."""
from __future__ import annotations

import base64
import html
import json
import sys
from pathlib import Path


def image(path: str | None) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()


def block(label: str, run: dict) -> str:
    shots = "".join(
        f'<figure><img src="{image(s)}"><figcaption>{html.escape(Path(s).name)}</figcaption></figure>'
        for s in run.get("screenshots", [])
    )
    records = json.dumps(run.get("records", []), indent=2)
    return f"""
    <section class="run">
      <h2>{html.escape(label)}</h2>
      <div class="verdict {'pass' if run.get('ok') else 'fail'}">{'PASS' if run.get('ok') else 'FAIL'}</div>
      <p><b>Expected:</b> {run.get('expected')} record(s) &nbsp; <b>Observed:</b> {run.get('observed')} record(s)</p>
      <p><b>First message:</b> {html.escape(str(run.get('observations', {}).get('first_message', '')))}<br>
         <b>Second message:</b> {html.escape(str(run.get('observations', {}).get('second_message', '')))}</p>
      <pre>{html.escape(records)}</pre>
      {shots}
    </section>"""


def main() -> int:
    source = Path(sys.argv[1] if len(sys.argv) > 1 else "evidence/day2-compare.json")
    data = json.loads(source.read_text())
    control = data.get("fixed", {}).get("control")
    control_html = ""
    if control:
        control_html = f"""
        <section class="run">
          <h2>Control — distinct purchases</h2>
          <div class="verdict {'pass' if control.get('ok') else 'fail'}">{'PASS' if control.get('ok') else 'FAIL'}</div>
          <p><b>Expected:</b> {control.get('expected')} record(s) &nbsp; <b>Observed:</b> {control.get('observed')} record(s)</p>
        </section>"""
    doc = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>CrashLab evidence</title>
<style>
 body{{font-family:system-ui,sans-serif;max-width:1100px;margin:40px auto;padding:0 24px;background:#0d1117;color:#c9d1d9}}
 h1{{margin-bottom:4px}} .sub{{color:#8b949e;margin-top:0}}
 .run{{border:1px solid #30363d;border-radius:10px;padding:18px;margin:20px 0;background:#161b22}}
 .verdict{{display:inline-block;font-weight:700;padding:4px 10px;border-radius:999px;margin:6px 0}}
 .pass{{background:#12351f;color:#3fb950}} .fail{{background:#3d1519;color:#f85149}}
 pre{{background:#0d1117;padding:12px;border-radius:6px;overflow:auto;font-size:12px}}
 figure{{display:inline-block;width:46%;margin:10px 1%;vertical-align:top}} img{{width:100%;border:1px solid #30363d;border-radius:6px}}
 figcaption{{color:#8b949e;font-size:12px}} code{{color:#58a6ff}}
</style></head><body>
<h1>CrashLab evidence</h1>
<p class="sub">Contract: <code>{html.escape(str(data.get('contract')))}</code> · overall: <b>{'PASS' if data.get('ok') else 'FAIL'}</b></p>
{block('Buggy application', data.get('buggy', {}))}
{block('Repaired application', data.get('fixed', {}))}
{control_html}
<p class="sub">Persisted records are read from <code>/state</code>. A pass requires the observed count to equal the contract's expected count.</p>
</body></html>"""
    out = source.with_suffix(".html")
    out.write_text(doc, encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
