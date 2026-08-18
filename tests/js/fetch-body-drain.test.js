'use strict';
/*
 * fetch-body-drain.test.js — a NARROW guard against the /dev/shm leak.
 *
 * The rule it defends: every fetch() response body is read on every path. An
 * unread body pins a 2 MiB /dev/shm data pipe until Chromium collects the
 * Response, and OASIS dashboards run for days without navigating away. That leak
 * cost a kiosk ~500 MB/h through the Winlink mailbox probe, was fixed once in the
 * shared modules, again in 65e4cd4, and again in the sweep this file came out of.
 *
 * ── What this scan CAN see ───────────────────────────────────────────────────
 * Three textual shapes, and only in authored front-end sources:
 *
 *   1. `const r = await fetch(...)` where the first thing done with `r` is a
 *      status guard that returns/throws/continues, with no read of r's body
 *      before it. This is the shape the sweep removed 70-odd times.
 *   2. `if (r.ok) { …read… }` — the body taken on the success arm only.
 *   3. `r.ok ? r.json() : something-that-does-not-read` — the ternary form of
 *      the same bug, where the failure arm skips the body.
 *   4. `await fetch(...)` as a bare statement: the Response is never bound, so
 *      its body cannot be read by anyone, ever.
 *
 * Run against the tree as it stood before the sweep (af81f49) it named 66 of the
 * ~75 sites that sweep fixed, including every one on index.html and the kiosk.
 *
 * HEAD requests and opaque `mode: 'no-cors'` probes are exempt on purpose. A HEAD
 * has no body to pin — switching a probe to HEAD is one of the fixes for this
 * leak, not an instance of it — and an opaque response's body is not readable at
 * all, so there is nothing to drain.
 *
 * ── What it CANNOT see, and there is no honest way around it ─────────────────
 * A regex over source text cannot prove a body is drained. Specifically it will
 * NOT catch:
 *
 *   - a Response handed to another function that reads (or fails to read) it;
 *   - a promise chain whose branches live in different files or callbacks;
 *   - `fetch(...).then(…)` / `.catch(…)` with no `await` and no bound Response,
 *     which is how three of the nine sites it missed were written;
 *   - a bare `await fetch(...)` that does not begin its line (e.g. tucked inside
 *     a one-line `try { … }`);
 *   - a leak more than ~25 lines below the fetch, or a guard whose exit is more
 *     than 6 lines below the guard itself -- setup.html's 404 arm was one;
 *   - `res.clone()` read on one branch only, which buffers just the same;
 *   - anything reached by dynamic dispatch, or a body read inside a `finally`.
 *
 * A site that drains in the ELSE arm of `if (r.ok)` would be reported wrongly.
 * Nothing in the tree is written that way, and the fix everywhere else is to
 * read before the guard, which silences it honestly rather than by exception.
 *
 * It also blanks `//` comments before scanning (cutting at the first `//` that
 * is not part of a URL scheme), so prose describing the bug is not mistaken for
 * the bug. That can only hide a real site, never invent one.
 *
 * So: a pass here means "none of the three known-bad shapes is present", NOT
 * "no fetch leaks a body". Treat a failure as certain and a pass as partial.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { execSync } = require('node:child_process');

const ROOT = path.join(__dirname, '..', '..');

// Same exclusions as front-end-gate.test.js: third-party bundles are not ours.
const VENDOR = /(^|\/)vendor\/|maplibre-gl\.js$|pmtiles\.js$|satellite\.min\.js$|pdf-lib\.min\.js$|\.min\.js$/;
// Not browser code: the test harness stubs fetch, and scripts/ runs under node
// for seconds at a time rather than in a page left open for days.
const NOT_BROWSER = /^tests\/|^scripts\//;

function tracked() {
  return execSync("git ls-files '*.html' '*.js'", { cwd: ROOT }).toString()
    .trim().split('\n').filter(Boolean)
    .filter((f) => !VENDOR.test(f) && !NOT_BROWSER.test(f));
}

// Blank the tail of any line from its first `//`, unless that `//` follows a
// colon (http://, https://). Line count is preserved so reports stay accurate.
function decomment(src) {
  return src.split('\n').map((line) => {
    const m = /(^|[^:])\/\//.exec(line);
    return m === null ? line : line.slice(0, m.index + m[1].length);
  }).join('\n');
}

const BODY = '(?:json|text|arrayBuffer|blob|formData|bytes)';
const reads = (v) => new RegExp('\\b' + v + '\\s*\\.\\s*(?:' + BODY + '\\s*\\(|body\\b)');
// A status guard: !r.ok, or any branch on r.status (a 404 arm is still an arm).
const guards = (v) => new RegExp('!\\s*' + v + '\\s*\\.\\s*ok\\b|\\b' + v + '\\s*\\.\\s*status\\b');
// The mirror image: `if (r.ok) { …read… }` reads on the success arm only, which
// is the shape 65e4cd4 fixed. Matched only before any read of r, so the fixed
// `const d = await r.json(); if (r.ok && d)` form is not caught by it.
const ifOk = (v) => new RegExp('\\bif\\s*\\(\\s*' + v + '\\s*\\.\\s*ok\\b');
const EXITS = /\b(?:return|throw|continue|break)\b/;
// Exempt: no body to drain (HEAD) or no readable body at all (opaque).
const EXEMPT = /method\s*:\s*['"`]HEAD['"`]|mode\s*:\s*['"`]no-cors['"`]/i;

// End of the fetch(...) call, by paren balance from its opening paren. Parens
// inside string literals would confuse this; none of the tracked URLs have any.
function endOfCall(src, openIdx) {
  let depth = 0;
  for (let i = openIdx; i < src.length; i++) {
    if (src[i] === '(') depth++;
    else if (src[i] === ')' && --depth === 0) return i;
  }
  return src.length;
}

const lineOf = (src, index) => src.slice(0, index).split('\n').length;
const at = (re, line) => { const m = re.exec(line); return m === null ? -1 : m.index; };

const LOOKAHEAD = 25;   // lines after the fetch to judge
const GUARD_TAIL = 6;   // lines after a guard in which its exit must appear

function scanAssigned(file, src, out) {
  const re = /(?:^|[^\w$.])(?:const|let|var)?\s*([A-Za-z_$][\w$]*)\s*=\s*await\s+fetch\s*\(/g;
  let m;
  while ((m = re.exec(src))) {
    const v = m[1];
    const open = src.indexOf('(', m.index + m[0].length - 1);
    const end = endOfCall(src, open);
    if (EXEMPT.test(src.slice(open, end + 1))) continue;

    const rest = src.slice(end).split('\n').slice(0, LOOKAHEAD + 1);
    const readsV = reads(v), guardsV = guards(v), ifOkV = ifOk(v);
    for (let i = 0; i < rest.length; i++) {
      const line = rest[i];
      // Order within the line matters, and this repo writes whole health checks
      // on one: `const r=await fetch(u); if(!r.ok) return {…}; const d=await
      // r.json();` drains nothing on the failure path even though a read is
      // sitting right there on the same line.
      const ri = at(readsV, line), gi = at(guardsV, line), oi = at(ifOkV, line);
      if (oi >= 0 && (ri < 0 || ri > oi)) {
        out.push(`${file}:${lineOf(src, end) + i} — \`${v}\` is read only inside `
          + `\`if (${v}.ok)\`; the failure path leaves the body in the pipe`);
        break;
      }
      if (gi < 0) { if (ri >= 0) break; continue; }        // read, or nothing yet
      if (ri >= 0 && ri < gi) break;                       // drained before the guard
      let verdict = EXITS.test(ri >= 0 ? line.slice(gi, ri) : line.slice(gi))
        ? 'leak' : (ri >= 0 ? 'ok' : null);
      // A guard whose exit sits below it, in a block. Whichever comes first
      // between the exit and a read of the body decides.
      for (let j = i + 1; verdict === null && j <= i + GUARD_TAIL && j < rest.length; j++) {
        if (readsV.test(rest[j])) verdict = 'ok';
        else if (EXITS.test(rest[j])) verdict = 'leak';
      }
      if (verdict === 'leak') {
        out.push(`${file}:${lineOf(src, end) + i} — \`${v}\` exits on the status `
          + `without reading the body; drain it (see any of the fixed sites)`);
      }
      break;
    }
  }
}

function scanTernary(file, src, out) {
  const re = /\b([A-Za-z_$][\w$]*)\s*\.\s*ok\s*\?([^:\n]*):([^;)\n]*)/g;
  let m;
  while ((m = re.exec(src))) {
    const v = m[1];
    const readsV = reads(v);
    // Only interesting when the TRUE arm reads and the FALSE arm does not.
    if (readsV.test(m[2]) && !readsV.test(m[3])) {
      out.push(`${file}:${lineOf(src, m.index)} — \`${v}.ok ? …read… : …\` skips `
        + `the body on the failure arm; read it on both`);
    }
  }
}

function scanDiscarded(file, src, out) {
  const lines = src.split('\n');
  for (let i = 0; i < lines.length; i++) {
    if (!/^\s*await\s+fetch\s*\(/.test(lines[i])) continue;
    const at = src.split('\n').slice(0, i).join('\n').length + lines[i].indexOf('await');
    const open = src.indexOf('(', at);
    if (EXEMPT.test(src.slice(open, endOfCall(src, open) + 1))) continue;
    out.push(`${file}:${i + 1} — the Response is never bound, so its body can `
      + `never be read; keep it and drain it`);
  }
}

test('no authored fetch() exits on the status without reading the body', () => {
  const found = [];
  for (const file of tracked()) {
    const src = decomment(fs.readFileSync(path.join(ROOT, file), 'utf8'));
    if (!/\bfetch\s*\(/.test(src)) continue;
    scanAssigned(file, src, found);
    scanTernary(file, src, found);
    scanDiscarded(file, src, found);
  }
  assert.deepStrictEqual(found, [], 'unread fetch() bodies pin a 2 MiB /dev/shm '
    + 'pipe each until Chromium collects the Response:\n  ' + found.join('\n  '));
});

// The scan is only worth having if it fires. These fixtures are the three shapes
// as they were actually written in the tree before the sweep, so a refactor that
// quietly stops matching them fails here rather than passing everywhere.
test('the scan fires on the shapes it claims to catch', () => {
  const cases = [
    ['assigned', 'const r = await fetch(u);\nif (!r.ok) return null;\nconst d = await r.json();\n'],
    ['assigned, block guard', 'const res = await fetch(u);\nif (!res.ok) {\n  msg("no");\n  return;\n}\nreturn res.json();\n'],
    ['status arm', 'const r = await fetch(u);\nif (r.status === 404) { stop(); return; }\nconst d = await r.json();\n'],
    ['all on one line', 'const r=await fetch(u); if(!r.ok) return {up:false}; const d=await r.json(); return d;\n'],
    ['success arm only', 'const r = await fetch(u);\nif (r.ok) {\n  const d = await r.json();\n  use(d);\n}\n'],
    ['ternary', 'fetch(u).then(r => r.ok ? r.json() : null).then(d => d);\n'],
    ['discarded', 'await fetch(u, { method: "POST" });\n'],
  ];
  for (const [name, src] of cases) {
    const out = [];
    scanAssigned('f.js', src, out);
    scanTernary('f.js', src, out);
    scanDiscarded('f.js', src, out);
    assert.ok(out.length === 1, `${name}: expected one finding, got ${out.length}`);
  }
});

test('the scan stays quiet on the shapes that are already correct', () => {
  const cases = [
    ['parse first', 'const r = await fetch(u);\nconst d = await r.json().catch(() => null);\nif (!r.ok || !d) return null;\n'],
    ['drain then throw', 'const r = await fetch(u);\nif (!r.ok) { await r.text().catch(() => {}); throw new Error("x"); }\nreturn r.json();\n'],
    ['drain in a block', 'const r = await fetch(u);\nif (r.status === 404) {\n  await r.text();\n  return;\n}\nconst d = await r.json();\n'],
    ['HEAD has no body', 'const r = await fetch(u, { method: "HEAD" });\nif (!r.ok) return false;\nreturn true;\n'],
    ['opaque has no readable body', 'await fetch(u, { cache: "no-store", mode: "no-cors" });\n'],
    ['unconditional read', 'const r = await fetch(u);\nconst d = await r.json();\nif (!r.ok) return null;\n'],
    ['cancel counts as a drain', 'const r = await fetch(u);\nif (!r.ok) { r.body.cancel(); return null; }\nreturn r.json();\n'],
    ['one line, parse first', 'const r=await fetch(u); const d=await r.json().catch(()=>null); if(!r.ok||!d) return {up:false};\n'],
  ];
  for (const [name, src] of cases) {
    const out = [];
    scanAssigned('f.js', src, out);
    scanTernary('f.js', src, out);
    scanDiscarded('f.js', src, out);
    assert.deepStrictEqual(out, [], `${name}: expected silence`);
  }
});
