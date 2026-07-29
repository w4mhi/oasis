import contextlib, os, sys, unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, os.path.dirname(_HERE))
import app as oasis_app          # noqa: E402
from routes import system as sysmod   # noqa: E402


class FakeProc:
    """Minimal psutil.Process stand-in: cpu_percent already 'primed'."""
    def __init__(self, name, cpu, mem): self._n, self._c, self._m = name, cpu, mem
    def cpu_percent(self, interval=None): return self._c
    def name(self): return self._n
    def memory_percent(self): return self._m
    def oneshot(self): return contextlib.nullcontext()


class TestTopProcs(unittest.TestCase):
    def test_sorts_desc_limits_and_drops_idle(self):
        procmap = {1: FakeProc("alpha", 5.0, 1.0), 2: FakeProc("beta", 50.0, 2.0),
                   3: FakeProc("idle", 0.0, 0.0),  4: FakeProc("gamma", 20.0, 3.0)}
        top = sysmod._read_top_procs(procmap, limit=3)
        self.assertEqual([p["name"] for p in top], ["beta", "gamma", "alpha"])
        self.assertTrue(all(p["cpu"] > 0 for p in top))
        self.assertEqual(top[0], {"name": "beta", "cpu": 50.0, "mem": 2.0})

    def test_truncates_long_names(self):
        top = sysmod._read_top_procs({1: FakeProc("x" * 40, 9.0, 0.0)})
        self.assertLessEqual(len(top[0]["name"]), 20)


class TestApiSystemShape(unittest.TestCase):
    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()

    def test_api_system_has_cores_and_procs(self):
        d = self.c.get("/api/system").get_json()
        self.assertIn("cpu_cores", d); self.assertIsInstance(d["cpu_cores"], list)
        self.assertIn("top_procs", d); self.assertIsInstance(d["top_procs"], list)


if __name__ == "__main__":
    unittest.main()
