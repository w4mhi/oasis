"""rtl_connector — the process that holds the dongle and serves IQ on a socket.

This module knows nothing about satellites, orbits or Doppler. It starts one
`rtl_connector`, waits until its data socket actually accepts a connection, and
kills it again. That is the whole job.

WHY A CONNECTOR AND NOT rtl_fm
------------------------------
rtl_fm parses -f once and offers no way in, so following a moving signal means
killing and relaunching the pipeline. rtl_connector instead publishes raw IQ on
a TCP socket, which pycsdr reads directly (TcpSource) — and the Doppler tracking
then happens as a software NCO shift inside the chain, never as a retune.

ITS LIFETIME IS EXACTLY THE CAPTURE'S
-------------------------------------
Started when a capture starts, stopped when it stops, never left resident. A
connector sitting idle would hold the dongle forever, which is precisely the
OpenWebRX behaviour OASIS goes out of its way to avoid (see the openwebrx entry
in scripts/offline-manifest.json). Because the process only exists while a
capture does, listen.py's existing arbitration — is_capturing(), dongle_busy(),
the synthetic "satellites-listen" unit — keeps working untouched.

NO CONTROL SOCKET
-----------------
rtl_connector can expose a control port for coarse retuning, and the design notes
assumed a `key=value` protocol on it. Probed against 0.6.2 on 2026-08-12, none of
the obvious message forms retuned a running connector, and the daemon logged
"invalid message" for several of them.

It does not matter, and that is the point: **Doppler is a software shift, never a
retune.** The centre frequency is fixed for a capture by construction (see
doppler.centre_hz), so the frequency is passed once with -f at start. Coarse
retuning would only ever mean "the operator picked a different satellite", and
that is a new capture with a new connector. Rather than reverse-engineer a
protocol we do not need, this module does not open the control socket at all.
"""
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time

BINARY = "rtl_connector"
DEFAULT_PORT = 4590
DEFAULT_GAIN = "40"
DEFAULT_PPM = "0"

# How long to wait for the daemon to claim the dongle and open its socket. It
# enumerates USB, loads the tuner and allocates buffers first; ~2 s on a Pi 5,
# more on a Pi 3. Failing fast here beats a chain that reads zero bytes forever.
START_TIMEOUT_S = 12.0

# Where Debian's python3-csdr lands. The OASIS venv is created by
# `python3 -m venv` with no --system-site-packages, so a dpkg-installed pycsdr is
# invisible to it — `import pycsdr` fails AFTER a completely successful apt
# install, and because backend selection degrades silently the symptom is
# "Doppler never turns on" with nothing logged anywhere.
#
# Appending the path here is deliberately narrower than recreating the venv with
# --system-site-packages, which would change dependency isolation for every
# module in the project to solve one module's problem. It is APPENDED, not
# prepended, so nothing in dist-packages can shadow a venv package.
DIST_PACKAGES = "/usr/lib/python3/dist-packages"


def enable_dist_packages(path=DIST_PACKAGES, path_list=None):
    """Make Debian's dist-packages importable. Returns True if it is now on the
    path. Idempotent, and never adds a directory that does not exist."""
    path_list = sys.path if path_list is None else path_list
    if not os.path.isdir(path):
        return False
    if path not in path_list:
        path_list.append(path)
    return True


def pycsdr_available():
    """Can we actually import pycsdr? A capability probe, not an artifact check:
    the package can be installed and still unusable when the venv was built on a
    different Python than the one the C extension was compiled for (bookworm 3.11
    vs trixie 3.13). Only the import knows."""
    enable_dist_packages()
    try:
        import pycsdr  # noqa: F401
        from pycsdr import modules  # noqa: F401
    except Exception:      # ImportError, but also a bad .so -> OSError
        return False
    return True


def missing_deps(which=shutil.which):
    """Binaries the tracked-capture path needs that aren't on PATH."""
    return [b for b in (BINARY,) if not which(b)]


def connector_argv(center_hz, samp_rate, port=DEFAULT_PORT, gain=DEFAULT_GAIN,
                   ppm=DEFAULT_PPM, device_serial=None):
    """argv for rtl_connector. Pure — the subprocess tests exercise this, not a
    dongle.

    -d takes an index OR a serial; the serial is what we always have, and an
    index on a multi-dongle Pi is whichever device happened to enumerate first
    (usually another service's). Confirmed against 0.6.2: `-d 00000042` selected
    device 1 of 2 by serial.

    No -c: the control socket is deliberately not opened (see the module
    docstring). No -r/--rtltcp either — it gives 8-bit rtl_tcp compatibility and
    loses the float IQ the chain expects."""
    argv = [BINARY]
    if device_serial:
        argv += ["-d", str(device_serial)]
    argv += ["-f", str(int(center_hz)),
             "-s", str(int(samp_rate)),
             "-g", str(gain),
             "-P", str(ppm),
             "-p", str(int(port))]
    return argv


def connector_command(*a, **kw):
    """connector_argv as a shell string — for logs and error messages only."""
    return " ".join(shlex.quote(t) for t in connector_argv(*a, **kw))


def port_is_open(port, host="127.0.0.1", timeout=0.5):
    """Does something accept a TCP connection on this port right now?"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_port(port, timeout=START_TIMEOUT_S, host="127.0.0.1",
                  sleep=time.sleep, now=time.monotonic, probe=None):
    """Block until the data socket accepts a connection, or give up.

    Monotonic clock on purpose: an offline station can step its wall clock the
    moment GPS or an RTC arrives, and a timeout measured against it could wait
    forever or not at all. Same reason doppler curves are indexed by seconds
    since capture start rather than timestamps."""
    probe = port_is_open if probe is None else probe
    deadline = now() + timeout
    while now() < deadline:
        if probe(port, host):
            return True
        sleep(0.25)
    return probe(port, host)


class Connector:
    """One rtl_connector process. Not thread-safe; listen.py's lock owns it."""

    def __init__(self, center_hz, samp_rate, port=DEFAULT_PORT, gain=DEFAULT_GAIN,
                 ppm=DEFAULT_PPM, device_serial=None):
        self.center_hz = int(center_hz)
        self.samp_rate = int(samp_rate)
        self.port = int(port)
        self.gain = gain
        self.ppm = ppm
        self.device_serial = device_serial
        self.proc = None

    @property
    def argv(self):
        return connector_argv(self.center_hz, self.samp_rate, self.port,
                              self.gain, self.ppm, self.device_serial)

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, popen=None, wait=None, which=None):
        """Launch it and return the data port once the socket is live.

        Raises RuntimeError if the binary is missing, if the process dies during
        startup, or if the socket never opens. A connector that failed to claim
        the dongle exits quickly, so the died-during-startup case is the common
        one — report it as such rather than letting the caller block for the full
        timeout on a process that is already gone.

        `which` is injectable so the lifecycle is testable on a laptop with no
        DSP stack installed — the same shape as listen.missing_deps."""
        which = shutil.which if which is None else which
        if self.is_running():
            raise RuntimeError("connector already running")
        if missing_deps(which):
            raise RuntimeError(
                f"{BINARY} is not installed — run "
                "services/satellites/install-dsp.py")
        popen = subprocess.Popen if popen is None else popen
        wait = wait_for_port if wait is None else wait
        # Own process group so stop() takes the whole thing down, exactly as
        # listen.py does for its rtl_fm pipelines.
        self.proc = popen(self.argv, stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE, preexec_fn=os.setsid)
        if not wait(self.port):
            err = self._drain_stderr()
            self.stop()
            if err:
                raise RuntimeError(f"{BINARY} did not start: {err}")
            raise RuntimeError(
                f"{BINARY} did not open port {self.port} within "
                f"{START_TIMEOUT_S:.0f}s — is the dongle free?")
        return self.port

    def _drain_stderr(self):
        """Last words of a connector that failed, trimmed to something a UI can
        show. Never blocks on a process that is still alive."""
        if self.proc is None or self.proc.poll() is None:
            return ""
        try:
            out = (self.proc.stderr.read() or b"").decode("utf-8", "replace")
        except Exception:
            return ""
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        return lines[-1] if lines else ""

    def stop(self):
        """SIGTERM the process group. Idempotent, and never raises — a capture
        must always be able to end."""
        p, self.proc = self.proc, None
        if p is None or p.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        except Exception:
            try:
                p.terminate()
            except Exception:
                pass
        try:
            p.wait(timeout=3)
        except Exception:
            pass
