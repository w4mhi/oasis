"""Shared diagnostics framework: check registry + run_all() aggregation.

This module is the single "brain" behind OASIS station health reporting.
It defines:

  - ``Check``       -- static metadata + callable for one diagnostic check.
  - ``_result()``   -- helper that builds the check-result dict every
                        ``check_*`` function must return.
  - ``REGISTRY``    -- the list of registered ``Check`` instances. Real
                        checks are registered here in later tasks; this
                        module starts with an empty registry.
  - ``run_all()``   -- executes every applicable check, groups results,
                        rolls them up into per-capability verdicts, and
                        picks the single highest-impact failure ("fix now").

Offline-first / stdlib-only: no third-party imports.

Adding or removing a check is registry-only -- ``run_all`` iterates
``REGISTRY`` and never needs to change.
"""

from __future__ import annotations

import datetime
from collections import namedtuple

# ---------------------------------------------------------------------------
# Registry entry
# ---------------------------------------------------------------------------

# Check(id, group, label, capability, critical, tier, fn)
#   group      in {"CORE", "HARDWARE", "SERVICES", "DATA", "SYSTEM"}
#   capability in {"APRS_RX", "WINLINK", "POSITION", "POWER", "ACCESS", "REFERENCE"}
#   critical   : bool  -- does a fail here take the capability down?
#   tier       in {"v1", "backlog"}
#   fn         : callable(ctx) -> check-result dict (ctx carries host/port)
Check = namedtuple("Check", ["id", "group", "label", "capability", "critical", "tier", "fn"])

# Real checks are registered here starting in Task 2. This module's own
# tests exercise run_all() entirely via fake checks that swap REGISTRY.
REGISTRY: list = []

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GROUP_ORDER = ["CORE", "HARDWARE", "SERVICES", "SYSTEM", "DATA"]

# Capability -> member checks (v1). Used to render capability tiles and to
# know which capability a check id "belongs" to when building the rollup.
# (id -> {"label": ..., "members": [...]})
CAPABILITIES = {
    "ACCESS": {
        "label": "Core / Access",
        "members": ["server", "station_identity", "webssh"],
    },
    "APRS_RX": {
        "label": "APRS Receive",
        "members": ["rtl_sdr", "digirig", "dra_pi", "graywolf", "graywolf_api", "aprs_feed"],
    },
    "WINLINK": {
        "label": "Winlink",
        "members": ["pat"],
    },
    "POSITION": {
        "label": "Position / GPS",
        "members": ["gps"],
    },
    "POWER": {
        "label": "Power / Health",
        "members": ["power", "temp", "disk", "cooling_hat"],
    },
    "REFERENCE": {
        "label": "Reference Data",
        "members": ["fcc", "repeaterbook", "zim", "forms", "maps"],
    },
}

_STATUS_RANK = {"fail": 2, "warn": 1, "ok": 0}


def _result(id, group, label, status, badge, detail, breaks=None, fix=None):
    """Build the check-result dict every check function returns.

    {
      "id": "server", "group": "CORE", "label": "OASIS Server",
      "status": "ok",            # "ok" | "warn" | "fail"
      "badge": "RUNNING",        # short uppercase word
      "detail": "Serving on :8083",
      "breaks": None,            # plain-language consequence when status=="fail"
      "fix": None,               # Setup deep-link URL, or None
    }
    """
    return {
        "id": id,
        "group": group,
        "label": label,
        "status": status,
        "badge": badge,
        "detail": detail,
        "breaks": breaks,
        "fix": fix,
    }


def _run_one(check, ctx):
    """Run a single check, converting any exception into a fail/ERROR result.

    This keeps one bad check from sinking the whole sweep.
    """
    try:
        return check.fn(ctx)
    except Exception as exc:  # noqa: BLE001 - deliberately broad; see docstring
        return _result(
            check.id,
            check.group,
            check.label,
            "fail",
            "ERROR",
            f"Check raised an exception: {exc}",
            breaks="This check could not complete, so its status is unknown.",
            fix=None,
        )


def _rollup_capability(cap_id, results_by_id, members):
    """Deterministic, pure capability rollup rule.

    - fail if any *critical* child check is fail.
    - else warn if any child (critical or not) is fail or warn (a
      non-critical fail degrades to warn at capability level).
    - else ok.
    """
    present = [(m, results_by_id[m]) for m in members if m in results_by_id]
    if not present:
        return "ok"

    any_critical_fail = any(
        r["status"] == "fail" and r.get("_critical") for _, r in present
    )
    if any_critical_fail:
        return "fail"

    any_fail_or_warn = any(r["status"] in ("fail", "warn") for _, r in present)
    if any_fail_or_warn:
        return "warn"

    return "ok"


def _fix_now(results, critical_by_id):
    """Pick the single highest-impact fail.

    Among all fail checks, sort by (critical desc, GROUP_ORDER index) and
    return the first. None if there are no fails.
    """
    fails = [r for r in results if r["status"] == "fail"]
    if not fails:
        return None

    def sort_key(r):
        is_critical = critical_by_id.get(r["id"], False)
        try:
            group_idx = GROUP_ORDER.index(r["group"])
        except ValueError:
            group_idx = len(GROUP_ORDER)
        return (0 if is_critical else 1, group_idx)

    return sorted(fails, key=sort_key)[0]


def run_all(host, port, include_backlog=False):
    """Run every applicable registered check and aggregate the results.

    Returns:
    {
      "ran_at": "2026-07-17T21:53:07Z",
      "summary": {"fail": 2, "warn": 1, "ok": 17},
      "capabilities": [
        {"id": "APRS_RX", "label": "APRS Receive", "status": "ok",
         "checks": ["rtl_sdr", "graywolf", "graywolf_api", "aprs_feed"]},
        ...
      ],
      "fix_now": <check-result dict of the single highest-impact fail> | None,
      "groups": [{"name": "CORE", "checks": [<check-result dicts>, ...]}, ...],
    }
    """
    ctx = {"host": host, "port": port}

    checks = [c for c in REGISTRY if include_backlog or c.tier != "backlog"]

    results = []
    critical_by_id = {}
    capability_by_id = {}
    for check in checks:
        result = _run_one(check, ctx)
        results.append(result)
        critical_by_id[check.id] = check.critical
        capability_by_id[check.id] = check.capability

    summary = {"fail": 0, "warn": 0, "ok": 0}
    for r in results:
        if r["status"] in summary:
            summary[r["status"]] += 1

    # Build results_by_id annotated with "_critical" so the rollup can see
    # criticality without a second lookup pass; strip it back out before
    # returning results to the caller.
    results_by_id = {}
    for r in results:
        annotated = dict(r)
        annotated["_critical"] = critical_by_id.get(r["id"], False)
        results_by_id[r["id"]] = annotated

    # Which checks (by id) actually ran, grouped per capability -- this may
    # include ids not in the static CAPABILITIES member table (e.g. tests'
    # fake checks), so build capability groupings from what actually ran
    # rather than solely from the static table.
    capabilities = []
    seen_cap_ids = set()

    def _members_for(cap_id):
        static_members = CAPABILITIES.get(cap_id, {}).get("members", [])
        # Preserve static ordering, then append any ids that ran under
        # this capability but aren't in the static table (keeps this
        # module test-fixture friendly without a real check catalog yet).
        ran_ids = [r["id"] for r in results if capability_by_id.get(r["id"]) == cap_id]
        ordered = [m for m in static_members if m in results_by_id and capability_by_id.get(m) == cap_id]
        extra = [i for i in ran_ids if i not in ordered]
        return ordered + extra

    # Emit capability tiles in CAPABILITIES table order first...
    for cap_id, meta in CAPABILITIES.items():
        members = _members_for(cap_id)
        if not members:
            continue
        status = _rollup_capability(cap_id, results_by_id, members)
        capabilities.append({
            "id": cap_id,
            "label": meta["label"],
            "status": status,
            "checks": members,
        })
        seen_cap_ids.add(cap_id)

    # ...then any capability ids that appear only via registered checks
    # (not in the static table), so nothing silently vanishes.
    extra_cap_ids = sorted({cid for cid in capability_by_id.values() if cid not in seen_cap_ids})
    for cap_id in extra_cap_ids:
        members = [r["id"] for r in results if capability_by_id.get(r["id"]) == cap_id]
        status = _rollup_capability(cap_id, results_by_id, members)
        capabilities.append({
            "id": cap_id,
            "label": cap_id,
            "status": status,
            "checks": members,
        })

    fix_now = _fix_now(results, critical_by_id)

    groups = []
    for group_name in GROUP_ORDER:
        group_checks = [r for r in results if r["group"] == group_name]
        # Failures float to the top within each group.
        group_checks = sorted(group_checks, key=lambda r: _STATUS_RANK.get(r["status"], -1), reverse=True)
        groups.append({"name": group_name, "checks": group_checks})

    # Any group not in GROUP_ORDER (shouldn't happen with real checks, but
    # keep run_all defensive/registry-driven for fixtures/tests) is appended
    # so no result silently disappears.
    known_groups = set(GROUP_ORDER)
    extra_group_names = sorted({r["group"] for r in results if r["group"] not in known_groups})
    for group_name in extra_group_names:
        group_checks = [r for r in results if r["group"] == group_name]
        group_checks = sorted(group_checks, key=lambda r: _STATUS_RANK.get(r["status"], -1), reverse=True)
        groups.append({"name": group_name, "checks": group_checks})

    ran_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "ran_at": ran_at,
        "summary": summary,
        "capabilities": capabilities,
        "fix_now": fix_now,
        "groups": groups,
    }
