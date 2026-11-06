const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');
(async () => {
  const outDir = path.join(__dirname, 'raw');
  fs.mkdirSync(outDir, { recursive: true });
  const browser = await chromium.launch({
    executablePath: process.env.CHROME_PATH || '/usr/bin/chromium',
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
    recordVideo: { dir: outDir, size: { width: 1280, height: 720 } },
  });
  const page = await context.newPage();
  await page.goto('file://' + path.join(__dirname, 'index.html'), { waitUntil: 'load' });
  await page.waitForTimeout(80000);
  await context.close();
  await browser.close();
})();
