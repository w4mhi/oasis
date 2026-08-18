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

  // The meter reads the MARGIN, not the level. rtl_power's dBm are relative,
  // and on a live station the empty channels measured -5.7 dBm while the
  // transmitter measured -1.95 — so a -70..-20 dBm window painted every real
  // sweep, healthy or dead, hard against 100%. What separates the two is how
  // far the winner stands above the rest of the band: +3.75 dB with a working
  // antenna, ~0 dB with none (scan.channel_margin()).
  //
  // 0 is "no channel stands out at all". The top is 6 dB, which keeps that
  // measured +3.75 well inside the bar rather than pinned at the end, and puts
  // the daemon's 2 dB weak threshold at a third of the way along — so weak and
  // healthy are different at a glance, which is the whole job of this bar.
  var METER_FLOOR_DB = 0;
  var METER_TOP_DB = 6;

  function _mhz(hz) { return (hz / 1e6).toFixed(3); }

  // "WX7 162.550" — the label and the frequency, because an operator reaching
  // for a handheld needs the number, not just the channel name.
  function _channelText(w) {
    var parts = [];
    if (w.channel) { parts.push(w.channel); }
    if (w.channel_hz) { parts.push(_mhz(w.channel_hz)); }
    return parts.length ? parts.join(' ') : 'no channel';
  }

  // How far the winning channel stood above the rest of the band on the LAST
  // completed sweep, in dB, or null when there is no answer — no sweep has run
  // (a pinned channel skips it entirely, see choose_channel()), the sweep
  // failed, or fewer than two channels were read. Null is not zero: zero means
  // nothing stood out, null means nothing was measured.
  function _margin(w) {
    var s = w.scan;
    if (!s) { return null; }
    var v = Number(s.margin_db);
    return (s.margin_db === null || s.margin_db === undefined || isNaN(v)) ? null : v;
  }

  // Short enough for .feed-meter-label — see SWEEP_TEXT.
  function _marginText(v) { return v.toFixed(1) + ' dB margin'; }

  // The meter is a SIGNAL meter, not a sweep-progress bar: the daemon reports
  // no progress through a sweep, and a bar that animates against no measurement
  // is exactly the stand-in this repo keeps getting burned by. While a sweep is
  // running the bar is therefore empty with a "sweeping" label, and it fills
  // when a real measurement lands.
  function _signalMeter(w, cls) {
    var v = _margin(w);
    if (v === null) { return null; }
    var pct = ((v - METER_FLOOR_DB) / (METER_TOP_DB - METER_FLOOR_DB)) * 100;
    return { pct: Math.round(Math.max(0, Math.min(100, pct))),
             cls: cls, label: _marginText(v) };
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
  // Returns {state, badge, sub, meter, alert, audio}:
  //   state   'up' | 'warn' | 'down' — the HEALTH, which is what the service
  //           tally counts. An active alert does NOT make this 'down': a
  //           tornado warning is the watch working, not the watch failing.
  //   alert   paint the card red regardless of `state` (CSS .svc-card.alert).
  //   meter   null, or {pct, cls, label} for the shared feed-meter markup.
  //   audio   is there anything to HEAR right now — the daemon is capturing
  //           AND it has an encoder to hand the bytes to. Decided here, in the
  //           branch that already knows, rather than re-derived by every caller
  //           from `badge`: the alert overlay below REPLACES badge with the
  //           event code, so a badge-reading caller loses the health the moment
  //           a warning lands. GET /api/nwr/listen/stream refuses with 409/503
  //           whenever this is false (services/nwr/routes.py).
  function nwrCardState(status, alert) {
    var d = status || {};
    var p = d.preconditions || {};
    var w = d.watch || {};
    var cs = { state: 'down', badge: 'DOWN', sub: '', meter: null, alert: false,
               audio: false };

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
      // The margin, not the level: an absolute dBm here read -5.7 on a dead
      // band and -1.95 on a live one, which tells the operator nothing. "0.1 dB
      // margin" says the thing that is actually wrong — no channel stands out.
      var m = _margin(w);
      cs.sub = _channelText(w) + (m === null ? '' : ' · ' + _marginText(m));
      cs.meter = _signalMeter(w, 'silent');
      // A weak channel still starts, so there IS audio — bad audio, which is
      // the operator's to judge by ear. `!== false` and not truthiness: a
      // precondition key that stops being sent must not silently mute the
      // station, which is the exact failure this module was written for.
      cs.audio = !!w.listening && p.can_stream !== false;
    } else if (w.listening) {
      cs.state = 'up';
      cs.badge = 'LISTENING';
      cs.audio = p.can_stream !== false;
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

  // What the kiosk's WX listen pill should paint, from the same nwrCardState()
  // the card and the health tally read. Pure: the kiosk owns the <audio> and
  // the DOM, this owns the decision.
  //
  //   cs       a nwrCardState() return, or null before the first poll lands.
  //   playing  is the kiosk's <audio> element streaming right now.
  //
  // Returns {cls, title, disabled}. `cls` carries the playing state too, so the
  // pill is fully described by one string and cannot be painted half-updated.
  //
  // Two judgment calls, both deliberate:
  //
  // 1. AMBER BEATS BLUE. An alerting watch paints amber even while the operator
  //    is listening to it. A warning is the more urgent fact, and the pill has
  //    one colour to spend; "there is a warning" outranks "audio is flowing",
  //    which the inverted fill says anyway.
  //
  // 2. GREY-DISABLED MEANS "NOTHING TO HEAR", NOT ONLY "NO DONGLE". The
  //    operator's rule was no-dongle-is-grey, and it holds exactly as stated —
  //    but WATCH DOWN, DEPS, SCANNING and an unreachable status have no stream
  //    either, and /api/nwr/listen/stream answers all of them with 409 or 503.
  //    A blue button that silently does nothing is worse than an honest grey
  //    one, so every state with no audio is grey, each with its own title.
  //    Grey also outranks amber, for the same honesty: a stale alert over a
  //    dead watch must not offer audio that cannot play. Nothing is lost by
  //    that — the event code still rides the kiosk's #svcpill WX badge, and the
  //    title below still names it.
  function nwrListenPill(cs, playing) {
    var c = cs || { badge: 'DOWN', sub: 'status unavailable', audio: false };
    var name = 'Weather Radio ' + (c.badge || 'DOWN');
    if (!c.audio) {
      return { cls: 'wx-off', disabled: true,
               title: name + ' - nothing to hear' + (c.sub ? ': ' + c.sub : '') };
    }
    var cls = c.alert ? 'wx-alert' : 'wx-live';
    return { cls: playing ? cls + ' wx-play' : cls, disabled: false,
             title: name + (playing ? ' - playing, tap to stop'
                                    : ' - tap to listen') };
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

  // A paint generation. Both dashboards run every health check inside a
  // Promise.race with a 4 s timeout, and losing that race rejects the RACE --
  // it does not abort the fetch. The check's continuation still runs when the
  // slow answer finally lands, and it still paints. So on a Pi 3, where losing
  // that race is routine: fail() paints the unreachable card, then the late
  // answer repaints a live-looking -32.0 dBm meter and the alert wash
  // underneath a badge that still reads TIMEOUT. That is the exact pairing the
  // failure paint was added to prevent, reached through the other ordering.
  //
  // Each caller takes a generation before it starts and hands it back when it
  // paints; taking one invalidates every generation before it, so the failure
  // paint is final and a superseded continuation is dropped. Passing no
  // generation paints unconditionally (there is no race to lose).
  var _paintGen = 0;
  var _onPaint = null;
  function nwrOnCardPaint(fn) { _onPaint = fn; }
  function nwrPaintGeneration() {
    _paintGen += 1;
    return _paintGen;
  }

  // Paint the parts of the card that checkService() knows nothing about: the
  // alert wash and the signal meter. A no-op on the kiosk, which has no service
  // cards at all — its Weather Radio surface is the #svcpill WX badge.
  //
  // classList, never className: checkService() rewrites the card's state class
  // on every poll, and an `alert` class assigned wholesale would be wiped by
  // it (or would wipe it). Both survive because each only touches its own.
  //
  // A page with a Weather Radio surface that is NOT the card registers it with
  // nwrOnCardPaint() and gets driven from here. That indirection is not taste:
  // tests/js/nwr-card.test.js compares the two dashboards' whole `nwr` registry
  // entries byte for byte, so the kiosk cannot add a call of its own inside
  // one. Hanging off nwrRenderCard also means the extra surface is fed by BOTH
  // paths — check() and fail() — and is dropped by the same generation guard,
  // so a lost race cannot leave it painted live beside a DOWN badge.
  function nwrRenderCard(cs, gen) {
    if (gen !== undefined && gen !== _paintGen) { return; }
    if (_onPaint) { _onPaint(cs); }
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
  root.nwrListenPill = nwrListenPill;
  root.nwrOnCardPaint = nwrOnCardPaint;
  root.nwrHealth = nwrHealth;
  root.nwrActiveEvent = nwrActiveEvent;
  root.nwrPollAlertEvent = nwrPollAlertEvent;
  root.nwrPaintGeneration = nwrPaintGeneration;
  root.nwrRenderCard = nwrRenderCard;
})(typeof window !== 'undefined' ? window : this);
