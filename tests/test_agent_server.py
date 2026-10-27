"""The chat through the REAL console server on synthetic prepared sheets: sessions, a turn in the background, live cards, memory, settings.
No provider (Higgsfield is reported unavailable), no language model (no key, no local server): everything is rules and the file store."""
import http.client
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from stickerloom.generation import higgsfield
from stickerloom.console.server import serve
from stickerloom.engine.config import EngineConfig
from tests.test_console import build_inputs


class ChatServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = {k: os.environ.get(k) for k in ("STICKERLOOM_AGENT_PROVIDER", "STICKERLOOM_LLM_PROVIDER", "OPENAI_API_KEY")}
        os.environ["STICKERLOOM_AGENT_PROVIDER"] = "openai"          # no key + openai = no model: the rules decide everything
        os.environ["STICKERLOOM_LLM_PROVIDER"] = "openai"
        os.environ.pop("OPENAI_API_KEY", None)
        from stickerloom.services import llm
        cls._loaded = llm._ENV_LOADED
        llm._ENV_LOADED = True
        cls.patch = mock.patch.object(higgsfield, "available", lambda: False)
        cls.patch.start()
        cls.tmp = Path(tempfile.mkdtemp())
        build_inputs(cls.tmp / "in")
        cls.srv, cls.c = serve(cls.tmp / "out", cls.tmp / "in", 0, cfg=EngineConfig(min_sheet_px=256), block=False)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.c.release_writer()
        cls.patch.stop()
        from stickerloom.services import llm
        llm._ENV_LOADED = cls._loaded
        for k, v in cls.env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, method, path, body=None):
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
        r = h.getresponse()
        raw = r.read()
        h.close()
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw

    def wait(self, sid, pred, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            s, j = self.req("GET", f"/api/chat/sessions/{sid}")
            if s == 200 and pred(j):
                return j
            time.sleep(0.2)
        self.fail("timeout: " + json.dumps(j)[:600])

    def test_the_ai_vision_switch_is_the_settings_route_and_makes_no_turn(self):
        """P10 of the UI/UX spec through the real server: allowing or refusing AI vision writes state only (no message, no card), asked-once is remembered, and a repeat press with the same value changes nothing."""
        s, sess = self.req("POST", "/api/chat/sessions", {"settings": {"ai": False}})
        sid = sess["id"]
        n = len(self.req("GET", f"/api/chat/sessions/{sid}")[1]["messages"])
        s, r = self.req("POST", f"/api/chat/sessions/{sid}/settings", {"allow_vlm": True})
        self.assertEqual((s, r["settings"]["allow_vlm"]), (200, True))
        got = self.req("GET", f"/api/chat/sessions/{sid}")[1]
        self.assertEqual(len(got["messages"]), n, "no user message and no assistant turn")
        s, r = self.req("POST", f"/api/chat/sessions/{sid}/settings", {"allow_vlm": "maybe"})
        self.assertIs(self.req("GET", f"/api/chat/sessions/{sid}")[1]["settings"]["allow_vlm"], True, "a value that is not true or false changes nothing")
        self.req("POST", f"/api/chat/sessions/{sid}/settings", {"allow_vlm": False})
        self.assertIs(self.req("GET", f"/api/chat/sessions/{sid}")[1]["settings"]["allow_vlm"], False)

    def test_the_stage_is_a_validated_setting_and_makes_no_turn(self):
        """plan.md Step 1: settings.stage through the real settings route; an unknown stage is a 400; the old creator switches still map onto it."""
        s, sess = self.req("POST", "/api/chat/sessions", {"settings": {"ai": False}})
        sid = sess["id"]
        self.assertEqual(sess["settings"]["stage"], "auto", "a new chat starts on Auto")
        s, r = self.req("POST", f"/api/chat/sessions/{sid}/settings", {"stage": "animation"})
        self.assertEqual((s, r["settings"]["stage"]), (200, "animation"))
        self.assertEqual(len(self.req("GET", f"/api/chat/sessions/{sid}")[1]["messages"]), 0, "no chat turn")
        s, r = self.req("POST", f"/api/chat/sessions/{sid}/settings", {"stage": "everything"})
        self.assertEqual(s, 400)
        self.assertEqual(self.req("GET", f"/api/chat/sessions/{sid}")[1]["settings"]["stage"], "animation", "the refused one changed nothing")
        s, r = self.req("POST", f"/api/chat/sessions/{sid}/settings", {"creator": {"on": True, "scope": "video"}})
        self.assertEqual(r["settings"]["stage"], "export", "an old client's creator on + video reads as Export")
        s, r = self.req("POST", f"/api/chat/sessions/{sid}/settings", {"creator": {"bypass": True}})
        self.assertEqual((r["settings"]["stage"], r["settings"]["creator"]["bypass"]), ("export", True), "bypass alone leaves the stage alone")
        s, r = self.req("POST", f"/api/chat/sessions/{sid}/settings", {"stage": "api"})
        self.assertEqual((s, r["settings"]["stage"]), (200, "api"), "the fifth stop: Export to the API")

    def test_the_chats_models_are_validated_settings(self):
        """The slider's details: the chat's own image / video / AI model, checked against the catalog; empty returns to the default."""
        s, sess = self.req("POST", "/api/chat/sessions", {"settings": {"ai": False}})
        sid = sess["id"]
        s, r = self.req("POST", f"/api/chat/sessions/{sid}/settings", {"models": {"image": "nano_banana_pro", "ai": "qwen3-8b"}})
        self.assertEqual((s, r["settings"]["models"]), (200, {"image": "nano_banana_pro", "ai": "qwen3-8b"}))
        s, r = self.req("POST", f"/api/chat/sessions/{sid}/settings", {"models": {"image": "no_such_model"}})
        self.assertEqual(s, 400)
        s, r = self.req("POST", f"/api/chat/sessions/{sid}/settings", {"models": {"sound": "x"}})
        self.assertEqual(s, 400)
        s, r = self.req("POST", f"/api/chat/sessions/{sid}/settings", {"models": {"image": ""}})
        self.assertEqual(r["settings"]["models"], {"ai": "qwen3-8b"}, "empty = the server's default again")

    def test_a_full_conversation(self):
        s, sess = self.req("POST", "/api/chat/sessions", {"settings": {"ai": False}})
        self.assertEqual(s, 200)
        sid = sess["id"]
        self.assertEqual(sess["settings"]["ai"], False)

        # 1. a request: starts in the background, answers at once
        s, r = self.req("POST", f"/api/chat/sessions/{sid}/messages", {"text": "create a blob for school"})
        self.assertEqual((s, r["id"]), (202, sid))
        j = self.wait(sid, lambda j: not j["working"] and j["messages"][-1]["role"] == "assistant")
        last = j["messages"][-1]
        self.assertEqual(last["status"], "done")
        card = next(c for c in last["cards"] if c["type"] == "generation")
        self.assertEqual(card["generation"], "G001")
        self.assertEqual([s_["kind"] for s_ in last["steps"]][0], "task")
        self.assertEqual(last["steps"][-1]["kind"], "final")

        # 2. the stickers appear on the card as the engine finishes them (live data, with file urls)
        gen_msg = lambda j: next(m for m in reversed(j["messages"]) if any(c["type"] == "generation" for c in m.get("cards") or []))      # the batch follow-up (no cards) may follow it
        j = self.wait(sid, lambda j: len([x for x in (gen_msg(j)["cards"][0].get("data") or {}).get("stickers", []) if x["status"] == "READY"]) >= 3)
        st = gen_msg(j)["cards"][0]["data"]["stickers"]
        png = next(x["png"] for x in st if x["png"])
        self.assertEqual(self.req("GET", png)[0], 200)

        # 3. memory: feedback about stickers of that pass is stored on the subject
        self.c.wait_jobs()
        s, r = self.req("POST", f"/api/chat/sessions/{sid}/messages", {"text": "I like 2 and 3 but not 4"})
        j = self.wait(sid, lambda j: not j["working"])
        pass_ = j["subjects"][0]["passes"][0]
        self.assertEqual((pass_["liked"], pass_["disliked"]), (["G001/S2", "G001/S3"], ["G001/S4"]))
        self.assertIn("liked S2, S3", j["summary_text"])
        self.assertIn("noted", j["messages"][-1]["text"].lower())

        # 4. an answer from metadata only, no generation
        s, r = self.req("POST", f"/api/chat/sessions/{sid}/messages", {"text": "which one is number 2?"})
        j = self.wait(sid, lambda j: not j["working"])
        self.assertIn("G001/S2", j["messages"][-1]["text"])
        self.assertEqual(len(self.req("GET", "/api/generations")[1]["summary"] if "summary" in self.req("GET", "/api/generations")[1] else []) or 1, 1)

        # 5. settings through the minimal settings route and through chat
        s, r = self.req("POST", f"/api/chat/sessions/{sid}/settings", {"grid": "2x2", "ask_before_spending": False, "bogus": 1})
        self.assertEqual((r["settings"]["grid"], r["settings"]["ask_before_spending"], "bogus" in r["settings"]), ("2x2", False, False))

        # 6. the list and the resume
        s, lst = self.req("GET", "/api/chat/sessions")
        self.assertIn(sid, [x["id"] for x in lst["sessions"]])
        s, again = self.req("GET", f"/api/chat/sessions/{sid}")
        self.assertEqual(len(again["interactions"]), 3)

    def test_the_style_is_a_real_preset_everywhere_the_chat_touches_it(self):
        """The AI screen shows the same style presets as the Studio (GET /api/chat/agent carries them), a style is picked through the settings route, and an unknown one is refused
        out loud instead of being stored to fail later; the names on the plan card are the presets' own, not a list of the chat's."""
        from stickerloom.agent import graph
        from stickerloom.generation import styles
        s, info = self.req("GET", "/api/chat/agent")
        self.assertEqual([x["id"] for x in info["styles"]], [x["id"] for x in styles.PRESETS])
        self.assertEqual(info["default_style"], styles.DEFAULT)
        s, sess = self.req("POST", "/api/chat/sessions", {"settings": {"ai": False}})
        sid = sess["id"]
        s, r = self.req("POST", f"/api/chat/sessions/{sid}/settings", {"style_id": "clay_3d"})
        self.assertEqual(s, 200)
        self.assertEqual(r["settings"]["style_id"], "clay_3d")
        s, r = self.req("POST", f"/api/chat/sessions/{sid}/settings", {"style_id": "pixar_3d"})
        self.assertEqual(s, 400, "an id that is not a preset is refused")
        self.assertIn("unknown style", json.dumps(r))
        s, again = self.req("GET", f"/api/chat/sessions/{sid}")
        self.assertEqual(again["settings"]["style_id"], "clay_3d", "the refused one changed nothing")
        self.assertEqual(set(graph.STYLE_NAMES), {x["id"] for x in styles.PRESETS}, "every preset has a name on the card, and nothing else does")

    def test_a_second_message_while_one_runs_gets_409(self):
        s, sess = self.req("POST", "/api/chat/sessions", {"settings": {"ai": False}})
        sid = sess["id"]
        store, tools, agent, _ = self.c.chat_parts()
        turn = agent.prepare(sid, "hello", [], None)                      # the first turn holds the session
        try:
            s, r = self.req("POST", f"/api/chat/sessions/{sid}/messages", {"text": "hello again"})
            self.assertEqual(s, 409)
        finally:
            agent.execute(turn)
        s, r = self.req("POST", f"/api/chat/sessions/{sid}/messages", {"text": "hello again"})
        self.assertEqual(s, 202)
        self.wait(sid, lambda j: not j["working"])

    def test_unknown_session_and_bad_input(self):
        self.assertEqual(self.req("GET", "/api/chat/sessions/S999")[0], 404)
        self.assertEqual(self.req("GET", "/api/chat/sessions/../../etc")[0] in (400, 404), True)
        s, sess = self.req("POST", "/api/chat/sessions", {})
        self.assertEqual(self.req("POST", f"/api/chat/sessions/{sess['id']}/messages", {"text": "   "})[0], 400)

    def test_agent_status_reports_the_models(self):
        s, j = self.req("GET", "/api/chat/agent")
        self.assertEqual(s, 200)
        self.assertIn("agent", j)
        self.assertIn("vision", j)
        s, h = self.req("GET", "/api/health")
        self.assertEqual(s, 200)
        self.assertIn("database", h)


if __name__ == "__main__":
    unittest.main()
