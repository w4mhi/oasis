'use strict';
// nwr-listen-pill.test.js — the kiosk's WX listen pill.
//
// The pill is a button on a wall-mounted panel that plays the weather station,
// so two things have to be true of it and neither is checkable by eye on a Pi:
//
//   1. it never offers audio that cannot play. /api/nwr/listen/stream answers
//      409 or 503 for every state with no capture running, and a blue button
//      that silently does nothing is worse than an honest grey one.
//   2. it never goes on claiming to play after the watch has stopped. The same
//      pairing — a live-looking surface beside a DOWN badge — was already fixed
//      once for the card's meter, and must not come back through a new element.
//
// nwrListenPill() is pure, so every state below is pinned without a browser.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..', '..');
const src = fs.readFileSync(path.join(root, 'common', 'js', 'nwr-card.js'), 'utf8');
const sandbox = {};
new Function('window', src).call(sandbox, sandbox);
const { nwrCardState, nwrListenPill } = sandbox;

// The shape GET /api/nwr/status returns (services/nwr/routes.py).
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

const pill = (s, alert, playing) => nwrListenPill(nwrCardState(s, alert || null), !!playing);

// ── the states that HAVE audio ──────────────────────────────────────────────

test('a listening watch is blue and pressable', () => {
  const p = pill(status());
  assert.strictEqual(p.cls, 'wx-live');
  assert.strictEqual(p.disabled, false);
  assert.match(p.title, /tap to listen/);
});

test('playing inverts the pill without changing its colour', () => {
  assert.strictEqual(pill(status(), null, true).cls, 'wx-live wx-play');
  assert.match(pill(status(), null, true).title, /playing, tap to stop/);
});

test('a weak channel still plays — the antenna is the operator to judge by ear', () => {
  // The daemon refuses nothing: a weak channel starts a capture like any other,
  // so there IS something to hear. Greying this out would take away the only
  // way to confirm by ear what the margin figure is claiming.
  const p = pill(status({ watch: { scan_weak: true, phase: 'listening' } }));
  assert.strictEqual(p.cls, 'wx-live');
  assert.strictEqual(p.disabled, false);
});

// ── judgment call 1: amber beats blue ───────────────────────────────────────

test('an alerting watch is amber even while it is being listened to', () => {
  // The pill has one colour to spend and a warning is the more urgent fact.
  // "Audio is flowing" is not lost — the inverted fill says it.
  assert.strictEqual(pill(status(), 'TOR').cls, 'wx-alert');
  assert.strictEqual(pill(status(), 'TOR', true).cls, 'wx-alert wx-play');
  assert.strictEqual(pill(status(), 'TOR').disabled, false);
});

// ── judgment call 2: grey means "nothing to hear" ───────────────────────────

test('no dongle assigned is dim grey and disabled', () => {
  const p = pill(status({ preconditions: { assigned: false } }));
  assert.strictEqual(p.cls, 'wx-off');
  assert.strictEqual(p.disabled, true);
  assert.match(p.title, /assign a dongle in Setup/);
});

test('a dongle that is gone rather than unassigned is grey too, and says so', () => {
  const p = pill(status({ preconditions: { dongle_present: false },
                          watch: { listening: false, phase: 'idle' } }));
  assert.strictEqual(p.cls, 'wx-off');
  assert.match(p.title, /no RTL-SDR detected/);
});

test('a watch that is not running is grey — the relay would answer 409', () => {
  const p = pill(status({ watch: { reachable: false, detail: 'unit inactive' } }));
  assert.strictEqual(p.cls, 'wx-off');
  assert.strictEqual(p.disabled, true);
  assert.match(p.title, /unit inactive/);
});

test('missing dependencies are grey, and name themselves', () => {
  const p = pill(status({ preconditions: { missing_deps: ['ffmpeg'] } }));
  assert.strictEqual(p.cls, 'wx-off');
  assert.strictEqual(p.disabled, true);
  assert.match(p.title, /ffmpeg/);
});

test('a sweep in progress is grey — there is no capture to relay yet', () => {
  const p = pill(status({ watch: { phase: 'scanning', listening: false } }));
  assert.strictEqual(p.cls, 'wx-off');
  assert.strictEqual(p.disabled, true);
});

test('a retune gap is grey — the capture is stopped for the length of it', () => {
  const p = pill(status({ watch: { phase: 'retuning', listening: false } }));
  assert.strictEqual(p.disabled, true);
});

test('no encoder means no stream, whatever the watch is doing', () => {
  // preconditions.can_stream false is "decode only, no listening in". The
  // relay answers 503, so the pill must not offer the press.
  const p = pill(status({ preconditions: { can_stream: false } }));
  assert.strictEqual(p.cls, 'wx-off');
  assert.strictEqual(p.disabled, true);
});

test('a precondition key that stops being sent does not mute the station', () => {
  // This module exists because both dashboards once read a key the API had
  // stopped returning. An ABSENT can_stream is a payload-shape change, not a
  // measurement, and must not be read as "no encoder".
  const s = status();
  delete s.preconditions.can_stream;
  assert.strictEqual(pill(s).disabled, false);
});

test('status unavailable is grey and says which unknown it is', () => {
  const p = nwrListenPill(nwrCardState(null), false);
  assert.strictEqual(p.cls, 'wx-off');
  assert.strictEqual(p.disabled, true);
  assert.match(p.title, /status unavailable/);
});

test('no state at all — before the first poll — is grey, not a crash', () => {
  const p = nwrListenPill(null, false);
  assert.strictEqual(p.cls, 'wx-off');
  assert.strictEqual(p.disabled, true);
});

test('every disabled reason gets its own title', () => {
  const titles = [
    pill(status({ preconditions: { assigned: false } })),
    pill(status({ watch: { reachable: false } })),
    pill(status({ preconditions: { missing_deps: ['ffmpeg'] } })),
    pill(status({ watch: { phase: 'scanning', listening: false } })),
    nwrListenPill(nwrCardState(null), false),
  ].map(p => p.title);
  assert.strictEqual(new Set(titles).size, titles.length,
    'two disabled states are indistinguishable to the operator');
});

test('a disabled pill never claims to play, whatever the caller passes', () => {
  // The failed-check path repaints with nwrCardState(null) while _wxPlaying may
  // still be true. The class string is the whole paint, so this is the rule
  // that stops a dead watch showing a filled, playing-looking pill.
  const dead = nwrListenPill(nwrCardState(null), true);
  assert.strictEqual(dead.cls, 'wx-off');
  assert.ok(!/wx-play/.test(dead.cls));
  assert.strictEqual(dead.disabled, true);
});

test('a stale alert over a dead watch stays grey, and still names the event', () => {
  // Grey outranks amber: an alert can outlive the watch that decoded it, and a
  // pill that offered audio there would answer with silence. The event code is
  // not lost — it rides the kiosk #svcpill WX badge, and the title here.
  const p = pill(status({ watch: { reachable: false } }), 'TOR');
  assert.strictEqual(p.cls, 'wx-off');
  assert.strictEqual(p.disabled, true);
  assert.match(p.title, /TOR/);
});

// ── the kiosk side: the DOM and the <audio> ─────────────────────────────────

const page = fs.readFileSync(path.join(root, 'oasis-dashboard', 'dashboard.html'), 'utf8');

function extractFunction(source, name) {
  const start = source.indexOf('function ' + name + '(');
  assert.notStrictEqual(start, -1, name + ' not found in oasis-dashboard/dashboard.html');
  const braceStart = source.indexOf('{', start);
  let depth = 0, end = braceStart;
  for (; end < source.length; end++) {
    if (source[end] === '{') depth++;
    else if (source[end] === '}') { depth--; if (depth === 0) { end++; break; } }
  }
  return source.slice(start, end);
}

// The pill and the <audio>, lifted out of the page and run against a fake DOM.
function kiosk(playing) {
  const audio = { src: null, paused: true, calls: [],
    pause() { this.calls.push('pause'); this.paused = true; },
    removeAttribute(k) { this.calls.push('removeAttribute:' + k); this.src = null; },
    load() { this.calls.push('load'); } };
  const span = { className: 'upill wx-pill wx-live', title: '', attrs: {},
    setAttribute(k, v) { this.attrs[k] = v; } };
  const doc = { getElementById: id => (id === 'k-wx-audio' ? audio
                                      : id === 'k-wx' ? span : null) };
  const body = 'let _wxPlaying = ' + (playing ? 'true' : 'false') + '; let _wxCs = null;\n'
    + extractFunction(page, '_wxEl') + '\n'
    + extractFunction(page, 'wxHalt') + '\n'
    + extractFunction(page, 'renderWxPill') + '\n'
    + 'return { renderWxPill, wxHalt, playing: () => _wxPlaying };';
  const api = new Function('document', 'nwrListenPill', body)(doc, nwrListenPill);
  return Object.assign(api, { audio, span });
}

test('a failed check tears the stream down instead of leaving a live pill', () => {
  const k = kiosk(true);
  k.audio.src = '/api/nwr/listen/stream?t=1';
  k.audio.paused = false;
  k.renderWxPill(nwrCardState(null));            // what fail() paints
  assert.strictEqual(k.playing(), false, 'the pill still thinks it is playing');
  assert.deepStrictEqual(k.audio.calls, ['pause', 'removeAttribute:src', 'load'],
    'pausing alone leaves the daemon a subscriber and one of its three slots');
  assert.strictEqual(k.span.className, 'upill wx-pill wx-off');
  assert.strictEqual(k.span.attrs['aria-pressed'], 'false');
  assert.strictEqual(k.span.attrs['aria-disabled'], 'true');
});

test('a healthy check while playing leaves the stream alone', () => {
  const k = kiosk(true);
  k.audio.paused = false;
  k.renderWxPill(nwrCardState(status()));
  assert.strictEqual(k.playing(), true);
  assert.deepStrictEqual(k.audio.calls, [], 'a routine poll must not cut the audio off');
  assert.strictEqual(k.span.className, 'upill wx-pill wx-live wx-play');
  assert.strictEqual(k.span.attrs['aria-pressed'], 'true');
});

test('an alert repaints the pill amber under the operator', () => {
  const k = kiosk(true);
  k.renderWxPill(nwrCardState(status(), 'TOR'));
  assert.strictEqual(k.span.className, 'upill wx-pill wx-alert wx-play');
});

test('the kiosk wires the pill to the shared paint, not to its own poll', () => {
  // The two dashboards' nwr registry entries are compared byte for byte
  // (tests/js/nwr-card.test.js), so the pill CANNOT be painted from inside one.
  // Hanging off nwrRenderCard is also what puts it on the failure path.
  assert.match(page, /nwrOnCardPaint\(renderWxPill\)/,
    'the pill is not registered with the shared card paint');
  assert.ok(!/nwrCardState\(d,\s*_nwrAlertEvent\)[\s\S]{0,200}renderWxPill/.test(page),
    'the pill is painted from inside the nwr check — that path skips fail()');
});

test('the pill sits between the stats bar and OPS, and is a real button', () => {
  // Its right-hand neighbour has changed twice: DATA moved into the system bar,
  // then the units pill was deleted outright (the TEMP cell had been calling the
  // same toggleUnits() all along). OPS is what is left. The ordering is the point
  // either way: WX is a control the operator reaches for, and it belongs with the
  // other controls rather than drifting to the end of the row.
  const wx = page.indexOf('id="k-wx"');
  const ops = page.indexOf('class="upill ops-pill"');
  assert.notStrictEqual(wx, -1, 'no WX pill');
  assert.notStrictEqual(ops, -1, 'no OPS pill');
  assert.ok(wx < ops, 'the WX pill must come before OPS');
  assert.ok(!/id="k-units"/.test(page),
    'the units pill is back — it duplicated the TEMP cell, which toggles units too');
  assert.match(page, /id="k-wx"[\s\S]{0,400}role="button"/);
  assert.match(page, /id="k-wx"[\s\S]{0,400}tabindex="0"/);
  assert.match(page, /class="upill wx-pill wx-off" id="k-wx"/,
    'the pill must start grey — the first poll is what earns it a colour');
});

test('the WX pill is an icon in a finger-sized square', () => {
  // Its label was a constant "WX" and every state it has is carried by colour,
  // so the letters were never doing the work — but the BOX still has to be
  // something a finger can aim at. 2.75rem, like every other target here.
  assert.match(page, /id="k-wx"[\s\S]{0,400}<svg viewBox="0 0 24 24"/,
    'the pill must carry a drawn glyph');
  assert.match(page,
    /\.upill\.wx-pill, \.upill\.ops-pill\{ padding:\.3rem; min-width:2\.75rem; min-height:2\.75rem; \}/,
    'square, and no smaller than the panel\'s other finger targets');
  // Scoped to the pill: "WX" is still the svcbar's dot label and the alert
  // badge's prefix, and both are right to keep it.
  const at = page.indexOf('id="k-wx"');
  assert.ok(!/>WX<\/span>/.test(page.slice(at, page.indexOf('</span>', at) + 7)),
    'the WX letters are back on the pill');
  // renderWxPill() rebuilds className from scratch, so the icon rules must hang
  // off classes it writes — anything else is wiped on the first repaint.
  assert.match(page, /el\.className = 'upill wx-pill ' \+ p\.cls;/,
    'if this stops writing wx-pill, the icon sizing goes with it');
});

test('the audio element costs the daemon nothing until it is pressed', () => {
  assert.match(page, /<audio id="k-wx-audio" preload="none" hidden><\/audio>/,
    'preload="none" is what keeps an armed element from opening a connection');
});

test('the stream does not outlive the page, and pagehide is what says so', () => {
  assert.match(page, /window\.addEventListener\('pagehide', wxHalt\)/,
    'no pagehide teardown — a bfcached kiosk pins a stream slot and an ffmpeg');
  assert.ok(!/addEventListener\('unload'/.test(page),
    'unload disqualifies the page from the bfcache it is meant to survive');
  assert.ok(!/visibilitychange[\s\S]{0,120}wxHalt/.test(page),
    'a merely hidden kiosk must keep playing — teardown is for stop and for leaving');
});

test('the pill classes it paints all exist in the stylesheet', () => {
  ['wx-pill', 'wx-live', 'wx-alert', 'wx-off', 'wx-play'].forEach(c => {
    assert.ok(page.includes('.upill.' + c), 'no CSS for .upill.' + c);
  });
});
