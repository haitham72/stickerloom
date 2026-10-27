"""A kept sticker whose stored picture is gone (a batch cut before `source/plain/` was kept, or rejected before the blue-screen logic) must not crash the video-sheet preview:
the route answers in words (409) naming the sticker, never a 500 FileNotFoundError."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stickerloom.engine.config import EngineConfig
from stickerloom.flow import gates, pipeline as pl


class PreviewWithAMissingPicture(unittest.TestCase):
    def test_a_missing_picture_is_a_plain_409_not_a_crash(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "G007"
            (d / "source" / "plain").mkdir(parents=True)                                  # the folder exists, S1.png and S3.png do not
            (d / "source" / "plain" / "S2.png").write_bytes(b"x")                          # one picture is there: the message must name only the missing ones
            res = {"grid": [3, 3], "stickers": [{"index": i, "status": "READY", "review": {"still": "APPROVED"}} for i in (1, 2, 3)]}
            with mock.patch.object(pl, "read_result", return_value=res), mock.patch.object(pl, "gen_dir", return_value=d), \
                    mock.patch.object(pl, "cfg_for", side_effect=lambda r, c: c):
                with self.assertRaises(pl.PipelineError) as cm:
                    gates.preview_sheet(Path(td), 7, EngineConfig(), 0.8, 300)
        self.assertEqual(cm.exception.code, 409)
        msg = str(cm.exception)
        self.assertIn("S1", msg)
        self.assertIn("S3", msg)
        self.assertNotIn("S2", msg.replace("S2.png", ""))


if __name__ == "__main__":
    unittest.main()
