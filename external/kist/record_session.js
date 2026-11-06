// Record a real Kist browser session for the demo.
// This is evidence capture, not a replacement for the CrashLab contract.
const { chromium } = require('playwright-core')
const { spawn } = require('child_process')
const fs = require('fs')
const net = require('net')
const os = require('os')
const path = require('path')

const PYTHON = process.env.KIST_PYTHON || 'python3'
const NAME = 'CRASHLAB_KIST_ONE_INTENT'

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port
      server.close(() => resolve(port))
    })
    server.on('error', reject)
  })
}

async function waitReady(base, proc, log) {
  for (let i = 0; i < 150; i += 1) {
    if (proc.exitCode !== null) throw new Error(fs.readFileSync(log, 'utf8'))
    try {
      const response = await fetch(base + '/')
      if (response.ok) return
    } catch {}
    await new Promise((r) => setTimeout(r, 100))
  }
  throw new Error('app startup timeout')
}

async function configure(base) {
  const response = await fetch(base + '/api/app-config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ modules: {}, wizard_completed: true, base_currency: 'USD' }),
  })
  if (!response.ok) throw new Error(`app-config HTTP ${response.status}`)
}

async function record(app, output, suppress) {
  const run = path.join(output, suppress ? 'before' : 'after')
  fs.mkdirSync(path.join(run, 'data'), { recursive: true })
  fs.mkdirSync(path.join(run, 'home'), { recursive: true })
  const port = await freePort()
  const base = `http://127.0.0.1:${port}`
  const log = path.join(run, 'server.log')
  const proc = spawn(PYTHON, [path.join(app, 'server/app.py')], {
    cwd: app,
    env: {
      PATH: process.env.PATH,
      HOME: path.join(run, 'home'),
      FINANCE_PROJECT_DIR: path.join(run, 'data'),
      PORT: String(port),
      PYTHONUNBUFFERED: '1',
      LANG: 'C.UTF-8',
      TZ: 'UTC',
    },
    stdio: ['ignore', fs.openSync(log, 'w'), fs.openSync(log, 'w')],
  })
  try {
    await waitReady(base, proc, log)
    await configure(base)
    const browser = await chromium.launch({
      executablePath: '/usr/bin/chromium',
      headless: true,
      args: ['--no-sandbox', '--disable-dev-shm-usage'],
    })
    const context = await browser.newContext({
      viewport: { width: 1280, height: 1200 },
      recordVideo: { dir: path.join(run, 'video'), size: { width: 1280, height: 1200 } },
    })
    const page = await context.newPage()
    if (suppress) {
      let done = false
      await context.route('**/*', async (route) => {
        const request = route.request()
        if (!done && request.method() === 'POST' && request.url().endsWith('/api/goals')) {
          done = true
          const upstream = await route.fetch()
          if (upstream.status() !== 201) throw new Error(`upstream ${upstream.status()}`)
          await route.abort('failed')
          return
        }
        await route.continue()
      })
    }
    await page.goto(base + '/#goals', { waitUntil: 'domcontentloaded' })
    await page.locator('#gAdd').waitFor()
    await page.waitForTimeout(1200)
    await page.locator('#gName').fill(NAME)
    await page.locator('#gTarget').fill('1000')
    await page.locator('#gCurrent').fill('100')
    await page.locator('#gMonthly').fill('50')
    await page.waitForTimeout(700)
    await page.locator('#gAdd').click()
    if (suppress) {
      await page.waitForFunction(
        (name) => document.querySelector('#gName')?.value === name, NAME)
      await page.waitForTimeout(600)
      await page.locator('#gAdd').click()
      await page.waitForFunction(
        (name) => Array.from(document.querySelectorAll('#gList h3')).filter((x) => x.textContent === name).length === 2,
        NAME,
      )
    } else {
      await page.waitForFunction(
        (name) => Array.from(document.querySelectorAll('#gList h3')).filter((x) => x.textContent === name).length === 1,
        NAME,
      )
    }
    if (process.env.DEBUG_DOM) {
      console.log('H3', await page.locator('h3').allTextContents())
      console.log('LIST', (await page.evaluate(() => document.querySelector('#gList')?.outerHTML || 'no gList')).slice(0, 3000))
    }
    await page.locator('#gList h3').last().scrollIntoViewIfNeeded()
    await page.waitForTimeout(1200)
    const video = page.video()
    await context.close()
    await browser.close()
    if (video) console.log('video', await video.path())
  } finally {
    proc.kill('SIGTERM')
    await new Promise((resolve) => proc.once('exit', resolve))
  }
}

async function main() {
  const [app, repaired, output] = process.argv.slice(2)
  if (!app || !repaired || !output) {
    throw new Error('usage: node record_session.js <upstream-app> <repaired-app> <output-dir>')
  }
  fs.mkdirSync(output, { recursive: true })
  await record(app, output, true)
  await record(repaired, output, false)
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
