"""Predefined actions use the full bank; creative requests and named presets retain their behavior."""
import unittest

from stickerloom.generation import actions, tasks


class ActionsModeTests(unittest.TestCase):
    def test_predefined_draws_the_first_nine_bank_actions_as_whole_characters(self):
        p = tasks.preview("kid superhero", "3x3", actions_mode="predefined")
        self.assertEqual(p["actions_mode"], "predefined")
        self.assertEqual([s["key"].rsplit("_", 1)[-1] for s in p["stickers"]][:3], ["happy", "laugh", "cry"])
        self.assertFalse(p["slots"]["face"], "a character is drawn whole, not as a face")
        self.assertEqual(len(p["stickers"]), 9)
        self.assertGreater(p["batches_max"], 1, "unused bank emojis follow as the next batches")

    def test_predefined_emoji_requests_use_the_full_bank_and_named_presets_keep_their_cells(self):
        self.assertNotEqual(tasks.preview("kid superhero", "3x3").get("actions_mode"), "predefined")
        a, b = tasks.preview("cat emojis", "3x3"), tasks.preview("cat emojis", "3x3", actions_mode="predefined")
        self.assertEqual([s["emoji"] for s in b["stickers"]], [actions.generation_bank()[t]["emoji"] for t in actions.next_tokens([],9)])
        self.assertEqual(a["slots"]["preset"], "core-v1")
        self.assertEqual(b["actions_mode"], "predefined")
        self.assertIsNone(b["slots"]["preset"])
        named=tasks.preview("cat emojis social-v1", "3x3", actions_mode="predefined")
        self.assertEqual(named["slots"]["preset"],"social-v1")

    def test_an_unknown_mode_is_refused(self):
        with self.assertRaises(Exception):
            tasks.preview("kid superhero", "3x3", actions_mode="random")

    def test_the_bank_is_the_four_grids(self):
        self.assertEqual(set(actions.ACTION_BANK), {t for g in actions.PRESETS.values() for t in g})


if __name__ == "__main__":
    unittest.main()
