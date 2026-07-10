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
