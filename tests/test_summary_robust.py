"""The generations list tolerates a batch whose result.json has no "error" key (flow/pipeline.py `summary`): one old or
partially-written batch must not fail the whole GET /api/generations (which also feeds the Settings health card)."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from stickerloom.flow import pipeline as pl


def _result(**kw):
    base = {"generation_id": "G001", "prompt": "a cat", "stage": "sliced", "stickers": [],
            "source": {"subject": "cat", "variant": 1}}
    base.update(kw)
    return base


class SummaryRobustTests(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    def _write(self, gid, res):
        d = pl.gen_dir(self.out, gid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "result.json").write_text(json.dumps(res), encoding="utf-8")

    def test_a_batch_without_an_error_key_is_listed_with_error_none(self):
        self._write(1, _result())
        rows = pl.summary(self.out)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["error"])
        self.assertEqual(rows[0]["generation_id"], "G001")

    def test_a_batch_with_an_error_keeps_it(self):
        self._write(1, _result(error="boom"))
        self.assertEqual(pl.summary(self.out)[0]["error"], "boom")


if __name__ == "__main__":
    unittest.main()
