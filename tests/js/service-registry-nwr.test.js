'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..', '..');
const src = fs.readFileSync(
  path.join(root, 'common', 'js', 'service-registry.js'), 'utf8');
const sandbox = {};
new Function('window', src).call(sandbox, sandbox);

const ids = sandbox.OASIS_SERVICES.map(function (s) { return s.id; });

assert.ok(ids.indexOf('nwr') !== -1, 'nwr must be in the registry');

const nwr = sandbox.OASIS_SERVICES.filter(function (s) { return s.id === 'nwr'; })[0];
assert.strictEqual(nwr.name, 'Weather Radio');
assert.deepStrictEqual(nwr.gate.features, ['nwr']);
// The capture left Flask for the oasis-nwr daemon, so the gate's live-reality
// fallback has a real unit to probe. It was `undefined` while `nwr-listen` was
// a synthetic token no systemd could answer for.
assert.strictEqual(nwr.gate.unit, 'oasis-nwr',
  'the watch is an ordinary unit now: a box that enabled it outside ' +
  'setup-oasis.py must keep its Weather Radio card');

// Both dashboards must implement a check for every registry id.
['index.html', path.join('oasis-dashboard', 'dashboard.html')].forEach(function (page) {
  const html = fs.readFileSync(path.join(root, page), 'utf8');
  assert.ok(/id:\s*'nwr'/.test(html), page + ' is missing the nwr check');
});

// Finding 2: having a *check* is not the same as *gating* it. index.html
// builds its own HIDDEN_SVCS from a SEPARATE map (GATEABLE), not from
// oasisResolveHidden() -- so it can implement the nwr check above and still
// count an uninstalled nwr as one extra DOWN that the kiosk (which does use
// oasisResolveHidden()) correctly hides. Assert index.html's own gate table
// actually carries the entry, not just that a check function exists.
const idxSrc = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const gateableMatch = idxSrc.match(/const GATEABLE = \{[\s\S]*?\n\};/);
assert.ok(gateableMatch, 'index.html: GATEABLE object not found');
const GATEABLE = new Function(gateableMatch[0] + '\nreturn GATEABLE;')();
assert.ok(Object.prototype.hasOwnProperty.call(GATEABLE, 'nwr'),
  'index.html GATEABLE is missing an "nwr" entry -- a box without NWR ' +
  'installed will count it as an extra DOWN service and the "Weather Radio" ' +
  'nav link will never be gated, unlike the kiosk which shares the registry');
assert.deepStrictEqual(GATEABLE.nwr, ['nwr']);

console.log('service-registry-nwr: ok');
