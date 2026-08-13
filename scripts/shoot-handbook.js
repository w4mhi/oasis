#!/usr/bin/env node
/*
 * shoot-handbook.js — reshoot handbook screenshots from a LIVE OASIS box.
 *
 * The handbook pictures go stale every time a page changes, and the framing
 * (viewport size and device scale factor) is the part that gets forgotten and
 * re-derived. It lives in SHOTS below: each entry reproduces one image at the
 * exact dimensions the committed PNG already has, so a reshoot is a swap rather
 * than a redesign.
 *
 *   node scripts/shoot-handbook.js 192.168.1.46            # every shot
 *   node scripts/shoot-handbook.js 192.168.1.46 ics-205    # one
 *   node scripts/shoot-handbook.js 192.168.1.46 --list
 *
 * Shoots the DEVICE, not localhost, so the picture is the build that ships —
 * deploy first, then shoot. Each run prints the toolbar it is about to capture;
 * if that does not match what you just deployed, you are photographing a stale
 * page and the run is worth aborting.
 *
 * Needs: Node 18+ (global fetch + WebSocket; Node 24 here) and Google Chrome.
 * No npm — CDP is spoken directly over a WebSocket. Node is a dev/CI tool in
 * this repo; the Pi never runs this.
 *
 * A throwaway Chrome profile per shot keeps localStorage empty, so forms
 * photograph blank instead of showing whatever the last operator typed.
 */
'use strict';

const { spawn, execSync } = require('node:child_process');
const { writeFileSync, rmSync, existsSync } = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const IMG = path.join(ROOT, 'static', 'oasis-handbook', 'img');

// width/height are CSS pixels; dpr multiplies them into the committed PNG size.
// Keep these matching the existing files — `sips -g pixelWidth -g pixelHeight`
// on the PNG tells you what a shot must reproduce.
const SHOTS = [
  { name: 'ics-205',    page: '/static/ics-205/ics-205.html', out: 'ics-205.png',    w: 1280, h: 880, dpr: 2 },
  { name: 'ics-213',    page: '/static/ics-213/ics-213.html', out: 'ics-213.png',    w: 1280, h: 880, dpr: 2 },
  { name: 'ics-214',    page: '/static/ics-214/ics-214.html', out: 'ics-214.png',    w: 1280, h: 880, dpr: 2 },
  { name: 'ics-309',    page: '/static/ics-309/ics-309.html', out: 'ics-309.png',    w: 1280, h: 880, dpr: 2 },
  { name: 'net-logger', page: '/tools/net-log.html',          out: 'net-logger.png', w: 1440, h: 912, dpr: 2 },
];

const CHROME_CANDIDATES = [
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/Applications/Chromium.app/Contents/MacOS/Chromium',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
];

const PORT = 9333;
const PROFILE = '/tmp/oasis-shot-profile';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Chrome keeps writing to its profile for a moment after being killed, so a
// plain rmSync races it and throws ENOTEMPTY. A leftover temp profile is not
// worth failing a run over — retry, then shrug.
function dropProfile() {
  try {
    rmSync(PROFILE, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  } catch { /* next run's fresh-profile flag handles it */ }
}

function findChrome() {
  const hit = CHROME_CANDIDATES.find((p) => existsSync(p));
  if (!hit) throw new Error('No Chrome/Chromium found. Tried:\n  ' + CHROME_CANDIDATES.join('\n  '));
  return hit;
}

// Minimal CDP client: connect, send commands, await results by id.
async function connect(wsUrl) {
  const ws = new WebSocket(wsUrl);
  await new Promise((res, rej) => {
    ws.addEventListener('open', res, { once: true });
    ws.addEventListener('error', () => rej(new Error('CDP socket failed')), { once: true });
  });
  let id = 0;
  const pending = new Map();
  ws.addEventListener('message', (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  });
  const send = (method, params) => new Promise((res, rej) => {
    const n = ++id;
    pending.set(n, (m) => (m.error ? rej(new Error(method + ': ' + m.error.message)) : res(m.result)));
    ws.send(JSON.stringify({ id: n, method, params: params || {} }));
  });
  return { send, close: () => ws.close() };
}

async function shoot(host, shot) {
  dropProfile();
  const chrome = spawn(findChrome(), [
    '--headless=new',
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${PROFILE}`,
    '--hide-scrollbars',
    '--force-color-profile=srgb',
    '--disable-gpu',
    '--no-first-run', '--no-default-browser-check',
    'about:blank',
  ], { stdio: 'ignore' });

  try {
    let target = null;
    for (let i = 0; i < 40 && !target; i++) {
      await sleep(250);
      try {
        const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
        target = list.find((t) => t.type === 'page');
      } catch { /* debugger not up yet */ }
    }
    if (!target) throw new Error('Chrome debugger never came up');

    const cdp = await connect(target.webSocketDebuggerUrl);
    await cdp.send('Page.enable');
    await cdp.send('Runtime.enable');
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width: shot.w, height: shot.h, deviceScaleFactor: shot.dpr, mobile: false,
    });
    const url = `http://${host}:8083${shot.page}`;
    await cdp.send('Page.navigate', { url });
    // Let the page settle: form init, pdf-lib reporting in, the autosave stamp.
    await sleep(3500);

    // Print what is actually on screen. A mismatch here means the box has not
    // been redeployed and the shot would bake in a stale toolbar.
    const probe = await cdp.send('Runtime.evaluate', {
      expression: `JSON.stringify([...document.querySelectorAll('.actions > *')]
        .map(e => e.tagName === 'SPAN' ? '|' : e.textContent.trim()))`,
      returnByValue: true,
    });
    console.log(`  toolbar: ${probe.result.value}`);

    const png = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    const dest = path.join(IMG, shot.out);
    writeFileSync(dest, Buffer.from(png.data, 'base64'));
    cdp.close();

    let dims = '';
    try {
      dims = execSync(`sips -g pixelWidth -g pixelHeight ${JSON.stringify(dest)}`, { stdio: ['ignore', 'pipe', 'ignore'] })
        .toString().trim().split('\n').slice(1).map((l) => l.split(':')[1].trim()).join('x');
    } catch { dims = `${shot.w * shot.dpr}x${shot.h * shot.dpr} (expected)`; }
    console.log(`  wrote ${path.relative(ROOT, dest)} — ${dims}`);
  } finally {
    chrome.kill();
  }
}

async function main() {
  const [host, which] = process.argv.slice(2);
  if (which === '--list' || (!host && !which)) {
    if (!host) console.error('usage: node scripts/shoot-handbook.js <host> [name|--list]\n');
    console.error('shots:');
    for (const s of SHOTS) console.error(`  ${s.name.padEnd(11)} ${s.page}  ${s.w * s.dpr}x${s.h * s.dpr}`);
    process.exit(host ? 0 : 1);
  }
  const wanted = which ? SHOTS.filter((s) => s.name === which) : SHOTS;
  if (!wanted.length) throw new Error(`Unknown shot "${which}". Try --list.`);

  console.log(`Shooting ${wanted.length} image(s) from ${host}\n`);
  for (const s of wanted) {
    console.log(`${s.name}:`);
    await shoot(host, s);
  }
  dropProfile();
  console.log('\nDone. Review with git diff before committing — these are binaries.');
  console.log('Note: the autosave stamp differs every run, so a reshoot is never');
  console.log('byte-identical even when nothing visible changed.');
}

main().catch((err) => { console.error('shoot-handbook: ' + err.message); process.exit(1); });
