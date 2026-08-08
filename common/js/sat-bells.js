// sat-bells.js
// -----------------------------------------------------------------------------
// Shared decision for reconciling this browser's pass-alert bells with the
// SHARED server roster, used by the Satellites page (and readable by the kiosk,
// which consumes bells but never writes them).
//
// Background: the bell used to live only in localStorage, so it never left the
// browser that armed it — you armed a bell on a laptop and the shack kiosk, which
// only ever sees GET /api/satellites, stayed silent through the pass. The bell is
// a property of the BIRD, so it now lives in the roster beside `selected`.
// (Muting is NOT here: that is per-DEVICE, so the kiosk can be quiet overnight
// while a laptop still chimes.)
//
// The subtlety this function exists for: reconciliation must NOT be a permanent
// union, the way the SELECTION reconcile is. A union can never carry a DISARM
// across devices — turn a noisy bird off on the laptop, and the next device to
// load re-arms it from its own stale copy and pushes it back up, so a bell could
// never be switched off for good. Instead local bells migrate up exactly ONCE (so
// nothing armed before the change is lost), after which the server simply wins.
//
// Pure, because the failure mode is silent: get this wrong and the operator's
// alerts vanish, or a disarmed bird starts chiming again, with nothing on screen
// to say why.
(function (root) {
  'use strict';

  // (localArmed, rosterArmed, migrated) → what to do this load.
  //   localArmed  — norads armed in THIS browser's localStorage
  //   rosterArmed — norads the server reports armed (roster `bell` flag)
  //   migrated    — has this browser already handed its bells over?
  //
  // Returns { action, push, adopt, markMigrated }:
  //   action 'migrate' — push these norads up; adopt the response, and only mark
  //                      migrated once the write is CONFIRMED. Marking it before
  //                      confirming would strand the bells: the next load takes
  //                      the 'adopt' path and adopts a roster that never got them.
  //   action 'adopt'   — nothing local to hand over (a fresh browser, or one that
  //                      already migrated); the roster is the truth. This is the
  //                      path by which a bell armed on another device shows up.
  function oasisBellPlan(localArmed, rosterArmed, migrated) {
    var push = migrated ? [] : (localArmed || []).slice().sort(function (a, b) { return a - b; });
    if (push.length) {
      return { action: 'migrate', push: push, adopt: null, markMigrated: false };
    }
    return {
      action: 'adopt',
      push: [],
      adopt: (rosterArmed || []).slice().sort(function (a, b) { return a - b; }),
      // A browser with no local bells has nothing to lose, so its migration is
      // trivially complete — record that, or it re-checks on every single load.
      markMigrated: !migrated,
    };
  }

  root.oasisBellPlan = oasisBellPlan;
})(typeof window !== 'undefined' ? window : this);
