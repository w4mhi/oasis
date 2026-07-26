# OASIS Dashboard kiosk

A self-contained **touchscreen dashboard** — an alternative to the full desktop
dashboard (`/index.html`) for dedicated panels. It's a touch-first layout with
its own dark palette; nothing scrolls but the station table.

The layout is **fluid** and ships in two panel resolutions:

| Resolution | Panel | Reference display |
|---|---|---|
| `800x480` | 7″ touchscreen | **BigTreeTech 7″** (also fits the official Raspberry Pi 7″ DSI panel or any 800×480 display) |
| `1920x1200` | 10″ wide panel | a 10″ 1920×1200 touchscreen |

Everything is sized in rem and the root font-size tracks viewport height, so the
same 3-zone layout grows proportionally between the two. The chosen resolution is
an **explicit flag** (no viewport sniffing): the kiosk autostart launches Chromium
with `?res=<WxH>`, which the page applies as a `[data-res]` attribute on `<html>`
and persists to `localStorage.oasis_layout`.

## Files

| File | What it is |
|---|---|
| `dashboard.html` | The kiosk page. All logic is inline; loads `kiosk.css` and the shared `common/js` formatters (`units.js`, `geo.js`, `format.js`). |
| `kiosk.css` | Standalone stylesheet — **intentionally detached** from `css/common.css` (own palette, never uses the light/dark theme toggle). |
| `uninstall.py` | Removes the Chromium kiosk autostart and the stored layout override (and deregisters the legacy `pi-small-screen-7` key). |

## Install

Pick it in `setup-oasis.py` → **Kiosk mode — OASIS Dashboard** (7″ 800×480 or
10″ 1920×1200), or run it directly:

```bash
python3 scripts/enable-autostart-pi.py --resolution 800x480     # 7″ touchscreen
python3 scripts/enable-autostart-pi.py --resolution 1920x1200   # 10″ wide panel
```

That writes `/usr/local/bin/oasis-browser-launch` plus the desktop autostart, so
on boot Chromium opens fullscreen at `/oasis-dashboard/dashboard.html?res=<WxH>`
with touch + Pi-tuned flags (GPU rasterisation, crash-loop/CPU mitigations, cursor
hidden via CSS). Uninstall with `python3 oasis-dashboard/uninstall.py`.

> The deprecated `--7inch` flag still works as an alias for `--resolution 800x480`.

## BigTreeTech 7″ RTC (PCF8563)

The BigTreeTech panel has an **onboard PCF8563 real-time clock** at `0x51` on the
**DSI ribbon's I²C bus** (`i2c-10` / `i2c_csi_dsi`) — *not* the GPIO header, which
is why it never shows on `i2cdetect -y 1`. Enable it so the Pi keeps time offline
across reboots / power loss:

```bash
python3 features/rtc-hat/enable-rtc.py --board bigtreetech-7in   # then reboot
```

or the setup item **BigTreeTech 7″ RTC (PCF8563)**. This is independent of the
kiosk layout — other panels won't have this RTC.

## Notes for developers

- `dashboard.html` sets `<base href="/">` so every relative URL resolves from the
  site root, not this folder.
- `theme.js` deliberately **skips `/oasis-dashboard/`** — the kiosk owns its palette.
- An early `<head>` script resolves the resolution (`?res=` → `localStorage` →
  default `800x480`, with legacy `"7inch"` mapping to `800x480`), sets
  `[data-res]` before paint, and persists `localStorage.oasis_layout`, so ⌂ HOME
  links across the suite route back to this page rather than the desktop dashboard.
- Poll cadences are deliberately conservative (coalesced APRS render, round-robin
  service pings, top-10 station cap) to keep Chromium CPU low on a Pi — see the
  bootstrap block at the bottom of `dashboard.html`.
