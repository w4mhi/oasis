// sat-bells.js
// -----------------------------------------------------------------------------
// Pass-alert bells: which birds wake THIS screen. One definition of the state,
// the glyph and the tap, shared by the Satellites page and the kiosk dashboard.
//
// ── The model ────────────────────────────────────────────────────────────────
//   What the station WATCHES is global — `selected` lives in the server roster,
//   so curating birds on a laptop updates the shack.
//   What wakes THIS SCREEN is local — the bell lives in this browser.
//
// The bell was briefly a roster field, for a good reason that has since expired:
// the kiosk had no way to arm one, so a bell set on a laptop was the only way the
// shack could ever chime. Now the kiosk arms its own, and per-device is what the
// question actually means — the bed-drawer panel should wake for the ISS and
// nothing else, while the bench laptop watches all thirteen. Neither speaks for
// the other, which a shared flag could never express.
//
// The accepted cost: a brand-new browser, or a reflashed kiosk, starts with
// nothing armed. There is no server default to fall back on by design. The glyph
// on every bird is what shows it — see bellHTML.
//
// Muting is a separate, also-local fact: `oasis_sat_muted` silences THIS screen
// regardless of what is armed. Armed-but-muted is a real state and the glyph
// says so (bright, crossed) rather than pretending the bird is disarmed.
(function (root) {
  'use strict';

  var KEY = 'oasis_sat_bells';
  // Distinct from the OLD 'oasis_sat_bells_migrated' flag, which recorded the
  // migration in the opposite direction (local -> roster). A browser that still
  // carries the old key must adopt again under the new rules, so reusing it
  // would skip exactly the devices that need this.
  var ADOPTED = 'oasis_sat_bells_adopted';
  var MUTE_KEY = 'oasis_sat_muted';

  // Material bell / bell-off, inline because the bell emoji renders blank on Pi
  // Chromium. 1.35em + currentColor so each surface's own font-size and colour
  // still apply — the kiosk row and the page roster are not the same size.
  var BELL_ON = '<svg viewBox="0 0 24 24" width="1.35em" height="1.35em" fill="currentColor" aria-hidden="true" style="vertical-align:-.15em"><path d="M12 2a2 2 0 0 0-2 2v.29C7.12 5.14 5 7.82 5 11v5l-2 2v1h18v-1l-2-2v-5c0-3.18-2.12-5.86-5-6.71V4a2 2 0 0 0-2-2Zm0 20a2 2 0 0 0 2-2h-4a2 2 0 0 0 2 2Z"/></svg>';
  var BELL_OFF = '<svg viewBox="0 0 24 24" width="1.35em" height="1.35em" fill="currentColor" aria-hidden="true" style="vertical-align:-.15em"><path d="M20 18.69 7.84 6.14 5.27 3.49 4 4.76l2.8 2.8v.01c-.52.99-.8 2.16-.8 3.42v5l-2 2v1h13.73l2 2L21 19.72l-1-1.03zM12 22c1.11 0 2-.89 2-2h-4c0 1.11.89 2 2 2zm6-7.32V11c0-3.08-1.64-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68c-.15.03-.29.08-.42.13l7.92 7.87z"/></svg>';

  var _ls = null;
  try { _ls = root.localStorage || null; } catch (e) { _ls = null; }   // private mode

  function _read(key, fallback) {
    if (!_ls) return fallback;
    try {
      var raw = _ls.getItem(key);
      return raw == null ? fallback : JSON.parse(raw);
    } catch (e) { return fallback; }
  }
  function _write(key, value) {
    if (!_ls) return;
    try { _ls.setItem(key, JSON.stringify(value)); } catch (e) { /* quota / private */ }
  }

  var _bells = _read(KEY, {}) || {};
  var _listeners = [];

  function _emit() {
    for (var i = 0; i < _listeners.length; i++) {
      try { _listeners[i](); } catch (e) { /* a bad listener must not stop the rest */ }
    }
  }

  /** Register a repaint callback. Both surfaces re-render their own way. */
  function onChange(fn) { if (typeof fn === 'function') _listeners.push(fn); }

  function muted() {
    if (!_ls) return false;
    try { return _ls.getItem(MUTE_KEY) === '1'; } catch (e) { return false; }
  }

  function armed(norad) { return !!_bells[norad]; }

  function armedNorads() {
    return Object.keys(_bells)
      .filter(function (k) { return _bells[k]; })
      .map(Number)
      .filter(function (n) { return !isNaN(n); })
      .sort(function (a, b) { return a - b; });
  }

  function count() { return armedNorads().length; }

  function setArmed(norad, on) {
    // Disarming DELETES rather than storing false. The map is "what is armed",
    // so a false would be an entry meaning nothing, and armedNorads would have to
    // filter it out forever.
    if (on) _bells[norad] = true; else delete _bells[norad];
    _write(KEY, _bells);
    _emit();
    return on;
  }

  function toggle(norad) { return setArmed(norad, !armed(norad)); }

  /** Arm or disarm a whole set in one write — the ARM ALL / ARM NONE control. */
  function setMany(norads, on) {
    (norads || []).forEach(function (n) {
      if (on) _bells[n] = true; else delete _bells[n];
    });
    _write(KEY, _bells);
    _emit();
    return count();
  }

  /** Drop bells for birds no longer monitored.
   *
   * An orphan bell is inert — every alert path walks the monitored set — so it
   * would sit unseen and then fire months later when the bird was re-selected,
   * a ghost with no visible cause.
   */
  function prune(monitoredNorads) {
    var keep = {};
    (monitoredNorads || []).forEach(function (n) { keep[n] = true; });
    var changed = false;
    Object.keys(_bells).forEach(function (k) {
      if (!keep[k]) { delete _bells[k]; changed = true; }
    });
    if (changed) { _write(KEY, _bells); _emit(); }
    return changed;
  }

  // ── One-time adoption from the old roster field ────────────────────────────
  // Pure, so the decision is testable: the failure mode is silent either way —
  // adopt twice and a bird the operator just disarmed comes back; never adopt and
  // everything armed before the change is lost.
  function adoptPlan(localArmed, rosterArmed, adopted) {
    if (adopted) return { adopt: null, markAdopted: false };
    return {
      // A browser that already has local bells keeps them: it armed them under
      // the new rules, and the roster is the stale copy.
      adopt: (localArmed || []).length ? [] : (rosterArmed || []).slice()
        .sort(function (a, b) { return a - b; }),
      markAdopted: true,
    };
  }

  /** Run adoption once against the roster's legacy `bell` values. */
  function adoptOnce(rosterArmed) {
    var already = false;
    if (_ls) { try { already = _ls.getItem(ADOPTED) === '1'; } catch (e) { /* private */ } }
    var plan = adoptPlan(armedNorads(), rosterArmed, already);
    if (plan.adopt && plan.adopt.length) setMany(plan.adopt, true);
    if (plan.markAdopted && _ls) {
      try { _ls.setItem(ADOPTED, '1'); } catch (e) { /* private */ }
    }
    return plan;
  }

  // ── The control ────────────────────────────────────────────────────────────
  /** The bell button for one bird.
   *
   * `o.compact` shrinks the hit padding for the page's dense roster rows; the
   * kiosk leaves it off, because its pass row is a WHOLE-ROW tap target that
   * opens the detail sheet and a bell inside it needs a target big enough to hit
   * on a 7-inch panel without catching the row instead.
   */
  function bellHTML(norad, o) {
    o = o || {};
    var on = armed(norad);
    // The glyph reflects whether a chime will actually SOUND. An armed bird shows
    // the ringing bell; while this screen is muted even armed birds show the
    // crossed bell, kept bright by .on so it still reads as armed. A disarmed
    // bird is a dim crossed bell. Three states, three appearances.
    var glyph = (on && !muted()) ? BELL_ON : BELL_OFF;
    var title = on
      ? (muted()
          ? 'Pass alerts armed, but this screen is muted. Tap to disarm.'
          : 'Pass alerts armed on this screen — VVV chime at T-10 and T-5. Tap to disarm.')
      : 'Alerts off for this bird on this screen. Tap to arm.';
    return '<button class="sat-bell' + (on ? ' on' : '') +
      (o.compact ? ' compact' : '') + '" onclick="OasisBells.tap(event,' + norad + ')"' +
      ' aria-pressed="' + (on ? 'true' : 'false') + '" title="' + title + '">' + glyph + '</button>';
  }

  /** Click handler for bellHTML. Stops propagation so a bell inside a whole-row
   *  tap target never also opens the sheet behind it. */
  function tap(ev, norad) {
    if (ev) {
      if (ev.stopPropagation) ev.stopPropagation();
      if (ev.preventDefault) ev.preventDefault();
    }
    toggle(norad);
    // The tap that arms a bell is also the gesture that unlocks the AudioContext,
    // so the chime it just armed can actually play.
    if (typeof root.oasisSatAudioUnlock === 'function') root.oasisSatAudioUnlock();
    return false;
  }

  var api = {
    KEY: KEY, ADOPTED_KEY: ADOPTED,
    BELL_ON: BELL_ON, BELL_OFF: BELL_OFF,
    armed: armed, armedNorads: armedNorads, count: count,
    setArmed: setArmed, toggle: toggle, setMany: setMany, prune: prune,
    adoptPlan: adoptPlan, adoptOnce: adoptOnce,
    bellHTML: bellHTML, tap: tap, onChange: onChange, muted: muted,
  };
  root.OasisBells = api;
  if (typeof module === 'object' && module.exports) { module.exports = api; }
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
