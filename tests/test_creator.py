"""The agentic creator (agent/creator.py), 2026-10-02: one go-ahead from the request to the pack on Telegram; any rejection stops it; the price is shown once and never exceeded."""
import unittest

from tests.test_agent import Base, gen


class CreatorRuns(Base):
    def setUp(self):
        super().setUp()
        s = self.sess()
        s["settings"]["creator"] = {"on": True, "scope": "images", "bypass": True}
        s["settings"]["allow_vlm"] = True
        self.store.save(s)

    def settings(self, **kw):
        s = self.sess()
        s["settings"]["creator"] = {**s["settings"]["creator"], **kw}
        self.store.save(s)

    def sheet_arrives(self, gid="G050", failed=(), anim=None):
        g = gen(gid)
        for s in g["stickers"]:
            s["still"] = "PENDING"
            s["anim"] = "PENDING"
            if s["index"] in failed:
                s["status"], s["reason"] = "FAILED", "inside_cell"
            if anim is not None:
                s["anim_status"] = anim
        self.tools.gens[gid] = g
        self.tools.job_generations["J001"] = gid
        return g

    def run_until(self, *states, limit=40):
        for _ in range(limit):
            st = self.agent.creator_tick(self.sid)
            if st in states:
                return st
        self.fail(f"the creator never reached {states}: {self.sess()['creator_run']}")

    def start(self, text="make me falcon stickers"):
        m = self.say(text)
        self.assertEqual(m["cards"][0]["type"], "plan")
        self.assertEqual(m["cards"][0]["creator"]["scope"], self.sess()["settings"]["creator"]["scope"])
        self.assertEqual([c.get("action") for c in m["chips"] if c.get("action") in ("confirm", "cancel")], ["confirm", "cancel"])
        return self.say("", action={"type": "confirm"})

    def calls(self, name):
        return [c for c in self.tools.calls if c[0] == name]

    def test_one_click_takes_a_request_all_the_way_to_telegram_with_nothing_asked(self):
        self.start()
        self.assertEqual(len(self.calls("create")), 1)
        self.sheet_arrives()
        self.assertEqual(self.run_until("done"), "done")
        run = self.sess()["creator_run"]
        self.assertEqual(run["step"], "done")
        self.assertEqual([c[0] for c in self.tools.calls if c[0] in ("create", "judge", "review", "pack_add", "telegram_send")],
                         ["create", "judge", "review", "pack_add", "telegram_send"])
        approve = self.calls("review")[0]
        self.assertEqual((approve[2], approve[3]), ("APPROVE", list(range(1, 10))))
        self.assertIn("t.me/addstickers", self.sess()["messages"][-1]["text"])
        self.assertEqual(len(self.calls("create")), 1, "the run never pays twice")

    def test_the_price_of_the_whole_run_is_shown_once_in_the_plan(self):
        self.settings(scope="video")
        m = self.say("make me falcon stickers")
        self.assertEqual(m["cards"][0]["estimate"], 10.0)
        self.assertEqual(m["cards"][0]["creator"], {"scope": "video", "bypass": True, "sheet": 2.0, "video": 8.0})
        self.assertFalse(self.calls("create"), "nothing is spent before the go-ahead")

    def test_without_bypass_it_waits_for_one_click_at_the_approval(self):
        self.settings(bypass=False)
        self.start()
        self.sheet_arrives()
        self.assertEqual(self.run_until("waiting"), "waiting")
        self.assertFalse(self.calls("review"), "nothing is approved on the person's behalf without bypass")
        self.assertFalse(self.calls("telegram_send"))
        self.say("", action={"type": "creator_go"})
        self.assertEqual(self.run_until("done"), "done")
        self.assertTrue(self.calls("review"))

    def test_typing_continue_is_the_same_click(self):
        self.settings(bypass=False)
        self.start()
        self.sheet_arrives()
        self.run_until("waiting")
        self.say("continue")
        self.assertEqual(self.run_until("done"), "done")

    def test_a_cell_python_blocked_stops_the_run_even_with_bypass(self):
        self.start()
        self.sheet_arrives(failed=(4,))
        self.assertEqual(self.run_until("stopped"), "stopped")
        run = self.sess()["creator_run"]
        self.assertIn("S4", run["stop"]["why"])
        self.assertFalse(self.calls("review") or self.calls("pack_add") or self.calls("telegram_send"))
        self.assertEqual([c["action"] for c in run["stop"]["chips"]], ["creator_allow", "creator_skip", "creator_stop"])
        self.say("", action={"type": "creator_skip", "indexes": [4]})
        self.assertEqual(self.run_until("done"), "done")
        self.assertEqual(self.calls("review")[0][3], [1, 2, 3, 5, 6, 7, 8, 9])

    def test_a_judgement_block_offers_use_it_anyway_and_the_run_finishes(self):
        """CLAUDE.md rule 10 (2026-10-03): a judgement-call block is never a dead end. The owner hit exactly this: the run stopped and the only
        choices were drop or abandon. `inside_cell` is in `verify.OVERRIDABLE['still']`, so it may be allowed."""
        self.start()
        self.sheet_arrives(failed=(4,))
        self.assertEqual(self.run_until("stopped"), "stopped")
        run = self.sess()["creator_run"]
        chip = run["stop"]["chips"][0]
        self.assertEqual((chip["action"], chip["indexes"], chip["kind"]), ("creator_allow", [4], "still"))
        self.assertIn("use it anyway", run["stop"]["why"].lower())
        self.say("", action={"type": "creator_allow", "indexes": [4]})
        self.assertEqual(self.run_until("done"), "done")
        self.assertEqual(self.calls("allow")[0][2:], ([4], "still", True), "the permission goes through the engine, recorded")
        self.assertIn(4, self.calls("review")[0][3], "the allowed sticker is back in the set, not dropped")

    def test_a_technical_block_offers_no_allow_and_says_why(self):
        """Telegram's own limits (or an empty cell) are final: no allow chip, and the sentence says so rather than reading as a dead end."""
        s = self.sess()
        s["settings"]["creator"] = {**s["settings"]["creator"], "bypass": True}
        self.store.save(s)
        self.start()
        g = self.sheet_arrives()
        g["stickers"][3]["status"], g["stickers"][3]["reason"] = "FAILED", "dimensions"
        self.tools.allowable = lambda gid, kind="still", allow=True: []      # the engine reports nothing allow-able
        self.assertEqual(self.run_until("stopped"), "stopped")
        run = self.sess()["creator_run"]
        self.assertNotIn("creator_allow", [c["action"] for c in run["stop"]["chips"]])
        self.assertIn("final", run["stop"]["why"])
        self.assertEqual([c["action"] for c in run["stop"]["chips"]], ["creator_skip", "creator_stop"])

    def test_the_stop_message_carries_the_picture_of_what_was_rejected(self):
        """A person cannot judge what they cannot see: the stop message attaches the batch's carousel, not only the run card."""
        self.start()
        self.sheet_arrives(failed=(4,))
        self.run_until("stopped")
        msg = [m for m in self.sess()["messages"] if m["role"] == "assistant"][-1]
        self.assertEqual([c["type"] for c in msg["cards"]], ["creator", "generation"])
        self.assertEqual(msg["cards"][1]["generation"], "G050")

    def test_a_stopped_run_still_shows_the_carousel_when_telegram_is_missing(self):
        self.tools.telegram = False
        self.start()
        self.sheet_arrives()
        self.assertEqual(self.run_until("stopped"), "stopped")
        msg = [m for m in self.sess()["messages"] if m["role"] == "assistant"][-1]
        self.assertIn("generation", [c["type"] for c in msg["cards"]])

    def test_an_engine_error_is_not_pictured(self):
        self.start()
        self.tools.job_status["J001"], self.tools.job_errors["J001"] = "FAILED", "provider said no"
        self.assertEqual(self.run_until("stopped"), "stopped")
        msg = [m for m in self.sess()["messages"] if m["role"] == "assistant"][-1]
        self.assertEqual([c["type"] for c in msg["cards"]], ["creator"], "nothing was drawn, so nothing to show")

    def test_a_blocked_animation_offers_use_it_anyway_too(self):
        self.settings(scope="video")
        self.start()
        g = self.sheet_arrives()
        for _ in range(4):
            self.agent.creator_tick(self.sid)
        for s in g["stickers"]:
            s["anim_status"] = "READY"
        g["stickers"][2]["anim_status"] = "FAILED"
        self.assertEqual(self.run_until("stopped"), "stopped")
        chip = self.sess()["creator_run"]["stop"]["chips"][0]
        self.assertEqual((chip["action"], chip["indexes"], chip["kind"]), ("creator_allow", [3], "animation"))
        self.say("", action={"type": "creator_allow", "indexes": [3]})
        self.assertEqual(self.run_until("done"), "done")
        self.assertEqual(self.calls("allow")[0][2:], ([3], "animation", True))

    def test_a_sticker_the_vision_model_would_reject_stops_the_run_and_the_person_decides(self):
        self.tools.judge_rejects = [2]
        self.start()
        self.sheet_arrives()
        self.assertEqual(self.run_until("stopped"), "stopped")
        self.assertIn("S2", self.sess()["creator_run"]["stop"]["why"])
        self.say("", action={"type": "creator_skip", "indexes": [2]})
        self.assertEqual(self.run_until("done"), "done")
        rev = self.calls("review")
        self.assertEqual(rev[0][2:4], ("REJECT", [2]), "the sticker the person dropped is rejected by their decision, never deleted")
        self.assertEqual(rev[1][2], "APPROVE")
        self.assertNotIn(2, rev[1][3])

    def test_continuing_with_the_rejected_sticker_is_the_persons_call(self):
        self.tools.judge_rejects = [2]
        self.start()
        self.sheet_arrives()
        self.run_until("stopped")
        self.say("", action={"type": "creator_force"})
        self.assertEqual(self.run_until("done"), "done")
        self.assertIn(2, self.calls("review")[0][3])

    def test_with_vision_off_the_check_is_skipped_not_faked(self):
        s = self.sess()
        s["settings"]["allow_vlm"] = False
        self.store.save(s)
        self.start()
        self.sheet_arrives()
        self.run_until("done")
        self.assertFalse(self.calls("judge"))

    def test_a_blocked_sheet_stops_with_the_retry_button_and_the_run_follows_the_new_sheet(self):
        self.start()
        g = self.sheet_arrives()
        g["problem"] = {"check": "grid_detected", "title": "The sheet came back, but Python could not find the grid", "why": "found 3x2", "fix": "x", "received": {"job": "J001"}}
        for s in g["stickers"]:
            s["status"] = "FAILED"
        self.assertEqual(self.run_until("stopped"), "stopped")
        chips = self.sess()["creator_run"]["stop"]["chips"]
        self.assertEqual(chips[0], {"label": "Try the sheet again", "action": "retry_sheet", "generation": "G050"})
        self.say("", action={"type": "retry_sheet", "generation": "G050"})
        run = self.sess()["creator_run"]
        self.assertEqual((run["status"], run["step"], run["job"]), ("running", "sheet", "J002"))
        self.assertEqual(len(self.calls("create")), 2)

    def test_a_failed_sheet_job_stops_the_run_and_says_nothing_more_was_spent(self):
        self.start()
        self.tools.job_status["J001"], self.tools.job_errors["J001"] = "FAILED", "provider said no"
        self.assertEqual(self.run_until("stopped"), "stopped")
        self.assertIn("Nothing more was spent", self.sess()["creator_run"]["stop"]["why"])

    def test_telegram_not_connected_stops_before_sending_and_keeps_the_pack(self):
        self.tools.telegram = False
        self.start()
        self.sheet_arrives()
        self.assertEqual(self.run_until("stopped"), "stopped")
        run = self.sess()["creator_run"]
        self.assertEqual(run["stop"]["kind"], "telegram")
        self.assertTrue(self.calls("pack_add"))
        self.assertFalse(self.calls("telegram_send"))
        self.tools.telegram = True
        self.say("", action={"type": "creator_go"})
        self.assertEqual(self.run_until("done"), "done")
        self.assertEqual(len(self.calls("pack_add")), 1, "the pack is made once; only the sending is tried again")

    def test_full_video_stops_if_the_price_rose_and_goes_on_when_the_person_accepts(self):
        self.settings(scope="video")
        self.start()
        self.sheet_arrives()
        self.tools.video_estimate = 20.0                                       # the price changed after the plan was shown
        self.assertEqual(self.run_until("stopped"), "stopped")
        self.assertEqual(self.sess()["creator_run"]["stop"]["kind"], "price")
        self.assertFalse(self.calls("animate"), "no paid call beyond the price the person saw")
        self.say("", action={"type": "creator_force_video"})
        self.agent.creator_tick(self.sid)
        self.assertEqual(len(self.calls("animate")), 1)

    def test_full_video_animates_once_then_approves_the_animations_and_sends(self):
        self.settings(scope="video")
        self.start()
        g = self.sheet_arrives()
        for _ in range(6):
            self.agent.creator_tick(self.sid)
        self.assertEqual(len(self.calls("animate")), 1)
        self.assertEqual(self.sess()["creator_run"]["step"], "video")
        for s in g["stickers"]:
            s["anim_status"] = "READY"
        self.assertEqual(self.run_until("done"), "done")
        anim = [c for c in self.calls("review") if len(c) > 4 and c[4] == "anim"]
        self.assertEqual(len(anim), 1)
        self.assertEqual(len(self.calls("animate")), 1)

    def test_an_animation_python_blocked_stops_the_run(self):
        self.settings(scope="video")
        self.start()
        g = self.sheet_arrives()
        for _ in range(4):
            self.agent.creator_tick(self.sid)
        for s in g["stickers"]:
            s["anim_status"] = "READY"
        g["stickers"][2]["anim_status"] = "FAILED"
        self.assertEqual(self.run_until("stopped"), "stopped")
        self.assertIn("S3", self.sess()["creator_run"]["stop"]["why"])
        self.assertFalse(self.calls("telegram_send"))

    def test_stop_ends_the_run_and_nothing_else_happens(self):
        self.start()
        self.say("", action={"type": "creator_stop"})
        self.assertEqual(self.sess()["creator_run"]["status"], "stopped")
        self.sheet_arrives()
        self.agent.creator_tick(self.sid)
        self.assertFalse(self.calls("pack_add") or self.calls("telegram_send"))

    def test_a_second_request_while_a_run_is_going_is_refused_not_started(self):
        self.start()
        m = self.say("make me teddy stickers")
        self.assertIn("still working", m["text"])
        self.assertEqual(len(self.calls("create")), 1)

    def test_with_the_creator_off_the_plan_is_the_normal_one(self):
        self.settings(on=False)
        m = self.say("make me falcon stickers")
        self.assertNotIn("creator", m["cards"][0])
        self.assertEqual(m["chips"][0]["label"], "Create it")


if __name__ == "__main__":
    unittest.main()
