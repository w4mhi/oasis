import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import diagnostics as D

def _fake(id, group, status, capability, critical, badge="X", tier="v1"):
    chk = D.Check(id=id, group=group, label=id, capability=capability,
                  critical=critical, tier=tier,
                  fn=lambda ctx, s=status, b=badge, g=group, i=id:
                      D._result(i, g, i, s, b, "d",
                                breaks=("broken" if s == "fail" else None),
                                fix=("/system/setup.html" if s == "fail" else None)))
    return chk

class TestRollup(unittest.TestCase):
    def _run(self, checks):
        orig = D.REGISTRY
        D.REGISTRY = checks
        try:
            return D.run_all("127.0.0.1", 8083)
        finally:
            D.REGISTRY = orig

    def test_summary_counts(self):
        r = self._run([_fake("a","CORE","ok","ACCESS",True),
                       _fake("b","CORE","warn","ACCESS",False),
                       _fake("c","SYSTEM","fail","POWER",True)])
        self.assertEqual(r["summary"], {"fail":1,"warn":1,"ok":1})

    def test_capability_fail_needs_critical(self):
        # non-critical fail => capability warn, not fail
        r = self._run([_fake("a","HARDWARE","fail","APRS_RX",False),
                       _fake("b","SERVICES","ok","APRS_RX",True)])
        cap = next(c for c in r["capabilities"] if c["id"]=="APRS_RX")
        self.assertEqual(cap["status"], "warn")

    def test_capability_fail_on_critical(self):
        r = self._run([_fake("a","SERVICES","fail","APRS_RX",True)])
        cap = next(c for c in r["capabilities"] if c["id"]=="APRS_RX")
        self.assertEqual(cap["status"], "fail")

    def test_reference_never_blocks_but_can_warn(self):
        r = self._run([_fake("d","DATA","fail","REFERENCE",False)])
        cap = next(c for c in r["capabilities"] if c["id"]=="REFERENCE")
        self.assertIn(cap["status"], ("warn","fail"))  # its own tile may show it

    def test_fix_now_prefers_critical_then_group_order(self):
        r = self._run([_fake("late","DATA","fail","REFERENCE",False),
                       _fake("crit","CORE","fail","ACCESS",True)])
        self.assertEqual(r["fix_now"]["id"], "crit")

    def test_fix_now_none_when_all_pass(self):
        r = self._run([_fake("a","CORE","ok","ACCESS",True)])
        self.assertIsNone(r["fix_now"])

    def test_backlog_excluded_by_default(self):
        r = self._run([_fake("a","CORE","ok","ACCESS",True),
                       _fake("b","DATA","fail","REFERENCE",False,tier="backlog")])
        ids = [c["id"] for g in r["groups"] for c in g["checks"]]
        self.assertNotIn("b", ids)

if __name__ == "__main__":
    unittest.main()
