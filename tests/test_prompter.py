import json
import tempfile
import unittest
from pathlib import Path

from stickerloom.flow import pipeline as pl
from stickerloom.generation import prompter

MARGIN = "generous empty margin on every side"


class TemplateTests(unittest.TestCase):
    def test_every_cell_has_tags_and_the_margin_clause(self):
        for grid, n, tid in (((3, 3), 9, "sheet_3x3"), ((2, 2), 4, "sheet_2x2"), ((1, 1), 1, "single_1x1")):
            p = prompter.expand("teddy yellow bear for school", grid)
            self.assertEqual((len(p["stickers"]), p["template_id"], p["template_version"]), (n, tid, prompter.TEMPLATE_VERSION))
            self.assertEqual(p["slots"]["mode"], tid)
            for s in p["stickers"]:
                self.assertTrue(1 <= len(s["tags"]) <= 5, s["tags"])
                self.assertEqual(s["tags"][0], s["key"])
                self.assertEqual(len(set(s["tags"])), len(s["tags"]))
                self.assertTrue(all(prompter.tag(t) == t and t for t in s["tags"]), s["tags"])
                self.assertIn(MARGIN, s["prompt"])
            self.assertIn(MARGIN, p["sheet_prompt"])

    def test_prompts_are_rebuilt_from_the_template_file(self):
        p = prompter.expand("teddy bear for school")
        self.assertEqual(prompter.render_plan(p["slots"], p["template_id"], p["template_version"])["sheet_prompt"], p["sheet_prompt"])
        p["sheet_prompt"] = "tampered free text"
        again = prompter.validate_plan(json.loads(json.dumps(p)))
        self.assertNotEqual(again["sheet_prompt"], "tampered free text")      # the saved template + slots win over stored text
        self.assertIn("3 rows of 3", again["sheet_prompt"])
        v1 = dict(p, template_version=1)                                          # a plan saved with v1 still rebuilds to the v1 wording
        self.assertIn("3x3 sticker sheet", prompter.validate_plan(json.loads(json.dumps(v1)))["sheet_prompt"])
        with self.assertRaises(ValueError):
            prompter.render_plan(p["slots"], "sheet_3x3", 99)
        for name in ("sheet_3x3_v1", "sheet_2x2_v1", "single_1x1_v1", "video_v1", "sheet_3x3_v2", "sheet_2x2_v2", "single_1x1_v2", "video_v2", "sheet_3x3_v3", "sheet_2x2_v3", "single_1x1_v3", "video_v3", "sheet_3x3_v4", "sheet_2x2_v4", "single_1x1_v4", "video_v4"):
            self.assertTrue((prompter.TEMPLATES / f"{name}.txt").is_file(), name)

    def test_old_prompts_files_without_tags_still_load(self):
        old = {"task_slug": "x", "stickers": [{"index": i, "prompt": "p", "key": f"k{i}", "emoji": "x"} for i in range(1, 5)]}
        plan = prompter.validate_plan(old)
        self.assertEqual([s["tags"] for s in plan["stickers"]], [[f"k{i}"] for i in range(1, 5)])
        odd = prompter.clean_tags("key", ["Key", "A B", "a_b", "x1", "x2", "x3", "x4", "!!"])
        self.assertEqual(odd, ["key", "a_b", "x1", "x2", "x3"])               # unique, [a-z0-9_]+, at most 5, the key first

    def test_results_written_before_1f_still_render(self):
        old = {"generation_id": "G001", "stage": "video_sliced", "stickers": [
            {"index": 1, "key": "k", "status": "READY", "name": "n"}, {"index": 2, "key": "j", "status": "FAILED", "name": "m"}]}
        n = pl.normalise(old)
        self.assertEqual([s["review"]["still"] for s in n["stickers"]], ["PENDING", "BLOCKED"])
        self.assertEqual((n["stickers"][0]["tags"], n["video_sheets"], n["reviews"]["plan"]), (["k"], [], None))


if __name__ == "__main__":
    unittest.main()
