"""common/hardware.py — hardware-aware conflict engine (Slice 1: engine core).

Exclusive "switchboard" device allocation: a device is assigned to at most one
logical service at a time. Conflicts are prevented at ASSIGNMENT time
(can_assign), not refereed at start time — the runtime only ever needs to check
whether THIS service's own assignment is present and attached (can_start),
never whether another service is already using the device.
"""
import json
import os
import subprocess
import sys

from common import config_paths

VALID_KINDS = {"rtl-sdr", "digirig", "dra-pi", "draws"}

# The DRAWS HAT is a single stereo codec exposing TWO independent radio ports.
# It is declared as TWO devices, not one, so the existing per-device exclusivity
# rule (see can_assign) lets aprs hold the left port while winlink holds the
# right AT THE SAME TIME — no non-exclusive special case anywhere in the engine.
# PTT GPIOs are swapped relative to the UDRC II; `channel` is the Direwolf
# channel that owns the port in the shared 2-channel TNC config.
# Labels carry the TNC profile names (features/draws-audio/draws_audio.py) so a
# port reads the same in the assignment console, in oasis-draws.conf, and in the
# unit description.
DRAWS_PORTS = (
    {"id": "draws-left",  "kind": "draws", "ptt": "gpio12", "alsa": "draws",
     "channel": 0, "label": "oasis-draws-aprs (left, ch0)"},
    {"id": "draws-right", "kind": "draws", "ptt": "gpio23", "alsa": "draws",
     "channel": 1, "label": "oasis-draws-winlink (right, ch1)"},
)

# Logical service -> the systemd unit(s) it starts. One unit per service in
# Slice 1; dual-mode selection (aprs's SDR-vs-radio-port TNC modes) and
# per-RTL feed units land in a later slice.
SERVICE_UNITS = {
    "aprs":      [],
    "winlink":   ["pat-direwolf"],
    "adsb":      ["dump1090-fa"],
    # openwebrx's assignment is advisory (no apply hook — it's configured in
    # OpenWebRX's own Admin → SDR profiles UI). It's tracked only so the
    # start-time shared-dongle check knows which RTL-SDR it "wants"; empty
    # unit list mirrors aprs's soundcard-only base. See v2 design §2.
    "openwebrx": [],
    # satellites listening (services/satellites/listen.py) is an ad-hoc rtl_fm
    # subprocess run by Flask, not a systemd unit. "satellites-listen" is a
    # SYNTHETIC unit: callers that care whether we hold the dongle wrap is_active
    # so it answers this token from listen.is_recording() (see listen.is_active_
    # wrapper). Keeps device_states() generic — no special-casing. Also advisory
    # (no apply hook); the recorder binds the dongle itself at start time.
    "satellites": ["satellites-listen"],
}

# Which device kind(s) each logical service may be assigned.
DEVICE_KIND_FOR_SERVICE = {
    "aprs":      {"rtl-sdr", "digirig", "dra-pi", "draws"},
    "winlink":   {"digirig", "dra-pi", "draws"},
    "adsb":      {"rtl-sdr"},
    "openwebrx": {"rtl-sdr"},
    "satellites": {"rtl-sdr"},
}

# aprs runs an extra RX feed unit (rtl_fm -> UDP -> GrayWolf) ONLY when assigned
# an SDR; on a radio port it's GrayWolf soundcard-only. All other services are
# mode-invariant. (GrayWolf itself is web-admin-configured — OASIS binds only the
# feed's dongle, not GrayWolf's radio interface.)
#
# openwebrx is intentionally absent from SERVICE_UNITS/DEVICE_KIND_FOR_SERVICE:
# scripts/apply_hardware.py has no apply hook for it (its RTL-SDR is picked
# entirely inside OpenWebRX's own Admin -> SDR profiles UI), so an OASIS-level
# assignment would never do anything — it would only block START on a
# no-op gate. Same reasoning as GrayWolf above.
APRS_FEED_UNIT = "aprs-sdr-feed"

# On a DRAWS box ONE always-on 2-channel direwolf owns the stereo card and
# serves both radio ports over AGW/KISS. pat-direwolf must never run there: it
# would try to open the same PCM, and templated for a DRA-Pi card the box does
# not have it simply crash-loops ("Cannot get card index for audioinjectorpi").
DRAWS_TNC_UNIT = "direwolf-draws"

# Units that report STATUS for a service but must never be stopped by a
# per-service action. The DRAWS TNC is shared infrastructure: it carries APRS on
# channel 0 and Winlink on channel 1, so stopping it "for winlink" silently
# takes APRS down too — and, since it is also winlink's status unit, leaves the
# Winlink card reading STOPPED with no way back short of a manual start. It is
# enabled at boot and meant to stay up, like the sound card it owns.
NEVER_STOP_UNITS = frozenset({DRAWS_TNC_UNIT})


def stoppable_units(inv, service):
    """service_units() minus shared infrastructure — what a per-service STOP may
    actually stop. Use this for any stop/displace path; use service_units() for
    status, since an always-on shared unit is still the honest 'is this service
    on air?' signal."""
    return [u for u in service_units(inv, service) if u not in NEVER_STOP_UNITS]


def _assigned_kind(inv, service):
    dev_id = inv.assignments.get(service)
    dev = inv.devices.get(dev_id) if dev_id else None
    return (dev or {}).get("kind")


def service_units(inv, service):
    """The systemd unit(s) implementing `service` given the current inventory.

    Two services are mode-dependent: `aprs` prepends the RX feed on an rtl-sdr
    assignment, and `winlink` SWAPS pat-direwolf for the shared DRAWS TNC when
    its assigned port lives on the DRAWS HAT."""
    base = list(SERVICE_UNITS.get(service, []))
    if service == "aprs":
        if _assigned_kind(inv, "aprs") == "rtl-sdr":
            return [APRS_FEED_UNIT] + base
    if service == "winlink" and _assigned_kind(inv, "winlink") == "draws":
        return [DRAWS_TNC_UNIT]
    return base


def service_for_unit(inv, unit):
    """Reverse map: the logical service a systemd unit belongs to (or None),
    resolved against the current inventory."""
    for svc in SERVICE_UNITS:
        if unit in service_units(inv, svc):
            return svc
    return None


class Inventory:
    def __init__(self, devices, assignments, errors=None):
        self.devices = devices          # dict: device id -> device dict
        self.assignments = assignments  # dict: service -> device id
        self.errors = errors or []      # validation errors (non-fatal, surfaced)


def _empty_inventory():
    return Inventory(devices={}, assignments={})


def load(repo_root):
    """Load + validate configuration/hardware.json. Never raises — an absent or
    corrupt file returns an empty inventory (first-run / degraded state)."""
    path = config_paths.hardware_json(repo_root)
    if not os.path.exists(path):
        return _empty_inventory()
    try:
        with open(path) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return _empty_inventory()

    devices = {}
    errors = []
    for d in raw.get("devices", []):
        did = d.get("id")
        kind = d.get("kind")
        if not did or did in devices:
            errors.append(f"skipped device with missing/duplicate id: {d!r}")
            continue
        if kind not in VALID_KINDS:
            errors.append(f"device {did!r}: unknown kind {kind!r}")
            continue
        devices[did] = d

    assignments = {}
    for service, device_id in raw.get("assignments", {}).items():
        if device_id not in devices:
            errors.append(f"service {service!r} assigned to unknown device {device_id!r}")
            assignments[service] = device_id  # keep it — can_start() distinguishes
            continue                          # "unassigned" from "device missing"
        allowed_kinds = DEVICE_KIND_FOR_SERVICE.get(service)
        if allowed_kinds is not None and devices[device_id]["kind"] not in allowed_kinds:
            errors.append(
                f"service {service!r} assigned to device {device_id!r} of kind "
                f"{devices[device_id]['kind']!r}, not in {sorted(allowed_kinds)!r}")
            continue  # kind mismatch: drop (can only happen via manual edit —
                      # can_assign(), built in a later task, prevents this
                      # through the API)
        assignments[service] = device_id

    return Inventory(devices=devices, assignments=assignments, errors=errors)


def save(repo_root, inv):
    """Write the inventory back to configuration/hardware.json (atomic replace)."""
    path = config_paths.hardware_json(repo_root)
    os.makedirs(config_paths.config_dir(repo_root), exist_ok=True)
    payload = {
        "version": 1,
        "devices": list(inv.devices.values()),
        "assignments": inv.assignments,
    }
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def clear_assignments(repo_root):
    """Drop all service->device assignments, keeping the detected device
    inventory. Used by the factory-reset finalizer: once every feature is
    uninstalled, the leftover assignments (adsb/aprs/satellites -> dongle) are
    stale config that made the dongles read as still set up. Detection keeps the
    hardware list; only the wiring is cleared. No-op when nothing is assigned."""
    inv = load(repo_root)
    if not inv.assignments:
        return False
    inv.assignments = {}
    save(repo_root, inv)
    return True


def assignee(inv, device_id):
    """The FIRST service a device is assigned to, or None. rtl-sdr devices are
    a shared resource (§2) — several services may point at the same dongle;
    use assignees() for the full list. Retained single-valued for exclusive
    (digirig/dra-pi) kinds where at most one service can ever hold a device."""
    for service, did in inv.assignments.items():
        if did == device_id:
            return service
    return None


def assignees(inv, device_id):
    """Every service pointing at a device, in assignment (dict) order. Under
    the shared-rtl-sdr model a dongle can be assigned to aprs/adsb/openwebrx
    at once — assignment is advisory bookkeeping; exclusivity is acquired at
    start time, not here."""
    return [service for service, did in inv.assignments.items() if did == device_id]


def device_of(inv, service):
    return inv.assignments.get(service)


def is_locked(inv, device_id):
    """Whether a device is locked to its current assignment. A locked device is
    protected from reassignment (operator or auto-assign) until unlocked. Absent
    flag / unknown device -> unlocked."""
    return bool(inv.devices.get(device_id, {}).get("locked", False))


def set_lock(repo_root, inv, device_id, locked):
    """Set or clear a device's lock and persist. Unlocking drops the key (absent
    == unlocked keeps hardware.json clean). No-op for an unknown device."""
    dev = inv.devices.get(device_id)
    if dev is None:
        return inv
    if locked:
        dev["locked"] = True
    else:
        dev.pop("locked", None)
    save(repo_root, inv)
    return inv


def _default_is_active(unit):
    """systemctl is-active <unit>.service. Non-Linux or errors -> False."""
    if sys.platform != "linux":
        return False
    try:
        r = subprocess.run(["systemctl", "is-active", f"{unit}.service"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() == "active"
    except Exception:
        return False


def device_states(inv, is_active=_default_is_active):
    """[{id, label, kind, serial, assignee, running}] — drives the Setup card's
    Dongles list. 🟢 free = no assignees, or assigned-but-idle (shared default);
    🔴 in use = one of its assigned services' unit(s) is systemctl is-active. A
    shared rtl-sdr may have several assignees (advisory); `assignee` lists ALL of
    them (comma-joined) so an idle co-assignee (e.g. `satellites`) isn't hidden
    behind the one that's currently running, while `running` still flags in-use.
    `serial` lets the UI join in the USB-port map (server/app.py) — empty for
    kinds without one."""
    out = []
    for did, d in inv.devices.items():
        services = assignees(inv, did)
        running_svc = next(
            (svc for svc in services
             if any(is_active(u) for u in service_units(inv, svc))),
            None)
        label = ", ".join(services) if services else None   # all assignees, not just the running one
        out.append({"id": did, "label": d.get("label", did), "kind": d["kind"],
                    "serial": d.get("serial", ""), "ptt": d.get("ptt", ""),
                    "assignee": label, "running": running_svc is not None})
    return out


def warnings(inv, is_active=_default_is_active):
    """Health warnings for the console + dashboard rail — the shared contract both
    surfaces read. Pure function of the inventory and unit-active state. Each item
    is {kind, device, service, severity, message} with severity in {"warn","crit"}.
    An empty list means all nominal (green rail). Runtime warnings (crashes,
    running-vs-assigned drift) are added as the console wiring needs them."""
    out = []
    for service, device_id in inv.assignments.items():
        if device_id not in inv.devices:
            out.append({
                "kind": "device-missing", "device": device_id, "service": service,
                "severity": "crit",
                "message": f"{service} is assigned to a device that isn't present ({device_id})",
            })
    return out


def can_start(inv, service, is_active=_default_is_active):
    """(ok, reason). reason in {"", "unassigned", "device-not-attached"}. Under
    exclusive allocation a device can never be held by ANOTHER service, so
    this can only fail for the requesting service's OWN assignment state."""
    device_id = inv.assignments.get(service)
    if device_id is None:
        return False, "unassigned"
    if device_id not in inv.devices:
        return False, "device-not-attached"
    return True, ""


def can_assign(inv, service, device_id):
    """(ok, holder). holder is the blocking service's name, or None. Refuses the
    wrong kind, and — for exclusive (digirig/dra-pi) devices only — a device
    already assigned to a DIFFERENT service. rtl-sdr devices are shared and
    never refused on holder grounds (§2)."""
    if device_id not in inv.devices:
        return False, None
    kind = inv.devices[device_id]["kind"]
    allowed_kinds = DEVICE_KIND_FOR_SERVICE.get(service)
    if allowed_kinds is not None and kind not in allowed_kinds:
        return False, None
    # rtl-sdr is a SHARED resource: aprs/adsb/openwebrx may all be assigned the
    # same dongle (advisory bookkeeping — §2). Exclusivity is acquired at start
    # time, not here. digirig/dra-pi (winlink) stay exclusive: one holder only.
    if kind != "rtl-sdr":
        holder = assignee(inv, device_id)
        if holder is not None and holder != service:
            return False, holder
    return True, None


def assign(repo_root, inv, service, device_id):
    """Assign device_id to service and persist. Raises ValueError if
    can_assign() refuses (caller should check can_assign() first to report a
    clean error instead of catching this)."""
    ok, holder = can_assign(inv, service, device_id)
    if not ok:
        raise ValueError(f"cannot assign {device_id!r} to {service!r} (holder={holder!r})")
    inv.assignments[service] = device_id
    save(repo_root, inv)
    return inv


def release(repo_root, inv, service, stop_fn=None):
    """Unassign service's device, persist. If stop_fn is given, it is called
    for each of the service's units (frees the device) BEFORE the assignment
    is cleared — no auto-restart of anything else."""
    if service in inv.assignments:
        if stop_fn is not None:
            # Resolve units against the CURRENT assignment, before it is cleared:
            # they are mode-dependent (aprs's SDR feed, winlink's DRAWS TNC), so
            # the raw SERVICE_UNITS dict would stop the wrong thing — and after
            # the delete the mode is gone. stoppable_units keeps shared
            # infrastructure (the DRAWS TNC) out of it.
            for unit in stoppable_units(inv, service):
                stop_fn(unit)
        del inv.assignments[service]
        save(repo_root, inv)
    return inv


def can_reroute(inv, service, device_id):
    """(ok, reason). reason in {"", "target-locked", "source-locked"}. A locked
    device protects its assignment from ANY displacement: you can neither claim a
    locked target dongle nor move a service off a locked source dongle. Callers
    check this first to show the two-step 'unlock it to move it' warning."""
    if is_locked(inv, device_id):
        return False, "target-locked"
    old = inv.assignments.get(service)
    if old is not None and old != device_id and is_locked(inv, old):
        return False, "source-locked"
    return True, ""


def reroute(repo_root, inv, service, device_id, start_fn=None, stop_fn=None):
    """Console-enforced exclusive reroute: assign `service` to `device_id`, persist,
    and start its unit(s), displacing whatever else was on that dongle and stopping
    the service on its old dongle first. Raises ValueError if can_reroute() refuses
    (a lock) — callers should check can_reroute() first to report it cleanly.
    Units are started/stopped via injected callables (one unit name each) so the
    logic is testable without systemd."""
    ok, reason = can_reroute(inv, service, device_id)
    if not ok:
        raise ValueError(f"cannot reroute {service!r} to {device_id!r}: {reason}")
    # If the service is moving from a different device, stop it there first
    # (units are computed against its OLD assignment before we reassign).
    old_dev = inv.assignments.get(service)
    if old_dev is not None and old_dev != device_id and stop_fn is not None:
        for unit in stoppable_units(inv, service):
            stop_fn(unit)
    # Console-enforced exclusive: displace any OTHER service currently on the
    # target device (stop its units, unassign it) before claiming the dongle.
    for other in [s for s in assignees(inv, device_id) if s != service]:
        if stop_fn is not None:
            for unit in stoppable_units(inv, other):
                stop_fn(unit)
        del inv.assignments[other]
    inv.assignments[service] = device_id
    save(repo_root, inv)
    if start_fn is not None:
        for unit in service_units(inv, service):
            start_fn(unit)
    return inv


def default_assign(repo_root, inv, service, allowed_kinds):
    """If `service` has no assignment yet, assign it the first free declared
    device (in hardware.json declaration order) whose kind is in
    `allowed_kinds`, and persist. No-op if `service` is already assigned or
    no free compatible device exists.

    specs/2026-07-15-hardware-conflict-resolution-v2-design.md §3: reverses
    the original design's "nothing auto-assigned, ever" stance for the
    common single-dongle case — the operator can still override via the
    per-card dropdown."""
    if service in inv.assignments:
        return inv
    for device_id, device in inv.devices.items():
        if device["kind"] not in allowed_kinds:
            continue
        if is_locked(inv, device_id):
            continue  # a locked dongle is protected — never auto-assign onto it
        # rtl-sdr is shared: a dongle already assigned to another service is
        # still a valid default here (all three RTL consumers converge on the
        # first dongle out of the box — the operator spreads them across
        # dongles via the dropdown). Exclusive kinds skip already-claimed ones.
        if device["kind"] != "rtl-sdr" and assignee(inv, device_id) is not None:
            continue
        inv.assignments[service] = device_id
        save(repo_root, inv)
        return inv
    return inv


def auto_declare_rtl_sdrs(repo_root, inv, detected_serials):
    """Declare every detected RTL-SDR whose serial is UNIQUE among the detected
    set and not already declared (auto id `rtl-sdr-<serial>`), and persist.
    Persist once if anything was added; no-op otherwise.

    Dongles that share a serial — the ubiquitous factory `00000001` duplicate —
    are indistinguishable to software, so they're skipped: the operator burns a
    unique serial onto one first (hardware_detect.can_burn_serial / the burn
    flow), after which the next detect declares both.

    Supersedes the original single-dongle-only auto-declare: a second,
    uniquely-serialed dongle was detected but left undeclared, so it never
    appeared in the assignment dropdowns (which list DECLARED devices). Still
    matches the design's "list detected devices, that's it" intent — just for
    N unambiguous dongles instead of exactly one."""
    counts = {}
    for s in detected_serials:
        counts[s] = counts.get(s, 0) + 1
    declared_serials = {d.get("serial") for d in inv.devices.values()
                        if d.get("kind") == "rtl-sdr"}
    changed = False
    for serial in detected_serials:
        if counts[serial] != 1:          # ambiguous duplicate — needs a burn first
            continue
        if serial in declared_serials:
            continue
        device_id = f"rtl-sdr-{serial}"
        if device_id in inv.devices:      # id already taken (non-rtl or manual)
            continue
        inv.devices[device_id] = {"id": device_id, "kind": "rtl-sdr", "serial": serial,
                                   "label": f"RTL-SDR ({serial})"}
        declared_serials.add(serial)
        changed = True
    if changed:
        save(repo_root, inv)
    return inv


def reconcile_present_rtl_sdrs(repo_root, inv, detected_serials, present_count,
                               is_active=_default_is_active):
    """Undeclare RTL-SDRs that are no longer physically present — the inverse of
    auto_declare_rtl_sdrs, so an unplugged dongle stops showing as assigned.

    A declared dongle counts as PRESENT if its serial appears in rtl_test
    detection OR a service assigned to it is currently running (a busy dongle is
    hidden from rtl_test's exclusive probe but is obviously still attached).

    Assignments to a removed dongle are CLEARED, not left dangling: the caller's
    default_assign then fails those services over to a surviving dongle (they
    all share the one that's left), and only when NO dongle remains do they end
    up truly unassigned. So unplugging one of two dongles keeps the services
    running on the other; unplugging the last one is what shows "unassigned".

    Acts only when the number of confirmed-absent dongles equals the real USB
    count drop (declared − present_count). A mismatch means detection is
    ambiguous this cycle (e.g. rtl_test transiently empty, or lsusb unavailable
    so present_count reads 0) — safer to leave everything declared than to guess
    and unassign a dongle that's actually attached."""
    declared = [(did, d) for did, d in inv.devices.items() if d.get("kind") == "rtl-sdr"]
    present = set(detected_serials)
    for did, d in declared:
        if any(is_active(u) for svc in assignees(inv, did) for u in service_units(inv, svc)):
            present.add(d.get("serial"))
    absent = [did for did, d in declared if d.get("serial") not in present]
    if not absent or len(absent) != len(declared) - present_count:
        return inv
    for did in absent:
        del inv.devices[did]
        for svc in [s for s, dev in inv.assignments.items() if dev == did]:
            del inv.assignments[svc]   # freed → default_assign re-points to a survivor
    save(repo_root, inv)
    return inv


def auto_declare_digirig(repo_root, inv, digirig):
    """Declare a detected lone DigiRig (hardware_detect.detect_digirig()) when
    none is declared yet — the radio-port analogue of auto_declare_rtl_sdrs. Its
    CP210x by-id becomes the PTT and its chip serial gives a stable device id
    (survives replug). `alsa` is left blank on purpose: winlink.radio_port_config
    autodetects the USB sound card at apply time. No-op if a digirig is already
    declared or `digirig` is None (absent/ambiguous)."""
    if not digirig:
        return inv
    if any(d.get("kind") == "digirig" for d in inv.devices.values()):
        return inv
    serial = digirig.get("serial") or ""
    device_id = f"digirig-{serial}" if serial else "digirig"
    if device_id in inv.devices:
        return inv
    inv.devices[device_id] = {"id": device_id, "kind": "digirig",
                               "ptt": digirig["ptt"], "alsa": "", "serial": serial,
                               "label": "DigiRig"}
    save(repo_root, inv)
    return inv


def reconcile_digirig(repo_root, inv, path_exists=None):
    """Undeclare a DigiRig whose PTT by-id path no longer exists (unplugged) —
    the radio-port analogue of reconcile_present_rtl_sdrs. Precise: it checks the
    exact declared by-id, so no busy/ambiguity guard is needed. The assignment is
    left dangling so re-plug (auto_declare_digirig re-adds the same id)
    revalidates it."""
    if path_exists is None:
        path_exists = os.path.exists
    removed = [did for did, d in inv.devices.items()
               if d.get("kind") == "digirig" and d.get("ptt") and not path_exists(d["ptt"])]
    if not removed:
        return inv
    for did in removed:
        del inv.devices[did]
    save(repo_root, inv)
    return inv


def auto_declare_dra_pi(repo_root, inv, present):
    """Declare the DRA-Pi HAT when its ALSA card is present and none is declared.
    Fully deterministic — fixed `audioinjectorpi` ALSA + GPIO-12 PTT (the real
    GPIO is resolved at apply time by winlink.compute_ptt_gpio), so a bare
    kind='dra-pi' record is all that's needed; no detection ambiguity like the
    DigiRig. There's only ever one DRA-Pi (a HAT), so the id is fixed."""
    if not present:
        return inv
    if any(d.get("kind") == "dra-pi" for d in inv.devices.values()):
        return inv
    inv.devices["dra-pi"] = {"id": "dra-pi", "kind": "dra-pi", "ptt": "gpio12",
                             "alsa": "audioinjectorpi", "label": "DRA-Pi"}
    save(repo_root, inv)
    return inv


def auto_declare_draws(repo_root, inv, present):
    """Declare BOTH DRAWS radio ports when the `draws` ALSA card is present.

    Deterministic like the DRA-Pi (fixed ALSA card + fixed PTT GPIOs), so no
    detection ambiguity — but two records instead of one, because the HAT has
    two independent mDin6 connectors sharing one stereo codec. Idempotent."""
    if not present:
        return inv
    existing = [d for d in inv.devices.values() if d.get("kind") == "draws"]
    if existing:
        # Already declared — but refresh the STATIC fields, because this function
        # only ever fires on a box with no draws device, so a record written
        # before a label/channel change would otherwise keep the stale value
        # forever. Merge into the existing dict so operator/runtime state
        # (assignments live elsewhere; `locked` lives here) survives.
        changed = False
        for port in DRAWS_PORTS:
            dev = inv.devices.get(port["id"])
            if dev is None:
                inv.devices[port["id"]] = dict(port)
                changed = True
                continue
            for key, value in port.items():
                if dev.get(key) != value:
                    dev[key] = value
                    changed = True
        if changed:
            save(repo_root, inv)
        return inv
    for port in DRAWS_PORTS:
        inv.devices[port["id"]] = dict(port)
    save(repo_root, inv)
    return inv


def reconcile_draws(repo_root, inv, present):
    """Undeclare both DRAWS ports when the card is gone (overlay unloaded / HAT
    removed) — the analogue of reconcile_dra_pi. Assignments are left dangling
    so a re-detect revalidates them."""
    if present:
        return inv
    removed = [did for did, d in inv.devices.items() if d.get("kind") == "draws"]
    if not removed:
        return inv
    for did in removed:
        del inv.devices[did]
    save(repo_root, inv)
    return inv


def reconcile_dra_pi(repo_root, inv, present):
    """Undeclare the DRA-Pi when its ALSA card is gone (overlay unloaded / HAT
    removed) — the analogue of reconcile_digirig. Assignment left dangling so a
    re-detect revalidates it."""
    if present:
        return inv
    removed = [did for did, d in inv.devices.items() if d.get("kind") == "dra-pi"]
    if not removed:
        return inv
    for did in removed:
        del inv.devices[did]
    save(repo_root, inv)
    return inv
