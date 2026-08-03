#!/usr/bin/env python3
"""mapctl — install the pmtiles CLI and download offline vector basemaps.

Standalone tooling for the maps module. Works two ways from one file:

  • As a CLI:   python modules/maps/mapctl.py <command> ...
  • As a lib:   the direwolf-gui web API (server/api/tiles.py) imports these
                functions so there is a single implementation.

It cuts per-region `.pmtiles` archives out of a planet source with
`pmtiles extract`, choosing the area by US state name (from us-states.geojson),
a bounding box, or a GeoJSON region file — mirroring pmtiles' own options.

Pure standard library; no third-party or server dependencies.
"""

import argparse
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

# pmtiles CLI (protomaps/go-pmtiles), pinned so the artifact is deterministic.
PMTILES_VERSION = "1.31.2"
_BASE = ("https://github.com/protomaps/go-pmtiles/releases/download/v"
         + PMTILES_VERSION + "/")
_ARCH = {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}

# Documented default planet source — Protomaps prunes old dated builds, so update
# the date (build.protomaps.com) or pass --source / DIREWOLF_GUI_TILES_SOURCE.
DEFAULT_SOURCE = "https://build.protomaps.com/20260801.pmtiles"

# Map/output names: letters, digits, spaces, underscore, hyphen — no dot or slash,
# so a name can never traverse out of the maps dir or reach a sibling.
NAME_RE = re.compile(r"^[A-Za-z0-9 _-]+$")


class MapctlError(Exception):
    pass


class UnsupportedPlatform(MapctlError):
    pass


class PmtilesMissing(MapctlError):
    pass


def default_maps_dir():
    """The module's own tiles/ dir (sibling of this file), or the env override."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.environ.get("DIREWOLF_GUI_MAPS", os.path.join(here, "tiles"))


def default_source():
    return (os.environ.get("OASIS_TILES_SOURCE")
            or os.environ.get("DIREWOLF_GUI_TILES_SOURCE")
            or DEFAULT_SOURCE)


def pmtiles_asset(system=None, machine=None):
    """Release asset (url + archive kind) for a platform, or None if unsupported.

    Naming differs per-OS: Darwin uses `go-pmtiles-<v>_Darwin_<arch>.zip`
    (hyphen, zip); Linux uses `go-pmtiles_<v>_Linux_<arch>.tar.gz`."""
    system = system or platform.system()
    machine = machine or platform.machine()
    arch = _ARCH.get(machine.lower())
    if arch is None:
        return None
    v = PMTILES_VERSION
    if system == "Darwin":
        return {"url": _BASE + "go-pmtiles-%s_Darwin_%s.zip" % (v, arch), "kind": "zip"}
    if system == "Linux":
        return {"url": _BASE + "go-pmtiles_%s_Linux_%s.tar.gz" % (v, arch), "kind": "tar.gz"}
    return None


def local_pmtiles(maps_dir):
    """Path to a pmtiles binary we downloaded into <maps_dir>/.bin (may not exist)."""
    return os.path.join(maps_dir, ".bin", "pmtiles")


def resolve_pmtiles(maps_dir):
    """The pmtiles executable to run: our downloaded copy first, else PATH."""
    local = local_pmtiles(maps_dir)
    if os.path.isfile(local) and os.access(local, os.X_OK):
        return local
    return shutil.which("pmtiles")


def _states_geojson(maps_dir, states_geojson=None):
    # OASIS keeps us-states.geojson at maps/ while tiles land in maps/tiles/state;
    # the explicit override lets the geojson live apart from the output dir.
    return states_geojson or os.path.join(maps_dir, "us-states.geojson")


def load_states(maps_dir, states_geojson=None):
    """name -> feature, from the states geojson (empty dict if it's missing)."""
    try:
        with open(_states_geojson(maps_dir, states_geojson)) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    out = {}
    for feat in data.get("features", []):
        name = (feat.get("properties") or {}).get("name")
        if name:
            out[name] = feat
    return out


def available_maps(maps_dir):
    """[{name, bytes}] for every *.pmtiles archive in maps_dir, sorted by name."""
    try:
        names = os.listdir(maps_dir)
    except OSError:
        return []
    maps = []
    for fn in names:
        if fn.endswith(".pmtiles"):
            path = os.path.join(maps_dir, fn)
            maps.append({"name": fn[:-len(".pmtiles")], "bytes": os.path.getsize(path)})
    maps.sort(key=lambda m: m["name"])
    return maps


def _extract_binary(archive_bytes, kind, dest):
    """Pull the single `pmtiles` member out of the release archive into dest."""
    if kind == "zip":
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
            data = zf.read("pmtiles")
    else:  # tar.gz
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tf:
            data = tf.extractfile("pmtiles").read()
    with open(dest, "wb") as fh:
        fh.write(data)
    os.chmod(dest, 0o755)


def install_pmtiles(maps_dir):
    """Download the go-pmtiles binary into <maps_dir>/.bin. Yields progress lines.

    Raises UnsupportedPlatform when there is no prebuilt binary for this OS/arch."""
    asset = pmtiles_asset()
    if asset is None:
        raise UnsupportedPlatform(
            "no prebuilt pmtiles for %s/%s" % (platform.system(), platform.machine()))
    dest = local_pmtiles(maps_dir)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    yield "downloading pmtiles %s\n" % PMTILES_VERSION
    yield asset["url"] + "\n"
    with urllib.request.urlopen(asset["url"]) as resp:  # noqa: S310 (pinned HTTPS)
        data = resp.read()
    yield "downloaded %.1f MB, extracting…\n" % (len(data) / (1024 * 1024))
    _extract_binary(data, asset["kind"], dest)
    yield "[install complete] pmtiles ready at %s\n" % dest


def _region_args(maps_dir, state, bbox, region, states_geojson=None):
    """(extra pmtiles-extract args, tempfile-to-clean-or-None) for the area.

    Exactly one of state / bbox / region must be given."""
    given = [x for x in (state, bbox, region) if x]
    if len(given) != 1:
        raise ValueError("exactly one of --state / --bbox / --region is required")
    if state:
        feat = load_states(maps_dir, states_geojson).get(state)
        if feat is None:
            raise ValueError("unknown state: %r" % state)
        tf = tempfile.NamedTemporaryFile(mode="w", suffix=".geojson",
                                         delete=False, dir=maps_dir)
        json.dump({"type": "FeatureCollection", "features": [feat]}, tf)
        tf.close()
        return ["--region=" + tf.name], tf.name
    if region:
        return ["--region=" + region], None
    return ["--bbox=" + bbox], None


def extract(maps_dir, *, name, source=None, state=None, bbox=None, region=None,
            maxzoom=None, minzoom=None, states_geojson=None, on_start=None):
    """Run `pmtiles extract` into <maps_dir>/<name>.pmtiles. Yields progress lines.

    Area chosen by exactly one of state / bbox / region. Raises PmtilesMissing if
    the CLI can't be resolved, ValueError for a bad name / unknown state / no area."""
    if not name or not NAME_RE.match(name):
        raise ValueError("invalid output name: %r" % name)
    pmt = resolve_pmtiles(maps_dir)
    if pmt is None:
        raise PmtilesMissing("pmtiles CLI not installed (run: mapctl install)")
    src = source or default_source()
    out_path = os.path.join(maps_dir, name + ".pmtiles")
    args, tmp = _region_args(maps_dir, state, bbox, region, states_geojson)
    if maxzoom is not None:
        args.append("--maxzoom=%d" % maxzoom)
    if minzoom is not None:
        args.append("--minzoom=%d" % minzoom)
    try:
        if os.path.exists(out_path):
            os.remove(out_path)
        cmd = [pmt, "extract", src, out_path] + args
        yield "$ " + " ".join(cmd) + "\n"
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        if on_start:
            on_start(proc)
        for line in proc.stdout:
            yield line
        proc.wait()
        if proc.returncode == 0:
            yield "\n[extract complete] saved %s.pmtiles\n" % name
        else:
            yield "\n[extract failed] exit %d\n" % proc.returncode
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


# ── CLI ──────────────────────────────────────────────────────────────────────

def _cmd_install(args):
    for line in install_pmtiles(args.maps_dir):
        sys.stdout.write(line)
        sys.stdout.flush()
    return 0


def _cmd_list(args):
    md = args.maps_dir
    print("maps dir: %s" % md)
    pmt = resolve_pmtiles(md)
    print("pmtiles:  %s" % (pmt or "not installed (run: mapctl install)"))
    maps = available_maps(md)
    print("\ndownloaded (%d):" % len(maps))
    for m in maps:
        print("  %-24s %6.1f MB" % (m["name"], m["bytes"] / (1024 * 1024)))
    states = sorted(load_states(md, getattr(args, "states_geojson", None)).keys())
    print("\nstates available to download (%d):" % len(states))
    print("  " + ", ".join(states) if states else "  (us-states.geojson not found)")
    return 0


def _cmd_download(args):
    name = args.name or args.state
    if not name:
        raise SystemExit("--name is required with --bbox / --region")
    for line in extract(args.maps_dir, name=name, source=args.source,
                        state=args.state, bbox=args.bbox, region=args.region,
                        maxzoom=args.maxzoom, minzoom=args.minzoom,
                        states_geojson=getattr(args, "states_geojson", None)):
        sys.stdout.write(line)
        sys.stdout.flush()
    return 0


def main(argv=None):
    # --maps-dir / --states-geojson are accepted in EITHER position (before OR
    # after the subcommand) via a shared parent parser. SUPPRESS defaults so the
    # subparser's copy never clobbers a value given before the subcommand; the
    # real defaults are filled in after parsing.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--maps-dir", dest="maps_dir", default=argparse.SUPPRESS,
                        help="folder holding the .pmtiles archives (default: the module's tiles/)")
    common.add_argument("--states-geojson", dest="states_geojson", default=argparse.SUPPRESS,
                        help="path to us-states.geojson (default: <maps-dir>/us-states.geojson)")

    p = argparse.ArgumentParser(prog="mapctl", description=__doc__.splitlines()[0],
                                parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("install", parents=[common],
                   help="download the pmtiles CLI into <maps-dir>/.bin")
    sub.add_parser("list", parents=[common],
                   help="list downloaded maps and available states")

    d = sub.add_parser("download", parents=[common],
                       help="extract a region into <maps-dir>/<name>.pmtiles")
    area = d.add_mutually_exclusive_group(required=True)
    area.add_argument("--state", help="US state name (from us-states.geojson)")
    area.add_argument("--bbox", help="min_lon,min_lat,max_lon,max_lat")
    area.add_argument("--region", help="path to a GeoJSON Polygon/MultiPolygon file")
    d.add_argument("--name", "-o", help="output name (defaults to the state name)")
    d.add_argument("--source", default=None, help="planet .pmtiles URL or path")
    d.add_argument("--maxzoom", type=int, default=None)
    d.add_argument("--minzoom", type=int, default=None)

    args = p.parse_args(argv)
    if not getattr(args, "maps_dir", None):
        args.maps_dir = default_maps_dir()
    if not getattr(args, "states_geojson", None):
        args.states_geojson = None
    try:
        return {"install": _cmd_install, "list": _cmd_list,
                "download": _cmd_download}[args.cmd](args)
    except (MapctlError, ValueError) as exc:
        raise SystemExit("mapctl: %s" % exc)


if __name__ == "__main__":
    sys.exit(main())
