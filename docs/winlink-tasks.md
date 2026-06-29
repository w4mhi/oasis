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
- [x] Connect UI — single-line session bar: dot + state + one **connection dropdown** (saved
  aliases + recent connections + "Custom…" raw-URL escape hatch) + Connect + Abort + Refresh.
  Last connection persists (`oasis_winlink_lastconn`) and is pre-selected; recents in
  `oasis_winlink_recent`. The old 3-field transport builder was dropped — RF target-picking
  now lives in the Gateways panel. No auto-connect (explicit click only) — `winlink/mail.html`
- [x] Privacy: ✕ in the message corner clears the reading pane (ephemeral, on-screen only;
  does not touch Pat's stored copy)
- [x] Mark all read (per folder) + per-message Mark read/unread toggle
- [x] Body search — search also matches message bodies via a background prefetch into an
  in-memory `_bodyCache` (never localStorage); sequential + cancellable (generation token,
  superseded on box switch); reuses bodies fetched when a message is opened; "scanning
  message text…" hint while a search loads. Verified `GET …/mailbox/{box}/{mid}` does NOT
  mark a message read in Pat, so prefetch has no side effects — `winlink/mail.html`
- [x] **RMS gateway list (offline-cacheable).** Collapsible "📡 Gateways" panel in
  `mail.html`: pick a mode (packet/ardop/vara/varahf/varafm/pactor), "⬇ Update list"
  downloads while online, cached per-mode in `localStorage` (`oasis_winlink_rms_<mode>`) for
  full offline search/sort, one-click Connect per gateway via its ready-made Pat `url`.
  Backend `GET /api/winlink/rmslist?mode=` strips the per-gateway VOACAP `output_raw`
  (keeps `link_quality`): HF e.g. ardop 2.5 MB → 105 KB (96% smaller). Mode allowlisted
  (no-mode call hangs). Source is Pat's web API → needs connectivity at download time —
  `server/app.py`, `winlink/mail.html`; tests in `test_winlink_proxy.py`.
- [x] Abort an in-progress connect session — ■ Abort button (shown during a session) hits
  Pat's `/api/disconnect`, which unblocks the connect — `server/app.py`, `winlink/mail.html`
- [x] Position-report → OFFLINE map — messages carrying `Lat:/Lon:` get a "📍 Show on map"
  link to `aprs/map.html?center=lon,lat` (OASIS's own map, not an online service) —
  `winlink/mail.html`
- [x] Compose niceties — draft autosave (single draft in `oasis_winlink_draft`, restored on
  blank compose, cleared on send/discard) + recipient autocomplete (`<datalist>` from
  `oasis_winlink_addrs`, harvested from message From/To/Cc) — `winlink/mail.html`
- [x] Attachment open/download — reader shows each attachment as a view link + ⬇ download;
  Flask attachment proxy passes Pat's Content-Type through (not forced JSON), `?download=1`
  sets Content-Disposition — `server/app.py`, `winlink/mail.html`; tests in
  `test_winlink_proxy.py`
- [x] Body entity-decode — numeric HTML char refs (e.g. `&#10146;`) in message bodies are
  decoded to their symbols before display — `winlink/mail.html`
- [x] Reply / Reply-all / Forward — Compose pre-filled (To=sender, RE:/FW: subject, quoted
  body); Reply-all Cc excludes the sender + own callsign; Forward is text-only (no
  attachments) — `winlink/mail.html`
- [x] Search + "Unread only" filter — client-side filter of the current folder by subject
  and callsign; folder counts stay whole-box; Mark-all-read covers the whole box —
  `winlink/mail.html`
- [x] Compose `date` fix — `sendMessage` now sends the RFC3339 date Pat requires (was missing
  it; would have 400'd against real Pat). Verified posting against live Pat — `winlink/mail.html`
- [x] Compose attachments — `sendMessage` sends multipart (`files` field) when an attachment is
  pending; reader-style indicator + remove. Proxy already forwards multipart — `winlink/mail.html`
- [x] **Form → Winlink (ICS-213).** "→ Send via Winlink" button on `static/ics-213/ics-213.html`
  builds a Winlink-style text body + `RMS_Express_Form` XML (matched to a real received sample),
  hands `{to,subject,body,attachment}` to `mail.html` via `localStorage.oasis_winlink_compose`,
  which opens Compose pre-filled + attached. All offline (shared localStorage). Verified
  end-to-end against live Pat: posts (HTTP 201), attaches, retrievable. Sender call/grid from
  `oasis_callsign`/`oasis_grid`. Receiver-side interactive rendering still to confirm (see Planned).
- [x] **Form → Winlink: ICS-205 / 214 / 309.** Same mechanism as ICS-213: each form has a
  `→ Send via Winlink` button that builds a Winlink-style text body + `RMS_Express_Form` XML
  and hands `{to,subject,body,attachment(s)}` to `mail.html` via `localStorage`. Each XML
  generator was built field-for-field against a REAL received sample (forwarded by K7ISQ):
  `Form 309 v13.12` (`Form-309_Viewer.html`), `ICS205- v 19.5` (`ICS205 Radio Plan_Viewer.html`),
  `ICS 214 v 17.10` (`ICS214_Viewer.html`). Verified offline: each generator reproduces 100%
  of its sample's variable tags and emits well-formed XML. Notable mappings: **309** Winlink
  rows are Time/From/To/Subject (no Msg #), so OASIS Message→Subject and From/To Msg # are
  folded into the call sign (e.g. "KI7RMO #12"); **205** the combined "freq N/W" cell is split
  into Winlink's separate freq + N/W columns, op-period maps to Winlink's quirky
  datefrom/timefrom/dateto/timeto, and the channel-data CSV is attached alongside the XML;
  **214** op-period combines date+time. Receiver-side interactive rendering still to confirm
  (see Planned) — `static/ics-{205,214,309}/*.html` —
  files: `static/ics-309/ics-309.html`, `static/ics-205/ics-205.html`, `static/ics-214/ics-214.html`
- [x] **mail.html: multi-attachment handoff.** Compose handoff now accepts an `attachments:[…]`
  array (back-compat with single `attachment`); each is appended as a Pat multipart `files`
  field. Used by ICS-205 (XML + channel CSV) — `winlink/mail.html`
- [x] **Bug fix: ICS-309 dead script.** `ics-309.html` was missing its `function showToast(…)`
  declaration line — a SyntaxError that broke the *entire* `<script>` (Save PDF, CSV, autosave,
  Add Entry all dead). Restored the declaration — `static/ics-309/ics-309.html`
- [x] Tests: `server/tests/test_winlink_proxy.py`, `server/tests/test_port_resolution.py`
  (plain unittest, run via `.venv/bin/python`)

---

## Planned

- [ ] **Verify ICS-205/214/309 forms render on the receiving end** (same template caveat as
  ICS-213 below) — send one of each to self, open in Winlink Express, adjust XML if needed.
- [ ] **Verify ICS-213 form renders on the receiving end.** The compose chain is proven
  end-to-end against Pat (attaches + retrievable), but whether the XML renders as an
  *interactive* form in the recipient's Winlink Express depends on their Standard Templates
  (`display_form: ICS213_Initial_Viewer.html`). User to confirm by sending to self + opening
  in Winlink Express; adjust XML variables if needed.
- [ ] **ICS form rendering — PARKED after spike.** Originally "the big one," but a spike
  (2026-06-28) found Pat already renders ICS-213 into readable text in the message **Body**
  (the empty body was only the *list* endpoint; the individual GET has the full form). Our
  reader already shows it. So a custom XML→form renderer would be **cosmetic only** (a boxed
  "official form" layout over already-readable text). Revisit only if the printable official
  look is wanted. Form XML is at `…/mailbox/{box}/{mid}/RMS_Express_Form_*.xml`.
- [ ] Print / ICS paper-trail view of a message

---

## Notes / open questions

- **Form → Winlink (DONE for all four).** Each ICS form (205/213/214/309) has a "Send via
  Winlink" button → hands `{to, subject, body, attachment(s)}` to `mail.html` via localStorage
  (`oasis_winlink_compose`), which opens Compose pre-filled. Decision realized: **text body +
  real `RMS_Express_Form` XML attachment** (full fidelity). Each XML was built against a real
  received sample (213 from KI7RMO; 205/214/309 forwarded by K7ISQ, MIDs in the inbox
  2026-06-29) — none hand-rolled blind. Samples cached at `/tmp/winlink-samples/` during the
  build (not committed). 205 also attaches Winlink's channel-data CSV via the new
  multi-attachment handoff.
- **Spike (2026-06-28) — Pat compose API:** `POST /api/mailbox/out` requires a `date`
  (RFC3339, no fractional seconds; else `400 Missing date value`). Attachments use multipart
  field name **`files`** (multiple OK) — verified an XML attached. `in_reply_to` field exists
  for reply threading. Receiver-side interactive form rendering depends on that station having
  the matching Winlink Standard Templates (`display_form`); the XML always carries the data.
- **Bug fixed in spike:** `sendMessage` now sends `date` — compose was missing it and would
  have failed against real Pat (was only mock-tested before).
- Pat field shapes are read defensively in the front-end; verify against the installed Pat
  version when adding features that bind new fields.
- Forward currently won't carry attachments (compose is plain text in v1) — revisit with the
  attachment work.
