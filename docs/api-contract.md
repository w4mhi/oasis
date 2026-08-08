# OASIS API contract

**Status:** adopted 2026-08-07 · enforced by `tests/test_api_contract.py`

This is the contract every `/api/*` endpoint must satisfy. `docs/api.md` is the
*reference* (what each endpoint does); this is the *contract* (what every endpoint
guarantees, regardless of what it does).

## Who this is for

Three consumers, in order of how much the rules cost them when we get it wrong:

1. **Small local models.** The station assistant runs 0.5B–3B models. They cannot
   infer intent from an irregular shape. A field that is sometimes absent and
   sometimes `null` is read as two different worlds; a list that silently
   truncates is reasoned about as if it were complete. Benchmarks on a Pi 5 show
   tool-choice accuracy swinging 60–100% across models — a shape they have to
   guess at spends the accuracy we have.
2. **MCP servers and other programmatic clients.** An MCP wrapper should be a thin
   projection of this API. Every inconsistency here becomes normalisation code in
   every consumer, written slightly differently each time.
3. **The OASIS front-end.** Already written, already works. The contract must be
   worth the churn of moving it.

## 1 · Envelope

Every `/api/*` JSON response is a **JSON object** (never a bare array) carrying a
boolean `ok`.

```jsonc
{ "ok": true,  "stations": [...], "total": 42, "truncated": false }
{ "ok": false, "error": "unknown mailbox", "code": "UNKNOWN_MAILBOX" }
```

Payload keys sit **flat** alongside `ok`, named for what they are (`stations`,
`aircraft`, `passes`). No `data` wrapper: it adds a level of nesting for every
consumer and buys nothing that a named key doesn't.

## 2 · `ok` means the REQUEST succeeded — not that the news is good

This is the rule most often broken today and the one that costs a small model the
most.

`ok: false` means **this call did not do what was asked**. It never means "the
call worked and the answer is negative".

```jsonc
// WRONG — the probe succeeded; the service being down is the answer, not a failure
{ "ok": false, "service": "pat", "error": "connection refused" }

// RIGHT — request fine, domain state reported in typed fields
{ "ok": true, "service": "pat", "reachable": false, "detail": "connection refused" }
```

Corollary: **an `ok: false` body must never be served with HTTP 200**, and an
`ok: true` body must never carry an `error` key. A client can branch on the status
code alone, or on `ok` alone, and get the same answer.

Where a call genuinely cannot fail by design — `/api/satellites/refresh` is
online-only and reports being offline rather than erroring — say so with a typed
field (`"online": false`), not with `ok: false`.

## 3 · Errors

```jsonc
{ "ok": false, "error": "<human-readable, safe to display>", "code": "<STABLE_SLUG>" }
```

- `error` is for a person. Wording may change; do not parse it.
- `code` is for a machine. `SCREAMING_SNAKE_CASE`, stable across releases. This is
  what an MCP client and a model branch on.
- Never leak internals. The unhandled-exception handler in `server/app.py` already
  logs the traceback server-side and returns a generic message; keep it that way.

### Status codes

| code | meaning |
|---|---|
| `200` | the request did what was asked |
| `400` | malformed or invalid input |
| `403` | rejected by the CSRF guard, or not permitted |
| `404` | the named resource does not exist |
| `409` | conflict — busy, already running, capacity reached |
| `500` | unexpected server fault (bug) |
| `503` | a dependency this endpoint proxies is unreachable |
| `507` | out of disk |

## 4 · Lists

Every list-returning endpoint MUST provide:

```jsonc
{
  "ok": true,
  "aircraft":  [ ... ],   // named for the thing, always an array, never null
  "total":     137,       // how many exist, before the limit
  "truncated": true,      // total > returned
  "limit":     50         // the limit actually applied
}
```

- **A default limit is mandatory.** Unbounded is not an option: the assistant runs
  in an 8192-token context, and "every aircraft in range" has emptied it before.
- **Ordering MUST be deterministic and documented** — nearest-first,
  most-recent-first, whatever fits, but the same input always yields the same
  order. Dict/insertion order is not an ordering.
- An empty result is `[]` with `ok: true`. Emptiness is an answer, not an error.

## 5 · Fields are always present; unknown values are `null`

If a field is in the schema, it is in **every** response for that endpoint.
Unknown or not-applicable is `null`.

```jsonc
{ "callsign": "W4MHI", "altitude_ft": null }   // RIGHT — the key is always there
{ "callsign": "W4MHI" }                        // WRONG — is altitude unknown, or zero?
```

A model handles an explicit `null` correctly and guesses at a missing key.

## 6 · Timestamps

**ISO-8601, UTC, `Z`-suffixed, always**: `"2026-08-07T16:03:36Z"`.

No epoch floats in an API response. They are unreadable to a model, ambiguous
between seconds and milliseconds, and require the client to know the unit. Where
an age is genuinely more useful than an instant, provide **both** — `last_heard`
plus `age_s` — never age alone, which is unstable across calls.

## 7 · Units live in the field name

`altitude_ft`, `distance_km`, `freq_mhz`, `age_s`, `bytes`. Never a bare
`altitude`. The name is the only documentation a model reads at call time.

## 8 · Idempotency

- `GET`/`HEAD` are safe: no observable state change, ever.
- `PUT`/`DELETE`/`PATCH` are idempotent: applying twice equals applying once.
- `POST` that **creates** must be safe to retry. A model that times out will retry,
  and a duplicate that keys the transmitter is not an acceptable outcome. Either
  accept a client-supplied idempotency key, or de-duplicate on natural identity
  within a window.
- Prefer set-semantics over per-item calls for bulk changes. N concurrent
  single-item writes against one file is how the satellite roster lost 19 of 20
  selections; one call applying a whole set is both faster and correct.

## 9 · Versioning

The contract is versioned with the product. A breaking change to any endpoint is a
**major** version bump, recorded in `CHANGELOG.md` with the old and new shape.
There is no in-band update mechanism for a deployed station, so the version number
is the only signal a consumer gets — treat it as the announcement.

## 10 · The envelope must be visible where it is returned

`return jsonify(payload)` — where `payload` is a variable built elsewhere — is not
acceptable. It hides the response shape from review, and from the conformance
test: 17 routes were silently satisfying every other rule purely because their
shape could not be inspected at the return site.

The same applies to delegating the whole response to a helper. `return
_adsb_proxy("/alerts")` hands the shape to a pass-through that copies the upstream
daemon's bytes — the OASIS response is then whatever an internal service happened
to emit, which is not a contract at all.

```python
return jsonify({"ok": True, **summary}), 200        # RIGHT — envelope is visible
return jsonify(summary)                             # WRONG — shape decided elsewhere
return _some_proxy("/upstream")                     # WRONG — shape is upstream's
```

**A proxy route is a contract boundary, not a pipe.** Parse the upstream response
and build ours. That is what keeps the contract true even when the upstream daemon
ships as its own systemd unit and is not restarted in lockstep with the web server
— which is exactly the case for `adsb-api` and `graywolf-api`.

Genuinely non-JSON responders (`send_file`, `send_from_directory`, streamed
`Response`) are out of scope: this contract governs JSON shapes.

## 11 · Exit criterion — a functional harness

**DELIVERED** — `scripts/api-probe.py`. Run it against a running station:

```bash
python3 scripts/api-probe.py --host 192.168.1.28 --out station-a.txt
diff station-a.txt station-b.txt      # shapes must match; data need not
```

Safe by default; `--mutate` adds endpoints that change state or seize
hardware; `--danger` adds the ones that transmit, reboot, install or delete.
Exits non-zero on any contract violation, so it is a gate and not just a
dump. **On its first run it found 17 violations the static gate cannot see**
— §3 error codes and §4 list bounds exist only in a real response.

`tests/test_api_contract.py` reads the *source*; it proves what we wrote, not what
the server actually puts on the wire. The unit and end-to-end suites cover the
logic. What is missing is one script that calls every `/api/*` endpoint against a
**running station** and writes each request and its response to a file for a human
to read.

Requirements:

- **Python, stdlib only**, runnable against any host: `python3 scripts/api-probe.py
  --host 192.168.1.28`. It must work from a Mac against a Pi, and on the Pi itself.
- **Re-runnable, in different environments.** The point is comparing a dev box, a
  Pi with no SDR, and a fully-populated station — the shapes must match even when
  the data does not.
- **Output is for inspection and for diffing**: one record per call (method, path,
  params, status, elapsed, body) with **sorted keys and stable formatting**, so two
  environments can be diffed directly. Volatile fields (timestamps, uptimes, load)
  are recorded but listed separately so they do not swamp a diff.
- **Safe by default.** It will encounter `/api/setup/reboot`, `/api/service`,
  `/api/hardware/burn-serial` and `/api/aprs/warnings`. Read-only endpoints are
  called by default; mutating ones are **skipped unless explicitly opted in**, and
  the ones that key a transmitter or reboot the host stay behind a second, separate
  flag. A probe that reboots the station it is probing is not a probe.
- **Fails loudly on a contract violation**, so it doubles as the runtime half of
  the gate: envelope present, `ok` matching the status class, lists carrying
  `total`/`truncated`/`limit`, timestamps ISO-8601.

This is the artifact that answers "did the migration actually land?" in a way a
static test cannot, and it stays useful afterwards as the thing you run against a
box before an event.

## 12 · Migration status

**COMPLETE as of 2026-08-08.** All three allowlists (`_OK_FALSE_200`,
`_NO_ENVELOPE`, `_UNVERIFIABLE`) are EMPTY: every `/api/*` route on the OASIS
surface carries the envelope, means "the request succeeded" by `ok`, and builds
its response where a reader can see it.

The lists stay in `tests/test_api_contract.py` and the ratchet now asserts
**zero**. That is deliberate — the migration being finished is exactly when an
exemption becomes tempting. A new entry is not debt to pay down later; it is a
regression of a rule the API is built on, and the test says so.

Still owed, and tracked honestly rather than pretended away:

- **§7 field renames.** ADS-B keeps dump1090's `alt_baro`/`gs`; Winlink passes
  Pat's inner objects through (see *Bounded migrations* above). Both are named
  in the code where they occur.
- **§11 functional probe harness** — `scripts/api-probe.py`, below.

### Which API the contract governs

Three Flask apps live in this repo: the OASIS API on `:8083`, `graywolf-api` on
`:8085` (`services/aprs/common/aprs.py`), and the copy `enable-graywolf-api.py`
writes out at install time. **The contract governs the OASIS API** — the surface a
browser, an MCP server, or a model actually talks to. The internal daemons bind
loopback and are reached only through the Flask route in front of them, which is
the contract boundary (§10) and re-normalises everything they emit.

The scan tells them apart structurally: every OASIS route is a module-level
function on a module-level Blueprint, while both daemons build `Flask(__name__)`
inside a factory and nest their routes in it. That inference is pinned by a test,
because two of these servers serve a route literally named `/api/system` and a
third named `/api/aprs/stations` — keying facts by rule string alone let a
conforming route be held hostage by a same-named route on a different server.

Daemon routes are still required to carry an `ok` envelope; they are exempt from
the rest.

### When the gate cannot see a status

Some routes compute their HTTP status — `return jsonify(...), e.code`, where an
exception carries both the status and the slug. That is better code than eight
literal branches, but `tests/api_contract_scan.py` parses source, so it cannot
read the value. It marks those returns `status_dynamic` and the
ok:false-with-200 rule **skips** them rather than guessing: recording an unknown
status as 200 would invent a violation, recording it as non-200 would invent a
clearance.

Skipping silently would be a hole, so the routes that do it are pinned in
`_DYNAMIC_ERROR_STATUS` and each is covered by a runtime test asserting the real
status (`tests/test_satellites_listen_contract.py`). A static gate that admits
its limit and hands off is worth more than one that guesses.

### Bounded migrations: third-party upstreams

`/api/winlink/*` fronts **Pat**, a third-party Go binary whose payload shape we
neither own nor can pin — `read-state.js` copes with `MID`/`Mid`/`mid` and
`Unread`/`unread` because the casing varies by version.

For an upstream like that the migration is deliberately **envelope-only**: OASIS
owns `ok`, the named container key, the list bounds and the error codes, and the
upstream's INNER objects pass through untouched. Renaming fields we cannot verify
against a live instance would break the client on a version nobody tested.

That is a real §7 gap and it is recorded as debt, not pretended away. The rule:
**wrap what you own, pass through what you cannot verify, and say which is
which** — never guess at a shape and call it a contract.

### Shared helpers

`common/api_shape.py` holds the one implementation of §4 and §6 — `iso_utc()`,
`iso_utc_from_text()`, `clamp_limit()`. Endpoints import them rather than growing
private copies; two copies of a timestamp formatter is how an API ends up with two
timestamp formats.
