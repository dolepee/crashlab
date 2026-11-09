// CrashLab reusable contract runner.
// Usage: node crashlab_runner.js contract.json
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const crypto = require('crypto');
const { chromium } = require('playwright-core');

function nowId() {
  return new Date().toISOString().replace(/[:.]/g, '-');
}

const MAX_SAFE_INTEGER = BigInt(Number.MAX_SAFE_INTEGER);

// JSON.parse rounds integers above 2^53-1 before any comparison can see them.
// Reject those literals while the raw text still contains the digits. Decimal
// strings remain supported; this is deliberately not a bigint adapter.
function assertSafeJsonNumbers(text, where) {
  let i = 0;
  let inString = false;
  let escaped = false;
  while (i < text.length) {
    const ch = text[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === '\\') escaped = true;
      else if (ch === '"') inString = false;
      i += 1;
      continue;
    }
    if (ch === '"') { inString = true; i += 1; continue; }
    if (ch === '-' || (ch >= '0' && ch <= '9')) {
      const start = i;
      if (ch === '-') i += 1;
      if (text[i] === '0') i += 1;
      else while (i < text.length && text[i] >= '0' && text[i] <= '9') i += 1;
      let fractional = false;
      let exponent = false;
      if (text[i] === '.') {
        fractional = true; i += 1;
        while (i < text.length && text[i] >= '0' && text[i] <= '9') i += 1;
      }
      if (text[i] === 'e' || text[i] === 'E') {
        exponent = true; i += 1;
        if (text[i] === '+' || text[i] === '-') i += 1;
        while (i < text.length && text[i] >= '0' && text[i] <= '9') i += 1;
      }
      const literal = text.slice(start, i);
      if (!fractional && !exponent) {
        const value = BigInt(literal);
        if (value > MAX_SAFE_INTEGER || value < -MAX_SAFE_INTEGER) {
          throw new Error(`${where}: unsafe integer ${literal}; use a decimal string`);
        }
      } else {
        const value = Number(literal);
        if (!Number.isFinite(value)) {
          throw new Error(`${where}: non-finite number ${literal}; use a finite number or decimal string`);
        }
        if (Number.isInteger(value) && Math.abs(value) > Number.MAX_SAFE_INTEGER) {
          throw new Error(`${where}: unsafe integer ${literal}; use a decimal string`);
        }
      }
      continue;
    }
    i += 1;
  }
}

function parseJson(text, where) {
  if (typeof text !== 'string') throw new Error(`${where}: expected JSON text`);
  assertSafeJsonNumbers(text, where);
  try { return JSON.parse(text); }
  catch (err) { throw new Error(`${where}: invalid JSON (${String(err && err.message ? err.message : err)})`); }
}

function sha256(value) {
  return crypto.createHash('sha256').update(String(value)).digest('hex');
}

function scalarIdentity(value) {
  if (typeof value === 'string' && value.length) return {key: `string:${value}`, value};
  if (typeof value === 'number' && Number.isSafeInteger(value)) return {key: `number:${String(value)}`, value};
  return null;
}

function identityKey(value) {
  return scalarIdentity(value)?.key || null;
}

function identityValue(value) {
  return scalarIdentity(value)?.value ?? null;
}

// URL query strings are always text. A safe integer initial intent was sent as
// a JSON number and is naturally rendered as its exact decimal string on the
// readback URL; do not use this helper for reply/record identity.
function queryIdentityKey(value) {
  if (typeof value === 'number' && Number.isSafeInteger(value)) return `string:${String(value)}`;
  if (typeof value === 'string' && value.length) return `string:${value}`;
  return null;
}

function own(object, key) {
  return Boolean(object) && Object.prototype.hasOwnProperty.call(object, key);
}

function pathValue(value, dotted) {
  return String(dotted).split('.').reduce((current, key) => own(current, key) ? current[key] : undefined, value);
}

function recordIdentity(row, field) {
  if (!own(row, field)) return null;
  return identityValue(row[field]);
}

function responseIdentityValue(response, path) {
  return identityValue(pathValue(response, path));
}

// Only a leading transport cause or a structured error code can prove a
// connection failure. Never scan request logs, URLs, headers or payloads.
function recoveryTransportCause(err) {
  const name = String(err && err.name ? err.name : '');
  const code = String(err && err.code ? err.code : '');
  const message = String(err && err.message ? err.message : err);
  const firstLine = message.split(/\r?\n/, 1)[0].trim();
  if (/^(?:TimeoutError|TargetClosedError|BrowserClosedError)$/i.test(name)
      || /^route\.fetch:\s*Timeout\b/i.test(firstLine)) return null;
  if (/^route\.fetch:\s*(?:Target page|Browser|browser|Page|Context).*(?:closed|closed\b)/i.test(firstLine)
      || /browser has been closed|Target page, context or browser has been closed/i.test(firstLine)) return null;
  const allowedCodes = new Set(['ECONNRESET', 'ERR_CONNECTION_RESET', 'ERR_CONNECTION_CLOSED']);
  if (allowedCodes.has(code)) return { firstLine, code };
  const prefix = 'route.fetch:';
  if (!firstLine.toLowerCase().startsWith(prefix)) return null;
  const cause = firstLine.slice(prefix.length).trim();
  if (/^(?:socket hang up|connection reset|connection closed)(?:\s*\((?:ECONNRESET|ERR_CONNECTION_RESET|ERR_CONNECTION_CLOSED)\))?$/i.test(cause)) {
    return { firstLine, code: code || null };
  }
  if (/^(?:read|write|connect)\s+(?:ECONNRESET|ERR_CONNECTION_RESET|ERR_CONNECTION_CLOSED)$/i.test(cause)) {
    return { firstLine, code: code || null };
  }
  if (/^net::(?:ERR_CONNECTION_RESET|ERR_CONNECTION_CLOSED)$/i.test(cause)) return { firstLine, code: code || null };
  return null;
}

if (require.main === module) {
(async () => {
  const contractPath = process.argv[2];
  if (!contractPath) throw new Error('contract path required');
  const baseArgIndex = process.argv.indexOf('--base-url');
  const baseOverride = baseArgIndex >= 0 ? process.argv[baseArgIndex + 1] : null;
  const contract = parseJson(fs.readFileSync(contractPath, 'utf8'), 'contract');
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(String(contract.id))) {
    throw new Error('contract.id must be a safe identifier: letters, digits, dot, dash or underscore');
  }
  if (baseOverride) contract.app.base_url = baseOverride;
  const base = contract.app.base_url.replace(/\/$/, '');
  const requestedEvidenceDir = path.resolve(contract.evidence?.screenshot_dir || 'evidence');
  fs.mkdirSync(requestedEvidenceDir, { recursive: true });
  const evidenceDir = fs.realpathSync(requestedEvidenceDir);
  const runId = `${contract.id}-${nowId()}`;
  const safeScreenshotName = value => {
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$/.test(String(value))) {
      throw new Error('screenshot.as must be a bounded screenshot slug');
    }
    return String(value);
  };
  const configuredRetryTimeout = Number(process.env.CRASHLAB_RETRY_FETCH_TIMEOUT_MS || 30000);
  const retryFetchTimeoutMs = Number.isFinite(configuredRetryTimeout) && configuredRetryTimeout > 0
    ? configuredRetryTimeout : 30000;
  const observations = {};
  const screenshots = [];
  let interrupted = false;
  let faultMatched = false;
  let faultDelivered = false;
  let faultError = null;
  let retryError = null;
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
  function canonical(value) {
    const ordered = item => Array.isArray(item) ? item.map(ordered)
      : item && typeof item === 'object' ? Object.fromEntries(Object.keys(item).sort().map(key => [key, ordered(item[key])])) : item;
    return JSON.stringify(ordered(value));
  }
  const recoverySpec = contract.recovery || { mode: 'retry' };
  let initialRequest = null;
  let faultAbortStarted = false;
  const reconciliations = [];
  const responseEvents = [];
  const pendingResponses = [];
  const retryAttempts = [];

  function identityHeaderEvidence(request) {
    const names = recoverySpec.identity_headers || [];
    if (!names.length) return null;
    const headers = request.headers();
    return Object.fromEntries(names.map(name => {
      const key = Object.keys(headers).find(candidate => candidate.toLowerCase() === name.toLowerCase());
      const value = key === undefined ? null : headers[key];
      return [name, {
        present: value !== null && value !== undefined,
        sha256: value === null || value === undefined ? null : sha256(value),
        length: value === null || value === undefined ? 0 : String(value).length,
      }];
    }));
  }

  function identityHeadersMatch(left, right) {
    if (!left && !right) return true;
    if (!left || !right || Object.keys(left).length !== Object.keys(right).length) return false;
    return Object.keys(left).every(name => {
      const a = left[name];
      const b = right[name];
      return Boolean(a && b && a.present && b.present && a.sha256 && b.sha256 && a.sha256 === b.sha256);
    });
  }

  function canonicalFormPairs(pairs) {
    // Preserve submitted order and every repeated value. Form order can be part
    // of an application's intent, so sorting would reintroduce ambiguity.
    return JSON.stringify([...pairs].map(([name, value]) => [name, value]));
  }

  function sameRequest(left, right) {
    // Playwright returns the same Request object for the routed retry and its
    // terminal response/redirect chain. URL, method and frame are not identity:
    // an unrelated POST to the same endpoint must not be able to satisfy the
    // recovery proof.
    return Boolean(left) && left === right;
  }

  function retryAttemptFor(request) {
    for (const attempt of retryAttempts) {
      let current = request;
      for (let depth = 0; current && depth < 20; depth += 1) {
        if (sameRequest(current, attempt.request)) return attempt;
        current = current.redirectedFrom();
      }
    }
    return null;
  }

  function requestEvidence(request) {
    const raw = request.postData();
    let body = null;
    let bodyError = null;
    let formPairs = null;
    let duplicateFormFields = [];
    if (raw) {
      const type = String(request.headers()['content-type'] || '');
      const looksJson = /^\s*[\[{]/.test(raw);
      if (/json/i.test(type) || looksJson) {
        // A JSON-looking body must not fall back to form parsing after a
        // syntax or unsafe-integer failure; that would hide the rejected value.
        try { body = parseJson(raw, 'request body'); }
        catch (err) { bodyError = String(err); }
      } else if (/application\/x-www-form-urlencoded/i.test(type)) {
        try {
          formPairs = [...new URLSearchParams(raw).entries()];
          const names = formPairs.map(([name]) => name);
          duplicateFormFields = [...new Set(names.filter((name, index) => names.indexOf(name) !== index))];
          body = Object.fromEntries(formPairs);
          if (duplicateFormFields.length) {
            Object.defineProperty(body, '__form_duplicate_fields', {
              value: duplicateFormFields, enumerable: false,
            });
          }
        } catch (err) { bodyError = String(err); }
      }
    }
    return { method: request.method(),
      url: request.url(), body, raw_body: raw, body_error: bodyError,
      form_pairs: formPairs, duplicate_form_fields: duplicateFormFields,
      after_interruption: faultAbortStarted,
      identity_headers: identityHeaderEvidence(request) };
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

  page.on('response', (response) => {
    if (!faultAbortStarted || !initialRequest) return;
    const request = response.request();
    const resourceType = request.resourceType();
    if (!['document', 'xhr', 'fetch'].includes(resourceType)) return;
    const attempt = retryAttemptFor(request);
    if (!attempt) return;
    const redirectedFrom = request.redirectedFrom();
    const event = recordEvent('response', {
      status: response.status(),
      url: response.url(),
      method: request.method(),
      resource_type: resourceType,
      redirected_from: redirectedFrom ? redirectedFrom.url() : null,
      retry_write_sequence: attempt.event.sequence,
      retry_chain: true,
      terminal: response.status() < 300 || response.status() >= 400,
    });
    responseEvents.push(event);
    if (response.status() >= 300 && response.status() < 400) return;
    if (response.status() === 204 || response.status() === 205) {
      // No-content statuses. Record the empty body without asking Playwright
      // for a body it does not expose; 205 is reset-content by definition.
      event.body_sha256 = sha256('');
      event.body_length = 0;
      event.body_complete = true;
      event.json = null;
      return;
    }
    event.body_complete = false;
    pendingResponses.push((async () => {
      try {
        // response.text() resolves only after the body transport completes and
        // rejects on a truncated/failed body. Bound it so a broken transport
        // cannot hold the whole experiment open indefinitely.
        const text = await Promise.race([
          response.text(),
          new Promise((_, reject) => {
            const timer = setTimeout(
              () => reject(new Error('terminal response body did not complete within 5000ms')), 5000);
            timer.unref?.();
          }),
        ]);
        event.body_complete = true;
        event.body_sha256 = sha256(text);
        event.body_length = text.length;
        const contentType = String(response.headers()['content-type'] || '');
        if (text.length === 0) {
          // Parse only a body that exists. An absent body is not invalid JSON,
          // regardless of a JSON content-type header; a configured reply-ID
          // predicate still fails because there is no ID to match.
          event.json = null;
        } else if (/json/i.test(contentType)) {
          try { event.json = parseJson(text, 'terminal response'); }
          catch (err) { event.json_error = String(err); }
        } else {
          event.text_excerpt = text.slice(0, 200);
        }
      } catch (err) {
        event.body_complete = false;
        event.body_error = String(err);
      }
    })());
  });

  if (contract.fault && recoverySpec.mode === 'reconcile') {
    await page.route(recoverySpec.route, async route => {
      const request = route.request();
      if (phase !== 'primary' || !faultAbortStarted || request.method() !== 'GET') return route.continue();
      const event = recordEvent('reconciliation', requestEvidence(request));
      network.push(event);
      const response = await route.fetch();
      event.status = response.status();
      try { event.response = parseJson(await response.text(), 'reconciliation response'); }
      catch (err) { event.response = null; event.response_error = String(err); }
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
      if (spec.request_fields) requests.push(event.body);
      if (phase !== 'primary') return route.continue();
      if (interrupted) {
        // The retry is the recovery observation. A transport failure here is
        // evidence that recovery did not complete, not a runner failure. Keep
        // the failed request event and let the measured state/control verdict
        // decide the outcome.
        retryAttempts.push({ request: route.request(), event });
        try {
          const response = await route.fetch({ maxRedirects: 0, timeout: retryFetchTimeoutMs });
          event.status = response.status();
          await route.fulfill({ response });
        } catch (err) {
          const message = String(err && err.message ? err.message : err);
          const transport = recoveryTransportCause(err);
          event.transport_error = message;
          event.transport_name = String(err && err.name ? err.name : '');
          event.transport_code = String(err && err.code ? err.code : '');
          event.transport_cause = String(err && err.message ? err.message : err).split(/\r?\n/, 1)[0].trim();
          if (!transport) {
            event.unclassified_recovery_transport = true;
            retryError = err;
            try {
              await route.abort('failed');
            } catch (abortErr) {
              event.transport_abort_error = String(abortErr && abortErr.message ? abortErr.message : abortErr);
            }
            return;
          }
          event.status = null;
          event.failed_recovery_transport = true;
          try {
            await route.abort('failed');
          } catch (abortErr) {
            event.transport_abort_error = String(abortErr && abortErr.message ? abortErr.message : abortErr);
          }
        }
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
        if (!before.records_available) throw new Error('Restart preservation requires a record-level observer, not a numeric-only HTTP count');
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
        const rows = state => state.records.map(canonical).sort();
        event.state_after = after;
        event.lifecycle = lifecycle;
        event.scoped_records_preserved = after.records_available && before.observed === after.observed
          && JSON.stringify(rows(before)) === JSON.stringify(rows(after));
        if (!event.scoped_records_preserved) throw new Error('Restart did not preserve the observed durable records');
        event.completed = true;
      } else if (step.action === 'text') {
        observations[step.as || 'text'] = await page.textContent(step.selector);
      } else if (step.action === 'screenshot') {
        const captureIndex = screenshots.length + 1;
        const file = path.resolve(evidenceDir, `${runId}-${safeScreenshotName(step.as || 'shot')}-${captureIndex}.png`);
        if (path.dirname(file) !== evidenceDir) {
          throw new Error('screenshot path escapes the evidence directory');
        }
        const buffer = await page.screenshot({ fullPage: true });
        // O_EXCL fails closed if a file or symlink already occupies the leaf.
        fs.writeFileSync(file, buffer, { flag: 'wx' });
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
      const body = parseJson(raw, 'sqlite state');
      if (body.error) throw new Error(body.error);
      const records = Array.isArray(body.records) ? body.records : [];
      return {
        observed: records.length,
        expected: Number(spec.expect),
        records,
        records_available: true,
        observer: 'sqlite',
        db: path.resolve(path.dirname(contractPath), db),
      };
    }
    const response = await fetch(base + spec.url);
    if (!response.ok) throw new Error(`state endpoint HTTP ${response.status}`);
    const body = parseJson(await response.text(), 'state response');
    const records = spec.path.split('.').reduce((v, key) => v?.[key], body);
    return {
      observed: Array.isArray(records) ? records.length : Number(records),
      expected: Number(spec.expect),
      records: Array.isArray(records) ? records : [],
      records_available: Array.isArray(records),
      observer: 'http',
    };
  }

  function matches(state, spec, allowDuplicates = false) {
    if (state.observed !== state.expected) return false;
    const fields = spec.record_fields || {};
    if (Object.keys(fields).length && state.records.length !== state.observed) return false;
    if (!state.records.every(row => Object.entries(fields).every(([key, value]) => own(row, key) && row[key] === value))) return false;
    if (spec.request_fields) {
      if (!requests.length || requests.some(body => !body || typeof body !== 'object')) return false;
      const mapping = Object.entries(spec.request_fields);
      const ambiguous = requests.some(body => mapping.some(([, key]) =>
        Array.isArray(body?.__form_duplicate_fields) && body.__form_duplicate_fields.includes(key)));
      if (ambiguous) return false;
      const expected = requests.map(body => mapping.map(([, key]) => own(body, key) ? body[key] : undefined));
      if (expected.some(values => values.some(value => value === undefined || value === null || value === ''))) return false;
      const signatures = new Set(expected.map(values => JSON.stringify(values)));
      const actual = state.records.map(row => JSON.stringify(mapping.map(([column]) => own(row, column) ? row[column] : undefined)));
      if (!allowDuplicates && (actual.length !== signatures.size || new Set(actual).size !== actual.length)) return false;
      if ([...signatures].some(signature => !actual.includes(signature))) return false;
      if (actual.some(signature => !signatures.has(signature))) return false;
    }
    return true;
  }

  function historiesInvalid(events) {
    return events.some(event => event.response_error);
  }

  function recoveryEvidence(primary) {
    if (!contract.fault) return { mode: 'none', ok: true };
    const writes = network.filter(event => event.phase === 'primary' && event.kind === 'write');
    const successStatuses = Array.isArray(recoverySpec.success_statuses)
      ? recoverySpec.success_statuses : [];
    // One validity rule for every primary-mode write. A later same-intent
    // write can carry unsupported numeric data even when the initial request
    // was safe; reconciliation must not ignore it just because it only needs
    // the initial intent for the query comparison.
    const unsafePrimaryRequest = Boolean(initialRequest?.body_error)
      || writes.some(event => Boolean(event.body_error));
    if (!successStatuses.length) {
      return { mode: recoverySpec.mode || 'retry', ok: false,
        success_statuses: successStatuses,
        reason: 'No explicit success_statuses configured; CrashLab does not infer business success.' };
    }
    if (recoverySpec.mode === 'retry') {
      const sameBody = event => {
        const eventForm = Array.isArray(event.form_pairs);
        const initialForm = Array.isArray(initialRequest?.form_pairs);
        if (eventForm || initialForm) {
          return eventForm && initialForm
            && canonicalFormPairs(event.form_pairs) === canonicalFormPairs(initialRequest.form_pairs);
        }
        if (event.body && initialRequest?.body) {
          return canonical(event.body) === canonical(initialRequest.body);
        }
        return event.raw_body === initialRequest?.raw_body;
      };
      const sameIntentWrites = writes.filter(event => event.after_interruption && event !== initialRequest
        && event.method === initialRequest?.method && event.url === initialRequest?.url && sameBody(event));
      const matchingWrites = sameIntentWrites.filter(event =>
        identityHeadersMatch(initialRequest?.identity_headers, event.identity_headers));
      const retries = matchingWrites.filter(event => event.status !== null && event.status !== undefined
        && event.failed_recovery_transport !== true);
      const failedTransports = writes.filter(event => event.after_interruption && event !== initialRequest
        && event.failed_recovery_transport === true
        && event.method === initialRequest?.method && event.url === initialRequest?.url && sameBody(event)
        && identityHeadersMatch(initialRequest?.identity_headers, event.identity_headers));
      const matchingSequences = new Set(matchingWrites.map(event => event.sequence));
      const terminal = responseEvents.filter(event => event.retry_chain && event.terminal
        && matchingSequences.has(event.retry_write_sequence)).at(-1) || null;
      const terminalComplete = Boolean(terminal && terminal.body_complete === true && !terminal.body_error);
      const terminalOK = Boolean(terminal && successStatuses.includes(terminal.status) && terminalComplete);
      const unsafeResponse = responseEvents.some(event => event.json_error);
      let responseIdentity = {
        configured: false,
        ok: true,
        reason: 'No response identity predicate configured; terminal status and declared journey/state assertions are the boundary.',
      };
      if (recoverySpec.response_id_path && recoverySpec.record_id_field) {
        const ids = primary.records.map(row => recordIdentity(row, recoverySpec.record_id_field));
        const rawReplyId = responseIdentityValue(terminal?.json, recoverySpec.response_id_path);
        const replyId = identityKey(rawReplyId);
        const ok = Boolean(terminal?.json && replyId !== null && ids.length === 1
          && identityKey(ids[0]) !== null && identityKey(ids[0]) === replyId);
        responseIdentity = {
          configured: true,
          ok,
          response_id_path: recoverySpec.response_id_path,
          record_id_field: recoverySpec.record_id_field,
          response_id: rawReplyId === undefined || rawReplyId === null ? null : rawReplyId,
          record_ids: ids,
          reason: ok
            ? 'The terminal JSON reply ID matched the single observed record ID.'
            : 'The terminal JSON reply ID did not match exactly one observed record ID.',
        };
      }
      let reason;
      if (unsafePrimaryRequest) {
        reason = 'A configured request identity contained an unsafe integer; CrashLab rejects it before it can be rounded.';
      } else if (unsafeResponse) {
        reason = 'An observed JSON response contained an unsafe integer; CrashLab rejects it before it can be rounded.';
      } else if (!matchingWrites.length) {
        const headerMismatch = Boolean(initialRequest?.identity_headers)
          && sameIntentWrites.some(event => !identityHeadersMatch(
            initialRequest?.identity_headers, event.identity_headers));
        reason = failedTransports.length
          ? 'A matching recovery write was attempted but its transport failed before a response; recovery is not observed.'
          : headerMismatch
            ? 'A retry was observed with the original request, but a required identity header was missing or changed.'
            : 'No matching recovery write occurred after interruption. A local success message is insufficient.';
      } else if (!terminal) {
        reason = 'A matching recovery write was observed but no terminal response was observed; an intermediate redirect or status is not recovery.';
      } else if (!terminalComplete) {
        reason = terminal.body_error
          ? `The terminal response transport did not complete: ${terminal.body_error}`
          : 'The terminal response body was not fully observed; received headers/status alone are not recovery.';
      } else if (!successStatuses.includes(terminal.status)) {
        reason = `Terminal response ${terminal.status} was not in the configured success statuses.`;
      } else if (!responseIdentity.ok) {
        reason = responseIdentity.reason;
      } else {
        reason = responseIdentity.configured
          ? 'Observed a repeated write with the original request identity and payload, a terminal success, and the configured reply/record identity match.'
          : 'Observed a repeated write with the original request identity and payload plus a terminal success; no response identity predicate was configured.';
      }
      return { mode: 'retry', ok: !unsafePrimaryRequest && !unsafeResponse
          && matchingWrites.length > 0 && terminalOK && responseIdentity.ok,
        identity_header_mismatch: Boolean(initialRequest?.identity_headers)
          && sameIntentWrites.some(event => !identityHeadersMatch(
            initialRequest?.identity_headers, event.identity_headers)),
        invalid_measurement: unsafePrimaryRequest || unsafeResponse,
        terminal_transport_complete: terminalComplete,
        matching_retries: retries.length,
        failed_recovery_transports: failedTransports.length,
        success_statuses: successStatuses,
        identity_headers: initialRequest?.identity_headers || null,
        identity_headers_match: Boolean(initialRequest?.identity_headers)
          ? identityHeadersMatch(initialRequest.identity_headers, matchingWrites[0]?.identity_headers)
          : null,
        response_identity: responseIdentity,
        terminal_response: terminal ? {
          status: terminal.status,
          url: terminal.url,
          redirected_from: terminal.redirected_from,
          body_complete: terminal.body_complete === true,
          body_error: terminal.body_error || null,
          body_sha256: terminal.body_sha256 || null,
          json: terminal.json === undefined ? null : terminal.json,
          json_error: terminal.json_error || null,
        } : null,
        write_requests: writes.length,
        initial_request: initialRequest,
        reason };
    }
    const initialIdentityValue = own(initialRequest?.body, recoverySpec.initial_field)
      ? initialRequest.body[recoverySpec.initial_field] : null;
    const initialIdentity = queryIdentityKey(initialIdentityValue);
    const ids = primary.records.map(row => recordIdentity(row, recoverySpec.record_id_field));
    const confirmations = reconciliations.filter(event => {
      const identityText = new URL(event.url).searchParams.get(recoverySpec.query_parameter);
      const identity = queryIdentityKey(identityText);
      const id = identityKey(responseIdentityValue(event.response, recoverySpec.response_id_path));
      return initialIdentity !== null && identity !== null && identity === initialIdentity
        && successStatuses.includes(event.status)
        && id !== null && ids.length === 1 && identityKey(ids[0]) !== null && identityKey(ids[0]) === id;
    });
    const unsafeReconciliation = historiesInvalid(reconciliations);
    const ok = confirmations.length > 0 && !unsafePrimaryRequest && !unsafeReconciliation;
    let reason;
    if (unsafePrimaryRequest) {
      reason = 'A configured request identity contained an unsafe integer; CrashLab rejects it before it can be rounded.';
    } else if (unsafeReconciliation) {
      reason = 'An observed reconciliation response contained an unsupported value; CrashLab rejects it before it can be rounded.';
    } else {
      reason = confirmations.length
        ? 'Server readback confirmed the original intent and independently observed record ID with a configured success status.'
        : 'No successful server readback linked the original intent to the observed record ID.';
    }
    return { mode: 'reconcile', ok, confirmations: confirmations.length,
      invalid_measurement: unsafePrimaryRequest || unsafeReconciliation,
      success_statuses: successStatuses,
      initial_request: initialRequest, responses: reconciliations, reason };
  }

  try {
    await reset();
    baseline = await readState(contract.state);
    recordEvent('baseline', { state: baseline });
    if (contract.fault && baseline.observed !== 0) throw new Error("INVALID: fault experiment requires an empty scoped baseline");
    await run(contract.journey);
    if (retryError) throw retryError;
    await page.waitForTimeout(50);
    await Promise.allSettled(pendingResponses);
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
    if (recovery.invalid_measurement === true) {
      scenario.valid = false;
      scenario.invalid_reason = recovery.reason;
    }
    let control = null;
    if (contract.control) {
      await reset();
      phase = 'control';
      requests = [];
      const controlBaseline = await readState(contract.control.state);
      recordEvent('baseline', { state: controlBaseline });
      if (contract.fault && controlBaseline.observed !== 0) throw new Error('INVALID: control requires an empty scoped baseline');
      await run(contract.control.journey);
      await page.waitForTimeout(50);
      await Promise.allSettled(pendingResponses);
      const checked = await readState(contract.control.state);
      recordEvent('final_state', { state: checked });
      // A control that carries unsupported numeric data has not measured a
      // legitimate-use boundary, whatever its count/content assertions say.
      // Every control write is captured by the fault-route handler, so the
      // parsing error is available after the journey has finished.
      const controlWrites = network.filter(event => event.phase === 'control' && event.kind === 'write');
      const controlUnsafeRequest = controlWrites.some(event => Boolean(event.body_error));
      control = { ...checked, ok: matches(checked, contract.control.state),
        write_requests: controlWrites.length,
        unsafe_request: controlUnsafeRequest,
        invalid_measurement: controlUnsafeRequest };
      if (controlUnsafeRequest) {
        control.reason = 'A control request contained unsupported numeric data; CrashLab rejects it before it can be rounded.';
        scenario.valid = false;
        scenario.invalid_reason = control.reason;
      }
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
}

module.exports = { parseJson, assertSafeJsonNumbers };
