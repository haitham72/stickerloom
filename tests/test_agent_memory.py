"""Durable turns and profiles (docs/architecture.md): checkpoints per turn, safe resume,
focus fallback from the last turn, and the SQLite profile table. Fakes only: no network, no spend."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stickerloom.runtime import cache as cachemod
from stickerloom.agent.brain import Brain
from stickerloom.agent.graph import Agent
from stickerloom.agent.memory import SessionStore
from stickerloom.agent.profile import Profile
from stickerloom.agent.tools import FakeTools


def gen(gid, n=9, keys=None):
    keys = keys or [f"s{i}" for i in range(1, n + 1)]
    return {"generation": gid, "stickers": [{"id": f"{gid}/S{i}", "index": i, "key": keys[i - 1], "name": keys[i - 1], "tags": [keys[i - 1]],
                                             "emoji": ["\U0001F600"], "status": "READY", "still": "PENDING"} for i in range(1, n + 1)]}


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.out = Path(self.td.name)
        self.cache = cachemod.Cache(force_memory=True)
        self.store = SessionStore(self.out, self.cache)
        self.tools = FakeTools()
        self.said = []
        self.agents = []
        self.agent = self.make_agent()
        self.sid = self.store.create()["id"]

    def tearDown(self):
        for a in self.agents:
            try:
                a.close()
            except Exception:
                pass
        self.td.cleanup()

    def make_agent(self):
        a = Agent(self.store, self.tools, Brain(complete=self._llm), self.cache)
        self.agents.append(a)
        return a

    def _llm(self, system, user):
        self.said.append(user)
        return "{}", {"model": "fake"}

    def say(self, text, **kw):
        return self.agent.run_turn(self.sid, text, **kw)

    def sess(self):
        return self.store.load(self.sid)

    def seed(self, gid="G012", keys=None, subject="banana"):
        self.tools.gens[gid] = gen(gid, keys=keys)
        s = self.sess()
        self.store.add_pass(s, subject, generation=gid, prompt=f"{subject} stickers", grid="3x3", style_id="flat_vector")
        s["focus"] = {"generation": gid, "stickers": []}
        self.store.save(s)

    def cfg(self):
        return {"configurable": {"thread_id": self.sid}}


class Checkpoints(Base):
    def test_resumed_finish_does_not_call_the_summary_provider(self):
        turn = self.agent.prepare(self.sid, "hi")
        turn.queue = ["smalltalk"]
        self.agent.graph.update_state(self.cfg(), {"turn": turn}, as_node="resolve")
        turn._lock.__exit__(None, None, None)
        def reduce(sess, summarise=None):
            if summarise:
                summarise(sess)
            return False
        with mock.patch.object(self.store, "reduce", side_effect=reduce), mock.patch.object(self.agent.brain, "summarise") as provider:
            self.assertTrue(self.agent._resume_dead_turn(self.sid, self.sess(), None))
            provider.assert_not_called()

    def test_resume_revalidates_routes_before_spending_or_deciding(self):
        for node in ("review", "confirm"):
            with self.subTest(node=node), mock.patch.object(Agent, "n_" + node) as unsafe:
                agent = self.make_agent()
                self.agent = agent
                turn = agent.prepare(self.sid, "old approval")
                turn.queue = [node]
                agent.graph.update_state(self.cfg(), {"turn": turn}, as_node="understand")
                turn._lock.__exit__(None, None, None)
                self.say("cancel")
                unsafe.assert_not_called()
                self.assertTrue(any(m.get("status") == "error" for m in self.sess()["messages"]))

    def test_failed_vision_names_is_not_replayed_by_the_next_message(self):
        self.seed()
        agent = self.make_agent()
        turn = agent.prepare(self.sid, "check names")
        turn.sess["settings"]["allow_vlm"] = True
        turn.queue = ["names"]
        agent.graph.update_state(self.cfg(), {"turn": turn}, as_node="resolve")
        with mock.patch.object(self.tools, "name_proposals", side_effect=RuntimeError("recorded then failed")) as call:
            with self.assertRaises(RuntimeError):
                agent.graph.invoke(None, self.cfg())
            turn._lock.__exit__(None, None, None)
            self.say("thanks")
            self.assertEqual(call.call_count, 1)

    def test_turn_is_checkpointed_under_the_session_id(self):
        self.say("hi")
        self.assertTrue((self.out / "checkpoints.sqlite").exists())
        snap = self.agent.graph.get_state(self.cfg())
        self.assertEqual(snap.values["turn"].text, "hi")
        self.assertEqual(tuple(snap.next), ())

    def test_safe_interrupted_turn_resumes_on_the_next_message(self):
        with mock.patch.object(Agent, "n_smalltalk", side_effect=RuntimeError("boom")):
            doomed = self.make_agent()
            turn = doomed.prepare(self.sid, "hi")
            with self.assertRaises(RuntimeError):
                doomed.graph.invoke({"turn": turn}, self.cfg())
            turn._lock.__exit__(None, None, None)
        pending = self.agent.graph.get_state(self.cfg())
        self.assertTrue(pending.next, "the crashed turn leaves a pending checkpoint")
        m = self.say("thanks")
        self.assertIn("You're welcome", m["text"])
        texts = [x["text"] for x in self.sess()["messages"] if x.get("role") == "assistant" and x.get("status") == "done"]
        self.assertTrue(any(t.startswith("Hi!") for t in texts), "the dead 'hi' turn completed on resume")
        self.assertEqual(tuple(self.agent.graph.get_state(self.cfg()).next), ())


class FocusFallback(Base):
    def test_last_turn_stickers_repair_an_emptied_focus(self):
        self.seed("G012")
        self.say("I like 2 and 7")
        s = self.sess()
        self.assertEqual(s["focus"]["stickers"], ["G012/S2", "G012/S7"])
        s["focus"] = {"generation": "G012", "stickers": []}      # a paid batch emptied the focus
        self.store.save(s)
        self.assertEqual(self.agent._last_focus(self.sid, "G012", None), ["G012/S2", "G012/S7"])
        m = self.say("make number 3 happier")
        p = self.sess().get("pending") or {}
        self.assertEqual((p.get("items") or [{}])[0].get("regen_of"), "G012/S3")
        self.assertIn("G012", m["text"] + str(p))


class ProfileTable(Base):
    def test_explicit_tone_requests_route_to_profile(self):
        for text in ("be brief", "keep it short", "stay formal", "be warm"):
            self.say(text)
            self.assertEqual(self.agent._profile().tone(), "formal" if "formal" in text else "warm" if "warm" in text else "brief")
            self.assertFalse(self.sess().get("pending"))

    def test_unrelated_and_negated_facts_do_not_set_tone(self):
        from stickerloom.agent.profile import detect_tone
        for text in ("my name is Shorty", "I like warm colours", "don't be brief", "do not keep it short", "never stay formal", "be not warm", "be briefly"):
            with self.subTest(text=text):
                self.assertIsNone(detect_tone(text))
        self.say("my name is Shorty")
        self.say("I like warm colours")
        self.assertIsNone(self.agent._profile().tone())

    def test_facts_edits_and_tone_grow_one_row(self):
        prof = self.agent._profile()
        self.assertEqual(prof.set_facts({"name": "Noor", "likes": ["camels"]}, "said"), {"name": "Noor", "likes": ["camels"]})
        self.assertTrue((self.out / "profiles.sqlite").exists())
        self.assertEqual(prof.name(), "Noor")
        prof.count_edit("tweak")
        prof.count_edit("tweak")
        prof.count_edit("action")
        self.assertEqual(prof.top_edit(), ("tweak", 2))
        prof.count_edit("action")
        self.assertIsNone(prof.top_edit(), "a split habit is no habit")
        self.assertTrue(prof.set_tone("brief"))
        self.assertEqual(prof.tone(), "brief")
        self.assertFalse(prof.set_tone("silly"))
        doc = Profile(self.out).load()
        self.assertEqual((doc.get("tone"), doc.get("edit_counts", {}).get("tweak"), doc["facts"]["name"]["value"]),
                         ("brief", 2, "Noor"))

    def test_brief_tone_shortens_the_greeting(self):
        self.agent._profile().set_tone("brief")
        self.assertIn("What are we making?", self.say("hi")["text"])

    def test_successful_edits_are_counted_and_hinted(self):
        self.seed("G012")
        self.say("make number 3 less flattened")
        self.assertEqual(self.agent._profile().top_edit(), ("tweak", 1))
        self.assertIn("tweak", self.agent._edit_hint())
