// hw-console.js
// -----------------------------------------------------------------------------
// Shared brains of the HW/SRV assignment console ("Service Operations"), used by
// BOTH the desktop console (index.html, the rail + matrix) and the kiosk overlay
// (oasis-dashboard/dashboard.html). Each page keeps its OWN render (their DOM
// differs — a right-docked popover vs a full-screen touch overlay), but the
// decision logic and the server contract live here so the two can't drift.
//
// Console state (GET /api/hardware/console):
//   services: [{ id, name, kinds:[deviceKind…] }]
//   devices:  [{ id, label, kind, serial, assigned, running, locked }]
//   warnings: [{ severity:'warn'|'crit', … }]
(function (root) {
  'use strict';

  // Cell state for a device×service pair: 'na' | 'on' | 'stopped' | 'off'.
  //   na      — this device kind can't serve this service (no toggle, just a dot)
  //   on      — assigned here AND running
  //   stopped — assigned here but not running (idle claim)
  //   off     — not assigned here
  function oasisCstate(dev, svc) {
    if ((svc.kinds || []).indexOf(dev.kind) < 0) return 'na';
    if (dev.assigned === svc.id) return dev.running ? 'on' : 'stopped';
    return 'off';
  }

  // What tapping a cell should do — the decision only, no DOM, no network:
  //   { action: 'none' }   — na cell, ignore
  //   { action: 'locked' } — device locked, refuse and warn
  //   { action: 'stop' }   — running here → stop it (device stays assigned, idle)
  //   { action: 'route' }  — off/stopped → assign + start here (displacing others)
  function oasisFlipPlan(dev, svc) {
    var st = oasisCstate(dev, svc);
    if (st === 'na') return { action: 'none' };
    if (dev.locked) return { action: 'locked' };
    if (st === 'on') return { action: 'stop' };
    return { action: 'route' };
  }

  // Aggregate health for the rail/summary glyph: {lvl, glyph, count}.
  function oasisRailHealth(warnings) {
    var w = warnings || [];
    if (w.some(function (x) { return x.severity === 'crit'; })) return { lvl: 'crit', glyph: '⚠', count: w.length };
    if (w.length) return { lvl: 'warn', glyph: '!', count: w.length };
    return { lvl: 'ok', glyph: '✓', count: 0 };
  }

  // ── Server contract — the ONE place the console endpoints live. Each returns
  // the fetch promise; callers add their own .then/messages. Uses root.fetch so
  // the module loads (but isn't exercised) under node for the pure-logic tests.
  function _post(url, body) {
    return root.fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-OASIS-Request': '1' },
      body: JSON.stringify(body || {})
    });
  }
  var oasisHwConsole = {
    fetchState:     function () { return root.fetch('/api/hardware/console', { cache: 'no-store' }).then(function (r) { return r.json(); }); },
    route:          function (service, deviceId) { return _post('/api/hardware/route', { service: service, device_id: deviceId }); },
    serviceStop:    function (service) { return _post('/api/hardware/service-stop', { service: service }); },
    lock:           function (deviceId, locked) { return _post('/api/hardware/lock', { device_id: deviceId, locked: locked }); },
    stopAll:        function () { return _post('/api/hardware/stop-all', {}); },
    guardianState:  function () { return root.fetch('/api/hardware/guardian', { cache: 'no-store' }).then(function (r) { return r.json(); }); },
    guardianCancel: function () { return _post('/api/hardware/guardian/cancel', {}); }
  };

  root.oasisCstate = oasisCstate;
  root.oasisFlipPlan = oasisFlipPlan;
  root.oasisRailHealth = oasisRailHealth;
  root.oasisHwConsole = oasisHwConsole;
})(typeof window !== 'undefined' ? window : this);
