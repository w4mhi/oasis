// Pure geometry for the satellite SVG views. UMD: usable in the browser
// (window.satgeo) and under node (module.exports) for tests. No dependencies.
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.satgeo = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // Equirectangular projection into a w×h box. lon −180..180 → x 0..w,
  // lat 90..−90 → y 0..h (north up).
  function project(lat, lon, w, h) {
    return { x: (lon + 180) / 360 * w, y: (90 - lat) / 180 * h };
  }

  // Break a lon/lat point list wherever consecutive longitudes jump > 180°
  // (a dateline crossing), so an equirectangular polyline doesn't smear across.
  function splitAntimeridian(points) {
    const segs = [];
    let cur = [];
    for (let i = 0; i < points.length; i++) {
      if (i > 0 && Math.abs(points[i].lon - points[i - 1].lon) > 180) {
        segs.push(cur); cur = [];
      }
      cur.push(points[i]);
    }
    if (cur.length) segs.push(cur);
    return segs;
  }

  // Split a track at the live index. past = [0..nowIndex], future = [nowIndex..end]
  // — they SHARE points[nowIndex] so the drawn line has no gap at the marker.
  function splitAtNow(points, nowIndex) {
    const i = Math.max(0, Math.min(nowIndex, points.length - 1));
    return { past: points.slice(0, i + 1), future: points.slice(i) };
  }

  // Az/el "radar": el 90° → centre (cx,cy); el 0° → radius r. az 0° = north = up.
  function polar(az, el, cx, cy, r) {
    const rad = r * (90 - el) / 90;
    const a = az * Math.PI / 180;
    return { x: cx + rad * Math.sin(a), y: cy - rad * Math.cos(a) };
  }

  // Footprint = the ground region from which the satellite is above the horizon.
  // Its great-circle angular radius from the sub-point, for altitude altKm above
  // a spherical Earth: γ = acos(R / (R + h)).  Degrees.
  var _R_EARTH_KM = 6371;
  function footprintRadiusDeg(altKm) {
    return Math.acos(_R_EARTH_KM / (_R_EARTH_KM + altKm)) * 180 / Math.PI;
  }

  // Ring of {lat, lon} points on the footprint circle centred on (lat, lon) at
  // great-circle angular radius radiusDeg — great-circle destination points at
  // every bearing. n segments (default 72). Longitudes normalised to −180..180.
  function footprint(lat, lon, radiusDeg, n) {
    n = n || 72;
    var latR = lat * Math.PI / 180, lonR = lon * Math.PI / 180, d = radiusDeg * Math.PI / 180;
    var pts = [];
    for (var i = 0; i <= n; i++) {
      var brng = 2 * Math.PI * i / n;
      var lat2 = Math.asin(Math.sin(latR) * Math.cos(d) +
                           Math.cos(latR) * Math.sin(d) * Math.cos(brng));
      var lon2 = lonR + Math.atan2(Math.sin(brng) * Math.sin(d) * Math.cos(latR),
                                   Math.cos(d) - Math.sin(latR) * Math.sin(lat2));
      pts.push({ lat: lat2 * 180 / Math.PI,
                 lon: ((lon2 * 180 / Math.PI + 540) % 360) - 180 });
    }
    return pts;
  }

  // Build SVG path 'd' string(s) for a footprint filled on the equirectangular
  // map, handling the two cases a great-circle circle breaks under: it encircles
  // a POLE (fill the polar cap — close along the top/bottom map edge), or it
  // crosses the ANTIMERIDIAN without a pole (split into arcs). Returns an array
  // of path strings (usually 1, or 2 for a dateline split).
  function footprintPaths(lat, lon, radiusDeg, w, h, n) {
    var ring = footprint(lat, lon, radiusDeg, n || 96);
    var trace = function (pts) {
      return pts.map(function (p, i) {
        var q = project(p.lat, p.lon, w, h);
        return (i ? 'L' : 'M') + q.x.toFixed(1) + ' ' + q.y.toFixed(1);
      }).join(' ');
    };
    // Pole cap: the circle reaches over a pole. Trace the boundary sorted by
    // longitude and close it along that pole's map edge (y=0 north, y=h south).
    if (lat + radiusDeg >= 90 || lat - radiusDeg <= -90) {
      var edgeY = (lat + radiusDeg >= 90) ? 0 : h;
      var sorted = ring.slice().sort(function (a, b) { return a.lon - b.lon; });
      return ['M0 ' + edgeY + ' ' + sorted.map(function (p) {
        var q = project(p.lat, p.lon, w, h);
        return 'L' + q.x.toFixed(1) + ' ' + q.y.toFixed(1);
      }).join(' ') + ' L' + w + ' ' + edgeY + ' Z'];
    }
    // No pole: one polygon, or two arcs if it straddles the dateline.
    var segs = splitAntimeridian(ring);
    if (segs.length <= 1) return [trace(ring) + ' Z'];
    return segs.filter(function (s) { return s.length >= 2; }).map(function (s) { return trace(s) + ' Z'; });
  }

  // Doppler factor at wall-clock time tMs, linearly interpolated between a
  // track's 10 s samples. The factor is dimensionless (−ṙ/c), so the CALLER
  // multiplies by whatever carrier is armed — the server deliberately does not
  // pick a frequency on the operator's behalf.
  //
  // Returns { factor, phase } with phase 'before' | 'live' | 'after', clamped to
  // the track's ends. The clamp is not a fudge: before AOS the first sample is
  // the frequency to dial the rig to when the bird appears, which is the number
  // an operator actually wants while waiting. What to DO with each phase is the
  // page's business, not this function's, hence the phase rather than a null.
  //
  // null when there is no usable track — a caller that fetched one before the
  // server carried `factor` gets nothing rather than a plausible zero.
  function dopplerAt(track, tMs) {
    if (!track || track.length < 2) return null;
    var first = track[0], last = track[track.length - 1];
    if (typeof first.factor !== 'number' || typeof last.factor !== 'number') return null;
    var t0 = Date.parse(first.t), tN = Date.parse(last.t);
    if (!isFinite(t0) || !isFinite(tN)) return null;
    if (tMs <= t0) return { factor: first.factor, phase: 'before' };
    if (tMs >= tN) return { factor: last.factor, phase: 'after' };
    for (var i = 1; i < track.length; i++) {
      var tb = Date.parse(track[i].t);
      if (!isFinite(tb) || tb < tMs) continue;
      var ta = Date.parse(track[i - 1].t), span = tb - ta;
      var fa = track[i - 1].factor, fb = track[i].factor;
      if (typeof fa !== 'number' || typeof fb !== 'number') return null;
      return { factor: span > 0 ? fa + (fb - fa) * ((tMs - ta) / span) : fb,
               phase: 'live' };
    }
    return { factor: last.factor, phase: 'after' };
  }

  return { project, splitAntimeridian, splitAtNow, polar,
           footprintRadiusDeg, footprint, footprintPaths, dopplerAt };
});
