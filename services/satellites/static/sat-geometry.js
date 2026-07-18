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

  return { project, splitAntimeridian, splitAtNow, polar };
});
