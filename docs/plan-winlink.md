# Plan — Winlink from a webpage (Pat) on OASIS

Living plan to add **Winlink send/receive from the browser** to OASIS. Tracks the
solution, decisions, deliverables, and open questions. Nothing here is built yet
— this is the design we agree on before writing code.

*Started 2026-06-19. Status: PLANNING — Phase 1 scope locked, ready to build.*

### Decisions locked (2026-06-19)

- **Transport:** Telnet MVP first, then off-grid **packet/AX.25 reusing GrayWolf
  as the modem** (its KISS TNC) in Phase 2 — **no Direwolf, no second modem**.
  VARA not pursued.
- **Winlink account:** exists for **W4MHI**, password available → goes in
  `config.json` (mode 600).
- **Port:** Pat web UI on **:8082** (free; 8080 graywolf, 8081 kiwix, 8083
  flask, 8085 aprs_api, 7681 webssh).

---

## Goal

From the OASIS dashboard, open a web page and **send / receive Winlink messages**
(radio email) — ideally off-grid over RF, but at minimum over an internet gateway.

## The solution: Pat

**[Pat](https://getpat.io)** (`la5nta/pat`) is *the* answer to "Winlink from a
webpage." It's a cross-platform Winlink client written in Go with a **built-in,
mobile-friendly web UI** — compose, read, and send Winlink mail from a browser.
Current release: **v1.0.0**.

Why Pat fits OASIS perfectly:

- **Single static Go binary**, shipped as `.deb` for exactly our arches —
  `linux_amd64`, `linux_arm64`, `linux_armhf`, `linux_i386` — so the installer
  mirrors `install-graywolf.py` (download the matching `.deb` from GitHub
  releases, offline-first from a bundled copy).
- **Web UI** (`pat http`) — same "service + dashboard card" shape as GrayWolf
  and webssh. OASIS links to it; no embedding needed.
- **Multiple transports**: `telnet` (internet gateway), `ax25` (packet),
  `ardop`, `varahf` / `varafm`, `pactor`.
- **Config is a single JSON** (`~/.config/pat/config.json`) — we can template it
  like every other OASIS service.

Operator callsign: **W4MHI** (Mihai) — pre-fill in config.

---

## The key decision: which transport?

This is what we need to agree on first, because it drives the hardware and the
effort. "Send Winlink from a webpage" splits into two very different things:

| Transport | Radio needed | Effort | Off-grid? | Notes |
|---|---|---|---|---|
| **Telnet** | none (internet) | **Low** | ✗ needs internet | Connects to Winlink CMS over IP. Always works. The MVP. |
| **Packet / AX.25 (VHF) via GrayWolf KISS** | TX-capable radio + DRA-Pi-Zero | Medium | ✓ | **Reuses GrayWolf as the modem — no second modem.** Pat (kernel AX.25) → `kissattach` → socat pty↔TCP → GrayWolf KISS TCP. Connects to a local RMS Packet gateway in VHF range. |
| ~~Packet via Direwolf~~ | — | — | — | **Rejected** — would add a 2nd modem competing for the one radio. GrayWolf already does connected-mode AX.25 via its KISS TNC. |
| **VARA HF / FM** | TX radio + sound i/f | High | ✓ | Best throughput, most popular Winlink RF mode. BUT VARA is **closed-source Windows** → runs under **Wine** on Pi (heavy, fiddly, licensing). Not pursued. |
| **ARDOP (HF)** | HF radio + sound i/f | Medium-High | ✓ | Open modem (`ardopcf`) runs on Linux. Needs HF rig. Not pursued for now. |

### Why GrayWolf KISS, not Pat's AGWPE

GrayWolf exposes both an **AGWPE** and a **KISS** TNC, but they differ in what
they carry:

- **GrayWolf AGWPE** implements only **Monitor + Unproto(UI) + Raw** frames — not
  the AGWPE connected-mode `C`/`D` frames. Pat's native `agwpe` transport expects
  those connected-mode frames, so **Pat→GrayWolf over AGWPE won't establish a
  connected session.** (Winlink Express works over AGW because it builds raw
  AX.25 itself; Pat's `agwpe` doesn't.)
- **GrayWolf KISS** is frame-transparent — it passes raw AX.25 both ways. The
  connected-mode state machine runs in the **client** (the Linux kernel AX.25
  stack), so Winlink connected sessions work. This is the path for Pat.

### Proposed phasing

- **Phase 1 — Pat + Telnet (MVP).** Install Pat, configure callsign + Winlink
  password, run `pat http` on **:8082** as a systemd service, add a dashboard
  card. Send/receive Winlink email from the browser over internet. **Always
  works, lowest risk — this is the first deliverable.**
- **Phase 2 — RF transport (off-grid).** Packet/AX.25 over VHF **reusing
  GrayWolf's KISS TNC** as the modem (see arbitration section — single modem, so
  no service-kill conflict). Bridge GrayWolf's KISS TCP to a kernel AX.25 port
  for Pat. **Verify connected-mode end-to-end to a real RMS on-air.**

---

## Architecture (Phase 1)

```
Browser ──http──> Pat web UI (:8082) ──Telnet/IP──> Winlink CMS ──> email / Winlink
                       ▲
                       │ linked from
                  index.html card  (status via server-ports.json)
```

Same pattern as GrayWolf/webssh: a systemd service runs the web UI; the OASIS
Flask server advertises the port via `/server-ports.json`; `index.html` shows a
card that opens it and turns red when unreachable.

---

## Deliverables / checklist

### Phase 1 — Pat + Telnet

- [ ] **`scripts/install-pat.py`** — install Pat from bundled `.deb`
      (`offline-packages/pat/`) or download from GitHub releases. Mirror
      `install-graywolf.py`. Version-aware (no downgrade).
- [ ] **oasis_lib helpers** — `pat_find_local`, `pat_latest_release`,
      `pat_download_deb` (or **generalize** the GrayWolf GitHub-release-`.deb`
      helpers into a shared `github_release_deb(...)` — this is the 2nd such
      installer, so factoring it is the consistent move).
- [ ] **`create-oasis-offline.py` Phase 7** — download Pat `.deb` for arm64,
      armhf, amd64 into `oasis-offline/offline-packages/pat/`. Add to `--check`.
- [ ] **Configuration** — write `~/.config/pat/config.json` (mode 600):
      `mycall=W4MHI`, `locator` (Maidenhead), `secure_login_password` (Winlink
      password — prompt), `http_addr=":8082"`, telnet connect alias. A
      `--configure` flow or a small `configure-pat` step.
- [ ] **systemd unit** `pat.service` → `pat --listen ... http -a 0.0.0.0:8082`
      (bind LAN; or 127.0.0.1 + note). Enable on boot.
- [ ] **Port registration** — add `"pat": 8082` to the ports map in
      `server/app.py` (the `/api/config` + `/server-ports.json` payload).
- [ ] **index.html dashboard card** — "Winlink (Pat)" card that opens
      `http://<pi>:8082`, red when down (copy the GrayWolf/webssh card pattern).
- [ ] **Docs** — `docs/SETUP.md` Winlink section + a `docs/winlink-pat.md`
      writeup (config, telnet test, gotchas), like `sdr-to-graywolf.md`.

### Phase 2 — RF packet via GrayWolf's KISS TNC, later

- [ ] Enable GrayWolf's **KISS TCP** interface (default :8071) on the DRA channel.
- [ ] Bridge it to a kernel AX.25 port: `socat` PTY ↔ `TCP:127.0.0.1:8071`, then
      `kissattach` the pty to an `axports` entry (e.g. `wl2k`). Script + systemd.
- [ ] Pat `ax25` transport + a connect alias to a local RMS Packet gateway.
- [ ] **Verify connected-mode end-to-end on-air** to a real RMS (this is the
      unproven link — KISS is transparent and GrayWolf does connected-mode, but
      confirm the full Pat→kissattach→GrayWolf→RMS path).
- [ ] Document the RF setup; note it reuses GrayWolf (no second modem) and the
      operational frequency switch (APRS 144.390 ↔ RMS freq).

---

## Radio arbitration — mostly dissolved (single modem)

Earlier worry: GrayWolf (APRS) and a second modem (Direwolf) fighting over the
one DRA-Pi-Zero sound card + PTT, needing systemd `Conflicts=` to keep them
apart. **That's gone** — GrayWolf does connected-mode AX.25 via its own KISS TNC,
so Winlink packet **reuses GrayWolf as the modem**. There is only one modem. No
two-process ALSA fight, no `Conflicts=` units, no service-kill toggle.

What's left is just **physics**, not software:

- **One radio, half-duplex, one frequency at a time.** APRS lives on 144.390; a
  Winlink RMS Packet gateway is on its own frequency. To run a Winlink session
  you tune the radio to the RMS frequency — APRS simply isn't being heard while
  you're parked there. When done, tune back to 144.390.
- GrayWolf keeps running throughout (it's the modem for both); Pat connects to
  GrayWolf's KISS port for the session.
- The RTL-SDR `sdr_udp` feed is a **separate** device on 144.390 — unaffected.

So the "mode" is **operational**, not a managed service state. Optional polish:
a dashboard indicator of what the radio is doing (APRS-monitoring vs
Winlink-session), but **no** machinery to kill one service for another.

**Phase 1 split to preserve:** Pat's web UI + Telnet need no radio and run
always-on. Only the *RF connect* uses GrayWolf's KISS. Keep that separation so
Phase 2 is purely additive.

---

## Open questions / decisions

*Resolved: transport (Telnet→packet/Direwolf), Winlink account (have it), port
(8082), radio arbitration (systemd `Conflicts=` + dashboard mode toggle, APRS
default) — see above.*

Still open (none block Phase 1):

1. **Web UI exposure.** Bind Pat to the LAN (`0.0.0.0:8082`) like GrayWolf, or
   localhost-only? Plain HTTP — keep on trusted LAN. Default: LAN, matching
   GrayWolf.
3. **Forms/templates.** Pat's Winlink form support is limited vs. Winlink
   Express. OASIS already ships static ICS forms — possible bridge later; out of
   scope for now.

---

## Notes / constraints

- **RX-only SDR can't do Winlink.** Winlink is two-way — the RTL-SDR feed
  (`sdr-to-graywolf.md`) is monitor-only and irrelevant here. RF Winlink needs a
  TX-capable radio (the DRA path).
- **Security.** Pat serves plain HTTP and `config.json` holds the Winlink
  password (mode 600). Keep the UI on the trusted LAN; don't port-forward
  without TLS. Same posture as webssh.
- **Pi 1 / Zero (armv6).** Pat's `armhf` build is the closest; verify it runs on
  armv6 if that hardware is in play (matches the RTL-SDR script's armv6 caveat).

---

## Reference

- Pat homepage: <https://getpat.io>
- Pat source + releases: <https://github.com/la5nta/pat>
- Pat v1.0.0 assets: `pat_1.0.0_linux_{amd64,arm64,armhf,i386}.deb`
- Pat on Raspberry Pi (community guides):
  <https://k0swe.radio/pipat> ·
  <https://rockfloat.com/ham/pat_linux/index.html>
- Pat AGWPE-over-TCP support (and why connected-mode matters):
  <https://github.com/la5nta/pat/pull/389> ·
  <https://github.com/la5nta/pat/issues/171>
- GrayWolf TNC interfaces (local handbook): KISS TCP (default :8071), AGWPE
  (:8000, Monitor/Unproto/Raw only), AX.25 Terminal (connected-mode) —
  `static/graywolf-handbook/{kiss,agwpe,ax25-terminal}.html`
- AX.25 helper units shipped with Pat:
  `/usr/share/pat/ax25/install-systemd-ax25-unit.bash`
