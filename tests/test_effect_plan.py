"""The smart step of a particle effect: pieces, grouping, moods, consent and the table fallback. The model is a fake that records what it was given; nothing here reaches a real one."""
import json
import unittest

from stickerloom.runtime import cache as cachemod
from stickerloom.vision import effect_plan as ep
from stickerloom.vision.judge import VisionJudge

def _png(n=0):
    import io
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGBA", (32, 32), (200 - n * 40, 40 + n * 30, 40, 255)).save(b, "PNG")
    return b.getvalue()


PNG = _png()


def fake(answers):
    seen = []

    def complete(system, user, images=None, **kw):
        seen.append({"system": system, "user": user, "images": len(images or [])})
        return (answers.pop(0) if answers else '{"subject":"cat","elements":["cat paws"]}'), {"model": "fake-vlm", "provider": "local"}
    complete.seen = seen
    return complete


def judge(complete):
    return VisionJudge(complete=complete, cache=cachemod.Cache(force_memory=True) if hasattr(cachemod, "Cache") else None, pol="FAIL_CLOSED")


class LexiconTests(unittest.TestCase):
    def test_the_table_answers_by_emoji_then_by_name_then_generically(self):
        self.assertEqual(ep.lexicon_plan("x", "🍓")["subject"], "strawberry")
        self.assertIn("gold bars", ep.lexicon_plan("jewelry shop", "")["elements"] + ep.lexicon_plan("", "💍")["elements"])
        self.assertEqual(ep.lexicon_plan("Batman waving", "")["subject"], "bat")
        g = ep.lexicon_plan("whatever thing", "")
        self.assertEqual((g["by"], g["elements"]), ("generic", ep.GENERIC))

    def test_every_table_entry_passes_the_lint_of_the_prompt_template(self):
        from stickerloom.generation import effect_prompts as p
        for e, (subject, pieces) in ep.LEXICON.items():
            p.lint_plan({"subject": subject, "elements": pieces})


class AnalyseTests(unittest.TestCase):
    def stickers(self):
        return [{"id": "a", "name": "Superhero flying", "emoji": ["🦸"], "png": _png(1)}, {"id": "b", "name": "Superhero sad", "emoji": ["🦸"], "png": _png(2)},
                {"id": "c", "name": "Jewelry shop", "emoji": ["💍"], "png": _png(3)}]

    def test_without_consent_the_table_answers_and_says_so_and_no_picture_is_sent(self):
        c = fake([])
        r = ep.analyse(self.stickers(), vlm=judge(c), allowed=None)
        self.assertEqual(c.seen, [])
        self.assertTrue(any("not allowed" in n for n in r["notes"]))
        self.assertEqual({g["subject"] for g in r["groups"]}, {"superhero", "jewelry"})

    def test_the_model_chooses_the_pieces_and_a_pack_of_one_subject_is_one_group(self):
        c = fake(['{"subject":"Superhero","elements":["red cape pieces","shield badges"],"mood":"happy"}',
                  'sure: {"subject":"superhero","elements":["cape pieces","small stars"],"mood":"sad"}',
                  '{"subject":"jewelry","elements":["small gold bars","small diamonds"],"mood":"excited"}'])
        r = ep.analyse(self.stickers(), vlm=judge(c), allowed=True)
        self.assertEqual(len(c.seen), 3)
        self.assertTrue(all(s["images"] == 1 for s in c.seen))
        self.assertEqual(len(r["groups"]), 2, "the two Superhero stickers share one group")
        sup = next(g for g in r["groups"] if "uperman" in g["subject"])
        self.assertEqual(sup["stickers"], ["a", "b"])
        self.assertEqual((sup["preset"]["a"], sup["preset"]["b"]), ("burst", "rain"), "a sad one rains, a happy one bursts")
        self.assertIn("red cape pieces", sup["elements"])
        self.assertEqual(sup["by"], "vlm")
        self.assertEqual(r["model"], "fake-vlm")

    def test_a_nonsense_answer_falls_back_to_the_table_for_that_sticker_only(self):
        c = fake(["I cannot do that", "still not json", '{"subject":"cat","elements":["a sticker of a cat"]}', "no", '{"subject":"jewelry","elements":["gold bars"]}', "x"])
        r = ep.analyse(self.stickers(), vlm=judge(c), allowed=True)
        self.assertTrue(any("could not read" in n for n in r["notes"]))
        self.assertEqual(sum(1 for p in r["per_sticker"].values() if p["by"] == "lexicon"), 2)
        self.assertEqual(r["per_sticker"]["c"]["by"], "vlm")

    def test_a_picture_that_does_not_open_gets_the_tables_answer_not_a_crash(self):
        c = fake([])
        r = ep.analyse([{"id": "z", "name": "Cat", "emoji": ["🐱"], "png": b"not a png"}], vlm=judge(c), allowed=True)
        self.assertEqual(r["groups"][0]["subject"], "cat")
        self.assertTrue(any("could not read" in n for n in r["notes"]))

    def test_a_note_that_names_pieces_replaces_them_a_sentence_about_anything_else_does_not(self):
        r = ep.analyse(self.stickers()[2:], allowed=None, note="pieces: gold bars, diamonds and coins")
        self.assertEqual(r["groups"][0]["elements"], ["gold bars", "diamonds", "coins"])
        self.assertEqual(r["groups"][0]["by"], "you")
        r2 = ep.analyse(self.stickers()[2:], allowed=None, note="make it nice please")
        self.assertNotEqual(r2["groups"][0]["by"], "you")

    def test_the_key_colour_follows_the_pieces(self):
        r = ep.analyse([{"id": "s", "name": "x", "emoji": ["🍓"], "png": None}], allowed=None)
        self.assertEqual(r["groups"][0]["key"], "blue", "strawberry leaves are green: the screen is blue")
        r = ep.analyse([{"id": "s", "name": "x", "emoji": ["❤"], "png": None}], allowed=None)
        self.assertEqual(r["groups"][0]["key"], "green")


if __name__ == "__main__":
    unittest.main()
