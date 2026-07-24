import hashlib
import os
import tarfile
import tempfile
import unittest
from unittest import mock

from ai.runtime import install


class TestGate(unittest.TestCase):
    def _gate(self, machine, ram_gib, pyver):
        with mock.patch("ai.runtime.install.platform.system", return_value="Linux"), \
             mock.patch("ai.runtime.install.platform.machine", return_value=machine), \
             mock.patch("ai.runtime.install._total_ram_bytes",
                        return_value=int(ram_gib * 1024**3)), \
             mock.patch("ai.runtime.install.sys") as fake_sys:
            fake_sys.version_info = pyver
            return install.gate()

    def test_ok_on_pi5(self):
        ok, _ = self._gate("aarch64", 8.0, (3, 11, 0))
        self.assertTrue(ok)

    def test_refuses_wrong_arch(self):
        ok, reason = self._gate("x86_64", 16.0, (3, 12, 0))
        self.assertFalse(ok)
        self.assertIn("arch", reason.lower())

    def test_refuses_low_ram(self):
        ok, reason = self._gate("aarch64", 4.0, (3, 11, 0))
        self.assertFalse(ok)
        self.assertIn("ram", reason.lower())

    def test_refuses_old_python(self):
        ok, reason = self._gate("aarch64", 8.0, (3, 9, 0))
        self.assertFalse(ok)
        self.assertIn("3.10", reason)

    def test_refuses_non_linux(self):
        with mock.patch("ai.runtime.install.platform.system", return_value="Darwin"), \
             mock.patch("ai.runtime.install.platform.machine", return_value="arm64"), \
             mock.patch("ai.runtime.install._total_ram_bytes",
                        return_value=int(8 * 1024**3)):
            ok, reason = install.gate()
        self.assertFalse(ok)
        self.assertIn("linux", reason.lower())


class TestDownloadVerified(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.payload = b"hello-model-bytes"
        self.sha = hashlib.sha256(self.payload).hexdigest()
        self.dest = os.path.join(self.tmp, "f.bin")

    def _fake_urlopen(self, data):
        cm = mock.MagicMock()
        cm.__enter__.return_value.read.side_effect = [data, b""]
        return cm

    def test_downloads_and_verifies(self):
        with mock.patch("ai.runtime.install.urllib.request.urlopen",
                        return_value=self._fake_urlopen(self.payload)):
            ok = install.download_verified("http://x/f", self.dest, self.sha)
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(self.dest))

    def test_hash_mismatch_leaves_no_file(self):
        with mock.patch("ai.runtime.install.urllib.request.urlopen",
                        return_value=self._fake_urlopen(b"corrupted")):
            ok = install.download_verified("http://x/f", self.dest, self.sha)
        self.assertFalse(ok)
        self.assertFalse(os.path.exists(self.dest))

    def test_skips_when_present_and_valid(self):
        with open(self.dest, "wb") as fh:
            fh.write(self.payload)
        with mock.patch("ai.runtime.install.urllib.request.urlopen") as uo:
            ok = install.download_verified("http://x/f", self.dest, self.sha)
        self.assertTrue(ok)
        uo.assert_not_called()  # idempotent — no re-download


class TestExtractAndUnit(unittest.TestCase):
    def test_extract_finds_llama_server_and_makes_it_executable(self):
        tmp = tempfile.mkdtemp()
        # build a fake tarball: build/bin/llama-server + build/bin/libfoo.so
        src = os.path.join(tmp, "src", "build", "bin")
        os.makedirs(src)
        for name in ("llama-server", "libfoo.so"):
            with open(os.path.join(src, name), "wb") as fh:
                fh.write(b"\x7fELF")
        tarball = os.path.join(tmp, "a.tar.gz")
        with tarfile.open(tarball, "w:gz") as tf:
            tf.add(os.path.join(tmp, "src"), arcname="src")
        bin_dir = os.path.join(tmp, "bin")
        server = install.extract_llama(tarball, bin_dir)
        self.assertTrue(server.endswith("llama-server"))
        self.assertTrue(os.path.exists(server))
        self.assertTrue(os.access(server, os.X_OK))
        self.assertTrue(os.path.exists(os.path.join(bin_dir, "libfoo.so")))

    def test_build_unit_has_execstart_jinja_port_and_ld_path(self):
        unit = install.build_unit(binary="/opt/ai/bin/llama-server",
                                  model="/opt/ai/models/m.gguf",
                                  bin_dir="/opt/ai/bin", user="mihai",
                                  port=8087, ctx=4096, threads=4)
        self.assertIn("ExecStart=/opt/ai/bin/llama-server", unit)
        self.assertIn("--jinja", unit)
        self.assertIn("--port 8087", unit)
        self.assertIn("-m /opt/ai/models/m.gguf", unit)
        self.assertIn("LD_LIBRARY_PATH=/opt/ai/bin", unit)
        self.assertIn("User=mihai", unit)
        self.assertIn("WantedBy=multi-user.target", unit)


class TestInstallWheels(unittest.TestCase):
    def test_reads_specs_from_manifest_and_returns_import_ok(self):
        with mock.patch("ai.runtime.install._manifest_entry",
                        return_value={"packages": [{"name": "mcp", "version": ">=1.2,<2"},
                                                    {"name": "httpx", "version": ">=0.27,<1"}]}), \
             mock.patch("ai.runtime.install.os.path.exists", return_value=True), \
             mock.patch("ai.runtime.install.S._venv_bin", return_value="/venv/bin/pip"), \
             mock.patch("ai.runtime.install.S.decide_source", return_value=(False, "offline")), \
             mock.patch("ai.runtime.install.S.print_source_banner"), \
             mock.patch("ai.runtime.install.S.install_one") as inst, \
             mock.patch("ai.runtime.install.S._import_ok", return_value=True) as impok:
            ok = install._install_wheels()
        self.assertTrue(ok)
        specs = [c.args[1] for c in inst.call_args_list]  # spec is 2nd positional
        self.assertIn("mcp>=1.2,<2", specs)
        self.assertIn("httpx>=0.27,<1", specs)
        impok.assert_called_once()

    def test_no_venv_returns_false_without_installing(self):
        with mock.patch("ai.runtime.install.os.path.exists", return_value=False), \
             mock.patch("ai.runtime.install.S.install_one") as inst:
            ok = install._install_wheels()
        self.assertFalse(ok)
        inst.assert_not_called()

    def test_no_source_returns_false(self):
        with mock.patch("ai.runtime.install._manifest_entry",
                        return_value={"packages": [{"name": "mcp", "version": ">=1.2,<2"}]}), \
             mock.patch("ai.runtime.install.os.path.exists", return_value=True), \
             mock.patch("ai.runtime.install.S._venv_bin", return_value="/venv/bin/pip"), \
             mock.patch("ai.runtime.install.S.decide_source", return_value=(None, "no source")), \
             mock.patch("ai.runtime.install.S.install_one") as inst:
            ok = install._install_wheels()
        self.assertFalse(ok)
        inst.assert_not_called()
