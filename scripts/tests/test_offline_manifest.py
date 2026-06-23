#!/usr/bin/env python3
"""
Self-tests for the OASIS offline package manifest + reader.

Run: python3 scripts/tests/test_offline_manifest.py
No pytest needed — plain asserts; exits non-zero on the first failure.

Guards the suite-aware / capability-gate / newest-source-wins behaviour that the
"bookworm bundle installed librtlsdr 0.6.0 on Trixie" bug exposed. See
docs/offline-architecture.md.
"""

import os
import sys

HERE    = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
sys.path.insert(0, SCRIPTS)

from common import manifest as M


def check(name, cond):
    print(("ok  : " if cond else "FAIL: ") + name)
    if not cond:
        sys.exit(1)


def main():
    M.load_manifest()

    # ── Suite-awareness ──────────────────────────────────────────────────────
    check("suites include bookworm + trixie",
          {"bookworm", "trixie"} <= set(M.suites()))
    check("rtl-sdr is an apt feature", M.source_type("rtl-sdr") == "apt")
    check("trixie resolves librtlsdr2",
          "librtlsdr2" in M.apt_packages("rtl-sdr", "trixie"))
    check("bookworm resolves librtlsdr0",
          "librtlsdr0" in M.apt_packages("rtl-sdr", "bookworm"))

    # ── Capability gate (RTL-SDR Blog V4 needs librtlsdr >= 2.0) ──────────────
    check("gate flags librtlsdr 0.6.0",
          bool(M.check_min_version("rtl-sdr", {"librtlsdr": "0.6.0"})))
    check("gate passes librtlsdr 2.0.2",
          not M.check_min_version("rtl-sdr", {"librtlsdr": "2.0.2"}))

    # ── Newest-source-wins (the exact RasPad regression) ─────────────────────
    check("fresh host: apt 2.0.2 beats stale bundle 0.6.0",
          M.resolve_source(None, "2.0.2", "0.6.0") == "apt")
    check("never downgrade: installed 2.0.2 vs bundle 0.6.0 -> skip",
          M.resolve_source("2.0.2", None, "0.6.0") == "skip")
    check("offline: suite-matched bundle is used",
          M.resolve_source(None, None, "2.0.2") == "bundle")
    check("nothing installed, nothing available -> None",
          M.resolve_source(None, None, None) is None)

    # ── Bundle layout (builder + installer must agree) ───────────────────────
    check("apt bundle dir is suite-scoped",
          M.bundle_dir("offline-packages", "rtl-sdr", "trixie")
          .endswith(os.path.join("rtl-sdr", "trixie")))
    check("rtl-sdr-feed shares the rtl-sdr bundle group",
          M.bundle_group("rtl-sdr-feed") == "rtl-sdr")
    check("winlink resolves to the pat bundle group",
          M.bundle_dir("offline-packages", "winlink").endswith("pat"))

    # ── Contract corrections ─────────────────────────────────────────────────
    check("graywolf has no armhf asset", "armhf" not in M.feature_arches("graywolf"))

    print("\nAll offline-manifest self-tests passed.")


if __name__ == "__main__":
    main()
