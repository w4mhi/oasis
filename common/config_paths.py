"""Single source of truth for OASIS shared-config file locations.

All operator-facing shared config lives under <repo_root>/configuration/.
Callers pass their already-resolved repo root (suite root) — the same value
each module already computes — and get an absolute path back. The folder name
lives here ONCE, so relocating/renaming it later is a one-file change.
"""
import os

CONFIG_DIRNAME = "configuration"

def config_dir(repo_root):
    return os.path.join(repo_root, CONFIG_DIRNAME)

def station_json(repo_root):
    return os.path.join(config_dir(repo_root), "station.json")

def installed_services_json(repo_root):
    return os.path.join(config_dir(repo_root), "installed-services.json")

def user_folders_json(repo_root):
    return os.path.join(config_dir(repo_root), "user-folders.json")

def aprs_frequencies_json(repo_root):
    return os.path.join(config_dir(repo_root), "aprs_frequencies.json")

def hazards_json(repo_root):
    return os.path.join(config_dir(repo_root), "hazards.json")

def satellites_json(repo_root):
    return os.path.join(config_dir(repo_root), "satellites.json")

def tle_cache_dir(repo_root):
    return os.path.join(config_dir(repo_root), "tle-cache")

def hardware_json(repo_root):
    return os.path.join(config_dir(repo_root), "hardware.json")

def installer_queue_dir(repo_root):
    return os.path.join(config_dir(repo_root), "installer-queue")
