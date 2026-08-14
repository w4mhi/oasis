// sat-listen.js
// -----------------------------------------------------------------------------
// The recorder client: everything between a Rec button and /api/satellites/listen.
// Extracted from the Satellites page so the kiosk dashboard can grow the same
// transport without a second implementation of it — one dongle, one set of rules,
// and no chance of the two surfaces disagreeing about what the radio is doing.
//
// WHAT IS HERE AND WHAT IS NOT. This owns the STATE and the ACTIONS: the poll,
// the start/stop calls, the <audio> element, the noise-filter graph, and the
// transport dispatch. It owns no layout. Every page paints its own controls from
// buttons() and statusText(), because a 7" kiosk panel and a desktop roster have
// nothing useful to say to each other about pixels.
//
// TWO TRUTHS, AND BOTH ARE NEEDED. The server knows whether a capture is running
// (/listen/status is global, so every browser agrees). Only THIS browser knows
// whether it is the one listening — _state["mode"] is "record" or "stream", never
// both, so a listener attached to a running recording leaves `streaming` false
// server-side. Hence streamNorad, which is deliberately local and deliberately
// lost on reload: a reload kills the <audio> element too, so the flag and the
// reality stay in agreement.
//
// The page supplies what only it can know — where the roster is, which pass is
// next, what is armed — through the hooks in create(). Nothing here reaches for
// a global the page happens to have.
(function (root, factory) {
  const api = factory(root);
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.OasisSatListen = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
  'use strict';

  const AUDIO_ID = 'sat-audio';
  const DENOISE_KEY = 'oasis_sat_denoise';

  function create(opts) {
    const o = opts || {};
    const onChange = o.onChange || function () {};
    const nextPass = o.nextPass || function () { return null; };
    const getArmed = o.getArmed || function () { return null; };
    const setArmed = o.setArmed || function () {};
    const findSat = o.findSat || function () { return null; };
    const doc = o.document || root.document;

    let LISTEN = { recording: false };
    let streamNorad = null, streamFreq = null, errNorad = null;
    let denoise = 0;
    try { denoise = parseFloat(root.localStorage.getItem(DENOISE_KEY)) || 0; }
    catch (e) { denoise = 0; }
    let dnNodes = null;      // {source, hp, lp} — built once, then re-aimed
    let dnFailed = false;    // no AudioContext: the readout says so instead of lying

    // ── live audio ──────────────────────────────────────────────────────────
    // One hidden <audio> plays the /listen/stream endpoint. Closing it (pause +
    // drop src) ends the HTTP request, which tears the pipeline down server-side.
    function audio() {
      let a = doc.getElementById(AUDIO_ID);
      if (!a) {
        a = doc.createElement('audio');
        a.id = AUDIO_ID; a.autoplay = true; a.style.display = 'none';
        a.addEventListener('error', () => {
          if (streamNorad != null) { errNorad = streamNorad; LISTEN._err = 'listen stream failed'; }
          streamNorad = streamFreq = null; onChange();
        });
        // Stream closed by the server (EOF) → drop the client flag so nothing lingers.
        a.addEventListener('ended', () => { streamNorad = streamFreq = null; onChange(); });
        doc.body.appendChild(a);
      }
      return a;
    }

    // createMediaElementSource can be called ONCE per element and permanently
    // reroutes it through the graph — there is no putting it back. So the graph is
    // built lazily on first use, and once built the element ALWAYS plays through it
    // (bypass is expressed by neutral corners, not by tearing the graph down).
    function graph() {
      if (dnNodes || dnFailed) return dnNodes;
      const a = doc.getElementById(AUDIO_ID);
      const ctxFn = root.oasisAudioContext;
      if (!a || typeof ctxFn !== 'function') { dnFailed = true; return null; }
      try {
        const ctx = ctxFn();
        if (!ctx) { dnFailed = true; return null; }
        const source = ctx.createMediaElementSource(a);
        const hp = ctx.createBiquadFilter(); hp.type = 'highpass';
        const lp = ctx.createBiquadFilter(); lp.type = 'lowpass';
        source.connect(hp); hp.connect(lp); lp.connect(ctx.destination);
        dnNodes = { ctx, source, hp, lp };
        return dnNodes;
      } catch (e) {
        // A browser that refuses the graph must not cost us the audio itself.
        dnFailed = true;
        return null;
      }
    }

    function applyDenoise() {
      const f = root.satdenoise.filterFor(denoise);
      if (f.bypass && !dnNodes) return;    // never build the graph just to do nothing
      const g = graph();
      if (!g) return;
      // Bypass = corners outside anything the stream carries, so the biquads are
      // in circuit but inaudible. Tearing the graph down is not an option.
      g.hp.frequency.value = f.bypass ? 10 : f.highpassHz;
      g.lp.frequency.value = f.bypass ? 20000 : f.lowpassHz;
      if (g.ctx.state === 'suspended') g.ctx.resume().catch(() => {});
    }

    function setDenoise(v) {
      denoise = root.satdenoise.filterFor(v).amount;
      try { root.localStorage.setItem(DENOISE_KEY, String(denoise)); }
      catch (e) { /* private mode */ }
      applyDenoise();
      onChange();
    }

    // What the readout should say. 'n/a' when the graph was refused — a slider
    // that silently does nothing is worse than one that admits it.
    function denoiseLabel() {
      return dnFailed ? 'n/a' : root.satdenoise.labelFor(denoise);
    }

    // ── state ───────────────────────────────────────────────────────────────
    function capturing() { return !!(LISTEN.recording || LISTEN.streaming); }
    function capturingFreq(norad, freq) {
      return capturing() && String(LISTEN.norad) === String(norad)
        && LISTEN.freq_hz && Math.abs(LISTEN.freq_hz / 1e6 - freq) < 1e-6;
    }
    function dongleFree() {
      return LISTEN.dongle_present && LISTEN.assigned && !LISTEN.busy
        && !(LISTEN.missing_deps || []).length;
    }
    function inWindow(norad) {
      const p = nextPass(norad);
      if (!p) return false;
      const now = Date.now();
      return now >= new Date(p.rise).getTime() - 5 * 60000
          && now <= new Date(p.set).getTime();
    }

    // The button matrix. Both truths feed it — see the header.
    function buttons() {
      const armed = getArmed();
      return root.sattransport.transportState({
        recording: !!LISTEN.recording,
        streaming: !!LISTEN.streaming,
        backend: LISTEN.backend,
        listening: streamNorad != null,
        armed: !!armed,
        dongleFree: dongleFree(),
        inWindow: !!armed && inWindow(armed.norad),
      });
    }

    // {on, live, text} for the header pill. Computed, not painted.
    function statusText() {
      if (!capturing()) return { on: false, live: false, text: '' };
      const sat = findSat(LISTEN.norad);
      const m = Math.floor(LISTEN.seconds / 60);
      const s = String(LISTEN.seconds % 60).padStart(2, '0');
      return {
        on: true,
        live: !!LISTEN.streaming,
        text: `${LISTEN.streaming ? 'LIVE' : 'REC'} ${sat ? sat.name : LISTEN.norad} ${m}:${s}`,
      };
    }

    // ── actions ─────────────────────────────────────────────────────────────
    async function poll() {
      const err = LISTEN._err;   // preserve a just-set error across the refresh
      try { LISTEN = await (await fetch('/api/satellites/listen/status')).json(); }
      catch (e) { LISTEN = { recording: false }; }
      if (err && errNorad != null) LISTEN._err = err;
      // Reconcile the client stream flag against the server: if we think we're
      // streaming but the server is idle and the <audio> isn't actually playing,
      // the stream ended — drop it so buttons don't stay greyed. (Requires the
      // audio to be stopped too, so a just-started stream in the click→first-poll
      // gap isn't cleared.)
      if (streamNorad != null && !LISTEN.streaming) {
        const a = doc.getElementById(AUDIO_ID);
        if (!a || a.paused || a.ended || !a.getAttribute('src')) {
          streamNorad = streamFreq = null;
        }
      }
      // Reflect an in-progress capture (e.g. after a reload) as the armed
      // frequency, so the transport shows the running pass rather than an empty
      // selection.
      if (capturing() && !getArmed() && LISTEN.norad != null && LISTEN.freq_hz) {
        const freq = LISTEN.freq_hz / 1e6;
        // The mode has to be recovered from the ROSTER, not from LISTEN.mode —
        // that field is the capture KIND ("record" / "stream"), and sending it as
        // a downlink mode would be rejected as not-on-this-frequency. Only adopt
        // one when the frequency names exactly one channel; where two share it,
        // no mode is honest, and the server falls back to its first-match.
        const sat = findSat(LISTEN.norad);
        const at = ((sat || {}).downlinks || [])
          .filter(d => Math.abs(d.freq_mhz - freq) < 1e-6);
        setArmed({ norad: LISTEN.norad, freq, mode: at.length === 1 ? at[0].mode : null });
      }
      onChange();
    }

    async function startRecord(norad, freqMhz, mode) {
      errNorad = null;
      const body = { norad };
      if (freqMhz != null) body.freq_mhz = freqMhz;
      // Name the MODE too. A frequency alone is ambiguous where two channels
      // share it with different demodulators, and the server would otherwise
      // take the first — capturing FM when you asked for CW.
      if (mode) body.mode = mode;
      try {
        const r = await fetch('/api/satellites/listen', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-OASIS-Request': '1' },
          body: JSON.stringify(body),
        });
        const d = await r.json();
        if (!r.ok) { errNorad = norad; LISTEN._err = d.error || 'listen failed'; }
      } catch (e) { errNorad = norad; LISTEN._err = 'listen request failed'; }
      await poll();
    }

    async function stopCapture() {
      try {
        await fetch('/api/satellites/listen/stop', {
          method: 'POST', headers: { 'X-OASIS-Request': '1' },
        });
      } catch (e) { /* ignore */ }
      errNorad = null;
      await poll();
    }

    function startStream(norad, freqMhz, mode) {
      errNorad = null;
      streamNorad = norad; streamFreq = freqMhz;
      const a = audio();
      a.src = '/api/satellites/listen/stream?norad=' + norad
            + (freqMhz != null ? '&freq_mhz=' + freqMhz : '')
            + (mode ? '&mode=' + encodeURIComponent(mode) : '');  // same ambiguity as record
      a.play().catch(() => {});   // the click IS the user gesture autoplay needs
      // After play(): this click is also the gesture that lets a suspended
      // AudioContext resume, which the filter graph needs before it can pass audio.
      applyDenoise();
      onChange();
    }

    // Drop the browser end of the audio and nothing else. On the tracked path
    // this IS the detach: ending the HTTP response runs _attach_stream's finally,
    // which removes the sink from the running capture. No POST — the recording
    // underneath must survive being listened to.
    function detachStream() {
      const a = doc.getElementById(AUDIO_ID);
      if (a) { a.pause(); a.removeAttribute('src'); a.load(); }
      streamNorad = streamFreq = null;
      poll();
    }

    function stopStream() {
      detachStream();
      // A STANDALONE stream owns the dongle, so letting go of it ends the
      // capture. The POST is not redundant with the disconnect: gunicorn often
      // doesn't notice a browser going away, so the rtl_fm pipeline (and the
      // streaming flag) would otherwise linger and keep every downlink button
      // greyed out. This is why the detach case above must NOT come through here.
      fetch('/api/satellites/listen/stop', {
        method: 'POST', headers: { 'X-OASIS-Request': '1' },
      }).catch(() => {});
      poll();
    }

    // Every branch is chosen by the pure matrix, so the click and the enabled
    // state can never disagree.
    function transport(action) {
      const s = buttons();
      const armed = getArmed();
      if (action === 'stop') {
        if (s.stop.disabled) return;
        // The hard stop, and the only one: it ends the capture, and any listener
        // attached to it goes with it. Drop our own audio first so the element is
        // not left holding a response that is about to be torn down under it.
        if (streamNorad != null) detachStream();
        stopCapture();
        return;
      }
      if (action === 'rec') {
        if (s.rec.disabled || !armed) return;
        startRecord(armed.norad, armed.freq, armed.mode);
        return;
      }
      if (action !== 'play' || s.play.disabled) return;
      if (s.play.action === 'detach') detachStream();          // recording survives
      else if (s.play.action === 'stop-stream') stopStream();  // the stream held the dongle
      else if (armed) startStream(armed.norad, armed.freq, armed.mode);
    }

    return {
      get state() { return LISTEN; },
      get streamNorad() { return streamNorad; },
      get streamFreq() { return streamFreq; },
      get errNorad() { return errNorad; },
      get denoise() { return denoise; },
      poll, transport, setDenoise, applyDenoise, denoiseLabel,
      capturing, capturingFreq, dongleFree, inWindow, buttons, statusText,
      startRecord, stopCapture, startStream, detachStream, stopStream,
    };
  }

  return { create };
});
