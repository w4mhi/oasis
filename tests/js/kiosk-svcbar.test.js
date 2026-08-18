'use strict';
// The kiosk's SDR service bar is TWO hand-maintained lists that have to agree:
// the SDR_SVCS array that fetchSdrStatus() paints from, and the row of
// <span class="dot" id="dot-*"> elements in #svcbar that it paints onto.
// Nothing connects them at runtime -- fetchSdrStatus() does
// `getElementById('dot-'+svc.id); if (!dot) return;`, so an entry with no dot
// is silently skipped and a dot with no entry silently stays hollow forever.
//
// That is exactly how the NOAA Weather Radio watch (oasis-nwr) went missing
// from this bar: it was the FOURTH hardcoded service list on this branch to
// not learn about `nwr`. This test makes the next omission a failing build
// rather than a dot nobody notices is absent.
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..', '..');
const src = fs.readFileSync(path.join(root, 'oasis-dashboard', 'dashboard.html'), 'utf8');

// ── the list ────────────────────────────────────────────────────────────────
function svcList() {
  const start = src.indexOf('const SDR_SVCS = [');
  assert.ok(start !== -1, 'SDR_SVCS not found in oasis-dashboard/dashboard.html');
  const open = src.indexOf('[', start);
  let depth = 0, end = open;
  for (; end < src.length; end++) {
    if (src[end] === '[') depth++;
    else if (src[end] === ']') { depth--; if (depth === 0) { end++; break; } }
  }
  return new Function('return ' + src.slice(open, end) + ';')();
}

// ── the markup ──────────────────────────────────────────────────────────────
function barDotIds() {
  const start = src.indexOf('id="svcbar"');
  assert.ok(start !== -1, '#svcbar not found in oasis-dashboard/dashboard.html');
  const end = src.indexOf('</div>', start);
  assert.ok(end !== -1, '#svcbar has no closing tag');
  const bar = src.slice(start, end);
  return [...bar.matchAll(/id="dot-([a-z0-9-]+)"/g)].map(m => m[1]);
}

const svcs = svcList();
const dots = barDotIds();

// The bug this file exists for: neither side may carry an id the other lacks.
{
  const ids = svcs.map(s => s.id);
  const orphanEntries = ids.filter(id => !dots.includes(id));
  const orphanDots = dots.filter(id => !ids.includes(id));
  assert.deepStrictEqual(orphanEntries, [],
    'SDR_SVCS entries with no dot in #svcbar (they paint nothing): ' + orphanEntries.join(', '));
  assert.deepStrictEqual(orphanDots, [],
    'dots in #svcbar with no SDR_SVCS entry (they stay hollow forever): ' + orphanDots.join(', '));
  assert.strictEqual(new Set(ids).size, ids.length, 'duplicate id in SDR_SVCS');
  assert.strictEqual(new Set(dots).size, dots.length, 'duplicate dot id in #svcbar');
}

// Every entry has to be usable by fetchSdrStatus(): it looks the service up in
// the /api/hardware/devices `services` map by `hw`, so a missing or misspelled
// `hw` leaves the dot permanently hollow with no error anywhere.
for (const s of svcs) {
  assert.ok(s.hw && typeof s.hw === 'string', 'SDR_SVCS entry ' + s.id + ' has no hw key');
  assert.strictEqual(typeof s.daemon, 'boolean', 'SDR_SVCS entry ' + s.id + ' has no daemon flag');
  assert.ok(s.unit === null || typeof s.unit === 'string',
    'SDR_SVCS entry ' + s.id + ' has a unit that is neither null nor a string');
}

// Weather Radio specifically. The hardware services map keys it `nwr`, the
// health probe answers for `oasis-nwr`, and readiness genuinely IS a running
// unit -- the watch holds its dongle continuously, unlike satellites.
{
  const wx = svcs.find(s => s.hw === 'nwr');
  assert.ok(wx, 'Weather Radio (hw:"nwr") is missing from the kiosk SDR bar');
  assert.strictEqual(wx.unit, 'oasis-nwr');
  assert.strictEqual(wx.daemon, true);
  assert.ok(dots.includes(wx.id), 'no dot element for the Weather Radio entry');
}

// The label is WX, not NWR: every other label in this bar is short and the
// kiosk's alert badge and listen pill already say WX.
{
  const start = src.indexOf('id="svcbar"');
  const bar = src.slice(start, src.indexOf('</div>', start));
  assert.match(bar, /id="dot-wx"><\/span>WX</,
    'the Weather Radio dot should be labelled WX');
  assert.ok(!/>NWR</.test(bar), 'the bar should say WX, not NWR');
}

console.log('kiosk-svcbar: ok (' + svcs.length + ' services, ' + dots.length + ' dots)');
