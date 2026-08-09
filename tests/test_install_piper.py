"""Regression tests for the speech-dispatcher config install-piper.py generates.

None of this can be caught by running the installer in CI — it needs a live
speech-dispatcher — so these pin the specific mistakes that produced a module
which registered but never spoke, with speech-dispatcher silently falling back
to espeak and nothing reporting an error anywhere.
"""

import importlib.util
import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, _REPO)

_PATH = os.path.join(_REPO, "services", "satellites", "install-piper.py")


def _load():
    """Import the hyphenated installer as a module."""
    spec = importlib.util.spec_from_file_location("install_piper", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class PiperModuleConfTest(unittest.TestCase):
    def setUp(self):
        self.ip = _load()
        self.conf = self.ip._module_conf(
            "/opt/oasis-piper/piper/piper",
            "/opt/oasis-piper/en_GB-jenny_dioco-medium.onnx",
            22050)

    def test_cmd_dependency_is_a_bare_name_not_a_path(self):
        """GenericCmdDependency is a PATH lookup. An absolute path never
        resolves, so the module refuses to start and speech-dispatcher falls
        back to espeak — which sounds like a broken voice but is no module."""
        deps = re.findall(r'^GenericCmdDependency\s+"([^"]+)"', self.conf, re.M)
        self.assertTrue(deps, "expected at least one GenericCmdDependency")
        for dep in deps:
            with self.subTest(dependency=dep):
                self.assertFalse(dep.startswith("/"),
                                 f"GenericCmdDependency {dep!r} must be a bare command name")

    def test_execute_synth_command_is_one_line(self):
        """The continuation belongs after the KEY; splitting the quoted string
        across lines is not the documented form and stopped the module loading."""
        m = re.search(r'^GenericExecuteSynth\s*\\\n"(.*)"$', self.conf, re.M)
        self.assertIsNotNone(m, "GenericExecuteSynth must be key, backslash, then one quoted line")
        self.assertNotIn("\\\n", m.group(1), "the command itself must not be split across lines")

    def test_only_documented_keys(self):
        """One unknown key sinks the whole config. DefaultVolume was the one
        that did it; keep the key set to what the stock generic modules use."""
        keys = set(re.findall(r"^([A-Z][A-Za-z]+)\s", self.conf, re.M))
        allowed = {"Debug", "GenericExecuteSynth", "GenericCmdDependency",
                   "AddVoice", "DefaultVoice", "GenericPunctNone",
                   "GenericPunctSome", "GenericPunctMost", "GenericPunctAll"}
        self.assertEqual(keys - allowed, set(), f"undocumented key(s): {sorted(keys - allowed)}")

    def test_data_is_single_quoted(self):
        """$DATA is untrusted text interpolated into a shell command. Double
        quotes would let a `$` or backtick in a satellite name reach the shell."""
        self.assertIn(r"\'$DATA\'", self.conf)
        self.assertNotIn(r'\"$DATA\"', self.conf)

    def test_voice_name_matches_what_the_page_prefers(self):
        """common/js/sat-alerts.js prefers a voice whose name contains piper or
        jenny. Renaming here drops the box back to espeak with nothing red."""
        voice = re.search(r'^AddVoice\s+"en"\s+"\w+"\s+"([^"]+)"', self.conf, re.M)
        self.assertIsNotNone(voice)
        name = voice.group(1).lower()
        self.assertTrue("piper" in name or "jenny" in name,
                        f"voice name {name!r} would not be picked by the ladder")

    def test_sample_rate_comes_from_the_voice(self):
        """A wrong rate plays the alert at the wrong pitch and speed, which reads
        as a bad voice rather than a config error."""
        self.assertIn("-r 22050", self.conf)
        other = self.ip._module_conf("/x/piper", "/x/v.onnx", 16000)
        self.assertIn("-r 16000", other)


class SpeechdBlockTest(unittest.TestCase):
    def setUp(self):
        self.ip = _load()
        self.block = self.ip._speechd_block()

    def test_declares_espeak_too(self):
        """The stock speechd.conf has every AddModule commented out, so
        speech-dispatcher auto-loads. Adding ONE explicit AddModule switches it
        to explicit mode and deregisters everything else — which silently
        removed espeak from a working box. Declaring espeak-ng here is what
        keeps the fallback voice alive."""
        self.assertIn('AddModule "espeak-ng"', self.block)
        self.assertIn('AddModule "oasis-piper"', self.block)

    def test_espeak_stays_default(self):
        """Piper is chosen by voice name from the page's ladder, never by being
        the default module — so a station whose Piper module fails to start can
        still speak."""
        self.assertIn("DefaultModule espeak-ng", self.block)

    def test_block_is_delimited_for_clean_removal(self):
        """--uninstall must remove exactly the lines it wrote: speechd.conf is a
        package conffile and everything outside the markers belongs to Debian."""
        self.assertTrue(self.block.startswith(self.ip.BEGIN))
        self.assertTrue(self.block.rstrip("\n").endswith(self.ip.END))

    def test_strip_block_is_the_exact_inverse(self):
        original = "SomeKey 1\n# a comment\nOtherKey 2\n"
        written = original + "\n" + self.block
        self.assertEqual(self.ip._strip_block(written).strip(), original.strip())
