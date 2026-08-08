'use strict';
/*
 * front-end-gate.test.js — syntax + hygiene gate for EVERY tracked front-end asset.
 *
 * Why this exists: OASIS ships ~18,000 lines of inline JS across its HTML, and
 * until now the only automated check was `node --check` over `maps/**.js` plus a
 * curly-quote scan of `maps/**.html`. That covered roughly 8% of the front-end
 * logic. Everything in `services/`, `static/`, `server/system/`, `tools/` and the
 * inline blocks of `index.html` itself shipped with no syntax check at all — a
 * stray brace in a 3,400-line inline block would reach the Pi and only surface as
 * a blank page.
 *
 * It lives in tests/js (not a workflow step) so ONE mechanism serves both CI
 * (js-tests.yml runs `node --test`) and the local `/preflight`. A gate that only
 * exists in YAML is a gate you can't run before pushing.
 *
 * Three checks:
 *   1. every inline <script> block parses
 *   2. every authored .js file parses (vendored bundles excluded)
 *   3. no curly/smart quotes INSIDE a tag
 *
 * On (3): the original maps-only lint failed on curly quotes anywhere in the file.
 * That can't go repo-wide — the handbook and setup pages use proper typographic
 * quotes in prose, correctly. The actual failure mode is a smart quote inside an
 * ATTRIBUTE, where it silently stops delimiting and breaks the element. So this
 * scans inside tags only, which is both more correct and repo-wide clean.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { execSync } = require('node:child_process');

const ROOT = path.join(__dirname, '..', '..');

// Third-party bundles: not ours to fix, and some are minified ES modules that a
// classic-script parse would reject for reasons that tell us nothing.
const VENDOR = /(^|\/)vendor\/|maplibre-gl\.js$|pmtiles\.js$|satellite\.min\.js$|pdf-lib\.min\.js$|\.min\.js$/;

function tracked(glob) {
  return execSync(`git ls-files ${glob}`, { cwd: ROOT }).toString().trim().split('\n')
    .filter(Boolean).filter((f) => !VENDOR.test(f));
}

const read = (f) => fs.readFileSync(path.join(ROOT, f), 'utf8');
const lineOf = (src, index) => src.slice(0, index).split('\n').length;

test('every inline <script> block parses', () => {
  const failures = [];
  let blocks = 0;
  for (const file of tracked("'*.html'")) {
    const src = read(file);
    const re = /<script(?![^>]*\bsrc=)([^>]*)>([\s\S]*?)<\/script>/g;
    let m, n = 0;
    while ((m = re.exec(src))) {
      n++;
      // Skip non-JS payloads (JSON-LD, templates) — they are not scripts to parse.
      const type = (/type\s*=\s*["']([^"']+)["']/.exec(m[1]) || [])[1];
      if (type && !/javascript|module/i.test(type)) continue;
      blocks++;
      try {
        new vm.Script(m[2]);
      } catch (e) {
        failures.push(`${file}:${lineOf(src, m.index)} (block ${n}) — ${e.message.split('\n')[0]}`);
      }
    }
  }
  assert.ok(blocks > 25, `expected to find inline blocks, found ${blocks} — did the scan break?`);
  assert.deepStrictEqual(failures, [],
    'inline <script> blocks with syntax errors:\n  ' + failures.join('\n  '));
});

test('every authored .js file parses', () => {
  const failures = [];
  const files = tracked("'*.js'").filter((f) => !f.startsWith('tests/'));
  for (const file of files) {
    try {
      new vm.Script(read(file));
    } catch (e) {
      failures.push(`${file} — ${e.message.split('\n')[0]}`);
    }
  }
  assert.ok(files.length > 10, `expected authored .js files, found ${files.length}`);
  assert.deepStrictEqual(failures, [],
    'authored .js files with syntax errors:\n  ' + failures.join('\n  '));
});

test('no curly/smart quotes inside an HTML tag', () => {
  // U+2018..U+201D. Inside a tag these stop delimiting an attribute and silently
  // break the element; node --check never sees them because it only parses
  // <script>. In prose they are correct typography and are left alone.
  const failures = [];
  for (const file of tracked("'*.html'")) {
    const src = read(file);
    for (const m of src.matchAll(/<[^>!][^>]*>/g)) {
      for (const ch of m[0]) {
        const c = ch.codePointAt(0);
        if (c >= 0x2018 && c <= 0x201d) {
          failures.push(`${file}:${lineOf(src, m.index)} — ${m[0].slice(0, 70)}`);
          break;
        }
      }
    }
  }
  assert.deepStrictEqual(failures, [],
    'curly quotes inside a tag (they break attribute delimiting):\n  ' + failures.join('\n  '));
});
