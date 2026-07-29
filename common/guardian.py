"""common/guardian.py — resource guardian state machine (design 2026-07-28).

The ONE sanctioned autonomous action in OASIS, kept deliberately separate from
the observe-only assignment monitor: when temperature / CPU / memory crosses a
threshold, arm a cancellable countdown and, if the operator doesn't intervene,
STOP ALL services to protect an unattended box.

This module is the PURE state machine — no threads, no psutil, no systemctl.
The server runner (server/routes/guardian.py) reads real stats, drives this on
a timer, executes the STOP ALL when `evaluate` returns the 'fire' action, and
exposes state + cancel to the dashboard.

States: idle -> armed (countdown) -> tripped (fired). Recovery (metrics back
under threshold) or an operator cancel returns to idle from any state.
"""

IDLE = "idle"
ARMED = "armed"
TRIPPED = "tripped"

# Conservative defaults — "something is genuinely wrong" levels, not everyday
# load. 80 °C is near the Pi's own throttle point; sustained 95% CPU / 92% mem
# means the box is in trouble. Operator-tunable via the guardian config.
DEFAULT_THRESHOLDS = {"temp_c": 80.0, "cpu_pct": 95.0, "mem_pct": 92.0}
COUNTDOWN_SEC = 30

_METRICS = ("temp_c", "cpu_pct", "mem_pct")


def over_threshold(stats, thresholds):
    """Name of the first metric at/over its threshold, or None. A metric that is
    absent/None (couldn't be read) never trips the guardian."""
    for key in _METRICS:
        value = stats.get(key)
        limit = thresholds.get(key)
        if value is not None and limit is not None and value >= limit:
            return key
    return None


def _idle_state():
    return {"mode": IDLE, "deadline": None, "reason": None}


def evaluate(stats, thresholds, state, now, countdown=COUNTDOWN_SEC):
    """Advance the state machine one tick. Returns (new_state, action) where
    action is None or 'fire' ('fire' == the caller must STOP ALL now, after which
    the returned state is 'tripped')."""
    mode = state.get("mode", IDLE)
    over = over_threshold(stats, thresholds)

    if mode == ARMED:
        if not over:
            return _idle_state(), None                      # recovered — auto-disarm
        if now >= state.get("deadline", now):
            return {"mode": TRIPPED, "deadline": None,
                    "reason": state.get("reason") or over}, "fire"
        return state, None                                  # still counting down

    if mode == TRIPPED:
        if not over:
            return _idle_state(), None                      # recovered — re-arm allowed
        return state, None                                  # already fired; don't loop

    # IDLE (or unknown)
    if over:
        return {"mode": ARMED, "deadline": now + countdown, "reason": over}, None
    return _idle_state(), None


def cancel(state):
    """Operator override — disarm a countdown or clear a tripped state."""
    return _idle_state()
