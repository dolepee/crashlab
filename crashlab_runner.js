// CrashLab reusable contract runner.
// Usage: node crashlab_runner.js contract.json
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright-core');

function nowId() {
  return new Date().toISOString().replace(/[:.]/g, '-');
}

(async () => {
  const contractPath = process.argv[2];
  if (!contractPath) throw new Error('contract path required');
  const contract = JSON.parse(fs.readFileSync(contractPath, 'utf8'));
  const base = contract.app.base_url.replace(/\/$/, '');
  const evidenceDir = path.resolve(contract.evidence?.screenshot_dir || 'evidence');
  fs.mkdirSync(evidenceDir, { recursive: true });
  const runId = `${contract.id}-${nowId()}`;
  const observations = {};
  const screenshots = [];
  let interrupted = false;
  let upstreamStatus = null;

  const browser = await chromium.launch({
    executablePath: process.env.CHROME_PATH || '/usr/bin/chromium',
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
    env: {
      ...process.env,
      HOME: path.join(evidenceDir, '.home'),
      XDG_CONFIG_HOME: path.join(evidenceDir, '.config'),
    },
  });
  const page = await browser.newPage({ viewport: { width: 1000, height: 760 } });

  if (contract.fault) {
    await page.route(contract.fault.route, async (route) => {
      if (interrupted) return route.continue();
      interrupted = true;
      if (contract.fault.mode === 'suppress_after_upstream') {
        const response = await route.fetch();
        upstreamStatus = response.status();
        await route.abort('failed');
      } else {
        await route.abort('failed');
      }
    });
  }

  async function reset() {
    if (!contract.app.reset_url) return;
    const response = await fetch(base + contract.app.reset_url, { method: 'POST' });
    if (!response.ok) throw new Error(`reset endpoint HTTP ${response.status}`);
  }

  async function run(steps) {
    for (const step of steps) {
      if (step.action === 'goto') {
        await page.goto(base + (step.path || '/'), { waitUntil: 'domcontentloaded' });
      } else if (step.action === 'click') {
        await page.click(step.selector);
      } else if (step.action === 'wait_for_selector') {
        await page.waitForSelector(step.selector, { timeout: step.timeout || 10000 });
      } else if (step.action === 'wait_for_text') {
        await page.waitForFunction(
          (text) => document.body.innerText.includes(text),
          step.text,
          { timeout: step.timeout || 10000 },
        );
      } else if (step.action === 'reload') {
        await page.reload({ waitUntil: 'domcontentloaded' });
      } else if (step.action === 'text') {
        observations[step.as || 'text'] = await page.textContent(step.selector);
      } else if (step.action === 'screenshot') {
        const file = path.join(evidenceDir, `${runId}-${step.as || 'shot'}.png`);
        await page.screenshot({ path: file, fullPage: true });
        screenshots.push(file);
      } else {
        throw new Error(`unknown action: ${step.action}`);
      }
    }
  }

  async function readState(spec) {
    const response = await fetch(base + spec.url);
    if (!response.ok) throw new Error(`state endpoint HTTP ${response.status}`);
    const body = await response.json();
    const records = spec.path.split('.').reduce((v, key) => v?.[key], body);
    return {
      observed: Array.isArray(records) ? records.length : Number(records),
      expected: Number(spec.expect),
      records: Array.isArray(records) ? records : [],
    };
  }

  try {
    await reset();
    await run(contract.journey);
    await page.unroute(contract.fault?.route || '**/order');
    const primary = await readState(contract.state);
    let control = null;
    if (contract.control) {
      await reset();
      await run(contract.control.journey);
      const checked = await readState(contract.control.state);
      control = { ...checked, ok: checked.observed === checked.expected };
    }
    const result = {
      contract: contract.id,
      run_id: runId,
      base_url: base,
      ok: primary.observed === primary.expected && (!control || control.ok),
      expected: primary.expected,
      observed: primary.observed,
      observations,
      upstream_status: upstreamStatus,
      screenshots,
      records: primary.records,
      control,
    };
    console.log(JSON.stringify(result, null, 2));
    await browser.close();
    process.exit(result.ok ? 0 : 1);
  } catch (err) {
    console.error(JSON.stringify({ contract: contract.id, ok: false, error: String(err) }, null, 2));
    await browser.close().catch(() => {});
    process.exit(2);
  }
})().catch((err) => {
  console.error(JSON.stringify({ ok: false, error: String(err) }, null, 2));
  process.exit(3);
});
