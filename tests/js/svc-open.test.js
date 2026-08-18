'use strict';
// index.html's OPEN-button gate, run as code (sliced out of the page) rather
// than asserted as text.
//
// The Weather Radio card's OPEN button is the ONLY link to /server/nwr/ in the
// whole UI -- the nav link was deliberately removed -- and that page is the
// feature's diagnostic surface: which binary is missing, the daemon's last
// error, the preconditions, the decode log. Gating it on health made it
// unreachable in exactly the states it exists to explain.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');

function openable() {
  const start = html.search(/const SVC_OPEN_\w+ = new Set\(/);
  assert.notStrictEqual(start, -1, 'index.html: the OPEN gate set is gone');
  const end = html.indexOf('\n}', html.indexOf('function _svcOpenable', start));
  assert.notStrictEqual(end, -1, 'index.html: _svcOpenable not found');
  return new Function(html.slice(start, end + 2) + '\nreturn _svcOpenable;')();
}

test('the weather page opens in the states it exists to diagnose', () => {
  const can = openable();
  // 'down' is what nwrCardState returns for missing_deps (DEPS), an
  // unreachable daemon (WATCH DOWN) and no dongle detected -- see
  // common/js/nwr-card.js. All three are read on that page and nowhere else.
  ['down', 'warn', 'up'].forEach((state) => {
    assert.strictEqual(can('nwr', state), true,
      'the weather page is unreachable while the card reads ' + state);
  });
});

test('the exception is nwr alone — no other card changed', () => {
  const can = openable();
  ['adsb', 'gw', 'winlink', 'kiwix', 'owrx', 'wiki'].forEach((id) => {
    assert.strictEqual(can(id, 'up'), true, id + ' must open when it is up');
    assert.strictEqual(can(id, 'warn'), false,
      id + ': OPEN leads to the service itself, and a link to a degraded ' +
      'service was not part of this change');
    assert.strictEqual(can(id, 'down'), false, id + ' must not open when down');
    assert.strictEqual(can(id, undefined), false, id + ': unchecked is not open');
  });
});
