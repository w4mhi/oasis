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
    // `nwr-listen` was a SYNTHETIC unit while the capture was an ad-hoc Flask
    // subprocess, and a systemd probe would have answered false forever. The
    // watch is now the oasis-nwr daemon — an ordinary unit — so the live probe
    // is real, and a station that enabled it outside setup-oasis.py keeps its
    // Weather Radio card instead of hiding it.
    { id: 'nwr',     name: 'Weather Radio',   gate: { features: ['nwr'], unit: 'oasis-nwr' } },
    { id: 'fcc',     name: 'FCC Database',    gate: { features: ['fcc'] } },
    { id: 'maps',    name: 'Offline Maps',    gate: null },
    { id: 'rb',      name: 'Repeater Book',   gate: { features: ['repeaterbook'] } },
    { id: 'manuals', name: 'Radio Manuals',   gate: null },
    { id: 'kiwix',   name: 'Kiwix',           gate: { features: ['kiwix'], unit: 'kiwix' } },
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

  // oasisCountPmtiles lived here: a client-side recursive walk that counted
  // .pmtiles by issuing one /api/browse request PER DIRECTORY. Sharing it kept
  // the two dashboards consistent, but it also made both of them hammer the
  // station — on a live Pi it made /api/browse the busiest endpoint on the box,
  // and the cost scaled with tree depth times open dashboards. The recursion now
  // runs once, server-side and cached, behind GET /api/health/maps. Nothing
  // client-side should walk a directory tree over HTTP again.

  // ── The RTL/SDR assignment dot ─────────────────────────────────────────────
  // hollow = nothing assigned · amber = assigned but not working · green = working.
  // index.html and the kiosk each carried a byte-identical copy of this decision,
  // which is how the bug below survived in both at once.
  //
  // GREEN MEANS "THIS SERVICE CAN DO ITS JOB", not "something is happening right
  // now". For a daemon service that is its unit being active. Satellites has no
  // daemon — the recorder is an ad-hoc subprocess started when you arm a pass —
  // so its healthy steady state is "assigned and the dongle is free", and the old
  // rule (green only while recording or streaming) painted a perfectly ready
  // station amber whenever it was not mid-capture, which is nearly always.
  //
  // Amber keeps the meaning it should always have had for satellites: assigned,
  // but ANOTHER service is holding the dongle, so arming a pass right now would
  // fail. That is worth a warning colour. Idle is not.
  //
  //   assigned  something is assigned to this service at all
  //   active    the unit is running, or WE hold the dongle (recording/streaming)
  //   blocked   assigned, but someone else holds it
  //   daemon    readiness IS a running unit. Explicit, not inferred from having a
  //             unit name — deriving "has a unit" from "is a daemon" is how a
  //             service OASIS cannot actually probe ends up painted a green
  //             nothing supports. Say which it is; never infer it.
  function sdrDotClass(s) {
    s = s || {};
    if (!s.assigned) return '';
    if (s.active) return 'inuse';
    if (s.blocked) return 'assigned';
    return s.daemon ? 'assigned' : 'inuse';
  }

  root.OASIS_SERVICES = OASIS_SERVICES;
  root.oasisResolveHidden = oasisResolveHidden;
  root.oasisAssertChecks = oasisAssertChecks;
  root.sdrDotClass = sdrDotClass;
})(typeof window !== 'undefined' ? window : this);
