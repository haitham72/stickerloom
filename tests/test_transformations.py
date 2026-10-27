"""Transformation templates ("dog as banana"): what counts as one, the required cells, the user's overrides, the lint, and the integration with the planner and the chat.
Deterministic: no model, no network."""
import unittest

from stickerloom.generation import expander, prompter, tasks
from stickerloom import transformations as T
from stickerloom.transformations import base


class DetectTests(unittest.TestCase):
    def test_a_subject_that_becomes_something_is_a_transformation(self):
        for text, subject, target in (("dog as banana", "dog", "banana"), ("make a dog as a banana", "dog", "banana"), ("turn my cat into a pizza", "cat", "pizza"),
                                      ("a frog shaped like a pear", "frog", "pear"), ("create a teddy bear turned into a cupcake", "teddy bear", "cupcake"),
                                      ("a dog that looks like a banana", "dog", "banana"), ("DOG   AS   BANANA!", "dog", "banana")):
            m = T.detect(text)
            self.assertIsNotNone(m, text)
            self.assertEqual((m.subject, m.target), (subject, target), text)

    def test_two_things_a_role_or_a_comparison_is_not(self):
        for text in ("dog with bananas", "a dog holding a banana", "dog eating a banana", "dog and banana", "a dog wearing a banana hat", "a dog with a banana as a hat",
                     "dog as a pilot", "a cat as a king", "happy teddy bear", "banana", "as a banana", "a birthday sticker pack as a gift", "a dog as big as a house",
                     "make 5 stickers as soon as possible", "a dog riding a banana", ""):
            self.assertIsNone(T.detect(text), text)

    def test_the_context_is_kept_and_not_confused_with_the_target(self):
        m = T.detect("make a dog as a banana for school")
        self.assertEqual((m.target, m.context), ("banana", "school"))

    def test_overrides_are_read_from_the_request(self):
        self.assertEqual(T.detect("dog as banana, no dancing").forbidden, ["danc"])
        self.assertEqual(T.detect("dog as banana without squash or shock").forbidden, ["squash", "shock"])
        self.assertEqual(T.detect("dog as banana but no crying").forbidden, ["cry"])
        self.assertEqual(T.detect("dog as banana, not too scary").forbidden, ["scary"])                # "too" is filler, the word after it is what is ruled out
        self.assertEqual(T.detect("dog as banana").forbidden, [])
        self.assertEqual(base.stem("dancing"), base.stem("dancer"))


class PlanTests(unittest.TestCase):
    def plan(self, text, grid=(3, 3)):
        p = T.plan(prompter.expand(text, grid), text, *grid)
        self.assertIsNotNone(p, text)
        return p

    def test_dog_as_banana_has_the_three_required_cells_and_a_full_grid(self):
        p = self.plan("dog as banana")
        cells = p["slots"]["cells"]
        self.assertEqual(len(cells), 9)
        self.assertEqual(p["slots"]["transformation"], {"id": "subject_as_target", "version": 1, "flavour": "banana", "subject": "dog", "target": "banana",
                                                        "required": ["dance", "shock", "squash"], "forbidden": [], "connector": "as"})
        tags = [set(c["tags"]) for c in cells]
        for rid in ("dance", "shock", "squash"):
            self.assertEqual(sum(rid in t for t in tags), 1, rid)
        self.assertEqual(len({c["tags"][0] for c in cells}), 9)                                          # nine different keys
        self.assertEqual(len({c["label"] for c in cells}), 9)
        for c in cells:
            self.assertTrue(1 <= len(c["tags"]) <= 5 and c["emoji"] and c["motion"])
            self.assertTrue(c["tags"][0].startswith("dog_as_banana_"))
            self.assertIn("banana", c["tags"])                                                           # searchable as a banana and as a dog
            self.assertIn("dog", c["tags"])

    def test_the_character_sentence_says_one_character_and_reaches_every_prompt(self):
        p = self.plan("dog as banana")
        sd = p["slots"]["subject_description"]
        for must in ("a dog transformed into a banana", "ONE single character", "not a dog standing next to a banana", "not a dog holding or wearing a banana"):
            self.assertIn(must, sd)
        self.assertIn("banana-yellow", sd)                                                              # the banana lexicon
        self.assertIn(sd, p["sheet_prompt"])
        self.assertIn("the banana body sways", p["video_prompt"])                                        # the video prompt carries the per-cell motions (the sheet is its start image)
        self.assertTrue(all("a dog transformed into a banana" in s["prompt"] for s in p["stickers"]))
        self.assertEqual((p["expanded_by"], p["kind"], p["subject"], p["task_slug"]), ("transformation", "transformation", "dog as banana", "dog_as_banana"))

    def test_the_plan_has_the_shape_of_every_other_plan(self):
        base_plan = prompter.expand("dog as banana", (3, 3))
        p = self.plan("dog as banana")
        self.assertEqual(set(base_plan) - set(p), set())                                                  # nothing the pipeline reads is missing
        self.assertEqual(sorted(s["index"] for s in p["stickers"]), list(range(1, 10)))
        prompter.validate_plan(dict(p))                                                                  # the pipeline's own check accepts it
        self.assertEqual(prompter.render_plan(p["slots"], p["template_id"], p["template_version"])["sheet_prompt"], p["sheet_prompt"])      # deterministic

    def test_the_user_can_rule_out_a_required_cell(self):
        p = self.plan("dog as banana, no dancing")
        cells = p["slots"]["cells"]
        self.assertEqual(len(cells), 9)                                                                  # still a full sheet
        self.assertEqual(p["slots"]["transformation"]["required"], ["shock", "squash"])
        self.assertEqual(p["slots"]["transformation"]["forbidden"], ["danc"])
        for c in cells:
            self.assertNotIn("danc", f"{c['label']} {c['motion']} {c['tags'][0]}".lower())               # nowhere, not even in a filler cell
        p = self.plan("dog as banana without squash or shock")
        self.assertEqual(p["slots"]["transformation"]["required"], ["dance"])
        self.assertEqual(len(p["slots"]["cells"]), 9)
        self.assertFalse(base.Transformation().validate(p["slots"]))

    def test_a_word_nobody_knows_is_still_kept_out(self):
        p = self.plan("dog as banana, no crying")
        blob = " ".join(f"{c['label']} {c['motion']} {c['tags'][0]}" for c in p["slots"]["cells"]).lower()
        self.assertNotIn("cry", blob)
        self.assertEqual(len(p["slots"]["cells"]), 9)

    def test_smaller_grids(self):
        p = self.plan("dog as banana", (2, 2))
        self.assertEqual(len(p["slots"]["cells"]), 4)
        self.assertEqual(p["slots"]["transformation"]["required"], ["dance", "shock", "squash"])        # the three, plus one more
        self.assertEqual(p["slots"]["mode"], "sheet_2x2")
        p = self.plan("dog as banana", (1, 1))
        self.assertEqual(len(p["slots"]["cells"]), 1)
        self.assertEqual(p["slots"]["transformation"]["required"], ["dance"])                          # only what fits

    def test_any_target_works_with_the_generic_wording(self):
        p = self.plan("turn my cat into a pizza")
        tr = p["slots"]["transformation"]
        self.assertEqual((tr["flavour"], tr["target"], tr["required"]), (None, "pizza", ["dance", "shock", "squash"]))
        self.assertIn("a cat transformed into a pizza", p["slots"]["subject_description"])
        self.assertNotIn("banana", p["sheet_prompt"])
        self.assertEqual(p["slots"]["key_colour"], "green")

    def test_a_green_target_gets_a_blue_screen(self):
        self.assertEqual(self.plan("a dog as an avocado")["slots"]["key_colour"], "blue")

    def test_the_lint_catches_a_broken_plan(self):
        p = self.plan("dog as banana")
        tpl = base.Transformation()
        self.assertEqual(tpl.validate(p["slots"]), [])
        broken = {**p["slots"], "cells": [c for c in p["slots"]["cells"] if "squash" not in c["tags"]]}
        self.assertTrue(any("squash" in x for x in tpl.validate(broken)))
        self.assertTrue(any("need 9 cells" in x for x in tpl.validate(broken)))
        lost = {**p["slots"], "subject_description": "a dog and a banana"}
        self.assertTrue(any("character sentence" in x for x in tpl.validate(lost)))
        ruled = {**p["slots"], "transformation": {**p["slots"]["transformation"], "forbidden": ["shock"]}}
        self.assertTrue(any("ruled out" in x for x in tpl.validate(ruled)))

    def test_the_planner_stays_deterministic_in_the_request(self):
        a, b = self.plan("dog as banana"), self.plan("dog as banana")
        self.assertEqual(a["slots"], b["slots"])
        self.assertNotEqual([c["label"] for c in self.plan("cat as banana")["slots"]["cells"]][3:], [c["label"] for c in a["slots"]["cells"]][3:] + ["x"])


class IntegrationTests(unittest.TestCase):
    def test_expander_uses_the_template_with_or_without_the_ai(self):
        def never_asked(*a, **k):
            raise AssertionError("a transformation is built by its template: the model must not be asked")
        for ai in (False, True):
            p = expander.expand("dog as banana", (3, 3), use_ai=ai, complete=never_asked)
            self.assertEqual((p["expanded_by"], p["slots"]["transformation"]["target"]), ("transformation", "banana"), ai)
            self.assertNotIn("expand_error", p)

    def test_not_a_transformation_keeps_the_normal_plan(self):
        for text in ("dog with bananas", "a dog as a pilot", "happy teddy bear"):
            p = expander.expand(text, (3, 3))
            self.assertEqual(p["expanded_by"], "deterministic", text)
            self.assertNotIn("transformation", p["slots"], text)

    def test_the_inbox_preview_and_reservation_carry_it(self):
        import shutil
        import tempfile
        from pathlib import Path
        pv = tasks.preview("dog as banana, no dancing")
        self.assertEqual(pv["slots"]["transformation"]["forbidden"], ["danc"])
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "in" / "Images_gen").mkdir(parents=True)
            (tmp / "in" / "videos_gen").mkdir(parents=True)
            t = tasks.reserve(tmp / "out", tmp / "in", "dog as banana")
            saved = tasks.read_task(tmp / "out", t["id"])
            self.assertEqual(saved["plan"]["slots"]["transformation"]["id"], "subject_as_target")      # kept with the task, so with the generation (slots)
            self.assertEqual(saved["plan"]["task_slug"], "dog_as_banana")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ChatTests(unittest.TestCase):
    def test_the_chat_says_it_is_a_transformation_and_the_card_carries_it(self):
        from tests.test_agent import Base

        class T(Base):
            def runTest(self):
                self.tools.plan = lambda prompt, grid, style_id, ai: tasks.preview(prompt, grid, style_id, ai)
                m = self.say("make a dog as a banana, no dancing")
                card = m["cards"][0]
                assert card["type"] == "plan" and card["subject"] == "dog as banana", card
                assert card["transformation"]["target"] == "banana" and card["transformation"]["required"] == ["shock", "squash"], card
                notes = [x for x in m["steps"] if x["kind"] == "note"]
                assert any("a transformation" in x["label"] and "left out as you asked: danc" in x["label"] for x in notes), notes
        t = T()
        t.setUp()
        try:
            t.runTest()
        finally:
            t.tearDown()

    def test_the_planner_cache_key_follows_the_template_versions(self):
        sig = T.registry.signature()
        self.assertIn("subject_as_target:1", sig)
        self.assertIn("banana:1", sig)


if __name__ == "__main__":
    unittest.main()
