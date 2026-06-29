# APRS SDR Feed — flow meter (health monitor)

**Date:** 2026-06-28
**Status:** approved, implementing

## Problem

The dashboard's **APRS SDR Feed** tile reports UP purely from `systemctl is-active`
on `aprs-sdr-feed.service`. That unit is `rtl_fm … | socat …`. When the RTL-SDR
dongle is yanked from USB, `socat` (the tail of the pipe) can stay alive and the
unit stays "active" while **zero audio flows** to GrayWolf. GrayWolf raises no
error — it just receives nothing. Symptom observed 2026-06-27: tile green,
stations silently stopped arriving.

Liveness (process running) ≠ health (data flowing). We want a data-flow signal.

## Decisions

- **Signal:** packet flow / liveness (UDP datagram rate on the feed port), not
  true audio amplitude. Cheap, no audio decode, nails the yanked-dongle case.
- **Mechanism:** passive `tcpdump` capture on `lo` (libpcap copies datagrams; it
  does not steal them from GrayWolf — a second `bind()` on 7355 would). Privilege
  via the existing scoped-sudoers pattern (`enable-service-controls.py`).
- **Cadence:** live ~2 s while the dashboard tab is visible (paused when hidden).
  Implemented as a single constant + visibility guard so retuning to 30 s, folding
  into `pingAll`, or an on-demand burst is a trivial change.

## Architecture

### Backend — `GET /api/health/feed-flow` (`server/app.py`)

- Runs a **pinned argv** (no shell): `sudo -n <tcpdump> -ni lo -l -c 10 udp port 7355`,
  wrapped in a ~1.0 s subprocess timeout.
- Counts captured packets (one stdout line per packet); `pps = packets / elapsed`.
  - Healthy (~50 pkt/s): collects 10 packets in ~200 ms → returns fast.
  - Dead: 0 packets → returns at the ~1.0 s timeout with `flowing:false`.
- Returns `{ ok, supported, port, packets, pps, nominal_pps, flowing }`, or
  `{ ok:false, reason:"not-linux"|"tcpdump-missing"|"no-privilege"|"probe-error" }`.
- The client never supplies the port to the privileged path — the port (7355) is
  baked into both the argv and the sudoers rule, so there is no injection surface.

### Privilege — `scripts/enable-service-controls.py`

- Add a second `Cmnd_Alias OASIS_SNIFF` pinning the **exact** tcpdump argv, granted
  alongside `OASIS_SVC` in the same NOPASSWD line. Resolved tcpdump path baked in
  at install time; `--check` reports it. The argv string MUST match `app.py`
  token-for-token or `sudo -n` denies it.

### Frontend — feed tile (`index.html`)

- Add a thin meter (track + fill + `N pkt/s` label) inside the APRS SDR Feed card.
- `pollFeedFlow()` every `FEED_FLOW_MS = 2000`, gated on `visibilityState` and on
  the tile already being active (`_svcStates['feed']`); skips the probe when the
  unit is stopped/not-installed (a stopped feed is legitimately 0 pps, not SILENT).
- States layered on the existing systemd gate:
  - not active → STOPPED / not installed (unchanged), meter hidden.
  - active + flowing → green `→ GrayWolf · N pkt/s`, bar = `pps / nominal_pps`.
  - active + 0 pkt/s → **SILENT** (amber) `active · no audio (check SDR/USB)`, bar flat.
- The 30 s `feed.check()` consults the last flow result so it doesn't flash green
  over a SILENT feed on each slow refresh.

## Error handling (meter never breaks the tile)

- non-Linux → `supported:false` → "Linux only" (as today), no meter.
- tcpdump missing → muted "flow check n/a"; systemd UP still shows.
- `sudo -n` denied → "flow: enable controls" (run `enable-service-controls.py`).
- timeout with 0 packets is a **valid SILENT result**, not an error.

## Testing

- Backend unit test mocks subprocess: (a) fast N packets → pps/flowing, (b)
  timeout/0 → SILENT, (c) tcpdump missing → reason, (d) argv is the pinned form.
- Manual on Pi: `start.sh`, open dashboard, pull SDR → bar → SILENT within ~2 s;
  restart feed → recovers. Watch CPU for the 2 s cadence.

## Files

- `server/app.py` — endpoint + `FEED_FLOW_*` constants.
- `scripts/enable-service-controls.py` — `OASIS_SNIFF` sudoers rule + `--check`.
- `index.html` — meter markup/CSS, `pollFeedFlow()`, visibility-gated timer.
- `system/setup.html` — the RTL-SDR check now probes flow: an active-but-0-pkt/s
  feed reads ⚠ ("0 pkt/s — no audio reaching GrayWolf") instead of a false ✓.
- `server/tests/test_feed_flow.py` — endpoint test (6 cases).
- `docs/graywolf-rtl-sdr.md` — note the dashboard now shows live feed flow.
- Offline bundle (`oasis-offline/`) regenerates from source via `create-oasis-offline.py`.
