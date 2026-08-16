"""Recording what got installed must not depend on unrelated features.

installed-services.json is the single source of truth for removal. A feature
that installed but was never recorded cannot be uninstalled from the UI, and
nothing anywhere says so — the failure is silent and the operator only finds it
when they try to remove the feature months later.

Real bug this pins (2026-08-16): the call to _setup_record_installed_features
sat inside `if not _blocked:`, so ticking a feature that installed perfectly
alongside ANY blocked feature recorded nothing at all. Clicking the same box
again later appeared to "fix" it, because by then the blocker had cleared.
"""
import ast
import os
import sys
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "server"))

_SETUP_PY = os.path.join(_REPO, "server", "routes", "setup.py")


def _guards_over(node, tree):
    """Names tested by every `if` statement enclosing *node*."""
    guards = []

    def walk(n, stack):
        if n is node:
            guards.extend(stack)
            return True
        for child in ast.iter_child_nodes(n):
            if isinstance(n, ast.If) and child in n.body:
                if walk(child, stack + [ast.dump(n.test)]):
                    return True
            elif walk(child, stack):
                return True
        return False

    walk(tree, [])
    return guards


class RecordingIsNotGatedOnBlockedTest(unittest.TestCase):
    def setUp(self):
        with open(_SETUP_PY, encoding="utf-8") as fh:
            self.tree = ast.parse(fh.read())

    def _record_calls(self):
        out = []
        for n in ast.walk(self.tree):
            if (isinstance(n, ast.Call)
                    and getattr(n.func, "id", None)
                    == "_setup_record_installed_features"):
                out.append(n)
        return out

    def test_the_call_exists(self):
        self.assertTrue(self._record_calls(),
                        "nothing records installed features any more")

    def test_no_call_sits_under_a_blocked_guard(self):
        for call in self._record_calls():
            for guard in _guards_over(call, self.tree):
                self.assertNotIn(
                    "_blocked", guard,
                    "recording installed features must not depend on whether "
                    "some OTHER feature in the plan was blocked — an "
                    "unrecorded feature cannot be uninstalled from the UI")

    def test_the_failure_path_is_not_silent(self):
        """`except Exception: pass` here is how the original bug hid."""
        with open(_SETUP_PY, encoding="utf-8") as fh:
            lines = fh.readlines()
        calls = self._record_calls()
        self.assertTrue(calls)
        # Locate by AST line number: a substring search would match the
        # function DEFINITION, whose name contains the same text.
        for call in calls:
            window = "".join(lines[call.lineno - 1:call.lineno + 12])
            self.assertIn(
                "_setup_emit_log_line", window,
                "a swallowed recording failure is indistinguishable from "
                "'nothing to record'; log it")


class OnlySuccessfulFeaturesAreRecordedTest(unittest.TestCase):
    """The per-feature filter is what makes dropping the guard safe."""

    def setUp(self):
        from routes import setup as S
        from common import setup_engine as SE
        self.S, self.SE = S, SE

    def _summary(self, pairs):
        class _Summary:
            features = [{"feature": k, "status": v} for k, v in pairs]
        return _Summary()

    def test_success_statuses_are_recorded_failures_are_not(self):
        S, SE = self.S, self.SE
        captured = {}

        def _fake_add(root, keys, records):
            captured["keys"] = set(keys)

        orig = S.installed_services.add_installed
        S.installed_services.add_installed = _fake_add
        try:
            S._setup_record_installed_features(self._summary([
                ("draws-audio", SE.STATUS_INSTALLED_NEEDS_REBOOT),
                ("graywolf", SE.STATUS_INSTALLED),
                ("kiwix", SE.STATUS_INSTALL_FAILED),
                ("adsb", SE.STATUS_BLOCKED_PREFLIGHT),
                ("winlink", SE.STATUS_SKIPPED_DEPENDENCY),
            ]))
        finally:
            S.installed_services.add_installed = orig

        self.assertEqual(captured["keys"], {"draws-audio", "graywolf"})

    def test_a_reboot_pending_install_still_counts(self):
        # draws-audio exits 10 ("config written, reboot required") on a first
        # install. That is a success, not a failure, and must be recorded.
        self.assertIn(self.SE.STATUS_INSTALLED_NEEDS_REBOOT,
                      self.S._SETUP_SUCCESS_STATUSES)


if __name__ == "__main__":
    unittest.main()
