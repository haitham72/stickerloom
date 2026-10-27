"""The reported G129 conversation: still-grounded streaming, display, and ambiguous edits. No providers."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stickerloom.agent import editroute, resolver
from stickerloom.services import llm
from stickerloom.vision import consent, describe
from tests.test_agent import Base
from tests.test_naming_vision import make
from tests.test_vision import png


class InteractionTests(Base):
    def setUp(self):
        super().setUp()
        self.seed("G129", subject="superhero kid in Paris with cape")
        session = self.sess()
        session["settings"]["allow_vlm"] = True
        self.store.save(session)

    def test_reported_sequence_selects_before_streaming_then_shows_all_nine(self):
        original = self.tools.describe
        snapshots = []

        def answer(gid, index, question, allowed=None, on_delta=None):
            before = self.sess()["messages"][-1]
            self.assertEqual(before["status"], "working")
            self.assertTrue(before["cards_first"])
            self.assertEqual([s["id"] for s in before["cards"][0]["stickers"]], [f"G129/S{index}"])
            self.assertEqual(before["text"], "")

            def receive(text):
                on_delta(text)
                snapshots.append(self.sess()["messages"][-1]["text"])

            return original(gid, index, question, allowed, receive)

        self.tools.describe = answer
        for question, index in [("what is the second sticker doing", 2), ("what is he doing", 2), ("what about 4th sticker", 4)]:
            msg = self.say(question)
            self.assertIn("looking down", msg["text"])
            self.assertEqual(self.sess()["focus"]["stickers"], [f"G129/S{index}"])
        self.assertEqual(len(snapshots), 6)
        self.assertTrue(snapshots[1].startswith(snapshots[0]))
        msg = self.say("show all of them", selected=["G129/S4"])
        self.assertEqual([s["id"] for s in msg["cards"][0]["stickers"]], [f"G129/S{i}" for i in range(1, 10)])
        self.assertIsNone(self.sess()["pending"])
        self.assertFalse(any(c[0] in ("create", "sheet_reference", "slice_reference") for c in self.tools.calls))

    def test_vision_permission_is_respected_and_confirmation_keeps_the_question(self):
        session = self.sess()
        session["settings"]["allow_vlm"] = None
        self.store.save(session)
        msg = self.say("what is the second sticker doing")
        self.assertIn("Allow AI vision", msg["text"])
        self.assertFalse(any(c[0] == "describe" for c in self.tools.calls))
        self.say("yes")
        self.assertEqual(next(c for c in self.tools.calls if c[0] == "describe")[3], "what is the second sticker doing")
        self.assertEqual(self.sess()["focus"]["stickers"], ["G129/S2"])
        session = self.sess()
        session["settings"]["allow_vlm"] = False
        self.store.save(session)
        msg = self.say("what about 4th sticker")
        self.assertIn("vision is off", msg["text"])
        self.assertEqual(len([c for c in self.tools.calls if c[0] == "describe"]), 1)

    def test_replacement_changes_only_the_target_and_reuses_its_pose(self):
        self.say("replace 2 with a cat")
        pending = self.sess()["pending"]
        self.assertEqual(len(pending["items"]), 1)
        item = pending["items"][0]
        self.assertEqual(item["regen_of"], "G129/S2")
        self.assertEqual(item["plan"]["subject"], "cat")
        self.assertEqual(item["plan"]["slots"]["cells"][0]["label"], "pose 2")
        self.assertFalse(item["refs"])
        self.assertFalse(any(c[0] == "create" for c in self.tools.calls))

    def test_missing_replacement_and_unknown_edits_ask_without_planning(self):
        for text in ("replace 2 with", "make 3 flabberagsser the unwhistlemended", "flabberagsser the unwhistlemended"):
            with self.subTest(text=text):
                msg = self.say(text)
                self.assertIn("?", msg["text"])
                self.assertIsNone(self.sess()["pending"])
                self.assertFalse(msg["cards"])

    def test_replacing_a_focused_sticker_does_not_redesign_the_whole_batch(self):
        session = self.sess()
        session["focus"]["stickers"] = ["G129/S2"]
        self.store.save(session)
        self.say("replace it with a cat")
        self.assertEqual([i["regen_of"] for i in self.sess()["pending"]["items"]], ["G129/S2"])

    def test_number_to_number_replacement_asks_copy_or_swap(self):
        msg = self.say("replace 2 with 4")
        self.assertIn("copy 4 into S2", msg["text"])
        self.assertIn("swap", msg["text"])
        self.assertIsNone(self.sess()["pending"])

    def test_replacement_name_does_not_compete_with_the_source_for_resolution(self):
        self.tools.gens["G129"]["stickers"][1]["key"] = "kid_standing_proud"
        self.tools.gens["G129"]["stickers"][3]["key"] = "cat_reading"
        self.say("replace the proud one with a cat")
        self.assertEqual([i["regen_of"] for i in self.sess()["pending"]["items"]], ["G129/S2"])

    def test_mixed_request_without_a_model_asks_instead_of_dropping_the_unknown_clause(self):
        msg = self.say("make 3 laugh and flabberagsser the unwhistlemended")
        self.assertIn("?", msg["text"])
        self.assertIsNone(self.sess()["pending"])
        self.assertFalse(any(c[0] == "create" for c in self.tools.calls))

    def test_model_uncertainty_beats_a_recognized_partial_change(self):
        self.agent.brain._complete = lambda system, user: (json.dumps({"clear": False, "confidence": 0.9,
            "understood": "You want S3 to laugh.", "clarification": "What does the remaining phrase mean?"}), {"model": "fake"})
        msg = self.say("make 3 laugh and flabberagsser the unwhistlemended")
        self.assertIn("remaining phrase", msg["text"])
        self.assertIsNone(self.sess()["pending"])
        self.agent.brain._complete = lambda system, user: (json.dumps({"clear": True, "confidence": 0.2,
            "case": "tweak", "change": "laugh"}), {"model": "fake"})
        self.assertIn("?", self.say("make 3 laugh")["text"])
        self.assertIsNone(self.sess()["pending"])

    def test_failed_inspection_preserves_the_card_and_explains_the_model_error(self):
        from stickerloom.agent.tools import ToolError
        with patch.object(self.tools, "describe", side_effect=ToolError("The requested model does not exist (HTTP 404).")):
            msg = self.say("what is 4 doing?")
        self.assertIn("requested model does not exist", msg["text"])
        self.assertEqual(msg["cards"][0]["stickers"][0]["id"], "G129/S4")
        self.assertEqual(msg["steps"][-1]["label"], "could not inspect the still")
        self.assertNotIn("described the still", msg["steps"][-1]["label"])

    def test_clear_laugh_request_still_plans_one_sticker(self):
        self.say("make 3 laugh")
        self.assertEqual(self.sess()["pending"]["items"][0]["regen_of"], "G129/S3")


class DescriptionTests(unittest.TestCase):
    def test_only_the_requested_still_reaches_vision_and_consent_is_required(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            gid = make(out)
            from stickerloom.flow import pipeline as pl
            image = png((10, 20, 30, 255))
            (pl.gen_dir(out, gid) / "slices/img-x-2.png").write_bytes(image)
            got = []

            def fake(system, user, **kw):
                self.assertIn("doing", user)
                self.assertEqual(len(kw["images"]), 1)
                kw["on_delta"]("He is ")
                kw["on_delta"]("reading a comic.")
                return "He is reading a comic.", {"model": "fake"}

            with patch("stickerloom.vision.describe._flatten", side_effect=lambda b: got.append(b) or b):
                chunks = []
                answer = describe.sticker(out, gid, 2, "what is he doing", allowed=True, complete=fake, on_delta=chunks.append)
                self.assertEqual(got, [image])
                self.assertEqual("".join(chunks), answer)
                with self.assertRaises(consent.ConsentRequired):
                    describe.sticker(out, gid, 1, "describe", allowed=False, complete=fake)
            self.assertEqual(len(got), 1)

    def test_sse_chunks_are_visible_during_the_request_and_thinking_stays_hidden(self):
        parts = ["<thi", "nk>private</think>", "He is ", "reading."]
        wire = b"".join(("data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": p,
            "reasoning_content": "private"}}]}) + "\n\n").encode() for p in parts) + b"data: [DONE]\n\n"
        response = io.BytesIO(wire)
        response.headers = {"content-type": "text/event-stream"}
        chunks = []
        with patch("urllib.request.urlopen", return_value=response):
            result = llm._post_stream("http://fake/chat/completions", {"model": "fake"}, None, 5, chunks.append)
        self.assertEqual(chunks, ["He is ", "reading."])
        self.assertEqual(result["choices"][0]["message"]["content"], "He is reading.")

    def test_partial_stream_failure_does_not_retry_on_a_fallback(self):
        def fail(prov, system, user, **kw):
            kw["on_delta"]("He is reading")
            raise llm.LLMError("interrupted")
        with patch.object(llm, "_complete_one", side_effect=fail) as call, patch.object(llm, "fallback_model", return_value="fallback"):
            with self.assertRaises(llm.LLMError):
                llm._complete_on("openai", "system", "question", on_delta=lambda text: None)
            self.assertEqual(call.call_count, 1)

    def test_cloud_vision_inherits_the_configured_model_unless_explicitly_overridden(self):
        from stickerloom.vision import judge
        with patch.object(llm, "_load_dotenv"), patch.object(llm, "preferred_cloud_model", return_value=""), patch.dict("os.environ", {"STICKERLOOM_VISION_PROVIDER": "openai", "STICKERLOOM_LLM_MODEL": "router-vision"}, clear=True):
            self.assertEqual(judge.target()["model"], "router-vision")
            with patch.dict("os.environ", {"STICKERLOOM_VISION_OPENAI_MODEL": "vision-override"}):
                self.assertEqual(judge.target()["model"], "vision-override")
                with patch.dict("os.environ", {"VISION_MODEL": "explicit-vision"}):
                    self.assertEqual(judge.target()["model"], "explicit-vision")
        with patch.dict("os.environ", {"STICKERLOOM_VISION_PROVIDER": "local"}, clear=True), patch.object(llm, "local_model", return_value="local-vision"):
            self.assertEqual(judge.target()["model"], "local-vision")

    def test_nonstreaming_endpoint_emits_one_chunk_or_a_shape_error(self):
        chunks = []
        for data, valid in (({"choices": [{"message": {"content": "He is reading."}}]}, True), ({"choices": []}, False)):
            response = io.BytesIO(json.dumps(data).encode())
            response.headers = {"content-type": "application/json"}
            with patch("urllib.request.urlopen", return_value=response):
                if valid:
                    llm._post_stream("http://fake/chat/completions", {"model": "fake"}, None, 5, chunks.append)
                else:
                    with self.assertRaises(llm.LLMError) as error:
                        llm._post_stream("http://fake/chat/completions", {"model": "fake"}, None, 5, chunks.append)
                    self.assertEqual(error.exception.kind, "shape")
        self.assertEqual(chunks, ["He is reading."])

    def test_display_words_are_not_edits_but_show_a_pose_can_still_be_an_edit(self):
        for text in ("show all of them", "show all", "list every sticker", "can you show me all the stickers?"):
            self.assertTrue(resolver.display_request(text), text)
            self.assertIsNone(editroute.classify_edit(text))
        self.assertEqual(editroute.classify_edit("show him playing football")["case"], "action")
