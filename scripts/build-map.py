#!/usr/bin/env python3
"""
build-map.py
------------
Convert a BBBike GPKG extract into a PMTiles file and register it in
maps/map-config.json. Wraps the ogr2ogr + tippecanoe pipeline.

Requires ogr2ogr (GDAL) and tippecanoe installed on the prep machine:
  macOS:  brew install gdal tippecanoe
  Linux:  sudo apt install gdal-bin  (tippecanoe must be built from source)

Usage:
  python3 scripts/build-map.py planet_-122.603_47.241_391b4027.gpkg \\
    --bbox -122.17 47.47 -121.93 47.58 \\
    --name issaquah \\
    --center -122.0321 47.5301 \\
    --zoom 13

  # Cap zoom range to save space on a Pi SD card:
  python3 scripts/build-map.py <file>.gpkg \\
    --bbox WEST SOUTH EAST NORTH \\
    --name <area> \\
    --min-zoom 5 --max-zoom 15

  # Skip the tippecanoe step (ogr2ogr only):
  python3 scripts/build-map.py <file>.gpkg --bbox ... --name <area> --geojson-only

The script writes intermediate GeoJSON files to a temp directory and cleans
them up on success. The finished PMTiles file is copied to maps/ and
maps/map-config.json is updated automatically.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

# ── Paths ──────────────────────────────────────────────────────────────────────
REPO_ROOT   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPS_DIR    = os.path.join(REPO_ROOT, "maps")
MAP_CONFIG  = os.path.join(MAPS_DIR, "map-config.json")

# ── Helpers ────────────────────────────────────────────────────────────────────
def _hr():
    print("─" * 60)

def _step(n, label):
    print(f"\n[{n}] {label}")
    _hr()

def _ok(msg):   print(f"    ✓  {msg}")
def _info(msg): print(f"       {msg}")
def _warn(msg): print(f"    ⚠  {msg}")
def _fail(msg):
    print(f"\n  ERROR: {msg}", file=sys.stderr)
    sys.exit(1)

def _run(cmd):
    print(f"       $ {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        _fail(f"Command failed (exit {result.returncode})")


# ── Step 1: Check tools ────────────────────────────────────────────────────────
def check_tools(geojson_only):
    _step(1, "Checking required tools")
    for tool in (["ogr2ogr", "--version"], ["tippecanoe", "--version"] if not geojson_only else []):
        if not tool:
            continue
        which = shutil.which(tool[0])
        if not which:
            _fail(
                f"'{tool[0]}' not found.\n"
                f"  macOS:  brew install {'gdal' if tool[0] == 'ogr2ogr' else 'tippecanoe'}\n"
                f"  Linux:  see README.md — Offline Maps — Prerequisites"
            )
        result = subprocess.run(tool, capture_output=True, text=True)
        version_line = (result.stdout or result.stderr).splitlines()[0]
        _ok(f"{tool[0]} — {version_line}")


# ── Step 2: ogr2ogr — GPKG → GeoJSON ──────────────────────────────────────────
def convert_layers(gpkg, bbox, workdir):
    west, south, east, north = bbox
    _step(2, f"Converting GPKG layers  (bbox: {west} {south} {east} {north})")
    _info(f"Source: {gpkg}")

    layers = ["lines", "multipolygons", "points"]
    geojson_files = []

    for layer in layers:
        out = os.path.join(workdir, f"{layer}.geojson")
        _info(f"→ {layer}.geojson")
        _run([
            "ogr2ogr",
            "-f", "GeoJSON",
            "-t_srs", "EPSG:4326",
            "-clipsrc", str(west), str(south), str(east), str(north),
            out, gpkg, layer,
        ])
        size_mb = os.path.getsize(out) / 1_048_576
        _ok(f"{layer}.geojson  ({size_mb:.1f} MB)")
        geojson_files.append(out)

    return geojson_files


# ── Step 3: tippecanoe — GeoJSON → PMTiles ────────────────────────────────────
def build_pmtiles(geojson_files, name, workdir, min_zoom, max_zoom):
    _step(3, f"Building PMTiles  →  {name}.pmtiles")

    out = os.path.join(workdir, f"{name}.pmtiles")

    cmd = ["tippecanoe", "-o", out, "--drop-densest-as-needed", "--force"]

    if min_zoom is not None and max_zoom is not None:
        cmd += [f"-Z{min_zoom}", f"-z{max_zoom}"]
        _info(f"Zoom range: {min_zoom}–{max_zoom}")
    elif max_zoom is not None:
        cmd += [f"-z{max_zoom}"]
        _info(f"Max zoom: {max_zoom}")
    else:
        cmd.append("-zg")
        _info("Zoom: auto (-zg)")

    cmd += geojson_files
    _run(cmd)

    size_mb = os.path.getsize(out) / 1_048_576
    _ok(f"{name}.pmtiles  ({size_mb:.1f} MB)")
    return out


# ── Step 4: Copy to maps/ ──────────────────────────────────────────────────────
def install_pmtiles(src, name):
    _step(4, f"Installing to maps/")
    dest = os.path.join(MAPS_DIR, f"{name}.pmtiles")
    shutil.copy2(src, dest)
    size_mb = os.path.getsize(dest) / 1_048_576
    _ok(f"maps/{name}.pmtiles  ({size_mb:.1f} MB)")
    return dest


# ── Step 5: Update map-config.json ────────────────────────────────────────────
def update_config(name, center, zoom):
    _step(5, "Updating maps/map-config.json")

    config = {}
    if os.path.exists(MAP_CONFIG):
        with open(MAP_CONFIG) as f:
            config = json.load(f)

    key = f"{name}.pmtiles"
    entry = {"center": list(center), "zoom": zoom}
    config[key] = entry

    with open(MAP_CONFIG, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")

    _ok(f'"{key}": {json.dumps(entry)}')
    _info(f"All entries in map-config.json:")
    for k, v in config.items():
        _info(f"  {k}: center={v['center']}, zoom={v['zoom']}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Convert a BBBike GPKG to PMTiles and register it in OASIS."
    )
    parser.add_argument("gpkg", help="Path to the .gpkg file from BBBike")
    parser.add_argument(
        "--bbox", nargs=4, metavar=("WEST", "SOUTH", "EAST", "NORTH"),
        type=float, required=True,
        help="Clipping bounding box (decimal degrees)",
    )
    parser.add_argument("--name", required=True,
        help="Output name, e.g. 'issaquah' → issaquah.pmtiles")
    parser.add_argument("--center", nargs=2, metavar=("LON", "LAT"),
        type=float, default=None,
        help="Map center for map-config.json [longitude latitude]")
    parser.add_argument("--zoom", type=int, default=12,
        help="Default zoom level for map-config.json (default: 12)")
    parser.add_argument("--min-zoom", type=int, default=None,
        help="Minimum tile zoom (tippecanoe -Z). Omit to let tippecanoe decide.")
    parser.add_argument("--max-zoom", type=int, default=None,
        help="Maximum tile zoom (tippecanoe -z). Omit for auto (-zg).")
    parser.add_argument("--geojson-only", action="store_true",
        help="Run ogr2ogr only, skip tippecanoe and install steps")
    args = parser.parse_args()

    gpkg = os.path.abspath(args.gpkg)
    if not os.path.exists(gpkg):
        _fail(f"File not found: {gpkg}")

    print(f"\nOASIS — build-map")
    _hr()
    _info(f"GPKG   : {gpkg}")
    _info(f"bbox   : {args.bbox}")
    _info(f"output : {args.name}.pmtiles")

    check_tools(args.geojson_only)

    with tempfile.TemporaryDirectory(prefix="oasis-map-") as workdir:
        geojson_files = convert_layers(gpkg, args.bbox, workdir)

        if args.geojson_only:
            _info("--geojson-only: stopping after ogr2ogr.")
            return

        pmtiles = build_pmtiles(geojson_files, args.name, workdir, args.min_zoom, args.max_zoom)
        install_pmtiles(pmtiles, args.name)

    center = args.center if args.center else [
        (args.bbox[0] + args.bbox[2]) / 2,
        (args.bbox[1] + args.bbox[3]) / 2,
    ]
    update_config(args.name, center, args.zoom)

    print(f"\n{'─' * 60}")
    _ok(f"Done. Open: http://<pi-ip>:8083/maps/map.html")
    print()


if __name__ == "__main__":
    main()
