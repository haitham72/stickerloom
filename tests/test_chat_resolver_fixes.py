"""The chat's resolver and edit fixes (HANDOFF "Agent and chat", 2026-10-02/03): a bare person reference is the last subject, `last` is sticker 9 only as an ordinal, an edit REUSES the parent's prompt,
"undo" takes the last refinement back, a number the batch does not have is said, and "don't ask me about vision" does not switch off the spending question.
Rules only, FakeTools, no provider; the one server test uses the fake Higgsfield CLI of test_live."""
import threading
import unittest
from pathlib import Path
from unittest import mock

from stickerloom.agent import editroute
from stickerloom.agent import resolver as R
from stickerloom.agent.tools import ConsoleTools
from stickerloom.flow import gates
from stickerloom.generation import higgsfield, prompter, styles
from tests import test_live as TL
from tests.test_agent import Base, banana_plan, gen


def ctx(**kw):
    base = {"generation": "G012", "n": 9, "stickers": [], "known": {"G012": 9}, "latest": "G012", "focus_stickers": []}
    base.update(kw)
    return base


class PersonReferences(unittest.TestCase):
    """HANDOFF "The chat asks 'make who?'": `him` / `her` / `he` / `she` / `they` and `the guy` / `last guy` are the character of the chat, never a question and never a new sheet."""

    def test_him_her_he_she_they_are_pronouns_of_an_edit(self):
        for text in ("make him wear winter coat", "make her happier", "give him a hat", "make the guy wear a coat", "make the last guy happier"):
            self.assertEqual(R.classify(text, False, True)[0], ["EDIT_STICKERS"], text)
        self.assertEqual(R.classify("make me a falcon", False, True)[0], ["NEW"])
        self.assertEqual(R.classify("make a winter coat for him", False, False)[0], ["NEW"], "with nothing open it is a request")

    def test_a_bare_person_reference_with_a_batch_open_is_never_a_new_subject(self):
        for text in ("last guy", "him", "the guy", "the last guy", "he looks sad"):
            self.assertEqual(R.classify(text, False, True)[0], ["EDIT_STICKERS"], text)
        self.assertEqual(R.classify("last guy", False, False)[0], ["NEW"], "no batch open: nothing to point at")
        self.assertEqual(R.classify("I like him", False, True)[0], ["FEEDBACK"], "an opinion stays an opinion")

    def test_the_person_is_the_focus_batch_and_last_is_the_newest_batch_of_the_chat(self):
        c = ctx(generation="G012", latest="G013", known={"G012": 9, "G013": 9})
        r = R.resolve("make him wear a hat", c)
        self.assertEqual((r.stickers, r.generation, r.how, r.needs_clarification), ([], "G012", "person reference", False))
        r = R.resolve("make the last guy happier", c)
        self.assertEqual((r.stickers, r.generation, r.how, r.needs_clarification), ([], "G013", "person reference", False), "'last guy' is the last subject, not sticker 9")
        r = R.resolve("last guy", c)
        self.assertEqual((r.stickers, r.generation), ([], "G013"))

    def test_with_nothing_else_to_point_at_a_pronoun_is_the_clicked_sticker(self):
        r = R.resolve("make her happier", ctx(focus_stickers=["G012/S4"]))
        self.assertEqual((r.stickers, r.how), (["G012/S4"], "focus"))
        r = R.resolve("make them happier", ctx(selected=["G012/S2", "G012/S7"]))
        self.assertEqual(r.stickers, ["G012/S2", "G012/S7"])

    def test_last_is_sticker_nine_only_when_it_is_an_ordinal(self):
        for text, want in (("the last one", [9]), ("last sticker", [9]), ("make the last one happier", [9]), ("last picture", [9]), ("the one before last", [8])):
            self.assertEqual(R._numbers(text, 9), want, text)
        for text in ("last guy", "the last guy", "make the last guy happier", "the last batch", "undo the last change", "last time"):
            self.assertEqual(R._numbers(text, 9), [], text)
        self.assertEqual(R.resolve("make the last guy happier", ctx()).stickers, [])
        self.assertEqual(R.resolve("make the last one happier", ctx()).stickers, ["G012/S9"])
        self.assertEqual(R.resolve("last one happier", ctx(n=4, known={"G012": 4})).stickers, ["G012/S4"], "the last of a 2x2 is 4")


class NumbersTheBatchDoesNotHave(unittest.TestCase):
    def test_a_number_beyond_the_batch_is_found_and_other_numbers_are_not_mistaken_for_it(self):
        self.assertEqual(R.beyond("make number 12 happier", 9), [12])
        self.assertEqual(R.beyond("make 12 happier", 9), [12])
        self.assertEqual(R.beyond("approve #10 and 11th", 9), [10])
        self.assertEqual(R.beyond("make him happier 12", 9, "12"), [12], "the answer to 'which one?'")
        self.assertEqual(R.beyond("make number 0 happier", 9), [0])
        for text in ("make number 3 happier", "make me 12 falcon stickers", "use a 3x3 grid", "G012/S3 happier", "make G012 happier", "the 4 stickers on the left", "pack of 12"):
            self.assertEqual(R.beyond(text, 9), [], text)
        self.assertEqual(R.beyond("make number 7 happier", 4), [7], "a 2x2 batch has 4")


class SettingsWordsAreNotSpendingWords(unittest.TestCase):
    def test_dont_ask_me_about_x_does_not_turn_the_spending_question_off(self):
        self.assertEqual(R.settings_from("please don't ask me about vision again"), {})
        self.assertEqual(R.settings_from("stop asking me whether to use vision"), {})
        self.assertEqual(R.settings_from("don't ask me before spending"), {"ask_before_spending": False})
        self.assertEqual(R.settings_from("stop asking"), {"ask_before_spending": False})
        self.assertEqual(R.settings_from("don't ask"), {"ask_before_spending": False})
        self.assertEqual(R.settings_from("instant"), {"ask_before_spending": False})
        self.assertEqual(R.settings_from("allow ai vision"), {"allow_vlm": True})


class UndoWords(unittest.TestCase):
    def test_undo_and_revert_are_their_own_intent_even_with_a_batch_open(self):
        for text in ("undo", "Undo!", "revert", "please undo that", "undo the last change", "ok undo", "roll back", "go back", "take it back", "undo my last edit"):
            self.assertEqual(R.classify(text, False, True)[0], ["UNDO"], text)
            self.assertEqual(R.classify(text, False, False)[0], ["UNDO"], text)
        for text in ("go back to the previous one", "undo 3", "make number 3 happier", "yes"):
            self.assertNotEqual(R.classify(text, False, True)[0], ["UNDO"], text)


class WhatIsLeftOfAnEdit(unittest.TestCase):
    """The edit text keeps the person's own words: a, the, and, it, one, like stay ("wear a hat" was "wear hat")."""

    def test_the_change_is_the_sentence_without_what_it_points_at_and_nothing_else(self):
        for text, want in (("make number 3 wear a hat", "wear a hat"), ("make him wear a hat", "wear a hat"), ("make 3 wear a hat and a scarf", "wear a hat and a scarf"),
                           ("make the last one happier", "happier"), ("make the third one bigger", "bigger"), ("make these more energetic", "more energetic"), ("make 2 and 7 happier", "happier"),
                           ("make the guy wear a coat", "wear a coat"), ("remove the paris skyline", "remove the paris skyline"), ("I like 2 but make 5 happier", "happier"),
                           ("make 5 like 2", ""), ("change his clothes colour", "change his clothes colour")):
            self.assertEqual(editroute.delta_of(text), want, text)

    def test_a_verb_and_a_number_alone_is_a_fresh_take_not_a_change(self):
        for d in ("", "redo 3", "regenerate 2 and 5", "redo", "fix these"):
            self.assertTrue(editroute.fresh_take(d), d)
        for d in ("wear a hat", "happier", "redo the hat"):
            self.assertFalse(editroute.fresh_take(d), d)

    def test_a_person_noun_is_a_target_of_an_edit_of_the_sheet(self):
        for text in ("make the last guy happier", "make the guy wear a coat", "make the girl blonde"):
            got = editroute.classify_edit(text)
            self.assertEqual((got["route"], got["case"]), ("regen", "tweak"), text)
        self.assertEqual(editroute.classify_edit("make the last one happier")["delta"], "happier")
        self.assertEqual(editroute.classify_edit("make 3 wear a hat and a scarf")["delta"], "wear a hat and a scarf")
        self.assertIsNone(editroute.classify_edit("redo 3"), "no change named: the sticker edit says 'a fresh take'")


def parent_result(version, style="clay_3d", key="blue"):
    cells = [{"pos": i, "label": f"pose {i}", "tags": [f"banana_pose_{i}"], "emoji": "🍌", "motion": f"wobbles {i}"} for i in range(1, 10)]
    return {"task": "banana", "task_slug": "banana", "template_version": version,
            "slots": {"subject_description": "a cheerful banana mascot", "style_id": style, "cells": cells, "key_colour": key, "mode": "sheet_3x3"},
            "stickers": [{"index": i, "key": f"banana_pose_{i}", "tags": [f"banana_pose_{i}"], "emoji": "🍌", "prompt": f"pose {i}"} for i in range(1, 10)]}


class TheParentsTemplate(unittest.TestCase):
    """HANDOFF trap: `regen_plan` hardcoded template v1 (a clay batch came out as "flat vector sticker illustration"). It now keeps the parent's version (when it is 2 or later) and its style and key colour."""

    def test_the_one_by_one_keeps_the_parents_style_key_colour_and_wording(self):
        for ver in (2, 3):
            plan = gates.regen_plan(parent_result(ver), 3)
            self.assertEqual((plan["template_id"], plan["template_version"], plan["grid"]), ("single_1x1", ver, [1, 1]), ver)
            self.assertIn(styles.PHRASE["clay_3d"], plan["sheet_prompt"], ver)
            self.assertNotIn("flat vector sticker illustration", plan["sheet_prompt"])
            self.assertIn("#0000FF", plan["sheet_prompt"], "the parent's key colour")
            self.assertIn("pose 3", plan["sheet_prompt"])
            self.assertEqual(plan["slots"]["cells"][0]["motion"], "wobbles 3", "the cell's motion travels with it")
        self.assertIn("state of action", gates.regen_plan(parent_result(3), 3)["sheet_prompt"])
        self.assertNotIn("state of action", gates.regen_plan(parent_result(2), 3)["sheet_prompt"], "a v2 parent keeps its v2 wording")

    def test_a_v1_parent_is_never_regenerated_with_the_v1_style_line(self):
        plan = gates.regen_plan(parent_result(1), 2)
        self.assertEqual(plan["template_version"], prompter.TEMPLATE_VERSION)
        self.assertIn(styles.PHRASE["clay_3d"], plan["sheet_prompt"])
        self.assertNotIn("flat vector sticker illustration", plan["sheet_prompt"])
        self.assertEqual(gates.regen_plan({**parent_result(3), "template_version": None}, 2)["template_version"], prompter.TEMPLATE_VERSION)


class TheNoorRepro(Base):
    """The transcript: with a batch open, "make him wear winter coat" was a NEW sheet and "last guy" was sticker 9."""

    def setUp(self):
        super().setUp()
        self.seed("G012", subject="banana")
        self.tools.plans["G012"] = banana_plan()
        s = self.sess(); s["settings"]["allow_vlm"] = False; self.store.save(s)          # keep the vision question out of these turns

    def pending(self):
        return self.sess()["pending"]

    def planned(self):
        return [c for c in self.tools.calls if c[0] == "plan"]

    def test_make_him_wear_winter_coat_is_an_edit_of_the_batch_on_screen(self):
        m = self.say("make him wear winter coat")
        self.assertEqual(self.planned(), [], "no new plan was asked for")
        it = self.pending()["items"][0]
        self.assertEqual((it["parent"], it["subject"], it["refs"], it["note"]), ("G012", "banana", ["R101"], "tweak: wear winter coat"))
        self.assertIn("wear winter coat", it["ref_clause"])
        self.assertEqual([c["label"] for c in it["plan"]["slots"]["cells"]], [f"pose {i}" for i in range(1, 10)], "the parent's prompt is reused as it is")
        self.assertNotIn("winter coat", m["text"].lower().replace("wear winter coat", ""))
        self.say("yes")
        self.assertEqual((self.tools.sent[-1]["parent"], self.tools.sent[-1]["refs"]), ("G012", ["R101"]))

    def test_last_guy_alone_asks_what_to_change_about_the_batch_it_assumed(self):
        for text in ("last guy", "him", "the guy"):
            self.setUp()
            m = self.say(text)
            self.assertEqual(self.planned(), [], text)
            self.assertIsNone(self.pending(), text)
            self.assertIn("What should I change", m["text"], text)
            self.assertIn("Banana", m["text"], "it says which batch it assumed")
            self.assertNotIn("Which sticker", m["text"])
            self.assertEqual(self.sess()["focus"]["generation"], "G012")
            self.assertEqual({c["text"] for c in m["chips"]}, {"make him happier", "give him a hat"})
            self.tearDown()
        self.setUp()

    def test_make_the_last_guy_happier_is_the_newest_batch_of_the_chat_not_sticker_nine(self):
        self.seed("G013", subject="falcon")
        self.tools.plans["G013"] = banana_plan("falcon")
        s = self.sess(); s["focus"] = {"generation": "G012", "stickers": []}; s["settings"]["allow_vlm"] = False; self.store.save(s)
        self.say("make the last guy happier")
        it = self.pending()["items"][0]
        self.assertEqual((it["parent"], it.get("regen_of")), ("G013", None), "the whole last batch, not S9 of anything")
        self.assertEqual(self.planned(), [])

    def test_make_the_guy_wear_a_coat_does_not_ask_which_sticker(self):
        m = self.say("make the guy wear a coat")
        self.assertNotIn("Which sticker", m["text"])
        self.assertEqual(self.pending()["items"][0]["parent"], "G012")
        self.assertIn("wear a coat", self.pending()["items"][0]["ref_clause"])

    def test_the_last_one_is_still_sticker_nine(self):
        self.say("make the last one happier")
        it = self.pending()["items"][0]
        self.assertEqual((it["regen_of"], it["parent"]), ("G012/S9", "G012"))
        self.assertIn("happier", it["ref_clause"])
        self.assertNotIn("last", it["ref_clause"].lower().replace("the last", "x"), "'last' is not part of the change")


class AnEditReusesTheParentPrompt(Base):
    """HANDOFF "An edit must reuse the same prompt and adjust it": the 1x1 is the parent's own cell (style, key colour, template version, label) with the change appended, the parent's picture of the sticker goes
    with it, the batch keeps the parent's edge finish, and the words of the sentence are never rebuilt into a new prompt."""

    def setUp(self):
        super().setUp()
        self.seed("G012", subject="banana")
        plan = banana_plan()
        plan["slots"]["style_id"] = "clay_3d"
        plan["slots"]["key_colour"] = "blue"
        plan["template_version"] = 2
        self.tools.plans["G012"] = plan
        self.tools.edges = {"G012": {"outline": 7, "erode": 2}}
        s = self.sess(); s["settings"]["allow_vlm"] = False; self.store.save(s)

    def pending(self):
        return self.sess()["pending"]

    def test_a_selection_edit_is_the_parents_cell_plus_the_change_and_the_sticker_as_the_picture(self):
        m = self.say("make these wear a hat", selected=["G012/S2", "G012/S7"])
        p = self.pending()
        self.assertEqual([i["regen_of"] for i in p["items"]], ["G012/S2", "G012/S7"])
        for it, n in zip(p["items"], (2, 7)):
            cell = it["plan"]["slots"]["cells"][0]
            self.assertEqual(cell["label"], f"pose {n}, wear a hat", "the parent's label with the change appended; 'wear a hat' is not 'wear hat'")
            self.assertEqual((it["plan"]["slots"]["style_id"], it["plan"]["slots"]["key_colour"], it["plan"]["template_version"]), ("clay_3d", "blue", 2), "the parent's own plan")
            self.assertEqual((it["style_id"], it["outline"], it["erode"]), ("clay_3d", 7, 2))
            self.assertEqual(it["plan"]["grid"], [1, 1])
            self.assertIn("wear a hat", it["ref_clause"])
            self.assertIn("character to keep", it["ref_clause"])
            self.assertNotIn("change only the expression and the pose", it["ref_clause"], "the default clause would contradict 'add a hat'")
            self.assertIn("wear a hat", it["prompt"])
        self.assertEqual([i["refs"] for i in p["items"]], [["R201"], ["R202"]], "each parent sticker goes as the picture")
        self.assertIn("4 credits", m["text"])
        self.say("yes")
        sent = self.tools.sent
        self.assertEqual([(x["grid"], x["regen_of"], x["style_id"], x["outline"], x["erode"]) for x in sent], [("1x1", "G012/S2", "clay_3d", 7, 2), ("1x1", "G012/S7", "clay_3d", 7, 2)],
                         "the batch is made in the PARENT's style (the session says flat) and with the parent's edge finish")
        self.assertEqual([x["plan"]["slots"]["cells"][0]["label"] for x in sent], ["pose 2, wear a hat", "pose 7, wear a hat"])

    def test_make_5_like_2_sends_sticker_2_as_the_picture_with_a_look_clause(self):
        self.say("make 5 like 2")
        it = self.pending()["items"][0]
        self.assertEqual((it["regen_of"], it["refs"]), ("G012/S5", ["R301"]))
        self.assertEqual([c for c in self.tools.calls if c[0] == "reference_from_sticker"], [("reference_from_sticker", "G012/S2")])
        self.assertTrue(it["ref_clause"].startswith("Reference: the attached image is the look to match"))
        self.assertNotIn("change only the expression and the pose", it["ref_clause"])
        self.assertTrue(it["plan"]["slots"]["cells"][0]["label"].startswith("pose 5, same look as"))
        self.assertEqual((it["style_id"], it["outline"]), ("clay_3d", 7))

    def test_a_fresh_take_changes_nothing_in_the_prompt_and_sends_no_picture(self):
        self.say("redo 3")
        it = self.pending()["items"][0]
        self.assertEqual((it["regen_of"], it["refs"], it["ref_clause"]), ("G012/S3", [], None))
        self.assertEqual(it["plan"]["slots"]["cells"][0]["label"], "pose 3", "the parent's wording exactly")
        self.assertTrue(it["prompt"].endswith("a fresh take"))
        self.assertEqual((it["style_id"], it["outline"], it["erode"]), ("clay_3d", 7, 2))

    def test_more_than_four_is_said_not_silently_cut(self):
        m = self.say("make these happier", selected=[f"G012/S{i}" for i in range(1, 7)])
        self.assertEqual(len(self.pending()["items"]), 4)
        self.assertIn("first 4 of 6", m["text"])

    def test_the_other_edit_routes_carry_the_edge_finish_too(self):
        self.say("make number 3 happier")                                          # one slice, drawn alone
        self.say("yes")
        self.say("make him wear a hat")                                            # the whole sheet
        self.say("yes")
        self.assertEqual([(x["outline"], x["erode"], x["regen_of"]) for x in self.tools.sent], [(7, 2, "G012/S3"), (7, 2, None)])

    def test_a_new_request_has_no_edge_of_its_own(self):
        self.say("make me falcon stickers")
        self.say("yes")
        self.assertEqual((self.tools.sent[-1]["outline"], self.tools.sent[-1]["erode"]), (None, None), "the session default applies")


class UndoInTheChat(Base):
    def setUp(self):
        super().setUp()
        self.seed("G012", subject="banana")
        self.tools.plans["G012"] = banana_plan()
        s = self.sess(); s["settings"]["allow_vlm"] = False; self.store.save(s)

    def child(self, gid="G013", parent="G012", subject="banana"):
        s = self.sess()
        self.tools.gens[gid] = gen(gid)
        self.store.add_pass(s, subject, generation=gid, prompt=subject, parent=parent, style_id="flat_vector")
        s["focus"] = {"generation": gid, "stickers": []}
        self.store.save(s)

    def test_undo_returns_to_the_batch_the_last_refinement_came_from(self):
        self.child()
        m = self.say("undo")
        self.assertEqual(self.sess()["focus"], {"generation": "G012", "stickers": []})
        self.assertIn("Undone", m["text"])
        self.assertIn("nothing is deleted", m["text"])
        self.assertEqual([c for c in m["cards"] if c["type"] == "generation"][0]["generation"], "G012")
        self.assertEqual([c for c in self.tools.calls if c[0] in ("create", "plan")], [], "free: nothing is generated or spent")
        self.assertNotIn("Which sticker", m["text"])
        p = [p for s in self.sess()["subjects"] for p in s["passes"] if p["generation"] == "G013"][0]
        self.assertTrue(p["undone"], "the newer batch stays in the chat, marked")

    def test_after_undo_it_and_number_three_mean_the_earlier_version(self):
        self.child()
        self.say("undo")
        self.say("make number 3 happier")
        it = self.sess()["pending"]["items"][0]
        self.assertEqual((it["parent"], it["regen_of"]), ("G012", "G012/S3"))

    def test_undo_twice_walks_back_one_refinement_at_a_time_and_then_says_there_is_nothing_more(self):
        self.child("G013", "G012")
        self.child("G014", "G013")
        self.say("undo")
        self.assertEqual(self.sess()["focus"]["generation"], "G013")
        self.say("revert")
        self.assertEqual(self.sess()["focus"]["generation"], "G012")
        m = self.say("undo")
        self.assertIn("nothing to undo", m["text"].lower())
        self.assertEqual(self.sess()["focus"]["generation"], "G012")

    def test_undo_with_a_plan_waiting_drops_the_plan_and_spends_nothing(self):
        self.say("make number 3 happier")
        self.assertIsNotNone(self.sess()["pending"])
        m = self.say("undo")
        self.assertIsNone(self.sess()["pending"])
        self.assertIn("Nothing was spent", m["text"])
        self.assertEqual([c for c in self.tools.calls if c[0] == "create"], [])

    def test_undo_of_a_refinement_that_is_still_being_drawn_says_it_cannot_be_stopped(self):
        self.say("make him wear a hat")
        self.say("yes")                                                        # live: a job, no generation yet
        m = self.say("undo")
        self.assertEqual(self.sess()["focus"]["generation"], "G012")
        self.assertIn("still being drawn", m["text"])
        s = self.sess()
        s["subjects"][0]["passes"][-1]["generation"] = "G013"                  # the sheet comes back later
        self.store.save(s)
        self.store.refresh(self.sess())
        self.assertEqual(self.store.latest_pass(self.sess(), with_generation=True)["generation"], "G012", "an undone batch is not 'the last batch'")

    def test_undo_with_nothing_changed_says_so(self):
        m = self.say("undo")
        self.assertIn("nothing to undo", m["text"].lower())
        self.assertEqual(self.sess()["focus"]["generation"], "G012")

    def test_undo_is_not_taken_for_a_new_sheet_or_a_question_about_which_sticker(self):
        m = self.say("revert")
        self.assertFalse([c for c in self.tools.calls if c[0] == "plan"])
        self.assertNotIn("Which sticker", m["text"])


class TheBatchHasNine(Base):
    def setUp(self):
        super().setUp()
        self.seed("G012", subject="banana")
        self.tools.plans["G012"] = banana_plan()
        s = self.sess(); s["settings"]["allow_vlm"] = False; self.store.save(s)

    def test_number_twelve_in_a_batch_of_nine_says_the_batch_has_nine_and_the_loop_ends(self):
        m = self.say("make number 12 happier")
        self.assertIn("9 stickers", m["text"])
        self.assertIn("no number 12", m["text"])
        self.assertIsNone(self.sess()["pending"])
        m = self.say("12")                                                     # the answer to the question, still beyond the batch
        self.assertIn("9 stickers", m["text"])
        self.assertIn("no number 12", m["text"])
        m = self.say("3")
        it = self.sess()["pending"]["items"][0]
        self.assertEqual((it["regen_of"], it["parent"]), ("G012/S3", "G012"), "a valid answer ends the loop")
        self.assertIn("happier", it["ref_clause"])

    def test_the_same_in_the_plain_edit_path_and_for_a_bare_number(self):
        m = self.say("make 12 happier")
        self.assertIn("9 stickers", m["text"])
        self.assertEqual([c for c in self.tools.calls if c[0] == "plan"], [])
        m = self.say("redo 11")
        self.assertIn("no number 11", m["text"])

    def test_a_2x2_batch_says_it_has_four(self):
        self.tools.gens["G012"] = gen("G012", n=4)
        m = self.say("make number 7 happier")
        self.assertIn("4 stickers", m["text"])
        self.assertIn("1 to 4", m["text"])

    def test_a_count_in_a_request_is_not_a_sticker_number(self):
        m = self.say("make me 12 falcon stickers")
        self.assertEqual(m["cards"][0]["type"], "plan")


class VisionIsNotSpending(Base):
    def test_dont_ask_me_about_vision_again_leaves_the_spending_question_on(self):
        m = self.say("please don't ask me about vision again")
        s = self.sess()
        self.assertTrue(s["settings"]["ask_before_spending"])
        self.assertTrue(s["vision_asked"], "the one-time vision question is not asked again in this chat")
        self.assertIsNone(s["settings"].get("allow_vlm"), "and the vision choice itself is left undecided")
        self.assertIn("ask before spending", m["text"])
        self.assertIn("unchanged", m["text"])
        self.assertNotIn("One more thing, once", m["text"])
        self.say("make me falcon stickers")
        self.assertIsNotNone(self.sess()["pending"], "it still asks before spending")
        self.assertEqual([c for c in self.tools.calls if c[0] == "create"], [])

    def test_dont_ask_me_before_spending_still_turns_it_off(self):
        self.say("don't ask me before spending")
        self.assertFalse(self.sess()["settings"]["ask_before_spending"])


class EdgeFinishReachesTheBatch(TL.Base):
    """`tools.create(outline=, erode=)` -> the job's request -> `pl.start`: an edit made through the real server has its parent's finish (erode used to be dropped on the way)."""

    def test_console_tools_hand_outline_and_erode_to_the_live_sheet_call(self):
        calls = []

        class C:
            out = self.tmp / "out"

            def live(self, kind, body, base_plan=None, **kw):
                calls.append((kind, dict(body), base_plan))
                return {"job": "J001", "task": "001", "estimate": 2.0}
        with mock.patch.object(higgsfield, "available", lambda: True):
            ConsoleTools(C()).create("x", "1x1", "clay_3d", True, parent=None, regen_of=None, refs=["R1"], base_plan={"p": 1}, ref_clause="clause", outline=7, erode=2)
            ConsoleTools(C()).create("x", "3x3", "clay_3d", True)
        self.assertEqual((calls[0][1]["outline"], calls[0][1]["erode"], calls[0][1]["ref_clause"]), (7, 2, "clause"))
        self.assertNotIn("outline", calls[1][1])
        self.assertNotIn("erode", calls[1][1])

    def test_the_server_keeps_erode_with_the_job_and_starts_the_batch_with_it(self):
        import http.client
        import json
        import time
        from stickerloom.console.server import serve
        from stickerloom.engine.config import EngineConfig
        from tests.test_golden import shape_sheet
        srv, c = serve(self.out, self.tmp / "in", 0, cfg=EngineConfig(), block=False)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.files["png"] = TL.png_bytes(shape_sheet(1200, [(x, y) for y in (200, 600, 1000) for x in (200, 600, 1000)]))

        def req(method, path, body=None):
            h = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=60)
            h.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
            r = h.getresponse(); data = r.read(); h.close()
            return r.status, json.loads(data)
        try:
            s, j = req("POST", "/api/live/sheet", {"prompt": "blob", "grid": "3x3", "style_id": "toon_shade", "outline": 7, "erode": 2})
            self.assertEqual(s, 200, j)
            end = time.time() + 120
            job = {}
            while time.time() < end:
                job = req("GET", f"/api/jobs/{j['job']}")[1]
                if job.get("generation") and job["status"] == "DONE":
                    break
                time.sleep(0.3)
            self.assertTrue(job.get("generation"), job)
            self.assertEqual((job["request"]["outline"], job["request"]["erode"]), (7, 2))
            res = req("GET", f"/api/generations/{int(job['generation'][1:])}")[1]
            self.assertEqual((res["outline_px"], res["erode_px"]), (7, 2))
        finally:
            c.wait_jobs(90)
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
