import json
import importlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stickerloom import cli, test_tiers


class TierContractTests(unittest.TestCase):
    def test_focused_is_small_explicit_and_starts_with_the_exact_gate(self):
        def ids(suite):
            for case in suite:
                if isinstance(case, unittest.TestSuite):
                    yield from ids(case)
                else:
                    yield case.id()
        names = list(ids(test_tiers.suite_for("focused")))
        self.assertEqual(names[0], test_tiers.GOLDEN_GATE)
        self.assertGreaterEqual(len(names), 20)
        self.assertLessEqual(len(names), 25)
        self.assertEqual(len(names), len(set(names)))
        for profile in test_tiers.FOCUSED_PROFILES:
            selected = list(ids(test_tiers.suite_for("focused", profile)))
            self.assertEqual(selected[0], test_tiers.GOLDEN_GATE)
            self.assertTrue(set(selected) <= set(names))
        with self.assertRaisesRegex(ValueError, "No focused profile"):
            test_tiers.suite_for("focused", "not-real")

    def test_focused_cli_accepts_an_explicit_profile(self):
        with mock.patch.object(test_tiers, "run", return_value=0) as run:
            self.assertEqual(cli.main(["test", "focused", "video-sheet"]), 0)
            run.assert_called_once_with("focused", "video-sheet")

    def test_focused_never_stamps_full_media_verification(self):
        with mock.patch.object(test_tiers, "suite_for", return_value=unittest.TestSuite([unittest.FunctionTestCase(lambda: None)])), mock.patch.object(test_tiers, "_write_slow_result") as stamp:
            self.assertEqual(test_tiers.run("focused"), 0)
            stamp.assert_not_called()

    def test_empty_selection_is_not_a_pass(self):
        with mock.patch.object(test_tiers, "suite_for", return_value=unittest.TestSuite()):
            self.assertEqual(test_tiers.run("focused"), 2)

    def test_slow_tier_is_exactly_the_six_media_modules(self):
        self.assertEqual(test_tiers.SLOW_MODULES, (
            "tests.test_golden", "tests.test_effect_video", "tests.test_allow_still",
            "tests.test_anim_speed", "tests.test_engine", "tests.test_verify",
        ))

    def test_area_uses_the_one_explicit_map_and_never_guesses(self):
        self.assertGreater(test_tiers.suite_for("area", "stickerloom/engine/video.py").countTestCases(), 0)
        for names in test_tiers.MODULE_TESTS.values():
            for name in names:
                module, cls = name.rsplit(".", 1)
                self.assertTrue(hasattr(importlib.import_module(module), cls), name)
        with self.assertRaisesRegex(ValueError, "No area map"):
            test_tiers.suite_for("area", "engine/not_real")

    def test_doctor_status_distinguishes_current_stale_and_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            result = Path(td) / "slow.json"
            result.write_text(json.dumps({"success": True, "ran": 30, "skipped": 0}), encoding="utf-8")
            with mock.patch.object(test_tiers, "SLOW_RESULT", result), mock.patch.object(test_tiers, "newest_engine_mtime", return_value=result.stat().st_mtime - 1):
                self.assertEqual(test_tiers.slow_status()["state"], "verified")
            with mock.patch.object(test_tiers, "SLOW_RESULT", result), mock.patch.object(test_tiers, "newest_engine_mtime", return_value=result.stat().st_mtime + 1):
                self.assertEqual(test_tiers.slow_status()["state"], "stale")
            result.write_text(json.dumps({"success": False, "ran": 29, "skipped": 1}), encoding="utf-8")
            with mock.patch.object(test_tiers, "SLOW_RESULT", result), mock.patch.object(test_tiers, "newest_engine_mtime", return_value=result.stat().st_mtime - 1):
                status = test_tiers.slow_status()
                self.assertEqual((status["state"], status["ran"], status["skipped"]), ("failed", 29, 1))

    def test_cli_has_no_implicit_tier(self):
        with mock.patch.object(test_tiers, "run", return_value=0) as run:
            self.assertEqual(cli.main(["test", "fast"]), 0)
            run.assert_called_once_with("fast", None)


if __name__ == "__main__":
    unittest.main()
