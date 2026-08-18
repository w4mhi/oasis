'use strict';
// Unit tests for the shared speech helper.
//
// The failure that matters is silent: if oasisSpeak throws instead of falling
// back, a station without Piper says NOTHING, and the only symptom is a pass
// nobody was told about.
const test = require('node:test');
const assert = require('node:assert');
const S = require('../../common/js/speech.js');

function withSynth(voices, fn) {
  const spoken = [];
  S.speechSynthesis = { getVoices: () => voices, speak: u => spoken.push(u) };
  S.SpeechSynthesisUtterance = function (text) { this.text = text; this.voice = null; };
  try { return fn(spoken); } finally {
    delete S.speechSynthesis;
    delete S.SpeechSynthesisUtterance;
  }
}

test('the ladder prefers a Piper voice over everything else', () => {
  const voices = [{ lang: 'en-US', name: 'Albert' },
                  { lang: 'en-US', name: 'Samantha' },
                  { lang: 'en-GB', name: 'piper-jenny' }];
  assert.strictEqual(S.oasisPickVoice(voices).name, 'piper-jenny');
});

test('Samantha beats an espeak variant, and any English beats none', () => {
  assert.strictEqual(S.oasisPickVoice(
    [{ lang: 'en-US', name: 'English (America)+Steph2' },
     { lang: 'en-US', name: 'Samantha' }]).name, 'Samantha');
  assert.strictEqual(S.oasisPickVoice(
    [{ lang: 'de-DE', name: 'Anna' }, { lang: 'en-GB', name: 'Daniel' }]).name, 'Daniel');
});

test('the Chromium capitalisation of the espeak variant still matches', () => {
  // Chromium reports "English (America)+Steph2"; espeak's own form is
  // lower case. A case-sensitive compare silently drops to first-English.
  const v = [{ lang: 'en-US', name: 'English (America)+Steph2' }];
  assert.strictEqual(S.oasisPickVoice(v).name, 'English (America)+Steph2');
});

test('no voices at all is null, not a throw', () => {
  assert.strictEqual(S.oasisPickVoice([{ lang: 'af', name: 'Afrikaans' }]), null);
  assert.strictEqual(S.oasisPickVoice([]), null);
  assert.strictEqual(S.oasisPickVoice(undefined), null);
});

test('an EMPTY voice list still speaks — it is not proof there is no voice', () => {
  withSynth([], spoken => {
    assert.strictEqual(S.oasisSpeakFallback('ISS, in ten minutes'), true);
    assert.strictEqual(spoken.length, 1);
    assert.strictEqual(spoken[0].voice, null);   // engine picks its default
  });
});

test('the picked voice is actually assigned to the utterance — Piper wins', () => {
  withSynth([{ lang: 'en-US', name: 'Albert' },
             { lang: 'en-GB', name: 'piper-jenny' }], spoken => {
    assert.strictEqual(S.oasisSpeakFallback('ISS, in ten minutes'), true);
    assert.strictEqual(spoken.length, 1);
    assert.strictEqual(spoken[0].voice.name, 'piper-jenny');
  });
});

test('the picked voice is actually assigned to the utterance — espeak floor only', () => {
  withSynth([{ lang: 'en-US', name: 'English (America)+Steph2' }], spoken => {
    assert.strictEqual(S.oasisSpeakFallback('ISS, in ten minutes'), true);
    assert.strictEqual(spoken.length, 1);
    assert.strictEqual(spoken[0].voice.name, 'English (America)+Steph2');
  });
});

test('empty text and a missing engine are both no-ops, never throws', () => {
  withSynth([], spoken => {
    assert.strictEqual(S.oasisSpeakFallback(''), false);
    assert.strictEqual(spoken.length, 0);
  });
  assert.strictEqual(S.oasisSpeakFallback('anything'), false);
});

// NOTE: these three do NOT use withSynth. It tears the fake engine down in a
// `finally` that runs the moment oasisSpeak returns its PROMISE — long before
// the fallback inside the catch actually runs. The fallback would then find no
// engine and return false, failing a test that describes correct behaviour.
// Install the fake, await, then clean up.
function installSynth(voices) {
  const spoken = [];
  S.speechSynthesis = { getVoices: () => voices, speak: u => spoken.push(u) };
  S.SpeechSynthesisUtterance = function (text) { this.text = text; this.voice = null; };
  return spoken;
}
function removeSynth() {
  delete S.speechSynthesis;
  delete S.SpeechSynthesisUtterance;
  delete S.fetch;
}

test('oasisSpeak falls back when the station has no speech endpoint', async () => {
  const spoken = installSynth([{ lang: 'en-US', name: 'Samantha' }]);
  // arrayBuffer() is part of the stub because the real code now drains the body
  // on the failure path too (an unread one pins a /dev/shm pipe). Without it the
  // fallback would still happen, but off a TypeError rather than the 503.
  S.fetch = () => Promise.resolve({
    ok: false, status: 503, arrayBuffer: () => Promise.resolve(new ArrayBuffer(0)),
  });
  try {
    assert.strictEqual(await S.oasisSpeak('ISS, in ten minutes'), true);
    assert.strictEqual(spoken.length, 1);
    assert.strictEqual(spoken[0].text, 'ISS, in ten minutes');
  } finally { removeSynth(); }
});

test('oasisSpeak falls back when fetch itself rejects', async () => {
  const spoken = installSynth([]);
  S.fetch = () => Promise.reject(new Error('offline'));
  try {
    assert.strictEqual(await S.oasisSpeak('ISS'), true);
    assert.strictEqual(spoken.length, 1);
  } finally { removeSynth(); }
});

test('the endpoint IS called when fetch exists — the fallback tests are real', async () => {
  // Guards the bug this file was written around: if oasisSpeak bailed to the
  // fallback before ever fetching, both tests above would pass while proving
  // nothing about the endpoint path.
  let asked = null;
  installSynth([]);
  S.fetch = (url) => { asked = url; return Promise.reject(new Error('x')); };
  try {
    await S.oasisSpeak('ISS, in ten minutes');
    assert.match(asked, /^\/api\/speech\/say\?text=ISS%2C%20in%20ten%20minutes$/);
  } finally { removeSynth(); }
});

test('empty text never calls the endpoint', async () => {
  let called = false;
  S.fetch = () => { called = true; return Promise.reject(new Error('x')); };
  try {
    assert.strictEqual(await S.oasisSpeak(''), false);
    assert.strictEqual(called, false);
  } finally { delete S.fetch; }
});

// Regression: on main, every oasisSpeak call ended in speechSynthesis.speak(),
// and the Web Speech API queues utterances for free. The Piper/audio path has
// no queue of its own — three birds crossing T-10 on the same tick used to
// play on top of each other, mush an operator could not parse. oasisSpeak
// must serialise announcements so each one starts only after the previous
// has actually finished playing (onended), not merely been started.
test('two oasisSpeak calls play in order, not on top of each other', async () => {
  const events = [];
  let fetchCalls = 0;
  S.fetch = function () {
    const n = ++fetchCalls;
    events.push('fetch:' + n);
    return Promise.resolve({
      ok: true,
      arrayBuffer: () => Promise.resolve(new ArrayBuffer(8)),
    });
  };
  S.AudioContext = function () {
    return {
      state: 'running',
      resume: function () {},
      destination: {},
      decodeAudioData: function (buf, resolve) { resolve({}); },
      createBufferSource: function () {
        const n = fetchCalls;   // the call currently being serviced
        const src = { onended: null, connect: function () {} };
        src.start = function () {
          events.push('play-start:' + n);
          // The FIRST call takes longer to finish than the second — if the
          // two calls were not serialised, 'play-start:2' would appear
          // before 'play-end:1' below.
          setTimeout(function () {
            events.push('play-end:' + n);
            if (src.onended) src.onended();
          }, n === 1 ? 20 : 1);
        };
        return src;
      },
    };
  };
  try {
    const p1 = S.oasisSpeak('one');
    const p2 = S.oasisSpeak('two');
    assert.deepStrictEqual(await Promise.all([p1, p2]), [true, true]);
    assert.deepStrictEqual(events, [
      'fetch:1', 'play-start:1', 'play-end:1',
      'fetch:2', 'play-start:2', 'play-end:2',
    ]);
  } finally {
    delete S.fetch; delete S.AudioContext;
  }
});

// ── Silencing ────────────────────────────────────────────────────────────────
// Regression: the mute bell was a POLICY switch — it stopped the next alert from
// starting, and did nothing to the one already talking. Tap it while Jenny is
// mid-sentence, which is exactly when an operator reaches for it, and she
// finished the sentence and then read out every bird still queued behind her.
// Silence has to mean silence.

// speech.js caches its AudioContext for the life of the module, so these tests
// take a FRESH copy each time. Reusing `S` would hand them the fake context an
// earlier test built, and every source they think they are watching would report
// into that test's log instead.
function freshSpeech() {
  delete require.cache[require.resolve('../../common/js/speech.js')];
  return require('../../common/js/speech.js');
}

// A fake AudioContext whose sources report when they were stopped. `stop()` does
// NOT fire onended here on purpose: a browser that skips the event must not wedge
// the queue, so oasisSpeakStop has to settle the promise itself.
function installAudio(S, log) {
  S.fetch = function () {
    return Promise.resolve({ ok: true, arrayBuffer: () => Promise.resolve(new ArrayBuffer(8)) });
  };
  S.AudioContext = function () {
    return {
      state: 'running',
      resume: function () {},
      destination: {},
      decodeAudioData: function (buf, resolve) { resolve({}); },
      createBufferSource: function () {
        const src = { onended: null, connect: function () {} };
        src.start = function () { log.push('start'); };
        src.stop = function () { log.push('stop'); };
        return src;
      },
    };
  };
}

test('oasisSpeakStop stops what is playing and settles it — no wedged queue', async () => {
  const log = [], S = freshSpeech();
  installAudio(S, log);
  try {
    // Never resolves on its own: this source's onended is never fired, so if
    // stop() did not settle the promise the await below would hang forever.
    const playing = S.oasisSpeak('ISS, in ten minutes');
    await new Promise(r => setTimeout(r, 5));   // let fetch + decode land
    assert.deepStrictEqual(log, ['start']);
    S.oasisSpeakStop();
    assert.deepStrictEqual(log, ['start', 'stop']);
    assert.strictEqual(await playing, false);   // it did not finish, it was cut off
  } finally { delete S.fetch; delete S.AudioContext; }
});

test('an announcement queued behind another never speaks after a stop', async () => {
  // The case that hurts most: three birds cross T-10 together, the operator hits
  // the bell during the first, and the other two are still queued. Nothing
  // waiting its turn may start once the station has been silenced.
  const log = [], S = freshSpeech();
  installAudio(S, log);
  try {
    const first = S.oasisSpeak('ISS');
    const second = S.oasisSpeak('NOAA 19');
    await new Promise(r => setTimeout(r, 5));
    assert.deepStrictEqual(log, ['start']);     // second is still queued
    S.oasisSpeakStop();
    assert.deepStrictEqual(await Promise.all([first, second]), [false, false]);
    // One start, one stop: the queued announcement never reached the audio graph.
    assert.deepStrictEqual(log, ['start', 'stop']);
  } finally { delete S.fetch; delete S.AudioContext; }
});

test('oasisSpeakStop also silences the fallback engine', async () => {
  // A station without Piper speaks through speechSynthesis, which has its own
  // queue. Stopping only our side would leave the browser reading the backlog.
  let cancelled = 0;
  const S = freshSpeech();
  S.speechSynthesis = {
    getVoices: () => [{ lang: 'en-US', name: 'Samantha' }],
    speak: function () {},
    cancel: function () { cancelled++; },
  };
  S.SpeechSynthesisUtterance = function (text) { this.text = text; this.voice = null; };
  try {
    assert.strictEqual(S.oasisSpeakStop(), true);
    assert.strictEqual(cancelled, 1);
  } finally { delete S.speechSynthesis; delete S.SpeechSynthesisUtterance; }
});

test('a stop does not deafen the station — the next announcement still speaks', async () => {
  // Unmuting has to work. If the generation guard were checked against the value
  // captured at stop time rather than at enqueue time, the page would go quiet
  // for good and the only symptom would be silence.
  const log = [], S = freshSpeech();
  installAudio(S, log);
  try {
    S.oasisSpeak('ISS');
    await new Promise(r => setTimeout(r, 5));
    S.oasisSpeakStop();
    log.length = 0;
    S.oasisSpeak('NOAA 19');
    await new Promise(r => setTimeout(r, 5));
    assert.deepStrictEqual(log, ['start']);
  } finally { delete S.fetch; delete S.AudioContext; }
});

test('a failing first announcement still lets the second one speak', async () => {
  // Both calls fall back to speechSynthesis (fetch always rejects). The
  // FIRST attempt to speak() throws (simulating a busy/broken engine); the
  // SECOND succeeds. If a rejected step ever wedged the queue, 'fetch:2'
  // would never be logged and the second utterance would never be spoken.
  const events = [];
  let fetchCalls = 0;
  S.fetch = function () {
    events.push('fetch:' + (++fetchCalls));
    return Promise.reject(new Error('offline'));
  };
  const spoken = [];
  let speakCalls = 0;
  S.speechSynthesis = {
    getVoices: () => [{ lang: 'en-US', name: 'Samantha' }],
    speak: function (u) {
      const n = ++speakCalls;
      events.push('speak:' + n);
      if (n === 1) throw new Error('device busy');
      spoken.push(u);
    },
  };
  S.SpeechSynthesisUtterance = function (text) { this.text = text; this.voice = null; };
  try {
    const results = await Promise.all([
      S.oasisSpeak('first, fails everywhere'),
      S.oasisSpeak('second, should still speak'),
    ]);
    assert.deepStrictEqual(results, [false, true]);
    assert.strictEqual(spoken.length, 1);
    assert.strictEqual(spoken[0].text, 'second, should still speak');
    // Proves ordering, not just outcome: call 2's fetch must not fire until
    // call 1 has fully settled (fetch -> fallback -> speak throw).
    assert.deepStrictEqual(events, ['fetch:1', 'speak:1', 'fetch:2', 'speak:2']);
  } finally {
    delete S.fetch; delete S.speechSynthesis; delete S.SpeechSynthesisUtterance;
  }
});

// ── onstart: the avatar must not mouth at silence ────────────────────────────
// jennySpeak used to wake the avatar when the announcement was REQUESTED. On a
// cache miss the station then spent 3.6 s synthesising (measured on a Pi 5),
// and her mouth ran through all of it with nothing coming out. onstart fires one
// statement before the first sample instead.

test('onstart fires immediately before playback, not when asked', async () => {
  const log = [], S = freshSpeech();
  installAudio(S, log);
  try {
    const p = S.oasisSpeak('ISS', { onstart: () => log.push('wake') });
    assert.deepStrictEqual(log, [], 'nothing has played yet — nothing should have woken');
    await new Promise(r => setTimeout(r, 5));
    assert.deepStrictEqual(log, ['wake', 'start'], 'wake, then the first sample');
    S.oasisSpeakStop();
    await p;
  } finally { delete S.fetch; delete S.AudioContext; }
});

test('a silenced announcement never wakes anything', async () => {
  // The avatar would otherwise be left animating at an utterance that was
  // dropped before it reached the speaker — and nothing would ever rest it.
  const log = [], S = freshSpeech();
  installAudio(S, log);
  try {
    const first = S.oasisSpeak('ISS', { onstart: () => log.push('wake:1') });
    const second = S.oasisSpeak('NOAA 19', { onstart: () => log.push('wake:2') });
    await new Promise(r => setTimeout(r, 5));
    S.oasisSpeakStop();
    await Promise.all([first, second]);
    assert.ok(!log.includes('wake:2'), 'the queued one never played, so it never woke');
  } finally { delete S.fetch; delete S.AudioContext; }
});

test('a throwing onstart cannot silence the station', async () => {
  // It runs inside the audio path's try/catch reach: an unguarded throw would be
  // "recovered" by speaking the whole line again through the fallback engine.
  const log = [], S = freshSpeech();
  installAudio(S, log);
  const spoken = [];
  S.speechSynthesis = { getVoices: () => [], speak: u => spoken.push(u), cancel: () => {} };
  S.SpeechSynthesisUtterance = function (t) { this.text = t; this.voice = null; };
  try {
    const p = S.oasisSpeak('ISS', { onstart: () => { throw new Error('caller bug'); } });
    await new Promise(r => setTimeout(r, 5));
    assert.deepStrictEqual(log, ['start'], 'it still played');
    assert.deepStrictEqual(spoken, [], 'and did NOT double up through the fallback');
    S.oasisSpeakStop();
    await p;
  } finally {
    delete S.fetch; delete S.AudioContext;
    delete S.speechSynthesis; delete S.SpeechSynthesisUtterance;
  }
});

test('oasisSpeak still works with no options at all', async () => {
  const log = [], S = freshSpeech();
  installAudio(S, log);
  try {
    S.oasisSpeak('ISS');
    await new Promise(r => setTimeout(r, 5));
    assert.deepStrictEqual(log, ['start']);
  } finally { delete S.fetch; delete S.AudioContext; }
});

test('oasisSpeakWarm asks with HEAD and reads no body', async () => {
  // GET here would be a regression to a real leak: a fetch() body that is never
  // read pins a 2 MiB /dev/shm data pipe per request, ~500 MB/h on a kiosk.
  const S = freshSpeech();
  let seen = null;
  S.fetch = (url, opts) => { seen = { url, opts }; return Promise.resolve({ ok: true }); };
  try {
    assert.strictEqual(await S.oasisSpeakWarm('ISS, in ten minutes'), true);
    assert.strictEqual(seen.opts.method, 'HEAD');
    assert.match(seen.url, /^\/api\/speech\/say\?text=ISS%2C%20in%20ten%20minutes$/);
  } finally { delete S.fetch; }
});

test('a prewarm that fails is not an error anyone hears about', async () => {
  // It is an optimisation. A station with no speech engine 503s every one of
  // these, and that must stay silent rather than surfacing as a broken promise.
  const S = freshSpeech();
  S.fetch = () => Promise.reject(new Error('offline'));
  try {
    assert.strictEqual(await S.oasisSpeakWarm('ISS'), false);
  } finally { delete S.fetch; }
  assert.strictEqual(await S.oasisSpeakWarm(''), false);
});
