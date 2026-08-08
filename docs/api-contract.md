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

Build the response dict inline at the `return`, even when the body comes from a
helper:

```python
return jsonify({"ok": True, **summary}), 200        # RIGHT — envelope is visible
return jsonify(summary)                             # WRONG — shape decided elsewhere
```

## 11 · Exit criterion — a functional harness

**Owed at the end of the migration, before the major version ships.**

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

Enforced by `tests/test_api_contract.py`. Endpoints not yet migrated are listed in
that test's three allowlists (`_OK_FALSE_200`, `_NO_ENVELOPE`, `_UNVERIFIABLE`), which may only ever **shrink** — the test
fails both when a conforming endpoint regresses *and* when an endpoint on the list
starts conforming without being removed from it, so the list cannot rot.
