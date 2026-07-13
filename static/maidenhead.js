(function (root) {
  'use strict';

  function latLonToGrid(lat, lon, precision) {
    precision = precision || 6;
    try {
      lat = parseFloat(lat);
      lon = parseFloat(lon);
    } catch (_) {
      return '';
    }
    if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return '';
    if (precision !== 4 && precision !== 6 && precision !== 8) precision = 6;

    let L = lon + 180;
    let B = lat + 90;
    const out = [];
    out.push(String.fromCharCode((L / 20 | 0) + 65));
    out.push(String.fromCharCode((B / 10 | 0) + 65));
    L %= 20;
    B %= 10;
    out.push(String((L / 2 | 0)));
    out.push(String((B / 1 | 0)));
    L %= 2;
    B %= 1;
    if (precision >= 6) {
      out.push(String.fromCharCode((L / (2 / 24) | 0) + 97));
      out.push(String.fromCharCode((B / (1 / 24) | 0) + 97));
    }
    if (precision >= 8) {
      out.push(String((L / (2 / 240) | 0)));
      out.push(String((B / (1 / 240) | 0)));
    }
    return out.slice(0, precision).join('');
  }

  function gridToLatLon(grid) {
    if (typeof grid !== 'string') return null;
    const g = grid.trim().toUpperCase();
    if (!/^[A-R]{2}[0-9]{2}([A-X]{2})?$/.test(g)) return null;
    const lon = (g.charCodeAt(0) - 65) * 20 - 180 + (+g[2]) * 2;
    const lat = (g.charCodeAt(1) - 65) * 10 - 90 + (+g[3]) * 1;
    if (g.length >= 6) {
      return [lat + (g.charCodeAt(5) - 65) * (1 / 24) + (0.5 / 24),
              lon + (g.charCodeAt(4) - 65) * (2 / 24) + (1 / 24)];
    }
    return [lat + 0.5, lon + 1];
  }

  function gridToLonLat(grid) {
    const result = gridToLatLon(grid);
    return result ? [result[1], result[0]] : null;
  }

  const api = {
    latLonToGrid,
    gridToLatLon,
    gridToLonLat
  };

  root.maidenhead = api;
})(typeof window !== 'undefined' ? window : globalThis);
