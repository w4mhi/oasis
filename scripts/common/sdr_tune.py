"""Pure logic for the RTL-SDR APRS tuning bench (features/rtl-sdr/tune-rtl-sdr.py).

Everything here is import-safe and hardware-free so it can be unit-tested in CI.
The curses TUI, subprocess orchestration, and sweep driving live in the entry
point features/rtl-sdr/tune-rtl-sdr.py."""

import re
from collections import namedtuple


def build_pipeline(freq, gain, ppm, vol, srate, conf_path):
    """The rtl_fm | sox | direwolf shell pipeline. srate (24000/48000) drives
    rtl_fm -s, sox -r, and direwolf -r together — they must always match."""
    rtl = f"rtl_fm -M fm -f {freq} -s {srate} -F 0 -g {gain} -p {ppm} -"
    sox = (f"sox -t raw -r {srate} -e signed-integer -b 16 -c 1 - "
           f"-t raw - vol {vol}")
    dw  = f"direwolf -c {conf_path} -r {srate} -D 1 -"
    return f"{rtl} | {sox} | {dw}"
