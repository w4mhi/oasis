// Roster search matching — what the operator types vs what the row shows.
// UMD: browser (window.satsearch) and node (module.exports) for tests.
//
// THE RULE: ANYTHING THE ROW RENDERS MUST BE SEARCHABLE.
//
// The roster row is two lines (renderRoster in satellites.html):
//
//     SAUDISAT 1C          <- line 1, bareName(s.name)
//     SO-50 [LEO]          <- line 2, designator + orbit class
//
// and the search used to read `s.name` alone. So line 2 was on screen and
// unmatchable: an operator who knows the bird as SO-50 — which is what the
// AMSAT status page, every net and every QSL card calls it — typed the exact
// token the row was showing them and got "No satellites match the filters."
// A UI that displays an identifier and then denies it is worse than one that
// never showed it, because it teaches the operator the bird is not there.
//
// Hence the fields below are not a wish-list, they are a derivation: they are
// the row, plus the catalogue number.
//
// NORAD is the one addition beyond what the row draws. It is not on the row,
// but CelesTrak, the AMSAT list and every pass predictor quote it, so it is
// carried in from the outside world on paper and typed in verbatim — 25544
// should find the ISS. It is on every record already (`s.norad`).
//
// WHAT IS NOT SEARCHABLE, AND WHY. SatNOGS also publishes an alias blob
// (`names`: "AO-73, FUNcube-1"), but build-roster.py parses out the single
// OSCAR/RS designator and discards the rest (satnogs.py amateur_designator),
// so "FUNcube-1" finds nothing. Fixing that is a roster SCHEMA change plus a
// rebuild, not a search change. The saving grace is that the limit is
// invisible: the designator is searchable exactly when it is rendered, so
// "if you can see it, you can type it" holds with no exceptions.
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.satsearch = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // The roster stores the orbit class INSIDE the name ("SAUDISAT 1C [LEO]") as
  // well as in its own `orbit` field — a roster built before that field existed
  // still carries it only in the name. Strip it end-anchored, so a bracket
  // mid-name is left alone.
  //
  // This is the ONE implementation: satellites.html's bareSatName delegates
  // here rather than keeping its own copy of the regex, because two copies of a
  // name rule drift and the drift shows up as a bird you cannot find.
  function bareName(name) {
    const s = String(name == null ? '' : name);
    const m = s.match(/^(.*?)\s*\[[^\]]*\]\s*$/);
    return m ? m[1] : s;
  }

  // Fold case and drop every separator, so the hyphen an operator may or may
  // not type stops mattering: `ao7`, `AO 7` and `ao-7` all reach `AO-7`.
  // Without this the fix only works for people who type the designator exactly
  // the way we happen to store it, which is not a thing anyone can be expected
  // to guess.
  function norm(s) {
    return String(s == null ? '' : s).toLowerCase().replace(/[^a-z0-9]+/g, '');
  }

  // The fields a query is tested against, in row order.
  function fields(sat) {
    const s = sat || {};
    return [bareName(s.name), s.designator, s.orbit, s.norad]
      .filter(function (v) { return v !== null && v !== undefined && v !== ''; })
      .map(String);
  }

  // Match PER FIELD, never against the fields joined into one string. Joining
  // lets a query straddle a boundary and match text that appears nowhere on the
  // row — "50leo" would hit SO-50 [LEO] — which is how a search starts
  // returning results the operator cannot account for.
  function matches(sat, query) {
    const q = norm(query);
    if (!q) return true;                 // empty query filters nothing
    return fields(sat).some(function (f) { return norm(f).indexOf(q) >= 0; });
  }

  return { matches, fields, bareName, norm };
});
