"""Narrow regression checks for the two October 4 reviews; no providers or media processing."""
import os
import threading
from types import SimpleNamespace
from unittest import mock

from stickerloom.agent import creator, editroute, resolver
from stickerloom.agent.brain import Brain
from stickerloom.agent.graph import hydrate
from stickerloom.agent.tools import ConsoleTools
from stickerloom.console.server import Console
from stickerloom.engine.config import EngineConfig
from stickerloom.generation import jobs
from stickerloom.services import llm
from tests.test_agent import Base
from tests.test_llm_backend import Env


class Crash(Base):
    def _video_run(self):
        self.seed("G012")
        s = self.sess()
        run = creator.new_run(prompt="banana", subject="banana", grid="3x3", style_id="flat_vector", scope="video",
                              bypass=True, estimate=2, video_estimate=9)
        run.update(step="video", generation="G012")
        s["creator_run"] = run
        self.store.save(s)
        return run

    def test_video_job_is_saved_before_animate_returns(self):
        self._video_run()
        animate = self.tools.animate

        def crash(*args, **kw):
            animate(*args, **kw)
            raise KeyboardInterrupt("server stopped")

        with mock.patch.object(self.tools, "animate", crash):
            with self.assertRaises(KeyboardInterrupt):
                self.agent.creator_tick(self.sid)
        job = self.sess()["creator_run"]["video_job"]
        self.assertEqual(job, "J001")
        self.agent.creator_tick(self.sid)
        self.assertEqual(len([c for c in self.tools.calls if c[0] == "animate"]), 1)

    def test_lost_job_pointer_is_recovered_from_the_run_tag(self):
        run = self._video_run()
        self.tools.animate("G012", creator_run=run["id"])
        self.agent.creator_tick(self.sid)
        self.assertEqual(self.sess()["creator_run"]["video_job"], "J001")
        self.assertEqual(len([c for c in self.tools.calls if c[0] == "animate"]), 1)

    def test_each_transition_is_saved_before_the_next_tool_call(self):
        run = self._video_run()
        s = self.sess()
        s["creator_run"].update(step="cut", scope="images")
        self.store.save(s)

        def crash(gid):
            self.assertEqual(self.sess()["creator_run"]["step"], "look")
            raise KeyboardInterrupt()

        s["settings"]["allow_vlm"] = True
        self.store.save(s)
        with mock.patch.object(self.tools, "judge", crash):
            with self.assertRaises(KeyboardInterrupt):
                self.agent.creator_tick(self.sid)

    def test_real_video_job_is_tagged_before_scheduling(self):
        scheduled = []
        c = SimpleNamespace(out=self.out, cfg=EngineConfig(), lock=threading.Lock(),
                            actor=lambda: {"id": "local", "can_spend": True}, visible=lambda *a: True,
                            commit_edge=lambda *a: None, reserve=lambda *a: None, video_prompt_for=lambda *a: "animate banana",
                            fulfil_async=lambda jid, **kw: scheduled.append(jid), attach_video_from_job=lambda *a: None)

        def created(job):
            self.assertEqual(jobs.read(self.out, job["id"])["request"]["creator_run"], "C123")
            self.assertEqual(scheduled, [])

        with mock.patch("stickerloom.console.server.higgsfield.available", return_value=True), \
             mock.patch("stickerloom.console.server.higgsfield.cost", return_value=9), \
             mock.patch("stickerloom.console.server.pl.read_result", return_value={"task": "T001", "prompt": "banana"}), \
             mock.patch("stickerloom.console.server.gates.sheet_of", return_value={"status": "APPROVED", "file": "sheet.png"}):
            r = Console.live(c, "video", {"generation": 12, "sheet": "A1"}, creator_run="C123", on_job=created)
        self.assertEqual(scheduled, [r["job"]])
        self.assertEqual(ConsoleTools(c).creator_job("C123")["id"], r["job"])


class Context(Base):
    def test_focus_survives_a_long_chat_without_large_prompts(self):
        seen = []
        brain = Brain(complete=lambda system, user: (seen.append(user) or '{"intents":["REFINE"]}', {}))
        self.seed()
        s = self.sess()
        s["focus"]["stickers"] = ["G012/S3"]
        with mock.patch.object(self.store, "generation_card", return_value={"stickers": [
                {"id": "G012/S3", "key": "waving", "prompt": "x" * 9000}]}):
            focus = self.store.focus_context(s)
        brain.classify("less cartoony", "old subject " * 500, focus=focus)
        prefix = seen[0].split("What this chat has so far:")[0]
        self.assertIn("G012/S3", prefix)
        self.assertIn("flat_vector", prefix)
        self.assertIn("waving", prefix)
        self.assertLess(len(prefix), 1000)
        self.assertNotIn("x" * 100, prefix)

    def test_structured_summary_is_detached_and_keeps_ids(self):
        self.seed()
        s = self.sess()
        s["subjects"][0]["passes"][0]["liked"] = ["G012/S3"]
        data = self.store.summary_structured(s)
        self.assertEqual(data["latest_generation"], "G012")
        self.assertEqual(data["subjects"][0]["passes"][0]["liked"], ["G012/S3"])
        data["subjects"].clear()
        self.assertEqual(len(s["subjects"]), 1)

    def test_restart_preserves_trace_and_never_promises_no_spend(self):
        s = self.sess()
        self.store.add_message(s, "assistant", "", status="working", steps=[{"kind": "step", "label": "started J001"}])
        self.store.save(s)
        hydrate(self.store, self.tools, s)
        m = self.sess()["messages"][-1]
        self.assertEqual(m["status"], "error")
        self.assertNotIn("nothing was spent", m["text"])
        self.assertEqual(m["steps"][0]["label"], "started J001")
        self.assertEqual(m["steps"][-1]["kind"], "note")
        hydrate(self.store, self.tools, self.sess())
        self.assertEqual(len(self.sess()["messages"][-1]["steps"]), 2)

    def test_traits_still_require_two_explicit_requests(self):
        s = self.sess()
        s["interactions"] = [{"user": "I want more anime-like stickers"}, {"user": "make them less cartoony"}]
        self.assertEqual(self.store.traits(s), [])
        s["interactions"] += [{"user": "use an anime look"}, {"user": "not so cartoonish"}]
        self.assertEqual(set(self.store.traits(s)), {"an anime-inspired look", "a less cartoony look"})

    def test_reference_sources_are_not_edit_targets(self):
        r = resolver.resolve("make 5 with the style from S2 and pose from number 7", {"generation": "G012", "n": 9})
        self.assertEqual(r.stickers, ["G012/S5"])
        self.assertEqual([x["role"] for x in r.references], ["STYLE", "POSE"])
        clause = editroute.reference_roles_clause(r.references, "angrier")
        self.assertIn("Reference image 2: use its pose only", clause)
        self.assertIn("Apply this change: angrier", clause)

    def test_reference_roles_reach_the_priced_plan(self):
        self.seed()
        self.say("make 5 with the style from S2 and pose from number 7")
        pending = self.sess()["pending"]
        self.assertEqual(len(pending["items"]), 1)
        item = pending["items"][0]
        self.assertEqual(item["regen_of"], "G012/S5")
        self.assertEqual(len(item["refs"]), 2)
        self.assertIn("Reference image 2: use its pose only", item["ref_clause"])
        self.assertFalse([c for c in self.tools.calls if c[0] == "create"])


class Budgets(Base):
    def test_auto_fallback_shares_the_original_timeout(self):
        with Env(), mock.patch.object(llm, "local_reachable", return_value=True), \
             mock.patch.object(llm.time, "monotonic", side_effect=[100, 112]), \
             mock.patch.object(llm, "_complete_on", side_effect=[llm.LLMError("stalled"), ("ok", {})]) as complete:
            self.assertEqual(llm.complete("s", "u", timeout=45)[0], "ok")
        self.assertEqual([call.args[0] for call in complete.call_args_list], ["local", "openai"])
        self.assertEqual([call.kwargs["timeout"] for call in complete.call_args_list], [15, 33])

    def test_explicit_local_choice_keeps_its_full_timeout(self):
        with Env(), mock.patch.object(llm, "_complete_on", return_value=("ok", {})) as complete:
            llm.complete("s", "u", provider_="local", timeout=45)
        self.assertEqual(complete.call_args.kwargs["timeout"], 45)

    def test_auto_without_a_cloud_key_keeps_the_local_budget(self):
        with Env(), mock.patch.object(llm, "local_reachable", return_value=True), \
             mock.patch.object(llm, "_complete_on", return_value=("ok", {})) as complete:
            os.environ.pop(llm.KEY_VAR)
            llm.complete("s", "u", timeout=45)
        self.assertEqual(complete.call_args.kwargs["timeout"], 45)
