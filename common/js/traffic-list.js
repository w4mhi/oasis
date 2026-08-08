/*
 * traffic-list.js — shared logic for the OASIS traffic list.
 *
 * The dashboard (index.html) and the traffic map's station drawer
 * (maps/traffic/map.html) render the SAME ten columns in the same order, but
 * grew as two independent implementations. This module holds the parts that are
 * genuinely one thing:
 *
 *   lastHeardEpoch   timestamp string -> epoch ms (both wire formats)
 *   stationSource    APRS station -> 'rf' | 'is'   (heard-via transport)
 *   sourceKey        any row -> 'rf' | 'is' | 'adsb'
 *   sourceLabel      any row -> 'RF' | 'IS' | 'ADS-B'
 *   aircraftRows     live + 24h-history ADS-B -> station-shaped rows
 *   sortByRecency    newest-first, keyed once per row
 *   courseHtml       course + speed -> the rotated-arrow compass cell fragment
 *   altColorFor      row -> altitude colour (aircraft only) or null
 *   isUnlocated      row -> heard-but-unpositioned aircraft
 *   filters.*        composable predicate factories (age/callsign/grid/sources)
 *   sourceBreakdown  per-cycle counts by receive path + newest-packet freshness
 *
 * The filter ENGINE is shared; the filter SET is not. Each page composes
 * filters.all(...) from these factories plus its own extras, because the two
 * lists have different jobs: the map drawer is a view OF THE MAP, so it
 * inherits map-only filters (objects-panel categories, movers-only, hazard
 * dismissal, hazards exempt from ageing) and its rows fly to a track; the
 * dashboard list is standalone, so it owns filters the map has no use for
 * (emergency chip, hazard pills) and its rows link out. Forcing the SETS
 * together would mean giving the dashboard controls it can't drive, or making
 * the drawer stop matching its own map.
 *
 * Likewise sourceBreakdown returns data, not markup — the pages paint the same
 * numbers into different elements with different colour conventions.
 *
 * Still page-local by design: the <tr> builders (different ids, different row
 * click behaviour) and the per-page filter sets above.
 *
 * Depends on common/js/adsb.js (acSymbol / acShapeFor / adsbSymCode / altColor).
 *
 * Classic <script> served by Flask (loaded by the map + dashboard); also
 * requireable by node --test (no root package.json -> .js is CommonJS).
 */
(function (root, factory) {
  var api = factory(root);
  root.OasisTrafficList = api;
  root.lastHeardEpoch = api.lastHeardEpoch;
  root.trafficSourceKey = api.sourceKey;
  root.trafficSourceLabel = api.sourceLabel;
  root.trafficCourseHtml = api.courseHtml;
  root.trafficSortByRecency = api.sortByRecency;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
  'use strict';

  // adsb.js helpers, resolved at call time: in the browser it is a sibling
  // <script> (load order already guarantees it); under node --test a direct
  // require works whether or not the caller loaded it first.
  function _adsb() {
    if (root && typeof root.acShapeFor === 'function') return root;
    if (typeof require === 'function') { try { return require('./adsb.js'); } catch (_) { /* fall through */ } }
    return null;
  }

  // last_heard -> epoch ms. Parses BOTH GrayWolf's "YYYY-MM-DD HH:MM:SS.fff…±HH:MM"
  // (space separator, nanoseconds, local offset) and aircraft ISO "…T…Z":
  // normalize space->T and clamp the fraction to milliseconds so every browser's
  // Date() agrees. Unparseable -> 0, which sorts last.
  //
  // This replaced a localeCompare that string-compared the two different formats
  // (space+offset vs T+Z) — never a valid chronological order.
  function lastHeardEpoch(s) {
    if (!s) return 0;
    var norm = String(s).replace('Z', '+00:00').replace(' ', 'T').replace(/(\.\d{3})\d+/, '$1');
    var t = new Date(norm).getTime();
    return isNaN(t) ? 0 : t;
  }

  // Heard-via transport for an APRS station. GrayWolf stores it in `via`: 'rf'
  // (off the air) or 'is' (APRS-IS/internet). Trust that; only sniff the path
  // for a q-construct / igate marker when `via` is missing or unrecognised.
  function stationSource(s) {
    var v = String((s && s.via) || '').trim().toLowerCase();
    if (v === 'rf') return 'rf';
    if (v === 'is') return 'is';
    var hay = (v + ' ' + (((s && s.path) || []).join(' '))).toUpperCase();
    var isIS = hay.indexOf('TCPIP') !== -1 || hay.indexOf('IGATE') !== -1 || /\bQA[A-Z]/.test(hay);
    return isIS ? 'is' : 'rf';
  }

  // Any list row -> which of the three receive paths it came in on.
  function sourceKey(s) {
    return (s && s._kind === 'aircraft') ? 'adsb' : stationSource(s);
  }

  function sourceLabel(s) {
    var k = sourceKey(s);
    return k === 'adsb' ? 'ADS-B' : k.toUpperCase();
  }

  // Live aircraft UNION last-24h history, keyed by hex (live wins). Each entry
  // gets an absolute epoch `_ts`: a live target from the poll clock
  // (now - seen), a history-only target from its stored observation ts — so both
  // age identically through the shared last_heard path.
  function _mergeByHex(live, recent, now) {
    var byHex = {};
    // _live distinguishes currently-tracked aircraft from 24h-history-only ones
    // (out of range / landed) — the list keeps the latter so they age like APRS.
    // Both feeds now send an absolute `last_seen` (ISO) resolved server-side, so
    // there is ONE path: no epoch `ts`, and no reconciling dump1090's clock with
    // the browser's. `now` is kept only as the fallback for a record that somehow
    // arrives without it.
    function _tsOf(a) {
      if (a.last_seen) return Date.parse(a.last_seen) / 1000;
      if (typeof a.ts === 'number') return a.ts;
      return now - ((typeof a.seen === 'number') ? a.seen : 0);
    }
    (recent || []).forEach(function (a) {
      byHex[a.hex] = Object.assign({}, a, { _ts: _tsOf(a), _live: false });
    });
    (live || []).forEach(function (a) {
      byHex[a.hex] = Object.assign({}, a, { _ts: _tsOf(a), _live: true });
    });
    return Object.values(byHex);
  }

  // A row with an unusable timestamp must render as "unknown age", NOT throw.
  // new Date(NaN).toISOString() raises RangeError, and because this runs inside a
  // .map() that single bad record used to abort aircraftRows entirely — taking the
  // APRS stations down with it, since renderAprsList never reached its tbody
  // write. A rendering path must degrade, never die.
  function _isoOrNull(tsSeconds) {
    var ms = Number(tsSeconds) * 1000;
    if (!isFinite(ms)) return null;
    var d = new Date(ms);
    return isNaN(d.getTime()) ? null : d.toISOString();
  }

  var EMERGENCY_SQUAWKS = ['7500', '7600', '7700'];

  // Normalize aircraft into station-shaped rows the list already understands:
  // an APRS aircraft/heli symbol (so the sprite icon + Type filter fold them
  // in), gs->mph and alt(ft)->m for the shared unit-aware formatters, and
  // `seen` -> an absolute ISO last_heard for the Age column.
  //
  // opts: { live, recent, now }  — now is the poll's dump1090 clock, in seconds.
  function aircraftRows(opts) {
    opts = opts || {};
    var A = _adsb();
    var merged = _mergeByHex(opts.live, opts.recent, opts.now || 0);
    // Drop LIVE aircraft with no position (heard-but-no-fix — many, and they
    // clutter). But KEEP aged/out-of-range aircraft (24h history) even when
    // their last observation was position-less, so a plane that disappears ages
    // 1m/2m… in the list like an APRS station instead of vanishing.
    return merged.filter(function (a) {
      return (a.lat != null && a.lon != null) || !a._live;
    }).map(function (a) {
      var positioned = a.lat != null && a.lon != null;
      // alt_baro is null when the aircraft sends no barometric altitude
      // (unknown, not 0) — guard before Number(), since Number(null) === 0 would
      // render a no-altitude aircraft as a (red) "0 ft".
      var altFt = (a.alt_baro == null || a.alt_baro === 'ground') ? null
        : (isFinite(Number(a.alt_baro)) ? Number(a.alt_baro) : null);
      var sym = (A && typeof A.acSymbol === 'function')
        ? A.acSymbol(a)
        : ['/', (A && A.adsbSymCode) ? A.adsbSymCode(a.category) : (a.category === 'A7' ? 'X' : "'")];
      var shp = (A && typeof A.acShapeFor === 'function') ? A.acShapeFor(a) : null;
      var bits = [];
      if (a.squawk) bits.push('sq ' + a.squawk);
      if (EMERGENCY_SQUAWKS.indexOf(a.squawk) !== -1) bits.push('⚠ ' + a.squawk);
      if (shp && shp.key === 'mil') bits.push(shp.filled ? 'military' : 'susp. mil');
      return {
        _kind: 'aircraft',
        hex: a.hex || '',
        callsign: (a.flight || a.hex || '').trim() || (a.hex || '?'),
        sym_table: sym[0], sym_code: sym[1],
        lat: positioned ? a.lat : null,
        lon: positioned ? a.lon : null,
        speed_mph: (a.gs != null) ? Math.round(a.gs * 1.15078) : 0,
        course: (a.track != null) ? a.track : null,
        alt_m: (altFt != null) ? altFt / 3.28084 : null,
        last_heard: _isoOrNull(a._ts),
        via: 'adsb',
        comment: bits.join(' · '),
        _positioned: positioned
      };
    });
  }

  // Newest-first by real time. Decorate-sort-undecorate: lastHeardEpoch does
  // three regex replaces plus a Date parse, so calling it from the comparator
  // ran it O(n log n) times. Keying each row once is several times faster and
  // gives the same order (sort is stable in both forms). Never mutates `rows`.
  function sortByRecency(rows) {
    var keyed = rows.map(function (s) { return [lastHeardEpoch(s.last_heard), s]; });
    keyed.sort(function (a, b) { return b[0] - a[0]; });
    return keyed.map(function (p) { return p[1]; });
  }

  var COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];

  // The Speed cell's course fragment: a glyph arrow rotated to the heading, the
  // 8-point compass name, and the degrees. Only meaningful when actually moving,
  // so a parked station with a stale course reads blank. Contains no caller data,
  // so the returned HTML needs no escaping.
  function courseHtml(course, speedMph) {
    if (course == null || !(speedMph > 0)) return '';
    var deg = Math.round(course);
    return ' <span style="display:inline-block;transform:rotate(' + deg + 'deg)">↑</span> '
      + COMPASS[Math.round(deg / 45) % 8] + ' ' + deg + '°';
  }

  // Altitude colour for a row, or null to leave the cell in the default colour.
  // Only aircraft are altitude-banded; APRS stations report ground elevation,
  // which the flight-level palette would misrepresent.
  function altColorFor(s) {
    var A = _adsb();
    if (!s || s._kind !== 'aircraft' || s.alt_m == null) return null;
    if (!A || typeof A.altColor !== 'function') return null;
    return A.altColor(s.alt_m * 3.28084);
  }

  // Heard but never positioned — rendered greyed, and not clickable on the map.
  function isUnlocated(s) {
    return !!(s && s._kind === 'aircraft' && !s._positioned);
  }


  // ── Filter engine ──────────────────────────────────────────────────────────
  // Small predicate factories the two lists compose themselves. Sharing the
  // ENGINE, not the filter SET: the pages legitimately filter on different
  // things (the map drawer inherits objects-panel categories, movers-only and
  // hazard dismissal from the map; the dashboard has an emergency chip and
  // hazard pills the map has no use for), so each composes these with its own
  // extras via filters.all(...).

  // Age cutoff. `cutoffMs` of 0 disables it. `isExempt` lets a page keep rows
  // that must never age out (the map holds active hazard incidents on screen
  // regardless of staleness — they are cleared by hand, not by time).
  function fAge(cutoffMs, isExempt) {
    if (!cutoffMs) return function () { return true; };
    return function (s) {
      if (isExempt && isExempt(s)) return true;
      var t = lastHeardEpoch(s && s.last_heard);
      return t > 0 && t >= cutoffMs;
    };
  }

  // Callsign: case-insensitive prefix (K7 catches every K7… incl. SSIDs), or a
  // `*` wildcard anchored at both ends (K7*9). Every regex metacharacter except
  // `*` is escaped, so an operator typing "K7(" gets no matches rather than a
  // thrown SyntaxError. An invalid pattern still yields a match-nothing
  // predicate rather than taking the render down.
  function fCallsign(query) {
    var q = String(query || '').trim().toUpperCase();
    if (!q) return function () { return true; };
    if (q.indexOf('*') === -1) {
      return function (s) { return String((s && s.callsign) || '').toUpperCase().indexOf(q) === 0; };
    }
    var re;
    try {
      re = new RegExp('^' + q.replace(/[.+?^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*') + '$');
    } catch (_) {
      return function () { return false; };
    }
    return function (s) { return re.test(String((s && s.callsign) || '').toUpperCase()); };
  }

  // Grid: 'All', a 4-char Maidenhead field, or '__nofix' for position-less rows.
  // `gridOf` is injected so a page can supply its own locator function.
  function fGrid(sel, gridOf) {
    if (!sel || sel === 'All') return function () { return true; };
    return function (s) {
      var g = gridOf(s && s.lat, s && s.lon);
      if (sel === '__nofix') return !g;
      return String(g || '').slice(0, 4) === sel;
    };
  }

  // Source: an empty/absent/complete selection means everything, so clearing the
  // last pill returns to all sources rather than to an empty list.
  function fSources(selected) {
    if (!selected || !selected.length || selected.length >= 3) return function () { return true; };
    return function (s) { return selected.indexOf(sourceKey(s)) !== -1; };
  }

  // Compose: every predicate must pass. Nulls are ignored so a page can build
  // its list conditionally without filtering them out first.
  function fAll() {
    var preds = Array.prototype.slice.call(arguments).filter(Boolean);
    return function (s) {
      for (var i = 0; i < preds.length; i++) { if (!preds[i](s)) return false; }
      return true;
    };
  }

  // ── Source breakdown ───────────────────────────────────────────────────────
  // Per-cycle counts by receive path plus the freshness of the newest packet
  // across EVERYTHING tracked. Returns data only — the two pages paint it
  // differently (the dashboard into its own element with CSS classes, the map
  // appended to the breakdown with an inline colour), so the DOM stays local
  // while the arithmetic is shared.
  //
  // `windowMs` bounds the "heard this cycle" counts; it is padded past the
  // refresh interval to absorb fetch/clock jitter. Counts deliberately ignore
  // the active column filters — this is a live "what is arriving" readout.
  //
  // `now` must be the instant the STATION snapshot was fetched, not render-time
  // wall clock: callers re-render far more often than they re-poll (the map
  // redraws the drawer every 2 s on the ADS-B poll), and a moving clock over a
  // fixed snapshot made these counts decay toward zero between polls with no
  // new data involved. `aircraftNow` is the same for the aircraft snapshot,
  // which on the map is polled on a much faster cadence; it defaults to `now`
  // for callers that fetch both together.
  function sourceBreakdown(opts) {
    opts = opts || {};
    var now = opts.now || Date.now();
    var acNow = opts.aircraftNow || now;
    var windowMs = opts.windowMs || 20000;
    var stations = opts.stations || [];
    var aircraft = opts.aircraft || [];
    var counts = { rf: 0, is: 0, adsb: 0 };
    var newest = 0;
    function tally(rows, anchor) {
      for (var i = 0; i < rows.length; i++) {
        var t = lastHeardEpoch(rows[i].last_heard);
        if (t > newest) newest = t;
        if (t > 0 && (anchor - t) <= windowMs) {
          var k = sourceKey(rows[i]);
          if (k in counts) counts[k]++;
        }
      }
    }
    tally(stations, now);
    tally(aircraft, acNow);
    var out = { rf: counts.rf, is: counts.is, adsb: counts.adsb, newest: newest,
                ageMins: null, freshText: '', tone: '' };
    if (newest > 0) {
      var ageMins = Math.round((now - newest) / 60000);
      out.ageMins = ageMins;
      out.freshText = ageMins < 2 ? 'just now'
        : ageMins < 60 ? ageMins + 'm ago'
        : Math.round(ageMins / 60) + 'h ago';
      // Tone, not colour: each page maps it to its own palette.
      out.tone = ageMins < 30 ? 'ok' : ageMins < 360 ? 'warn' : 'stale';
    }
    return out;
  }

  return {
    lastHeardEpoch: lastHeardEpoch,
    stationSource: stationSource,
    sourceKey: sourceKey,
    sourceLabel: sourceLabel,
    aircraftRows: aircraftRows,
    sortByRecency: sortByRecency,
    courseHtml: courseHtml,
    altColorFor: altColorFor,
    isUnlocated: isUnlocated,
    EMERGENCY_SQUAWKS: EMERGENCY_SQUAWKS,
    sourceBreakdown: sourceBreakdown,
    filters: { age: fAge, callsign: fCallsign, grid: fGrid, sources: fSources, all: fAll }
  };
});
