"""Export-as-ZIP names: `{emoji}-{pack_slug}-{multi_action_tag}-{sNN}-{G###}-{date}`,
e.g. `🤣-falcon-laugh_rofl_lmao-s08-G112-20261008.webm`. Pure functions only: no provider, no Postgres, no files."""
import unittest

from stickerloom.generation import actions
from stickerloom.media import export_names as xn


class BankTests(unittest.TestCase):
    def test_the_bank_has_36_tokens(self):
        self.assertEqual(len(actions.ACTION_BANK), 36)

    def test_the_extended_bank_covers_every_remaining_base_ios_emoji(self):
        self.assertEqual(len(actions.EXTENDED_BANK), 1898)
        for token, glyph in (("school", "🏫"), ("bus", "🚌"), ("statue-of-liberty", "🗽"),
                             ("cherries", "🍒"), ("cherry-blossom", "🌸"), ("cat", "🐈")):
            self.assertEqual(actions.EXTENDED_BANK[token]["emoji"], glyph)

    def test_object_words_resolve_to_ios_emoji_not_unresolved(self):
        self.assertEqual(actions.canonical_for("x", ["school"])[0], "school")
        self.assertEqual(actions.canonical_for("x", ["bus"])[0], "bus")
        self.assertEqual(actions.canonical_for("x", ["statue"])[0], "statue-of-liberty")
        self.assertEqual(actions.canonical_for("x", ["cherries"])[0], "cherries")
        self.assertIsNone(actions.canonical_for("x", ["cherry"]), "ambiguous fruit/flower requires a choice")
        self.assertEqual(actions.canonical_for("x", ["heart"])[0], "love", "shared words keep their core token")

    def test_a_happy_bus_keeps_its_subject_and_its_moods(self):
        self.assertEqual([t for t, _ in actions.canonical_all("bus_happy", ["bus", "happy", "laugh"])],
                         ["bus", "happy", "laugh"])
        self.assertEqual([t for t, _ in actions.canonical_all("eye_roll", ["eyeroll"])], ["eye-roll"],
                         "split-word hits never become extras")

    def test_only_well_established_public_ios_emoji_validate(self):
        for glyph in ("🚌", "🗽", "🍒", "🏫", "👍🏽", "🤣😂"):
            self.assertTrue(actions.is_ios_emoji(glyph), glyph)
        for text in ("", "x", "bus", "gotham"):
            self.assertFalse(actions.is_ios_emoji(text), repr(text))

    def test_alias_lookup_finds_the_canonical_token(self):
        token, aliases = actions.canonical_for("laughing out loud", ["lmao", "rofl"])
        self.assertEqual(token, "laugh")
        self.assertIn("lmao", aliases)
        self.assertEqual(actions.canonical_for("x", ["lmfao"])[0], "laugh")

    def test_underscored_aliases_normalize_to_the_same_token(self):
        self.assertEqual(actions.canonical_for("x", ["thank_you"])[0], "thanks")
        self.assertEqual(actions.canonical_for("x", ["eye_roll"])[0], "eye-roll")
        self.assertEqual(actions.canonical_for("x", ["thumbs_up"])[0], "approve")

    def test_unknown_words_are_unresolved_not_guessed(self):
        self.assertIsNone(actions.canonical_for("batman_lego_gliding", ["gotham"]))
        self.assertEqual(actions.fallback_tag("Laughing Falcon"), "laughing_falcon")
        self.assertEqual(actions.fallback_tag("cat_1", ["cat"]), "cat")
        self.assertEqual(actions.fallback_tag("generic_emojis_grumpy", ["generic_emojis_grumpy", "grumpy", "arms", "crossed"],
                                              exclude=["generic", "emojis"]),
                         "grumpy_arms_crossed")


class NameTests(unittest.TestCase):
    def test_build_matches_the_decided_format(self):
        stem = xn.build(emoji="🤣", slug="falcon", tag="laugh_rofl_lmao", index=8, gid=112, date="20261008")
        self.assertEqual(stem, "🤣-falcon-laugh_rofl_lmao-s08-G112-20261008")

    def test_parse_round_trips_including_hyphenated_slugs(self):
        stem = "👌-royal_falcon-approve_okay_yes_thumbsup-s06-G113-20261009"
        p = xn.parse(stem + ".webm")
        self.assertEqual((p["emoji"], p["slug"], p["tag"], p["index"], p["gid"], p["date"]),
                         ("👌", "royal_falcon", "approve_okay_yes_thumbsup", 6, 113, "20261009"))

    def test_parse_reads_older_hyphenated_slugs_best_effort(self):
        p = xn.parse("👌-royal-falcon-approve_okay-s06-G113-20261009.webm")
        self.assertEqual((p["slug"], p["tag"]), ("royal-falcon", "approve_okay"))

    def test_parse_rejects_garbage(self):
        self.assertIsNone(xn.parse("img-001-cat.png"))
        self.assertIsNone(xn.parse("not-a-name.webm"))

    def test_first_emoji_keeps_modifiers_and_zwj_together(self):
        self.assertEqual(xn.first_emoji("😂🤣"), "😂")
        self.assertEqual(xn.first_emoji("👍🏽ok"), "👍🏽")
        self.assertEqual(xn.first_emoji("❤️‍🔥x"), "❤️‍🔥")
        self.assertEqual(xn.first_emoji(""), "")

    def test_describe_resolves_a_bank_hit_and_flags_a_fallback(self):
        hit = xn.describe(emoji="🤣😂", key="laughing_hard", tags=["lmao"], slug="falcon", index=8, gid=112, date="20261008")
        self.assertEqual((hit["stem"], hit["action"], hit["unresolved"], hit["emojis"]),
                         ("🤣-falcon-laugh_laughing_lol_rofl_lmao_lmfao-s08-G112-20261008", "laugh", False, ["🤣", "😂"]))
        miss = xn.describe(emoji="", key="gotham_1", tags=["gotham"], slug="gotham", index=2, gid=1, date="20261008")
        self.assertEqual((miss["stem"], miss["action"], miss["unresolved"]), ("🙂-gotham-gotham-s02-G001-20261008", "gotham", True))
        cat = xn.describe(emoji="🐱", key="cat_1", tags=["cat"], slug="cat", index=2, gid=1, date="20261008")
        self.assertEqual((cat["stem"], cat["action"], cat["unresolved"]), ("🐱-cat-cat-s02-G001-20261008", "cat", False))
        hyph = xn.describe(emoji="🙄", key="eye_roll", tags=["eyeroll"], slug="falcon", index=3, gid=112, date="20261008")
        self.assertEqual((hyph["tag"], hyph["action"]), ("eyeroll", "eye-roll"))

    def test_describe_keeps_subject_and_moods_in_one_tag(self):
        bus = xn.describe(emoji="🚌", key="bus_happy", tags=["bus", "happy", "laugh"], slug="city", index=3, gid=5, date="20261008")
        self.assertEqual((bus["stem"], bus["action"], bus["unresolved"]),
                         ("🚌-city-bus_happy_smile_joy_laugh_laughing_lol_rofl_lmao_lmfao-s03-G005-20261008", "bus", False))


if __name__ == "__main__":
    unittest.main()
