// service-registry.js
// -----------------------------------------------------------------------------
// The SINGLE source of truth for which services count toward the OASIS health
// summary, shared by index.html and the OASIS Dashboard kiosk
// (oasis-dashboard/dashboard.html) so their "N UP · N WRN · N DN" counts can't
// drift apart. (They used to: the kiosk lacked Winlink RF + ADS-B while counting
// the data-resource services index left out — same box, different numbers.)
//
// Each entry:
//   id, name  — identity + display label
//   gate      — null            → core service, always counted (flask, maps, …)
//               {features:[…], unit, port, enabledOnly}
//                               → hidden ONLY when none of `features` is in the
//                                 installed-services manifest AND the live
//                                 `unit`/`port` probe also says it's absent. The
//                                 unit fallback is why a service enabled outside
//                                 setup-oasis.py (e.g. webssh: running but not in
//                                 the manifest) still counts instead of hiding.
//                                 `enabledOnly` (gpsd) requires a deliberate
//                                 boot-enable, since gpsd is transitively pulled
//                                 in by Pat and would otherwise look installed.
//
// Order = display/tally order. Membership here is authoritative; each page keeps
// its own `check` per id (they touch page-specific state) and MUST cover every id
// — enforce with oasisAssertChecks() at startup so adding a service to the
// registry forces both pages to implement it.
(function (root) {
  'use strict';

  var OASIS_SERVICES = [
    { id: 'flask',   name: 'Flask',           gate: null },
    { id: 'wsgi',    name: 'Web Server',      gate: null },
    { id: 'gw',      name: 'GrayWolf',        gate: { features: ['graywolf'], unit: 'graywolf' } },
    { id: 'winlink', name: 'Winlink',         gate: { features: ['winlink'], unit: 'pat' } },
    { id: 'wlrf',    name: 'Winlink RF',      gate: { features: ['winlink'], unit: 'pat-direwolf' } },
    { id: 'aprs',    name: 'APRS Stats API',  gate: { features: ['graywolf'], unit: 'graywolf-api' } },
    { id: 'feed',    name: 'APRS SDR Feed',   gate: { features: ['rtl-sdr-feed', 'rtl-feed'], unit: 'aprs-sdr-feed' } },
    { id: 'gpsd',    name: 'GPS (gpsd)',      gate: { features: ['gps', 'gps-l76x'], unit: 'gpsd', enabledOnly: true } },
    { id: 'webssh',  name: 'SSH Terminal',    gate: { features: ['webssh'], unit: 'webssh' } },
    { id: 'adsb',    name: 'ADS-B Aircraft',  gate: { features: ['adsb'], unit: 'dump1090-fa' } },
    { id: 'fcc',     name: 'FCC Database',    gate: { features: ['fcc'] } },
    { id: 'maps',    name: 'Offline Maps',    gate: null },
    { id: 'rb',      name: 'Repeater Book',   gate: { features: ['repeaterbook'] } },
    { id: 'manuals', name: 'Radio Manuals',   gate: null },
    { id: 'kiwix',   name: 'Kiwix',           gate: { features: ['kiwix'], unit: 'kiwix' } },
    { id: 'owrx',    name: 'OpenWebRX',       gate: { features: ['openwebrx'], unit: 'openwebrx' } },
    { id: 'wiki',    name: 'Wikipedia',       gate: { features: ['wikipedia'], unit: 'kiwix' } },
  ];

  // Resolve which service ids are hidden (not counted) on this box.
  //   features — the installed-services feature list. A non-array (null) means the
  //              manifest is unknown/unconfigured → hide nothing, count everything.
  //   probe    — async (gate) => bool a page supplies to check live reality for a
  //              gate carrying `unit`/`port`. Called only when the feature gate
  //              misses, so a manually-enabled unit keeps its service visible.
  // Returns a Set of hidden ids.
  async function oasisResolveHidden(features, probe) {
    var hidden = new Set();
    if (!Array.isArray(features)) return hidden;   // unknown manifest → count all
    var inst = new Set(features);
    for (var i = 0; i < OASIS_SERVICES.length; i++) {
      var s = OASIS_SERVICES[i], g = s.gate;
      if (!g) continue;                                             // core → never hidden
      if ((g.features || []).some(function (f) { return inst.has(f); })) continue;
      if ((g.unit || g.port) && typeof probe === 'function') {
        var live = false;
        try { live = await probe(g); } catch (e) { live = false; }
        if (live) continue;                                         // enabled outside setup
      }
      hidden.add(s.id);
    }
    return hidden;
  }

  // Startup guard: warn loudly if a page hasn't supplied a check for every
  // registry service (the mechanism that keeps the two dashboards in lockstep).
  // Returns the list of missing ids (empty when the page is complete).
  function oasisAssertChecks(ids) {
    var have = new Set(ids || []);
    var missing = OASIS_SERVICES.filter(function (s) { return !have.has(s.id); })
                                .map(function (s) { return s.id; });
    if (missing.length) {
      console.error('[oasis] service-registry: this page is missing health checks for: ' +
                    missing.join(', ') + ' — its count will drift from the others.');
    }
    return missing;
  }

  // Count .pmtiles archives anywhere under `startPath`, recursing into
  // subdirectories — maps live nested (maps/tiles/state, maps/tiles/country, …),
  // not at the flat top, so a single listing false-WARNs even when a map exists.
  // Shared by both dashboards so the "N maps present" logic can't drift. `browse`
  // is an async (path) => entries[] the page supplies (same-origin /api/browse),
  // keeping this network-free and node-testable, mirroring oasisResolveHidden's
  // injected `probe`. Depth-capped (default 3) so a stray deep tree can't fan out
  // on the Pi. Entries use { name, type: 'dir'|'file' } from /api/browse.
  async function oasisCountPmtiles(browse, startPath, maxDepth) {
    var depth = (maxDepth == null) ? 3 : maxDepth;
    async function walk(path, d) {
      var entries = await browse(path);
      var n = 0, subdirs = [];
      for (var i = 0; i < (entries || []).length; i++) {
        var e = entries[i];
        if (e.type === 'file' && /\.pmtiles$/.test(e.name)) n++;
        else if (e.type === 'dir' && d > 0) subdirs.push(path + '/' + e.name);
      }
      for (var j = 0; j < subdirs.length; j++) n += await walk(subdirs[j], d - 1);
      return n;
    }
    return walk(startPath, depth);
  }

  root.OASIS_SERVICES = OASIS_SERVICES;
  root.oasisResolveHidden = oasisResolveHidden;
  root.oasisAssertChecks = oasisAssertChecks;
  root.oasisCountPmtiles = oasisCountPmtiles;
})(typeof window !== 'undefined' ? window : this);
