import json
import tempfile
import unittest
from pathlib import Path

from stickerloom.flow import measure


def _st(i, failed=(), px=300, status="READY"):
    return {"index": i, "anim_status": status, "anim_metrics": {"subject_px_in_video": px},
            "anim_report": [{"name": n, "ok": n not in failed, "severity": "BLOCK"} for n in ("inside_slot", "cross_slot", "inside_frame")]}


def _gen(out, gid, sheets, stickers):
    d = Path(out) / f"G{gid:03d}"
    d.mkdir(parents=True)
    (d / "result.json").write_text(json.dumps({"video_sheets": sheets, "stickers": stickers}), encoding="utf-8")


class MeasureCellsTests(unittest.TestCase):
    def test_shares_per_slot_fill(self):
        with tempfile.TemporaryDirectory() as td:
            _gen(td, 1, [{"id": "A1", "status": "SLICED", "video": "v.mp4", "slots": [1, 2, 3, 4], "slot_fill": 0.74}],
                 [_st(1), _st(2, ("inside_slot",)), _st(3, ("cross_slot", "inside_slot"), px=200), _st(4, status="FAILED")])
            _gen(td, 2, [{"id": "A1", "status": "SLICED", "video": "v.mp4", "slots": [1, 2], "slot_fill": 0.66},
                         {"id": "A2", "status": "BUILT", "video": None, "slots": [1, 2], "slot_fill": 0.66}],
                 [_st(1), _st(2)])
            m = measure.measure(Path(td))
        self.assertEqual((m["generations"], m["sheets"], m["cells"], m["flagged"]), (2, 2, 6, 2))
        a, b = m["by_slot_fill"]
        self.assertEqual((a["slot_fill"], a["cells"], a["flagged"], a["flagged_share"]), (0.66, 2, 0, 0.0))
        self.assertEqual((b["slot_fill"], b["cells"], b["flagged"], b["flagged_share"], b["cross_slot"], b["inside_slot"]),
                         (0.74, 4, 2, 0.5, 1, 2))
        self.assertEqual((b["cross_slot_rate"], b["subject_px_min"]), (0.25, 200))
        self.assertIn("flagged", measure.render(m))

    def test_nothing_returned_yet_says_so(self):
        with tempfile.TemporaryDirectory() as td:
            _gen(td, 1, [{"id": "A1", "status": "BUILT", "video": None, "slots": [1], "slot_fill": 0.74}], [_st(1)])
            m = measure.measure(Path(td))
        self.assertEqual(m["cells"], 0)
        self.assertIn("nothing to measure", measure.render(m))

    def test_record_appends_a_block(self):
        with tempfile.TemporaryDirectory() as td:
            _gen(td, 1, [{"id": "A1", "status": "SLICED", "video": "v", "slots": [1], "slot_fill": 0.74}], [_st(1)])
            p = measure.record(Path(td), measure.measure(Path(td)), Path(td) / "docs" / "m.md")
            measure.record(Path(td), measure.measure(Path(td)), p)
            txt = p.read_text(encoding="utf-8")
        self.assertEqual(txt.count("## measure-cells"), 2)
        self.assertEqual(txt.count("# Phase 2 measurements"), 1)


if __name__ == "__main__":
    unittest.main()
