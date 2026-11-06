// CrashLab Day 1: suppress the response after the server commits, retry, then
// inspect the persisted records independently of the browser.
const { chromium } = require('playwright-core');

const BASE = process.env.BASE || 'http://127.0.0.1:8765';
const EXPECTED = Number(process.env.EXPECTED || '2');

(async () => {
  const browser = await chromium.launch({
    executablePath: '/usr/bin/chromium',
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
  let interrupted = false;

  await page.route('**/order', async (route) => {
    if (interrupted) return route.continue();
    interrupted = true;
    await route.fetch();          // upstream commits the order
    await route.abort('failed');  // browser never sees success
  });

  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
  await page.click('#buy');
  await page.waitForSelector('#retry', { timeout: 10000 });
  const firstMessage = await page.textContent('#status');
  await page.click('#retry');
  await page.waitForFunction(() => document.querySelector('#status')?.textContent.includes('Order confirmed'), null, { timeout: 10000 });
  const secondMessage = await page.textContent('#status');
  await page.unroute('**/order');

  const state = await (await fetch(BASE + '/state')).json();
  const result = {
    base: BASE,
    firstMessage,
    secondMessage,
    persistedOrders: state.orders.length,
    expected: EXPECTED,
    ok: state.orders.length === EXPECTED,
    orders: state.orders,
  };
  console.log(JSON.stringify(result, null, 2));
  await browser.close();
  process.exit(result.ok ? 0 : 1);
})().catch((err) => {
  console.error(err);
  process.exit(2);
});
