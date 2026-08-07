'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const H = require('../../common/js/incident-icons.js');

const ROOT = path.join(__dirname, '..', '..');

test('every hazard in the shipped catalog has a glyph', () => {
  const cat = JSON.parse(fs.readFileSync(
    path.join(ROOT, 'configuration', 'hazards.json'), 'utf8'));
  for (const h of cat.hazards) {
    assert.ok(H.hasIcon(h.key), `no glyph for hazard key "${h.key}"`);
  }
});

test('the catalog no longer carries an emoji field', () => {
  // Pi OS has no emoji font — a re-added emoji would draw a tofu box on the
  // kiosk, which is the screen an operator watches during an incident.
  const raw = fs.readFileSync(path.join(ROOT, 'configuration', 'hazards.json'), 'utf8');
  assert.ok(!/"emoji"/.test(raw), 'hazards.json reintroduced an "emoji" field');
  for (const ch of raw) {
    assert.ok(ch.codePointAt(0) < 0x1F000,
      `hazards.json contains an emoji codepoint: ${ch}`);
  }
});

test('unknown keys fall back to the warning triangle, never empty', () => {
  const svg = H.incidentIconSvg('operator-added-regional-thing');
  assert.match(svg, /^<svg /);
  assert.match(svg, /<path /);
});

test('glyphs are self-contained, themeable SVG', () => {
  for (const key of H.KEYS) {
    const svg = H.incidentIconSvg(key);
    assert.match(svg, /stroke="currentColor"/, `${key} must inherit colour`);
    assert.ok(!/<image|xlink:href|http/.test(svg), `${key} must not reference anything external`);
    assert.match(svg, /aria-hidden="true"/, `${key} is decorative`);
  }
});

test('size is overridable for the map markers', () => {
  assert.match(H.incidentIconSvg('fire', { size: '16px' }), /width="16px"/);
  assert.match(H.incidentIconSvg('fire'), /width="1em"/);
});

test('no glyph contains an emoji codepoint', () => {
  for (const key of H.KEYS.concat(['unknown'])) {
    for (const ch of H.incidentIconSvg(key)) {
      assert.ok(ch.codePointAt(0) < 0x1F000, `${key} glyph contains an emoji`);
    }
  }
});

test('every operator-placed warning category has a glyph', () => {
  const cats = JSON.parse(fs.readFileSync(
    path.join(ROOT, 'maps', 'traffic', 'warnings.json'), 'utf8'));
  for (const c of cats) {
    assert.ok(H.hasIcon(c.id), `no glyph for warning id "${c.id}"`);
  }
});

test('the warning palette no longer carries an icon field', () => {
  // These icons ARE the map markers for operator-placed emergencies — the worst
  // possible place for a glyph that renders as a tofu box on the kiosk.
  const raw = fs.readFileSync(path.join(ROOT, 'maps', 'traffic', 'warnings.json'), 'utf8');
  assert.ok(!/"icon"/.test(raw), 'warnings.json reintroduced an "icon" field');
  for (const ch of raw) {
    assert.ok(ch.codePointAt(0) < 0x1F000,
      `warnings.json contains an emoji codepoint: ${ch}`);
  }
});
