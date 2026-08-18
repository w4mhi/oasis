"""The sdr-dsp feature across every surface that has to know about it.

A feature in OASIS is registered in FOUR places, and missing one fails in a
different way each time:

  common/setup_registry.py   miss it -> the browser cannot resolve or install it
  setup-oasis.py             miss it -> the CLI menu never offers it
  server/system/setup.html   miss it -> the checkbox is absent, with NO error
  scripts/offline-manifest.json  miss it -> bundling and version gates skip it

The setup.html one is the nastiest: the page renders, the operator ticks nothing
because there is nothing to tick, and no log line anywhere says why. Hence a test
that reads the actual HTML rather than trusting that someone remembered.
"""
import ast
import importlib.util
import json
import os
import unittest

from common import oasis_lib as OL
from common import setup_engine as SE
from common import setup_registry as SR

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = "sdr-dsp"


def _setup_oasis():
    spec = importlib.util.spec_from_file_location(
        "_setup_oasis_for_test", os.path.join(ROOT, "setup-oasis.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SurfacesTest(unittest.TestCase):
    def test_orchestrator_registry(self):
        reg = SR.build_registry("/tmp/oasis-test-root")
        self.assertIn(KEY, reg)
        self.assertIn(KEY, SR.PRIVILEGED_FEATURES)   # apt + sudo

    def test_cli_menu(self):
        feats = {f.key: f for f in _setup_oasis().FEATURES}
        self.assertIn(KEY, feats)
        f = feats[KEY]
        self.assertTrue(f.internet, "third-party apt repo — must warn when offline")
        self.assertFalse(f.default, "optional: capture works uncorrected without it")
        self.assertTrue(os.path.exists(os.path.join(ROOT, f.script)), f.script)

    def test_setup_html_checkbox(self):
        with open(os.path.join(ROOT, "server", "system", "setup.html"),
                  encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn(f'data-feature="{KEY}"', html,
                      "no checkbox -> uninstallable from the browser, with no error")

    def test_manifest_entry(self):
        with open(os.path.join(ROOT, "scripts", "offline-manifest.json"),
                  encoding="utf-8") as fh:
            man = json.load(fh)
        self.assertIn(KEY, man["features"])
        entry = man["features"][KEY]
        self.assertEqual(entry["type"], "apt")
        self.assertTrue(entry.get("online_only"),
                        "the bundler does no dep resolution; these pull libfftw3")
        self.assertIn("third_party_repo", entry)

    def test_the_manifest_does_not_install_openwebrx_itself(self):
        """The entire reason this is a separate feature. openwebrx seizes the
        RTL-SDR exclusively — which is why OASIS installs it disabled — while the
        DSP library alone has no such behaviour."""
        with open(os.path.join(ROOT, "scripts", "offline-manifest.json"),
                  encoding="utf-8") as fh:
            pkgs = json.load(fh)["features"][KEY]["packages"]["common"]
        self.assertNotIn("openwebrx", pkgs)
        self.assertEqual(sorted(pkgs),
                         ["csdr", "libcsdr0", "python3-csdr", "rtl-connector"])


class DependencyTest(unittest.TestCase):
    def setUp(self):
        self.reg = SR.build_registry("/tmp/oasis-test-root")

    def test_it_needs_the_rtl_sdr_tools(self):
        self.assertIn("rtl-sdr", self.reg[KEY].dependencies)

    def test_selecting_it_pulls_the_driver_first(self):
        order = SE.resolve_plan([KEY], self.reg).ordered_features
        self.assertLess(order.index("rtl-sdr"), order.index(KEY))

    def test_nothing_depends_on_it(self):
        """Everything above this degrades rather than breaks: a station without
        the DSP stack still records passes through rtl_fm, just uncorrected. If
        some feature ever declares a hard dependency on it, that promise is gone
        and the offline-first story goes with it."""
        for key, spec in self.reg.items():
            self.assertNotIn(KEY, spec.dependencies,
                             f"{key} must degrade without the DSP stack, not require it")


class SudoAptCmdCallers(unittest.TestCase):
    """sudo_apt_cmd's FIRST argument is the program to run.

    install-dsp.py called it as sudo_apt_cmd("update") and
    sudo_apt_cmd("install", "-y", ...), which built `sudo update` (not a
    command) and `sudo install -y csdr ...` — the latter reaching coreutils
    /usr/bin/install, which rejects -y. The installer therefore could not ever
    install a package, while step 1 still added the apt repository: a box left
    with the source configured and nothing installed, which reads as "apt is
    broken" rather than "the caller is wrong".

    Every gate on the branch passed: the registry tests only check that the
    feature is WIRED, and nothing executed the argv. So the guard is on the bug
    CLASS across the whole repo, not on the one line that had it.
    """

    PROGRAMS = {"apt", "apt-get", "apt-cache", "dpkg", "dpkg-query", "aptitude"}

    def _call_sites(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if not d.startswith(".") and d not in
                           {"__pycache__", "node_modules", "offline-packages", "oasis-offline"}]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(dirpath, fn)
                try:
                    with open(path, encoding="utf-8") as fh:
                        tree = ast.parse(fh.read(), filename=path)
                except (OSError, SyntaxError):
                    continue
                for node in ast.walk(tree):
                    if (isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Name)
                            and node.func.id == "sudo_apt_cmd"):
                        yield os.path.relpath(path, root), node

    def test_every_caller_names_the_program_first(self):
        checked = 0
        for rel, node in self._call_sites():
            if not node.args:
                self.fail(f"{rel}: sudo_apt_cmd() called with no program")
            first = node.args[0]
            # Only literal first args can be checked; a variable is opaque here
            # and is left to its own caller.
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                continue
            checked += 1
            self.assertIn(
                first.value, self.PROGRAMS,
                f"{rel}:{node.lineno}: sudo_apt_cmd({first.value!r}, ...) — the first "
                f"argument must be the PROGRAM (one of {sorted(self.PROGRAMS)}), not a "
                f"subcommand. As written this shells out to `sudo {first.value}`.")
        # A scan that silently matched nothing would pass forever.
        self.assertGreater(checked, 5,
                           f"only {checked} sudo_apt_cmd call sites found — did the scan break?")


class SudoAptCmdLockTimeoutTest(unittest.TestCase):
    """apt must WAIT for the dpkg lock, not fail on it.

    apt-daily.timer runs on every Debian / Pi OS box. An nwr install landed two
    seconds inside one of its windows, apt aborted on the held lock, and the
    feature was recorded install_failed permanently. Thirteen files call
    sudo_apt_cmd, so the guard belongs on the builder, not on one caller.
    """

    OPT = "DPkg::Lock::Timeout=300"

    def test_apt_get_waits_for_the_lock(self):
        argv = OL.sudo_apt_cmd("apt-get", "install", "-y", "multimon-ng")
        self.assertIn(self.OPT, argv)
        self.assertEqual(argv[argv.index(self.OPT) - 1], "-o")

    def test_apt_waits_for_the_lock(self):
        self.assertIn(self.OPT, OL.sudo_apt_cmd("apt", "update"))

    def test_dpkg_gets_no_lock_timeout(self):
        # `dpkg -i` has no such option and would reject it.
        argv = OL.sudo_apt_cmd("dpkg", "-i", "/tmp/x.deb")
        self.assertNotIn(self.OPT, argv)
        self.assertNotIn("-o", argv)

    def test_the_options_come_before_the_subcommand(self):
        # apt only accepts -o before the subcommand's own arguments.
        argv = OL.sudo_apt_cmd("apt-get", "install", "-y", "csdr")
        self.assertLess(argv.index(self.OPT), argv.index("install"))

    def test_the_conffile_policy_survives(self):
        argv = OL.sudo_apt_cmd("apt-get", "install", "-y", "csdr")
        self.assertIn("Dpkg::Options::=--force-confold", argv)
        self.assertIn("Dpkg::Options::=--force-confdef", argv)
        self.assertEqual(argv[:2], ["sudo", "DEBIAN_FRONTEND=noninteractive"])


if __name__ == "__main__":
    unittest.main()
