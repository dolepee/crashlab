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
  let faultError = null;
  let upstreamStatus = null;
  let faultState = null;
  let faultContentsValid = false;
  let baseline = null;
  let requests = [];
  let phase = "primary";
  const network = [];
  const events = [];
  const startedAt = Date.now();
  function recordEvent(kind, data) {
    const event = { sequence: events.length + 1, elapsed_ms: Date.now() - startedAt, phase, kind, ...data };
    events.push(event);
    return event;
  }
  const recoverySpec = contract.recovery || { mode: 'retry' };
  let initialRequest = null;
  let faultAbortStarted = false;
  const reconciliations = [];

  function requestEvidence(request) {
    let body = null;
    try { body = request.postDataJSON(); } catch {}
    return { method: request.method(),
      url: request.url(), body, raw_body: request.postData(), after_interruption: faultAbortStarted };
  }

  const browser = await chromium.launch({
    executablePath: process.env.CHROME_PATH || ['/usr/bin/chromium', '/usr/bin/google-chrome', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'].find(file => fs.existsSync(file)) || chromium.executablePath(),
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
    env: {
      ...process.env,
      HOME: path.join(evidenceDir, '.home'),
      XDG_CONFIG_HOME: path.join(evidenceDir, '.config'),
    },
  });
  const page = await browser.newPage({ viewport: { width: 1000, height: 760 } });

  if (contract.fault && recoverySpec.mode === 'reconcile') {
    await page.route(recoverySpec.route, async route => {
      const request = route.request();
      if (phase !== 'primary' || !faultAbortStarted || request.method() !== 'GET') return route.continue();
      const event = recordEvent('reconciliation', requestEvidence(request));
      network.push(event);
      const response = await route.fetch();
      event.status = response.status();
      try { event.response = await response.json(); } catch { event.response = null; }
      reconciliations.push(event);
      await route.fulfill({ response });
    });
  }

  if (contract.fault) {
    await page.route(contract.fault.route, async (route) => {
      if (contract.fault.method && route.request().method() !== contract.fault.method) {
        return route.fallback();
      }
      const spec = phase === 'primary' ? contract.state : contract.control.state;
      const event = recordEvent('write', requestEvidence(route.request()));
      network.push(event);
      if (spec.request_fields) {
        try { requests.push(route.request().postDataJSON()); }
        catch { requests.push(null); }
      }
      if (phase !== 'primary') return route.continue();
      if (interrupted) {
        const response = await route.fetch({ maxRedirects: 0 });
        event.status = response.status();
        await route.fulfill({ response });
        return;
      }
      interrupted = true;
      faultMatched = true;
      initialRequest = event;
      if (contract.fault.mode === 'suppress_after_upstream') {
        const response = await route.fetch();
        upstreamStatus = response.status();
        event.status = upstreamStatus;
        // Probe durable state immediately after the upstream call, before the
        // browser sees a failure and before the retry can run.
        try {
          faultState = await readState(contract.state);
          faultContentsValid = matches({...faultState, expected: faultState.observed}, contract.state, true);
          recordEvent('committed_state', { state: faultState });
        } catch (err) {
          faultState = { error: String(err) };
        }
        // A native POST abort transitions Chromium to its error document. The
        // requestfailed event can arrive before that document has an active
        // page target; reloading in that gap fails at the protocol layer.
        const navigationSettled = route.request().isNavigationRequest()
          && route.request().frame() === page.mainFrame()
          ? page.waitForEvent('domcontentloaded', { timeout: 10000 }).catch(error => {
              faultError = new Error('Interrupted navigation did not settle: ' + error.message);
            })
          : Promise.resolve();
        const interruption = recordEvent('interruption', { mode: contract.fault.mode, completed: false });
        faultAbortStarted = true;
        await route.abort('failed');
        await navigationSettled;
        faultDelivered = !faultError;
        interruption.completed = faultDelivered;
      } else {
        // Never forward this request. An empty observed state is the expected
        // pre-delivery boundary, not a failed post-commit measurement relabeled.
        faultState = await readState(contract.state);
        recordEvent('undelivered_state', { state: faultState });
        const interruption = recordEvent('interruption', { mode: contract.fault.mode, completed: false });
        faultAbortStarted = true;
        const settled = route.request().isNavigationRequest() && route.request().frame() === page.mainFrame()
          ? page.waitForEvent('domcontentloaded', { timeout: 10000 }).catch(error => { faultError = error; })
          : Promise.resolve();
        await route.abort('failed');
        await settled;
        faultDelivered = !faultError;
        interruption.completed = faultDelivered;
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
    if (condition.kind === 'count' || condition.action === 'wait_for_count') {
      const texts = await page.locator(condition.selector).allTextContents();
      const count = condition.text === undefined
        ? texts.length
        : texts.filter((text) => text.trim() === condition.text).length;
      const min = condition.min === undefined ? (condition.max === undefined ? 1 : 0) : Number(condition.min);
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
        await waitFor(() => { if (faultError) throw faultError; return faultDelivered; }, step.timeout || 10000, 'fault was not delivered');
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
      } else if (step.action === 'restart_app') {
        if (phase !== 'primary' || !faultDelivered) throw new Error('restart_app must follow the delivered primary fault');
        const directory = process.env.CRASHLAB_CONTROL_DIR;
        if (!directory) throw new Error('restart_app requires a CrashLab-managed app.start');
        const before = await readState(contract.state);
        const event = recordEvent('restart', { completed: false, state_before: before });
        const request = path.join(directory, 'restart.request.json');
        if (fs.existsSync(request)) throw new Error('Only one restart per scenario is supported');
        const pending = path.join(directory, 'request.tmp');
        fs.writeFileSync(pending, JSON.stringify({ action: 'restart' }));
        fs.renameSync(pending, request);
        const reply = path.join(directory, 'restart.reply.json');
        await waitFor(() => fs.existsSync(reply), (contract.app.start?.ready_timeout || 30) * 1000 + 15000, 'managed app restart did not finish');
        const lifecycle = JSON.parse(fs.readFileSync(reply, 'utf8'));
        if (!lifecycle.ok) throw new Error('Managed app restart failed: ' + lifecycle.error);
        const after = await readState(contract.state);
        const rows = state => state.records.map(row => JSON.stringify(Object.entries(row).sort())).sort();
        event.state_after = after;
        event.lifecycle = lifecycle;
        event.scoped_records_preserved = JSON.stringify(rows(before)) === JSON.stringify(rows(after));
        if (!event.scoped_records_preserved) throw new Error('Restart did not preserve the observed durable records');
        event.completed = true;
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
      ], { encoding: 'utf8', timeout: 10000 });
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

  function matches(state, spec, allowDuplicates = false) {
    if (state.observed !== state.expected) return false;
    const fields = spec.record_fields || {};
    if (Object.keys(fields).length && state.records.length !== state.observed) return false;
    if (!state.records.every(row => Object.entries(fields).every(([key, value]) => row[key] === value))) return false;
    if (spec.request_fields) {
      if (!requests.length || requests.some(body => !body || typeof body !== 'object')) return false;
      const mapping = Object.entries(spec.request_fields);
      const expected = requests.map(body => mapping.map(([, key]) => body[key]));
      if (expected.some(values => values.some(value => value === undefined || value === null || value === ''))) return false;
      const signatures = new Set(expected.map(values => JSON.stringify(values)));
      const actual = state.records.map(row => JSON.stringify(mapping.map(([column]) => row[column])));
      if (!allowDuplicates && (actual.length !== signatures.size || new Set(actual).size !== actual.length)) return false;
      if ([...signatures].some(signature => !actual.includes(signature))) return false;
      if (actual.some(signature => !signatures.has(signature))) return false;
    }
    return true;
  }

  function recoveryEvidence(primary) {
    if (!contract.fault) return { mode: 'none', ok: true };
    const writes = network.filter(event => event.phase === 'primary' && event.kind === 'write');
    if (recoverySpec.mode === 'retry') {
      const sameBody = event => {
        if (event.body && initialRequest?.body) {
          const ordered = value => Array.isArray(value) ? value.map(ordered)
            : value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(key => [key, ordered(value[key])])) : value;
          const canonical = body => JSON.stringify(ordered(body));
          return canonical(event.body) === canonical(initialRequest.body);
        }
        return event.raw_body === initialRequest?.raw_body;
      };
      const retries = writes.filter(event => event.after_interruption && event !== initialRequest
        && event.status >= 200 && event.status < 400
        && event.method === initialRequest?.method && event.url === initialRequest?.url && sameBody(event));
      return { mode: 'retry', ok: retries.length > 0, matching_retries: retries.length,
        write_requests: writes.length, initial_request: initialRequest,
        reason: retries.length ? 'Observed a repeated write with the original request identity and payload.' : 'No matching recovery write occurred after interruption. A local success message is insufficient.' };
    }
    const initialIdentity = initialRequest?.body?.[recoverySpec.initial_field];
    const ids = primary.records.map(row => row[recoverySpec.record_id_field]);
    const confirmations = reconciliations.filter(event => {
      const identity = new URL(event.url).searchParams.get(recoverySpec.query_parameter);
      const id = recoverySpec.response_id_path.split('.').reduce((v, key) => v?.[key], event.response);
      return initialIdentity !== undefined && initialIdentity !== null && initialIdentity !== ''
        && identity === String(initialIdentity) && event.status >= 200 && event.status < 300
        && id !== undefined && id !== null && ids.length === 1 && ids[0] === id;
    });
    return { mode: 'reconcile', ok: confirmations.length > 0, confirmations: confirmations.length,
      initial_request: initialRequest, responses: reconciliations,
      reason: confirmations.length ? 'Server readback confirmed the original intent and independently observed record ID.' : 'No successful server readback linked the original intent to the observed record ID.' };
  }

  try {
    await reset();
    baseline = await readState(contract.state);
    recordEvent('baseline', { state: baseline });
    if (contract.fault && baseline.observed !== 0) throw new Error("INVALID: fault experiment requires an empty scoped baseline");
    await run(contract.journey);
    // Keep interception for request identity capture; fault only fires once.
    const primary = await readState(contract.state);
    recordEvent('final_state', { state: primary });
    const recovery = recoveryEvidence(primary);
    const primaryOK = matches(primary, contract.state) && recovery.ok;
    const scenario = contract.fault
        ? {
            matched: faultMatched,
            delivered: faultDelivered,
            upstream_status: upstreamStatus,
            committed_after_fault: faultState?.observed ?? null,
            mode: contract.fault.mode,
            valid: faultMatched
              && faultDelivered
              && (contract.fault.mode === 'prevent_before_delivery'
                ? upstreamStatus === null && faultState?.observed === 0
                : Number.isFinite(upstreamStatus) && upstreamStatus >= 200 && upstreamStatus < 300
                  && Number.isFinite(faultState?.observed) && faultState.observed >= 1 && faultContentsValid),
          }
        : { matched: true, valid: true };
    let control = null;
    if (contract.control) {
      await reset();
      phase = 'control';
      requests = [];
      const controlBaseline = await readState(contract.control.state);
      recordEvent('baseline', { state: controlBaseline });
      if (contract.fault && controlBaseline.observed !== 0) throw new Error('INVALID: control requires an empty scoped baseline');
      await run(contract.control.journey);
      const checked = await readState(contract.control.state);
      recordEvent('final_state', { state: checked });
      control = { ...checked, ok: matches(checked, contract.control.state) };
    }
    const result = {
      contract: contract.id,
      run_id: runId,
      base_url: base,
      ok: primaryOK && (!control || control.ok),
      primary_ok: primaryOK,
      expected: primary.expected,
      observed: primary.observed,
      observations,
      status: primaryOK && (!control || control.ok) ? "PASS" : "FAIL",
      upstream_status: upstreamStatus,
      scenario,
      recovery,
      network,
      events,
      baseline,
      state_after_interruption: faultState,
      screenshots,
      records: primary.records,
      control,
    };
    result.ok = result.ok && result.scenario.valid;
    if (!result.scenario.valid) result.status = "INVALID";
    console.log(JSON.stringify(result, null, 2));
    await browser.close();
    process.exit(result.ok ? 0 : 1);
  } catch (err) {
    console.log(JSON.stringify({ contract: contract.id, run_id: runId, ok: false,
      status: String(err).includes("INVALID:") ? "INVALID" : "ERROR", error: String(err),
      events, network, baseline, state_after_interruption: faultState }, null, 2));
    await browser.close().catch(() => {});
    process.exit(2);
  }
})().catch((err) => {
  console.log(JSON.stringify({ ok: false, status: 'ERROR', error: 'Browser runner startup failed: ' + String(err) + '. Check Node dependencies, local Chrome/Chromium and CHROME_PATH.' }, null, 2));
  process.exit(3);
});
