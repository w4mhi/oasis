/*
 * sat-look.js — live look angles to a satellite, shared by the Satellites page
 * and the kiosk dashboard.
 *
 * Both screens now show "how high, which way, how far" for a bird that is
 * overhead, and they must not disagree: one is on the wall and the other is in
 * your hand, and an operator reading both at once will believe the one that is
 * wrong. So the propagation, the formatting and the rising/setting arrow all
 * live here, once.
 *
 * Loaded as a classic <script> AFTER satellite.min.js (the vendored SGP4
 * propagator), whose `satellite` global this reads at call time. Also
 * requireable by node --test — set `global.satellite` first — so the formatter
 * and the trend logic are unit-tested in tests/js/.
 */
(function (root, factory) {
  var api = factory(root);
  root.OasisSatLook = api;
  // Bare globals so the pages' inline call sites read naturally.
  root.currentLook = api.currentLook;
  root.fmtKm = api.fmtKm;
  root.elevTrend = api.elevTrend;
  root.trendArrow = api.trendArrow;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
  'use strict';
  var D2R = Math.PI / 180, R2D = 180 / Math.PI;

  /* Look angles from a station to a satellite at `when` (default: now).
     Returns null when anything needed is missing — no TLE, no station fix, or
     the propagator not loaded — so callers can treat "unknown" and "below the
     horizon" as the distinct things they are.

     rangeKm is the SLANT range: the straight line to the bird, NOT the distance
     across the ground to its sub-point. Those differ by a factor of two at low
     elevation and it is the slant range a radio cares about. */
  function currentLook(l1, l2, station, when) {
    var S = root.satellite;
    if (!S || !l1 || !l2 || !station || station.lat == null || station.lon == null) return null;
    try {
      var t = when || new Date();
      var pv = S.propagate(S.twoline2satrec(l1, l2), t);
      if (!pv || !pv.position) return null;
      var ecf = S.eciToEcf(pv.position, S.gstime(t));
      var la = S.ecfToLookAngles(
        { longitude: station.lon * D2R, latitude: station.lat * D2R, height: 0 }, ecf);
      var out = { el: la.elevation * R2D, az: la.azimuth * R2D, rangeKm: la.rangeSat };
      // A malformed TLE does not throw: twoline2satrec parses it into a satrec
      // full of NaN and propagate carries the NaN all the way out. Without this
      // the screen reads "NaN°", which looks like OUR bug rather than a bad
      // element set — so an unusable answer is reported as no answer.
      if (!isFinite(out.el) || !isFinite(out.az) || !isFinite(out.rangeKm)) return null;
      return out;
    } catch (e) { return null; }
  }

  /* Thousands-separated km, e.g. "1 240 km".
     A space, not a locale separator: a comma reads as a decimal point to half
     the planet, and toLocaleString would make the readout depend on whatever
     locale the kiosk browser happens to boot with.

     DELIBERATELY not routed through common/js/units.js. Satellite readouts are
     metric always, whatever 'oasis_units' says — satellite and amateur-radio
     work is metric worldwide (we speak of the 2 m band, not the 6.6 ft band),
     and converting here would put OASIS at odds with every other tool an
     operator has open. units.js is for terrestrial readouts, where the
     imperial/metric preference is a real preference: aircraft altitude (feet is
     correct aviation convention), ground speed, temperature.
     If you are here to "fix the inconsistency" with fmtAlt: don't. */
  function fmtKm(km) {
    if (km == null || !isFinite(km)) return '—';
    return Math.round(km).toString().replace(/\B(?=(\d{3})+$)/g, ' ') + ' km';
  }

  /* Rising or setting, from successive samples of the same bird.
     Returns 1 (climbing), -1 (falling) or 0 (not known yet).

     Measured rather than derived: comparing against the pass's predicted
     culmination time would need a pass record in scope on both screens, and
     re-propagating a second sample would double the SGP4 work on a tick that
     already walks the whole roster on a Pi 3. Successive ticks are free — the
     caller is already computing this elevation for the readout.

     The first sample of a bird returns 0: with nothing to compare against, an
     arrow would be a guess, and a guessed arrow is exactly the bug this
     replaces. On a flat sample (a paused tab, or a bird at culmination) the
     last known direction is held rather than flapping to neutral. */
  var _lastEl = {}, _lastDir = {};
  var _FLAT_DEG = 1e-4;

  function elevTrend(key, el) {
    var k = String(key);
    if (el == null || !isFinite(el)) { delete _lastEl[k]; delete _lastDir[k]; return 0; }
    var prev = _lastEl[k];
    _lastEl[k] = el;
    if (prev == null) return _lastDir[k] || 0;
    var d = el - prev;
    if (Math.abs(d) < _FLAT_DEG) return _lastDir[k] || 0;
    _lastDir[k] = d > 0 ? 1 : -1;
    return _lastDir[k];
  }

  function resetTrend() { _lastEl = {}; _lastDir = {}; }

  /* BMP arrows, not emoji: the Pi ships no emoji font and would render tofu. */
  function trendArrow(t) { return t > 0 ? '↗' : t < 0 ? '↘' : ''; }

  return { currentLook: currentLook, fmtKm: fmtKm, elevTrend: elevTrend,
           trendArrow: trendArrow, resetTrend: resetTrend };
});
