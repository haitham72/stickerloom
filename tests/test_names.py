"""The one naming convention (runtime/names.py): readable, traceable, non-repeatable, and old names still parse."""
import unittest

from stickerloom.runtime import names


class NamesTests(unittest.TestCase):
    WHEN = 1790948000          # 2026-10-02T13:33:20Z

    def test_a_name_carries_media_subject_action_time_and_a_fingerprint(self):
        n = names.build("img", "Falcon Stickers", "open_arms", when=self.WHEN, seed="G094|k")
        p = names.parse(n)
        self.assertEqual((p["media"], p["subject"], p["action"], p["pack"], p["legacy"]), ("img", "falcon_stickers", "open_arms", None, False))
        self.assertEqual(p["stamp"], "20261002T133320")
        self.assertRegex(p["hash"], r"^[0-9a-f]{6}$")

    def test_the_pack_is_optional_and_parsed_back(self):
        n = names.build("vid", "my dog", "wave", pack="Summer Pack", when=self.WHEN, seed="x")
        p = names.parse(n + ".webm")
        self.assertEqual((p["media"], p["subject"], p["action"], p["pack"]), ("vid", "my_dog", "wave", "summer_pack"))

    def test_same_inputs_same_name_different_seed_different_name(self):
        a = names.build("img", "falcon", "wave", when=self.WHEN, seed="G001|a")
        self.assertEqual(a, names.build("img", "falcon", "wave", when=self.WHEN, seed="G001|a"))
        self.assertNotEqual(a, names.build("img", "falcon", "wave", when=self.WHEN, seed="G002|a"))

    def test_a_still_and_its_animation_share_one_stem(self):
        a = names.build("img", "falcon", "wave", when=self.WHEN, seed="G001|a")
        v = names.build("vid", "falcon", "wave", when=self.WHEN, seed="G001|a")
        self.assertEqual(names.as_media(a, "vid"), v)
        self.assertEqual(names.as_media(v, "img"), a)

    def test_the_subject_words_are_not_repeated_in_the_action(self):
        self.assertEqual(names.action_of("falcon_stickers", "falcon_stickers_open_arms"), "open_arms")
        self.assertEqual(names.action_of("barbie_love", "barbie_blow_kiss"), "blow_kiss")
        self.assertEqual(names.action_of("falcon", "falcon"), "falcon")          # never empty

    def test_names_made_before_the_convention_still_parse(self):
        p = names.parse("img-005-barbie_love-barbie_blow_kiss.png")
        self.assertTrue(p["legacy"])
        self.assertEqual((p["media"], p["subject"], p["action"], p["generation"]), ("img", "barbie_love", "barbie_blow_kiss", 5))
        self.assertEqual(names.readable("img-005-barbie_love-barbie_blow_kiss"), "Barbie blow kiss")

    def test_nonsense_does_not_parse_and_a_bad_media_is_refused(self):
        self.assertIsNone(names.parse("holiday.png"))
        with self.assertRaises(ValueError):
            names.build("gif", "a", "b")

    def test_fields_never_contain_the_separator_and_stay_short(self):
        n = names.build("img", "A very long subject " * 5, "an extremely long action name " * 5, pack="p" * 80, when=self.WHEN)
        self.assertLess(len(n), 140)
        self.assertEqual(len(n.split("-")), 6)


if __name__ == "__main__":
    unittest.main()
