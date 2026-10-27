"""The chat knows the person it talks to: a greeting, a fact about the speaker or the answer to my own question is never a
subject or a plan; "what is my name?" reads the profile; "Not yet" lets the held plan go quietly; "create camel in lamborgini" plans a camel IN a Lamborghini, 3x3, the standard style.
The profile is per user, validated before every write, timestamped, and never seen by anyone else. No model and no provider is reached (Brain gets a fake, the planner runs without AI)."""
import json
import unittest

from stickerloom.agent.graph import Agent
from stickerloom.agent.memory import SessionStore
from stickerloom.agent.profile import Profile, validate_facts
from stickerloom.agent.resolver import classify, profile_facts, request_text
from stickerloom.agent.tools import FakeTools
from stickerloom.generation import tasks
from tests.test_agent import Base


class RealPlanner(FakeTools):
    """The fake console with the real built-in planner (generation/tasks.preview, no AI): what the card shows is what the engine would plan."""

    def plan(self, prompt, grid, style_id, ai):
        self.calls.append(("plan", prompt))
        return tasks.preview(prompt, grid, style_id, False)


class Transcript(Base):
    def setUp(self):
        super().setUp()
        self.tools = RealPlanner()
        self.agent.close()
        self.agent = Agent(self.store, self.tools, self.agent.brain, self.cache)
        self.addCleanup(self.agent.close)

    def plans(self, m):
        return [c for c in m["cards"] if c.get("type") in ("plan", "multi")]

    def test_lines_1_to_6(self):
        m = self.say("hello from noor")                                                         # 1
        self.assertEqual(self.plans(m), [])
        self.assertIn("Nice to meet you, Noor", m["text"])
        self.assertNotIn("vision", m["text"].lower())
        self.assertFalse([c for c in m["chips"] if c.get("setting")], "no AI-vision switch on a greeting")
        self.assertEqual(self.say("what is my name?")["text"], "You're Noor.")                  # 2 (known now)
        self.say("make me falcon stickers")                                                        # a plan to hold
        held = self.sess()["pending"]["subject"]
        m = self.say("it is 'noor'")                                                            # 3
        self.assertEqual(self.plans(m), [])
        self.assertEqual(self.sess()["pending"]["subject"], held)
        self.assertIn("your name", m["text"])
        self.assertIn("You're Noor", self.say("do you know my name?")["text"])                  # 4
        m = self.say("Not yet")                                                                    # 6
        self.assertEqual(self.plans(m), [])
        self.assertIsNone(self.sess().get("pending"), "the held plan is gone")
        self.assertFalse([c for c in self.tools.calls if c[0] == "create"], "nothing was made")

    def test_line_4_and_5_the_answer_to_my_question_sticks(self):
        m = self.say("do you know my name?")
        self.assertIn("What should I call you?", m["text"])
        m = self.say("noor")                                                                    # 5 (and the "another case" transcript)
        self.assertEqual(self.plans(m), [], "the answer to my question is not a subject")
        self.assertIsNone(self.sess().get("pending"))
        self.assertIn("Nice to meet you, Noor", m["text"])
        self.assertEqual(self.say("what is my name?")["text"], "You're Noor.")
        self.assertEqual(len(self.plans(self.say("noor"))), 1, "without my question, a bare word is still a request")

    def test_a_plan_made_of_the_name_goes_when_the_name_is_said(self):
        self.say("noor")                                                                        # the old misreading: a plan "for noor"
        self.assertIsNotNone(self.sess().get("pending"))
        m = self.say("my name is noor")
        self.assertIsNone(self.sess().get("pending"), "the plan made of the name is dropped")
        self.assertIn("I dropped the plan for", m["text"])
        self.assertNotIn("still waiting", m["text"])
        self.say("make me falcon stickers")
        self.say("my name is noor")
        self.assertIsNotNone(self.sess().get("pending"), "a real plan stays held")

    def test_line_7_the_whole_request_is_planned(self):
        m = self.say("create camel in lamborgini")
        card = self.plans(m)[0]
        self.assertEqual(card["subject"], "camel in Lamborghini")                                  # typo normalised, shown on the card, the scene kept
        self.assertEqual((card["grid"], card["count"], card["style"]), ("3x3", 9, "flat"))       # the defaults, nothing unasked
        self.assertTrue(all("Lamborghini" in n for n in card["names"]), card["names"])
        held = self.sess()["pending"]["plan"]
        self.assertIn("Lamborghini", held["slots"]["subject_description"])
        built = tasks.preview("create camel in lamborgini", "3x3", "flat_vector", False)
        self.assertTrue(all("Lamborghini" in s["prompt"] for s in built["stickers"]), "every cell is drawn with the car")

    def test_a_remembered_style_is_offered_not_applied(self):
        p = Profile(self.out, "local")
        for _ in range(3):
            p.vote("style", "paper_cut", "more paper cut")
        card = self.plans(self.say("make me owl stickers"))[0]
        self.assertEqual(card["style"], "flat")
        self.assertEqual(len(self.plans(self.say("make me owl stickers in paper cut style"))), 1)

    def test_a_greeting_with_a_request_saves_the_name_and_plans_only_the_request(self):
        m = self.say("hi, I'm Sam, make me a camel in lamborgini")
        self.assertIn("Nice to meet you, Sam", m["text"])
        self.assertEqual(self.plans(m)[0]["subject"], "camel in Lamborghini")


class ProfileStore(Base):
    def test_facts_are_validated_timestamped_and_last_write_wins(self):
        p = Profile(self.out, "U001")
        self.assertEqual(p.set_facts({"name": "Noor", "place": "Paris", "age": "40", "likes": ["camels"], "role": "admin", "extra": {"fav colour": "teal", "x" * 30: "y"}}),
                         {"name": "Noor", "place": "Paris", "age": 40, "likes": ["camels"], "extra": {"fav_colour": "teal", "x" * 24: "y"}})
        self.assertEqual(p.set_facts({"name": "<script>" + "a" * 80, "age": 500, "likes": "x" * 200, "nested": {"a": 1}}), {}, "nothing invalid is written")
        p.set_facts({"name": "Sam"})
        p.set_facts({"dislikes": ["camels"]})
        f = p.facts()
        self.assertEqual((f["name"], f["likes"], f["dislikes"]), ("Sam", [], ["camels"]), "last write wins; a dislike takes the like back")
        raw = json.loads(p.path.read_text(encoding="utf-8"))["facts"]
        self.assertTrue(all(isinstance(v.get("at"), float) for v in raw.values()), "every fact is timestamped")

    def test_profiles_never_leak_across_users(self):
        Profile(self.out, "U001").set_facts({"name": "Amira", "place": "Sharjah"})
        self.assertEqual(Profile(self.out, "U002").facts()["name"], None)
        b = SessionStore(self.out, self.cache, user="U002", see_all=False)
        agent = Agent(b, FakeTools(), self.agent.brain, self.cache)
        self.addCleanup(agent.close)
        sid = b.create()["id"]
        m = agent.run_turn(sid, "what is my name?")
        self.assertNotIn("Amira", m["text"])
        m = agent.run_turn(sid, "what do you know about me?")
        self.assertNotIn("Sharjah", m["text"])

    def test_the_model_proposal_passes_the_validator(self):
        self.assertEqual(validate_facts({"name": "Noor", "password": "x", "age": True, "likes": [{"a": 1}, "tea"]}), {"name": "Noor", "likes": ["tea"]})

    def test_a_correction_the_rules_cannot_read_goes_to_the_model_and_through_the_validator(self):
        replies = ['{"intents": ["PROFILE"], "confidence": 0.9}', '{"name": "Noor", "secret": "x", "age": 999}']
        brain_agent = Agent(self.store, FakeTools(), type(self.agent.brain)(complete=lambda s, u: (replies.pop(0) if replies else "{}", {"model": "fake"})), self.cache)
        self.addCleanup(brain_agent.close)
        m = brain_agent.run_turn(self.sid, "my name is not Sam, it is noor")
        self.assertIn("your name", m["text"])
        self.assertEqual(Profile(self.out, "local").facts()["age"], None, "the model's invalid age never reached the profile")


class Rules(unittest.TestCase):
    def test_extraction_templates(self):
        cases = {"hello from noor": {"name": "Noor"}, "it is 'noor'": {"name": "Noor"}, "I'm Noor, I live in Paris": {"name": "Noor", "place": "Paris"},
                 "I'm 30": {"age": 30}, "I love camels and falcons": {"likes": ["camels", "falcons"]}, "I hate pink": {"dislikes": ["pink"]},
                 "i'm from berlin": {"place": "Berlin"}, "hi falcon": {}, "I am tired": {}, "I like this one": {}, "my brother's name is Ali": {},
                 "create camel in lamborgini": {}}
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(profile_facts(text)[0], want)

    def test_routing(self):
        for text in ("hello from noor", "it is 'noor'", "I live in Paris", "I'm 30"):
            self.assertEqual(classify(text, True, False)[0], ["PROFILE"], text)
        self.assertEqual(classify("hi, I'm Sam, make me a falcon", False, False)[0], ["PROFILE", "NEW"])
        self.assertEqual(classify("I like the falcon", False, True)[0], ["FEEDBACK"], "next to a batch, an opinion about it")
        self.assertEqual(request_text("hello from noor, please create camel in lamborgini"), "create camel in lamborgini")


if __name__ == "__main__":
    unittest.main()
