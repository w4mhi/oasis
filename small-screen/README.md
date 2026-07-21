# OASIS 7″ small-screen kiosk

A self-contained **800×480 touchscreen** dashboard — an alternative to the
full desktop dashboard (`/index.html`) for small panels. It's a fixed-size,
touch-first layout with its own dark palette; nothing scrolls but the station
table.

The layout is **generic 800×480**, not tied to one vendor. The reference /
tested panel is the **BigTreeTech 7″ touchscreen**, but it fits any 800×480
display — the official Raspberry Pi 7″ DSI panel included.

## Files

| File | What it is |
|---|---|
| `index7.html` | The kiosk page. All logic is inline; loads `kiosk.css` and the shared `common/js` formatters (`units.js`, `geo.js`, `format.js`). |
| `kiosk.css` | Standalone stylesheet — **intentionally detached** from `css/common.css` (fixed 800×480, own palette, never uses the light/dark theme toggle). |
| `uninstall.py` | Removes the Chromium kiosk autostart and the stored 7″ layout override. |

## Install

Pick it in `setup-oasis.py` → **Kiosk mode — 7″ touchscreen (BigTreeTech /
800×480)**, or run it directly:

```bash
python3 scripts/enable-autostart-pi.py --7inch
```

That writes `/usr/local/bin/oasis-browser-launch` plus the desktop autostart, so
on boot Chromium opens fullscreen at `/small-screen/index7.html` with
touch + Pi-tuned flags (GPU rasterisation, crash-loop/CPU mitigations, cursor
hidden via CSS). Uninstall with `python3 small-screen/uninstall.py`.

## BigTreeTech 7″ RTC (PCF8563)

The BigTreeTech panel has an **onboard PCF8563 real-time clock** at `0x51` on the
**DSI ribbon's I²C bus** (`i2c-10` / `i2c_csi_dsi`) — *not* the GPIO header, which
is why it never shows on `i2cdetect -y 1`. Enable it so the Pi keeps time offline
across reboots / power loss:

```bash
python3 features/rtc-hat/enable-rtc.py --board bigtreetech-7in   # then reboot
```

or the setup item **BigTreeTech 7″ RTC (PCF8563)**. This is independent of the
kiosk layout — other 800×480 panels won't have this RTC.

## Notes for developers

- `index7.html` sets `<base href="/">` so every relative URL resolves from the
  site root, not this folder.
- `theme.js` deliberately **skips `/small-screen/`** — the kiosk owns its palette.
- On load it sets `localStorage.oasis_layout = "7inch"`, so ⌂ HOME links across
  the suite route back to this page rather than the desktop dashboard.
- Poll cadences are deliberately conservative (coalesced APRS render, round-robin
  service pings, top-10 station cap) to keep Chromium CPU low on a Pi — see the
  bootstrap block at the bottom of `index7.html`.
