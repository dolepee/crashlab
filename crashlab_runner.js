// CrashLab reusable contract runner.
// Usage: node crashlab_runner.js contract.json
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { chromium } = require('playwright-core');

function nowId() {
  return new Date().toISOString().replace(/[:.]/g, '-');
}

(async () => {
  const contractPath = process.argv[2];
  if (!contractPath) throw new Error('contract path required');
  const baseArgIndex = process.argv.indexOf('--base-url');
  const baseOverride = baseArgIndex >= 0 ? process.argv[baseArgIndex + 1] : null;
  const contract = JSON.parse(fs.readFileSync(contractPath, 'utf8'));
  if (baseOverride) contract.app.base_url = baseOverride;
  const base = contract.app.base_url.replace(/\/$/, '');
  const evidenceDir = path.resolve(contract.evidence?.screenshot_dir || 'evidence');
  fs.mkdirSync(evidenceDir, { recursive: true });
  const runId = `${contract.id}-${nowId()}`;
  const observations = {};
  const screenshots = [];
  let interrupted = false;
  let faultMatched = false;
  let faultDelivered = false;
  let upstreamStatus = null;
  let faultState = null;

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
      if (contract.fault.method && route.request().method() !== contract.fault.method) {
        return route.continue();
      }
      if (interrupted) return route.continue();
      interrupted = true;
      faultMatched = true;
      if (contract.fault.mode === 'suppress_after_upstream') {
        const response = await route.fetch();
        upstreamStatus = response.status();
        // Probe durable state immediately after the upstream call, before the
        // browser sees a failure and before the retry can run.
        try {
          faultState = await readState(contract.state);
        } catch (err) {
          faultState = { error: String(err) };
        }
        await route.abort('failed');
        faultDelivered = true;
      } else {
        await route.abort('failed');
        faultDelivered = true;
      }
    });
  }

  async function reset() {
    if (!contract.app.reset_url) return;
    const response = await fetch(base + contract.app.reset_url, { method: 'POST' });
    if (!response.ok) throw new Error(`reset endpoint HTTP ${response.status}`);
  }

  async function conditionMet(condition) {
    if (!condition || typeof condition !== 'object') return false;
    if (condition.kind === 'count' || condition.selector && condition.min !== undefined) {
      const texts = await page.locator(condition.selector).allTextContents();
      const count = condition.text === undefined
        ? texts.length
        : texts.filter((text) => text.trim() === condition.text).length;
      const min = condition.min === undefined ? 1 : Number(condition.min);
      const max = condition.max === undefined ? Infinity : Number(condition.max);
      return count >= min && count <= max;
    }
    if (condition.kind === 'value' || condition.selector && (condition.empty !== undefined || condition.equals !== undefined)) {
      let value;
      try { value = await page.locator(condition.selector).inputValue({ timeout: 250 }); }
      catch { return false; }
      if (condition.empty === true) return value === '';
      if (condition.equals !== undefined) return value === String(condition.equals);
    }
    return false;
  }

  async function waitFor(predicate, timeout, message) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      if (await predicate()) return;
      await page.waitForTimeout(100);
    }
    throw new Error(`${message} within ${timeout}ms`);
  }

  async function run(steps) {
    for (const step of steps) {
      if (step.action === 'goto') {
        await page.goto(base + (step.path || '/'), { waitUntil: 'domcontentloaded' });
      } else if (step.action === 'click') {
        await page.click(step.selector);
      } else if (step.action === 'fill') {
        await page.fill(step.selector, step.value);
      } else if (step.action === 'check') {
        await page.check(step.selector);
      } else if (step.action === 'uncheck') {
        await page.uncheck(step.selector);
      } else if (step.action === 'select_option') {
        await page.selectOption(step.selector, step.value);
      } else if (step.action === 'press') {
        await page.press(step.selector, step.key);
      } else if (step.action === 'wait_for_selector') {
        await page.waitForSelector(step.selector, { timeout: step.timeout || 10000 });
      } else if (step.action === 'wait_for_text') {
        await page.waitForFunction(
          (text) => document.body.innerText.includes(text),
          step.text,
          { timeout: step.timeout || 10000 },
        );
      } else if (step.action === 'wait_for_url') {
        await page.waitForURL(step.pattern, { timeout: step.timeout || 10000 });
      } else if (step.action === 'wait_for_fault') {
        await waitFor(() => faultDelivered, step.timeout || 10000, 'fault was not delivered');
      } else if (step.action === 'wait_for_count') {
        await waitFor(() => conditionMet(step), step.timeout || 10000, 'count condition not met');
      } else if (step.action === 'wait_for_value') {
        await waitFor(() => conditionMet(step), step.timeout || 10000, 'value condition not met');
      } else if (step.action === 'wait_for_any') {
        await waitFor(async () => {
          for (const condition of step.conditions || []) {
            if (await conditionMet(condition)) return true;
          }
          return false;
        }, step.timeout || 10000, 'no condition met');
      } else if (step.action === 'wait_for_load_state') {
        await page.waitForLoadState(step.state, { timeout: step.timeout || 10000 });
      } else if (step.action === 'wait_for_timeout') {
        await page.waitForTimeout(step.ms);
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
    if (spec.mode === 'sqlite') {
      const db = String(spec.db || '').replace('{port}', new URL(base).port);
      if (!db) throw new Error('state.db is required for sqlite mode');
      const probe = path.join(__dirname, 'crashlab_probe.py');
      const raw = execFileSync(process.env.PYTHON_BINARY || 'python3', [
        probe, path.resolve(path.dirname(contractPath), db), spec.table || 'orders', spec.where || '',
      ], { encoding: 'utf8' });
      const body = JSON.parse(raw);
      if (body.error) throw new Error(body.error);
      const records = Array.isArray(body.records) ? body.records : [];
      return {
        observed: records.length,
        expected: Number(spec.expect),
        records,
        observer: 'sqlite',
        db: path.resolve(path.dirname(contractPath), db),
      };
    }
    const response = await fetch(base + spec.url);
    if (!response.ok) throw new Error(`state endpoint HTTP ${response.status}`);
    const body = await response.json();
    const records = spec.path.split('.').reduce((v, key) => v?.[key], body);
    return {
      observed: Array.isArray(records) ? records.length : Number(records),
      expected: Number(spec.expect),
      records: Array.isArray(records) ? records : [],
      observer: 'http',
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
      scenario: contract.fault
        ? {
            matched: faultMatched,
            delivered: faultDelivered,
            upstream_status: upstreamStatus,
            committed_after_fault: faultState?.observed ?? null,
            valid: faultMatched
              && faultDelivered
              && Number.isFinite(upstreamStatus)
              && upstreamStatus >= 200 && upstreamStatus < 300
              && Number.isFinite(faultState?.observed)
              && faultState.observed >= 1,
          }
        : { matched: true, valid: true },
      screenshots,
      records: primary.records,
      control,
    };
    result.ok = result.ok && result.scenario.valid;
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
