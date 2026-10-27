"""The prompt of the particle sheet of a particle effect (docs/architecture.md ("Reactions")): template-locked, built from the linted plan only. The rest of that feature (the base plan the normal sheet path
accepts, the price first, the job, the particle batch and its link to every group, the pick, the old records) is `tests/test_particle_set.py`. Nothing here reaches a provider."""
import unittest

from stickerloom.generation import effect_prompts as ep

GOLD = {"subject": "jewelry", "elements": ["gold bar", "diamond", "ring", "coin"]}


class PiecesPromptTests(unittest.TestCase):
    def test_the_prompt_is_a_sheet_of_different_isolated_particles_on_the_key_colour_without_an_outline(self):
        t = ep.pieces_prompt(GOLD, 2, 2)
        for must in ("4 separate small particles", "one at each position: top-left, top-right, bottom-left and bottom-right", "Outline: none", "#00FF00 green", "centred there",
                     "Particles of jewelry: small things that fly out when the jewelry emoji is pressed", "Particle 1 (top-left): gold bar", "Particle 2 (top-right): diamond",
                     "Particle 4 (bottom-right): coin", "never a character, a person", "one seamless, flat, uniform pure #00FF00 green backdrop from edge to edge"):
            self.assertIn(must, t)
        self.assertNotIn("sticker", t.lower(), "the word sticker makes image models draw a die-cut border")
        self.assertNotIn("piece", t.lower(), "the person and the model read 'particles'")
        self.assertIsNone(ep.BANNED_WORDS.search(t), "no cell, grid, panel, tile, box or border word: Kling and Nano Banana draw what they read")
        self.assertEqual((ep.PIECES_TEMPLATE_ID, ep.PIECES_VERSION), ("effect_pieces", 2))
        self.assertEqual((ep.PARTICLES_TEMPLATE_ID, ep.PARTICLES_VERSION), ("effect_pieces", 2), "the new name of the same template")
        self.assertIs(ep.particles_prompt, ep.pieces_prompt)
        self.assertEqual(ep.describe_particles(GOLD)["version"], 2)

    def test_version_one_is_kept_so_a_stored_plan_can_be_rebuilt(self):
        t = ep.pieces_prompt(GOLD, 2, 2, version=1)
        for must in ("Theme: pieces that burst out of jewelry", "Each cell holds exactly ONE piece", "Piece 1 (top row, left): gold bar"):
            self.assertIn(must, t)
        self.assertEqual(ep.describe_pieces(GOLD, 2, 2, version=1)["version"], 1)
        with self.assertRaises(ValueError):
            ep.pieces_prompt(GOLD, 2, 2, version=3)

    def test_fewer_particles_than_places_are_cycled_in_other_sizes_so_no_two_match(self):
        cells = ep.pieces_cells({"subject": "jewelry", "elements": ["gold bar", "diamond"]}, 3, 3)
        labels = [c["label"] for c in cells]
        self.assertEqual(len(labels), 9)
        self.assertEqual(len(set(labels)), 9)
        self.assertEqual(labels[0], "gold bar")
        self.assertEqual(labels[1], "diamond")
        self.assertTrue(labels[2].startswith("gold bar, "), labels[2])
        one = ep.pieces_cells({"subject": "heart", "elements": ["red heart"]}, 2, 2)
        self.assertEqual(len({c["label"] for c in one}), 4)
        self.assertEqual([c["pos"] for c in one], [1, 2, 3, 4])

    def test_more_particles_than_places_use_the_first_ones(self):
        cells = ep.pieces_cells({"subject": "x", "elements": [f"piece{i}" for i in range(6)]}, 2, 2)
        self.assertEqual([c["label"] for c in cells], ["piece0", "piece1", "piece2", "piece3"])

    def test_three_by_three_and_the_screen_colour(self):
        t = ep.pieces_prompt({"subject": "strawberry", "elements": ["red strawberry", "green leaf"]}, 3, 3)
        self.assertIn("9 separate small particles", t)
        self.assertIn("#0000FF blue", t, "a green particle makes the screen blue")
        self.assertEqual(sum(f"Particle {i} (" in t for i in range(1, 10)), 9)
        for name in ("top-left", "top-centre", "top-right", "middle-left", "centre", "middle-right", "bottom-left", "bottom-centre", "bottom-right"):
            self.assertIn(f"({name})", t)
        self.assertIsNone(ep.BANNED_WORDS.search(t))
        self.assertEqual(ep.describe_pieces({"subject": "strawberry", "elements": ["green leaf"]}, 2, 2)["key"], "blue")

    def test_the_plan_is_linted_and_the_subject_never_says_sticker(self):
        with self.assertRaises(ValueError):
            ep.pieces_prompt({"subject": "cat", "elements": ["a sticker of a cat"]}, 2, 2)
        with self.assertRaises(ValueError):
            ep.pieces_prompt({"subject": "cat", "elements": ["a logo"]}, 2, 2)
        with self.assertRaises(ValueError):
            ep.pieces_prompt(GOLD, 4, 4)
        t = ep.pieces_prompt({"subject": "Batman sticker", "elements": ["bat signal"]}, 2, 2)
        self.assertNotIn("sticker", t.lower())
        self.assertIn("Batman", t)


if __name__ == "__main__":
    unittest.main()
