"""The deterministic resolver and intent rules (turn exit 4A: fully offline, these resolve exactly)."""
import unittest

from stickerloom.agent.resolver import classify, is_sticker_answer, polarity_of, resolve, settings_from

STICKERS = [{"index": i, "key": k, "name": k, "tags": [k]} for i, k in enumerate(
    ["banana_dancing", "banana_shocked", "banana_squashed", "dog_banana", "dog_banana_waving", "banana_sleeping", "banana_thinking",
     "banana_running", "banana_happy"], 1)]
CTX = {"generation": "G012", "n": 9, "stickers": STICKERS, "focus_stickers": ["G012/S3"], "selected": [], "parent": "G011",
       "latest": "G013", "known": {"G011": 9, "G012": 9, "G013": 9}}


def r(text, **kw):
    return resolve(text, {**CTX, **kw})


class ResolverTests(unittest.TestCase):
    def test_numbers_in_many_spellings(self):
        for text in ("number 2", "the second", "#2", "sticker two", "2nd one", "make number 2 happier"):
            self.assertEqual(r(text).stickers, ["G012/S2"], text)
        self.assertEqual(r("the last one").stickers, ["G012/S9"])

    def test_lists_and_ranges(self):
        self.assertEqual(r("animate 2 and 7").stickers, ["G012/S2", "G012/S7"])
        self.assertEqual(r("redo 3, 5 and 8").stickers, ["G012/S3", "G012/S5", "G012/S8"])
        self.assertEqual(r("drop 3-5").stickers, ["G012/S3", "G012/S4", "G012/S5"])

    def test_mixed_feedback_with_exclusions(self):
        x = r("I like 2 and 7 but not 3 and 4")
        self.assertEqual((x.positive, x.negative), (["G012/S2", "G012/S7"], ["G012/S3", "G012/S4"]))
        y = r("I hate 4")
        self.assertEqual((y.positive, y.negative), ([], ["G012/S4"]))
        z = r("keep 2 and 7")
        self.assertEqual(z.positive, ["G012/S2", "G012/S7"])

    def test_explicit_ids_win(self):
        x = r("make it like G011/S4")
        self.assertEqual((x.stickers, x.generation), (["G011/S4"], "G011"))
        self.assertEqual(r("S5 of G13").stickers, ["G013/S5"])
        self.assertEqual(r("show me g11").generation, "G011")

    def test_make_a_like_b_and_role_references(self):
        x = r("make 5 like 2")
        self.assertEqual(x.references, [{"source": "G012/S2", "target": "G012/S5", "role": "STYLE"}])
        self.assertEqual(x.stickers, ["G012/S5"])
        y = r("use the style from 2 and the pose from 7")
        self.assertEqual([(q["source"], q["role"]) for q in y.references], [("G012/S2", "STYLE"), ("G012/S7", "POSE")])

    def test_it_and_that_one_mean_the_focus(self):
        self.assertEqual(r("make it happier").stickers, ["G012/S3"])
        self.assertEqual(r("that one is great").stickers, ["G012/S3"])

    def test_previous_is_the_parent_in_this_branch_not_the_highest_id(self):
        x = r("go back to the previous one")
        self.assertEqual(x.generation, "G011")                 # the parent, not G013 (the latest)

    def test_a_concept_names_the_sticker(self):
        self.assertEqual(r("which one is the shocked banana?").stickers, ["G012/S2"])
        self.assertEqual(r("make the squashed one bigger").stickers, ["G012/S3"])

    def test_two_plausible_candidates_ask_one_short_question(self):
        x = r("which is the dog banana")
        self.assertTrue(x.needs_clarification)
        self.assertEqual(x.options, ["G012/S4", "G012/S5"])
        self.assertLess(len(x.clarification), 40)

    def test_a_clear_mapping_never_asks(self):
        self.assertFalse(r("make number 3 happier").needs_clarification)

    def test_the_ui_selection_is_used_for_these(self):
        x = r("make these more energetic", selected=["G012/S2", "G012/S7"])
        self.assertEqual((x.stickers, x.how), (["G012/S2", "G012/S7"], "selection"))
        y = r("make number 4 more energetic", selected=["G012/S2"])      # an explicit number beats the selection
        self.assertEqual(y.stickers, ["G012/S4"])

    def test_grid_sizes_are_not_sticker_numbers(self):
        self.assertEqual(r("use 2x2 and 3x3 please").stickers, [])


class IntentTests(unittest.TestCase):
    def intent(self, text, pending=False, gen=True):
        return classify(text, pending, gen)[0]

    def test_the_documented_examples(self):
        self.assertEqual(self.intent("make my dog as banana stickers", gen=False), ["NEW"])
        self.assertEqual(self.intent("I like 2 and 7 but not 3 and 4"), ["FEEDBACK"])
        self.assertEqual(self.intent("make number 3 less flattened"), ["EDIT_STICKERS"])
        self.assertEqual(self.intent("which one is the shocked banana?"), ["ASK"])
        self.assertEqual(self.intent("animate"), ["ANIMATE"])
        self.assertEqual(self.intent("make it like number 4"), ["EDIT_STICKERS"])

    def test_confirm_and_cancel_only_with_a_pending_plan(self):
        self.assertEqual(self.intent("yes", pending=True), ["CONFIRM"])
        self.assertEqual(self.intent("cancel", pending=True), ["CANCEL"])
        self.assertNotEqual(self.intent("yes", pending=False), ["CONFIRM"])

    def test_a_bare_subject_is_a_request(self):
        i, c = classify("falcon dancing", False, False)
        self.assertEqual(i, ["NEW"])
        self.assertTrue(0.6 <= c < 0.8)

    def test_mixed_message_is_feedback_plus_edit(self):
        self.assertEqual(self.intent("I like 2 but make 5 happier"), ["FEEDBACK", "EDIT_STICKERS"])

    def test_more_and_another_need_a_generation(self):
        self.assertEqual(self.intent("another one please"), ["ANOTHER"])

    def test_settings(self):
        self.assertEqual(self.intent("use 2x2 from now on"), ["CHANGE_SETTINGS"])
        self.assertEqual(settings_from("use 2x2 and don't ask me"), {"grid": "2x2", "ask_before_spending": False})
        self.assertEqual(settings_from("go pixar"), {"style_id": "glossy_3d"})

    def test_small_talk_and_nonsense(self):
        self.assertEqual(self.intent("hello"), ["SMALLTALK"])
        self.assertEqual(classify("", False, False)[0], ["AMBIGUOUS"])

    def test_an_opinion_about_this_is_feedback_not_a_new_subject(self):
        for text in ("this is bad", "I like this one", "that one is ugly", "they are great"):
            self.assertEqual(self.intent(text), ["FEEDBACK"], text)
        self.assertEqual(self.intent("this is bad", gen=False), ["AMBIGUOUS"])            # with nothing on screen it is no opinion, and a statement is never a subject to draw: I ask
        self.assertEqual(self.intent("make it not blurry"), ["NEW"])                      # a request keeps being a request

    def test_video_and_export_continue_the_open_batch(self):
        self.assertEqual(self.intent("create video and export"), ["EXPORT"])               # holds "create" but means the batch in focus, never a subject called "video and export"
        for text in ("export it", "export", "send it to telegram", "send to telegram", "pack it and send"):
            self.assertEqual(self.intent(text), ["EXPORT"], text)
        for text in ("make a video", "video it", "create a video"):
            self.assertEqual(self.intent(text), ["ANIMATE"], text)
        self.assertEqual(self.intent("create video and export", gen=False), ["NEW"])      # with nothing open it is a request like any other
        self.assertEqual(self.intent("make me a dog pack"), ["NEW"])                      # "pack" inside a new request stays a request


class FollowUpHelpers(unittest.TestCase):
    def test_polarity_of_a_whole_message(self):
        self.assertEqual(polarity_of("this is bad"), "NEGATIVE")
        self.assertEqual(polarity_of("I love this one"), "POSITIVE")
        self.assertEqual(polarity_of("I don't like this"), "NEGATIVE")
        self.assertIsNone(polarity_of("make it happier"))

    def test_only_a_which_is_an_answer(self):
        for text in ("5", "number 5", "#5", "the third one", "2 and 7", "number three", "S4", "G12/S3"):
            self.assertTrue(is_sticker_answer(text), text)
        for text in ("make me a falcon", "falcon 5", "yes", "hello", "", "I like 5 but not 3 and the sixth is too small to see at all"):
            self.assertFalse(is_sticker_answer(text), text)
        self.assertTrue(is_sticker_answer("this one", has_selection=True))
        self.assertTrue(is_sticker_answer("these", has_selection=True))
        self.assertFalse(is_sticker_answer("this one", has_selection=False))               # nothing is selected: "this one" points at nothing


if __name__ == "__main__":
    unittest.main()
