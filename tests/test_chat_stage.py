"""The chat's stage (Prompt · Stickers · Animation · Telegram · Export) decides how far a NEW request goes, on FakeTools (no provider, no paid call)."""
import unittest
from unittest.mock import patch

from stickerloom.agent import creator, stages
from tests.test_agent import Base


class Stage(Base):
    def set_stage(self, stage, **more):
        s = self.sess()
        s["settings"]["stage"] = stage
        s["settings"].update(more)
        self.store.save(s)

    def creates(self):
        return [c for c in self.tools.calls if c[0] == "create"]

    def drive(self, rounds=6):
        for _ in range(rounds):
            self.agent.creator_tick(self.sid)

    def test_predefined_is_the_default_but_a_saved_creative_choice_reaches_the_planner(self):
        self.assertEqual(self.sess()["settings"]["actions"], "predefined")
        for mode in ("predefined", "creative"):
            session = self.sess()
            session["settings"]["actions"] = mode
            self.store.save(session)
            with patch.object(self.tools, "plan", wraps=self.tools.plan) as call:
                self.say("make me falcon stickers")
                self.assertEqual(call.call_args.args[4], mode)

    def test_a_new_chat_is_on_emojis_and_plans_a_sheet_as_today(self):
        self.assertEqual(self.sess()["settings"]["stage"], "auto", "a new chat starts on Auto: a plain request still stops at the stickers")
        m = self.say("make me falcon stickers")
        card = m["cards"][0]
        self.assertEqual((card["type"], card["stage"], card["estimate"]), ("plan", "emojis", 2.0))
        self.assertEqual(self.sess()["pending"]["type"], "create")
        self.assertNotIn("creator", card, "the Emojis stage never runs the creator")
        self.assertFalse(self.creates())

    def test_prompt_is_plan_only_and_never_starts_even_without_asking(self):
        self.set_stage("prompt", ask_before_spending=False)
        m = self.say("make me falcon stickers")
        card = m["cards"][0]
        self.assertEqual((card["stage"], card["sheet_prompt"]), ("prompt", "sheet of falcon"))
        self.assertFalse(self.creates(), "the Prompt stage spends nothing and starts no job")
        self.assertEqual(self.sess()["pending"]["stage"], "prompt")
        labels = [c["label"] for c in m["chips"]]
        self.assertTrue(labels[0].startswith("Generate"))
        self.assertIn("Edit", labels)
        self.assertEqual(next(c for c in m["chips"] if c["label"] == "Edit")["fill"], self.sess()["pending"]["prompt"])
        self.say("", action={"type": "confirm"})
        self.assertEqual(len(self.creates()), 1, "Generate is the go-ahead: one sheet")

    def test_animation_prices_sheet_and_video_and_ends_after_the_animations(self):
        self.set_stage("animation")
        m = self.say("make me falcon stickers")
        card = m["cards"][0]
        self.assertEqual((card["estimate"], card["creator"]["sheet"], card["creator"]["video"], card["creator"]["end"]), (10.0, 2.0, 8.0, "animation"))
        p = self.sess()["pending"]
        self.assertEqual((p["type"], p["scope"], p["end"]), ("creator", "video", "animation"))
        self.assertEqual(m["chips"][0]["label"], "Create and animate")
        self.assertFalse(self.creates(), "nothing spends before the click (rule 13)")

    def test_animation_run_stops_with_no_pack_and_no_telegram(self):
        self.tools._live = False                                  # the sheet is there at once: the run can be driven to its end
        self.set_stage("animation", creator={"on": False, "scope": "images", "bypass": True})
        self.say("make me falcon stickers")
        run = self.sess()["creator_run"]
        self.assertEqual((run["end"], run["scope"], [s["id"] for s in creator.labels(run)][-1]), ("animation", "video", "approve_anim"))
        self.drive()
        gid = self.sess()["creator_run"]["generation"]
        for s in self.tools.gens[gid]["stickers"]:
            s["anim_status"], s["anim"] = "READY", "PENDING"
        self.drive()
        run = self.sess()["creator_run"]
        self.assertEqual(run["status"], "done")
        self.assertFalse([c for c in self.tools.calls if c[0] in ("pack_add", "telegram_send")], "Animation ends before the pack and Telegram")
        self.assertIn("animated", self.sess()["messages"][-1]["text"])

    def test_export_runs_to_telegram(self):
        self.tools._live = False
        self.set_stage("export", creator={"on": False, "scope": "images", "bypass": True})
        self.say("make me falcon stickers")
        self.assertEqual(self.sess()["creator_run"]["end"], "export")
        self.drive()
        gid = self.sess()["creator_run"]["generation"]
        for s in self.tools.gens[gid]["stickers"]:
            s["anim_status"], s["anim"] = "READY", "PENDING"
        self.drive()
        self.assertEqual([c[0] for c in self.tools.calls if c[0] in ("pack_add", "telegram_send")], ["pack_add", "telegram_send"])
        self.assertEqual(self.sess()["creator_run"]["status"], "done")

    def test_api_runs_to_telegram_then_exports_the_pack(self):
        self.tools._live = False
        self.set_stage("api", creator={"on": False, "scope": "images", "bypass": True})
        m = self.say("make me falcon stickers")
        run = self.sess()["creator_run"]
        self.assertEqual((run["end"], [s["id"] for s in creator.labels(run)][-2:]), ("api", ["telegram", "api"]))
        self.drive()
        gid = self.sess()["creator_run"]["generation"]
        for s in self.tools.gens[gid]["stickers"]:
            s["anim_status"], s["anim"] = "READY", "PENDING"
        self.drive()
        self.assertEqual([c[0] for c in self.tools.calls if c[0] in ("pack_add", "telegram_send", "collection_export")], ["pack_add", "telegram_send", "collection_export"])
        run = self.sess()["creator_run"]
        self.assertEqual((run["status"], run["collection"]["count"]), ("done", 9))
        self.assertIn("Exported to the API", self.sess()["messages"][-1]["text"])

    def test_api_refused_is_a_stop_with_try_again_not_a_dead_end(self):
        from stickerloom.agent.tools import ToolError
        self.tools._live = False
        self.tools.collection_export = lambda pid, name: (_ for _ in ()).throw(ToolError("The collection API is not set up", 409))
        self.set_stage("api", creator={"on": False, "scope": "images", "bypass": True})
        self.say("make me falcon stickers")
        self.drive()
        gid = self.sess()["creator_run"]["generation"]
        for s in self.tools.gens[gid]["stickers"]:
            s["anim_status"], s["anim"] = "READY", "PENDING"
        self.drive()
        run = self.sess()["creator_run"]
        self.assertEqual((run["status"], run["step"], run["stop"]["kind"]), ("stopped", "api", "api"))
        self.assertEqual([c["label"] for c in run["stop"]["chips"]], ["Try again", "Stop"])

    def test_export_waits_at_g2_without_bypass(self):
        self.tools._live = False
        self.set_stage("export")
        self.say("make me falcon stickers")
        self.drive()
        run = self.sess()["creator_run"]
        self.assertEqual((run["status"], run["step"]), ("waiting", "approve"), "D2: one click at G2 unless bypass is on")


class SliderBoundary(Stage):
    """The slider is a boundary both ways: asking for more or for "only" less gets a question with buttons that set it and go."""
    def test_auto_follows_the_message(self):
        m = self.say("make me falcon stickers and animate them")
        self.assertEqual(self.sess()["pending"]["end"], "animation", "Auto: the message asked for the animation")

    def test_animate_past_stickers_asks_and_the_button_sets_and_goes(self):
        self.set_stage("emojis")
        self.say("make me falcon stickers")
        self.say("", action={"type": "confirm"})
        m = self.say("animate it with grok lite")
        self.assertIn("needs **Animation**", m["text"])
        self.assertEqual([c["label"] for c in m["chips"]], ["Set to Animation and go", "Set to Auto and go", "Not now"])
        self.assertFalse([c for c in self.tools.calls if c[0] == "animate"], "nothing runs before the click")
        go = m["chips"][0]
        self.say("", action={"type": "stage_go", "stage": go["stage"], "text": go["text"]})
        self.assertEqual(self.sess()["settings"]["stage"], "animation")
        self.assertEqual(self.store.person_stage(), "animation", "one slider per person")

    def test_stickers_only_at_animation_asks_and_keep_goes_as_it_is(self):
        self.set_stage("animation")
        m = self.say("make new falcon stickers only")
        self.assertIn("You asked for Stickers only", m["text"])
        keep = m["chips"][2]
        self.assertEqual((keep["label"], keep.get("force")), ("Keep Animation and go", True))
        self.say("", action={"type": "stage_go", "stage": keep["stage"], "text": keep["text"], "force": True})
        self.assertEqual(self.sess()["pending"]["end"], "animation")

    def test_a_plain_request_at_a_fixed_stop_just_runs(self):
        self.set_stage("animation")
        m = self.say("make me falcon stickers")
        self.assertNotIn("slider", m["text"])


class Migration(unittest.TestCase):
    def test_need_and_resolve(self):
        self.assertEqual(stages.resolve("auto", "make kid superhero"), ("emojis", None))
        self.assertEqual(stages.resolve("emojis", "animate it with grok lite")[1]["kind"], "more")
        self.assertEqual(stages.resolve("animation", "stickers only")[1]["kind"], "less")
        self.assertEqual(stages.resolve("animation", "make kid batman"), ("animation", None))
        self.assertEqual(stages.resolve("auto", "send it to telegram")[0], "export")
        self.assertTrue(stages.at_least("auto", "api"))
        self.assertFalse(stages.at_least("emojis", "animation"))
        self.assertEqual(stages.reached({"stickers": [{"status": "READY", "anim_status": "READY"}]}), "animation")

    def test_old_creator_settings_read_as_a_stage(self):
        self.assertEqual(stages.of({"creator": {"on": True, "scope": "video"}}), "export")
        self.assertEqual(stages.of({"creator": {"on": True, "scope": "images"}}), "auto")
        self.assertEqual(stages.of({}), "auto", "nothing set: Auto")
        self.assertEqual(stages.of({"stage": "prompt", "creator": {"on": True, "scope": "video"}}), "prompt", "an explicit stage wins")
        self.assertEqual(stages.of({"stage": "bogus"}), "auto")

    def test_run_spec(self):
        self.assertEqual(stages.run_spec("animation", True), {"stage": "animation", "plan_only": False, "creator": True, "scope": "video", "end": "animation", "bypass": True})
        self.assertTrue(stages.run_spec("prompt")["plan_only"])
        self.assertFalse(stages.run_spec("emojis", True)["bypass"])
        self.assertEqual(stages.run_spec("api")["end"], "api")
        self.assertTrue(stages.animates("api"))
        self.assertEqual([stages.INFO[s]["label"] for s in stages.STAGES], ["Auto", "Prompt", "Stickers", "Animation", "Telegram", "Export"])

    def test_batch_label(self):
        self.assertEqual(stages.batch_label(2, {"slots": {"preset": "social-v1"}}), "Batch 02 · social")
        self.assertEqual(stages.batch_label(1, {"grid": [3, 3]}), "Batch 01 · actions 1-9")
        self.assertEqual(stages.batch_label(3, {"grid": "2x2"}), "Batch 03 · actions 9-12")


if __name__ == "__main__":
    unittest.main()
