"""Several subjects in one request, and feedback about a whole subject turned into a change of its prompt: the sentences of the person's own examples."""
import unittest

from stickerloom.agent import refine, subjects


class SubjectsTests(unittest.TestCase):
    def test_a_count_and_a_category_is_one_request_for_several_subjects(self):
        for text, want in (("create three sticker packs of fruits", (3, "fruits")), ("Make 3 packs of animals", (3, "animals")), ("can you make me five sets of desserts?", (5, "desserts")),
                           ("give me two fruit packs", (2, "fruit")), ("create 9 sticker packs of birds", (6, "birds")), ("make three sticker sets with foods please", (3, "foods please"))):
            got = subjects.parse_multi(text)
            self.assertEqual(got and got[0], want[0], text)
        for not_multi in ("make me a falcon", "create a sticker pack of fruits", "make one pack of dogs", "three little pigs", "make me 4 falcon stickers", "create a pack"):
            self.assertIsNone(subjects.parse_multi(not_multi), not_multi)

    def test_pick_is_random_varied_and_never_repeats_what_the_chat_made(self):
        a, how = subjects.pick("fruits", 3, seed=1)
        b, _ = subjects.pick("fruits", 3, seed=2)
        self.assertEqual((len(a), how), (3, "table"))
        self.assertEqual(len(set(a)), 3)
        self.assertNotEqual(a, b, "a different seed, a different draw")
        self.assertEqual(subjects.pick("fruits", 3, seed=1)[0], a, "the same seed reproduces it")
        c, _ = subjects.pick("fruits", 6, avoid=["strawberry", "banana", "cherry"], seed=3)
        self.assertFalse({"strawberry", "banana", "cherries"} & set(c), "strawberry / banana / cherry (singular or plural) were already made")

    def test_the_model_chooses_when_there_is_one_and_its_answer_is_cleaned(self):
        ask = lambda cat, n, avoid: ["Strawberry", "strawberries", "a sticker of a kiwi", "dragon fruit", "banana!!", "four five six seven", "mango"]
        got, how = subjects.pick("fruits", 3, ask=ask)
        self.assertEqual((got, how), (["strawberry", "dragon fruit", "banana"], "model"))
        short, how = subjects.pick("fruits", 3, ask=lambda *a: ["kiwi"], seed=5)
        self.assertEqual((short[0], len(short), how), ("kiwi", 3, "model+table"), "what the model did not fill comes from the table")
        self.assertEqual(subjects.pick("quantum widgets", 3), ([], "none"), "an unknown category with no model: the caller asks the person")

    def test_categories_are_found_by_their_plural_or_alias(self):
        self.assertEqual([subjects.category_of(x) for x in ("fruits", "fruit", "animals", "sweets", "veggies", "deserts", "sea creatures", "birds")],
                         ["fruit", "fruit", "animal", "dessert", "vegetable", "desert", "sea creature", "bird"])
        self.assertIsNone(subjects.category_of("quantum widgets"))


class RefineTests(unittest.TestCase):
    def test_the_persons_own_example_is_two_changes_for_two_subjects(self):
        said = "the cherries was so realistic make them more cartoonish the banana was so small make them bigger"
        segs = refine.mentions(said, ["strawberry", "cherries", "banana"])
        self.assertEqual([n for n, _ in segs], ["cherries", "banana"])
        cherries, banana = (refine.extract(c) for _, c in segs)
        self.assertEqual((cherries["style_id"], cherries["size"]), ("toon_shade", None))
        self.assertEqual((banana["size"], banana["style_id"]), ("larger", None))
        self.assertEqual(refine.describe(cherries), ["cartoonish style"])
        self.assertEqual(refine.describe(banana), ["bigger in its cell"])

    def test_a_complaint_moves_away_and_a_wish_moves_toward(self):
        self.assertEqual(refine.extract("the cherries are so realistic")["style_id"], "toon_shade")
        self.assertEqual(refine.extract("make them more realistic")["style_id"], "realistic")
        self.assertEqual(refine.extract("too cartoonish, make it lifelike")["style_id"], "realistic")
        self.assertEqual(refine.extract("less realistic")["style_id"], "toon_shade")
        self.assertEqual(refine.extract("make them clay")["style_id"], "clay_3d")
        self.assertEqual(refine.extract("in a watercolour style")["style_id"], "watercolor")
        self.assertEqual(refine.extract("too big")["size"], "smaller")
        self.assertEqual(refine.extract("make it smaller")["size"], "smaller")
        self.assertEqual(refine.extract("so tiny")["size"], "larger")
        self.assertEqual(refine.extract("make them more red")["colour"], "red")
        self.assertFalse(refine.extract("banana with a hat")["matched"], "an extra word alone is not something to act on")
        self.assertEqual(refine.extract("banana with a hat")["notes"], ["with a hat"])

    def test_apply_writes_the_change_into_a_copy_of_the_old_prompt(self):
        plan = {"template_id": "sheet_3x3", "template_version": 3, "subject": "cherries", "slots": {"subject_description": "cherries", "style_id": "realistic", "cells": [{"pos": 1, "label": "waving"}]},
                "stickers": [{"index": 1, "key": "cherries_waving"}], "sheet_prompt": "OLD TEXT"}
        d1 = refine.extract("so realistic make them more cartoonish and bigger")
        new = refine.apply(plan, d1, "so realistic make them more cartoonish and bigger", "cherries")
        self.assertEqual(new["slots"]["style_id"], "toon_shade")
        self.assertIn("fills most of its cell", new["slots"]["subject_description"])
        self.assertTrue(new["slots"]["subject_description"].startswith("cherries"), "the old description is still the start of the new one")
        self.assertNotIn("sheet_prompt", new, "the prompt is rebuilt from the changed slots")
        self.assertEqual(new["slots"]["cells"], plan["slots"]["cells"], "the cells and tags are the old ones")
        self.assertEqual(plan["slots"]["style_id"], "realistic", "the stored plan is not touched")
        self.assertEqual(new["refinements"][0]["delta"]["style_id"], "toon_shade")
        again = refine.apply(new, refine.extract("make them smaller"), "make them smaller")
        self.assertIn("plenty of empty space", again["slots"]["subject_description"])
        self.assertNotIn("fills most", again["slots"]["subject_description"], "a size clause is replaced, never stacked")
        self.assertEqual(len(again["refinements"]), 2, "the history of changes is kept on the plan")


if __name__ == "__main__":
    unittest.main()
