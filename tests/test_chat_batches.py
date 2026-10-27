"""A pack request starts at batch 01, and ONE follow-up offers Regenerate · Batch 02 · 03 · 04 (FakeTools: no provider, no paid call)."""
import unittest

from stickerloom.agent.resolver import batch_intent, classify
from tests.test_agent import Base, gen


class Batches(Base):
    def creates(self):
        return [c for c in self.tools.calls if c[0] == "create"]

    def start_batch01(self, text="make me falcon stickers"):
        m = self.say(text)
        self.say("", action={"type": "confirm"})
        return m

    def arrive(self, job, gid):
        self.tools.job_generations[job] = gid
        self.tools.gens[gid] = gen(gid)

    def last(self):
        return self.sess()["messages"][-1]

    def test_a_pack_request_plans_batch_01_only(self):
        m = self.say("make me falcon stickers")
        self.assertEqual(m["cards"][0]["batch"]["text"], "Batch 01 of 04 · actions 1-9")
        self.assertEqual(self.sess()["pending"]["batch"]["no"], 1)
        self.say("", action={"type": "confirm"})
        self.assertEqual(len(self.creates()), 1, "one sheet: batch 01")
        made = self.sess()["pack"]["made"]
        self.assertEqual([(r["no"], r["job"]) for r in made], [(1, "J001")])

    def test_an_emoji_pack_names_its_grids(self):
        m = self.say("make generic emojis")
        self.assertEqual(m["cards"][0]["batch"]["text"], "Batch 01 of 04 · core")
        self.say("", action={"type": "confirm"})
        self.arrive("J001", "G050")
        m = self.say("", action={"type": "batch_more", "to": 2})
        self.assertEqual(m["cards"][0]["batches"], ["Batch 02 · social"])

    def test_the_follow_up_comes_once_when_the_batch_is_cut(self):
        self.start_batch01()
        self.assertFalse(self.agent.batch_followup(self.sid), "the sheet is still being drawn")
        self.arrive("J001", "G050")
        self.assertTrue(self.agent.batch_followup(self.sid))
        m = self.last()
        self.assertEqual([c["label"] for c in m["chips"]], ["Regenerate · 2 credits", "Batch 02 · 2 credits", "Batch 03 · 4 credits", "Batch 04 · 6 credits"])
        self.assertEqual([c.get("to") for c in m["chips"]], [None, 2, 3, 4])
        self.assertEqual(m["chips"][0]["generation"], "G050")
        self.assertIn("batch 01 of 04", m["text"])
        self.assertFalse(self.agent.batch_followup(self.sid), "once, however often the chat is polled")

    def test_batch_03_with_one_made_makes_02_and_03_after_one_go_ahead(self):
        self.start_batch01()
        self.arrive("J001", "G050")
        m = self.say("", action={"type": "batch_more", "to": 3})
        self.assertEqual(self.sess()["messages"][-2]["text"], "Batch 03", "the button's label is the person's message")
        p = self.sess()["pending"]
        self.assertEqual((p["type"], len(p["items"]), p["estimate"]), ("batch_more", 2, 4.0))
        self.assertEqual(m["cards"][0]["batches"], ["Batch 02 · actions 10-18", "Batch 03 · actions 19-27"])
        self.assertEqual(len(self.creates()), 1, "nothing spends before the go-ahead")
        m2 = self.say("", action={"type": "confirm"})
        self.assertEqual(len(self.creates()), 3, "two sheets, one go-ahead")
        self.assertEqual([c.get("batch") for c in m2["cards"]], ["Batch 02 · actions 10-18", "Batch 03 · actions 19-27"])
        self.assertEqual([r["no"] for r in self.sess()["pack"]["made"]], [1, 2, 3])
        self.arrive("J002", "G051")
        self.assertFalse(self.agent.batch_followup(self.sid), "the follow-up waits for every batch just started")
        self.arrive("J003", "G052")
        self.assertTrue(self.agent.batch_followup(self.sid))
        self.assertEqual([c["label"] for c in self.last()["chips"]], ["Regenerate · 2 credits", "Batch 04 · 2 credits"])

    def test_never_a_fifth_batch(self):
        self.start_batch01()
        self.arrive("J001", "G050")
        m = self.say("make batch 5")
        self.assertIn("4 batches at most", m["text"])
        self.say("", action={"type": "batch_more", "to": 4})
        self.say("", action={"type": "confirm"})
        self.assertEqual([r["no"] for r in self.sess()["pack"]["made"]], [1, 2, 3, 4])
        for j, g in (("J002", "G051"), ("J003", "G052"), ("J004", "G053")):
            self.arrive(j, g)
        self.agent.batch_followup(self.sid)
        self.assertEqual([c["action"] for c in self.last()["chips"]], ["regenerate"], "all four made: Regenerate only")
        n = len(self.creates())
        m = self.say("next batch")
        self.assertEqual(len(self.creates()), n)
        self.assertIsNone(self.sess()["pending"])

    def test_regenerate_is_a_new_generation_of_the_batch(self):
        self.start_batch01()
        self.arrive("J001", "G050")
        m = self.say("", action={"type": "regenerate", "generation": "G050"})
        self.assertEqual(m["chips"][0]["label"], "Create it · 2 credits")
        self.say("", action={"type": "confirm"})
        c = self.creates()[-1]
        self.assertEqual((c[3], c[4]), ("G050", "G050"), "parent + regen_of: the batch's family, as the Studio's Regenerate")
        self.assertEqual([(r["no"], r["job"]) for r in self.sess()["pack"]["made"]], [(1, "J002")], "the new take is batch 01")

    def test_a_transformation_gets_regenerate_only(self):
        self.tools.transform = True
        m = self.say("make me my dog as a banana")
        self.assertNotIn("batch", m["cards"][0])
        self.say("", action={"type": "confirm"})
        self.arrive("J001", "G050")
        self.agent.batch_followup(self.sid)
        self.assertEqual([c["action"] for c in self.last()["chips"]], ["regenerate"])
        m = self.say("next batch")
        self.assertIn("one character", m["text"])

    def test_typed_forms(self):
        self.start_batch01()
        self.arrive("J001", "G050")
        self.say("next batch")
        self.assertEqual([i["batch"]["no"] for i in self.sess()["pending"]["items"]], [2])
        self.say("make the rest of the pack")
        self.assertEqual([i["batch"]["no"] for i in self.sess()["pending"]["items"]], [2, 3, 4])
        self.say("batch 3")
        self.assertEqual([i["batch"]["no"] for i in self.sess()["pending"]["items"]], [2, 3])
        self.say("regenerate it")
        self.assertEqual(self.sess()["pending"]["items"][0]["regen_of"], "G050")

    def test_prompt_stage_offers_batches_with_the_plan_and_spends_nothing(self):
        s = self.sess()
        s["settings"]["stage"] = "prompt"
        self.store.save(s)
        m = self.say("make me falcon stickers")
        self.assertEqual([c.get("to") for c in m["chips"] if c.get("action") == "batch_more"], [2, 3, 4])
        m = self.say("", action={"type": "batch_more", "to": 3})
        p = self.sess()["pending"]
        self.assertEqual([i["batch"]["no"] for i in p["items"]], [1, 2, 3], "nothing made yet: batch 01 goes with them")
        self.assertTrue(m["chips"][0]["label"].startswith("Generate"))
        self.assertFalse(self.creates())
        self.say("", action={"type": "confirm"})
        self.assertEqual(len(self.creates()), 3)

    def test_animation_stage_runs_the_batches_one_creator_run_after_the_other(self):
        self.start_batch01()
        self.arrive("J001", "G050")
        s = self.sess()
        s["settings"]["stage"] = "animation"
        self.store.save(s)
        m = self.say("", action={"type": "batch_more", "to": 3})
        self.assertEqual(self.sess()["pending"]["estimate"], 20.0, "sheet + animation per batch, one total")
        self.say("", action={"type": "confirm"})
        s = self.sess()
        self.assertEqual((s["creator_run"]["end"], len(s["creator_queue"])), ("animation", 1))
        self.say("", action={"type": "creator_stop"})
        self.assertNotIn("creator_queue", self.sess(), "Stop also drops the queued batches")

    def test_later_batch_pack_names_survive_telegram_truncation(self):
        from stickerloom.services.telegram import set_name
        self.start_batch01()
        self.arrive("J001", "G050")
        s = self.sess()
        s["settings"]["stage"] = "export"
        s["pack"]["subject"] = "falcon " + "expressive " * 12
        self.store.save(s)
        self.say("", action={"type": "batch_more", "to": 3})
        self.say("", action={"type": "confirm"})
        s = self.sess()
        first = s["creator_run"]["pack_name"]
        s["creator_run"].update(status="done", step="done", said="done:done")
        self.store.save(s)
        self.agent.creator_tick(self.sid)
        second = self.sess()["creator_run"]["pack_name"]
        names = [set_name(title, "", "stickerloombot") for title in (first, second)]
        self.assertEqual([len(name) for name in names], [64, 64])
        self.assertNotEqual(*names, "later batch names must stay distinct after Telegram truncates them")


class Typed(unittest.TestCase):
    def test_batch_intent(self):
        self.assertEqual(batch_intent("next batch"), {"type": "batch_more", "to": None})
        self.assertEqual(batch_intent("make batch 03"), {"type": "batch_more", "to": 3})
        self.assertEqual(batch_intent("Batch 3 please"), {"type": "batch_more", "to": 3})
        self.assertEqual(batch_intent("make the rest of the pack"), {"type": "batch_more", "to": "rest"})
        self.assertEqual(batch_intent("regenerate it"), {"type": "regenerate"})
        self.assertEqual(batch_intent("regenerate"), {"type": "regenerate"})
        for no in ("regenerate 2 and 5", "redo it as a banana", "redo 3", "make me falcon stickers", "another batch", "batch of cats"):
            self.assertIsNone(batch_intent(no), no)

    def test_classify(self):
        self.assertEqual(classify("next batch", False, True)[0], ["BATCH_MORE"])
        self.assertEqual(classify("make the rest of the pack", False, True)[0], ["BATCH_MORE"])
        self.assertEqual(classify("regenerate it", False, True)[0], ["REGENERATE"])
        self.assertNotEqual(classify("regenerate 2 and 5", False, True)[0], ["REGENERATE"])


if __name__ == "__main__":
    unittest.main()
