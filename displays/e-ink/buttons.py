#!/usr/bin/env python3
"""
Physical buttons for the Waveshare 2.7" HAT (KEY1-4 on GPIO 5/6/13/19).

Short press selects the matching screen's base view (KEY1 -> 1 ... KEY4 -> 4);
a long press (hold >= long_press_ms) selects that screen's secondary/list view.
Uses gpiozero with the lgpio backend — the same GPIO stack the e-ink driver
uses, so they share one pin factory without conflict (the panel owns
17/18/24/25, the keys own 5/6/13/19).

Short vs long are disambiguated cleanly: `when_held` fires the long action once
at the hold threshold; `when_released` fires the short action only if it was NOT
held. So each press yields exactly one action, no double-fire.

On a machine with no GPIO (dev laptop / PNG simulator), construction degrades to
a disabled no-op with an explanatory `.error`. `on_key(n, long)` is invoked from
gpiozero's callback thread — keep it to cheap, thread-safe work (set state,
signal an event); never drive the panel from there.
"""


class Buttons:
    KEYS = ("KEY1", "KEY2", "KEY3", "KEY4")

    def __init__(self, cfg, on_key):
        self._btns = []
        self.error = None
        bmap = cfg.get("buttons", {})

        try:
            from gpiozero import Button
        except Exception as exc:  # no GPIO libs / not a Pi
            self.error = f"gpiozero unavailable ({exc})"
            return

        bounce = bmap.get("bounce_ms", 50) / 1000.0
        hold = bmap.get("long_press_ms", 600) / 1000.0
        for idx, key in enumerate(self.KEYS, start=1):
            pin = bmap.get(key)
            if pin is None:
                continue
            try:
                btn = Button(pin, pull_up=True, bounce_time=bounce, hold_time=hold)
            except Exception as exc:  # pin busy, no factory, etc.
                self.error = f"KEY{idx}/GPIO{pin}: {exc}"
                continue
            self._bind(btn, idx, on_key)
            self._btns.append(btn)

        if not self._btns and not self.error:
            self.error = "no button pins configured"

    @staticmethod
    def _bind(btn, n, on_key):
        # Per-button 'held' flag distinguishes a long press (handled on hold)
        # from a short one (handled on release).
        state = {"held": False}

        def _held():
            state["held"] = True
            on_key(n, True)

        def _released():
            if state["held"]:
                state["held"] = False
            else:
                on_key(n, False)

        btn.when_held = _held
        btn.when_released = _released

    @property
    def available(self):
        return bool(self._btns)

    @property
    def count(self):
        return len(self._btns)

    def close(self):
        for btn in self._btns:
            try:
                btn.close()
            except Exception:
                pass
        self._btns = []
