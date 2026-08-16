/*
 * freshness.js — the SINGLE source of truth for how data staleness is rendered.
 *
 * Consumed by index.html (header pill), oasis-dashboard/dashboard.html (kiosk
 * chip), and the Diagnostics Data Updates section. No surface computes
 * staleness for itself: the kiosk and dashboard health pills once counted
 * different service sets and disagreed on screen, and this module exists so
 * that cannot happen again.
 *
 * Classic <script> in the pages; requireable by node --test.
 */
(function (root, factory) {
  var api = factory();
  root.OasisFreshness = api;
  if (typeof module === 'object' && module.exports) { module.exports = api; }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // Worst-first. unconfigured sits BELOW fresh deliberately: a source switched
  // off is not a problem to escalate, and must never make an otherwise-current
  // station look broken.
  var STATE_ORDER = ['missing', 'deferred', 'stale', 'fresh', 'unconfigured'];

  var CLS = {
    fresh: 'fx-ok',
    stale: 'fx-warn',
    deferred: 'fx-warn',
    missing: 'fx-bad',
    unconfigured: 'fx-off'
  };

  // Written for a human deciding whether to act, and IDENTICAL to the
  // Diagnostics badges (_UPDATE_BADGE in common/diagnostics.py) — two
  // vocabularies for one fact is how a product ends up saying "DATA STALE" on
  // the kiosk and "STALE" on Diagnostics for the same condition.
  //
  // Deliberately NOT "STALLED": stalled means the updater has stopped, which
  // sends the operator hunting a fault that does not exist. The data is old;
  // the mechanism is fine.
  //
  // Deliberately NOT "TAP TO UPDATE" for deferred: a kiosk tap runs an
  // ORDINARY pass, which by design will not fetch a held-back large source, so
  // that label would promise something the button does not do.
  //
  // Kept to 7-8 characters each — not for space, for STABILITY. The kiosk pill
  // sits between the stats bar and IMPERIAL; a label swinging from 3 to 13
  // characters reflows that row every time state changes.
  // tests/test_freshness_labels.py asserts these stay in step with Python.
  var LABEL = {
    fresh: 'DATA OK',
    stale: 'OLD DATA',
    deferred: 'ON HOLD',
    missing: 'NO DATA',
    unconfigured: 'OFF'
  };

  // The plain-language thing the operator must actually do. A UI that only says
  // "stale" leaves them guessing whether to plug in a cable, find a token, or
  // simply wait.
  var ACTION = {
    fresh: null,
    stale: null,
    deferred: 'Update now',
    missing: null,
    unconfigured: 'Add token'
  };

  var REASON = {
    fresh: 'Up to date.',
    stale: 'Needs an internet connection. Downloads by itself when online.',
    deferred: 'Held back because this connection looks metered.',
    missing: 'Never downloaded. Needs an internet connection.',
    unconfigured: 'Switched off: no API token set.'
  };

  function summarize(sources) {
    var list = sources || [];
    var counts = {
      fresh: 0, stale: 0, deferred: 0, missing: 0, unconfigured: 0
    };
    for (var i = 0; i < list.length; i++) {
      if (counts[list[i].state] !== undefined) { counts[list[i].state]++; }
    }
    var worst = 'fresh';
    for (var j = 0; j < STATE_ORDER.length; j++) {
      var s = STATE_ORDER[j];
      if (s === 'fresh') { break; }
      if (counts[s] > 0) { worst = s; break; }
    }
    return {
      worst: worst,
      counts: counts,
      label: LABEL[worst],
      cls: CLS[worst]
    };
  }

  function fmtAge(days) {
    if (days === null || days === undefined) { return 'never'; }
    if (days < 1) { return Math.round(days * 24) + 'h'; }
    if (days < 400) { return Math.round(days) + 'd'; }
    return Math.round(days / 365) + 'y';
  }

  function rowText(source) {
    var st = source.state;
    return {
      age: fmtAge(source.age_days),
      state: st,
      cls: CLS[st],
      action: ACTION[st] === undefined ? null : ACTION[st],
      reason: REASON[st] || ''
    };
  }

  return {
    summarize: summarize,
    rowText: rowText,
    fmtAge: fmtAge,
    STATE_ORDER: STATE_ORDER
  };
}));
