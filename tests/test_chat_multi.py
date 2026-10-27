"""Several subjects in one request, feedback on a whole subject, and what the chat learns about the person (the fruit-packs conversation). FakeTools, no provider."""
import unittest

from stickerloom.agent import refine, subjects
from stickerloom.agent.profile import Profile
from tests.test_agent import Base


class Multi(Base):
    def test_three_packs_of_fruits_is_one_plan_one_price_and_three_jobs_on_go(self):
        m = self.say("create three sticker packs of fruits")
        card = m["cards"][0]
        self.assertEqual(card["type"], "multi")
        self.assertEqual(len(card["items"]), 3)
        self.assertEqual(len({i["subject"] for i in card["items"]}), 3, "three different fruits")
        self.assertEqual(card["estimate"], 6.0, "ONE total price: 3 x 2.0")
        self.assertEqual([c for c in self.tools.calls if c[0] == "create"], [], "nothing is spent before the go-ahead")
        self.say("yes")
        self.assertEqual(len([c for c in self.tools.calls if c[0] == "create"]), 3)
        self.assertEqual(len(self.tools.sent_plans), 3, "each batch runs the plan the card showed")
        self.assertEqual(len(self.sess()["subjects"]), 3)
        self.assertIsNone(self.sess()["pending"])

    def test_a_refused_start_keeps_only_what_did_not_start(self):
        self.say("make me 3 packs of fruits")
        orig = self.tools.create
        n = {"i": 0}

        def flaky(*a, **k):
            n["i"] += 1
            if n["i"] == 2:
                self.tools.fail_next_create = True
            return orig(*a, **k)
        self.tools.create = flaky
        self.say("yes")
        pend = self.sess()["pending"]
        self.assertIsNotNone(pend)
        self.assertEqual(len(pend["items"]), 1, "the one that failed waits; the two that started are not asked again")

    def test_an_unknown_category_asks_for_the_subjects(self):
        m = self.say("create three sticker packs of zorbs")
        self.assertIn("zorbs", m["text"])
        self.assertIsNone(self.sess()["pending"])

    def test_parse_and_pick(self):
        self.assertEqual(subjects.parse_multi("create three sticker packs of fruits"), (3, "fruits"))
        self.assertEqual(subjects.parse_multi("make 4 fruit packs"), (4, "fruit"))
        self.assertIsNone(subjects.parse_multi("make me a falcon pack"))
        names, how = subjects.pick("fruits", 3, avoid=["banana"], seed=1)
        self.assertEqual((len(names), how), (3, "table"))
        self.assertNotIn("banana", names)


class Refine(Base):
    def _two(self):
        self.seed("G001", subject="cherries")
        self.seed("G002", subject="banana")
        self.tools.plans["G001"] = {"template_id": "t", "template_version": 1, "subject": "cherries", "slots": {"subject_description": "cherries", "style_id": "realistic"},
                                     "stickers": [{"index": i, "key": f"c{i}", "emoji": ["🍒"]} for i in range(1, 10)]}
        self.tools.plans["G002"] = {"template_id": "t", "template_version": 1, "subject": "banana", "slots": {"subject_description": "banana", "style_id": "flat_vector"},
                                     "stickers": [{"index": i, "key": f"b{i}", "emoji": ["🍌"]} for i in range(1, 10)]}

    def test_per_subject_feedback_changes_the_prompt_of_each_subject(self):
        self._two()
        m = self.say("the cherries was so realistic make them more cartoonish the banana was so small make them bigger")
        card = m["cards"][0]
        self.assertEqual(card["type"], "multi")
        by = {i["subject"]: i for i in card["items"]}
        self.assertEqual(set(by), {"cherries", "banana"})
        self.assertTrue(any("cartoon" in c for c in by["cherries"]["changes"]))
        self.assertTrue(any("bigger" in c for c in by["banana"]["changes"]))
        self.say("yes")
        sent = {p["subject"]: p for p in self.tools.sent_plans}
        self.assertEqual(sent["cherries"]["slots"]["style_id"], "toon_shade")
        self.assertIn("fills most of its cell", sent["banana"]["slots"]["subject_description"])
        self.assertEqual(sent["banana"]["slots"]["style_id"], "flat_vector", "the banana keeps its style")
        creates = [c for c in self.tools.calls if c[0] == "create"]
        self.assertEqual({c[3] for c in creates}, {"G001", "G002"}, "each new sheet is a child of the batch it changes")

    def test_a_new_request_with_a_style_is_not_a_refinement(self):
        self._two()
        m = self.say("make me a banana in clay style")
        self.assertEqual(m["cards"][0]["type"], "plan")

    def test_refine_is_pure_and_replaces_instead_of_stacking(self):
        base = {"slots": {"subject_description": "banana", "style_id": "flat_vector"}, "sheet_prompt": "x"}
        once = refine.apply(base, {"size": "larger"}, "bigger")
        twice = refine.apply(once, {"size": "smaller"}, "smaller")
        self.assertEqual((twice["slots"]["subject_description"].count("small"), "fills" in twice["slots"]["subject_description"]), (1, False))
        self.assertNotIn("sheet_prompt", twice)
        self.assertEqual(len(twice["refinements"]), 2)
        self.assertEqual(base["slots"]["subject_description"], "banana", "the stored plan is a copy")


class Taste(Base):
    def test_a_taste_needs_two_signals_and_is_said_on_the_card(self):
        p = Profile(self.out)
        p.vote("style", "toon_shade")
        self.assertIsNone(p.defaults()["style_id"])
        p.vote("style", "toon_shade")
        self.assertEqual(p.defaults()["style_id"], ("toon_shade", 2))
        m = self.say("create three sticker packs of animals")
        self.assertEqual(len({i["style"] for i in m["cards"][0]["items"]}), 1)
        self.assertEqual(self.sess()["pending"]["items"][0]["style_id"], "flat_vector")             # offered, never applied unasked
        self.assertTrue(m["cards"][0]["assumed"], "the card says how to use the remembered style")

    def test_a_style_in_the_sentence_beats_the_taste(self):
        p = Profile(self.out)
        p.vote("style", "toon_shade"); p.vote("style", "toon_shade")
        self.say("make me a falcon in pixel style")
        self.assertEqual(self.sess()["pending"]["plan"]["slots"]["style_id"], "pixel_art")

    def test_a_split_taste_is_no_taste(self):
        p = Profile(self.out)
        for k in ("toon_shade", "toon_shade", "realistic", "realistic"):
            p.vote("style", k)
        self.assertIsNone(p.defaults()["style_id"])


class Effects(Base):
    def test_particle_effects_for_a_named_pack_opens_an_effect_and_spends_nothing(self):
        self.tools.pack_list = [{"id": "P1", "name": "Superhero", "count": 8}, {"id": "P2", "name": "Cats", "count": 5}]
        m = self.say("make particle effects for my Superhero pack")
        card = m["cards"][0]
        self.assertEqual((card["type"], card["kind"], card["pack"], card["pack_id"]), ("particles_scope", "video", "Superhero", "P1"))
        self.assertEqual([c[:2] for c in self.tools.calls if c[0] == "effects_start"], [])
        self.assertEqual([c for c in self.tools.calls if c[0] == "create"], [], "no sheet, no spend")
        self.assertIsNone(self.sess()["pending"])

    def test_the_only_pack_is_used_and_an_unclear_one_is_asked(self):
        self.tools.pack_list = [{"id": "P1", "name": "Superhero", "count": 8}]
        self.assertEqual(self.say("add a burst effect to my stickers")["cards"][0]["pack"], "Superhero")
        self.setUp()
        self.tools.pack_list = [{"id": "P1", "name": "Superhero", "count": 8}, {"id": "P2", "name": "Cats", "count": 5}]
        m = self.say("I want a particle burst for my emoji")
        self.assertFalse(m.get("cards"))
        self.assertIn("Which pack", m["text"])
        self.assertEqual([c for c in self.tools.calls if c[0] == "effects_start"], [])

    def test_no_pack_says_what_to_do_first(self):
        m = self.say("make particle effects")
        self.assertIn("pack", m["text"].lower())

    def test_a_confetti_pack_request_is_still_a_normal_request(self):
        m = self.say("make me a pack of party hats")
        self.assertEqual(m["cards"][0]["type"], "plan")


class StyleWords(unittest.TestCase):
    def test_every_style_the_chat_can_emit_is_a_real_preset(self):
        from stickerloom.agent import resolver
        from stickerloom.generation import styles
        ids = {p["id"] for p in styles.PRESETS}
        self.assertLessEqual(set(resolver.STYLE_WORDS.values()), ids)
        self.assertLessEqual({sid for _, sid in refine.STYLE_WORDS}, ids)
        self.assertLessEqual(set(refine.OPPOSITE) | set(refine.OPPOSITE.values()), ids)

    def test_a_style_in_a_new_request_is_taken_out_of_the_subject(self):
        self.assertEqual(refine.style_of_request("a teddy bear in clay style"), ("clay_3d", "a teddy bear"))
        self.assertEqual(refine.style_of_request("a falcon")[0], None)


if __name__ == "__main__":
    unittest.main()
