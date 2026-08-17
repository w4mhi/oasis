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
assert.strictEqual(nwr.gate.unit, undefined,
  'nwr-listen is synthetic: a unit probe would hide the service forever');

// Both dashboards must implement a check for every registry id.
['index.html', path.join('oasis-dashboard', 'dashboard.html')].forEach(function (page) {
  const html = fs.readFileSync(path.join(root, page), 'utf8');
  assert.ok(/id:\s*'nwr'/.test(html), page + ' is missing the nwr check');
});

console.log('service-registry-nwr: ok');
