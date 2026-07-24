#!/usr/bin/env python3
"""Shared setup orchestrator core used by CLI and web entry points.

First slice scope: deterministic planning + strict sequential execution with
stage/status taxonomy aligned to setup specs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List, Optional


STATUS_PENDING = "pending"
STATUS_INSTALLING = "installing"
STATUS_INSTALL_FAILED = "install_failed"
STATUS_VERIFY_FAILED = "verify_failed"
STATUS_INSTALLED = "installed"
STATUS_INSTALLED_NEEDS_REBOOT = "installed_needs_reboot"
STATUS_ENABLE_FAILED = "enable_failed"
STATUS_INSTALLED_ENABLED_NOT_STARTED = "installed_enabled_not_started"
STATUS_SKIPPED_DEPENDENCY = "skipped_dependency"
STATUS_BLOCKED_PREFLIGHT = "blocked_preflight"
STATUS_CANCELED = "canceled"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ok_result() -> Dict[str, object]:
    return {"ok": True}


@dataclass
class FeatureSpec:
    key: str
    dependencies: List[str] = field(default_factory=list)
    install_fn: Optional[Callable[[], Dict[str, object]]] = None
    verify_fn: Optional[Callable[[], Dict[str, object]]] = None
    enable_fn: Optional[Callable[[], Dict[str, object]]] = None
    # Reserved hook for future per-feature removal (design: installed-aware
    # setup, C). None means "not removable yet". Same call/result contract as
    # install_fn: () -> {"ok": bool, ...}. No caller today.
    remove_fn: Optional[Callable[[], Dict[str, object]]] = None
    # Returns this feature's removal record (see common/removal.py for the schema)
    # from the installer's own constants. build_registry() wires it; install
    # persists the record into installed-services.json (one source of truth).
    # None for features excluded from removal (server, wikipedia).
    removal_record_fn: Optional[Callable[[], Dict[str, object]]] = None
    enable_policy: str = "none"  # none|if_installed|manual_only
    requires_reboot: bool = False
    # True for features whose install_fn needs real root (writes /etc,
    # /usr/local/bin, apt/GPG, sudoers, ...). The in-process web server never has
    # that (no TTY, no cached sudo credential), so a privileged feature's install
    # step is NOT run in-process here — the caller must supply
    # RunOptions.privileged_run_fn to hand it off to an out-of-process privileged
    # worker instead. install_fn is still the single source of truth for WHAT the
    # step does; only WHO calls it changes.
    privileged: bool = False
    # Teardown ordering knob for uninstall. Higher = removed LATER. Default 0.
    # "Lifeline" features that keep the operator's session usable during a mass
    # uninstall get a positive value so they are torn down after everything else:
    # service-controls, webssh (remote shell), and pi-headless (the oasis.service
    # autostart / running server) — the last thing standing before a reboot.
    # Safe only for features with no *removable* dependencies (these depend on
    # server, which is non-removable), so pushing them last never orphans a
    # dependant. resolve_uninstall applies this as a stable sort.
    teardown_priority: int = 0


@dataclass
class RunOptions:
    sequential: bool = True
    auto_start_services: bool = False
    job_id: str = "setup-job-cli"
    cancel_requested: Optional[Callable[[], bool]] = None
    # Called instead of spec.install_fn() for any FeatureSpec with privileged=True.
    # Signature: (feature_key: str, spec: FeatureSpec) -> Dict[str, object] (same
    # result shape _run_step returns). If None, a privileged feature's install_fn
    # runs in-process anyway (CLI/test callers already running as root, or
    # non-Linux dev callers where the feature is preflight-blocked).
    privileged_run_fn: Optional[Callable[[str, "FeatureSpec"], Dict[str, object]]] = None


@dataclass
class FeatureState:
    feature: str
    status: str = STATUS_PENDING
    stage: str = "pending"
    reason_code: Optional[str] = None
    reason_text: Optional[str] = None
    suggested_action: Optional[str] = None


@dataclass
class SetupPlan:
    selected_features: List[str]
    ordered_features: List[str]
    blocked: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class JobSummary:
    green: int
    amber: int
    red: int
    gray: int
    features: List[Dict[str, object]]


class SetupRunner:
    def __init__(self, registry: Dict[str, FeatureSpec]):
        self.registry = registry

    def resolve(self, selected_features: Iterable[str]) -> SetupPlan:
        return resolve_plan(selected_features, self.registry)

    def run(self, plan: SetupPlan, run_options: RunOptions, event_sink=None):
        return run_plan(plan, run_options, self.registry, event_sink=event_sink)


def resolve_plan(selected_features: Iterable[str], registry: Dict[str, FeatureSpec]) -> SetupPlan:
    selected: List[str] = []
    seen = set()
    for raw in selected_features:
        key = (raw or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        selected.append(key)

    blocked: List[Dict[str, str]] = []
    for key in selected:
        if key not in registry:
            blocked.append({
                "feature": key,
                "reason_code": "UNKNOWN_FEATURE",
                "reason_text": f"unknown feature: {key}",
            })

    if blocked:
        return SetupPlan(selected_features=selected, ordered_features=[], blocked=blocked)

    ordered: List[str] = []
    perm = set()
    temp = set()

    def visit(k: str):
        if k in perm:
            return
        if k in temp:
            blocked.append({
                "feature": k,
                "reason_code": "DEPENDENCY_CYCLE",
                "reason_text": f"dependency cycle detected at {k}",
            })
            return
        temp.add(k)
        spec = registry[k]
        for dep in spec.dependencies:
            if dep not in registry:
                blocked.append({
                    "feature": k,
                    "reason_code": "MISSING_DEPENDENCY",
                    "reason_text": f"missing dependency {dep} for {k}",
                })
                continue
            visit(dep)
        temp.remove(k)
        perm.add(k)
        if k not in ordered:
            ordered.append(k)

    for key in selected:
        visit(key)

    if blocked:
        return SetupPlan(selected_features=selected, ordered_features=ordered, blocked=blocked)

    return SetupPlan(selected_features=selected, ordered_features=ordered, blocked=[])


@dataclass
class UninstallPlan:
    selected: List[str]
    ordered: List[str]
    blocked: List[Dict[str, str]] = field(default_factory=list)


def _is_removable(spec):
    """A feature is removable if it carries a removal record or a remove hook.
    Non-removable specs (server, wikipedia) are excluded from teardown by
    design — their removal_record_fn/remove_fn are both None."""
    return spec.removal_record_fn is not None or spec.remove_fn is not None


def resolve_uninstall(selected, installed, registry):
    """Plan the removal of *selected* features.

    - Only installed features are considered (others are dropped).
    - A feature is blocked if some still-installed feature that is NOT also being
      removed depends on it (removing it would orphan the dependant).
    - `ordered` is the non-blocked removable set in reverse-dependency order:
      dependants are torn down before the dependencies they rely on.
    """
    installed = set(installed)
    sel = [k for k in selected if k in installed]
    sel_set = set(sel)

    blocked: List[Dict[str, str]] = []
    blocked_keys = set()
    for key in sel:
        for other in sorted(installed):
            if other in sel_set:
                continue
            spec = registry.get(other)
            # Only a *removable* dependant blocks: telling the operator to
            # "uninstall <other> first" is only actionable if <other> can be
            # uninstalled. A non-removable dependant (removal_record_fn/remove_fn
            # both None — i.e. wikipedia, whose ZIM is kept data, not a service)
            # can never be removed, so it must not permanently wedge removal of
            # its dependency (kiwix). Removing kiwix just leaves the ZIM as inert
            # data on disk, which is exactly the intended behavior.
            if spec and key in spec.dependencies and _is_removable(spec):
                blocked.append({
                    "feature": key,
                    "reason_code": "DEPENDANT_INSTALLED",
                    "reason_text": f"{other} depends on {key}; uninstall {other} first",
                })
                blocked_keys.add(key)

    removable = [k for k in sel if k not in blocked_keys]
    removable_set = set(removable)

    # Dependency-first order within the removable set, then reverse it so
    # dependants come out before the dependencies they need.
    order: List[str] = []
    perm = set()

    def visit(k):
        if k in perm:
            return
        perm.add(k)
        spec = registry.get(k)
        for dep in (spec.dependencies if spec else []):
            if dep in removable_set:
                visit(dep)
        order.append(k)

    for k in removable:
        visit(k)
    ordered = list(reversed(order))
    # Stable sort by teardown_priority: 0-priority features keep their
    # reverse-dependency order and come first; lifeline features (positive
    # priority) move to the end, ascending, so the session-critical ones are
    # removed last (pi-headless dead last). Stable + these features having no
    # removable dependencies means dependency ordering is preserved.
    def _prio(k):
        spec = registry.get(k)
        return spec.teardown_priority if spec else 0
    ordered.sort(key=_prio)
    return UninstallPlan(selected=sel, ordered=ordered, blocked=blocked)


def _run_step(fn: Optional[Callable[[], Dict[str, object]]]) -> Dict[str, object]:
    if fn is None:
        return _ok_result()
    try:
        result = fn()
    except Exception as exc:  # pragma: no cover - defensive path
        return {"ok": False, "reason_text": str(exc)}
    if isinstance(result, dict):
        return result
    if result is True:
        return _ok_result()
    if result is False:
        return {"ok": False, "reason_text": "step returned false"}
    return _ok_result()


def run_plan(plan: SetupPlan, run_options: RunOptions, registry: Dict[str, FeatureSpec], event_sink=None):
    sink = event_sink or (lambda event: None)

    def emit(event: str, **payload):
        sink({
            "schemaVersion": "1.0",
            "event": event,
            "jobId": run_options.job_id,
            "ts": _utc_now(),
            **payload,
        })

    states: Dict[str, FeatureState] = {k: FeatureState(feature=k) for k in plan.ordered_features}
    blocked = list(plan.blocked)
    canceled = False

    def is_canceled() -> bool:
        fn = run_options.cancel_requested
        if fn is None:
            return False
        try:
            return bool(fn())
        except Exception:
            return False

    emit(
        "job_started",
        executionMode="sequential",
        autoStartServices=bool(run_options.auto_start_services),
        selectedFeatures=list(plan.selected_features),
        orderedFeatures=list(plan.ordered_features),
    )

    if blocked:
        for b in blocked:
            emit(
                "feature_skipped",
                feature=b.get("feature", "unknown"),
                status=STATUS_BLOCKED_PREFLIGHT,
                reasonCode=b.get("reason_code", "BLOCKED"),
                reasonText=b.get("reason_text", "blocked preflight"),
            )
        summary = summarize_results(states, blocked=blocked)
        emit("job_finished", ok=False, exitCode=1, summary=summary.__dict__)
        return states, blocked, summary

    total = len(plan.ordered_features)
    completed = 0

    for idx, key in enumerate(plan.ordered_features, 1):
        spec = registry[key]
        st = states[key]

        if canceled or is_canceled():
            canceled = True
            st.status = STATUS_CANCELED
            st.stage = "pending"
            st.reason_code = "JOB_CANCELED"
            st.reason_text = "job canceled by operator"
            emit(
                "feature_skipped",
                feature=key,
                status=st.status,
                reasonCode=st.reason_code,
                reasonText=st.reason_text,
            )
            completed += 1
            emit(
                "job_progress",
                completed=completed,
                total=total,
                **summarize_results(states, blocked=blocked).__dict__,
            )
            continue

        # dependency gate
        dep_failed = None
        for dep in spec.dependencies:
            dep_state = states.get(dep)
            if dep_state and dep_state.status in {
                STATUS_INSTALL_FAILED,
                STATUS_VERIFY_FAILED,
                STATUS_ENABLE_FAILED,
                STATUS_SKIPPED_DEPENDENCY,
                STATUS_BLOCKED_PREFLIGHT,
            }:
                dep_failed = dep
                break
        if dep_failed:
            st.status = STATUS_SKIPPED_DEPENDENCY
            st.stage = "pending"
            st.reason_code = "dependency_failed"
            st.reason_text = f"dependency_failed:{dep_failed}"
            emit(
                "feature_skipped",
                feature=key,
                status=st.status,
                reasonCode=st.reason_code,
                reasonText=st.reason_text,
            )
            completed += 1
            emit(
                "job_progress",
                completed=completed,
                total=total,
                **summarize_results(states, blocked=blocked).__dict__,
            )
            continue

        emit("feature_started", feature=key, position=idx, total=total)

        # install
        st.status = STATUS_INSTALLING
        st.stage = "install"
        emit("stage_started", feature=key, stage="install")
        if spec.privileged and run_options.privileged_run_fn is not None:
            try:
                res = run_options.privileged_run_fn(key, spec)
            except Exception as exc:  # pragma: no cover - defensive path
                res = {"ok": False, "reason_text": str(exc)}
            if not isinstance(res, dict):
                res = _ok_result() if res else {"ok": False, "reason_text": "privileged step returned false"}
        else:
            res = _run_step(spec.install_fn)
        install_needs_reboot = bool(res.get("requires_reboot"))
        emit(
            "stage_completed",
            feature=key,
            stage="install",
            ok=bool(res.get("ok", False)),
            durationMs=int(res.get("duration_ms", 0)),
            stderrTail=res.get("stderr_tail"),
            stdoutTail=res.get("stdout_tail"),
        )
        if not res.get("ok", False):
            st.status = STATUS_INSTALL_FAILED
            st.reason_code = str(res.get("reason_code") or "INSTALL_FAILED")
            st.reason_text = str(res.get("reason_text") or "install failed")
            st.suggested_action = res.get("suggested_action")
            emit(
                "feature_terminal",
                feature=key,
                status=st.status,
                reasonCode=st.reason_code,
                reasonText=st.reason_text,
                suggestedAction=st.suggested_action,
            )
            completed += 1
            emit(
                "job_progress",
                completed=completed,
                total=total,
                **summarize_results(states, blocked=blocked).__dict__,
            )
            continue

        # verify
        st.stage = "verify"
        emit("stage_started", feature=key, stage="verify")
        res = _run_step(spec.verify_fn)
        emit(
            "stage_completed",
            feature=key,
            stage="verify",
            ok=bool(res.get("ok", False)),
            durationMs=int(res.get("duration_ms", 0)),
            stderrTail=res.get("stderr_tail"),
            stdoutTail=res.get("stdout_tail"),
        )
        if not res.get("ok", False):
            st.status = STATUS_VERIFY_FAILED
            st.reason_code = str(res.get("reason_code") or "VERIFY_FAILED")
            st.reason_text = str(res.get("reason_text") or "verify failed")
            st.suggested_action = res.get("suggested_action")
            emit(
                "feature_terminal",
                feature=key,
                status=st.status,
                reasonCode=st.reason_code,
                reasonText=st.reason_text,
                suggestedAction=st.suggested_action,
            )
            completed += 1
            emit(
                "job_progress",
                completed=completed,
                total=total,
                **summarize_results(states, blocked=blocked).__dict__,
            )
            continue

        # enable
        if spec.enable_policy == "if_installed":
            st.stage = "enable"
            emit("stage_started", feature=key, stage="enable")
            res = _run_step(spec.enable_fn)
            emit(
                "stage_completed",
                feature=key,
                stage="enable",
                ok=bool(res.get("ok", False)),
                durationMs=int(res.get("duration_ms", 0)),
                stderrTail=res.get("stderr_tail"),
                stdoutTail=res.get("stdout_tail"),
            )
            if not res.get("ok", False):
                st.status = STATUS_ENABLE_FAILED
                st.reason_code = str(res.get("reason_code") or "ENABLE_FAILED")
                st.reason_text = str(res.get("reason_text") or "enable failed")
                st.suggested_action = res.get("suggested_action")
                emit(
                    "feature_terminal",
                    feature=key,
                    status=st.status,
                    reasonCode=st.reason_code,
                    reasonText=st.reason_text,
                    suggestedAction=st.suggested_action,
                )
                completed += 1
                emit(
                    "job_progress",
                    completed=completed,
                    total=total,
                    **summarize_results(states, blocked=blocked).__dict__,
                )
                continue
            st.status = STATUS_INSTALLED_ENABLED_NOT_STARTED
        else:
            st.status = STATUS_INSTALLED

        if spec.requires_reboot or install_needs_reboot:
            st.status = STATUS_INSTALLED_NEEDS_REBOOT

        emit("feature_terminal", feature=key, status=st.status)
        completed += 1
        emit(
            "job_progress",
            completed=completed,
            total=total,
            **summarize_results(states, blocked=blocked).__dict__,
        )

    summary = summarize_results(states, blocked=blocked)
    code = 130 if canceled else terminal_exit_code(summary)
    if canceled:
        emit("job_canceled")
    emit("job_finished", ok=(code == 0), exitCode=code, summary=summary.__dict__)
    return states, blocked, summary


def summarize_results(states: Dict[str, FeatureState], blocked=None) -> JobSummary:
    blocked = blocked or []
    green_states = {STATUS_INSTALLED, STATUS_INSTALLED_ENABLED_NOT_STARTED}
    amber_states = {STATUS_INSTALLED_NEEDS_REBOOT}
    red_states = {STATUS_INSTALL_FAILED, STATUS_VERIFY_FAILED, STATUS_ENABLE_FAILED}
    gray_states = {STATUS_PENDING, STATUS_SKIPPED_DEPENDENCY, STATUS_BLOCKED_PREFLIGHT, STATUS_CANCELED}

    green = amber = red = gray = 0
    features = []
    for key in states:
        st = states[key]
        features.append(
            {
                "feature": st.feature,
                "status": st.status,
                "reasonCode": st.reason_code,
                "reasonText": st.reason_text,
                "suggestedAction": st.suggested_action,
            }
        )
        if st.status in green_states:
            green += 1
        elif st.status in amber_states:
            amber += 1
        elif st.status in red_states:
            red += 1
        elif st.status in gray_states:
            gray += 1

    # blocked items are outside ordered features, but still count as gray/red boundary.
    for b in blocked:
        gray += 1
        features.append(
            {
                "feature": b.get("feature", "unknown"),
                "status": STATUS_BLOCKED_PREFLIGHT,
                "reasonCode": b.get("reason_code"),
                "reasonText": b.get("reason_text"),
                "suggestedAction": None,
            }
        )

    return JobSummary(green=green, amber=amber, red=red, gray=gray, features=features)


def terminal_exit_code(summary: JobSummary) -> int:
    return 1 if summary.red > 0 else 0


def build_default_registry(repo_root: str):
    """Placeholder default registry; callers can override with richer maps."""
    return {}
