import copy
import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
from stickerloom.flow import gates, pipeline as pl
from stickerloom.engine.config import EngineConfig


class ReloopTests(unittest.TestCase):
    def test_cli_skips_missing_results_and_continues(self):
        from stickerloom import cli
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"STICKERLOOM_OUT": tmp}):
            with patch.object(pl, "list_ids", return_value=[1, 6, 7]), patch.object(gates, "reloop", side_effect=[{"animations": 1, "pack_copies": 0}, pl.PipelineError("No generation G006", 404), {"animations": 1, "pack_copies": 0}]) as run:
                self.assertEqual(cli.main(["reloop", "all"]), 0)
                self.assertEqual(run.call_count, 3)
    def test_reprocessing_keeps_human_decisions_and_pack_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            d = out / "G001"
            d.mkdir()
            (d / "video.mp4").write_bytes(b"stored video")
            before = {"generation_id": "G001", "stage": "pack_final", "error": None,
                      "video_sheets": [{"id": "A1", "status": "SLICED", "video": "video.mp4"}],
                      "stickers": [{"index": 1, "webm": "one.webm", "anim_status": "READY", "review": {"still": "APPROVED", "anim": "APPROVED"}},
                                   {"index": 2, "webm": "two.webm", "anim_status": "READY", "review": {"still": "APPROVED", "anim": "REJECTED"}},
                                   {"index": 3, "anim_status": "NOT_REQUESTED", "review": {"still": "REJECTED", "anim": "PENDING"}}]}
            after = copy.deepcopy(before)
            after["stage"] = "video_sliced"
            for st in after["stickers"][:2]:
                st["review"]["anim"] = "PENDING"
            lib = MagicMock()
            lib.refresh_from_generation.return_value = 1
            with patch.object(pl, "read_result", side_effect=[before, after]), patch.object(pl, "write_result") as write, patch.object(gates, "reslice"):
                r = gates.reloop(out, 1, EngineConfig(), lib)
                saved = write.call_args.args[2]
            self.assertEqual(saved["stage"], "pack_final")
            self.assertEqual([s["review"] for s in saved["stickers"]], [s["review"] for s in before["stickers"]])
            self.assertEqual((r["animations"], r["pack_copies"]), (2, 2))

    def test_edited_or_missing_source_batches_are_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            res = {"video_sheets": [{"id": "A1", "status": "SLICED", "video": "missing.mp4"}], "stickers": [{"index": 1, "edited": True, "webm": "one.webm"}]}
            with patch.object(pl, "read_result", return_value=res), patch.object(gates, "reslice") as cut:
                self.assertIn("edited", gates.reloop(out, 1, EngineConfig())["skipped"])
                res["stickers"][0]["edited"] = False
                self.assertIn("missing", gates.reloop(out, 1, EngineConfig())["skipped"])
                cut.assert_not_called()

    def test_failed_reprocessing_keeps_human_reviews_and_does_not_refresh_packs(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            d = out / "G001"
            d.mkdir()
            (d / "video.mp4").write_bytes(b"stored video")
            before = {"generation_id": "G001", "stage": "pack_final", "error": None,
                      "video_sheets": [{"id": "A1", "status": "SLICED", "video": "video.mp4"}],
                      "stickers": [{"index": 1, "webm": "one.webm", "anim_status": "READY", "review": {"still": "APPROVED", "anim": "APPROVED"}}]}
            after = copy.deepcopy(before)
            after.update(error="source could not be decoded", stage="video_returned")
            after["stickers"][0].update(anim_status="FAILED", review={"still": "APPROVED", "anim": "BLOCKED"})
            lib = MagicMock()
            with patch.object(pl, "read_result", side_effect=[before, after]), patch.object(pl, "write_result") as write, patch.object(gates, "reslice"):
                result = gates.reloop(out, 1, EngineConfig(), lib)
            self.assertEqual(write.call_args.args[2]["stickers"][0]["review"]["anim"], "APPROVED")
            self.assertEqual(result["animations"], 0)
            self.assertIn("could not be decoded", result["error"])
            lib.refresh_from_generation.assert_not_called()
