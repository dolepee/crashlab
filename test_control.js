// Control: a separate new purchase must still succeed after the repair.
const { chromium } = require('playwright-core');

const BASE = process.env.BASE || 'http://127.0.0.1:8766';
const EXPECTED = Number(process.env.EXPECTED || '2');

(async () => {
  const browser = await chromium.launch({
    executablePath: '/usr/bin/chromium',
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
  await page.click('#buy');
  await page.waitForFunction(() => document.querySelector('#status')?.textContent.includes('Order confirmed'), null, { timeout: 10000 });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.click('#buy');
  await page.waitForFunction(() => document.querySelector('#status')?.textContent.includes('Order confirmed'), null, { timeout: 10000 });
  const state = await (await fetch(BASE + '/state')).json();
  const result = {
    base: BASE,
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
