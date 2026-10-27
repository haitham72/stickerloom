"""Library > Reactions > Create reads the bank with per-token state; building starts from a free default sprite."""
import json
import tempfile
import unittest
from pathlib import Path

from stickerloom.flow import particle_sets as ps


def _write_set(out: Path, pid: str, **kw):
    d = out / "particles" / pid
    (d / "cells").mkdir(parents=True, exist_ok=True)
    rec = {"id": pid, "created": 1.0, "owner": [], "elements": [], "cells": [], "renders": [], "history": [],
           "motion": {}, "source": {"kind": "drawn"}, "credits": 0, **kw}
    (d / "set.json").write_text(json.dumps(rec), encoding="utf-8")
    return d


class ReactionBankTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        _write_set(self.out, "P001", elements=["laugh"], created=3.0,
                   renders=[{"id": "R001", "status": "READY"}])
        _write_set(self.out, "P002", elements=["cry"], created=2.0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_bank_marks_finished_and_unclaimed(self):
        bank = {m["token"]: m for m in ps.bank_states(self.out, None)}
        self.assertEqual(bank["laugh"]["state"], "finished")
        self.assertEqual(bank["laugh"]["set_id"], "P001")
        self.assertEqual(bank["cry"]["state"], "unclaimed")
        self.assertEqual(bank["cry"]["set_id"], "P002")
        self.assertEqual(bank["school"]["state"], "unclaimed")
        self.assertIsNone(bank["school"]["set_id"])
        self.assertEqual(len(bank), len(ps.emoji_motions(self.out, None)), "every bank token is listed")

    def test_export_lottie_returns_a_real_downloadable_document(self):
        with self.assertRaises(ps.SetError) as no_sprite:              # nothing to fly: an honest 409, never a placeholder shape
            ps.export_lottie(self.out, None, "P002", preset="burst")
        self.assertEqual(no_sprite.exception.code, 409)
        ps.ensure_default_sprite(self.out, None, "P002")
        r = ps.export_lottie(self.out, None, "P002", preset="burst")
        self.assertEqual(r["url"], "/out/particles/P002/renders/L001.lottie.json")
        doc = json.loads((self.out / "particles" / "P002" / r["file"]).read_text(encoding="utf-8"))
        self.assertEqual(doc["v"], "5.7.4")
        self.assertGreater(len(doc["layers"]), 0)
        self.assertTrue(all(l["ty"] == 2 and l["ks"]["p"]["k"] for l in doc["layers"]), "image layers with position keyframes")
        self.assertEqual(len(doc["assets"]), 1, "the set's one picked sprite, embedded once")
        self.assertEqual(r["layers"], len(doc["layers"]))

    def test_default_sprite_is_free_and_idempotent(self):
        first = ps.ensure_default_sprite(self.out, None, "P002")
        self.assertTrue(first["default"])
        self.assertEqual(first["n_picked"], 1)
        cell = [c for c in first["cells"] if c["picked"]][0]
        self.assertTrue((self.out / "particles" / "P002" / cell["file"]).is_file())
        second = ps.ensure_default_sprite(self.out, None, "P002")
        self.assertFalse(second["default"])
        self.assertEqual(second["n_picked"], 1, "no second sprite is added")


if __name__ == "__main__":
    unittest.main()
