# tsk.md — OASIS implementation tracker

Working tracker for in-flight OASIS features. Each step/phase is **independently
testable** — do one per session, verify, tick the boxes, stop. Nothing needs the
next item to be useful, so progress is never wasted.

Tracked here: **A) FT8 / WSJT-X** · **B) Dashboard service controls** (independent).

---

# Feature A — FT8 / WSJT-X

**Target hardware:** Raspberry Pi 4 / 4 GB (or CM4 stack) · WittyPi 3 RTC ·
GPS (USB first, I2C HAT later) · radio via IC-705 USB *or* DRA-Pi + FT-857D.

**Why this order:** time sync is the make-or-break for offline FT8, and it's
useful on its own (accurate clock for the whole suite), so it goes first.

---

## Phase 1 — Offline time discipline (GPS + chrony + RTC)  ⟵ START HERE

Goal: system clock within ~±1 s with **no internet**, surviving reboots.

- [ ] `sudo apt install gpsd gpsd-clients chrony` (note versions for the manifest later)
- [ ] Plug in **USB GPS**; find its device: `ls /dev/ttyACM* /dev/ttyUSB*`
- [ ] Point gpsd at it: set `DEVICES="/dev/ttyACM0"` in `/etc/default/gpsd`,
      `START_DAEMON="true"`; `sudo systemctl restart gpsd`
- [ ] Confirm a fix: `cgps -s` (or `gpspipe -w`) shows lat/lon + time, mode 2D/3D
- [ ] Feed chrony from gpsd shared memory: add to `/etc/chrony/chrony.conf`
      `refclock SHM 0 refid GPS precision 1e-1 offset 0.0 delay 0.2`
      (later, with a HAT PPS pin: add `refclock PPS /dev/pps0 lock GPS`)
- [ ] `sudo systemctl restart chrony`; verify:
      `chronyc sources` (GPS line, `*` selected) and
      `chronyc tracking` (System time offset well under 1 s)
- [ ] **RTC bridge:** confirm WittyPi RTC is read at boot and written back —
      `timedatectl` shows RTC time; `sudo hwclock -w` writes; reboot **with GPS
      unplugged and no network** → time is still correct
- [ ] **Done when:** offline reboot keeps good time, and with GPS attached
      `chronyc tracking` rides the GPS reference within FT8 tolerance.

Notes / blockers:
- (fill in device path, chrony offset seen, RTC drift observed)

---

## Phase 2 — WSJT-X + Hamlib install

Goal: WSJT-X and CAT control present, installable the OASIS way.

- [x] Write `scripts/install-wsjtx.py` + `scripts/common/wsjtx.py` (thin-CLI +
      logic, pattern: `install-rtl-sdr.py`) — idempotent, offline-first, `--check`.
      `--help` and `--check` verified on the dev box.
- [x] Add an `apt`-type feature to `scripts/offline-manifest.json` (`wsjtx`,
      `libhamlib-utils`, `gpsd`, `gpsd-clients`, `chrony`), suite-aware. Manifest
      validates and `manifest.apt_packages('wsjtx', ...)` reads correctly;
      `/preflight` green.
- [ ] **(Pi, tomorrow)** Bench-install: `python3 scripts/install-wsjtx.py`, then
      confirm `wsjtx --version` / `rigctld --version` run.
- [ ] **(Build)** Run `create-oasis-offline.py --check` to confirm the wsjtx set
      resolves offline (heads-up: WSJT-X pulls a large Qt closure — the bundle
      grows; the apt path is what tomorrow's connected-Pi test uses).
- [ ] **Done when:** `install-wsjtx.py` installs clean on a fresh Pi 4 and
      `--check` reports all four tools present.

---

## Phase 3 — Rig wiring

Do the **IC-705 USB** path first (simplest; its GPS also feeds Phase 1).

- [ ] IC-705: one USB cable → find audio card (`arecord -l` / `/api/audio`),
      CAT serial (`/dev/ttyACM*`), and GPS NMEA (route to gpsd in Phase 1)
- [ ] WSJT-X config: Radio = Icom IC-705 (Hamlib), Audio In/Out = IC-705 USB
      CODEC, PTT = CAT
- [ ] Decode live FT8; test TX into a **dummy load**
- [ ] DRA-Pi + FT-857D (second path): DRA audio + PTT line; FT-857D CAT via FTDI
      (Hamlib model "FT-857"); PTT via CAT or DRA. **Note:** shares the DRA sound
      card with GrayWolf → stop `graywolf` before FT8 (mutually exclusive)
- [ ] **Done when:** at least the IC-705 path decodes and keys reliably.

---

## Phase 4 — Browser / kiosk UI

WSJT-X is a Qt desktop app. Pick one (CM4 + big screen → kiosk is simplest):

- [ ] **Option A — kiosk:** launch WSJT-X on the local display via the existing
      `enable-autostart-pi.py` kiosk path
- [ ] **Option B — noVNC tile:** `Xvfb` + `x11vnc`/`wayvnc` + `websockify`/noVNC,
      embedded as a dashboard card (keeps the "any browser on the LAN" model)
- [ ] **Done when:** WSJT-X is reachable the chosen way without a keyboard/mouse
      hunt.

---

## Phase 5 — Dashboard integration

- [ ] Expose chrony state: extend `/api/system` (or new `/api/time`) with
      GPS-locked? / reference source / clock offset (read `chronyc tracking`)
- [ ] Add a **CLOCK / TIME-SYNC indicator** to the stats bar:
      green = GPS-locked & offset OK · amber = coasting on RTC · red = no sync.
      (Critical: tells the operator when FT8 timing is trustworthy.)
- [ ] `system/setup.html` checks: `wsjtx`, `rigctld`, `chrony`/`gpsd`, GPS lock
- [ ] Reuse `/api/audio` so the setup page names the right `hw:N,0` card
- [ ] **Done when:** dashboard shows trustworthy/untrustworthy time at a glance.

---

## Phase 6 — Bundle + docs

- [ ] Confirm the Phase-2 manifest entries vendor into the offline bundle
      (`create-oasis-offline.py` build, then `--check`)
- [ ] `docs/SETUP.md`: new "Digital Modes (FT8 / WSJT-X)" section (mirror the
      GrayWolf/DRA sections — wiring, time sync, audio device, contention notes)
- [ ] Remove the FT8 item from `docs/whats-next.md` (completed-items-are-removed)
- [ ] `/preflight` green; commit on a branch.

---

# Feature B — Dashboard Service Controls

Start/stop/restart OASIS systemd services from the dashboard — to free CPU/RAM
(stop Kiwix when idle) and resolve RTL-SDR contention (GrayWolf-APRS vs OpenWebRX
can't share one dongle). Independent of Feature A; do anytime.
**No passwords in the browser — the OS authorizes (polkit/sudoers); the UI only
confirms intent.**

Allowlisted units (start/stop/restart only): `graywolf`, `graywolf-api`,
`kiwix`, `ttyd`, `pat`, `aprs-sdr-feed`, `openwebrx`. **Excluded: the OASIS web
service itself** — stopping it kills the dashboard with no way back.

- [x] **SC-1 — Authorization (opt-in).** Written + `--help`/compile verified;
      installs/validates the scoped sudoers rule on the Pi (run there to activate).
      `scripts/enable-service-controls.py`
      installs a narrow polkit rule (or `sudoers` NOPASSWD) letting the OASIS user
      run *only* `systemctl start|stop|restart <allowlisted unit>`. Off by default.
      Verify: as the service user, `systemctl restart graywolf` needs no password;
      a non-allowlisted unit is refused.
- [x] **SC-2 — Backend.** Done + validation matrix verified (bad/protected unit
      → 403, bad action → 400, missing CSRF header → 403, valid→200).
      `POST /api/service {unit, action}` in `server/app.py`:
      validate `unit` ∈ allowlist (reject others **and** `oasis`), `action` ∈
      {start, stop, restart}; run `systemctl`; return the unit's new state.
      Same-origin + POST only (no GET, so a link can't fire it). Verify with curl:
      toggles an allowlisted unit; bad unit → 403; `oasis` → 403; GET → 405.
- [x] **SC-3 — Frontend (index.html).** START/STOP control (text label, readable
      on a wall) on the 5 controllable cards (gw, aprs, winlink, kiwix, webssh),
      confirm-on-stop, re-polls the card after acting. *Browser-verify on the Pi.*
- [ ] **SC-3b — Mirror to `index7.html`** (the 7-inch host screen — its own
      compact card layout).
- [ ] **SC-4 — RTL-SDR awareness (v2, optional).** Starting `openwebrx` offers to
      stop `aprs-sdr-feed` first, and vice versa (one dongle, one consumer).
- [ ] **SC-5 — Docs + gate.** `system/setup.html` shows whether controls are
      enabled; SETUP.md note; `/preflight` green.
- [ ] **Done when:** from the dashboard you can stop Kiwix and flip between the
      APRS feed and OpenWebRX — no shell, no password in the browser.

---

## Out of scope here (separate roadmap items)
- Alert-only update-check cron + image rollback
- Document the two install paths (online vs offline bundle) in SETUP.md
