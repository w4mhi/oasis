/*
 * diagnostics.js — shared front-end helpers for the Diagnostic page.
 *
 * statusClass   check/capability status ("ok"|"warn"|"fail"|anything else)
 *               -> the CSS state suffix used by .cap-tile / .check-row /
 *               .check-badge (".ok" / ".warn" / ".fail" / ".unknown").
 * statusIcon    same status -> the glyph shown in the status column.
 * capUpDown     capabilities[] (from GET /api/diagnostics) -> {up, down}.
 *               A capability counts as DOWN only when its rollup status is
 *               "fail" (nothing usable); "warn" still counts as UP because
 *               the capability is degraded but functioning.
 *
 * Classic <script> served by Flask (loaded by server/system/diagnostic.html);
 * also requireable by node --test (no root package.json -> .js is CommonJS).
 */
(function (root, factory) {
  var api = factory(root);
  root.OasisDiag = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
  'use strict';

  function statusClass(status) {
    if (status === 'ok')   return 'ok';
    if (status === 'warn') return 'warn';
    if (status === 'fail') return 'fail';
    return 'unknown';
  }

  function statusIcon(status) {
    if (status === 'ok')   return '✓';   // ✓
    if (status === 'warn') return '⚠';   // ⚠
    if (status === 'fail') return '✗';   // ✗
    return '…';                          // …
  }

  function capUpDown(capabilities) {
    var up = 0, down = 0;
    (capabilities || []).forEach(function (c) {
      if (c && c.status === 'fail') down++; else up++;
    });
    return { up: up, down: down };
  }

  return { statusClass: statusClass, statusIcon: statusIcon, capUpDown: capUpDown };
});
