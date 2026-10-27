"""One Generate, several batches (generation/tasks.more_batches, batches_max): the next preset grids of an emoji pack, else the next bank actions. Pure: no provider."""
import unittest
from pathlib import Path

from stickerloom.generation import actions, tasks


class MoreBatchesTests(unittest.TestCase):
    def test_an_emoji_pack_goes_through_its_four_grids_once(self):
        p = tasks.preview("generic emojis")
        self.assertEqual((p["slots"]["preset"], p["batches_max"]), ("core-v1", 4))
        more = tasks.more_batches("generic emojis", p, 3)
        self.assertEqual([m["slots"]["preset"] for m in more], ["social-v1", "reactions-v1", "daily-v1"])
        keys = [s["key"] for x in [p, *more] for s in x["stickers"]]
        self.assertEqual(len(keys), 36)
        self.assertEqual(len(set(keys)), 36, "36 different actions, none twice")
        self.assertEqual(len(tasks.more_batches("generic emojis", p, 9)), 3, "never more than the grids left")

    def test_a_named_grid_is_skipped_not_repeated(self):
        p = tasks.preview("generic emojis social-v1")
        self.assertEqual([m["prompt"] for m in tasks.more_batches("generic emojis social-v1", p, 3)],
                         ["generic emojis core-v1", "generic emojis reactions-v1", "generic emojis daily-v1"])

    def test_any_other_request_takes_the_next_unused_bank_actions(self):
        p = tasks.preview("a teddy bear")
        more = tasks.more_batches("a teddy bear", p, 2)
        self.assertEqual(len(more), 2)
        used = [actions.canonical_mood(s["key"], s["tags"]) for x in [p, *more] for s in x["stickers"]]
        names = [u[0] for u in used if u]
        self.assertEqual(len(names), len(set(names)), "no action twice across the batches")

    def test_one_character_changed_is_one_batch(self):
        self.assertEqual(tasks.preview("turn the banana into a pirate")["batches_max"], 1)
        self.assertEqual(tasks.more_batches("turn the banana into a pirate", tasks.preview("turn the banana into a pirate"), 3), [])


class PackGridTests(unittest.TestCase):
    """2026-10-09: batch 3 of "original generic emojis, colorful" repeated batch 1, because the request already carried "social-v1", two grid names were read
    as the first one, and only the batches on screen counted. Batch k of an emoji pack is grid k (core 1-9, social 10-18, reactions 19-27, daily 28-36)."""

    def test_the_last_named_grid_wins_and_grid_names_strip(self):
        self.assertEqual(actions.preset_name("original generic emojis, colorful social-v1 reactions-v1"), "reactions-v1")
        self.assertEqual(actions.strip_presets("original generic emojis, colorful social-v1"), "original generic emojis, colorful")
        self.assertEqual(actions.strip_presets("emojis core v1"), "emojis")

    def test_a_request_that_names_a_grid_never_repeats_it_in_the_following_batches(self):
        req = "original generic emojis, colorful social-v1"
        more = tasks.more_batches(req, tasks.preview(req), 3)
        self.assertEqual([m["prompt"] for m in more], ["original generic emojis, colorful core-v1", "original generic emojis, colorful reactions-v1",
                                                       "original generic emojis, colorful daily-v1"])
        self.assertEqual([m["slots"]["preset"] for m in more], ["core-v1", "reactions-v1", "daily-v1"])

    def test_the_session_counts_every_batch_of_the_pack_even_off_screen(self):
        from unittest import mock
        from stickerloom.flow import groups, pipeline as pl
        def res(n, prompt, preset):
            return {"number": n, "generation_id": f"G{n:03d}", "owner": "local", "prompt": prompt, "grid": [3, 3], "slots": {"preset": preset, "face": True},
                    "stickers": [], "source": {}}
        disk = {121: res(121, "original generic emojis, colorful", "core-v1"), 122: res(122, "original generic emojis, colorful social-v1", "social-v1"),
                130: res(130, "a different pack", "reactions-v1")}
        with mock.patch.object(pl, "read_result", lambda out, g: disk[int(g)]), mock.patch.object(pl, "list_ids", lambda out: sorted(disk)),                 mock.patch.object(groups, "root_of", lambda out, g: int(g)), mock.patch.object(groups, "members", lambda out, g: [int(g)]):
            s = tasks.session_state(Path("."), [122])                       # only batch 2 on screen
            self.assertEqual((s["request"], sorted(s["used_presets"]), s["existing"]), ("original generic emojis, colorful", ["core-v1", "social-v1"], 2))
            n = tasks.next_batch(Path("."), [122])
            self.assertEqual((n["slots"]["preset"], n["existing"], n["batches_max"]), ("reactions-v1", 2, 2), "batch 3 is reactions (19-27); 3 and 4 are offered")


if __name__ == "__main__":
    unittest.main()
