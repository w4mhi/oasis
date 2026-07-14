import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from common import setup_engine as SE


class SetupEnginePlanTest(unittest.TestCase):
    def test_resolve_plan_adds_dependencies_in_stable_order(self):
        reg = {
            "server": SE.FeatureSpec(key="server"),
            "service-controls": SE.FeatureSpec(key="service-controls", dependencies=["server"]),
        }
        plan = SE.resolve_plan(["service-controls"], reg)
        self.assertEqual(plan.ordered_features, ["server", "service-controls"])
        self.assertEqual(plan.blocked, [])

    def test_resolve_plan_unknown_feature_returns_blocker(self):
        reg = {"server": SE.FeatureSpec(key="server")}
        plan = SE.resolve_plan(["nope"], reg)
        self.assertEqual(plan.ordered_features, [])
        self.assertEqual(plan.blocked[0]["reason_code"], "UNKNOWN_FEATURE")


class SetupEngineRunTest(unittest.TestCase):
    def test_runner_marks_dependency_failed_children_skipped(self):
        def fail_install():
            return {"ok": False, "reason_code": "X", "reason_text": "broken"}

        reg = {
            "server": SE.FeatureSpec(key="server", install_fn=fail_install),
            "service-controls": SE.FeatureSpec(key="service-controls", dependencies=["server"], install_fn=lambda: {"ok": True}),
        }
        plan = SE.resolve_plan(["service-controls"], reg)
        states, _blocked, _summary = SE.run_plan(plan, SE.RunOptions(job_id="job1"), reg)
        self.assertEqual(states["server"].status, SE.STATUS_INSTALL_FAILED)
        self.assertEqual(states["service-controls"].status, SE.STATUS_SKIPPED_DEPENDENCY)
        self.assertEqual(states["service-controls"].reason_text, "dependency_failed:server")

    def test_runner_executes_features_strictly_sequential(self):
        trace = []

        def mk(name):
            def _step():
                trace.append(name)
                return {"ok": True}
            return _step

        reg = {
            "server": SE.FeatureSpec(
                key="server",
                install_fn=mk("server.install"),
                verify_fn=mk("server.verify"),
                enable_fn=mk("server.enable"),
                enable_policy="if_installed",
            ),
            "service-controls": SE.FeatureSpec(
                key="service-controls",
                dependencies=["server"],
                install_fn=mk("svc.install"),
                verify_fn=mk("svc.verify"),
                enable_fn=mk("svc.enable"),
                enable_policy="if_installed",
            ),
        }
        plan = SE.resolve_plan(["service-controls"], reg)
        SE.run_plan(plan, SE.RunOptions(job_id="job2"), reg)
        self.assertEqual(
            trace,
            [
                "server.install",
                "server.verify",
                "server.enable",
                "svc.install",
                "svc.verify",
                "svc.enable",
            ],
        )

    def test_runner_maps_success_to_installed_enabled_not_started(self):
        reg = {
            "server": SE.FeatureSpec(
                key="server",
                install_fn=lambda: {"ok": True},
                verify_fn=lambda: {"ok": True},
                enable_fn=lambda: {"ok": True},
                enable_policy="if_installed",
            )
        }
        plan = SE.resolve_plan(["server"], reg)
        states, _blocked, _summary = SE.run_plan(plan, SE.RunOptions(job_id="job3"), reg)
        self.assertEqual(states["server"].status, SE.STATUS_INSTALLED_ENABLED_NOT_STARTED)

    def test_terminal_exit_code_nonzero_on_red_status(self):
        summary = SE.JobSummary(green=0, amber=0, red=1, gray=0, features=[])
        self.assertEqual(SE.terminal_exit_code(summary), 1)

    def test_runner_stops_later_features_once_cancel_requested(self):
        trace = []

        def mk(name):
            def _step():
                trace.append(name)
                return {"ok": True}
            return _step

        reg = {
            "server": SE.FeatureSpec(key="server", install_fn=mk("server.install"), verify_fn=mk("server.verify")),
            "service-controls": SE.FeatureSpec(key="service-controls", install_fn=mk("service-controls.install"), verify_fn=mk("service-controls.verify")),
            "kiwix": SE.FeatureSpec(key="kiwix", install_fn=mk("kiwix.install"), verify_fn=mk("kiwix.verify")),
        }
        plan = SE.resolve_plan(["server", "service-controls", "kiwix"], reg)

        calls = {"n": 0}

        def cancel_requested():
            calls["n"] += 1
            return calls["n"] > 1

        states, _blocked, _summary = SE.run_plan(plan, SE.RunOptions(job_id="job-cancel", cancel_requested=cancel_requested), reg)

        self.assertEqual(trace, ["server.install", "server.verify"])
        self.assertNotEqual(states["server"].status, SE.STATUS_CANCELED)

        for key in ("service-controls", "kiwix"):
            self.assertEqual(states[key].status, SE.STATUS_CANCELED)
            self.assertEqual(states[key].reason_code, "JOB_CANCELED")


if __name__ == "__main__":
    unittest.main()
