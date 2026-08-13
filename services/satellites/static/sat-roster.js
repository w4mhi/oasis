// Pure roster predicates for the satellites page. UMD: usable in the browser
// (window.satroster) and under node (module.exports) for tests. No dependencies.
//
// WHY THIS FILTER IS CLIENT-SIDE, AND WHY IT WILL STAY THAT WAY
// -------------------------------------------------------------
// Roughly 130 birds are in the roster and roughly 100 of them carry a downlink
// the chain can demodulate. The other 30 are real spacecraft transmitting real
// signals — BPSK telemetry, LRPT imagery, a digital sounder — that this station
// cannot turn into audio. They are noise in a list an operator scrolls during a
// pass, so the page hides them by default.
//
// It hides them as a VIEW, never by rebuilding the roster: build-roster.py needs
// the internet, and an operator has to be able to change their mind at 2 a.m.
// with no network. A view filter costs one predicate and is reversible with one
// tap; a narrower roster file is a decision you cannot take back in the field.
//
// UNKNOWN IS NOT NO
// -----------------
// `supported` is stamped onto each downlink by listen.mode_support when the
// roster is built. A roster built before that decoration existed has downlinks
// with no such key — and on that box, reading absent-as-unsupported would hide
// every bird at once, on precisely the station with no connectivity to rebuild
// its way out. So an unclassified downlink counts as listenable. The filter's
// job is to remove birds the roster positively says are silent to us, not to
// remove everything it has no opinion about.
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.satroster = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // One downlink's verdict. Three states collapsed onto two deliberately:
  //   supported === true   -> yes
  //   supported === false  -> no
  //   anything else        -> unknown, and unknown is treated as yes
  // The strict `=== true` matters: a hand-edited roster carrying the STRING
  // "false" would be truthy, and a loose check would re-admit every bird while
  // looking like it was working.
  function downlinkListenable(d) {
    if (!d || typeof d !== 'object') return false;
    if (d.supported === true) return true;
    if (d.supported === false) return false;
    return !('supported' in d);
  }

  // True when any downlink is something we could demodulate. A bird with no
  // downlinks is false rather than unknown: that is the roster stating there is
  // nothing to hear, not the roster declining to say.
  function isListenable(sat) {
    if (!sat || typeof sat !== 'object') return false;
    const dls = sat.downlinks;
    if (!Array.isArray(dls)) return false;
    return dls.some(downlinkListenable);
  }

  // How many BIRDS survive the filter — what the chip's count means. Counting
  // downlinks instead would read as "100 things to listen to" when it is really
  // 100 spacecraft carrying rather more than 100 transmitters between them.
  function listenableCount(roster) {
    if (!Array.isArray(roster)) return 0;
    let n = 0;
    for (const s of roster) if (isListenable(s)) n++;
    return n;
  }

  return { downlinkListenable, isListenable, listenableCount };
});
