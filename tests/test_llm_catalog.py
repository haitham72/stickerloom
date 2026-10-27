"""Cloud catalog/selection and Luna routing on fake responses only."""
import os
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stickerloom.services import embed, llm, llm_catalog as catalog
from stickerloom.agent import brain
from stickerloom.vision import judge

FALLBACK_URL = "https://fallback.example/v1"
OFFICIAL_URL = "https://api.openai.com/v1"
# Whatever the generic fallback endpoint lists is shown (no curated list is
# configured in these tests); the official OpenAI set stays curated.
FALLBACK_LISTED = ["pixel-1-flash", "pixel-2-flash", "atlas-draft"]


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.env = mock.patch.dict(os.environ, {
            "STICKERLOOM_OUT": self.td.name, "STICKERLOOM_LLM_PROVIDER": "openai", "STICKERLOOM_AGENT_PROVIDER": "auto",
            "STICKERLOOM_VISION_PROVIDER": "auto", "STICKERLOOM_FALLBACK_URL": FALLBACK_URL,
            "STICKERLOOM_FALLBACK_KEY": "fallback-key", "OPENAI_API_KEY": "direct-key",
        }, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        p = mock.patch.object(llm, "_ENV_LOADED", True)
        p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(llm, "list_local_models", return_value=["qwen:2"])
        p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(llm, "local_reachable", return_value=False)
        p.start(); self.addCleanup(p.stop)
        catalog._CACHE.clear()
        llm._sticky.update(prov=None, until=0)
        self.addCleanup(catalog._CACHE.clear)
        token = llm.FORCE.set(None)
        self.addCleanup(llm.FORCE.reset, token)

    def fake_list(self, url, key):
        if key == "direct-key":
            ids = list(dict.fromkeys([*catalog.OPENAI_MODELS, *catalog.OPENAI_VISION, "gpt-5.6-luna",
                                      "gpt-6-sol", "gpt-audio", "atlas-draft", "text-embedding-3-small"]))
        else:
            ids = list(FALLBACK_LISTED)
        return {"data": [{"id": mid} for mid in ids]}

    def test_all_listed_models_are_shown_with_vision_flagged(self):
        with mock.patch.object(catalog, "_fetch", side_effect=self.fake_list) as fetch:
            groups = catalog.groups()
            self.assertEqual([r["model"] for r in groups["fallback"]], FALLBACK_LISTED)
            self.assertTrue(all(r["vision"] for r in groups["fallback"]),
                            "with no curated fallback vision list the shown ones are assumed image-capable")
            self.assertEqual(groups["local"][0]["id"], "local:qwen:2")
            self.assertEqual([r["model"] for r in groups["openai"]], ["gpt-6-luna", "gpt-6.1-sol"])
            catalog.groups()
            self.assertEqual(fetch.call_count, 2, "one cached read per cloud provider")

    def test_malformed_catalog_rows_are_skipped_not_fatal(self):
        payload = {"data": [{"id": "pixel-1-flash"}, "junk", 42, {}, {"id": "pixel-2-flash"}]}
        with mock.patch.object(catalog, "_fetch", return_value=payload):
            self.assertEqual([r["model"] for r in catalog.cloud_models("fallback", force=True)],
                             ["pixel-1-flash", "pixel-2-flash"])

    def test_fallback_lists_are_configurable_and_empty_means_none(self):
        with mock.patch.object(catalog, "_fetch", side_effect=self.fake_list):
            with mock.patch.dict(os.environ, {"STICKERLOOM_FALLBACK_MODELS": "pixel-1-flash",
                                              "STICKERLOOM_FALLBACK_VISION": "pixel-2-flash"}):
                rows = catalog.cloud_models("fallback", force=True)
                self.assertEqual([r["model"] for r in rows], ["pixel-1-flash"])
                self.assertFalse(rows[0]["vision"])
                self.assertTrue(catalog.supports_images("fallback", "pixel-2-flash"))
                self.assertFalse(catalog.supports_images("fallback", "pixel-1-flash"))
            with mock.patch.dict(os.environ, {"STICKERLOOM_FALLBACK_MODELS": ""}):
                self.assertEqual(catalog.cloud_models("fallback", force=True), [])

    def test_unknown_and_removed_models_cannot_be_selected(self):
        with mock.patch.object(catalog, "_fetch", return_value={"data": [{"id": "pixel-1-flash"}]}):
            with self.assertRaises(llm.LLMError):
                catalog.validate("fallback:atlas-draft")
            with self.assertRaises(llm.LLMError):
                catalog.validate("fallback:pixel-9-ultra")
            with self.assertRaises(llm.LLMError):
                catalog.validate("openai:gpt-6.2-sol")

    def test_catalog_authentication_is_separate_and_refreshes_when_a_key_changes(self):
        with mock.patch.object(catalog, "_fetch", side_effect=self.fake_list) as fetch:
            catalog.groups()
            self.assertEqual(fetch.call_args_list, [
                mock.call(FALLBACK_URL, "fallback-key"),
                mock.call(OFFICIAL_URL, "direct-key")])
            os.environ["STICKERLOOM_FALLBACK_KEY"] = "rotated-fallback-key"
            catalog.groups()
            self.assertEqual(fetch.call_count, 3)
            self.assertEqual(fetch.call_args, mock.call(FALLBACK_URL, "rotated-fallback-key"))

    def test_a_missing_provider_key_never_borrows_the_other_credential(self):
        for provider, missing, selected in (
            ("fallback", "STICKERLOOM_FALLBACK_URL", "fallback:pixel-1-flash"),
            ("openai", "OPENAI_API_KEY", "openai:gpt-5.6-luna"),
        ):
            with self.subTest(provider=provider), mock.patch.dict(os.environ):
                os.environ.pop(missing)
                os.environ["STICKERLOOM_LLM_MODEL"] = selected
                with mock.patch.object(catalog, "_fetch") as fetch, mock.patch.object(llm, "_post") as post:
                    self.assertEqual(catalog.cloud_models(provider), [])
                    self.assertFalse(llm.cloud_available())
                    self.assertIn(missing, llm.availability(probe=False)["cloud"]["why"])
                    with self.assertRaisesRegex(llm.LLMError, missing):
                        catalog.validate(selected)
                    with self.assertRaisesRegex(llm.LLMError, missing):
                        llm._complete_on("openai", "s", "u", model_=selected)
                    fetch.assert_not_called()
                    post.assert_not_called()

    def test_cloud_completions_bind_each_key_to_its_provider_endpoint(self):
        answer = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        for selected, url, key in (
            ("fallback:pixel-1-flash", FALLBACK_URL, "fallback-key"),
            ("openai:gpt-6.1-sol", OFFICIAL_URL, "direct-key"),
        ):
            with self.subTest(model=selected), mock.patch.object(llm, "_post", return_value=answer) as post:
                llm._complete_on("openai", "s", "u", model_=selected, base_url="https://other.example/v1")
                self.assertEqual((post.call_args.args[0], post.call_args.args[2]), (url + "/chat/completions", key))
        os.environ.pop("OPENAI_API_KEY")
        self.assertIsNone(catalog.endpoint("openai")[1])
        self.assertIsNone(catalog.endpoint("openai")[1], "a fallback key is never sent to the official URL")
        os.environ.pop("STICKERLOOM_FALLBACK_URL")
        self.assertIsNone(catalog.endpoint("fallback")[0], "no URL is invented for the fallback endpoint")

    def test_cloud_embeddings_use_only_the_configured_endpoint_credential(self):
        answer = {"data": [{"index": 0, "embedding": [0.0] * embed.DIMS}]}
        b = embed.backend()
        self.assertEqual(b, {"provider": "openai", "model": embed.OPENAI_EMBED_MODEL})
        with mock.patch.object(embed, "_post", return_value=answer) as post:
            self.assertEqual(len(embed._call(["text"], b)[0]), embed.DIMS)
            self.assertEqual((post.call_args.args[0], post.call_args.args[2]), (OFFICIAL_URL + "/embeddings", "direct-key"))
        with mock.patch.object(embed, "_post", side_effect=OSError("denied direct-key")):
            with self.assertRaises(embed.EmbedError) as raised:
                embed._call(["text"], b)
            self.assertNotIn("direct-key", str(raised.exception))
        os.environ.pop("OPENAI_API_KEY")
        self.assertEqual(embed.backend()["provider"], "none")
        with mock.patch.object(embed, "_post") as post:
            with self.assertRaisesRegex(embed.EmbedError, "OPENAI_API_KEY"):
                embed._call(["text"], b)
            post.assert_not_called()

    def test_provider_none_never_fetches_cloud_catalogs(self):
        os.environ["STICKERLOOM_LLM_PROVIDER"] = "none"
        with mock.patch.object(catalog, "_fetch", side_effect=AssertionError("network disabled")):
            self.assertEqual(catalog.cloud_models("fallback"), [])
            self.assertEqual(catalog.cloud_models("openai"), [])

    def test_luna_default_uses_official_openai_and_high_reasoning(self):
        self.assertEqual(llm.cloud_model(), "openai:gpt-6-luna")
        sent = []
        def post(url, body, key, timeout):
            sent.append((url, body, key))
            return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        with mock.patch.object(llm, "_post", side_effect=post):
            llm._complete_on("openai", "s", "u")
            llm._complete_on("openai", "s", "u", model_="openai:gpt-6.1-sol", images=[b"image"])
            llm._complete_on("local", "s", "u", model_="local:qwen:2")
        self.assertEqual([(url, key) for url, _, key in sent], [
            (OFFICIAL_URL + "/chat/completions", "direct-key"),
            (OFFICIAL_URL + "/chat/completions", "direct-key"),
            (llm.LOCAL_URL + "/chat/completions", None)])
        self.assertEqual(sent[0][1]["model"], "gpt-6-luna")
        self.assertEqual(sent[0][1]["reasoning_effort"], "high")
        self.assertGreaterEqual(sent[0][1]["max_completion_tokens"], 8192)
        self.assertNotIn("temperature", sent[0][1])
        self.assertEqual(sent[2][1]["model"], "qwen:2")

    def test_image_requests_reject_text_only_before_sending(self):
        with mock.patch.object(llm, "_post", side_effect=AssertionError("must not send")):
            with self.assertRaisesRegex(llm.LLMError, "image-capable"):
                llm._complete_on("openai", "s", "u", model_="openai:atlas-draft", images=[b"image"])

    def test_legacy_router_ids_are_gone_and_bare_luna_stays_official(self):
        self.assertEqual(catalog.split_model("gpt-5.6-luna"), ("openai", "gpt-5.6-luna"))
        for value in ("gpt-5.6-luna", "openai:gpt-5.6-luna"):
            with self.subTest(value=value):
                self.assertEqual(catalog.split_model(value), ("openai", "gpt-5.6-luna"))
                llm.FORCE.set({"model": value})
                self.assertEqual(brain.target()["model"], "openai:gpt-5.6-luna")
                self.assertEqual(judge.target()["model"], "openai:gpt-5.6-luna")
        os.environ.pop("OPENAI_API_KEY")
        self.assertEqual(llm.forced_provider(), "none", "the fallback key is never used as an official OpenAI key")
        with self.assertRaisesRegex(llm.LLMError, "OPENAI_API_KEY"):
            catalog.validate("openai:gpt-5.6-luna")
        llm.FORCE.set({"model": "fallback:pixel-1-flash"})
        self.assertEqual(brain.target()["model"], "fallback:pixel-1-flash")
        os.environ.pop("STICKERLOOM_FALLBACK_URL")
        self.assertEqual(llm.forced_provider(), "none")
        with self.assertRaisesRegex(llm.LLMError, "STICKERLOOM_FALLBACK_URL"):
            catalog.validate("fallback:pixel-1-flash")

    def test_luna_high_and_official_endpoint_survive_optional_parameter_retry(self):
        import urllib.error
        error = urllib.error.HTTPError(OFFICIAL_URL + "/chat/completions", 400, "bad format", {},
                                       io.BytesIO(b'{"error":{"message":"response_format unsupported"}}'))
        answer = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        with mock.patch.object(llm, "_post", side_effect=[error, answer]) as post:
            llm._complete_on("openai", "s", "u", model_="openai:gpt-5.6-luna", json_mode=True, max_tokens=400)
            self.assertEqual(post.call_count, 2)
            for call in post.call_args_list:
                url, body, key, _ = call.args
                self.assertEqual((url, key), (OFFICIAL_URL + "/chat/completions", "direct-key"))
                self.assertEqual(body["reasoning_effort"], "high")
                self.assertEqual(body["max_completion_tokens"], 8192)
            self.assertNotIn("response_format", post.call_args_list[-1].args[1])

    def test_saved_selection_reaches_chat_and_vision_and_can_return_to_default(self):
        with mock.patch.object(catalog, "_fetch", side_effect=self.fake_list):
            llm.set_model("fallback:pixel-1-flash")
            self.assertEqual(llm.cloud_model(), "fallback:pixel-1-flash")
            self.assertEqual(brain.target()["model"], "fallback:pixel-1-flash")
            self.assertEqual(judge.target()["model"], "fallback:pixel-1-flash")
            llm._write_pref_file(cloud_model="")
            self.assertEqual(llm.cloud_model(), "openai:gpt-6-luna")

    def test_new_openai_models_are_selectable_for_chat_and_images_only_when_listed(self):
        ids = ["gpt-6-luna", "gpt-6.1-sol", "gpt-6-sol", "gpt-6-astra", "gpt-image-2", "gpt-audio", "text-embedding-3-small"]
        answer = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        with mock.patch.object(catalog, "_fetch", return_value={"data": [{"id": mid} for mid in ids]}), mock.patch.object(llm, "_post", return_value=answer) as post:
            self.assertEqual([r["model"] for r in catalog.cloud_models("openai")], ["gpt-6-luna", "gpt-6.1-sol"])
            for mid in ("gpt-6-luna", "gpt-6.1-sol"):
                selected = catalog.validate("openai:" + mid)
                llm.FORCE.set({"model": selected})
                self.assertEqual(brain.target()["model"], selected)
                self.assertEqual(judge.target()["model"], selected)
                llm._complete_on("openai", "s", "u", model_=selected)
                self.assertEqual(post.call_args.args[1]["model"], mid)
                self.assertEqual(post.call_args.args[0], OFFICIAL_URL + "/chat/completions")
            llm._complete_on("openai", "s", "u", model_="openai:gpt-6.1-sol", images=[b"image"])
            self.assertEqual(post.call_args.args[1]["model"], "gpt-6.1-sol")
            with self.assertRaises(llm.LLMError):
                catalog.validate("openai:gpt-6.2-sol")
        llm.FORCE.set({"model": "local:qwen:2"})
        self.assertEqual(brain.target(), {"provider": "local", "model": "local:qwen:2"})
        self.assertEqual(judge.target()["model"], "local:qwen:2")

    def test_chat_model_status_uses_the_session_pick_and_does_not_leak_to_global_settings(self):
        from stickerloom.agent.memory import SessionStore, SessionError
        from stickerloom.console.server import Console, make_handler
        out = Path(self.td.name)
        store = SessionStore(out, cache=mock.Mock(), user="U001", see_all=False)
        session = store.create()
        session["settings"]["models"] = {"ai": "openai:gpt-5.6-luna"}
        store.save(session)
        console = object.__new__(Console)
        console.chat_parts = mock.Mock(return_value=(store, None, None, None))
        os.environ["STICKERLOOM_LLM_MODEL"] = "atlas-draft"
        user = {"id": "U001", "role": "member"}
        with mock.patch.object(brain, "LAST", {"error": None, "at": 0, "ok_at": 0}), mock.patch("stickerloom.console.server.higgsfield.available", return_value=False):
            info = console.agent_info(user, session["id"])
            self.assertEqual(info["agent"], {"provider": "openai", "model": "openai:gpt-5.6-luna"})
            self.assertEqual(info["preference"], "cloud")
            self.assertEqual(info["availability"]["cloud"]["model"], "openai:gpt-5.6-luna")
            self.assertTrue(info["availability"]["cloud"]["ok"])
            self.assertFalse(info["agent_status"]["fallback"])
            self.assertEqual(info["vision"]["model"], "openai:gpt-5.6-luna")
            self.assertIsNone(llm.forced())
            self.assertEqual(console.agent_info(user)["agent"]["model"], "atlas-draft")
            handler = object.__new__(make_handler(console))
            handler.path, handler.user = "/api/chat/agent?session=" + session["id"], user
            handler._json = mock.Mock()
            handler._get()
            self.assertEqual(handler._json.call_args.args[0], 200)
            self.assertEqual(handler._json.call_args.args[1]["agent"]["model"], "openai:gpt-5.6-luna")
            console.chat_parts.return_value = (SessionStore(out, cache=mock.Mock(), user="U002", see_all=False), None, None, None)
            with self.assertRaises(SessionError):
                console.agent_info({"id": "U002", "role": "member"}, session["id"])
            handler.user = {"id": "U002", "role": "member"}
            handler._get()
            self.assertEqual(handler._json.call_args.args[0], 404)
            self.assertIsNone(llm.forced())

    def test_explicit_vision_override_and_existing_env_model_are_kept(self):
        os.environ["STICKERLOOM_LLM_MODEL"] = "atlas-draft"
        os.environ["STICKERLOOM_VISION_OPENAI_MODEL"] = "pixel-1-flash"
        self.assertEqual(llm.cloud_model(), "atlas-draft")
        self.assertEqual(judge.target()["model"], "pixel-1-flash")
        llm.FORCE.set({"model": "fallback:pixel-2-flash"})
        self.assertEqual(judge.target()["model"], "pixel-1-flash")

    def test_configured_fallback_routes_to_its_provider_after_404(self):
        os.environ["STICKERLOOM_LLM_MODEL"] = "atlas-draft"
        os.environ["STICKERLOOM_LLM_FALLBACK"] = "openai:gpt-5.6-luna"
        failure = llm.LLMError("model unavailable", kind="http", code=404)
        answer = {"choices": [{"message": {"content": "fallback"}, "finish_reason": "stop"}]}
        with mock.patch.object(llm, "_post", side_effect=[failure, answer]) as post:
            self.assertEqual(llm._complete_on("openai", "s", "u")[0], "fallback")
            self.assertEqual(post.call_count, 2)
            self.assertTrue(post.call_args_list[-1].args[0].startswith(OFFICIAL_URL))

    def test_vision_403_retries_the_pinned_openai_or_local_choice_on_the_fallback(self):
        os.environ["STICKERLOOM_LLM_FALLBACK"] = "pixel-1-flash"
        denied = llm.LLMError("Your plan does not include the requested model.", kind="http", code=403)
        answer = {"choices": [{"message": {"content": "a wave"}, "finish_reason": "stop"}]}
        for selected in ("openai:gpt-6.1-sol", "local:qwen:2"):
            with self.subTest(selected=selected):
                llm.FORCE.set({"model": selected})
                with mock.patch.object(llm, "_post", side_effect=[denied, answer]) as post:
                    text, meta = llm.complete_vision("s", "u", images=[b"still"],
                        provider_="local" if selected.startswith("local:") else "openai", model_=selected,
                        base_url="http://local.example/v1" if selected.startswith("local:") else None)
                self.assertEqual(text, "a wave")
                self.assertEqual(post.call_count, 2)
                args = post.call_args_list[-1].args
                self.assertEqual((args[0], args[1]["model"], args[2]),
                                 (FALLBACK_URL + "/chat/completions", "pixel-1-flash", "fallback-key"))
                self.assertEqual(meta["fallback_from"], catalog.normalize_model(selected))
                self.assertEqual(meta["model_provider"], "fallback")
                self.assertEqual(llm.FORCE.get()["model"], selected, "stored selections stay intact")

    def test_vision_stream_recovers_before_output_but_never_restarts_partial_output(self):
        os.environ["STICKERLOOM_LLM_FALLBACK"] = "pixel-1-flash"
        for partial in (False, True):
            with self.subTest(partial=partial):
                chunks, sent = [], []
                def stream(url, body, key, timeout, receive):
                    sent.append(body["model"])
                    if len(sent) == 1:
                        if partial:
                            receive("He is ")
                        raise llm.LLMError("refused", kind="http", code=403)
                    receive("He is waving.")
                    return {"choices": [{"message": {"content": "He is waving."}, "finish_reason": "stop"}]}
                with mock.patch.object(llm, "_post_stream", side_effect=stream):
                    if partial:
                        with self.assertRaisesRegex(llm.LLMError, "refused"):
                            llm.complete_vision("s", "u", provider_="openai", model_="openai:gpt-6.1-sol",
                                                images=[b"still"], on_delta=chunks.append)
                        self.assertEqual(sent, ["gpt-6.1-sol"])
                        self.assertEqual(chunks, ["He is "])
                    else:
                        text, _ = llm.complete_vision("s", "u", provider_="openai", model_="openai:gpt-6.1-sol",
                                                     images=[b"still"], on_delta=chunks.append)
                        self.assertEqual(sent, ["gpt-6.1-sol", "pixel-1-flash"])
                        self.assertEqual("".join(chunks), text)

    def test_vision_fallback_is_bounded_disabled_when_none_and_keeps_both_errors(self):
        os.environ["STICKERLOOM_LLM_FALLBACK"] = "pixel-1-flash"
        for selected, mode, calls in (("fallback:pixel-1-flash", "openai", 1),
                                      ("openai:gpt-6.1-sol", "none", 0),
                                      ("openai:gpt-6.1-sol", "openai", 2)):
            with self.subTest(selected=selected, mode=mode):
                denied = llm.LLMError("subscription denied", kind="http", code=403)
                failed = llm.LLMError("endpoint unavailable", kind="unreachable")
                with mock.patch.object(llm, "_post", side_effect=[denied, failed]) as post:
                    with self.assertRaises(llm.LLMError) as caught:
                        llm.complete_vision("s", "u", provider_=mode, model_=selected, images=[b"still"])
                self.assertEqual(post.call_count, calls)
                if calls == 2:
                    self.assertIn("subscription denied", str(caught.exception))
                    self.assertIn("endpoint unavailable", str(caught.exception))

    def test_vision_does_not_retry_without_a_configured_fallback_or_on_success(self):
        answer = {"choices": [{"message": {"content": "a wave"}, "finish_reason": "stop"}]}
        with mock.patch.object(llm, "_post", return_value=answer) as post:
            llm.complete_vision("s", "u", provider_="openai", model_="openai:gpt-6.1-sol", images=[b"still"])
            self.assertEqual(post.call_count, 1)
        with mock.patch.object(llm, "_post", side_effect=llm.LLMError("no access")) as post:
            with self.assertRaisesRegex(llm.LLMError, "no access"):
                llm.complete_vision("s", "u", provider_="openai", model_="openai:gpt-6.1-sol", images=[b"still"])
            self.assertEqual(post.call_count, 1)

    def test_description_and_judge_use_fallback_and_record_the_actual_model(self):
        from pathlib import Path
        from PIL import Image
        from stickerloom.vision import describe
        from stickerloom.runtime import cache
        from tests.test_vision import GOOD
        os.environ["STICKERLOOM_LLM_FALLBACK"] = "pixel-1-flash"
        os.environ["STICKERLOOM_DB_WRITE"] = "0"
        llm.FORCE.set({"model": "openai:gpt-6.1-sol"})
        out = Path(self.td.name)
        gen = out / "G001"
        gen.mkdir()
        image = io.BytesIO()
        Image.new("RGBA", (64, 64), (200, 20, 20, 255)).save(image, "PNG")
        (gen / "s9.png").write_bytes(image.getvalue())
        (gen / "result.json").write_text(json.dumps({"generation_id": "G001", "stickers": [{"index": 9, "key": "wave", "status": "READY", "png": "s9.png"}]}))
        denied = llm.LLMError("plan does not include this model", kind="http", code=403)
        answer = {"choices": [{"message": {"content": "He is waving."}, "finish_reason": "stop"}]}
        with mock.patch.object(llm, "_post_stream", side_effect=[denied, answer]):
            self.assertEqual(describe.sticker(out, "G001", 9, "what is he doing?", allowed=True), "He is waving.")
        answer["choices"][0]["message"]["content"] = GOOD
        with mock.patch.object(llm, "_post", side_effect=[denied, answer]):
            result = judge.VisionJudge(out=out, cache=cache.Cache(force_memory=True)).judge_sticker(
                image.getvalue(), {"name": "wave"}, generation_id="G001", sticker_id="G001/S9")
            self.assertEqual((result.decision, result.model), ("APPROVE", "pixel-1-flash"))
        logged = [json.loads(row) for row in (out / "model_calls.jsonl").read_text().splitlines()]
        self.assertEqual([row["kind"] for row in logged], ["VLM_DESCRIBE", "VLM_STICKER"])
        for row in logged:
            self.assertEqual(row["model"], "pixel-1-flash")
            self.assertEqual(row["fallback_from"], "openai:gpt-6.1-sol")
