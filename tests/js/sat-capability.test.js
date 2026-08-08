'use strict';
// Unit tests for the shared satellite capability model.
//
// The blend is the part worth pinning: it is the map's only way to say
// "multi-capability", and the kiosk's edge rail now depends on the same
// numbers. If these drift, the two screens quietly disagree about what a bird
// does — with nothing on screen to say which one is right.
const test = require('node:test');
const assert = require('node:assert');
const C = require('../../common/js/sat-capability.js');

const keys = labels => C.oasisSatChannels(labels).map(c => c.key);

test('each label family maps to its own channel', () => {
  assert.deepStrictEqual(keys(['FM']), ['voice']);
  assert.deepStrictEqual(keys(['LINEAR']), ['voice']);
  assert.deepStrictEqual(keys(['SSB']), ['voice']);
  assert.deepStrictEqual(keys(['WEATHER']), ['imaging']);
  assert.deepStrictEqual(keys(['SSTV']), ['imaging']);
  assert.deepStrictEqual(keys(['APRS']), ['data']);
});

test('channels come back in a stable order regardless of label order', () => {
  assert.deepStrictEqual(keys(['APRS', 'SSTV', 'FM']), ['voice', 'imaging', 'data']);
  assert.deepStrictEqual(keys(['FM', 'APRS', 'SSTV']), ['voice', 'imaging', 'data']);
});

test('a bird with no real capability is telemetry-only, not empty', () => {
  // Callers must never have to special-case an empty list — a bare beacon still
  // gets a glyph and a colour.
  assert.deepStrictEqual(keys([]), ['telemetry']);
  assert.deepStrictEqual(keys(['CREWED']), ['telemetry']);
});

test('DATA-family telemetry does not masquerade as APRS', () => {
  // Only the APRS label earns the packet channel; a generic DATA beacon does
  // not, or every telemetry bird would look workable.
  assert.deepStrictEqual(keys(['DATA']), ['telemetry']);
});

test('the blend is additive and clamps at 255', () => {
  assert.strictEqual(C.oasisSatCapabilityColor(['FM']), 'rgb(0 200 90)');
  assert.strictEqual(C.oasisSatCapabilityColor(['WEATHER']), 'rgb(0 90 230)');
  assert.strictEqual(C.oasisSatCapabilityColor(['APRS']), 'rgb(230 100 0)');
  // Voice + APRS = yellow; green 200 + orange 100 clamps to 255.
  assert.strictEqual(C.oasisSatCapabilityColor(['FM', 'APRS']), 'rgb(230 255 90)');
  // All three — the ISS case — lands near white.
  assert.strictEqual(C.oasisSatCapabilityColor(['FM', 'SSTV', 'APRS']), 'rgb(230 255 255)');
});

test('telemetry-only takes the Data colour but never blends', () => {
  // Bare telemetry is on most of the roster; blending it would warm every
  // colour on the map into meaninglessness.
  assert.strictEqual(C.oasisSatCapabilityColor([]), 'rgb(230 100 0)');
  assert.strictEqual(C.oasisSatCapabilityColor(['CREWED']), 'rgb(230 100 0)');
});

test('labels are matched case-insensitively', () => {
  assert.deepStrictEqual(keys(['fm', 'aprs']), ['voice', 'data']);
});

test('missing/garbage input degrades to telemetry rather than throwing', () => {
  assert.deepStrictEqual(keys(undefined), ['telemetry']);
  assert.deepStrictEqual(keys(null), ['telemetry']);
  assert.strictEqual(C.oasisSatCapabilityColor(undefined), 'rgb(230 100 0)');
});

// ── Row chips — the roster's own labels, as the Satellites page shows them ───
const chips = labels => C.oasisSatLabelChips(labels).map(c => c.text);

test('chips use the roster vocabulary, abbreviated only where needed', () => {
  assert.deepStrictEqual(chips(['WEATHER']), ['WX']);
  assert.deepStrictEqual(chips(['LINEAR']), ['LIN']);
  assert.deepStrictEqual(chips(['APRS']), ['APRS']);
  assert.deepStrictEqual(chips(['SSTV']), ['SSTV']);
});

test('VOICE is dropped when FM is present — the aggregator always emits both', () => {
  // satnogs._MODE_LABELS maps FM/FMN/NFM to (VOICE, FM), so VOICE never appears
  // alone. Printing both on a 7" row is duplication, not detail.
  assert.deepStrictEqual(chips(['VOICE', 'FM']), ['FM']);
});

test('SSB is dropped when LINEAR is present — likewise always emitted together', () => {
  // From an SSB mode or a Transponder type, both labels land as a set.
  assert.deepStrictEqual(chips(['LINEAR', 'SSB']), ['LIN']);
});

test('a lone half of a redundant pair still shows', () => {
  // Defensive: if the aggregator ever emits one without the other, the row must
  // not silently lose the capability.
  assert.deepStrictEqual(chips(['VOICE']), ['VOICE']);
  assert.deepStrictEqual(chips(['SSB']), ['SSB']);
});

test('chips keep the roster order, not the order labels arrived in', () => {
  assert.deepStrictEqual(chips(['CREWED', 'APRS', 'FM', 'SSTV']),
                         ['FM', 'APRS', 'SSTV', 'CREW']);
});

test('the ISS case stays short enough for a row', () => {
  assert.deepStrictEqual(chips(['VOICE', 'FM', 'APRS', 'SSTV', 'CREWED']),
                         ['FM', 'APRS', 'SSTV', 'CREW']);
});

test('CREWED is neutral — it is a fact about the bird, not something to work', () => {
  const crew = C.oasisSatLabelChips(['CREWED'])[0];
  assert.strictEqual(crew.color, null);
});

test('each chip takes its channel colour', () => {
  const byText = Object.fromEntries(
    C.oasisSatLabelChips(['FM', 'WEATHER', 'APRS']).map(c => [c.text, c.color]));
  assert.strictEqual(byText.FM, 'rgb(0 200 90)');
  assert.strictEqual(byText.WX, 'rgb(0 90 230)');
  assert.strictEqual(byText.APRS, 'rgb(230 100 0)');
});

test('chip markup carries inline colour and no emoji', () => {
  const html = C.oasisSatCapabilityChips(['FM', 'APRS']);
  assert.match(html, /color:rgb\(0 200 90\)/);
  assert.match(html, /color:rgb\(230 100 0\)/);
  // The Pi has no emoji font — anything above the BMP renders as tofu.
  assert.ok(![...html].some(ch => ch.codePointAt(0) >= 0x1F000), 'no emoji in chip markup');
});

test('no chips for an unlabelled bird, and nothing throws', () => {
  assert.deepStrictEqual(chips([]), []);
  assert.deepStrictEqual(chips(undefined), []);
});
