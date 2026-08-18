'use strict';
// Finding 5(a): a clock_suspect NWR alert can sit in alerts.active() for up
// to STALE_CLOCK_QUARANTINE_S (90 days -- see services/nwr/common/alerts.py),
// and the kiosk's updateSvcCounts() used to EARLY-RETURN whenever an active
// matched alert existed, replacing the whole "N UP / N WRN / N DN" tally with
// the "WX <event>" badge. Both OASIS Pis have booted weeks stale with every
// health check green -- exactly what sets clock_suspect -- so that early
// return could blind the kiosk's service-health pill for months. The fix
// keeps the alert badge (a passer-by should still see it) but makes it
// NON-EXCLUSIVE: the health counts must always render too.
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..', '..');
const src = fs.readFileSync(path.join(root, 'oasis-dashboard', 'dashboard.html'), 'utf8');

function extractFunction(source, name) {
  const marker = 'function ' + name + '(';
  const start = source.indexOf(marker);
  assert.ok(start !== -1, name + ' not found in oasis-dashboard/dashboard.html');
  const braceStart = source.indexOf('{', start);
  let depth = 0, end = braceStart;
  for (; end < source.length; end++) {
    if (source[end] === '{') depth++;
    else if (source[end] === '}') { depth--; if (depth === 0) { end++; break; } }
  }
  return source.slice(start, end);
}

const fnSrc = extractFunction(src, 'updateSvcCounts');

function run(nwrAlertEvent, states) {
  const classes = new Set();
  const el = {
    classList: { add: c => classes.add(c), remove: c => classes.delete(c) },
    title: '',
    innerHTML: '',
  };
  const sandbox = new Function(
    'document', 'OASIS_SERVICES', 'HIDDEN_SVCS', '_svcStates', '_nwrAlertEvent', 'esc',
    fnSrc + '\nreturn updateSvcCounts;'
  );
  const updateSvcCounts = sandbox(
    { getElementById: id => (id === 'svcpill' ? el : null) },
    [{ id: 'a' }, { id: 'b' }, { id: 'c' }],
    new Set(),
    states,
    nwrAlertEvent,
    s => s
  );
  updateSvcCounts();
  return { el, classes };
}

// No alert: plain health tally, as before.
{
  const { el, classes } = run(null, { a: 'up', b: 'up', c: 'down' });
  assert.ok(!classes.has('alert'));
  assert.match(el.innerHTML, /2 UP/);
  assert.match(el.innerHTML, /1 DN/);
}

// An active alert must show ALONGSIDE the counts, never instead of them.
{
  const { el, classes } = run('TOR', { a: 'up', b: 'warn', c: 'down' });
  assert.ok(classes.has('alert'), 'alert styling must still apply');
  assert.ok(el.innerHTML.includes('TOR'), 'the alert event must still render');
  assert.match(el.innerHTML, /1 UP/,
    'service health counts must render even while an alert is active -- ' +
    'a stuck clock_suspect alert must never blind the health pill');
  assert.match(el.innerHTML, /1 WRN/);
  assert.match(el.innerHTML, /1 DN/);
}

console.log('nwr-kiosk-pill: ok');
