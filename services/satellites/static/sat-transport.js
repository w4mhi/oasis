// The Rec / Play / Stop matrix for the satellites transport, as one pure
// function. UMD: browser (window.sattransport) and node (module.exports).
//
// THE RULE THIS ENCODES
// ---------------------
// STOP IS THE ONLY THING THAT ENDS A RECORDING. Rec starts one and is inert
// while it runs; Play attaches and detaches a listener and can never destroy the
// artifact. A pass cannot be re-taken, so the destructive action gets exactly one
// button and it is never the one you pressed to start.
//
// That is also why Rec does not turn into a STOP button while recording. Two
// self-labelling toggles put two red STOPs on screen the moment you listen to
// your own recording, and the operator has to remember which is which at the one
// moment they cannot afford to. It would also move the destructive action under
// the finger that just started the capture.
//
// WHY THE BACKEND IS AN INPUT
// ---------------------------
// Play-while-Rec is a property of the DSP path, not a preference. On `csdr` the
// audio arrives in-process and a pump thread fans it out, so a listener joins a
// running recording (capture.py: ONE CAPTURE, MANY SINKS). On `rtl_fm` it is a
// shell pipeline with one stdout and the two are physically exclusive. The
// button follows the fact, and says which fact it is following — a silently grey
// Play is indistinguishable from a broken one.
//
// DETACH IS NOT STOP
// ------------------
// Letting go of a listener that joined a recording must tear down the browser's
// audio element and nothing else: the server removes the sink when the response
// generator closes (routes._attach_stream's finally). Posting /listen/stop there
// would kill the recording underneath — which is exactly why this was parked.
// A STANDALONE stream is different: it owns the dongle, so letting go of it does
// end the capture, and that path keeps its POST.
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.sattransport = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const ARM_HINT_NONE = 'arm a frequency first';
  const ARM_HINT_WINDOW = 'not in range yet (armable ~5 min before the pass)';
  const ARM_HINT_DONGLE = 'the SDR is busy with another service';

  // Only the corrected path can fan one capture out to several sinks. Anything
  // else — rtl_fm, or a backend we were not told — is treated as exclusive. An
  // absent backend means something is wrong upstream (it is always populated
  // while capturing), and an attach that the server refuses lands as an error
  // mid-pass, which is worse than a button that explains itself.
  function canAttach(backend) { return backend === 'csdr'; }

  function transportState(s) {
    const o = s || {};
    const recording = !!o.recording;
    const streaming = !!o.streaming;
    const listening = !!o.listening;
    const capturing = recording || streaming;
    const armed = !!o.armed;
    const canStart = armed && !capturing && !!o.dongleFree && !!o.inWindow;

    const armHint = !armed ? ARM_HINT_NONE
                  : !o.dongleFree ? ARM_HINT_DONGLE
                  : !o.inWindow ? ARM_HINT_WINDOW
                  : '';

    const rec = {
      disabled: capturing ? true : !canStart,
      active: recording,
      action: 'start',
      title: recording ? 'recording - press Stop to end it'
           : streaming ? 'listening - stop first, one dongle'
           : canStart ? 'record the armed pass to a WAV'
           : armHint,
    };

    // Order matters: the server flag outranks the client one. A standalone
    // stream sets both, and treating it as a detach would leave the dongle held.
    const playAction = streaming ? 'stop-stream' : (listening ? 'detach' : 'start');

    let playDisabled, playTitle;
    if (playAction !== 'start') {
      playDisabled = false;
      playTitle = playAction === 'detach'
        ? 'stop listening - the recording continues'
        : 'stop listening';
    } else if (recording) {
      playDisabled = !canAttach(o.backend);
      playTitle = playDisabled
        ? 'this recording is on the uncorrected path, which has one audio stream - stop the recording to listen'
        : 'listen in while the recording continues';
    } else {
      playDisabled = !canStart;
      playTitle = canStart ? 'listen live to the armed pass' : armHint;
    }

    const play = { disabled: playDisabled, active: listening || streaming,
                   action: playAction, title: playTitle };

    const stop = {
      disabled: !capturing,
      active: capturing,
      action: 'stop',
      title: recording ? 'stop the recording' : streaming ? 'stop listening'
                                              : 'nothing to stop',
    };

    return { rec: rec, play: play, stop: stop };
  }

  return { transportState: transportState, canAttach: canAttach };
});
