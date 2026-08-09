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
  S.fetch = () => Promise.resolve({ ok: false, status: 503 });
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
