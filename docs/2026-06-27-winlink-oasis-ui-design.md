# OASIS-styled Winlink interface — Design

**Date:** 2026-06-27
**Status:** Approved (design); pending implementation plan
**Author:** W4MHI (with Claude Code)

## 1. Goal

Replace Pat's stock web UI (for OASIS users) with an OASIS-styled, offline-first
Winlink mail client that runs the full core loop:

- browse mailboxes (Inbox / Outbox / Sent / Archive),
- read and delete messages,
- compose and queue a plain message (To / CC / Subject / Body),
- run a connect session with live feedback.

Pat (`la5nta/pat`) remains the actual Winlink engine. Its own web UI stays
installed and linked as a fallback. This project builds **only** a new
front-end plus a thin Flask proxy — it does not reimplement Winlink logic.

### Non-goals (v1)

- File attachments on compose.
- Winlink HTML form templates (ICS-213 etc.) — documented as the parity
  roadmap in §10, not built.
- Editing Pat's `config.json` from the UI.

## 2. Constraints

- **Offline-first.** No internet at runtime, no CDNs, no external API calls, no
  build step. Vanilla JS, `localStorage` for any client state, styling from the
  bundled `css/common.css` only.
- **Raspberry Pi Zero 2 W (512 MB).** Backend stays minimal and stateless;
  streaming/pass-through over buffering.
- Pat already runs as a systemd service (`pat http`) on **port 8082**
  (`scripts/install-winlink.py`). OASIS Flask serves on **8083**.

## 3. Architecture (Approach A — Flask-proxied page)

```
Browser (winlink/mail.html, css/common.css)
   │  HTTP (same-origin)                │  WebSocket (cross-origin, allowed)
   ▼                                    ▼
Flask :8083  /api/winlink/*  ───────►  Pat :8082  /api/*      Pat :8082  /ws
   (urllib proxy, mirrors                                     (live session log)
    /api/aprs/* convention)
```

- **HTTP** calls go through new Flask proxy routes so the browser stays
  same-origin (no CORS). Pat does **not** emit CORS headers by default, so
  proxying — not direct client-to-Pat — is the correct path.
- **WebSocket** for the live connect-session log is opened **directly** from the
  browser to `ws://<host>:8082/ws`. Browser WebSocket connections are not
  subject to CORS preflight, and Pat accepts them. This keeps the streaming load
  off the synchronous Flask/gunicorn backend (Pi Zero friendly).
- If the WebSocket fails to open, the UI **degrades gracefully** to polling
  `GET /api/winlink/status`.

### Rejected alternatives

- **B — Direct client-to-Pat for everything:** requires enabling CORS on Pat or
  a reverse proxy; fragile and off-convention.
- **C — Thick Flask backend owning the mailbox:** duplicates Pat's logic, adds
  state, violates the minimal/stateless backend rule.

## 4. Flask proxy surface (`server/app.py`)

Thin pass-throughs, one Pat endpoint each, built exactly like the existing
`api_aprs_stations_proxy` (`server/app.py:735`): `urllib.request`, return
`Response(resp.read(), status=..., content_type="application/json")`, pass
through `HTTPError` bodies verbatim, return `503` on `URLError`/timeout with an
OASIS-style error message. The Pat port (8082) comes from the existing port
constant rather than a new literal.

| Route | → Pat endpoint | Purpose |
|---|---|---|
| `GET /api/winlink/mailbox/<box>` | `/api/mailbox/{box}` | list a box (in/out/sent/archive) |
| `GET /api/winlink/mailbox/<box>/<mid>` | `/api/mailbox/{box}/{mid}` | read one message |
| `DELETE /api/winlink/mailbox/<box>/<mid>` | `/api/mailbox/{box}/{mid}` | delete a message |
| `POST /api/winlink/mailbox/out` | `/api/mailbox/out` | compose/queue (form-encoded: to/cc/subject/body) |
| `GET /api/winlink/status` | `/api/status` | connection state |
| `GET /api/winlink/aliases` | `/api/connect_aliases` | configured transports |
| `GET /api/winlink/connect?url=…` | `/api/connect?url=…` | start a session |

**Verification required during implementation:** exact Pat field names and JSON
shapes (status payload keys, the compose POST form field names, mailbox list
item shape, alias map shape) must be confirmed against the **installed Pat
version's** `pat http` API — they are not asserted here. The proxy is a
byte/JSON pass-through, so it is resilient to shape changes; the front-end is
where field names get bound and must be checked against a live Pat.

`box` is validated against an allowlist (`in`, `out`, `sent`, `archive`) before
proxying, matching the `ALLOWED`-set guard style at `server/app.py:405`.

## 5. Front-end UX (`winlink/mail.html`)

Standard OASIS page shell: `.wrap` → `header` with `⌂ HOME` link and a `🛰️`
title, like `winlink/position-report.html`. Body is a **three-pane layout**:

- **Left rail:** mailbox folders with unread/total counts; a **Connect** button
  with a transport-alias dropdown (populated from `/api/winlink/aliases`); a
  connection-status dot reusing `.svc-dot`.
- **Middle:** message list for the selected box (from / subject / date), click a
  row to open it.
- **Right:** reading pane. A **Compose** button swaps it for a To / CC / Subject
  / Body form with **Send**. Send POSTs to the outbox; the message is *queued*,
  and the UI explicitly nudges the user to **Connect** to actually transmit
  (this mirrors how Winlink works — composing ≠ sending).

Collapses to a single column on narrow screens. Page-specific CSS lives in a
`<style>` block in the page, as in the sibling `winlink/` pages.

## 6. Live session panel

Pressing **Connect**:

1. opens the WebSocket to `ws://<host>:8082/ws`,
2. issues `GET /api/winlink/connect?url=<alias>`,
3. streams Pat's log lines / progress events into a collapsible console panel
   (monospace, `--panel-2` background),
4. reflects connected/dialing state on the status dot,
5. closes the socket cleanly on session end.

If the WebSocket cannot open, fall back to polling `/api/winlink/status` and
show a "live log unavailable" note.

## 7. Design-system mapping

Pure `css/common.css` tokens — no new fonts, no CDN, no build step:

- surfaces `--bg` / `--panel` / `--panel-2` / `--border`,
- amber field labels (`--amber`),
- primary action buttons in green accent (`--accent`, black text) matching
  `position-report.html`'s `button` rule,
- `--red` for delete,
- `--mono` font throughout.

## 8. Integration into `index.html`

- The `card-winlink` service card and the `nlink-winlink` nav item
  (`index.html:778`, `index.html:868`) point to the **new page**
  (`winlink/mail.html`) as the primary Winlink entry.
- Pat's raw UI (`:8082/`) is demoted to a secondary "Pat (raw client)" link
  under the Winlink nav group, kept as the fallback.

## 9. Error / degraded states

- **Pat down** → proxy returns `503`; the page shows an OASIS-styled banner:
  "Pat service unreachable — is the `pat` service running?" (reusing the
  APRS-proxy error-copy style).
- **WebSocket won't open** → silent fallback to status polling; banner notes
  "live log unavailable."
- **No transport aliases configured** → Connect disabled with a hint pointing at
  `winlink/radio-settings.html`.

## 10. Future: Winlink forms parity (documented, not built)

Pat ships an HTML **form-template** system (ICS-213, position reports, etc.)
exposed via the form catalog API:

- `GET /api/formcatalog` — list available form templates,
- the form HTML flow Pat uses to render a template, collect fields, and produce
  the message body/attachment.

A future iteration would add a "Forms" entry to the compose surface that:

1. lists templates from the catalog,
2. renders the selected template (OASIS-styled),
3. submits the completed form through the existing compose/queue path.

This should tie into OASIS's existing ICS pages under `static/ics-205`,
`static/ics-213`, `static/ics-214`, `static/ics-309` where the form semantics
overlap, so operators get one consistent ICS experience rather than two. File
attachments on compose would land in the same iteration (multipart upload
through the Flask proxy), with attention to Pi Zero memory on large files.

## 11. Testing

- **Flask proxy routes:** add to the existing app test pattern — stub Pat with a
  mock responder, assert pass-through status/body and `503`-on-down. Keep
  `/preflight` green (manifest self-tests + byte-compile + thin-CLI `--help`).
- **Manual:** `winlink/mail.html` against a live `pat http` on the Pi — list
  boxes, open a message, compose + queue, connect via the Telnet CMS alias,
  watch the live log, delete a message.
