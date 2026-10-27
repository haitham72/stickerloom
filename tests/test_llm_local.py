"""The local model (LM Studio, vLLM: any OpenAI-compatible STICKERLOOM_LOCAL_URL) really works: the model is whatever the server lists, a readiness probe says whether it can answer, an empty
answer from a reasoning model is retried with room to think, and the chat says when it fell back to rules. Every test talks to a FAKE server on an ephemeral port (stdlib
http.server); none reaches the real LM Studio at localhost:1234 (the providers are pinned to `none` by tests/__init__.py, and a test that needs the local one sets it for itself)."""
import http.client
import json
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from stickerloom.console.assets import asset_path
from unittest import mock

from stickerloom.services import llm

LISTED = ["qwenlab-4b", "google/gemma-4-e2b", "text-embedding-nomic-embed-text-v1.5", "google/gemma-4-12b-qat", "qwen/qwenlab-9b", "qwen/qwenlab-4b-2507"]
NO_MODELS = "No models loaded. Please load a model in the developer page or use the `lms load` command."


def chat(content="OK", reasoning=None, finish="stop", model="m"):
    msg = {"role": "assistant", "content": content}
    if reasoning is not None:
        msg["reasoning_content"] = reasoning
    return {"id": "x", "model": model, "choices": [{"index": 0, "message": msg, "finish_reason": finish}], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}


class FakeLM:
    """An OpenAI-compatible server in a thread. `answerable` are the ids that answer a chat completion (LM Studio loads a listed model on first use; an id that is not listed, such as
    the instance suffix `qwenlab-4b:2` while no second copy is loaded, gets HTTP 400 "No models loaded"). `handler(body) -> dict | None` replaces the reply."""

    def __init__(self, models=None, answerable=None):
        self.models = list(LISTED if models is None else models)
        self.answerable = None if answerable is None else set(answerable)      # None: every listed id
        self.handler = None
        self.gets, self.posts = [], []
        self.refuse_all = False                                                  # every chat completion is the "No models loaded" 400 (nothing can be loaded)
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                raw = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                fake.gets.append(self.path)
                if self.path.endswith("/models"):
                    return self._send(200, {"object": "list", "data": [{"id": m, "object": "model", "owned_by": "organization_owner"} for m in fake.models]})
                self._send(404, {"error": {"message": "not found"}})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)).decode("utf-8"))
                fake.posts.append(body)
                ok = not fake.refuse_all and body.get("model") in (fake.models if fake.answerable is None else fake.answerable)
                if not ok:
                    return self._send(400, {"error": {"message": NO_MODELS, "type": "invalid_request_error", "param": "model", "code": "model_not_found"}})
                out = fake.handler(body) if fake.handler else None
                self._send(200, out or chat("OK", model=body.get("model")))

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


def closed_port_url() -> str:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}/v1"


KEYS = ("STICKERLOOM_LOCAL_URL", "STICKERLOOM_LOCAL_MODEL", "STICKERLOOM_LLM_PROVIDER", "STICKERLOOM_AGENT_PROVIDER", "STICKERLOOM_AI_BACKEND", "STICKERLOOM_OUT", "STICKERLOOM_LOCAL_MIN_TOKENS", "STICKERLOOM_LOCAL_PREFILL", llm.KEY_VAR)


class LocalCase(unittest.TestCase):
    """The env of a machine with LM Studio configured as `qwenlab-4b:2`, pointed at the fake server, with every cache empty; everything put back."""
    models = None

    def setUp(self):
        self.fake = FakeLM(self.models)
        self.td = tempfile.TemporaryDirectory()
        self.keep = {k: os.environ.get(k) for k in KEYS}
        os.environ.update(STICKERLOOM_LOCAL_URL=self.fake.url, STICKERLOOM_LOCAL_MODEL="qwenlab-4b:2", STICKERLOOM_LLM_PROVIDER="local", STICKERLOOM_OUT=self.td.name)
        for k in ("STICKERLOOM_AGENT_PROVIDER", "STICKERLOOM_AI_BACKEND", "STICKERLOOM_LOCAL_MIN_TOKENS", "STICKERLOOM_LOCAL_PREFILL", llm.KEY_VAR):
            os.environ.pop(k, None)
        self._loaded, llm._ENV_LOADED = llm._ENV_LOADED, True              # the developer's .env is not read
        self.reset()

    def reset(self):
        from stickerloom.agent import brain
        llm.reset_local()
        llm._local_up.update(t=0.0, ok=False, url=None)
        llm._sticky.update(prov=None, until=0.0)
        brain.reset_status()

    def tearDown(self):
        self.fake.stop()
        for k, v in self.keep.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        llm._ENV_LOADED = self._loaded
        self.reset()
        from stickerloom.agent.graph import Agent as _Ag
        _Ag.close_all()
        self.td.cleanup()

    def age(self, seconds):
        """Make every cache `seconds` older (the TTLs are real seconds; a test does not sleep)."""
        for d in (llm._models, llm._ready):
            d["t"] -= seconds
        llm._local_up["t"] -= seconds


class ModelListTests(LocalCase):
    def test_the_list_is_read_from_the_server_embeddings_left_out_and_the_order_kept(self):
        self.assertEqual(llm.list_local_models(), ["qwenlab-4b", "google/gemma-4-e2b", "google/gemma-4-12b-qat", "qwen/qwenlab-9b", "qwen/qwenlab-4b-2507"])

    def test_the_list_is_cached_for_30_seconds_and_force_reads_it_again(self):
        llm.list_local_models()
        llm.list_local_models()
        self.assertEqual(len(self.fake.gets), 1)
        self.age(29)
        llm.list_local_models()
        self.assertEqual(len(self.fake.gets), 1)
        self.age(2)                                                      # 31 s old
        llm.list_local_models()
        self.assertEqual(len(self.fake.gets), 2)
        llm.list_local_models(force=True)
        self.assertEqual(len(self.fake.gets), 3)

    def test_a_server_that_is_down_is_an_empty_list_quickly_and_silently(self):
        os.environ["STICKERLOOM_LOCAL_URL"] = closed_port_url()
        t0 = time.time()
        self.assertEqual(llm.list_local_models(), [])
        self.assertLess(time.time() - t0, llm.LOCAL_LIST_TIMEOUT + 1)

    def test_a_server_that_is_down_is_asked_again_when_it_comes_back(self):
        os.environ["STICKERLOOM_LOCAL_URL"] = closed_port_url()
        self.assertEqual(llm.list_local_models(), [])
        os.environ["STICKERLOOM_LOCAL_URL"] = self.fake.url
        self.assertTrue(llm.list_local_models())                        # another address is never answered from the old cache
        self.assertEqual(llm.list_local_models(force=True)[0], "qwenlab-4b")

    def test_a_model_list_is_never_asked_when_the_process_is_told_not_to_use_the_local_model(self):
        for p in ("none", "openai"):
            os.environ["STICKERLOOM_LLM_PROVIDER"] = p
            llm.reset_local()
            self.assertEqual(llm.list_local_models(), [])
            self.assertFalse(llm.local_ready()["ok"])
            self.assertIn("STICKERLOOM_LLM_PROVIDER", llm.local_ready()["why"])
        self.assertEqual((self.fake.gets, self.fake.posts), ([], []))


class ResolveTests(LocalCase):
    def test_the_configured_id_is_used_when_the_server_lists_it(self):
        os.environ["STICKERLOOM_LOCAL_MODEL"] = "google/gemma-4-e2b"
        self.assertEqual(llm.resolve_local_model(), "google/gemma-4-e2b")

    def test_the_instance_suffix_falls_back_to_the_base_id(self):
        """`qwenlab-4b:2` only exists while a second copy is loaded; the same request for `qwenlab-4b` just works (LM Studio loads it on first use)."""
        self.assertEqual(llm.resolve_local_model(), "qwenlab-4b")
        self.assertEqual(llm.local_model(), "qwenlab-4b")

    def test_a_listed_suffixed_id_is_kept(self):
        self.fake.models = ["qwenlab-4b", "qwenlab-4b:2"]
        self.assertEqual(llm.resolve_local_model(force=True), "qwenlab-4b:2")

    def test_an_id_the_server_does_not_have_falls_back_to_the_first_chat_model(self):
        os.environ["STICKERLOOM_LOCAL_MODEL"] = "gone/forever"
        self.fake.models = ["text-embedding-nomic-embed-text-v1.5", "google/gemma-4-e2b", "qwenlab-4b"]
        self.assertEqual(llm.resolve_local_model(force=True), "google/gemma-4-e2b")

    def test_with_the_server_down_the_configured_id_stays_and_nothing_hangs(self):
        os.environ["STICKERLOOM_LOCAL_URL"] = closed_port_url()
        self.assertEqual(llm.resolve_local_model(), "qwenlab-4b:2")

    def test_no_configured_or_listed_model_resolves_empty_instead_of_crashing(self):
        """No hard-coded local default remains: with the server down and nothing
        configured, the wish is empty (the server's own list decides)."""
        os.environ.pop("STICKERLOOM_LOCAL_MODEL")
        os.environ["STICKERLOOM_LOCAL_URL"] = closed_port_url()
        self.assertEqual(llm.resolve_local_model(), "")

    def test_the_persons_pick_wins_while_it_is_listed_and_is_kept_in_the_backend_file(self):
        self.assertEqual(llm.set_local_model("google/gemma-4-e2b"), "google/gemma-4-e2b")
        self.assertEqual(llm.resolve_local_model(), "google/gemma-4-e2b")
        saved = json.loads((Path(self.td.name) / "ai_backend.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["model"], "google/gemma-4-e2b")
        llm.set_preference("local")                                       # choosing the backend does not forget the model
        self.assertEqual(llm.preferred_model(), "google/gemma-4-e2b")
        self.assertEqual(llm.preference(), "local")
        self.fake.models = ["qwenlab-4b"]                                 # the picked model is gone from the server: back to the configured one
        self.assertEqual(llm.resolve_local_model(force=True), "qwenlab-4b")

    def test_a_model_the_server_does_not_list_cannot_be_picked(self):
        with self.assertRaises(llm.LLMError) as cm:
            llm.set_local_model("nope/not-here")
        self.assertIn("qwenlab-4b", str(cm.exception))
        self.assertEqual(llm.preferred_model(), "")
        with self.assertRaises(llm.LLMError):
            llm.set_local_model("text-embedding-nomic-embed-text-v1.5")  # an embedding model is never a chat model


class ProbeTests(LocalCase):
    def test_a_model_that_answers_is_ready_and_the_probe_is_tiny(self):
        r = llm.local_ready()
        self.assertEqual((r["ok"], r["model"], r["why"]), (True, "qwenlab-4b", None))
        self.assertEqual(len(self.fake.posts), 1)
        body = self.fake.posts[0]
        self.assertEqual(body["model"], "qwenlab-4b")
        self.assertLessEqual(body["max_tokens"], 64)
        self.assertEqual(body["reasoning_effort"], "none")

    def test_the_answer_is_cached_60_seconds_when_ok(self):
        llm.local_ready()
        llm.local_ready()
        self.assertEqual(len(self.fake.posts), 1)
        self.age(59)
        llm.local_ready()
        self.assertEqual(len(self.fake.posts), 1)
        self.age(2)
        llm.local_ready()
        self.assertEqual(len(self.fake.posts), 2)
        llm.local_ready(force=True)
        self.assertEqual(len(self.fake.posts), 3)

    def test_a_model_that_cannot_answer_says_why_in_plain_words_and_is_cached_15_seconds(self):
        self.fake.refuse_all = True
        r = llm.local_ready()
        self.assertFalse(r["ok"])
        self.assertEqual(r["model"], "qwenlab-4b")
        self.assertIn("LM Studio is running but the model could not answer", r["why"])
        self.assertIn("No models loaded", r["why"])
        self.assertIn("Load qwenlab-4b in LM Studio", r["why"])
        self.assertIn("Just-in-Time", r["why"])
        n = len(self.fake.posts)
        llm.local_ready()
        self.assertEqual(len(self.fake.posts), n)                           # not asked again on every poll
        self.age(16)
        self.fake.refuse_all = False
        self.assertTrue(llm.local_ready()["ok"])                            # a failure is only remembered briefly

    def test_a_reasoning_model_that_spends_the_probe_budget_thinking_is_still_ready(self):
        """It answered (HTTP 200) with nothing inside 32 tokens: it is loaded and serving, which is all the probe asks."""
        self.fake.handler = lambda body: chat("", reasoning="hmm", finish="length")
        self.assertTrue(llm.local_ready()["ok"])
        self.assertEqual(len(self.fake.posts), 1)                           # and the probe did not go on to spend a bigger budget

    def test_a_server_that_is_down_says_so(self):
        os.environ["STICKERLOOM_LOCAL_URL"] = closed_port_url()
        r = llm.local_ready()
        self.assertFalse(r["ok"])
        self.assertIn("LM Studio is not answering", r["why"])

    def test_the_probe_never_waits_longer_than_about_20_seconds(self):
        seen = []

        def post(url, body, key, timeout):
            seen.append(timeout)
            return chat("OK")
        with mock.patch.object(llm, "_post", post):
            llm.local_ready()
        self.assertTrue(seen and max(seen) <= 20)

    def test_availability_uses_the_probe_not_the_model_list(self):
        self.fake.refuse_all = True
        a = llm.availability()["local"]
        self.assertFalse(a["ok"])
        self.assertEqual(a["model"], "qwenlab-4b")
        self.assertIn("could not answer", a["why"])
        n = len(self.fake.posts)
        llm.availability()
        llm.availability()
        self.assertEqual(len(self.fake.posts), n)                           # polling does not probe again
        self.fake.refuse_all = False
        llm.local_ready(force=True)
        self.assertTrue(llm.availability()["local"]["ok"])
        self.assertIsNone(llm.availability()["local"]["why"])


    def test_the_health_check_never_asks_the_model(self):
        """`/api/health` is polled by whatever watches the app: it reports the last probe's answer (whatever its age), or whether anything listens, and never waits for a model."""
        from stickerloom.runtime import health
        self.assertTrue(llm.availability(probe=False)["local"]["ok"])           # nothing probed yet: something listens
        self.assertEqual(self.fake.posts, [])
        self.fake.refuse_all = True
        llm.local_ready()
        n = len(self.fake.posts)
        self.age(1000)                                                          # long stale
        self.fake.refuse_all = False
        a = llm.availability(probe=False)["local"]
        self.assertFalse(a["ok"])
        self.assertIn("could not answer", a["why"])
        self.assertFalse(health.models()["llm"]["availability"]["local"]["ok"])
        self.assertEqual(len(self.fake.posts), n)


class TokenHandlingTests(LocalCase):
    def _two_sizes(self, requested, model="google/gemma-4-e2b", **kw):
        def reply(body):
            return chat("", reasoning="thinking about it", finish="length") if body["max_tokens"] <= requested else chat("the answer")
        self.fake.handler = reply
        text, meta = llm._complete_on("local", "sys", "user", max_tokens=requested, model_=model, **kw)
        return text, meta, [p["max_tokens"] for p in self.fake.posts]

    def test_an_empty_answer_is_retried_once_with_a_bigger_budget(self):
        text, meta, sizes = self._two_sizes(100)
        self.assertEqual(text, "the answer")
        self.assertEqual(sizes, [100, llm.LOCAL_MIN_TOKENS])

    def test_the_bigger_budget_is_four_times_the_request_when_that_is_more_than_the_floor(self):
        text, meta, sizes = self._two_sizes(1000)
        self.assertEqual(sizes, [1000, 4000])

    def test_the_floor_is_the_one_the_environment_sets(self):
        os.environ["STICKERLOOM_LOCAL_MIN_TOKENS"] = "3000"
        text, meta, sizes = self._two_sizes(100)
        self.assertEqual(sizes, [100, 3000])

    def test_a_model_that_returned_reasoning_but_no_answer_gets_a_closed_think_block_on_the_retry(self):
        self._two_sizes(100)
        first, second = self.fake.posts
        self.assertEqual(first["messages"][-1]["role"], "user")             # a gemma is not prefilled up front
        self.assertEqual(second["messages"][-1], {"role": "assistant", "content": llm.CLOSED_THINK})

    def test_a_qwen_keeps_its_prefill_on_both_tries_and_never_gets_two(self):
        self._two_sizes(100, model="qwenlab-4b")
        for body in self.fake.posts:
            self.assertEqual([m["role"] for m in body["messages"]], ["system", "user", "assistant"])

    def test_a_qwen_with_a_vendor_prefix_is_a_qwen_too(self):
        """`qwen/qwenlab-9b` (the LM Studio catalogue id) thinks exactly like `qwenlab-4b`; the prefill used to look at the start of the whole id."""
        self.assertTrue(llm._prefill("qwen/qwenlab-9b"))
        self.assertTrue(llm._prefill("qwenlab-4b:2"))
        self.assertFalse(llm._prefill("google/gemma-4-e2b"))

    def test_an_answer_is_never_retried(self):
        self.fake.handler = lambda body: chat("fine")
        llm._complete_on("local", "s", "u", max_tokens=100, model_="google/gemma-4-e2b")
        self.assertEqual(len(self.fake.posts), 1)

    def test_still_empty_after_the_retry_is_an_error_not_an_empty_string(self):
        self.fake.handler = lambda body: chat("", reasoning="...", finish="length")
        with self.assertRaises(llm.LLMError):
            llm._complete_on("local", "s", "u", max_tokens=100, model_="google/gemma-4-e2b")
        self.assertEqual(len(self.fake.posts), 2)                           # exactly one retry

    def test_the_cloud_is_never_retried_this_way(self):
        calls = []

        def post(url, body, key, timeout):
            calls.append(body)
            return chat("", finish="stop")
        os.environ[llm.KEY_VAR] = "sk-test"
        with mock.patch.object(llm, "_post", post):
            llm._complete_on("openai", "s", "u", max_tokens=100)
        self.assertEqual(len(calls), 1)

    def test_a_request_without_a_model_asks_the_model_the_server_has(self):
        text, meta = llm._complete_on("local", "s", "u")
        self.assertEqual(self.fake.posts[0]["model"], "qwenlab-4b")        # never the unstable `:2`
        self.assertEqual(meta["model"], "qwenlab-4b")

    def test_the_model_ledger_carries_the_resolved_id(self):
        from stickerloom.agent.brain import Brain
        os.environ["STICKERLOOM_AGENT_PROVIDER"] = "local"
        out = Path(self.td.name)
        self.assertEqual(Brain(out=out)._ask("LLM_ANSWER", "s", "u"), "OK")
        rows = [json.loads(x) for x in (out / "model_calls.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(r["kind"], r["provider"], r["model"], r["status"]) for r in rows], [("LLM_ANSWER", "local", "qwenlab-4b", "OK")])

    def test_a_failed_call_is_in_the_ledger_with_the_model_and_the_reason(self):
        from stickerloom.agent.brain import Brain
        os.environ["STICKERLOOM_AGENT_PROVIDER"] = "local"
        self.fake.refuse_all = True
        out = Path(self.td.name)
        b = Brain(out=out)
        self.assertIsNone(b._ask("LLM_ANSWER", "s", "u"))
        row = json.loads((out / "model_calls.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual((row["model"], row["status"]), ("qwenlab-4b", "ERROR"))
        self.assertIn("No models loaded", row["error"])
        self.assertIn("No models loaded", b.last_error)


class BrainHonestyTests(LocalCase):
    def test_the_agent_status_says_when_the_chat_is_on_rules_and_why(self):
        from stickerloom.agent import brain
        os.environ["STICKERLOOM_AGENT_PROVIDER"] = "local"
        self.fake.refuse_all = True
        s = brain.status()
        self.assertTrue(s["fallback"])
        self.assertIn("could not answer", s["reason"])
        self.fake.refuse_all = False
        llm.local_ready(force=True)
        self.assertEqual(brain.status(), {"fallback": False, "reason": None})

    def test_the_agent_status_with_no_model_at_all_is_rules_with_a_reason(self):
        from stickerloom.agent import brain
        os.environ["STICKERLOOM_AGENT_PROVIDER"] = "none"
        os.environ["STICKERLOOM_LLM_PROVIDER"] = "none"
        s = brain.status()
        self.assertTrue(s["fallback"])
        self.assertTrue(s["reason"])

    def test_a_turn_the_model_failed_on_says_it_was_answered_by_rules(self):
        from stickerloom.runtime import cache as cachemod
        from stickerloom.agent.brain import Brain
        from stickerloom.agent.graph import Agent
        from stickerloom.agent.memory import SessionStore
        from stickerloom.agent.tools import FakeTools

        def failing(system, user):
            raise llm.LLMError("The AI service refused the request (HTTP 400): " + NO_MODELS)
        cache = cachemod.Cache(force_memory=True)
        store = SessionStore(Path(self.td.name), cache)
        agent = Agent(store, FakeTools(), Brain(complete=failing), cache)
        self.addCleanup(agent.close)
        sid = store.create()["id"]
        m = agent.run_turn(sid, "blorp zzz qqq vvv www xxx yyy zzz aaa bbb ccc")      # the rules are unsure: the model is asked, and cannot answer
        notes = [s for s in m["steps"] if s["kind"] == "note" and s["label"].startswith("answered by rules")]
        self.assertEqual(len(notes), 1, m["steps"])
        self.assertIn("No models loaded", notes[0]["label"])
        self.assertEqual(m["steps"][-1]["kind"], "final")                             # the note sits before the ending step, which keeps the collapsed summary's label
        again = agent.run_turn(sid, "hello")                                          # a turn the model was not needed for says nothing
        self.assertFalse([s for s in again["steps"] if s["label"].startswith("answered by rules")])


class RouteTests(LocalCase):
    """Through the real console server: GET /api/llm/models, POST /api/ai/backend {model}, and the honesty fields of GET /api/chat/agent."""

    def setUp(self):
        super().setUp()
        from stickerloom.console.server import serve
        from stickerloom.engine.config import EngineConfig
        from stickerloom.generation import higgsfield
        from tests.test_console import build_inputs
        os.environ["STICKERLOOM_AGENT_PROVIDER"] = "local"
        self.patch = mock.patch.object(higgsfield, "available", lambda: False)
        self.patch.start()
        self.tmp = Path(tempfile.mkdtemp())
        build_inputs(self.tmp / "in")
        os.environ["STICKERLOOM_OUT"] = str(self.tmp / "out")
        self.srv, self.c = serve(self.tmp / "out", self.tmp / "in", 0, cfg=EngineConfig(min_sheet_px=256), block=False)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.c.release_writer()
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def req(self, method, path, body=None):
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
        r = h.getresponse()
        raw = r.read()
        h.close()
        return r.status, json.loads(raw)

    def test_the_models_route_lists_what_the_server_has_and_says_which_one_runs(self):
        s, j = self.req("GET", "/api/llm/models")
        self.assertEqual(s, 200)
        self.assertEqual([m["id"] for m in j["models"]], ["qwenlab-4b", "google/gemma-4-e2b", "google/gemma-4-12b-qat", "qwen/qwenlab-9b", "qwen/qwenlab-4b-2507"])
        self.assertEqual((j["current"], j["preference"], j["ok"], j["why"]), ("qwenlab-4b", "auto", True, None))
        by = {m["id"]: m["loaded"] for m in j["models"]}
        self.assertIs(by["qwenlab-4b"], True)                               # the probe just got an answer from it
        self.assertIsNone(by["google/gemma-4-e2b"])                         # the list does not say which others are loaded

    def test_the_models_route_says_why_when_the_model_cannot_answer(self):
        self.fake.refuse_all = True
        s, j = self.req("GET", "/api/llm/models")
        self.assertEqual(s, 200)
        self.assertFalse(j["ok"])
        self.assertIn("could not answer", j["why"])
        self.assertTrue(j["models"])

    def test_a_member_may_read_the_list_but_only_an_owner_may_pick(self):
        from stickerloom.console import server
        self.assertIn("/api/llm/models", server.MEMBER_GET)
        self.assertNotIn("/api/ai/backend", server.MEMBER_POST)

    def test_the_backend_route_takes_a_model_and_the_chat_uses_it(self):
        s, j = self.req("POST", "/api/ai/backend", {"model": "google/gemma-4-e2b"})
        self.assertEqual(s, 200)
        s, j = self.req("GET", "/api/llm/models")
        self.assertEqual((j["current"], j["preference"]), ("google/gemma-4-e2b", "auto"))
        s, j = self.req("GET", "/api/chat/agent")
        self.assertEqual(j["agent"], {"provider": "local", "model": "google/gemma-4-e2b"})
        s, j = self.req("POST", "/api/ai/backend", {"backend": "local", "model": "qwenlab-4b"})      # both in one call
        self.assertEqual(s, 200)
        self.assertEqual(self.req("GET", "/api/llm/models")[1]["preference"], "local")

    def test_a_model_that_is_not_listed_is_a_400_with_the_list(self):
        s, j = self.req("POST", "/api/ai/backend", {"model": "nope/not-here"})
        self.assertEqual(s, 400)
        self.assertEqual(j["models"], ["qwenlab-4b", "google/gemma-4-e2b", "google/gemma-4-12b-qat", "qwen/qwenlab-9b", "qwen/qwenlab-4b-2507"])
        self.assertIn("nope/not-here", j["error"])
        s, j = self.req("POST", "/api/ai/backend", {})
        self.assertEqual(s, 400)                                             # neither a backend nor a model: still the old refusal

    def test_the_agent_route_reports_the_fallback_and_the_real_reason(self):
        s, j = self.req("GET", "/api/chat/agent")
        self.assertEqual(j["agent_status"], {"fallback": False, "reason": None})
        self.assertTrue(j["availability"]["local"]["ok"])
        self.fake.refuse_all = True
        llm.local_ready(force=True)
        s, j = self.req("GET", "/api/chat/agent")
        self.assertTrue(j["agent_status"]["fallback"])
        self.assertIn("could not answer", j["agent_status"]["reason"])
        self.assertFalse(j["availability"]["local"]["ok"])                   # it used to say ok: true because only the model LIST was asked
        self.assertIn("No models loaded", j["availability"]["local"]["why"])


class UiWiringTests(unittest.TestCase):
    """The page half (console/agent.js, agent.css) has no browser here: what it must contain is pinned as text; the pure helpers (`AIU.engine`, `AIU.modelNote`) run under node (tests/js/agent.test.js)."""

    @staticmethod
    def block(src, marker):
        rest = src[src.index(marker):]
        out = [rest.split("\n")[0]]
        for ln in rest.split("\n")[1:]:
            if ln and not ln[0].isspace():
                break
            out.append(ln)
        return "\n".join(out)

    def setUp(self):
        ui = Path(__file__).resolve().parent.parent / "stickerloom" / "console"
        self.js = asset_path("agent.js").read_text(encoding="utf-8")
        self.css = asset_path("agent.css").read_text(encoding="utf-8")

    def test_the_engine_row_has_a_dropdown_fed_by_the_server_without_a_count_limit(self):
        row = self.block(self.js, "function modelRow()")
        self.assertIn("m.models.map(", row)
        self.assertNotIn(".slice(", row, "every model the server lists is offered")
        self.assertIn("data-agmodel", row)
        self.assertIn("/api/llm/models", self.js)
        self.assertIn("AIU.modelNote(m)", row, "the one-line status comes from the server's own reason")
        self.assertIn("modelRow()", self.block(self.js, "function beRow()"), "the dropdown is part of the engine row")
        self.assertIn("{model:s.value}", self.js, "a pick is saved through POST /api/ai/backend")
        self.assertIn("'/api/ai/backend'", self.js)
        self.assertIn(".ag-sel", self.css, "the dropdown is styled in agent.css, in the namespace")

    def test_the_pill_says_why_the_chat_is_on_rules(self):
        self.assertIn("AIU.engine(A.agent)", self.block(self.js, "function pill()"))
        self.assertIn("Rules only (local model not loaded)", self.js)

    def test_the_first_paint_does_not_wait_for_the_readiness_probe(self):
        self.assertNotIn("Promise.all([loadSessions(),loadAgent()])", self.js, "GET /api/chat/agent carries the probe, which can take a moment while the server loads the model")


if __name__ == "__main__":
    unittest.main()
