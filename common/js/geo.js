/*
 * geo.js — shared geographic helpers for OASIS dashboards.
 *
 * Pure Maidenhead grid + great-circle distance/bearing, plus a stateful
 * distFromHome() that formats against the operator's saved home coordinates
 * (set once from /station.json via setHomeCoords) and the current unit
 * preference (reads globalThis.OasisUnits from units.js; falls back to
 * imperial). Classic <script> in both dashboards — load AFTER units.js —
 * and requireable by node --test.
 */
(function (root, factory) {
  var api = factory(root);
  root.OasisGeo = api;
  root.gridSquare = api.gridSquare;
  root.distFromHome = api.distFromHome;
  root.setHomeCoords = api.setHomeCoords;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
  'use strict';
  var _homeLat = null, _homeLon = null;

  function setHomeCoords(lat, lon) {
    _homeLat = (lat == null) ? null : parseFloat(lat);
    _homeLon = (lon == null) ? null : parseFloat(lon);
  }

  function gridSquare(lat, lon) {
    if (lat == null || lon == null) return '';
    var la = parseFloat(lat), lo = parseFloat(lon);
    if (isNaN(la) || isNaN(lo)) return '';
    lo += 180; la += 90;
    var c1 = String.fromCharCode(65 + Math.floor(lo / 20));
    var c2 = String.fromCharCode(65 + Math.floor(la / 10));
    lo %= 20; la %= 10;
    var n1 = Math.floor(lo / 2);
    var n2 = Math.floor(la);
    lo %= 2; la %= 1;
    var c3 = String.fromCharCode(97 + Math.floor(lo / (2 / 24)));
    var c4 = String.fromCharCode(97 + Math.floor(la / (1 / 24)));
    return c1 + c2 + n1 + n2 + c3 + c4;
  }

  // Great-circle distance in statute miles (mean earth radius 3958.8 mi).
  function haversineMi(lat1, lon1, lat2, lon2) {
    var p1 = lat1 * Math.PI / 180, p2 = lat2 * Math.PI / 180;
    var dphi = (lat2 - lat1) * Math.PI / 180;
    var dl = (lon2 - lon1) * Math.PI / 180;
    var a = Math.sin(dphi / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
    return 2 * 3958.8 * Math.asin(Math.min(1, Math.sqrt(a)));
  }

  // 8-point compass cardinal from point 1 to point 2.
  function bearingCardinal(lat1, lon1, lat2, lon2) {
    var p1 = lat1 * Math.PI / 180, p2 = lat2 * Math.PI / 180;
    var dl = (lon2 - lon1) * Math.PI / 180;
    var yb = Math.sin(dl) * Math.cos(p2);
    var xb = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
    var bear = (Math.atan2(yb, xb) * 180 / Math.PI + 360) % 360;
    return ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'][Math.round(bear / 45) % 8];
  }

  function distFromHome(lat, lon) {
    if (_homeLat == null || lat == null || lon == null) return '';
    var tLat = parseFloat(lat), tLon = parseFloat(lon);
    var dMi = haversineMi(_homeLat, _homeLon, tLat, tLon);
    if (dMi < 0.1) return 'here';
    var card = bearingCardinal(_homeLat, _homeLon, tLat, tLon);
    var imperial = !root.OasisUnits || root.OasisUnits.isImperial();
    if (imperial) return dMi < 100 ? (dMi.toFixed(1) + 'mi ' + card) : (Math.round(dMi) + 'mi ' + card);
    var dKm = dMi * 1.60934;
    return dKm < 100 ? (dKm.toFixed(1) + 'km ' + card) : (Math.round(dKm) + 'km ' + card);
  }

  return { gridSquare: gridSquare, haversineMi: haversineMi, bearingCardinal: bearingCardinal,
           setHomeCoords: setHomeCoords, distFromHome: distFromHome };
});
