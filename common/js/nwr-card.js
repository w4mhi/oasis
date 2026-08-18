// nwr-card.js
// -----------------------------------------------------------------------------
// The Weather Radio card's display logic, shared by index.html and the OASIS
// Dashboard kiosk (oasis-dashboard/dashboard.html).
//
// It lives here for the same reason common/js/service-registry.js does: the two
// pages each keep their own `check` for every service, and a service whose two
// copies drift reports different health on the same box. The nwr check WAS a
// byte-identical copy in both files, and both copies read `d.capture` — a key
// that stopped existing when the capture left Flask for the oasis-nwr daemon,
// so both dashboards painted Weather Radio red at the same moment. One module,
// called identically from both pages, is the fix that stays fixed.
//
// nwrCardState() is pure: it takes the /api/nwr/status payload and returns what
// to paint. No DOM, no fetch — so tests/js/nwr-card.test.js can pin every
// display state without a browser.
(function (root) {
  'use strict';

  // The band the daemon sweeps (services/nwr/common/scan.py BAND_LOW/HIGH).
  // Written short on purpose: this lands in .feed-meter-label, which shares one
  // row with the sub-line inside a card that is 120 px at its narrowest, and
  // neither element can shrink. The full "162.400-162.550 MHz" beside
  // "choosing a channel" is several times that width.
  var SWEEP_TEXT = 'sweeping 162.4-162.55';

  // dBm window for the signal meter. rtl_power's numbers are relative, so this
  // is a readable scale, not a calibration: the floor is "nothing there" and the
  // top is "as strong as a local transmitter gets". WEAK_DBM (-50, the daemon's
  // own weak threshold) lands at 40% — comfortably inside the bar, so weak and
  // healthy look different at a glance.
  var METER_FLOOR_DBM = -70;
  var METER_TOP_DBM = -20;

  function _mhz(hz) { return (hz / 1e6).toFixed(3); }

  // "WX7 162.550" — the label and the frequency, because an operator reaching
  // for a handheld needs the number, not just the channel name.
  function _channelText(w) {
    var parts = [];
    if (w.channel) { parts.push(w.channel); }
    if (w.channel_hz) { parts.push(_mhz(w.channel_hz)); }
    return parts.length ? parts.join(' ') : 'no channel';
  }

  // The best dBm the LAST completed sweep measured, or null when no sweep has
  // run (a pinned channel skips the sweep entirely — see choose_channel()).
  function _bestDbm(w) {
    var s = w.scan;
    if (!s) { return null; }
    var v = Number(s.best_dbm);
    return (s.best_dbm === null || s.best_dbm === undefined || isNaN(v)) ? null : v;
  }

  // The meter is a SIGNAL meter, not a sweep-progress bar: the daemon reports
  // no progress through a sweep, and a bar that animates against no measurement
  // is exactly the stand-in this repo keeps getting burned by. While a sweep is
  // running the bar is therefore empty with a "sweeping" label, and it fills
  // when a real measurement lands.
  function _signalMeter(w, cls) {
    var v = _bestDbm(w);
    if (v === null) { return null; }
    var pct = ((v - METER_FLOOR_DBM) / (METER_TOP_DBM - METER_FLOOR_DBM)) * 100;
    return { pct: Math.round(Math.max(0, Math.min(100, pct))),
             cls: cls, label: v.toFixed(1) + ' dBm' };
  }

  // What the card should paint, from GET /api/nwr/status.
  //
  //   status  the whole payload: {ok, preconditions, watch, config, channels}.
  //           `watch` is the daemon's own account of itself, with
  //           watch.reachable false when the daemon could not be asked at all.
  //   alert   the event code of an active, matched watch-list alert, or null.
  //           A SECOND argument because it comes from /api/nwr/alerts on its
  //           own cadence — folding it into the status payload would mean
  //           inventing a field the API does not have.
  //
  // Returns {state, badge, sub, meter, alert}:
  //   state   'up' | 'warn' | 'down' — the HEALTH, which is what the service
  //           tally counts. An active alert does NOT make this 'down': a
  //           tornado warning is the watch working, not the watch failing.
  //   alert   paint the card red regardless of `state` (CSS .svc-card.alert).
  //   meter   null, or {pct, cls, label} for the shared feed-meter markup.
  function nwrCardState(status, alert) {
    var d = status || {};
    var p = d.preconditions || {};
    var w = d.watch || {};
    var cs = { state: 'down', badge: 'DOWN', sub: '', meter: null, alert: false };

    if (d.ok !== true) {
      cs.sub = 'status unavailable';
    } else if ((p.missing_deps || []).length) {
      cs.badge = 'DEPS';
      cs.sub = p.missing_deps.join(', ');
    } else if (!p.assigned) {
      // Never green with nothing to listen with. Amber, not red: nothing is
      // broken, there is just an action to take, and it is one click away.
      cs.state = 'warn';
      cs.badge = 'NO SDR';
      cs.sub = 'assign a dongle in Setup';
    } else if (!w.reachable) {
      // The channel in `config` is what the watch WOULD use, not what it is
      // hearing. Showing it here would read as a running watch; say the real
      // thing instead, and carry the daemon's own reason when it gave one.
      cs.badge = 'WATCH DOWN';
      cs.sub = w.detail || 'the watch is not running';
    } else if (w.phase === 'scanning') {
      cs.state = 'up';
      cs.badge = 'SCANNING';
      cs.sub = 'choosing a channel';
      cs.meter = { pct: 0, cls: '', label: SWEEP_TEXT };
    } else if (w.phase === 'retuning') {
      // The gap an accepted POST /api/nwr/config costs: the daemon stops the
      // capture and starts one on the newly chosen channel (daemon.py
      // supervise()). Green, and ahead of scan_weak and listening, for the same
      // reason a rescan is: this is the watch doing what the operator asked,
      // and a card that read it as a decode failure would report the click as
      // damage. The state is one supervisor tick long -- retune_pending marks
      // its whole length, and phase is what says which part we are in, so a
      // retune whose capture will not start reads RETRYING here rather than
      // sitting on this word forever.
      cs.state = 'up';
      cs.badge = 'RETUNING';
      cs.sub = 'changing channel';
    } else if (w.scan_weak) {
      // Ahead of `listening` on purpose: a weak channel still starts (the
      // daemon refuses nothing), so this is the ONLY place the operator finds
      // out that the antenna is the problem rather than the band.
      cs.state = 'warn';
      cs.badge = 'WEAK';
      var dbm = _bestDbm(w);
      cs.sub = _channelText(w) + (dbm === null ? '' : ' · ' + dbm.toFixed(1) + ' dBm');
      cs.meter = _signalMeter(w, 'silent');
    } else if (w.listening) {
      cs.state = 'up';
      cs.badge = 'LISTENING';
      var heard = w.last_decode && w.last_decode.station;
      cs.sub = _channelText(w) + (heard ? ' · ' + heard : '');
      cs.meter = _signalMeter(w, 'flowing');
    } else if (!p.dongle_present) {
      // After the watch states, not before: a listening daemon holds the tuner,
      // and a presence probe that loses a race with it must not overrule a
      // watch that is demonstrably working.
      cs.badge = 'NO SDR';
      cs.sub = 'no RTL-SDR detected';
    } else if (p.busy) {
      cs.state = 'warn';
      cs.badge = 'BUSY';
      cs.sub = 'dongle held by ' + (p.holder || 'another service');
    } else if (w.phase === 'retrying') {
      cs.state = 'warn';
      cs.badge = 'RETRYING';
      cs.sub = w.retry_in_s ? 'next attempt in ' + w.retry_in_s + 's'
                            : (w.last_error || 'restarting the watch');
    } else {
      cs.state = 'warn';
      cs.badge = (w.phase || 'idle').toUpperCase();
      cs.sub = w.last_error || 'not listening';
    }

    // Last, and over any state above: the event code is the one thing worth
    // reading from across a room. The sub-line keeps the health detail, so the
    // card still says WHY when the alert is stale and the watch has since died.
    if (alert) {
      cs.alert = true;
      cs.badge = alert;
    }
    return cs;
  }

  // The shape checkService() on both pages expects. Split from nwrCardState so
  // the card can be red for an alert while the service tally still counts the
  // watch as up — two different questions with two different answers.
  function nwrHealth(cs) {
    return { up: cs.state !== 'down', warn: cs.state === 'warn',
             badge: cs.badge, sub: cs.sub };
  }

  // The active, matched, plottable alert's event code from GET /api/nwr/alerts,
  // or null. `type === null` is an informational SAME message (a weekly test),
  // which must never light the card.
  function nwrActiveEvent(d) {
    if (!d || !d.ok || !(d.active || []).length) { return null; }
    var active = new Set(d.active);
    var hit = (d.alerts || []).filter(function (a) {
      return active.has(a.id) && a.matched && a.type !== null;
    })[0];
    return hit ? hit.event : null;
  }

  // One fetch, shared, so the two pages cannot disagree about what counts as an
  // active alert. Every path consumes the body — an unread response body pins a
  // 2 MiB /dev/shm pipe on the Pi until the collector gets to it.
  async function nwrPollAlertEvent() {
    try {
      var r = await fetch('/api/nwr/alerts?limit=50', { cache: 'no-store' });
      var d = await r.json();
      return nwrActiveEvent(d);
    } catch (e) { return null; }
  }

  // Paint the parts of the card that checkService() knows nothing about: the
  // alert wash and the signal meter. A no-op on the kiosk, which has no service
  // cards at all — its Weather Radio surface is the #svcpill WX badge.
  //
  // classList, never className: checkService() rewrites the card's state class
  // on every poll, and an `alert` class assigned wholesale would be wiped by
  // it (or would wipe it). Both survive because each only touches its own.
  function nwrRenderCard(cs) {
    if (typeof document === 'undefined') { return; }
    var card = document.getElementById('card-nwr');
    if (!card) { return; }
    card.classList.toggle('alert', !!cs.alert);
    var meter = document.getElementById('nwr-meter');
    if (!meter) { return; }
    if (!cs.meter) { meter.hidden = true; return; }
    meter.hidden = false;
    meter.className = 'feed-meter' + (cs.meter.cls ? ' ' + cs.meter.cls : '');
    var fill = document.getElementById('nwr-meter-fill');
    var label = document.getElementById('nwr-meter-label');
    if (fill) { fill.style.width = cs.meter.pct + '%'; }
    if (label) { label.textContent = cs.meter.label; }
  }

  root.nwrCardState = nwrCardState;
  root.nwrHealth = nwrHealth;
  root.nwrActiveEvent = nwrActiveEvent;
  root.nwrPollAlertEvent = nwrPollAlertEvent;
  root.nwrRenderCard = nwrRenderCard;
})(typeof window !== 'undefined' ? window : this);
