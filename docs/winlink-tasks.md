# Winlink Interface — Tasks & Roadmap

Tracks the OASIS-styled Winlink (Pat) mail client. Full design rationale lives in
`docs/2026-06-27-winlink-oasis-ui-design.md`. This is a living document maintained
incrementally — agents may update checkboxes and sections as work lands.

---

## Guardrails (hard constraints)

- **Offline-first.** Must run on a Raspberry Pi Zero 2 W (512 MB RAM). No CDNs, no
  external calls, no database engine.
- **NEVER auto-connect or transmit.** Connecting to a gateway transmits the station
  callsign, so it is only ever triggered by an explicit user click with an explicitly
  chosen transport. Reading the LOCAL mailbox (already-downloaded messages) is passive
  and fine.
- **Deploy target ≠ dev repo.** The live service on the Pi runs the packaged copy at
  `/home/mihaim/oasis-offline/`, not the dev repo. Deploy = copy changed files there +
  restart the `oasis` service. Restart is needed only for `server/app.py` changes;
  static HTML/JS just needs a browser refresh.

---

## Done

- [x] Flask same-origin proxy `/api/winlink/*` (mailbox list/read/delete, compose→outbox,
  status, aliases, connect) — `server/app.py`
- [x] Restart port-drift fix: `resolve_port()` honors `OASIS_PORT`, probes with
  `SO_REUSEADDR`, waits for 8083 to free instead of drifting to 8084 — `server/app.py`
- [x] OASIS-styled mail client — `winlink/mail.html` (layout: Mailboxes + Inbox on top,
  message window full-width below; scrollbars on inbox list and message body)
- [x] Shared read/unread state module (seen/read sets in localStorage) —
  `winlink/read-state.js`, used by both the page and the dashboard
- [x] Unread = amber left accent; fades to green after a 3s read-dwell (dwell resets when
  switching messages)
- [x] Folder badges show unread/total; live decrement on read
- [x] Dashboard Winlink card shows inbox unread count (read-only local mailbox read; no
  connect) — `index.html`
- [x] Connect UI: Quick connect (saved aliases, e.g. telnet/CMS) + New connection transport
  builder (transport/gateway callsign/freq). No auto-connect — manual + explicit transport
  only
- [x] Privacy: ✕ in the message corner clears the reading pane (ephemeral, on-screen only;
  does not touch Pat's stored copy)
- [x] Mark all read (per folder) + per-message Mark read/unread toggle
- [x] Body search — search also matches message bodies via a background prefetch into an
  in-memory `_bodyCache` (never localStorage); sequential + cancellable (generation token,
  superseded on box switch); reuses bodies fetched when a message is opened; "scanning
  message text…" hint while a search loads. Verified `GET …/mailbox/{box}/{mid}` does NOT
  mark a message read in Pat, so prefetch has no side effects — `winlink/mail.html`
- [x] Reply / Reply-all / Forward — Compose pre-filled (To=sender, RE:/FW: subject, quoted
  body); Reply-all Cc excludes the sender + own callsign; Forward is text-only (no
  attachments) — `winlink/mail.html`
- [x] Search + "Unread only" filter — client-side filter of the current folder by subject
  and callsign; folder counts stay whole-box; Mark-all-read covers the whole box —
  `winlink/mail.html`
- [x] Tests: `server/tests/test_winlink_proxy.py`, `server/tests/test_port_resolution.py`
  (plain unittest, run via `.venv/bin/python`)

---

## Planned

- [ ] **ICS form rendering (forms parity, the big one).** The inbox messages are ICS-213
  with EMPTY bodies — real content is in attached `RMS_Express_Form_*.xml`. Render that XML
  into a readable form view; tie into existing `static/ics-213` styling. Documented as the
  parity roadmap in the design spec. Pure local rendering (no transport).
- [ ] Attachment open/download via Pat's attachment endpoint (companion to ICS rendering)
- [ ] Abort an in-progress connect session (Pat disconnect endpoint) + better live-session
  state
- [ ] Print / ICS paper-trail view of a message
- [ ] Tab title / favicon unread badge (e.g. "(3) Winlink Mail")
- [ ] Keyboard shortcuts (j/k navigate, Enter open, delete)
- [ ] Position-report message → map integration (ties to `winlink/position-report.html`,
  `winlink/to-position.html`)
- [ ] Compose niceties: draft autosave, recipient autocomplete from local history

---

## Notes / open questions

- Pat field shapes are read defensively in the front-end; verify against the installed Pat
  version when adding features that bind new fields.
- Forward currently won't carry attachments (compose is plain text in v1) — revisit with the
  attachment work.
