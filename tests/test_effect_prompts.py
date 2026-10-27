"""The prompts of the particle effects: template-locked (a plan in, text out), linted, with the screen colour chosen by the particles, and (version 2) the layout said only as positions."""
import re
import unittest

from stickerloom.generation import effect_prompts as ep

# what version 1 sent to Kling for J039 (2026-10-03): it must stay rebuildable, word for word, from the stored plan
J039_PROMPT = (
    "A flat, perfectly even, solid #00FF00 green screen with no shadows, no floor, no gradient, no text and no borders. Static camera, square frame, 3 seconds. "
    "The frame is an invisible grid of 2 by 2 equal square cells and nothing ever crosses from one cell into another. In EVERY cell at the same time, the same small burst effect plays: "
    "bat signal, lego brick and cape piece, all related to Batman Lego. Timeline for each cell: from 0.0 to 0.4 seconds the cell is completely empty green screen. At 0.4 seconds the pieces appear "
    "at the exact centre of the cell and burst outward in a spiralling vortex, growing a little as they fly. From about 1.2 seconds gravity takes over: the pieces slow down, arc and fall downward "
    "out of the cell. By 2.6 seconds every piece has left the cell and the cell is completely empty green screen again until the end at 3.0 seconds. The pieces are small and separate, never touching "
    "the cell edge; only the pieces listed are drawn, never one large central Batman Lego. Look: glossy cartoon look, bold clean shapes, vivid colours. Each cell is a different take of the same effect: "
    "cell 1 (top row, left): a tight fast vortex; cell 2 (top row, right): a wide gentle burst; cell 3 (bottom row, left): a slow lazy swirl; cell 4 (bottom row, right): a sharp pop that rises high first.")
# the words that made Kling and Nano Banana draw boxes, walls, panels and "a poster of faces". Listed here on purpose, apart from the module's own regex.
BANNED = ("square", "squares", "frame", "frames", "poster", "posters", "screen", "screens", "box", "boxes", "cell", "cells", "grid", "grids", "panel", "panels", "section", "sections",
          "tile", "tiles", "border", "borders", "divider", "dividers", "split", "splits", "invisible", "layout", "layouts")
J039_PLAN = {"subject": "Batman Lego", "elements": ["bat signal", "lego brick", "cape piece"], "style": "glossy cartoon look, bold clean shapes, vivid colours"}


class EffectPromptTests(unittest.TestCase):
    PLAN = {"subject": "strawberry", "elements": ["ripe red strawberries", "tiny seeds", "small gold sparkles"]}

    def test_a_burst_starts_and_ends_empty_and_has_no_start_image(self):
        t = ep.video_prompt(self.PLAN, 2, 2)
        for must in ("pure #00FF00 green", "for the first 0.4 seconds there is only the green background", "At 0.4 seconds each burst starts from the centre of its quarter", "From 1.2 seconds gravity",
                     "From 2.6 seconds there is only the green background again until 3.0 seconds", "never one large central strawberry", "Look: glossy cartoon look", "Static camera."):
            self.assertIn(must, t)
        self.assertNotIn("start image", t.lower())

    def test_the_layout_is_only_ever_said_as_position_names(self):
        t = ep.video_prompt(self.PLAN, 2, 2)
        self.assertIn("Four small bursts play at the same time, one in each quarter, each centred in its quarter: top-left a tight fast vortex, top-right a wide gentle burst, "
                      "bottom-left a slow lazy swirl and bottom-right a sharp pop that rises high.", t)
        self.assertIn("Everywhere the background is one seamless, flat, uniform, pure #00FF00 green: no texture, no shading or shadows, no pattern, no vignette.", t)
        for rows, cols in ((2, 2), (3, 3), (1, 1), (1, 3), (3, 1), (2, 3)):
            t = ep.video_prompt(self.PLAN, rows, cols)
            self.assertIsNone(ep.BANNED_WORDS.search(t), (rows, cols, ep.BANNED_WORDS.search(t)))
            self.assertEqual(t.count("#00FF00"), 1, "the background is described once")
            self.assertEqual(t.count("only the green background"), 2, "nothing but the background at the start and at the end, said short")
            self.assertNotIn("aspect", t.lower(), "size and aspect ratio are request parameters, never words")
            for name in ep.positions(rows, cols):
                self.assertIn(name, t)

    def test_no_banned_word_is_in_any_template_text(self):
        """Whole words, any case, plural too: the video prompt (2x2, 3x3, the other layouts, blue) and the particle image sheet (2x2, 3x3)."""
        texts = {f"video {r}x{c}": ep.video_prompt(self.PLAN, r, c) for r, c in ((2, 2), (3, 3), (1, 1), (1, 3), (3, 1), (2, 3))}
        texts["video 2x2 blue"] = ep.video_prompt({"subject": "frog", "elements": ["green leaves", "tiny flies"]}, 2, 2)
        texts["video 2x2 upper case"] = ep.video_prompt(self.PLAN, 2, 2).upper()
        texts.update({f"particles {r}x{c}": ep.pieces_prompt(self.PLAN, r, c) for r, c in ((2, 2), (3, 3))})
        texts["particles 2x2 blue"] = ep.pieces_prompt({"subject": "frog", "elements": ["green leaves", "tiny flies"]}, 2, 2)
        for name, t in texts.items():
            for w in BANNED:
                self.assertIsNone(re.search(rf"\b{w}\b", t, re.I), f"{name} says '{w}'")
            self.assertIsNone(ep.BANNED_WORDS.search(t), name)
        for text in ("a square frame", "four squares", "a poster", "green screen", "grid", "Boxes", "tiled panels", "a divider", "split in two", "invisible", "the layout"):
            self.assertIsNotNone(ep.BANNED_WORDS.search(text), f"the rule must catch '{text}'")
        for text in ("strawberries", "sparkles", "bold clean shapes", "bat signal"):
            self.assertIsNone(ep.BANNED_WORDS.search(text), text)
        # a person's own particle names are not policed for these words, only the template's text is
        self.assertEqual(ep.lint_plan({"subject": "tile shop", "elements": ["square tiles", "gift box"]})["elements"], ["square tiles", "gift box"])

    def test_version_one_keeps_its_old_wording_for_stored_jobs(self):
        self.assertIsNotNone(ep.BANNED_WORDS.search(ep.video_prompt(self.PLAN, 2, 2, version=1)), "version 1 is what drew the boxes (J039): kept as it was sent, never reused")

    def test_the_two_by_two_text_is_short(self):
        self.assertLess(len(ep.video_prompt(self.PLAN, 2, 2)), 950, "fewer sentences: version 1 was about 1,480 characters")
        self.assertLess(len(ep.video_prompt(self.PLAN, 2, 2)), len(ep.video_prompt(self.PLAN, 2, 2, version=1)) * 0.7)

    def test_positions_are_named_in_reading_order(self):
        self.assertEqual(ep.positions(2, 2), ["top-left", "top-right", "bottom-left", "bottom-right"])
        self.assertEqual(ep.positions(3, 3), ["top-left", "top-centre", "top-right", "middle-left", "centre", "middle-right", "bottom-left", "bottom-centre", "bottom-right"])
        self.assertEqual((ep.positions(1, 1), ep.positions(1, 3), ep.positions(3, 1)), (["centre"], ["left", "centre", "right"], ["top", "middle", "bottom"]))
        with self.assertRaises(ValueError):
            ep.positions(4, 1)

    def test_the_screen_is_blue_when_a_particle_is_green(self):
        self.assertEqual(ep.key_colour_for(["ripe red strawberries"]), "green")
        self.assertEqual(ep.key_colour_for(["strawberries", "green leaves"]), "blue")
        t = ep.video_prompt({"subject": "strawberry", "elements": ["red strawberries", "green leaves"]}, 2, 2)
        self.assertIn("#0000FF blue", t)
        self.assertIn("only the blue background", t)
        self.assertNotIn("green", t.replace("green leaves", ""))

    def test_three_by_three_has_nine_different_takes_by_position(self):
        t = ep.video_prompt(self.PLAN, 3, 3)
        self.assertIn("Nine small bursts play at the same time, one at each position, each centred on its position: top-left a tight fast vortex", t)
        self.assertEqual(len({v for v in ep.VARIATIONS if v in t}), 9)
        self.assertIn("bottom-right a fountain that shoots straight up", t)

    def test_the_takes_can_be_overridden_per_position(self):
        t = ep.video_prompt(self.PLAN, 2, 2, cells=["a slow fall", "a fast fall"])
        self.assertIn("top-left a slow fall, top-right a fast fall, bottom-left a slow lazy swirl", t, "the missing takes come from the table")

    def test_version_one_is_kept_and_rebuilds_what_was_sent(self):
        self.assertEqual(ep.video_prompt(J039_PLAN, 2, 2, version=1), J039_PROMPT)
        self.assertNotEqual(ep.video_prompt(J039_PLAN, 2, 2), J039_PROMPT)
        with self.assertRaises(ValueError):
            ep.video_prompt(J039_PLAN, 2, 2, version=3)

    def test_the_plan_is_linted_with_a_reason_a_person_can_act_on(self):
        for bad, why in (({"subject": "", "elements": ["x"]}, "subject"), ({"subject": "cat", "elements": []}, "at least one particle"),
                         ({"subject": "cat", "elements": ["a sticker of a cat"]}, "sticker"), ({"subject": "cat", "elements": ["one two three four five six"]}, "too long"),
                         ({"subject": "cat", "elements": [f"p{i}" for i in range(10)]}, "at most 9"), ({"subject": "cat", "elements": ["paws"], "style": "with text"}, "style")):
            with self.assertRaises(ValueError) as cm:
                ep.lint_plan(bad)
            self.assertIn(why, str(cm.exception))
        self.assertEqual(ep.lint_plan({"subject": " Cat ", "elements": ["paws", "PAWS", " ears. "]})["elements"], ["paws", "ears"])
        with self.assertRaises(ValueError):
            ep.video_prompt(self.PLAN, 4, 4)

    def test_describe_is_what_the_page_shows(self):
        d = ep.describe(self.PLAN, 2, 2)
        self.assertEqual((d["template"], d["version"], d["key"], d["grid"]), ("effect_video", 2, "green", [2, 2]))
        self.assertEqual(set(d), {"template", "version", "key", "grid", "plan", "prompt"})
        self.assertEqual(d["prompt"], ep.video_prompt(self.PLAN, 2, 2))
        old = ep.describe(J039_PLAN, 2, 2, version=1)
        self.assertEqual((old["version"], old["prompt"]), (1, J039_PROMPT), "a stored job's version reads back its own text")


if __name__ == "__main__":
    unittest.main()
