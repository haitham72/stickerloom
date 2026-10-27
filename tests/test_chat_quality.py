"""The chat's conversation quality (HANDOFF "AUDIT of the chat, 2026-10-02"): each test is a sentence that went wrong in the six reviews. Rules only, FakeTools, no provider."""
import unittest

from stickerloom.agent import resolver as R
from tests.test_agent import Base


class PlanIsExecution(Base):
    def test_the_plan_the_person_approved_is_the_plan_that_is_sent(self):
        """The card's names came from one planner call and Create planned again (temperature 0.8): the card and the batch had zero stickers in common."""
        self.say("make me falcon stickers")
        pend = self.sess()["pending"]
        self.assertTrue(pend["plan"]["stickers"], "the approved plan is stored in pending")
        self.assertNotIn("sheet_prompt", pend["plan"], "the stored plan is the cells and slots; the prompts are rebuilt from them")
        self.say("", action={"type": "confirm"})
        self.assertEqual(len(self.tools.sent_plans), 1)
        self.assertEqual([s["key"] for s in self.tools.sent_plans[0]["stickers"]], [s["key"] for s in pend["plan"]["stickers"]], "the very cells of the card")

    def test_a_refused_start_keeps_the_plan(self):
        self.say("make me falcon stickers")
        self.tools.fail_next_create = True
        m = self.say("yes")
        self.assertIn("kept", m["text"].lower())
        self.assertIsNotNone(self.sess()["pending"], "the plan is not lost when the provider refuses")
        self.say("yes")
        self.assertEqual(len([c for c in self.tools.calls if c[0] == "create"]), 1, "only the second attempt reached the provider")
        self.assertIsNone(self.sess()["pending"], "and now the plan is spent")


class PendingIsNotHijacked(Base):
    def test_only_a_whole_message_yes_confirms(self):
        for text in ("create a dragon pack", "yes make it red", "make it blue", "start over", "go back to the previous one", "yes but only 4 stickers", "perfect but make it flat",
                     "generate me a camel", "ok so I want a teddy bear"):
            self.setUp()
            self.say("make me falcon stickers")
            self.say(text)
            created = [c for c in self.tools.calls if c[0] == "create"]
            self.assertEqual(created, [], f"'{text}' must not spend on the old plan")
            self.tearDown()
        self.setUp()

    def test_these_do_confirm(self):
        for text in ("yes", "Yes please", "ok", "okay go", "sure", "do it", "sounds good", "y", "yalla", "👍", "ايوه", "تمام", "go ahead", "looks good"):
            self.setUp()
            self.say("make me falcon stickers")
            self.say(text)
            self.assertEqual(len([c for c in self.tools.calls if c[0] == "create"]), 1, text)
            self.tearDown()
        self.setUp()

    def test_these_cancel_and_a_negated_instruction_does_not(self):
        for text in ("no", "nope", "nah", "no thanks", "not now", "cancel", "never mind", "لا"):
            self.setUp()
            self.say("make me falcon stickers")
            self.say(text)
            self.assertIsNone(self.sess()["pending"], text)
            self.assertEqual([c for c in self.tools.calls if c[0] == "create"], [], text)
            self.tearDown()
        self.setUp()
        self.say("make me falcon stickers")
        self.say("don't make it blue")
        self.assertIsNotNone(self.sess()["pending"], "a negated instruction is not a cancel (it is something to understand)")

    def test_a_new_request_says_it_replaced_the_old_plan(self):
        self.say("make me falcon stickers")
        m = self.say("make me teddy stickers")
        self.assertIn("falcon", m["text"].lower(), "the reply names the plan that was dropped")
        self.assertIn("replace", m["text"].lower())
        self.assertEqual(self.sess()["pending"]["subject"].split()[0], "teddy")


class ShortWordsAreNotPlans(Base):
    def test_acknowledgements_are_small_talk_not_a_new_set(self):
        for text in ("ok", "nice", "thanks bro", "cool", "great", "thank you so much for that", "lol", "wow", "okay cool", "👍", "thx"):
            self.setUp()
            m = self.say(text)
            self.assertFalse([c for c in self.tools.calls if c[0] == "plan"], f"'{text}' started a plan")
            self.assertIsNone(self.sess()["pending"], text)
            self.assertTrue(m["text"], text)
            self.tearDown()
        self.setUp()

    def test_thanks_and_bye_do_not_answer_hi(self):
        self.assertIn("welcome", self.say("thanks").get("text", "").lower())
        self.assertIn("bye", self.say("bye").get("text", "").lower())
        self.assertIn("hi", self.say("hello").get("text", "").lower())

    def test_a_bare_attribute_with_a_batch_open_is_not_a_new_subject(self):
        self.seed()
        for text in ("bigger", "same but red", "happier", "undo"):
            self.setUp()
            self.seed()
            self.say(text)
            self.assertFalse([c for c in self.tools.calls if c[0] == "plan"], f"'{text}' planned a new set")
            self.tearDown()
        self.setUp()


class ReferencesAndPhrasing(Base):
    def test_S3_is_a_sticker_reference(self):
        self.seed()
        self.assertEqual(R.classify("make S3 happier", False, True)[0], ["EDIT_STICKERS"])
        r = R.resolve("make S3 happier", {"generation": "G012", "n": 9, "stickers": [], "known": {"G012": 9}})
        self.assertEqual(r.stickers, ["G012/S3"])
        r = R.resolve("S4", {"generation": "G012", "n": 9, "stickers": [], "known": {"G012": 9}})
        self.assertEqual(r.stickers, ["G012/S4"], "the answer 'S4' to which-one is understood")

    def test_a_person_pronoun_is_an_edit_of_what_is_open_not_a_new_sheet(self):
        for text in ("make him wear winter coat", "make her happier", "give him a hat", "make the guy wear a coat", "make all of them red", "make everything blue"):
            self.assertNotEqual(R.classify(text, False, True)[0], ["NEW"], text)
        self.assertEqual(R.classify("make me a falcon", False, True)[0], ["NEW"])

    def test_last_is_the_last_sticker_only_when_it_says_so(self):
        n = 9
        self.assertEqual(R._numbers("the last one", n), [9])
        self.assertEqual(R._numbers("last sticker", n), [9])
        self.assertEqual(R._numbers("the one before last", n), [8])
        self.assertEqual(R._numbers("last guy", n), [], "'last guy' is the last subject of the chat, not sticker 9")
        self.assertEqual(R._numbers("I loved the last batch", n), [])
        self.assertEqual(R._numbers("undo the last change", n), [])

    def test_a_count_in_a_new_request_is_not_a_sticker_number(self):
        self.seed()
        for text in ("make me 4 falcon stickers", "make a pack of 5 dogs", "make 9 stickers of a dog"):
            self.assertEqual(R.classify(text, False, True)[0], ["NEW"], text)

    def test_polite_questions_are_requests(self):
        self.assertEqual(R.classify("can you make me a falcon?", False, False)[0], ["NEW"])
        self.assertEqual(R.classify("could you make number 3 happier?", False, True)[0], ["EDIT_STICKERS"])
        self.assertEqual(R.classify("how much?", False, True)[0], ["ASK"])

    def test_a_pasted_prompt_with_the_word_style_is_a_request(self):
        t = "a cute cat in pixar style with big eyes sitting on a cloud holding a small umbrella and smiling happily"
        self.assertEqual(R.classify(t, False, False)[0], ["NEW"])


class ReviewIsNotAGuess(Base):
    def test_a_negated_approval_approves_nothing(self):
        for text in ("don't approve 3", "do not approve everything", "I can't accept these, they are all bad", "never approve 4"):
            self.setUp()
            self.seed()
            self.say(text)
            self.assertEqual([c for c in self.tools.calls if c[0] == "review"], [], text)
            self.tearDown()
        self.setUp()

    def test_a_bulk_decision_asks_once_and_a_single_one_does_not(self):
        self.seed()
        self.say("approve 3")
        self.assertEqual([c[:4] for c in self.tools.calls if c[0] == "review"], [("review", "G012", "APPROVE", [3])])
        self.say("approve everything")
        self.assertEqual(len([c for c in self.tools.calls if c[0] == "review"]), 1, "'everything' waits for the person's yes")
        self.assertEqual(self.sess()["pending"]["type"], "review")
        self.say("yes")
        self.assertEqual(len([c for c in self.tools.calls if c[0] == "review"]), 2)


class FocusFollowsTheNewestBatch(Base):
    def test_after_a_paid_job_returns_the_chat_works_on_the_new_batch(self):
        self.seed("G012", subject="banana")
        self.say("make me falcon stickers")
        self.say("yes")                                           # job J001, no generation yet
        from stickerloom.generation import jobs
        jobs.create(self.out, "sheet", request={"prompt": "falcon"})                       # J001 on disk, as the real queue writes it
        jobs.attach_generation(self.out, "J001", 13)
        self.tools.gens["G013"] = {"generation": "G013", "stickers": [{"id": f"G013/S{i}", "index": i, "key": f"f{i}", "name": f"f{i}", "tags": [], "emoji": ["😀"], "status": "READY", "still": "PENDING"} for i in range(1, 10)]}
        self.store.refresh(s := self.sess())
        self.store.save(s)
        self.assertEqual(self.sess()["focus"]["generation"], "G013")
        self.say("make number 3 happier")
        self.assertTrue(any(c[0] == "plan" and "G013" in str(c) or c[0] == "create" and c[3] == "G013" for c in self.tools.calls) or self.sess()["pending"] and "G013" in str(self.sess()["pending"]))

    def test_the_latest_pass_is_one_that_has_stickers(self):
        self.seed("G012", subject="banana")
        s = self.sess()
        self.store.add_pass(s, "falcon", generation=None, job="J009", prompt="falcon", grid="3x3", style_id="flat_vector")
        self.assertEqual(self.store.latest_pass(s, with_generation=True)["generation"], "G012")


if __name__ == "__main__":
    unittest.main()
