'use strict';
// nwr-card.test.js — the Weather Radio card's display states.
//
// nwrCardState() is pure so these can be pinned without a DOM. They are pinned
// because the last time this logic was duplicated in two HTML files, both
// copies went on reading a `capture` key that the API had stopped returning,
// and both dashboards painted Weather Radio red until someone noticed by eye.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..', '..');
const src = fs.readFileSync(path.join(root, 'common', 'js', 'nwr-card.js'), 'utf8');
const sandbox = {};
new Function('window', src).call(sandbox, sandbox);
const { nwrCardState, nwrHealth, nwrActiveEvent, nwrRenderCard } = sandbox;

// The shape GET /api/nwr/status actually returns (services/nwr/routes.py):
// local preconditions and config, plus the daemon's own status under `watch`.
function status(over) {
  const o = over || {};
  return {
    ok: true,
    preconditions: Object.assign({
      missing_deps: [], dongle_present: true, assigned: true,
      device: 'RTL #1', busy: false, holder: null,
      can_stream: true, can_scan: true,
    }, o.preconditions),
    watch: Object.assign({
      reachable: true, detail: null, phase: 'listening', listening: true,
      channel: 'WX7', channel_hz: 162550000, alerts_seen: 0,
      last_decode: null, last_error: null, scan: null, scan_weak: false,
      retune_pending: false, retry_in_s: 0, streams: 0, elapsed_s: 120,
    }, o.watch),
    config: { channel_hz: 162550000 },
    channels: [],
  };
}

const scan = (dbm) => ({ ok: true, error: null, code: null, powers: {},
                         best_hz: 162550000, best_dbm: dbm, weak: dbm < -50 });

test('an active watch-list alert paints red and badges the event code', () => {
  const cs = nwrCardState(status(), 'TOR');
  assert.strictEqual(cs.alert, true);
  assert.strictEqual(cs.badge, 'TOR');
  // The alert is the watch WORKING. Counting it as a down service would put a
  // tornado warning in the "N DN" tally and take the station's health with it.
  assert.strictEqual(cs.state, 'up');
  assert.ok(nwrHealth(cs).up, 'an alerting watch is still an up service');
  // The health detail survives underneath, so the card still says why.
  assert.match(cs.sub, /WX7 162\.550/);
});

test('scanning shows the meter and badges SCANNING', () => {
  const cs = nwrCardState(status({ watch: { phase: 'scanning', listening: false } }));
  assert.strictEqual(cs.badge, 'SCANNING');
  assert.strictEqual(cs.state, 'up');
  assert.ok(cs.meter, 'the meter is visible while a sweep runs');
  // Empty, not animated and not invented: the daemon reports no progress
  // through a sweep, so the bar fills only when a measurement lands.
  assert.strictEqual(cs.meter.pct, 0);
  assert.match(cs.meter.label, /sweeping 162\.4-162\.55/);
  // Short on purpose: .feed-meter-label shares a row with the sub-line inside a
  // card that is 120px at its narrowest, and neither element can shrink.
  assert.ok(cs.meter.label.length <= 24,
    'the sweep label has to fit a 120px card beside "choosing a channel"');
});

test('a retune is a deliberate gap, not a decode failure', () => {
  // The seconds an accepted pin costs: the daemon stops the capture and starts
  // one on the new channel. Reading that as the watch breaking would report the
  // operator's own click back to them as damage.
  const cs = nwrCardState(status({
    watch: { phase: 'retuning', listening: false, retune_pending: true },
  }));
  assert.strictEqual(cs.state, 'up');
  assert.strictEqual(cs.badge, 'RETUNING');
  assert.ok(nwrHealth(cs).up, 'a retune must not count as a down service');
  assert.strictEqual(cs.meter, null, 'nothing is being measured mid-retune');
});

test('a retune outranks the weak scan the last sweep left behind', () => {
  // scan_weak is the PREVIOUS sweep's verdict and is not cleared when a retune
  // starts, so if it won here a pinned change would badge WEAK and blame the
  // antenna for a gap the operator asked for.
  const cs = nwrCardState(status({
    watch: { phase: 'retuning', listening: false, retune_pending: true,
             scan: scan(-58), scan_weak: true },
  }));
  assert.strictEqual(cs.badge, 'RETUNING');
});

test('a retune whose capture will not start stops calling itself a retune', () => {
  // retune_pending stays true until the new capture is RUNNING, so it is still
  // true here. phase is what tells the two apart, and the card follows phase.
  const cs = nwrCardState(status({
    watch: { phase: 'retrying', listening: false, retune_pending: true,
             retry_in_s: 60 },
  }));
  assert.strictEqual(cs.badge, 'RETRYING');
  assert.strictEqual(cs.state, 'warn');
});

test('a weak scan is amber, badged WEAK, with the measured dBm in the sub-line', () => {
  const cs = nwrCardState(status({ watch: { scan: scan(-57.3), scan_weak: true } }));
  assert.strictEqual(cs.state, 'warn');
  assert.strictEqual(cs.badge, 'WEAK');
  assert.match(cs.sub, /-57\.3 dBm/);
  assert.strictEqual(nwrHealth(cs).warn, true);
  assert.strictEqual(cs.meter.cls, 'silent');
});

test('weak outranks listening — a weak channel still starts', () => {
  // The daemon starts on a weak best rather than leaving nothing running, so
  // `listening` is true here too. If listening won, the one signal that says
  // "check the antenna" would never reach the card.
  const cs = nwrCardState(status({ watch: { listening: true, scan: scan(-58), scan_weak: true } }));
  assert.strictEqual(cs.badge, 'WEAK');
});

test('a running watch with no alerts is green, with the channel and last station', () => {
  const cs = nwrCardState(status({
    watch: { scan: scan(-32), last_decode: { station: 'KEC55', event: 'RWT', at: 1 } },
  }));
  assert.strictEqual(cs.state, 'up');
  assert.strictEqual(cs.alert, false);
  assert.strictEqual(cs.badge, 'LISTENING');
  assert.strictEqual(cs.sub, 'WX7 162.550 · KEC55');
  assert.strictEqual(cs.meter.cls, 'flowing');
  assert.ok(cs.meter.pct > 50, 'a strong measurement fills most of the bar');
});

test('an unreachable watch is down and says so instead of showing a stale channel', () => {
  const cs = nwrCardState(status({
    watch: { reachable: false, detail: 'watch unavailable (Connection refused)',
             phase: null, listening: false, channel: null, channel_hz: null },
  }));
  assert.strictEqual(cs.state, 'down');
  assert.strictEqual(nwrHealth(cs).up, false);
  assert.match(cs.sub, /Connection refused/);
  // The config channel is still in the payload. It must not be paraded as if
  // something were tuned to it.
  assert.doesNotMatch(cs.sub, /162\.550/);
});

test('an unreachable watch with no reason still names the real problem', () => {
  const cs = nwrCardState(status({ watch: { reachable: false, detail: null } }));
  assert.strictEqual(cs.sub, 'the watch is not running');
});

test('unassigned is amber with an action, never green', () => {
  const cs = nwrCardState(status({ preconditions: { assigned: false } }));
  assert.strictEqual(cs.state, 'warn');
  assert.strictEqual(cs.badge, 'NO SDR');
  assert.match(cs.sub, /assign a dongle/);
  const h = nwrHealth(cs);
  assert.strictEqual(h.up && !h.warn, false, 'unassigned must never read as plain up');
});

test('unassigned outranks an unreachable watch — the dongle is the fix', () => {
  const cs = nwrCardState(status({
    preconditions: { assigned: false }, watch: { reachable: false },
  }));
  assert.strictEqual(cs.badge, 'NO SDR');
});

test('missing dependencies are down and name what is missing', () => {
  const cs = nwrCardState(status({ preconditions: { missing_deps: ['multimon-ng'] } }));
  assert.strictEqual(cs.state, 'down');
  assert.strictEqual(cs.badge, 'DEPS');
  assert.strictEqual(cs.sub, 'multimon-ng');
});

test('a failed status payload is down, not a thrown check', () => {
  assert.strictEqual(nwrCardState({ ok: false }).state, 'down');
  assert.strictEqual(nwrCardState(undefined).state, 'down');
  assert.strictEqual(nwrCardState(null).badge, 'DOWN');
});

test('a listening watch is not overruled by a lost dongle-presence race', () => {
  // rtl_test cannot open a tuner the daemon is holding. "no RTL-SDR detected"
  // over a watch that is demonstrably decoding would be a probe outranking the
  // capability it was standing in for.
  const cs = nwrCardState(status({ preconditions: { dongle_present: false } }));
  assert.strictEqual(cs.badge, 'LISTENING');
});

test('a held dongle is amber and names the holder', () => {
  const cs = nwrCardState(status({
    preconditions: { busy: true, holder: 'dump1090-fa' },
    watch: { phase: 'retrying', listening: false },
  }));
  assert.strictEqual(cs.state, 'warn');
  assert.strictEqual(cs.badge, 'BUSY');
  assert.match(cs.sub, /dump1090-fa/);
});

test('retrying counts down instead of sitting on a word', () => {
  const cs = nwrCardState(status({ watch: { phase: 'retrying', listening: false, retry_in_s: 240 } }));
  assert.strictEqual(cs.badge, 'RETRYING');
  assert.match(cs.sub, /240s/);
});

test('nwrActiveEvent ignores expired and informational SAME messages', () => {
  const rec = (id, over) => Object.assign(
    { id, matched: true, type: 'W', event: 'TOR' }, over);
  assert.strictEqual(nwrActiveEvent({ ok: true, active: [], alerts: [rec('a')] }), null);
  // A weekly test decodes and is active, but type null means informational.
  assert.strictEqual(
    nwrActiveEvent({ ok: true, active: ['a'], alerts: [rec('a', { type: null, event: 'RWT' })] }),
    null);
  // Not in the operator's county list.
  assert.strictEqual(
    nwrActiveEvent({ ok: true, active: ['a'], alerts: [rec('a', { matched: false })] }), null);
  assert.strictEqual(nwrActiveEvent({ ok: true, active: ['a'], alerts: [rec('a')] }), 'TOR');
  assert.strictEqual(nwrActiveEvent(null), null);
  assert.strictEqual(nwrActiveEvent({ ok: false }), null);
});

test('nwrRenderCard is a no-op where there is no card (the kiosk)', () => {
  // Both pages run the identical check, and the kiosk has no service cards at
  // all. This must not throw there, or the kiosk loses its whole health tally.
  assert.strictEqual(typeof document, 'undefined');
  assert.doesNotThrow(() => nwrRenderCard(nwrCardState(status())));
});

// A document stub with only what nwrRenderCard touches, so the card's own
// painting can be pinned without a browser.
function fakeCard() {
  const els = {};
  ['card-nwr', 'nwr-meter', 'nwr-meter-fill', 'nwr-meter-label'].forEach((id) => {
    els[id] = { id, hidden: false, className: '', textContent: '', style: {},
                _classes: new Set() };
    els[id].classList = {
      toggle: (c, on) => { if (on) { els[id]._classes.add(c); }
                           else { els[id]._classes.delete(c); } },
      has: (c) => els[id]._classes.has(c),
    };
  });
  return { getElementById: (id) => els[id] || null, _els: els };
}

function cardSandbox(doc) {
  // `document` as a parameter, so the module's `typeof document === 'undefined'`
  // guard sees one -- the same source, in a page instead of on the kiosk.
  const sb = {};
  new Function('window', 'document', src).call(sb, sb, doc);
  return sb;
}

test('the unreachable state repaints the card rather than leaving the last numbers', () => {
  const doc = fakeCard();
  const sb = cardSandbox(doc);
  const good = sb.nwrCardState(status({ watch: { scan: scan(-32) } }), 'TOR');
  sb.nwrRenderCard(good);
  assert.strictEqual(doc._els['nwr-meter'].hidden, false);
  assert.strictEqual(doc._els['nwr-meter-label'].textContent, '-32.0 dBm');
  assert.strictEqual(doc._els['card-nwr'].classList.has('alert'), true);

  // What the failure path paints. A card badged DOWN beside a live-looking
  // -32.0 dBm and a red alert wash is exactly the parade of stale state
  // nwrCardState refuses everywhere else.
  sb.nwrRenderCard(sb.nwrCardState(null));
  assert.strictEqual(doc._els['nwr-meter'].hidden, true, 'the stale meter is still showing');
  assert.strictEqual(doc._els['card-nwr'].classList.has('alert'), false,
    'the stale alert wash is still on the card');
});

// The nwr entry of a page's SERVICES array, verbatim.
//
// Anchored on the entry's opening brace, not on the id line: anything inserted
// BEFORE `id:` -- a second check, a different fetch -- would otherwise sit
// outside the slice and drift between the pages unnoticed. The id marker
// carries no indentation either, so a reflowed entry reports a drift instead of
// "this page has no nwr check at all".
function nwrCheckBlock(page) {
  const html = fs.readFileSync(path.join(root, page), 'utf8');
  const marker = html.indexOf("id: 'nwr', name: 'Weather Radio',");
  assert.notStrictEqual(marker, -1, page + ' has no nwr health check');
  const start = html.lastIndexOf('\n  {', marker);
  assert.notStrictEqual(start, -1, page + ": the nwr check's entry does not open");
  const end = html.indexOf('\n  },', start);
  assert.notStrictEqual(end, -1, page + ": the nwr check's entry does not close");
  return html.slice(start, end);
}

test('the drift guard covers the whole entry, opening brace included', () => {
  // The guard's own blind spot: a property inserted ahead of the id line used to
  // fall outside the compared slice.
  const block = nwrCheckBlock('index.html');
  assert.ok(block.startsWith('\n  {'), 'the slice must begin at the entry brace');
  assert.match(block, /id: 'nwr'/);
});

test('a failed check clears the card the last good one painted', () => {
  // checkService repaints the dot, the badge and the sub-line on its failure
  // path and knows nothing about the meter or the alert wash. The entry
  // declares what to do about that, and checkService calls it.
  ['index.html', path.join('oasis-dashboard', 'dashboard.html')].forEach((page) => {
    const html = fs.readFileSync(path.join(root, page), 'utf8');
    assert.match(nwrCheckBlock(page),
      /fail: \(\) => nwrRenderCard\(nwrCardState\(null\)\)/,
      page + ': the nwr entry declares no failure paint');
    assert.match(html, /if \(svc\.fail\) \{ svc\.fail\(\); \}/,
      page + ': checkService never calls a service failure paint');
  });
});

test('both dashboards run the SAME Weather Radio check, byte for byte', () => {
  // service-registry.js exists because these two pages once drifted into
  // counting different service sets. The nwr check is the one that drifted
  // hardest: both copies read a `capture` key the API had stopped returning.
  assert.strictEqual(
    nwrCheckBlock('index.html'),
    nwrCheckBlock(path.join('oasis-dashboard', 'dashboard.html')),
    'the two pages\' nwr checks differ — put the logic in nwr-card.js, do not copy it');
});

test('both dashboards call the shared module, not a private copy', () => {
  ['index.html', path.join('oasis-dashboard', 'dashboard.html')].forEach((page) => {
    const html = fs.readFileSync(path.join(root, page), 'utf8');
    assert.ok(html.includes('/common/js/nwr-card.js'), page + ' does not load nwr-card.js');
    const block = nwrCheckBlock(page);
    assert.match(block, /nwrCardState\(d, _nwrAlertEvent\)/, page + ' does not use nwrCardState');
    assert.match(block, /return nwrHealth\(cs\);/, page + ' does not use nwrHealth');
    assert.ok(!/\.capture\b/.test(block),
      page + ' still reads the retired `capture` key from /api/nwr/status');
    // The alert rule is shared too, so the pages cannot disagree about whether
    // a warning is active.
    assert.ok(!/nwrActiveBadge/.test(html),
      page + ' keeps a private copy of the active-alert rule');
  });
});

test('index.html carries the card the OPEN button replaced the nav link with', () => {
  const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  assert.ok(html.includes('id="card-nwr"'), 'no Weather Radio card');
  assert.ok(html.includes("svcOpenClick('nwr')"), 'the card has no OPEN button');
  assert.ok(html.includes('data-href="server/nwr/"'), 'OPEN does not point at the page');
  assert.ok(!html.includes('id="nlink-nwr"'),
    'the nav link must go — the card OPEN replaces it, like every other service');
});
