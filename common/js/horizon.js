/*
 * horizon.js — the operator's azimuth-dependent horizon mask.
 *
 * `min_elev` is one number for a horizon that is not a circle: you may see 5
 * degrees to the south over water and nothing under 20 to the north. This is the
 * shape of the real skyline, at 22.5-degree resolution.
 *
 * WHERE THE MATHS LIVES. Only here. Every pass already carries `peak_az`, so the
 * client can evaluate the mask with no help from the server — which is why
 * predict.py did not change for this feature: no new sampling, no /passes budget
 * risk on a Pi 3, no multi-window pass records. The server stores, serves and
 * validates the mask; it never evaluates it. One implementation, so there is no
 * second one to drift.
 *
 * THE MASK MARKS, IT NEVER FILTERS. A filter deletes the pass, and if the mask is
 * slightly wrong — or the antenna has just moved, or the operator would try a
 * marginal pass anyway — the pass is gone with nothing on screen to say why.
 * min_elev already does the crude "do not list garbage" job.
 *
 * A MISSING SECTOR FALLS BACK TO min_elev, so {"N": 25} is a legal mask and a
 * partial one is not an error — same posture as _min_elev() itself, where a typo
 * in a config file must never take the pass list down.
 *
 * Note the sector names are NOT azCompass's vocabulary: that one rounds to 8
 * names and is shared with the kiosk, where "NW" must keep meaning what it means.
 * These 16 are a superset — NW is 315 degrees in both — and exist only as config
 * keys.
 *
 * Loaded as a classic <script>, and requireable by node --test.
 */
(function (root, factory) {
  var api = factory(root);
  root.OasisHorizon = api;
  root.floorAt = api.floorAt;
  root.isBlocked = api.isBlocked;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var NAMES = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
               'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
  var STEP = 360 / NAMES.length;                    /* 22.5 degrees */

  var SECTORS = NAMES.map(function (name, i) {
    return { name: name, az: i * STEP };
  });

  function norm(deg) {
    var d = ((deg % 360) + 360) % 360;
    return Number.isFinite(d) ? d : 0;
  }

  /* One sector's floor, or min_elev when it is absent, non-numeric or out of the
     range the save route enforces. Silently falling back beats a NaN that would
     compare false against every elevation and quietly unmark every pass. */
  function sectorFloor(horizon, i, minElev) {
    var v = horizon ? horizon[NAMES[i]] : undefined;
    var n = typeof v === 'number' ? v : parseFloat(v);
    if (!Number.isFinite(n) || n < 0 || n > 89) return minElev;
    return n;
  }

  /* The floor at an arbitrary bearing, linearly interpolated between the two
     bracketing sector centres. Interpolating rather than stepping is what makes
     the drawn rim a curve instead of a staircase, and it wraps across north
     because index arithmetic is modulo 16 — the short way, always. */
  function floorAt(horizon, azDeg, minElev) {
    var floor = Number.isFinite(minElev) ? minElev : 0;
    var az = norm(azDeg);
    var i = Math.floor(az / STEP);
    var t = (az - i * STEP) / STEP;
    var a = sectorFloor(horizon, i % NAMES.length, floor);
    var b = sectorFloor(horizon, (i + 1) % NAMES.length, floor);
    return a + (b - a) * t;
  }

  function isBlocked(horizon, azDeg, elDeg, minElev) {
    return elDeg < floorAt(horizon, azDeg, minElev);
  }

  /* The shaded rim, as an SVG `d`: the outer edge, then the skyline traced BACK,
     as two closed subpaths for fill-rule evenodd. Tracing back matters — one
     continuous subpath leaves a radial spoke across the plot.

     `polar` is passed in rather than imported: sat-geometry.js lives in the
     satellites page's own static/ and a common/js module may not reach into it.

     Sampled every 3 degrees because the floor is INTERPOLATED between 16 sector
     centres; a coarser sample would draw the corners of the interpolation rather
     than the curve. */
  var RIM_STEP_DEG = 3;

  function rimPath(horizon, minElev, polar, cx, cy, r) {
    if (!horizon || !Object.keys(horizon).length) return '';
    var outer = [], inner = [];
    for (var az = 0; az <= 360; az += RIM_STEP_DEG) {
      var o = polar(az, 0, cx, cy, r);
      var q = polar(az, floorAt(horizon, az, minElev), cx, cy, r);
      outer.push(o.x.toFixed(1) + ' ' + o.y.toFixed(1));
      inner.push(q.x.toFixed(1) + ' ' + q.y.toFixed(1));
    }
    inner.reverse();
    return 'M' + outer.join('L') + 'ZM' + inner.join('L') + 'Z';
  }

  return { SECTORS: SECTORS, NAMES: NAMES, STEP: STEP,
           floorAt: floorAt, isBlocked: isBlocked, rimPath: rimPath };
});
