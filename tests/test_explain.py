"""Plain words for a blocked sheet (flow/explain.py): the falcon sheet G094 of 2026-10-02."""
import unittest

from stickerloom.flow import explain


def blocked(check="grid_detected", value="3x2", limit="3x3"):
    rep = [{"name": check, "ok": False, "severity": "BLOCK", "value": value, "limit": limit, "detail": "x"}]
    return {"stage": "sliced", "source": {"sheet_path": r"G:\x\out\jobs\J027\result.png"},
            "stickers": [{"index": i, "status": "FAILED", "reason": check, "report": rep, "metrics": {"sheet_blocked": True}} for i in range(1, 10)]}


class ExplainTests(unittest.TestCase):
    def test_a_blocked_grid_is_explained_with_the_paid_job(self):
        p = explain.sheet_problem(blocked())
        self.assertEqual((p["check"], p["value"], p["limit"]), ("grid_detected", "3x2", "3x3"))
        self.assertIn("3x2", p["why"])
        self.assertIn("3x3", p["why"])
        self.assertEqual(p["received"]["job"], "J027")
        self.assertTrue(p["retry"])

    def test_every_sheet_check_that_can_block_has_its_own_words(self):
        for c in ("grid_detected", "background_is_key", "sheet_size", "sheet_decodes"):
            self.assertIn(c, explain.HINTS)
            self.assertNotEqual(explain.sheet_problem(blocked(c, 1, 2))["title"], explain.GENERIC["title"])
        self.assertEqual(explain.sheet_problem(blocked("something_new"))["title"], explain.GENERIC["title"])

    def test_a_healthy_or_partly_failed_batch_has_no_sheet_problem(self):
        res = blocked()
        res["stickers"][0]["metrics"] = {}                      # one cell failed on its own: that is a sticker problem, not a sheet problem
        self.assertIsNone(explain.sheet_problem(res))
        self.assertIsNone(explain.sheet_problem({"stage": "stills_reviewed", "stickers": []}))


if __name__ == "__main__":
    unittest.main()


class CutAnywayTests(unittest.TestCase):
    def test_a_stopped_sheet_offers_the_free_click_unless_the_file_does_not_open(self):
        from stickerloom.flow import explain
        self.assertTrue(explain.sheet_problem(blocked("background_is_key", 3, 40))["cut_anyway"])
        self.assertFalse(explain.sheet_problem(blocked("sheet_decodes", None, None))["cut_anyway"])
