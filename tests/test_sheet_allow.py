"""G3 judgement calls use the one waiver catalogue; no providers and no real media render."""
import tempfile
import json
import time
import unittest
from pathlib import Path
from unittest import mock

from stickerloom.engine import verify
from stickerloom.engine.config import EngineConfig
from stickerloom.flow import gates, pipeline as pl
from stickerloom.agent import creator
from PIL import Image
from tests.test_golden import Api


def check(name, stage="video_sheet", **extra):
    return verify.Check(name, stage, "BLOCK", False, note="test judgement", **extra).to_dict()


class SheetAllows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)
        self.res = {"stage": "video_sheet_built", "reviews": {"video_sheet": {}, "pack": None},
                    "stickers": [{"index": 1, "key": "test", "status": "READY", "history": []}], "video_sheets": [
                        {"id": "A1", "slots": [1], "file": "sheet.png", "status": "BUILT", "verify": [check("no_outline_on_sheet")],
                         "blocked": "no_outline_on_sheet", "video": None}]}
        self.cfg = EngineConfig()
        pl.gen_dir(self.out, 1).mkdir()
        pl.write_result(self.out, 1, self.res)

    def test_catalogue_and_reversible_human_history(self):
        self.assertEqual(set(verify.OVERRIDABLE["video_sheet"]), {"no_outline_on_sheet", "video_specs", "layout_match"})
        self.assertEqual(gates.allow_info(self.res)["video_sheet"]["can"], ["A1"])
        for _ in range(2):
            gates.allow_sheet(self.out, 1, ["A1"], True, self.cfg)
            res = pl.read_result(self.out, 1)
            v = res["video_sheets"][0]
            self.assertIsNone(v["blocked"])
            self.assertEqual((v["verify"][0]["ok"], v["verify"][0]["severity"]), (False, "WARN"))
            self.assertEqual(v["verify"][0]["detail"].count("allowed by you"), 1)
            self.assertEqual(gates.allow_info(res)["video_sheet"]["undo"], ["A1"])
            gates.allow_sheet(self.out, 1, ["A1"], False, self.cfg)
        res = pl.read_result(self.out, 1)
        self.assertEqual(res["video_sheets"][0]["blocked"], "no_outline_on_sheet")
        self.assertEqual([h["actor"] for h in res["video_sheets"][0]["history"]], ["human"] * 4)
        self.assertEqual(len(res["stickers"][0]["history"]), 4)

    def test_allow_returned_video_reslices_same_sheet_and_keeps_both_warnings(self):
        v = self.res["video_sheets"][0]
        v.update(status="VIDEO_BLOCKED", video="held.mp4", verify=[], blocked=None,
                 video_checks=[check("video_specs", "video"), check("layout_match", "video")], block="video_specs")
        pl.write_result(self.out, 1, self.res)
        with mock.patch.object(gates, "slice_video") as cut:
            gates.allow_sheet(self.out, 1, ["A1"], True, self.cfg)
        cut.assert_called_once_with(self.out, 1, "A1", self.cfg, 0.0)
        v = pl.read_result(self.out, 1)["video_sheets"][0]
        self.assertEqual(v["video"], "held.mp4")
        self.assertTrue(all(not c["ok"] and c["severity"] == "WARN" and "allowed by you" in c["detail"] for c in v["video_checks"]))

    def test_hard_failures_and_crashes_are_never_allowed(self):
        v = self.res["video_sheets"][0]
        for bad in (check("video_decodes", "video"), check("slots_match_approved"), check("no_outline_on_sheet", detail={"error": "crashed"})):
            v["verify"] = [bad]
            self.assertIsNotNone(gates.sheet_problem(self.res, v))
            self.assertEqual(gates.allow_info(self.res)["video_sheet"]["can"], [])
        v["verify"] = [check("no_outline_on_sheet")]
        self.res["reviews"]["pack"] = {"decision": "APPROVE"}
        self.assertIn("final", gates.sheet_problem(self.res, v))

    def test_creator_stop_has_explicit_free_sheet_action_and_preserves_string_id(self):
        card = {"allow": gates.allow_info(self.res)}
        run = {"generation": "G001", "step": "video", "status": "running", "log": []}
        self.assertTrue(creator._stop_sheet(None, run, card))
        chip = run["stop"]["chips"][0]
        self.assertEqual((chip["action"], chip["indexes"]), ("creator_allow_sheet", ["A1"]))
        creator.resume(run, chip["action"], chip["indexes"])
        self.assertEqual(run["pending_allow"], {"indexes": ["A1"], "kind": "video_sheet", "allow": True})

    def test_creator_waits_for_the_free_sheet_override_before_rejudging(self):
        tools = mock.Mock()
        tools.allow.return_value = {"done": ["A1"]}
        tools.processing.return_value = True
        run = {"generation": "G001", "step": "video", "status": "running", "log": [],
               "pending_allow": {"indexes": ["A1"], "kind": "video_sheet", "allow": True}}
        with mock.patch.object(creator, "_step") as step:
            creator.advance(tools, run, False, None)
            creator.advance(tools, run, False, None)
            step.assert_not_called()
            tools.processing.return_value = False
            creator.advance(tools, run, False, None)
            step.assert_called_once()
        tools.allow.assert_called_once_with("G001", ["A1"], "video_sheet", True)

    def test_every_later_slice_uses_waiver_but_a_decode_failure_stops_it(self):
        v = self.res["video_sheets"][0]
        v.update(status="VIDEO_RETURNED", video="held.mp4", layout="layout.json", verify=[], blocked=None,
                 sheet_override=["video_specs", "layout_match"])
        d = pl.gen_dir(self.out, 1)
        Image.new("RGB", (8, 8)).save(d / "sheet.png")
        (d / "layout.json").write_text(json.dumps({"slots": []}))
        for bad in (False, True):
            checks = [verify.Check("video_specs", "video", "BLOCK", False), verify.Check("layout_match", "video", "BLOCK", False)]
            if bad:
                checks.append(verify.Check("video_decodes", "video", "BLOCK", False))
            pl.write_result(self.out, 1, self.res)
            with mock.patch.object(gates, "check_returned_video", return_value=checks), mock.patch.object(gates, "process_video", return_value=[]) as cut:
                gates.slice_video(self.out, 1, "A1", self.cfg)
            result = pl.read_result(self.out, 1)
            self.assertFalse(result.get("error"), result.get("error"))
            v2 = result["video_sheets"][0]
            self.assertEqual(v2["status"], "VIDEO_BLOCKED" if bad else "SLICED")
            self.assertEqual(cut.call_count, 0 if bad else 1)
            self.assertTrue(all(not c["ok"] and c["severity"] == "WARN" for c in v2["video_checks"][:2]))


class SheetAllowRoutes(Api):
    def test_g3_allow_http_uses_sheet_ids_bulk_undo_and_busy_words(self):
        gid = 901
        pl.gen_dir(self.c.out, gid).mkdir(exist_ok=True)
        res = {"stage": "video_sheet_built", "reviews": {"pack": None}, "stickers": [{"index": 1, "key": "test", "status": "READY"}],
               "video_sheets": [{"id": "A1", "slots": [1], "status": "BUILT", "video": None, "blocked": "no_outline_on_sheet", "verify": [check("no_outline_on_sheet")]}]}
        pl.write_result(self.c.out, gid, res)
        route = f"/api/generations/{gid}/allow"
        self.assertEqual(self.req("POST", route, {"kind": "video_sheet", "sheet": "A2"})[0], 404)
        with self.c.lock:
            code, msg = self.req("POST", route, {"kind": "video_sheet", "sheet": "A1"})
        self.assertEqual(code, 409)
        self.assertIn("job", msg["error"])
        for allow in (True, False):
            code, result = self.req("POST", route, {"kind": "video_sheet", "all": True, "allow": allow})
            self.assertEqual((code, result["indexes"]), (202, ["A1"]))
            for _ in range(100):
                if not self.c.lock.locked():
                    break
                time.sleep(.01)
            v = pl.read_result(self.c.out, gid)["video_sheets"][0]
            self.assertEqual(v["verify"][0]["severity"], "WARN" if allow else "BLOCK")
