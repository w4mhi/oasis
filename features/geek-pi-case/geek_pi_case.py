"""
geek_pi_case — pure decision/formatting logic for the GeekPi ZP-0129 case daemon.

No hardware imports (no smbus/gpiozero/PIL): this module is unit-tested off-Pi
and byte-compiled in CI. The I/O shell lives in
features/geek-pi-case/geek-pi-case.py.
"""

FAN_ON_DEFAULT  = 55.0
FAN_OFF_DEFAULT = 48.0


def parse_cpu_temp(raw):
    """/sys/class/thermal/thermal_zone0/temp holds milli-degrees C as text."""
    return int(str(raw).strip()) / 1000.0


def fan_decision(temp_c, currently_on, fan_on=FAN_ON_DEFAULT, fan_off=FAN_OFF_DEFAULT):
    """Desired fan on/off from temperature, with hysteresis.

    >= fan_on  → on; <= fan_off → off; inside the dead-band → hold current
    state. currently_on=None (boot/first pass) resolves the dead-band to OFF.
    """
    if temp_c >= fan_on:
        return True
    if temp_c <= fan_off:
        return False
    return bool(currently_on) if currently_on is not None else False


def format_stats_line(cpu_pct, temp_c, ram_pct):
    """One OLED line: 'ccc% tt.tC rrr%' — CPU busy %, CPU temp, RAM used %."""
    return f"{cpu_pct:>3.0f}%  {temp_c:>4.1f}C {ram_pct:>3.0f}%"


# ── UPS Plus (EP-0136) — I2C 0x17, little-endian word registers (low byte addr) ──
UPS_ADDR    = 0x17
REG_VOUT    = 0x03   # pogopin output       mV
REG_VBATT   = 0x05   # battery terminal     mV
REG_VUSBC   = 0x07   # USB-C input          mV
REG_VMICRO  = 0x09   # micro-USB input      mV
REG_TEMP    = 0x0B   # battery temperature  °C (signed)
REG_CAPACITY = 0x13  # remaining capacity   %

ON_BATTERY_MV = 3000   # both inputs below this ⇒ running on battery


def word_le(low_byte, high_byte):
    """Combine two register bytes, little-endian (as smbus read_word_data does)."""
    return (high_byte << 8) | low_byte


def decode_temp(word):
    """16-bit two's-complement → signed °C."""
    return word - 0x10000 if word & 0x8000 else word


def on_battery(usb_c_mv, micro_usb_mv, threshold_mv=ON_BATTERY_MV):
    """True when neither input rail is powered (both below threshold)."""
    return usb_c_mv < threshold_mv and micro_usb_mv < threshold_mv


def format_ups_line(capacity_pct, batt_mv, on_batt):
    """One OLED line: 'BAT  nn% SRC v.vvV' (SRC = BATT on battery, CHG on mains)."""
    src = "BATT" if on_batt else "CHG"
    return f"BAT {capacity_pct:>3d}% {src} {batt_mv / 1000:.2f}V"


class ShutdownGuard:
    """Debounced low-battery trip. Fires True exactly once after `samples`
    consecutive on-battery reads at/below `pct`; any non-qualifying read resets
    the counter so a transient I2C blip or a brief mains sag can't trigger it."""

    def __init__(self, pct=15, samples=3):
        self.pct = pct
        self.samples = samples
        self._count = 0
        self._fired = False

    def update(self, on_batt, capacity_pct):
        if on_batt and capacity_pct <= self.pct:
            self._count += 1
        else:
            self._count = 0
        if self._count >= self.samples and not self._fired:
            self._fired = True
            return True
        return False
