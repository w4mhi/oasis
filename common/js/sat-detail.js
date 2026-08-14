/*
 * sat-detail.js — the satellite detail card, shared by the Satellites page's
 * click-popup and the kiosk's row sheet.
 *
 * Both screens answer the same question about one bird — what can it do, where
 * do I tune, when is it up — and they must not disagree: one is on the wall and
 * the other is in your hand, and an operator reading both at once will believe
 * the one that is wrong. So the card is composed here, once.
 *
 * WHAT THIS DELIBERATELY DOES NOT OWN: the sky plot. It takes `plotHTML` as a
 * ready-made fragment instead of building it, because the polar projection lives
 * in the Satellites page's own static/sat-geometry.js and a common/js module may
 * not reach into it. That boundary also makes "no plot on the kiosk" simply
 * passing nothing, rather than a flag threaded through the drawing code.
 *
 * Reads `OasisSatUplink` and `OasisHorizon` off the global at CALL time, exactly
 * as the pages do (classic <script> tags in load order), so loading it here is
 * enough — and node --test can set the globals first.
 */
(function (root, factory) {
  var api = factory(root);
  root.OasisSatDetail = api;
  // Bare globals so the pages' inline call sites read naturally.
  root.satDetailHTML = api.satDetailHTML;
  root.dlLabel = api.dlLabel;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
  'use strict';

  /* Satellite names come from CelesTrak and SatNOGS, and modes and descriptions
     from SatNOGS free text. None of it is ours, so none of it goes into markup
     unescaped — even though every value seen so far has been tame. */
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /* The modes on one frequency, alphabetically: "FM/FSK/SSTV". The server
     already sorts `modes`; the join is the only presentation choice here. */
  function dlLabel(d) { return (d.modes || [d.mode]).join('/'); }

  /* Type / FDN / FUP.
     The FUP cell distinguishes THREE states, not two:
       a frequency  — this is where you transmit
       n/a          — `one_way`: SatNOGS types every member Transmitter, its own
                      word for a beacon or image downlink that does not listen
       unknown      — it says the bird listens but never said where
     The third never occurs in today's catalogue, and is there for the day that
     stops being true: inferring "no uplink exists" from a field merely being
     absent is the not-present-looks-like-permission-denied mistake.

     The unit lives in the header rather than on every row — seven repetitions of
     "MHz" down one card is noise, and losing it entirely was a real regression
     an earlier cut of this card shipped. */
  /* ARMING, when the caller asks for it (o.onPick). The table is read-only by
     default and stays that way for any caller that passes no options.

     Measured on the 101-bird roster before this was built, because the shape of
     the data decides the shape of the control: 59% of birds have exactly ONE
     recordable downlink and 1% have none, so for six birds in ten a picker is a
     control whose only option is the one already chosen. The remaining 41% are
     where it earns its place — and 26 of those carry downlinks needing
     DIFFERENT demodulators (every one of them fm/usb, an FM channel beside a CW
     or SSB beacon), which is exactly why the choice cannot be guessed. Arming
     the wrong one there does not degrade the recording, it records the wrong
     thing. Worst case is 7 rows (ISS), 87% are 3 or fewer.

     Only SUPPORTED rows are tappable. An unsupported one stays visible and
     inert: "we cannot demodulate this yet" is information about the bird, and
     hiding the row would leave an operator wondering where a frequency went. */
  function soleRecordable(sat) {
    var sup = (sat.downlinks || []).filter(function (d) { return d.supported; });
    return sup.length === 1 ? sup[0] : null;
  }

  function freqTableHTML(sat, o) {
    o = o || {};
    var uplinkCells = (root.OasisSatUplink || {}).uplinkCells;
    var dls = (sat.downlinks || []).slice().sort(function (a, b) {
      return a.freq_mhz - b.freq_mhz;
    });
    if (!dls.length) return '<div class="pop-row"><span class="pop-val">No downlinks.</span></div>';
    var armed = o.armed || null;
    var rows = dls.map(function (d) {
      // Several uplink-bearing members stack inside the one cell rather than
      // splitting the entry across rows — the entry is one tunable thing.
      var cells = uplinkCells ? uplinkCells(d) : [];
      var up = cells.length
        ? cells.map(esc).join('<br>')
        : '<span class="sp-na">' + (d.one_way ? 'n/a' : 'unknown') + '</span>';
      var cls = [], attr = '';
      if (armed && Math.abs(armed.freq - d.freq_mhz) < 1e-6
          && (!armed.mode || armed.mode === d.mode)) cls.push('armed');
      if (o.onPick && d.supported) {
        cls.push('sp-pick');
        // A global name rather than a function: the card is built as a string
        // and both surfaces wire actions with inline onclick.
        attr = ' onclick="' + esc(o.onPick) + '(' + sat.norad + ',' +
               d.freq_mhz + ',&#39;' + esc(d.mode || '') + '&#39;)"';
      } else if (o.onPick && !d.supported) {
        cls.push('sp-inert');
      }
      return '<tr' + (cls.length ? ' class="' + cls.join(' ') + '"' : '') + attr +
             '><td>' + esc(dlLabel(d)) + '</td><td>' + esc(d.freq_mhz) +
             '</td><td>' + up + '</td></tr>';
    }).join('');
    return '<table class="sp-freq">' +
      '<thead><tr><th>Type</th><th>FDN (MHz)</th><th>FUP (MHz)</th></tr></thead>' +
      '<tbody>' + rows + '</tbody></table>';
  }

  /* The workable window: when the bird actually clears the operator's own
     skyline, as opposed to the geometric 0-degree horizon the predictor uses.
     Read off the track the caller already holds, so it costs nothing.

     Segments, not first-to-last: an azimuth-dependent mask can be crossed more
     than once in a pass (clear, behind the hill, clear again), and reporting the
     span between the first and last clear sample would claim continuous
     workability across the blocked middle. */
  function workableWindowHTML(track, hz, minEl, fmtTime) {
    var H = root.OasisHorizon;
    if (!H || !track || !track.length || !hz || !Object.keys(hz).length) return '';
    var segs = H.clearSegments(track, hz, minEl);
    if (!segs.length) return '<div class="sp-window">never clears your skyline</div>';
    if (segs.length === 1 && segs[0].from === track[0].t &&
        segs[0].to === track[track.length - 1].t) return '';   // nothing to add
    var shown = segs.slice(0, 3).map(function (s) {
      return fmtTime(s.from) + '–' + fmtTime(s.to);
    }).join(', ');
    var extra = segs.length > 3 ? ' +' + (segs.length - 3) + ' more' : '';
    return '<div class="sp-window">workable ' + esc(shown + extra) + '</div>';
  }

  function passesHTML(sat, o) {
    var H = root.OasisHorizon;
    var hz = o.horizon || {}, minEl = o.minElev;
    var rows = (o.passes || []).slice(0, o.limit || 4).map(function (p, i) {
      var active = o.activeRise && p.rise === o.activeRise;
      // The soonest pass is marked separately from the ACTIVE one, because the
      // two mean different things and only coincide most of the time: `active`
      // is the pass drawn on the map, `first` is simply the next one up. The
      // Satellites page highlights `active` — it has a map to point at — and
      // the kiosk, which has nothing to plot onto, highlights `first`. Marking
      // both here lets each surface choose without the module guessing which
      // kind of screen it is on.
      var first = i === 0;
      // Marked, never removed. A filter would delete the pass, and a slightly
      // wrong mask would then silently remove workable passes with nothing on
      // screen to say why. peak_az is guarded because a pass cached before that
      // field existed has none.
      var blocked = H && p.peak_az != null && H.isBlocked(hz, p.peak_az, p.max_el, minEl);
      var why = blocked ? ' <span class="blocked-why">· peak below skyline</span>' : '';
      var win = active ? workableWindowHTML(o.track, hz, minEl, o.fmtTime) : '';
      var peak = p.peak_az != null
        ? ' ' + esc(o.compass(p.peak_az)) + ' ' + p.peak_az.toFixed(0) + '°' : '';
      // The click handler is a GLOBAL NAME rather than a function, because the
      // card is built as a string and both pages wire actions with inline
      // onclick. A page with nothing to do on a pass simply passes none.
      var click = o.onPassClick
        ? ' onclick="' + esc(o.onPassClick) + '(' + sat.norad + ',&#39;' +
          esc(p.rise) + '&#39;,&#39;' + esc(p.set) + '&#39;)"'
        : '';
      return '<div class="pop-pass' + (first ? ' first' : '') + (active ? ' active' : '') +
        (blocked ? ' blocked' : '') + (o.onPassClick ? ' tappable' : '') + '"' + click + '>' +
        esc(o.fmtTime(p.rise)) + '–' + esc(o.fmtTime(p.set)) +
        ' · max ' + p.max_el.toFixed(0) + '°' + peak +
        ' · rise ' + p.rise_az.toFixed(0) + '°' + why + win + '</div>';
    }).join('');
    return rows || '<div class="pop-row"><span class="pop-val">No passes in 24 h.</span></div>';
  }

  /* The whole card.
       sat  — a roster record: {name, norad, labels, downlinks}
       o    — {passes, limit, horizon, minElev, track, fmtTime, compass,
               activeRise, onPassClick, plotHTML}
     `fmtTime` and `compass` are injected because the two screens format
     differently and must keep doing so: the kiosk is 24h and space-starved. */
  function satDetailHTML(sat, o) {
    o = o || {};
    var chips = (sat.labels || []).map(function (l) {
      return '<span class="chip">' + esc(l) + '</span>';
    }).join('');
    // Frequencies and passes go into a grid container, but HOW MANY COLUMNS is
    // left to CSS — the kiosk sheet is the full width of the panel and has room
    // for two, the page's popup is 420px and does not. Baking a column count
    // into the markup would force one screen to wear the other's proportions.
    // The plot stays outside the grid, full width beneath both.
    return '<div class="pop-call">' + esc(sat.name) + '</div>' +
      '<div class="pop-chips">' + chips + '</div>' +
      '<div class="sd-cols">' +
        '<div class="sp-freqs">' + freqTableHTML(sat, o) + '</div>' +
        '<div class="pop-sec"><span class="pop-lbl">Upcoming passes</span>' +
        passesHTML(sat, o) + '</div>' +
      '</div>' +
      (o.plotHTML ? '<div class="sp-plot">' + o.plotHTML + '</div>' : '');
  }

  return { satDetailHTML: satDetailHTML, freqTableHTML: freqTableHTML,
           passesHTML: passesHTML, workableWindowHTML: workableWindowHTML,
           soleRecordable: soleRecordable, dlLabel: dlLabel, esc: esc };
});
