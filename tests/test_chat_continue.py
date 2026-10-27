"""Step 0 of plan.md: video/export words continue the open batch (never a new subject), and names/describe with nothing cut stays calm without a vision call."""
import unittest

from tests.test_agent import Base, gen


class Continue(Base):
    def seed_status(self, gid="G012", status="READY", anim=()):
        g = gen(gid)
        for s in g["stickers"]:
            s["status"] = status
            if s["index"] in anim:
                s["anim_status"] = "READY"
        self.tools.gens[gid] = g
        s = self.sess()
        self.store.add_pass(s, "viking girl", generation=gid, prompt="girl viking", grid="3x3", style_id="kawaii")
        s["focus"] = {"generation": gid, "stickers": []}
        self.store.save(s)

    def test_create_video_and_export_animates_the_open_batch(self):
        self.seed_status()
        m = self.say("create video and export")
        p = self.sess()["pending"]
        self.assertEqual((p["type"], p["generation"]), ("animate", "G012"))
        self.assertFalse([c for c in self.tools.calls if c[0] == "create"], "no new subject is planned")
        self.assertIn("pack", m["text"], "the reply says pack and send follow the animation")
        m2 = self.say("yes")
        self.assertEqual([c for c in self.tools.calls if c[0] == "animate"], [("animate", "G012")])
        self.assertTrue(m2["cards"][0]["animating"])

    def test_export_packs_and_sends_an_animated_batch(self):
        self.seed_status(anim=tuple(range(1, 10)))
        m = self.say("export it")
        self.assertEqual((self.sess()["pending"]["type"], self.sess()["pending"]["generation"]), ("pack_send", "G012"))
        self.assertIn("Pack and send", [c.get("label") for c in m["chips"]])
        m2 = self.say("yes")
        self.assertEqual([c[0] for c in self.tools.calls if c[0] in ("pack_add", "telegram_send")], ["pack_add", "telegram_send"])
        self.assertIn("t.me/addstickers", m2["text"])
        self.assertIsNone(self.sess()["pending"])

    def test_export_animates_first_when_nothing_moves(self):
        self.seed_status()
        m = self.say("export")
        self.assertEqual(self.sess()["pending"]["type"], "animate")
        self.assertIn("first", m["text"])

    def test_names_with_nothing_cut_is_calm_and_calls_no_vision(self):
        self.seed_status(status="PENDING")
        s = self.sess()
        s["pending"] = {"type": "names", "generation": "G012"}
        self.store.save(s)
        m = self.say("yes")
        self.assertIn("no finished stickers yet", m["text"])
        self.assertFalse([c for c in self.tools.calls if c[0] == "name_proposals"], "no vision call with nothing cut")
        self.assertIsNone(self.sess()["settings"]["allow_vlm"], "the setting is untouched, so no ack noise")
        self.assertIsNone(self.sess()["pending"])

    def test_describe_with_nothing_cut_is_calm_and_calls_no_vision(self):
        self.seed_status(status="PENDING")
        s = self.sess()
        s["pending"] = {"type": "describe", "generation": "G012", "only": []}
        self.store.save(s)
        m = self.say("yes")
        self.assertIn("no finished stickers yet", m["text"])
        self.assertFalse([c for c in self.tools.calls if c[0] == "captions"])

    def test_names_with_ready_stickers_still_looks(self):
        self.seed_status()
        s = self.sess()
        s["pending"] = {"type": "names", "generation": "G012"}
        self.store.save(s)
        self.say("yes")
        self.assertTrue([c for c in self.tools.calls if c[0] == "name_proposals"])
        self.assertTrue(self.sess()["settings"]["allow_vlm"])


if __name__ == "__main__":
    unittest.main()
