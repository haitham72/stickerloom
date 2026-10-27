"""The lifecycle of an effect E### on a synthetic pack: create, analyse (table, no model), edit the pieces, the simulated burst (preview, render), a returned clip cut into cells, and the
add-to-pack click. No real model, no real video, no provider."""
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

from stickerloom.engine.config import EngineConfig
from stickerloom.flow import effects as fx
from stickerloom.media.library import Library
from tests.test_effect_video import make_clip

CFG = EngineConfig()


def sticker_png(colour=(220, 40, 60)):
    im = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    a = np.zeros((128, 128, 4), np.uint8)
    yy, xx = np.ogrid[:128, :128]
    a[(yy - 64) ** 2 + (xx - 64) ** 2 < 50 ** 2] = (*colour, 255)
    b = io.BytesIO()
    Image.fromarray(a, "RGBA").save(b, "PNG")
    return b.getvalue()


class EffectsFlowTests(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        self.lib = Library(self.out)
        self.pack = self.lib.create_pack("Fruits")
        self.s = [self.lib.add_bytes(self.pack["id"], sticker_png((220 - 40 * i, 40 + 30 * i, 60)), "png", n, "static", e)["id"]
                  for i, (n, e) in enumerate([("Strawberry happy", "🍓"), ("Strawberry sad", "🍓"), ("Heart", "❤")])]

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    def test_create_validates_and_stores_an_addressable_record(self):
        e = fx.create(self.out, self.lib, pack_id=self.pack["id"], sticker_ids="all", mode="sim")
        self.assertEqual((e["id"], e["status"], len(e["stickers"]), e["grid"]), ("E001", "NEW", 3, [2, 2]))
        self.assertTrue((self.out / "effects" / "E001" / "src" / f"{self.s[0]}.png").is_file())
        self.assertEqual(fx.read(self.out, "E001")["pack_name"], "Fruits")
        self.assertEqual([r["id"] for r in fx.list_effects(self.out)], ["E001"])
        for bad, code in ((dict(pack_id="nope", sticker_ids="all"), 404), (dict(pack_id=self.pack["id"], sticker_ids=["zzz"]), 404), (dict(pack_id=self.pack["id"], sticker_ids="all", mode="x"), 400),
                          (dict(pack_id=self.pack["id"], sticker_ids="all", grid=(4, 4)), 400)):
            with self.assertRaises(fx.EffectError) as cm:
                fx.create(self.out, self.lib, **bad)
            self.assertEqual(cm.exception.code, code)
        with self.assertRaises(fx.EffectError):
            fx.read(self.out, "E999")

    def test_analyse_groups_by_subject_with_the_table_and_says_no_model_was_used(self):
        fx.create(self.out, self.lib, pack_id=self.pack["id"], sticker_ids="all")
        e = fx.analyse(self.out, "E001")
        self.assertEqual((e["status"], e["analysed_by"]), ("READY", "table"))
        subjects = {g["subject"]: g for g in e["groups"]}
        self.assertEqual(set(subjects), {"strawberry", "heart"})
        self.assertEqual(len(subjects["strawberry"]["stickers"]), 2, "two strawberry stickers share one group")
        self.assertEqual(subjects["strawberry"]["key"], "blue", "leaves are green")
        self.assertTrue(any("not allowed" in n for n in e["notes"]))

    def test_the_person_can_edit_the_pieces_and_the_edit_is_linted(self):
        fx.create(self.out, self.lib, pack_id=self.pack["id"], sticker_ids="all")
        e = fx.analyse(self.out, "E001")
        gid = e["groups"][0]["id"]
        e = fx.set_pieces(self.out, "E001", gid, elements=["gold bars", "small diamonds"], style="flat, bold")
        g = next(x for x in e["groups"] if x["id"] == gid)
        self.assertEqual((g["elements"], g["by"], g["key"]), (["gold bars", "small diamonds"], "you", "green"))
        with self.assertRaises(fx.EffectError):
            fx.set_pieces(self.out, "E001", gid, elements=["a sticker of a cat"])
        self.assertEqual(fx.video_plan(self.out, "E001", gid)["cells"], 4)
        self.assertIn("gold bars", fx.video_plan(self.out, "E001", gid, grid=(3, 3))["prompt"])

    def test_the_simulated_burst_previews_renders_and_adds_to_the_pack(self):
        fx.create(self.out, self.lib, pack_id=self.pack["id"], sticker_ids="all", mode="sim")
        fx.analyse(self.out, "E001")
        pv = fx.sim_preview(self.out, self.lib, "E001", self.s[0], {"gravity": 1.5, "magnitude": 1.2, "vortex": 0.5})
        self.assertTrue((self.out / "effects" / "E001" / pv["file"]).is_file())
        self.assertEqual(pv["params"]["vortex"], 0.5)
        again = fx.sim_preview(self.out, self.lib, "E001", self.s[0], {"gravity": 1.5, "magnitude": 1.2, "vortex": 0.5})
        self.assertEqual(again["file"], pv["file"], "the same sliders give the same file (cached)")
        with self.assertRaises(fx.EffectError):
            fx.sim_preview(self.out, self.lib, "E001", self.s[0], {"nonsense": 1})
        r = fx.sim_render(self.out, self.lib, "E001", self.s[0], CFG, {"gravity": 1.5})
        self.assertEqual((r["status"], r["blocks"]), ("READY", []))
        self.assertLessEqual(r["bytes"], CFG.video_max_bytes)
        self.assertEqual(r["metrics"]["frames"], 90)
        added = fx.add_to_pack(self.out, self.lib, "E001", [r["id"]])
        pack = next(p for p in self.lib.snapshot()["packs"] if p["id"] == self.pack["id"])
        fxs = [s for s in pack["stickers"] if (s.get("source") or {}).get("effect") == "E001"]
        self.assertEqual(len(fxs), 1)
        self.assertEqual((fxs[0]["type"], fxs[0]["emoji"], fxs[0]["source"]["source_sticker"]), ("animated", "🍓", self.s[0]), "tagged with the source emoji")
        self.assertTrue(fxs[0]["file"].endswith(".webm"))
        e = fx.read(self.out, "E001")
        self.assertEqual((e["status"], e["history"][-1]["decision"]), ("DONE", "APPROVE"), "the click is recorded as the person's decision")
        self.assertEqual(added["pack_id"], self.pack["id"])

    def test_a_returned_clip_is_cut_into_results_and_added_cell_by_cell(self):
        fx.create(self.out, self.lib, pack_id=self.pack["id"], sticker_ids="all", mode="video")
        e = fx.analyse(self.out, "E001")
        gid = next(g["id"] for g in e["groups"] if g["subject"] == "strawberry")
        mp4 = self.out / "jobs" / "J001" / "result.mp4"
        mp4.parent.mkdir(parents=True)
        make_clip(mp4, key=(0, 0, 255), empty_tail=(True, False, True, True))
        fx.set_pieces(self.out, "E001", gid, elements=["strawberries", "tiny seeds"])          # a screen colour is not forced: the one that is really there wins
        job = {"id": "J001", "result": {"file": "jobs/J001/result.mp4"}, "cost": 4.5}
        e = fx.on_video_done(self.out, "E001", gid, job, CFG)
        res = [r for r in e["results"] if r["group"] == gid]
        self.assertEqual([r["cell"] for r in res], [1, 2, 3, 4])
        self.assertTrue(all(r["status"] == "READY" for r in res), [r["checks"] for r in res])
        self.assertIn("effect_tail_faded", res[1]["warnings"], "the late cell was repaired and says so")
        self.assertEqual(e["video"][gid]["status"], "DONE")
        pairs = fx.targets(e, [r["id"] for r in res])
        self.assertEqual([s["sticker_id"] for s, _ in pairs], [self.s[0], self.s[1]], "the group's two stickers take cells 1 and 2")
        fx.add_to_pack(self.out, self.lib, "E001", [r["id"] for r in res])
        pack = next(p for p in self.lib.snapshot()["packs"] if p["id"] == self.pack["id"])
        self.assertEqual(sum(1 for s in pack["stickers"] if (s.get("source") or {}).get("effect") == "E001"), 2)

    def test_a_result_that_breaks_a_telegram_limit_cannot_be_added_and_nothing_else_is_refused(self):
        fx.create(self.out, self.lib, pack_id=self.pack["id"], sticker_ids=[self.s[2]], mode="sim")
        fx.analyse(self.out, "E001")
        e = fx.read(self.out, "E001")
        e["results"].append({"id": "R001", "mode": "sim", "group": e["groups"][0]["id"], "sticker_id": self.s[2], "file": None, "bytes": 0, "status": "FAILED", "checks": [], "warnings": [], "blocks": ["size_budget"]})
        fx._write(self.out, e)
        with self.assertRaises(fx.EffectError) as cm:
            fx.add_to_pack(self.out, self.lib, "E001", ["R001"])
        self.assertEqual(cm.exception.code, 409)
        self.assertIn("Telegram limit", str(cm.exception))
        with self.assertRaises(fx.EffectError):
            fx.add_to_pack(self.out, self.lib, "E001", [])                                        # nothing selected and nothing READY

    def test_a_video_job_is_text_only_and_carries_its_plan(self):
        from stickerloom.generation import jobs
        fx.create(self.out, self.lib, pack_id=self.pack["id"], sticker_ids="all")
        e = fx.analyse(self.out, "E001")
        gid = e["groups"][0]["id"]
        job = fx.new_video_job(self.out, "E001", gid, user="local")
        self.assertEqual(job["kind"], "video")
        self.assertTrue(job["request"]["t2v"])
        self.assertNotIn("start_image", job["request"])
        self.assertEqual(job["request"]["effect"]["id"], "E001")
        self.assertEqual(fx.read(self.out, "E001")["video"][gid]["job"], job["id"])
        self.assertEqual(jobs.read(self.out, job["id"])["status"], "REQUESTED", "created, not started: the caller shows the price first")


if __name__ == "__main__":
    unittest.main()
