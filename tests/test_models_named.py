"""A chat message that names a model uses it, on FakeTools."""
import unittest

from stickerloom.generation.model_catalog import named_in
from tests.test_agent import Base


class NamedInTests(unittest.TestCase):
    def test_the_message_names_a_model_after_with_using_in(self):
        self.assertEqual(named_in("animate with grok"), {"video": "grok_video_v15"})
        self.assertEqual(named_in("animate it using grok lite"), {"video": "grok_video_v15_lite"})
        self.assertEqual(named_in("make it with nano banana pro and animate with kling"), {"image": "nano_banana_pro", "video": "kling3_0"})
        self.assertEqual(named_in("draw in gpt image 2.5"), {"image": "gpt_image_2_5"})
        self.assertEqual(named_in("a grok themed sticker"), {}, "a bare word is not a pick")
        self.assertEqual(named_in(""), {})


class ChatPickTests(Base):
    def test_animate_with_grok_saves_the_pick_and_says_it(self):
        m = self.say("make me falcon stickers with seedream lite")
        self.assertEqual(self.sess()["settings"]["models"]["image"], "seedream_v5_lite")
        self.assertIn("Seedream 5.0 Lite for the stickers", m["text"])
        self.assertEqual(self.tools.models.get("image"), "seedream_v5_lite", "this turn's tools use it at once")


if __name__ == "__main__":
    unittest.main()
