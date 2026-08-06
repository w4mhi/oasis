#!/usr/bin/env python3
"""
enable-dra-rx-led.py
--------------------
Light the DRA-Pi-Zero **green RX LED** (GPIO 16) on GrayWolf receive activity.

GrayWolf drives GPIO 12 for PTT (the red TX LED) but has no DCD→GPIO feature,
so the green carrier-detect LED on GPIO 16 stays dark even while RX works
(see docs/graywolf-dra-pi.md). This script installs a tiny daemon that pulses
that LED whenever GrayWolf logs a freshly-decoded packet.

RX signal source — the GrayWolf history DB (not the WebSocket):
  The daemon polls /var/lib/graywolf/graywolf-history.db (the same DB the
  GrayWolf APRS History API reads) for new rows in the `positions` table and
  pulses the LED on each new packet. This avoids depending on GrayWolf's
  WebSocket URL, which is unconfirmed in the docs, and needs no network.
  Note: the DB logs decoded APRS *position* packets, so the LED tracks decoded
  RX rather than raw carrier/COS. DB path is overridable with $APRS_DB_PATH.

The red TX LED (GPIO 12) needs NO script — GrayWolf already keys it via its PTT
config (method=gpio, gpio_line=12). Don't drive GPIO 12 from here too, or you'd
fight GrayWolf for the pin. See docs/graywolf-dra-pi.md §4 (GrayWolf PTT).

This single file is both the **daemon** and its **enabler** (same pattern as
enable-graywolf-api.py). The systemd unit runs it in `run` mode as root so it
can drive the GPIO (via pinctrl) and read the GrayWolf DB.

Usage:
  python3 features/dra-audio-interface/enable-dra-rx-led.py                # write + enable the service
  python3 features/dra-audio-interface/enable-dra-rx-led.py --no-enable     # write unit, don't start
  python3 features/dra-audio-interface/enable-dra-rx-led.py --gpio 16       # override LED GPIO (BCM)
  python3 features/dra-audio-interface/enable-dra-rx-led.py --uninstall     # stop + remove the service
  python3 features/dra-audio-interface/enable-dra-rx-led.py --self-test     # blink the LED 5x and exit
  python3 features/dra-audio-interface/enable-dra-rx-led.py run             # run the daemon (used by unit)

Exit codes: 0 = done · 1 = error.
Requires: Linux (Raspberry Pi) + systemd + sudo for the enable path; `pinctrl`
(ships with Pi OS Bookworm/Trixie) to drive the GPIO.
"""

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import time

SERVICE      = "dra-rx-led"
SERVICE_FILE = f"/etc/systemd/system/{SERVICE}.service"
SELF         = os.path.abspath(__file__)

# DB path is overridable for testing off-Pi (e.g. APRS_DB_PATH=./test.db).
DB_PATH          = os.environ.get("APRS_DB_PATH", "/var/lib/graywolf/graywolf-history.db")
LED_GPIO_DEFAULT = 16     # BCM GPIO 16 = header pin 36 = green CD/RX LED
PULSE_SEC        = 0.12   # how long each RX blink stays lit
POLL_SEC         = 0.3    # DB poll cadence — responsive but light on the Pi
RECONNECT_SEC    = 3      # wait before retrying when the DB is missing/locked


# ── GPIO (via pinctrl, matching docs/graywolf-dra-pi.md) ────────────────────────
def _pinctrl():
    return shutil.which("pinctrl")


def led(gpio, on):
    """Drive the LED pin high (on) or low (off). 'op dh' = output high."""
    subprocess.run(["pinctrl", "set", str(gpio), "op", "dh" if on else "dl"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def pulse(gpio):
    led(gpio, True)
    time.sleep(PULSE_SEC)
    led(gpio, False)


# ── GrayWolf history DB (read-only poll) ────────────────────────────────────────
def _open_db(path):
    """Open the GrayWolf DB read-only. GrayWolf is the writer (WAL mode)."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    con.execute("PRAGMA query_only=ON")
    return con


def _max_packet_id(con):
    """Highest rowid in `positions` — increases by one per decoded packet."""
    row = con.execute("SELECT MAX(id) FROM positions").fetchone()
    return (row[0] or 0) if row else 0


def run_daemon(gpio, db_path):
    """Pulse the LED on each new GrayWolf packet. Blocking; what the unit execs."""
    if _pinctrl() is None:
        print("ERROR: 'pinctrl' not found — install Pi OS GPIO tools "
              "(it ships with Bookworm/Trixie).", file=sys.stderr)
        return 1

    print(f"[dra-rx-led] LED GPIO {gpio} · DB {db_path}", flush=True)
    led(gpio, False)
    con = None
    last_id = None
    while True:
        try:
            if con is None:
                if not os.path.exists(db_path):
                    time.sleep(RECONNECT_SEC)   # GrayWolf hasn't made the DB yet
                    continue
                con = _open_db(db_path)
                last_id = _max_packet_id(con)   # seed: don't blink the backlog
                print(f"[dra-rx-led] connected; baseline packet id={last_id}", flush=True)
            cur_id = _max_packet_id(con)
            if last_id is not None and cur_id > last_id:
                pulse(gpio)                     # one visible blink per poll cycle
            last_id = cur_id
            time.sleep(POLL_SEC)
        except sqlite3.Error:
            # DB locked / table not created yet / file rotated — reconnect.
            try:
                if con is not None:
                    con.close()
            except Exception:
                pass
            con = None
            led(gpio, False)
            time.sleep(RECONNECT_SEC)
        except KeyboardInterrupt:
            led(gpio, False)
            return 0


def self_test(gpio, n=5):
    """Blink the LED n times so you can verify GPIO wiring without GrayWolf."""
    if _pinctrl() is None:
        print("ERROR: 'pinctrl' not found.", file=sys.stderr)
        return 1
    print(f"Blinking GPIO {gpio} {n}× — watch the green LED…")
    for _ in range(n):
        pulse(gpio)
        time.sleep(0.25)
    led(gpio, False)
    return 0


# ── Service enabler (default mode) ──────────────────────────────────────────────
def enable_service(gpio, start=True):
    sys.path.insert(0, os.path.join(os.path.dirname(SELF), '..', '..'))
    from common.oasis_lib import _step, _ok, _info, _warn, _fail, _run

    _step(1, f"Enabling the DRA-Pi green RX LED service (GPIO {gpio})")

    if sys.platform != "linux":
        _fail("This sets up a systemd service — Linux only.")

    if _pinctrl() is None:
        _warn("'pinctrl' not found — the service won't be able to drive the LED.")
        _warn("It ships with Pi OS Bookworm/Trixie; install the Pi GPIO tools first.")

    if not os.path.exists(DB_PATH):
        _info(f"GrayWolf DB not present yet at {DB_PATH} —")
        _info("the daemon waits for it, so this is fine before GrayWolf's first run.")

    # Runs as root: pinctrl needs GPIO access and the GrayWolf DB lives under
    # /var/lib/graywolf. Only stdlib + pinctrl are used, so system python3 is fine.
    unit = f"""[Unit]
Description=DRA-Pi-Zero green RX LED on GrayWolf receive — OASIS
After=graywolf.service
Wants=graywolf.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 {SELF} --gpio {gpio} run
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
    proc = subprocess.Popen(["sudo", "tee", SERVICE_FILE],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    proc.communicate(unit.encode())
    if proc.returncode != 0:
        _fail(f"Could not write {SERVICE_FILE}")
    _run(["sudo", "chmod", "644", SERVICE_FILE], check=False)
    _ok(f"Service file: {SERVICE_FILE}")

    _run(["sudo", "systemctl", "daemon-reload"], check=False)

    if not start:
        _info(f"--no-enable: not starting. Later: sudo systemctl enable --now {SERVICE}")
        return

    _run(["sudo", "systemctl", "enable", "--now", SERVICE], check=False)
    status = _run(["systemctl", "is-active", SERVICE],
                  check=False, capture_output=True, text=True).stdout.strip()
    if status == "active":
        _ok(f"{SERVICE} is active — green LED follows GrayWolf RX")
        _info("Verify wiring any time:  python3 features/dra-audio-interface/enable-dra-rx-led.py --self-test")
    else:
        _warn(f"{SERVICE} status: {status}")
        log = _run(["journalctl", "-u", SERVICE, "-n", "12", "--no-pager", "--no-hostname"],
                   check=False, capture_output=True, text=True)
        for line in (log.stdout or log.stderr or "").strip().splitlines():
            _info(line)
        _info(f"Check logs with:  journalctl -u {SERVICE} -f")


def uninstall_service(gpio):
    sys.path.insert(0, os.path.join(os.path.dirname(SELF), '..', '..'))
    from common.oasis_lib import _step, _ok, _info, _run

    _step(1, f"Removing the DRA-Pi green RX LED service ({SERVICE})")
    _run(["sudo", "systemctl", "disable", "--now", SERVICE], check=False)
    _run(["sudo", "rm", "-f", SERVICE_FILE], check=False)
    _run(["sudo", "systemctl", "daemon-reload"], check=False)
    if _pinctrl() is not None:
        led(gpio, False)   # leave the LED off
    _ok(f"{SERVICE} removed; GPIO {gpio} left low.")
    _info("The red TX LED (GPIO 12) is unaffected — GrayWolf still keys it.")


# ── Entry point ─────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Install or run the DRA-Pi green RX LED service.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--gpio", type=int, default=LED_GPIO_DEFAULT,
                        help=f"LED GPIO, BCM numbering (default: {LED_GPIO_DEFAULT} = green RX LED).")
    parser.add_argument("--no-enable", action="store_true",
                        help="Write the systemd unit but don't enable/start it.")
    parser.add_argument("--uninstall", action="store_true",
                        help="Stop, disable and remove the service.")
    parser.add_argument("--self-test", action="store_true",
                        help="Blink the LED a few times and exit (verify wiring).")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("run", help="Run the LED daemon (used by the systemd unit).")
    args = parser.parse_args()

    if args.cmd == "run":
        sys.exit(run_daemon(args.gpio, DB_PATH))
    if args.self_test:
        sys.exit(self_test(args.gpio))
    if args.uninstall:
        uninstall_service(args.gpio)
        return
    enable_service(args.gpio, start=not args.no_enable)


if __name__ == "__main__":
    main()
