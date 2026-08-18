"""No installer may build an apt argv by hand — it must go through sudo_apt_cmd.

common/oasis_lib.py's sudo_apt_cmd() carries three things no hand-written argv
has, and each one was added after a real failure on a real station:

  DEBIAN_FRONTEND on SUDO's command line   sudoers env_reset drops it from the
                                           caller's environment, so a debconf
                                           question with no tty aborts the run
  --force-confdef / --force-confold        a dpkg conffile prompt with no tty
                                           leaves the package half-configured,
                                           which poisons EVERY later apt/dpkg
                                           operation on the box
  DPkg::Lock::Timeout=300                  an nwr install landed two seconds
                                           inside an apt-daily window, apt gave
                                           up instantly on the held lock, and
                                           the feature was recorded
                                           install_failed for good

Nine call sites across six files had none of it, and nothing caught them: the
argv is only ever executed on a Pi, mid-install, once. The tenth one would go
the same way, so the guard is on the bug CLASS — an apt/dpkg argv assembled as
a list literal instead of by the builder.

What this test CANNOT see, and what still needs a human reader:

  * an argv assembled from variables — `cmd = ["sudo"]; cmd += ["apt", ...]`,
    a program name held in a constant, an f-string. Only literal lists whose
    leading words are literal strings are readable here.
  * apt inside a string: a shell=True command line assembled anywhere but the
    call itself, and — the live example — apt commands inside a GENERATED
    script or systemd unit, e.g. scripts/enable-ap-fallback.py's NETWATCH_SRC.
    Those are string constants to the parser.
  * `sudo -u pi apt install ...` and friends: after a sudo flag that takes an
    argument of its own, the program word is no longer the first bare token,
    and the scan gives up rather than guess.
  * a subcommand or spelling it does not know. It matches an explicit list of
    WRITING subcommands against a bare `apt` / `apt-get` / `aptitude`, so a
    novel subcommand, or apt called by path (/usr/bin/apt-get), reads as
    harmless. That is the price of not flagging every ("apt", version) tuple
    in the codebase as an argv.
  * anything that is not Python — shell installers, docs, systemd units.
  * whether the argv is CORRECT. This only checks that it was routed through
    the builder; tests/test_sdr_dsp_registry.py checks what the builder emits
    and that its first argument names the program.
"""
import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Programs whose *installing* subcommands must be routed through sudo_apt_cmd.
APT_PROGRAMS = {"apt", "apt-get", "aptitude"}

# The apt/apt-get subcommands that take the dpkg lock or run maintainer
# scripts. Named positively rather than exempting the read-only ones (`list`,
# `show`, `policy`, ...) because a two-element tuple of program NAMES —
# ("apt", apt_candidate) in common/manifest.py, ("apt", "apt-get") in the
# builder's own dispatch — is data, not an argv, and reads as one otherwise.
APT_WRITE_SUBCOMMANDS = {"install", "reinstall", "remove", "purge", "update",
                         "upgrade", "dist-upgrade", "full-upgrade",
                         "autoremove", "build-dep", "clean", "autoclean"}

# dpkg carries the same debconf hazard, but only when it actually configures a
# package. `dpkg -l`, `dpkg-query` and `dpkg --compare-versions` are reads.
DPKG_WRITE_FLAGS = {"-i", "--install", "--unpack", "--configure", "-r",
                    "--remove", "-P", "--purge"}

# Trees that are not ours to lint, plus tests/: a fixture there DESCRIBES an
# argv (asserts on an expected list) rather than running one, so a literal
# there is data, not a call.
SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", "offline-packages",
             "oasis-offline", "mock-up", "specs", "tests", "vendor"}

# Call targets that would execute a string as a shell command line.
RUNNERS = {"run", "call", "check_call", "check_output", "Popen", "_run"}


def _leading_words(elts):
    """The literal string words at the head of a list/tuple literal.

    Stops at the first element that is not a plain string constant — a Name, a
    *splat, an f-string — because after that the argv is no longer readable.
    """
    words = []
    for e in elts:
        if isinstance(e, ast.Constant) and isinstance(e.value, str):
            words.append(e.value)
        else:
            break
    return words


def _program_and_rest(words):
    """Split ['sudo', 'DEBIAN_FRONTEND=x', 'apt', 'install'] into ('apt', [...]).

    Skips the sudo prefix, any VAR=value token sudo passes through, and any
    flag. Returns (None, []) when the program word cannot be identified.
    """
    for i, w in enumerate(words):
        if w == "sudo" or w.startswith("-") or "=" in w:
            continue
        return w, words[i + 1:]
    return None, []


def _offending_argv(tree):
    """Yield (lineno, argv-words) for every hand-built apt/dpkg argv literal."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        words = _leading_words(node.elts)
        prog, rest = _program_and_rest(words)
        if prog in APT_PROGRAMS:
            sub = next((w for w in rest if not w.startswith("-")), None)
            if sub in APT_WRITE_SUBCOMMANDS:
                yield node.lineno, words
        elif prog == "dpkg" and any(w in DPKG_WRITE_FLAGS for w in rest):
            yield node.lineno, words


def _offending_shell_string(tree):
    """Yield (lineno, command) for `run("sudo apt ...", shell=True)`-style calls.

    Only strings passed to a runner count. The installers print their apt
    command lines for the operator (`_info(f"Running: sudo apt install -y ...")`)
    and those are documentation, not execution.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        if name not in RUNNERS:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        prog, rest = _program_and_rest(first.value.split())
        if prog in APT_PROGRAMS and any(w in APT_WRITE_SUBCOMMANDS for w in rest):
            yield node.lineno, first.value


def _python_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


class HandBuiltAptArgvTest(unittest.TestCase):
    def test_no_installer_builds_its_own_apt_argv(self):
        offenders, scanned = [], 0
        for path in _python_files():
            try:
                with open(path, encoding="utf-8") as fh:
                    tree = ast.parse(fh.read(), filename=path)
            except (OSError, SyntaxError):
                continue
            scanned += 1
            rel = os.path.relpath(path, ROOT)
            for lineno, words in _offending_argv(tree):
                offenders.append(f"{rel}:{lineno}: {words}")
            for lineno, cmd in _offending_shell_string(tree):
                offenders.append(f"{rel}:{lineno}: {cmd!r}")

        # A scan that silently walked nothing would pass forever.
        self.assertGreater(scanned, 100, f"only {scanned} files parsed — scan broken?")
        self.assertEqual(
            [], offenders,
            "apt/dpkg argv built by hand — route it through "
            "common.oasis_lib.sudo_apt_cmd(), which supplies DEBIAN_FRONTEND on "
            "sudo's own command line, the non-interactive conffile policy, and "
            "the dpkg-lock wait:\n  " + "\n  ".join(offenders))


class ScanBehaviourTest(unittest.TestCase):
    """The scan's own coverage, on sources it is handed rather than the repo.

    Without these, the guard above passes just as happily when the detector
    stops detecting.
    """

    def _argv_hits(self, src):
        return [w for _, w in _offending_argv(ast.parse(src))]

    def test_catches_the_shape_that_was_swept(self):
        self.assertEqual(
            [["sudo", "apt", "install", "-y", "direwolf"]],
            self._argv_hits('_run(["sudo", "apt", "install", "-y", "direwolf"])'))

    def test_catches_apt_get_and_a_deb_path_and_no_sudo(self):
        self.assertEqual(1, len(self._argv_hits(
            'subprocess.run(["sudo", "apt-get", "install", "-y", deb])')))
        # An installer already running as root skips sudo — same hazard.
        self.assertEqual(1, len(self._argv_hits('run(["apt-get", "install", "-y", p])')))

    def test_catches_a_configuring_dpkg(self):
        self.assertEqual(1, len(self._argv_hits('_run(["sudo", "dpkg", "-i", tmp])')))

    def test_leaves_reads_alone(self):
        for src in ('subprocess.run(["apt-cache", "policy", pkg])',
                    'subprocess.run(["dpkg-query", "-W", "-f=${Status}", pkg])',
                    'subprocess.run(["dpkg", "--compare-versions", a, op, b])',
                    'subprocess.run(["sudo", "apt", "list", "--installed"])',
                    'subprocess.run(["sudo", "systemctl", "restart", unit])'):
            self.assertEqual([], self._argv_hits(src), src)

    def test_a_tuple_of_program_names_is_data_not_an_argv(self):
        # common/manifest.py ranks install sources as ("apt", candidate).
        for src in ('sources.append(("apt", apt_candidate))',
                    'if args[0] in ("apt", "apt-get"):\n    pass\n'):
            self.assertEqual([], self._argv_hits(src), src)

    def test_leaves_the_builder_and_its_callers_alone(self):
        src = ('def sudo_apt_cmd(*args):\n'
               '    conf = ["-o", "Dpkg::Options::=--force-confold"]\n'
               '    return ["sudo", "DEBIAN_FRONTEND=noninteractive", args[0], *conf, *args[1:]]\n'
               '_run(sudo_apt_cmd("apt", "install", "-y", "direwolf"))\n')
        self.assertEqual([], self._argv_hits(src))

    def test_a_printed_command_line_is_documentation_not_a_call(self):
        src = ('_info(f"Running: sudo apt install -y {deb_path}")\n'
               '_warn("Fix with: sudo apt install fake-hwclock")\n')
        self.assertEqual([], list(_offending_shell_string(ast.parse(src))))

    def test_catches_a_shell_string(self):
        src = 'subprocess.run("sudo apt-get install -y direwolf", shell=True)'
        self.assertEqual(1, len(list(_offending_shell_string(ast.parse(src)))))


if __name__ == "__main__":
    unittest.main()
