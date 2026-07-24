#!/usr/bin/env python3
"""ai/runtime/install.py — install the AI assistant runtime on a Pi 5.

Fetches (once, at setup) the prebuilt llama.cpp llama-server binary and the
Qwen GGUF model, verifies them by SHA-256, installs mcp+httpx into the venv,
and writes/enables the oasis-ai systemd unit whose ExecStart runs llama-server
as the OpenAI-compatible endpoint ai/config.json points at (127.0.0.1:8087).

Idempotent and version-aware: present+valid files are not re-downloaded.
Gated to Pi 5 / 8 GB / Python>=3.10 — a clean, non-fatal skip elsewhere.

Run:  python3 ai/runtime/install.py            # gate, fetch, verify, enable
      python3 ai/runtime/install.py --no-enable # write unit, don't start
      python3 ai/runtime/install.py --help
"""
import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_SUITE_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_BIN_DIR = os.path.join(_HERE, "bin")
_MODELS_DIR = os.path.join(_SUITE_ROOT, "ai", "models")
_MANIFEST = os.path.join(_SUITE_ROOT, "scripts", "offline-manifest.json")

sys.path.insert(0, _SUITE_ROOT)
try:
    from common import server as S
except ImportError:  # pragma: no cover - common should always be importable
    S = None

_MIN_RAM_BYTES = int(7.0 * 1024**3)
_SERVICE = "oasis-ai"
_SERVICE_FILE = f"/etc/systemd/system/{_SERVICE}.service"
_PORT = 8087
_CTX = 4096


def removal_record(repo_root=None):
    """Teardown record for the ai feature (see common/removal.py). Removes the
    oasis-ai service and the llama-server binary dir; the GGUF model (~1.9 GB,
    hard to re-download offline) is advisory-only. mcp/httpx were installed into
    the shared .venv and are left in place."""
    return {
        "services": [_SERVICE],
        "dirs": [_BIN_DIR],
        "data_paths": [_MODELS_DIR],
        "notes": ["AI model kept; mcp/httpx left in the shared .venv."],
    }


def _total_ram_bytes():
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return 0


def gate():
    if platform.system() != "Linux":
        return False, "AI assistant runs on Linux/Pi only (on macOS dev use: brew install llama.cpp)"
    machine = platform.machine().lower()
    if machine not in ("aarch64", "arm64"):
        return False, f"unsupported arch {machine!r} (AI assistant needs arm64/aarch64)"
    if sys.version_info < (3, 10):
        return False, "Python >=3.10 required (mcp SDK); this interpreter is older"
    if _total_ram_bytes() < _MIN_RAM_BYTES:
        return False, "insufficient RAM (AI assistant needs a Pi 5 / 8 GB class board)"
    return True, "ok"


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download_verified(url, dest, sha256, *, timeout=60):
    if os.path.exists(dest) and sha256_of(dest) == sha256:
        return True  # idempotent: already have the right file
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest), suffix=".part")
    try:
        h = hashlib.sha256()
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(url, timeout=timeout) as resp:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                h.update(chunk)
        if h.hexdigest() != sha256:
            os.unlink(tmp)
            return False
        os.replace(tmp, dest)
        return True
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def extract_llama(tarball, bin_dir):
    os.makedirs(bin_dir, exist_ok=True)
    server_path = None
    with tarfile.open(tarball, "r:*") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            base = os.path.basename(member.name)
            # keep the runtime bits: the server binary and shared libs
            if base == "llama-server" or base.endswith(".so") or ".so." in base:
                src = tf.extractfile(member)
                if src is None:
                    continue
                out_path = os.path.join(bin_dir, base)
                with open(out_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                if base == "llama-server":
                    os.chmod(out_path, 0o755)
                    server_path = out_path
    if server_path is None:
        raise RuntimeError("llama-server not found in the archive")
    return server_path


def build_unit(*, binary, model, bin_dir, user, port=_PORT, ctx=_CTX, threads):
    return f"""[Unit]
Description=OASIS AI assistant (llama-server)
After=network.target

[Service]
Type=simple
User={user}
Environment=LD_LIBRARY_PATH={bin_dir}
ExecStart={binary} --jinja -m {model} --host 127.0.0.1 --port {port} -c {ctx} -t {threads}
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
"""


def _manifest_entry(key):
    with open(_MANIFEST, encoding="utf-8") as fh:
        return json.load(fh)["features"][key]


def _run(cmd, **kw):
    return subprocess.run(cmd, **kw)


def _install_wheels():
    """Install mcp+httpx into the OASIS venv (not sys.executable) — bundled
    wheels first, PyPI as fallback — the same machinery
    services/satellites/install-predict.py uses, so behavior doesn't drift.
    Returns True iff mcp+httpx import cleanly in the venv afterward."""
    if S is None:
        print("[ai] WARNING: common.server not importable — cannot install "
              "mcp/httpx", file=sys.stderr)
        return False

    venv_dir = os.path.join(_SUITE_ROOT, ".venv")
    wheels_dir = os.path.join(_SUITE_ROOT, "server", "wheels")
    pip = S._venv_bin(venv_dir, "pip")

    if not os.path.exists(S._venv_bin(venv_dir, "python")):
        print(f"[ai] WARNING: no OASIS venv at {venv_dir} — run "
              "scripts/setup-server.py first", file=sys.stderr)
        return False

    specs = [f"{p['name']}{p['version']}" for p in _manifest_entry("ai")["packages"]]

    online, banner = S.decide_source(wheels_dir)
    if online is None:
        print("[ai] WARNING: server/wheels/ is empty and no internet is "
              "reachable — can't install mcp/httpx now", file=sys.stderr)
        return False
    S.print_source_banner(online, banner, wheels_dir)

    for spec in specs:
        S.install_one(pip, spec, online, wheels_dir)

    return S._import_ok(venv_dir, "import mcp, httpx")


def _write_and_enable_unit(unit_text, enable):
    proc = subprocess.Popen(["sudo", "tee", _SERVICE_FILE],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    proc.communicate(unit_text.encode())
    _run(["sudo", "chmod", "644", _SERVICE_FILE], check=False)
    _run(["sudo", "systemctl", "daemon-reload"], check=False)
    if enable:
        _run(["sudo", "systemctl", "enable", "--now", f"{_SERVICE}.service"], check=False)


def main(argv=None):
    p = argparse.ArgumentParser(description="Install the OASIS AI assistant runtime.")
    p.add_argument("--no-enable", action="store_true",
                   help="Write the systemd unit but don't enable/start it.")
    p.add_argument("--skip-model", action="store_true",
                   help="Skip the (large) model download — binary + unit only.")
    args = p.parse_args(argv)

    ok, reason = gate()
    if not ok:
        print(f"[ai] skipping AI assistant install: {reason}")
        return 0  # non-fatal skip

    llama = _manifest_entry("ai-llama")
    model = _manifest_entry("ai-model")
    version = llama["version"]
    asset = llama["asset_pattern"].format(version=version)
    binary_url = f"https://github.com/{llama['repo']}/releases/download/{version}/{asset}"

    with tempfile.TemporaryDirectory() as td:
        tarball = os.path.join(td, asset)
        print(f"[ai] fetching {asset} …")
        # GitHub release assets have no manifest sha; verify by successful extract.
        with urllib.request.urlopen(binary_url, timeout=120) as resp, open(tarball, "wb") as out:
            shutil.copyfileobj(resp, out)
        server = extract_llama(tarball, _BIN_DIR)
    print(f"[ai] llama-server → {server}")

    model_path = os.path.join(_SUITE_ROOT, model["out"])
    if not args.skip_model:
        print(f"[ai] fetching model (~{model['size_bytes'] // 1024**2} MB) …")
        if not download_verified(model["url"], model_path, model["sha256"], timeout=120):
            print("[ai] ERROR: model SHA-256 mismatch", file=sys.stderr)
            return 1
        print(f"[ai] model → {model_path}")

    if not _install_wheels():
        print("[ai] ERROR: could not install mcp/httpx into the venv", file=sys.stderr)
        return 1

    user = os.environ.get("SUDO_USER") or os.environ.get("USER") or "oasis"
    unit = build_unit(binary=server, model=model_path, bin_dir=_BIN_DIR,
                      user=user, threads=os.cpu_count() or 4)
    _write_and_enable_unit(unit, enable=not args.no_enable)
    print(f"[ai] {_SERVICE} unit written ({'enabled' if not args.no_enable else 'not started'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
