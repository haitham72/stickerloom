"""The particle SET of an effect (docs/architecture.md ("Reactions")): ONE sheet of particles drawn for the whole pack, shared by every sticker.

What is pinned here:
  * the cut: a particle sheet is cut into EXACT equal cells (the layout is the plan's, never detected) and no sticker rule can stop a cell; the real bug (G100: a 2x2 sheet with a white divider
    cross, found "3x3", cells 2-4 FAILED `inside_cell`) is reproduced with a synthetic sheet and shown fixed, while a normal sticker batch behaves exactly as before;
  * the flags: `pl.start(kind="particles")`, `result.json kind`, the job request, `start_from_job`;
  * the record: `effect.set`, the link to EVERY group, `picked`, the pick route, old records;
  * the routes: suggest (fake vision model, consent, the table, the contact sheet), particles_estimate / particles (price first, 409, 202), particles_pick;
  * the static resize: `sprite_px` / `scale` of preview and render, the cache digest.
No provider, no real model: a fake CLI and a fake `complete`."""
import http.client
import io
import json
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np
from PIL import Image

from stickerloom.console.server import serve
from stickerloom.engine import grid as engine_grid, particles, verify
from stickerloom.engine.config import EngineConfig
from stickerloom.engine.sheet import key_sheet, slice_cells
from stickerloom.flow import effects as fx, pipeline as pl, sources
from stickerloom.generation import jobs, tasks
from stickerloom.runtime import cache as cachemod
from stickerloom.vision import effect_plan
from stickerloom.vision.judge import VisionJudge
from tests import synth
from tests.test_effects_flow import sticker_png
from tests.test_golden import shape_sheet
from tests.test_live import Base, png_bytes

CFG = EngineConfig()


# ---------- synthetic particle sheets ----------
def blobs(size, centres, r=60, seed=5, line=None):
    """A noisy green sheet with one round piece per centre (None = an empty cell) and, optionally, a white divider cross of `line` px through the middle (what image models draw)."""
    s = synth.bg(size, seed).copy()
    for i, c in enumerate(centres):
        if c:
            cv2.circle(s, c, r, [synth.YELLOW, synth.RED, (60, 90, 220), (240, 140, 30)][i % 4], -1, cv2.LINE_AA)
    if line:
        m, h = size // 2, line // 2
        s[:, m - h:m + h] = 255
        s[m - h:m + h, :] = 255
    return s


SPARSE = [(150, 300), (720, 300), (420, 900), (1050, 900)]       # 2x2 of 600 px cells: one piece per cell, but 4 column gaps: gutter detection finds 4 columns


def equal_2x2(size):
    return [(c * size // 2, r * size // 2, size // 2, size // 2) for r in range(2) for c in range(2)]


def new_batch(out: Path, inp: Path, sheet: np.ndarray, kind=None, grid="2x2") -> int:
    """A batch started exactly the way `Console.start_from_job` starts one, from `sheet`."""
    f = Path(out).parent / f"sheet-{time.time_ns()}.png"
    f.write_bytes(png_bytes(sheet))
    t = tasks.reserve(out, inp, "particles", grid)
    pick = sources.Pick(subject=tasks.subject_of(t["plan"]), subject_id=t["id"], variant=1, n_variants=1, sheet=f, video=None)
    gid = pl.start(t["prompt"], out, inp, pick=pick, task=t, outline=0, kind=kind)
    pl.run_stills(out, gid, CFG)
    return gid


class Scratch(unittest.TestCase):
    def setUp(self):
        import shutil
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.inp, self.out = self.tmp / "in", self.tmp / "out"
        (self.inp / "Images_gen").mkdir(parents=True)
        (self.inp / "videos_gen").mkdir(parents=True)
        self.out.mkdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


# ---------- the cut ----------
class EqualGridTests(unittest.TestCase):
    def test_the_cells_tile_the_sheet_exactly_even_when_it_does_not_divide(self):
        rects, info = engine_grid.equal_rects(1200, 1200, 2, 2)
        self.assertEqual(rects, [(0, 0, 600, 600), (600, 0, 600, 600), (0, 600, 600, 600), (600, 600, 600, 600)])
        self.assertEqual((info["xs"], info["ys"], info["method"]), ([0, 600, 1200], [0, 600, 1200], "equal"))
        rects, info = engine_grid.equal_rects(1001, 1003, 3, 3)
        self.assertEqual(len(rects), 9)
        self.assertEqual(sum(r[2] for r in rects[:3]), 1001)
        self.assertEqual(sum(r[3] for r in rects[::3]), 1003)
        self.assertLessEqual(max(r[2] for r in rects) - min(r[2] for r in rects), 1)
        self.assertLessEqual(max(r[3] for r in rects) - min(r[3] for r in rects), 1)


class ParticleCutTests(Scratch):
    def test_a_sparse_sheet_that_gutter_detection_mis_reads_is_cut_into_exact_equal_cells(self):
        sheet = blobs(1200, SPARSE)
        self.assertEqual(engine_grid.detect_grid(sheet), (2, 4), "gutter detection reads the gaps between sparse pieces as columns")
        normal = pl.read_result(self.out, new_batch(self.out, self.inp, sheet))
        self.assertEqual([i["check"] for i in normal["sheet_issues"]], ["grid_detected"], "a sticker batch still reports it (and cuts anyway)")
        self.assertNotEqual(normal["source"]["grid"]["rects"], [list(r) for r in equal_2x2(1200)], "its cut follows the gutters")
        gid = new_batch(self.out, self.inp, sheet, kind="particles")
        res = pl.read_result(self.out, gid)
        self.assertEqual(res["kind"], "particles")
        self.assertEqual(res["source"]["grid"]["rects"], [list(r) for r in equal_2x2(1200)], "exactly equal, from the plan's 2x2")
        self.assertEqual((res["source"]["grid"]["xs"], res["source"]["grid"]["method"]), ([0, 600, 1200], "equal"))
        self.assertEqual(res["sheet_issues"], [], "no layout problem exists for a sheet whose layout is known")
        ids = [c["name"] for c in res["verify"]["sheet"]]
        self.assertNotIn("grid_detected", ids)
        self.assertNotIn("sheet_size", ids)
        self.assertEqual([s["status"] for s in res["stickers"]], ["READY"] * 4)
        self.assertEqual([s["metrics"]["cell"] for s in res["stickers"]], [list(r) for r in equal_2x2(1200)])
        self.assertTrue(all(s["png"] and (pl.gen_dir(self.out, gid) / s["png"]).is_file() for s in res["stickers"]))

    def test_a_small_sheet_is_not_a_layout_problem_either(self):
        res = pl.read_result(self.out, new_batch(self.out, self.inp, blobs(800, [(100, 100), (500, 100), (100, 500), (500, 500)], r=40), kind="particles"))
        self.assertEqual((res["sheet_issues"], [s["status"] for s in res["stickers"]]), ([], ["READY"] * 4))
        small = pl.read_result(self.out, new_batch(self.out, self.inp, blobs(800, [(200, 200), (600, 200), (200, 600), (600, 600)], r=40)))
        self.assertIn("sheet_size", [i["check"] for i in small["sheet_issues"]], "a sticker batch still warns about it")

    def test_the_white_divider_cross_of_the_real_g100_no_longer_kills_cells(self):
        sheet = blobs(1200, [(300, 300), (900, 300), (300, 900), (900, 900)], r=100, line=16)
        normal = pl.read_result(self.out, new_batch(self.out, self.inp, sheet))
        self.assertIn("FAILED", [s["status"] for s in normal["stickers"]], "as before: the divider crosses a cell and Python blocks it (inside_cell)")
        res = pl.read_result(self.out, new_batch(self.out, self.inp, sheet, kind="particles"))
        self.assertEqual([s["status"] for s in res["stickers"]], ["READY"] * 4)
        self.assertEqual(res["source"]["grid"]["inset_px"], 12, "2 % of a 600 px cell wipes the divider that sits on the cut")
        for s in res["stickers"]:
            x0, y0, x1, y1 = s["metrics"]["bbox"]
            self.assertLess(max(x1 - x0, y1 - y0), 260, "one round piece, not a piece plus a strip of the divider")

    def test_every_sticker_rule_is_a_warning_for_a_particle_and_stays_a_block_for_a_sticker(self):
        sheet = blobs(1200, [(600, 300), (900, 300), (300, 900), (900, 900)], r=100)          # the first piece crosses the cut between cells 1 and 2
        rects = equal_2x2(1200)
        sticker = slice_cells(key_sheet(sheet, CFG, rects), CFG)
        self.assertEqual((sticker[0].status, sticker[0].reason), ("FAILED", "inside_cell"))
        part = slice_cells(key_sheet(sheet, CFG, rects, 0, True), CFG)
        self.assertEqual([r.status for r in part], ["READY"] * 4)
        self.assertIn("inside_cell", part[0].metrics["warnings"])
        self.assertEqual([c["severity"] for c in part[0].report.checks if c["name"] == "inside_cell"], ["WARN"])
        self.assertIn("a particle is not a sticker", next(c["detail"] for c in part[0].report.checks if c["name"] == "inside_cell"))
        self.assertIsNotNone(part[0].data, "a READY particle has its picture")

    def test_only_a_cell_with_nothing_in_it_still_fails(self):
        sheet = blobs(1200, [(300, 300), None, (300, 900), (900, 900)])
        res = pl.read_result(self.out, new_batch(self.out, self.inp, sheet, kind="particles"))
        self.assertEqual([s["status"] for s in res["stickers"]], ["READY", "FAILED", "READY", "READY"])
        self.assertEqual(res["stickers"][1]["reason"], "empty_subject")
        self.assertIsNone(res["stickers"][1]["png"])

    def test_the_verifier_downgrades_every_block_of_the_still_stage_but_the_blank_cell(self):
        for stage, keep in verify.PARTICLE_KEEP.items():
            blocks = {cid for cid, sev, _, _ in verify.CATALOGUE[stage] if sev == verify.BLOCK}
            self.assertTrue(set(keep) <= blocks, (stage, keep))
        self.assertEqual(verify.PARTICLE_KEEP["sheet"], ("sheet_decodes", "background_is_key"), "a file that does not open, and a sheet with no key screen (one free click)")
        self.assertEqual(verify.PARTICLE_KEEP["still"], ("blank_cell",))

    def test_the_kind_is_only_written_for_a_particle_batch_and_only_that_value_is_accepted(self):
        sheet = blobs(1200, SPARSE)
        plain = pl.read_result(self.out, new_batch(self.out, self.inp, sheet))
        self.assertNotIn("kind", plain, "a sticker batch is written exactly as before")
        self.assertNotIn("inset_px", plain["source"]["grid"])
        gid = new_batch(self.out, self.inp, sheet, kind="particles")
        batches = [b for pack in pl.history(self.out)["items"] for b in pack["batches"]]
        item = next(b for b in batches if b["id"] == gid)
        self.assertEqual(item["kind"], "particles")
        self.assertNotIn("kind", next(b for b in batches if b["id"] != gid))
        with self.assertRaises(pl.PipelineError):
            new_batch(self.out, self.inp, sheet, kind="stickers")

    def test_a_cell_cut_again_alone_keeps_the_particle_rules(self):
        """`pl.recut_cells` ("Use it anyway" of one cell) keys the sheet again: a particle batch must come out the same, divider wipe and warnings-only included."""
        sheet = blobs(1200, [(300, 300), (900, 300), (300, 900), (900, 900)], r=100, line=16)
        gid = new_batch(self.out, self.inp, sheet, kind="particles")
        before = pl.read_result(self.out, gid)["stickers"][0]
        pl.recut_cells(self.out, gid, [1], CFG)
        after = pl.read_result(self.out, gid)["stickers"][0]
        self.assertEqual((after["status"], after["metrics"]["bbox"]), ("READY", before["metrics"]["bbox"]))
        self.assertEqual(after["metrics"]["cell"], before["metrics"]["cell"])

    def test_a_particle_sheet_without_a_key_screen_still_has_its_one_free_click(self):
        grey = np.full((1200, 1200, 3), 128, np.uint8)
        cv2.circle(grey, (300, 300), 80, (250, 220, 20), -1)
        gid = new_batch(self.out, self.inp, grey, kind="particles")
        res = pl.read_result(self.out, gid)
        self.assertEqual({s["reason"] for s in res["stickers"]}, {"background_is_key"})
        pl.recut(self.out, gid, CFG)                                  # "Cut it anyway": the existing free click, nothing new
        again = pl.read_result(self.out, gid)
        self.assertEqual(again["kind"], "particles")
        self.assertNotEqual({s["reason"] for s in again["stickers"]}, {"background_is_key"})


# ---------- the routes and the record ----------
class SetBase(Base):
    """One effect on a real console with the fake CLI: a pack of a strawberry and a heart (two groups)."""

    def setUp(self):
        super().setUp()
        self.srv, self.c = serve(self.out, self.tmp / "in", 0, cfg=EngineConfig(), block=False)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.sheet = shape_sheet(1200, [(x, y) for y in (300, 900) for x in (300, 900)], r=110)
        self.files["png"] = png_bytes(self.sheet)
        self.pack = self.c.lib.create_pack("Fruits")
        self.s1 = self.c.lib.add_bytes(self.pack["id"], sticker_png(), "png", "Strawberry", "static", "🍓")["id"]
        self.s2 = self.c.lib.add_bytes(self.pack["id"], sticker_png((30, 90, 200)), "png", "Heart", "static", "❤")["id"]
        s, j = self.req("POST", "/api/effects", {"pack_id": self.pack["id"], "sticker_ids": [self.s1, self.s2], "mode": "sim"})
        self.eid = j["id"]
        self.until(lambda: self.req("GET", f"/api/effects/{self.eid}")[1].get("status") == "READY", "the analysis")

    def tearDown(self):
        self.c.wait_jobs(90)
        self.srv.shutdown()
        super().tearDown()

    def req(self, method, path, body=None):
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
        r = h.getresponse(); data = r.read(); h.close()
        try:
            return r.status, json.loads(data)
        except ValueError:
            return r.status, data

    def until(self, fn, what, timeout=180):
        end = time.time() + timeout
        while time.time() < end:
            v = fn()
            if v:
                return v
            time.sleep(0.3)
        self.fail("timeout: " + what)

    def effect(self):
        return self.req("GET", f"/api/effects/{self.eid}")[1]

    def creates(self):
        return [c for c in self.cli.calls if c[:2] == ["generate", "create"]]

    def draw(self, elements=("small strawberries", "tiny hearts", "green leaves", "sparkles"), grid="2x2"):
        s, j = self.req("POST", f"/api/effects/{self.eid}/particles", {"grid": grid, "elements": list(elements), "go": True})
        self.assertEqual(s, 202, j)
        return j

    def drawn(self, **kw):
        """Draw, wait for the job, the link and the cut; return (job answer, generation number)."""
        j = self.draw(**kw)
        done = self.until(lambda: (lambda x: x if x.get("generation") and x["status"] == "DONE" else None)(self.req("GET", "/api/jobs/" + j["job"])[1]), "the sheet job")
        gn = int(done["generation"][1:])
        self.until(lambda: (lambda x: x if x["stage"] == "sliced" and not x["busy"] else None)(self.req("GET", f"/api/generations/{gn}")[1]), "the cells")
        return j, gn

    def batch(self, gn, cells, kind=None):
        """A hand-made batch G{gn}: cells = [(status, review, has_picture)]."""
        d = self.out / f"G{gn:03d}"
        (d / "slices").mkdir(parents=True)
        (d / "prompts.json").write_text("{}", encoding="utf-8")
        sts = []
        for i, (status, review, pic) in enumerate(cells, 1):
            png = None
            if pic:
                a = np.zeros((64, 64, 4), np.uint8)
                a[16:48, 16:48] = (200, 160, 30, 255)
                Image.fromarray(a, "RGBA").save(d / "slices" / f"S{i}.png")
                png = f"slices/S{i}.png"
            sts.append({"index": i, "key": f"k{i}", "status": status, "png": png, "review": review, "anim_status": "NOT_REQUESTED"})
        res = {"generation_id": f"G{gn:03d}", "number": gn, "prompt": "particles", "stage": "sliced", "error": None, "grid": [2, 2], "source": {"subject": "particles"}, "stickers": sts,
               **({"kind": kind} if kind else {})}
        (d / "result.json").write_text(json.dumps(res), encoding="utf-8")


def fake_vlm(answers, model="fake-vlm"):
    seen = []

    def complete(system, user, images=None, **kw):
        seen.append({"system": system, "user": user, "images": list(images or [])})
        return (answers.pop(0) if answers else '{"options": []}'), {"model": model, "provider": "local"}
    complete.seen = seen
    return complete


def judge(complete):
    return VisionJudge(complete=complete, cache=cachemod.Cache(force_memory=True), pol="FAIL_CLOSED")


GOOD = '{"options": ["small bats", "bat signals", "cape pieces", "utility belt pieces", "gold coins", "small stars", "tiny diamonds", "shield badges", "moon pieces", "sparks"]}'


def size_of(png: bytes):
    return Image.open(io.BytesIO(png)).size


class SuggestTests(SetBase):
    def test_the_model_looks_at_one_picture_the_batch_master_sheet_downscaled_and_its_answer_is_linted(self):
        gn = new_batch(self.out, self.tmp / "in", self.sheet, grid="2x2")
        for i in (1, 2):
            sid = self.c.lib.add_from_generation(self.out, self.pack["id"], gn, i, "static")["id"]
            self.assertTrue(sid)
        s, j = self.req("POST", "/api/effects", {"pack_id": self.pack["id"], "sticker_ids": [st["id"] for st in self.c.lib.snapshot()["packs"][0]["stickers"][2:]], "mode": "sim"})
        eid = j["id"]
        self.until(lambda: self.req("GET", f"/api/effects/{eid}")[1].get("status") == "READY", "the analysis")
        bad = '{"options": ["small bats", "a sticker of a bat", "company logo", "one two three four five six", "small bats", "bat signals", "cape pieces", "gold coins"]}'
        c = fake_vlm([bad])
        r = fx.suggest(self.out, self.c.lib, eid, "2x2", allowed=True, vlm=judge(c))
        self.assertEqual(len(c.seen), 1)
        self.assertEqual(len(c.seen[0]["images"]), 1, "ONE picture, no choice of stickers")
        self.assertEqual(max(size_of(c.seen[0]["images"][0])), 1024, "the 1200 px master sheet, downscaled to 1024")
        self.assertIn("master sheet", c.seen[0]["user"])
        self.assertEqual(r["source"], {"kind": "batch", "generation": gn})
        self.assertEqual((r["by"], r["model"], r["n"], r["grid"]), ("vlm", "fake-vlm", 4, [2, 2]))
        self.assertEqual(r["options"], ["small bats", "bat signals", "cape pieces", "gold coins"], "a sticker / logo / 6 words / a duplicate are dropped by the lint, the rest stays in order")
        e = fx.read(self.out, eid)
        self.assertEqual((e["set"]["status"], e["set"]["by"], e["set"]["options"], e["set"]["source"], e["set"]["picked"]), ("SUGGESTED", "vlm", r["options"], r["source"], []))
        self.assertEqual(e["set"]["model"], "fake-vlm")
        self.assertEqual(e["history"][-1]["decision"], "SUGGEST")

    def test_without_a_source_sheet_a_contact_sheet_of_the_stickers_is_the_one_picture(self):
        c = fake_vlm([GOOD])
        r = fx.suggest(self.out, self.c.lib, self.eid, "3x3", allowed=True, vlm=judge(c))
        self.assertEqual(r["source"], {"kind": "contact"})
        self.assertEqual(len(c.seen[0]["images"]), 1)
        w, h = size_of(c.seen[0]["images"][0])
        self.assertTrue(w == h and w <= 1024, (w, h))
        self.assertIn("contact sheet", c.seen[0]["user"])
        self.assertEqual((r["by"], len(r["options"]), r["n"]), ("vlm", 10, 9))

    def test_a_contact_sheet_takes_at_most_nine_stickers_spread_over_the_pack(self):
        e = fx.read(self.out, self.eid)
        d = self.out / "effects" / self.eid
        e["stickers"] = [{**e["stickers"][0], "sticker_id": f"x{i}", "src": f"src/x{i}.png"} for i in range(20)]
        for i in range(20):
            (d / "src" / f"x{i}.png").write_bytes(sticker_png((10 * i, 40, 60)))
        png = fx._contact_png(d, e)
        self.assertEqual(size_of(png), (1020, 1020), "3 x 3 tiles of 340 px")
        e["stickers"] = e["stickers"][:1]
        self.assertEqual(size_of(fx._contact_png(d, e)), (512, 512))

    def test_no_consent_means_the_table_answers_and_nothing_is_sent(self):
        c = fake_vlm([GOOD])
        r = fx.suggest(self.out, self.c.lib, self.eid, "2x2", allowed=None, vlm=judge(c))
        self.assertEqual(c.seen, [])
        self.assertEqual((r["by"], r["model"] if "model" in r else None), ("table", None))
        self.assertTrue(8 <= len(r["options"]) <= 12, r["options"])
        self.assertIn("small ripe strawberries", r["options"], "the strawberry sticker's emoji speaks")
        self.assertIn("red hearts", r["options"], "and the heart's: every subject of the pack is heard")
        self.assertTrue(any("not allowed" in n for n in r["notes"]))
        self.assertEqual(fx.read(self.out, self.eid)["set"]["by"], "table")

    def test_a_model_that_answers_nonsense_twice_falls_back_to_the_table_and_says_so(self):
        c = fake_vlm(["I cannot", '{"options": ["only one"]}'])
        r = fx.suggest(self.out, self.c.lib, self.eid, "2x2", allowed=True, vlm=judge(c))
        self.assertEqual(len(c.seen), 2, "one repair round, then the table")
        self.assertEqual(r["by"], "table")
        self.assertTrue(any("could not read" in n for n in r["notes"]))
        self.assertTrue(8 <= len(r["options"]) <= 12)

    def test_the_table_pads_a_one_subject_pack_with_generic_particles_and_never_exceeds_twelve(self):
        self.assertEqual(len(effect_plan.table_options([{"name": "x", "emoji": "🙂"}])), 8)
        many = [{"name": "x", "emoji": e} for e in "🍓🍒🍌🍎🍉🍕🍔🍩🎂🎉"]
        out = effect_plan.table_options(many)
        self.assertEqual(len(out), 12)
        self.assertEqual(len({o.lower() for o in out}), 12)
        for o in out:
            from stickerloom.generation import effect_prompts as ep
            ep.lint_plan({"subject": "x", "elements": [o]})

    def test_the_route_answers_with_the_table_by_default_and_with_the_model_only_with_consent(self):
        s, r = self.req("POST", f"/api/effects/{self.eid}/suggest", {"grid": "2x2"})
        self.assertEqual((s, r["by"], r["n"], r["source"]), (200, "table", 4, {"kind": "contact"}), r)
        self.assertTrue(8 <= len(r["options"]) <= 12)
        c = fake_vlm([GOOD])
        with mock.patch.object(effect_plan, "VisionJudge", lambda **kw: judge(c)):
            self.assertEqual(self.req("POST", f"/api/effects/{self.eid}/suggest", {"grid": "2x2"})[1]["by"], "table")
            self.assertEqual(c.seen, [], "allow_vlm is not true: nothing was sent")
            s, r = self.req("POST", f"/api/effects/{self.eid}/suggest", {"grid": "3x3", "allow_vlm": True})
        self.assertEqual((s, r["by"], r["model"], r["n"], len(r["options"])), (200, "vlm", "fake-vlm", 9, 10), r)
        self.assertEqual(self.req("POST", f"/api/effects/{self.eid}/suggest", {"grid": "4x4"})[0], 400)
        self.assertEqual(self.req("POST", "/api/effects/E999/suggest", {})[0], 404)

    def test_suggesting_again_after_the_sheet_was_drawn_keeps_the_drawn_set(self):
        _, gn = self.drawn()
        before = self.effect()["set"]
        self.assertEqual(before["status"], "DRAWN")
        r = fx.suggest(self.out, self.c.lib, self.eid, "3x3", allowed=None)
        s = self.effect()["set"]
        self.assertEqual((s["status"], s["generation"], s["picked"], s["grid"]), ("DRAWN", gn, before["picked"], [2, 2]))
        self.assertEqual(s["options"], r["options"])


class ParticleRoutesTests(SetBase):
    def test_the_price_comes_first_and_nothing_is_spent_without_go(self):
        before = len(self.creates())
        els = ["small strawberries", "tiny hearts"]
        s, est = self.req("POST", f"/api/effects/{self.eid}/particles_estimate", {"grid": "2x2", "elements": els})
        self.assertEqual((s, est["credits"], est["grid"], est["n"], est["outline"], est["kind"], len(est["cells"])), (200, 2.0, [2, 2], 4, 0, "particles", 4), est)
        self.assertEqual([c["label"] for c in est["cells"]][:2], els, "the picks come first; fewer picks than cells are cycled as variants")
        self.assertEqual(len({c["label"] for c in est["cells"]}), 4)
        self.assertIn("Particle 1", est["prompt"])
        s, est2 = self.req("POST", f"/api/effects/{self.eid}/particles", {"grid": "3x3", "elements": els, "estimate": True})
        self.assertEqual((s, len(est2["cells"])), (200, 9))
        s, no = self.req("POST", f"/api/effects/{self.eid}/particles", {"grid": "2x2", "elements": els})
        self.assertEqual((s, no["estimate"]["credits"]), (409, 2.0))
        self.assertIn("2.0 credits", no["error"])
        self.assertEqual(len(self.creates()), before, "no go-ahead, no spending")
        self.assertNotIn("set", self.effect(), "nothing was written either")

    def test_the_picks_are_validated_before_anything_is_spent(self):
        before = len(self.creates())
        for body, code in (({"grid": "4x4", "elements": ["a"], "go": True}, 400), ({"grid": "2x2", "elements": [], "go": True}, 400),
                           ({"grid": "2x2", "elements": [f"piece {i}" for i in range(5)], "go": True}, 400), ({"grid": "2x2", "elements": ["a sticker of a bat"], "go": True}, 400),
                           ({"grid": "2x2", "elements": "bats", "go": True}, 400), ({"grid": "2x2", "elements": [3], "go": True}, 400),
                           ({"grid": "2x2", "elements": ["one two three four five six"], "go": True}, 400)):
            s, r = self.req("POST", f"/api/effects/{self.eid}/particles", body)
            self.assertEqual(s, code, (body, r))
        self.assertEqual(self.req("POST", "/api/effects/E999/particles", {"grid": "2x2", "elements": ["a"], "go": True})[0], 404)
        self.assertEqual(len(self.creates()), before)
        s, r = self.req("POST", f"/api/effects/{self.eid}/particles", {"grid": "3x3", "elements": [f"piece {i}" for i in range(9)], "estimate": True})
        self.assertEqual((s, len(r["cells"])), (200, 9), "N picks fill an NxN sheet exactly")

    def test_one_sheet_for_the_whole_effect_is_a_particle_batch_and_is_linked_to_every_group(self):
        e = self.effect()
        self.assertEqual(len(e["groups"]), 2, "a strawberry and a heart")
        before = len(self.creates())
        j = self.draw()
        self.assertEqual((j["estimate"], j["grid"], j["id"]), (2.0, [2, 2], self.eid))
        self.assertNotIn("group", j)
        s = self.effect()["set"]
        self.assertIn(s["status"], ("REQUESTED", "DRAWN"))
        self.assertEqual((s["elements"], s["grid"], s["job"], s["by"]), (["small strawberries", "tiny hearts", "green leaves", "sparkles"], [2, 2], j["job"], "you"))
        job = jobs.read(self.out, j["job"])
        self.assertEqual((job["kind"], job["request"]["outline"], job["request"]["particles"]), ("sheet", 0, {"effect": self.eid}))
        self.assertNotIn("pieces", job["request"])
        self.assertIn("Particle 1 (top-left): small strawberries", job["request"]["prompt"])
        done = self.until(lambda: (lambda x: x if x.get("generation") and x["status"] == "DONE" else None)(self.req("GET", "/api/jobs/" + j["job"])[1]), "the sheet job")
        gn = int(done["generation"][1:])
        self.assertEqual(len(self.creates()), before + 1, "ONE sheet, whatever the number of groups")
        e = self.effect()
        s = e["set"]
        self.assertEqual((s["status"], s["generation"], s["job"], s["grid"], s["picked"]), ("DRAWN", gn, j["job"], [2, 2], [1, 2, 3, 4]))
        for g in e["groups"]:
            self.assertEqual(g["sprites"], {"generation": gn, "picked": [1, 2, 3, 4]}, "EVERY group shares the set")
            self.assertNotIn("pieces", g, "the per-group record of the old feature is gone")
        self.assertTrue(any(h["decision"] == "LINK" for h in e["history"]))
        self.until(lambda: (lambda x: x if x["stage"] == "sliced" and not x["busy"] else None)(self.req("GET", f"/api/generations/{gn}")[1]), "the cells")
        res = self.req("GET", f"/api/generations/{gn}")[1]
        self.assertEqual((res["kind"], res["outline_px"], res["sheet_issues"]), ("particles", 0, []))
        self.assertEqual(res["source"]["grid"]["method"], "equal")
        self.assertEqual([x["status"] for x in res["stickers"]], ["READY"] * 4)
        for sid in (self.s1, self.s2):
            s_, pv = self.req("POST", f"/api/effects/{self.eid}/preview", {"sticker_id": sid, "params": {}})
            self.assertEqual((s_, pv["sprites"]), (200, 4), pv)

    def test_queue_mode_links_too_because_the_link_lives_in_start_from_job(self):
        with mock.patch.object(type(self.c), "fulfil_async", lambda self_, jid, after=None: None):      # a queue worker fulfils the job elsewhere and calls start_from_job
            j = self.draw()
        s = self.effect()["set"]
        self.assertEqual((s["status"], s["job"], s["elements"]), ("REQUESTED", j["job"], ["small strawberries", "tiny hearts", "green leaves", "sparkles"]))
        self.assertEqual([g["sprites"] for g in self.effect()["groups"]], ["own", "own"], "nothing is linked before the sheet exists")
        f = self.tmp / "sheet.png"
        f.write_bytes(png_bytes(self.sheet))
        jobs.claim(self.out, j["job"], "ticket-1")
        jobs.done(self.out, j["job"], str(f), "nano_banana_flash")
        self.c.start_from_job(jobs.read(self.out, j["job"]))
        gn = int(jobs.read(self.out, j["job"])["generation"][1:])
        e = self.effect()
        self.assertEqual((e["set"]["status"], [g["sprites"]["generation"] for g in e["groups"]]), ("DRAWN", [gn, gn]))
        self.until(lambda: (lambda x: x if x["stage"] == "sliced" and not x["busy"] else None)(self.req("GET", f"/api/generations/{gn}")[1]), "the cells")
        self.assertEqual(pl.read_result(self.out, gn)["kind"], "particles")

    def test_a_job_whose_effect_was_removed_meanwhile_still_makes_its_batch(self):
        import shutil
        j = self.draw()
        shutil.rmtree(self.out / "effects" / self.eid)
        done = self.until(lambda: (lambda x: x if x.get("generation") and x["status"] == "DONE" else None)(self.req("GET", "/api/jobs/" + j["job"])[1]), "the sheet job")
        self.assertTrue(done["generation"].startswith("G"))

    def test_the_old_routes_are_aliases_and_a_group_there_means_that_groups_pieces(self):
        gid = self.effect()["groups"][0]["id"]
        els = self.effect()["groups"][0]["elements"]
        s, est = self.req("POST", f"/api/effects/{self.eid}/pieces_estimate", {"group": gid})
        self.assertEqual((s, est["credits"], est["kind"]), (200, 2.0, "particles"), est)
        self.assertEqual([c["label"] for c in est["cells"]][:len(els)], els[:4])
        self.assertEqual(self.req("POST", f"/api/effects/{self.eid}/pieces", {"group": gid})[0], 409)
        self.assertEqual(self.req("POST", f"/api/effects/{self.eid}/pieces", {"group": "G99", "go": True})[0], 404)
        s, j = self.req("POST", f"/api/effects/{self.eid}/pieces", {"group": gid, "go": True})
        self.assertEqual(s, 202, j)
        self.assertEqual(jobs.read(self.out, j["job"])["request"]["particles"], {"effect": self.eid})

    def test_picks_that_were_not_the_persons_are_cut_to_the_cells_and_the_persons_are_not(self):
        e = fx.read(self.out, self.eid)
        e["groups"][0]["elements"] = [f"thing {i}" for i in range(6)]
        fx._write(self.out, e)
        gid = e["groups"][0]["id"]
        s, est = self.req("POST", f"/api/effects/{self.eid}/pieces_estimate", {"group": gid})
        self.assertEqual((s, [c["label"] for c in est["cells"]], est["picks"]), (200, [f"thing {i}" for i in range(4)], [f"thing {i}" for i in range(4)]), est)
        s, est = self.req("POST", f"/api/effects/{self.eid}/particles_estimate", {})
        self.assertEqual((s, len(est["cells"])), (200, 4), "no picks at all: the groups' pieces, the first four")
        s, j = self.req("POST", f"/api/effects/{self.eid}/pieces", {"group": gid, "go": True})
        self.assertEqual(s, 202, j)
        self.assertEqual(self.req("POST", f"/api/effects/{self.eid}/particles_estimate", {"elements": [f"thing {i}" for i in range(5)]})[0], 400, "five picks of the person do not fit four cells")

    def test_the_base_plan_is_what_the_normal_reserve_path_accepts_and_keeps_the_particles_prompt(self):
        els = ["small strawberries", "tiny hearts", "green leaves", "sparkles"]
        base = fx.particles_base_plan(self.out, self.eid, "2x2", els)
        self.assertEqual((base["template_id"], base["template_version"], base["grid"]), ("sheet_2x2", 3, [2, 2]))
        self.assertEqual([s["key"] for s in base["stickers"]], ["small_strawberries", "tiny_hearts", "green_leaves", "sparkles"])
        self.assertEqual({s["emoji"] for s in base["stickers"]}, {"🍓"}, "the emoji of the pack's first (most common) sticker")
        self.assertEqual(base["slots"]["key_colour"], "blue", "a green leaf makes the screen blue")
        self.assertEqual(base["effect"]["id"], self.eid)
        self.assertNotIn("group", base["effect"])
        t = tasks.reserve(self.out, self.tmp / "in", base["task"], "2x2", "flat_vector", base_plan=base)
        self.assertEqual(t["plan"]["sheet_prompt"], base["custom"]["sheet_prompt"], "the particles prompt is what is sent")
        with self.assertRaises(fx.EffectError):
            fx.particles_base_plan(self.out, self.eid, "4x4", els)

    # ---------- an old sheet (the real G100) is cut again as particles

    def test_an_old_sheet_with_blocked_cells_is_cut_again_as_particles_by_one_free_click(self):
        sheet = blobs(1200, [(300, 300), (900, 300), (300, 900), (900, 900)], r=100, line=16)
        gn = new_batch(self.out, self.tmp / "in", sheet)                                # as the old feature made it: a sticker batch
        old = pl.read_result(self.out, gn)
        self.assertNotIn("kind", old)
        self.assertIn("FAILED", [x["status"] for x in old["stickers"]])
        fx.link_particles(self.out, self.eid, gn, grid=[2, 2])
        s, r = self.req("POST", f"/api/effects/{self.eid}/particles_recut", {})
        self.assertEqual((s, r), (202, {"id": self.eid, "generation": f"G{gn:03d}"}))
        self.until(lambda: (lambda x: x if x.get("kind") == "particles" and x["stage"] == "sliced" and not x["busy"] else None)(self.req("GET", f"/api/generations/{gn}")[1]), "the new cut")
        res = pl.read_result(self.out, gn)
        self.assertEqual(([x["status"] for x in res["stickers"]], res["sheet_issues"], res["source"]["grid"]["method"]), (["READY"] * 4, [], "equal"))
        self.assertTrue(all(any(h["decision"] == "PASS" and h["reason"] == "cut again as particles" for h in x["history"]) for x in res["stickers"]))
        self.assertEqual(len(fx.sprites_of(self.out, self.c.lib, self.eid, self.effect()["groups"][0]["id"])), 4)
        self.assertEqual(self.req("POST", f"/api/effects/{self.eid}/particles_recut", {})[0], 409, "already cut as particles: nothing would change")

    def test_cutting_again_is_refused_when_nothing_is_drawn_or_a_cell_was_decided(self):
        self.assertEqual(self.req("POST", f"/api/effects/{self.eid}/particles_recut", {})[0], 409)
        gn = new_batch(self.out, self.tmp / "in", blobs(1200, SPARSE))
        res = pl.read_result(self.out, gn)
        res["stickers"][0]["review"]["still"] = "APPROVED"
        pl.write_result(self.out, gn, res)
        fx.link_particles(self.out, self.eid, gn, grid=[2, 2])
        s, r = self.req("POST", f"/api/effects/{self.eid}/particles_recut", {})
        self.assertEqual(s, 409)
        self.assertIn("already decided", r["error"])
        self.assertEqual(self.req("POST", "/api/effects/E999/particles_recut", {})[0], 404)

    # ---------- pick

    def test_the_person_picks_the_cells_that_are_the_particles(self):
        self.batch(21, [("READY", {}, True), ("READY", {"still": "REJECTED"}, True), ("FAILED", {}, False), ("READY", {}, True)], kind="particles")
        fx.link_particles(self.out, self.eid, 21, grid=[2, 2])
        self.assertEqual(len(fx.sprites_of(self.out, self.c.lib, self.eid, self.effect()["groups"][0]["id"])), 4 - 1 - 1 + 1,
                         "all picked cells with a picture: the rejected one too (the person's list wins), the one with no picture never")
        s, e = self.req("POST", f"/api/effects/{self.eid}/particles_pick", {"indexes": [4, 1, 1]})
        self.assertEqual(s, 200, e)
        self.assertEqual(e["set"]["picked"], [1, 4])
        self.assertEqual([g["sprites"] for g in e["groups"]], [{"generation": 21, "picked": [1, 4]}] * 2)
        h = e["history"][-1]
        self.assertEqual((h["actor"], h["decision"]), ("you", "PICK"))
        for g in e["groups"]:
            self.assertEqual(len(fx.sprites_of(self.out, self.c.lib, self.eid, g["id"])), 2)
        s, pv = self.req("POST", f"/api/effects/{self.eid}/preview", {"sticker_id": self.s1, "params": {}})
        self.assertEqual((s, pv["sprites"]), (200, 2))

    def test_a_cell_can_be_picked_whatever_its_verifier_status_but_not_without_a_picture(self):
        self.batch(22, [("READY", {}, True), ("BLOCKED", {}, True), ("FAILED", {}, False), ("READY", {}, True)])
        fx.link_particles(self.out, self.eid, 22, grid=[2, 2])
        self.assertEqual(self.req("POST", f"/api/effects/{self.eid}/particles_pick", {"indexes": [2]})[0], 200, "a blocked cell that has a picture is the person's decision")
        s, r = self.req("POST", f"/api/effects/{self.eid}/particles_pick", {"indexes": [1, 3]})
        self.assertEqual(s, 400)
        self.assertIn("cell 3", r["error"])
        self.assertEqual(self.effect()["set"]["picked"], [2], "a refused pick changes nothing")
        for body in ({"indexes": []}, {"indexes": ["1"]}, {"indexes": [0]}, {"indexes": [True]}, {"indexes": 3}, {}, {"indexes": [9]}):
            self.assertEqual(self.req("POST", f"/api/effects/{self.eid}/particles_pick", body)[0], 400, body)

    def test_picking_before_anything_is_drawn_is_a_409(self):
        self.assertEqual(self.req("POST", f"/api/effects/{self.eid}/particles_pick", {"indexes": [1]})[0], 409)
        self.assertEqual(self.req("POST", "/api/effects/E999/particles_pick", {"indexes": [1]})[0], 404)

    def test_default_picks_use_every_ready_cell_that_has_a_picture_and_skip_a_missing_file(self):
        self.batch(23, [("READY", {}, True), ("READY", {}, True), ("READY", {}, True), ("READY", {}, True)])
        (self.out / "G023" / "slices" / "S2.png").unlink()
        gid = self.effect()["groups"][0]["id"]
        fx.set_sprites(self.out, self.eid, gid, {"generation": 23})
        self.assertEqual(len(fx.sprites_of(self.out, self.c.lib, self.eid, gid)), 3, "no `picked`: every READY cell with its file")
        fx.set_sprites(self.out, self.eid, gid, {"generation": 23, "picked": [1, 2]})
        self.assertEqual(len(fx.sprites_of(self.out, self.c.lib, self.eid, gid)), 1, "a picked cell whose file is missing is skipped")
        fx.set_sprites(self.out, self.eid, gid, {"generation": 23, "picked": [2]})
        with self.assertRaises(fx.EffectError) as cm:
            fx.sprites_of(self.out, self.c.lib, self.eid, gid)
        self.assertEqual(cm.exception.code, 409)
        self.assertIn("picked", str(cm.exception))
        for bad in ({"generation": 23, "picked": ["1"]}, {"generation": 23, "picked": 1}, {"generation": "x"}, "nope"):
            with self.assertRaises(fx.EffectError):
                fx.set_sprites(self.out, self.eid, gid, bad)

    def test_a_cell_with_only_warnings_is_used_and_a_blocked_or_rejected_one_is_not_by_default(self):
        self.batch(5, [("READY", {}, True), ("READY", {"still": "REJECTED"}, True), ("BLOCKED", {}, True), ("READY", {}, True)])
        gid = self.effect()["groups"][0]["id"]
        fx.set_sprites(self.out, self.eid, gid, {"generation": 5})
        self.assertEqual(len(fx.sprites_of(self.out, self.c.lib, self.eid, gid)), 2)
        self.assertEqual(fx.sim_preview(self.out, self.c.lib, self.eid, self.s1, {})["sprites"], 2)

    def test_a_batch_with_no_ready_cell_is_a_409_with_the_reason(self):
        gid = self.effect()["groups"][0]["id"]
        self.batch(6, [("PENDING", {}, False), ("PENDING", {}, False)])
        fx.set_sprites(self.out, self.eid, gid, {"generation": 6})
        with self.assertRaises(fx.EffectError) as cm:
            fx.sprites_of(self.out, self.c.lib, self.eid, gid)
        self.assertEqual(cm.exception.code, 409)
        self.assertIn("still being cut", str(cm.exception))
        self.batch(7, [("BLOCKED", {}, False), ("READY", {"still": "REJECTED"}, True)])
        fx.set_sprites(self.out, self.eid, gid, {"generation": 7})
        with self.assertRaises(fx.EffectError) as cm:
            fx.sprites_of(self.out, self.c.lib, self.eid, gid)
        self.assertEqual(cm.exception.code, 409)
        self.assertIn("no ready cell", str(cm.exception))

    def test_before_the_cells_exist_the_burst_answers_a_clear_409(self):
        fx.link_particles(self.out, self.eid, 77, grid=[2, 2])          # a batch that does not exist (yet)
        s, r = self.req("POST", f"/api/effects/{self.eid}/preview", {"sticker_id": self.s1, "params": {}})
        self.assertEqual(s, 409)
        self.assertIn("G077", r["error"])

    def test_a_new_analysis_gives_its_groups_the_drawn_set(self):
        self.batch(24, [("READY", {}, True)] * 4, kind="particles")
        fx.link_particles(self.out, self.eid, 24, grid=[2, 2])
        fx.analyse(self.out, self.eid)
        e = self.effect()
        self.assertEqual([g["sprites"] for g in e["groups"]], [{"generation": 24, "picked": [1, 2, 3, 4]}] * len(e["groups"]))

    def test_redrawing_keeps_the_old_cells_until_the_new_sheet_is_cut(self):
        self.batch(25, [("READY", {}, True)] * 4, kind="particles")
        fx.link_particles(self.out, self.eid, 25, grid=[2, 2])
        fx.pick_particles(self.out, self.eid, [2, 3])
        with mock.patch.object(type(self.c), "fulfil_async", lambda self_, jid, after=None: None):
            j = self.draw()
        s = self.effect()["set"]
        self.assertEqual((s["status"], s["generation"], s["picked"], s["job"]), ("REQUESTED", 25, [2, 3], j["job"]))
        self.assertEqual(self.effect()["groups"][0]["sprites"], {"generation": 25, "picked": [2, 3]})

    # ---------- old records

    def test_an_old_record_with_a_group_that_drew_its_pieces_keeps_working(self):
        self.batch(30, [("READY", {}, True), ("READY", {"still": "REJECTED"}, True), ("FAILED", {}, False), ("READY", {}, True)])
        e = fx.read(self.out, self.eid)
        e.pop("set", None)
        g = e["groups"][0]
        g["sprites"], g["pieces"] = {"generation": 30}, {"generation": 30, "job": "J040", "grid": [2, 2], "status": "DRAWN"}
        fx._write(self.out, e)
        self.assertEqual(len(fx.sprites_of(self.out, self.c.lib, self.eid, g["id"])), 2, "the old rule: READY and not rejected")
        r = self.effect()
        self.assertTrue(r["set"]["legacy"])
        self.assertEqual((r["set"]["status"], r["set"]["generation"], r["set"]["job"], r["set"]["grid"], r["set"]["picked"]), ("DRAWN", 30, "J040", [2, 2], [1, 4]))
        self.assertEqual(r["set"]["elements"], g["elements"])
        self.assertEqual(r["groups"][0]["pieces"]["status"], "DRAWN", "the old fields are not removed from a stored record")
        s, pv = self.req("POST", f"/api/effects/{self.eid}/preview", {"sticker_id": e["groups"][0]["stickers"][0], "params": {}})
        self.assertEqual((s, pv["sprites"]), (200, 2))
        s, r = self.req("POST", f"/api/effects/{self.eid}/particles_pick", {"indexes": [1]})
        self.assertEqual(s, 200, r)
        self.assertEqual((r["set"]["picked"], r["set"].get("legacy"), r["set"]["generation"]), ([1], None, 30), "picking upgrades the record to a real set")
        self.assertEqual([g["sprites"] for g in r["groups"]], [{"generation": 30, "picked": [1]}] * len(r["groups"]))
        self.assertNotIn("legacy", fx.read(self.out, self.eid)["set"], "the stored record is a real set now")

    def test_a_record_that_never_drew_anything_has_no_set(self):
        self.assertNotIn("set", self.effect())
        self.assertIsNone(fx.set_of(fx.read(self.out, self.eid)))

    def test_requested_old_record_is_derived_as_requested(self):
        e = fx.read(self.out, self.eid)
        e["groups"][0]["pieces"] = {"job": "J040", "grid": [2, 2], "status": "REQUESTED"}
        fx._write(self.out, e)
        s = self.effect()["set"]
        self.assertEqual((s["status"], s["generation"], s["picked"], s["legacy"]), ("REQUESTED", None, [], True))


# ---------- the static resize ----------
def rgba(w, h, colour=(200, 60, 60), margin=0):
    a = np.zeros((h, w, 4), np.uint8)
    a[margin:h - margin, margin:w - margin] = (*colour, 255)
    return a


class FitSpritesTests(unittest.TestCase):
    def test_a_sprite_is_trimmed_then_fitted_keeping_its_aspect_and_never_enlarged(self):
        big = rgba(512, 512, margin=0)
        big[:, :] = 0
        big[56:456, 106:306] = (200, 60, 60, 255)                        # 200 x 400 on a 512 canvas
        out = particles.fit_sprites([big], 100)[0]
        self.assertEqual(out.shape, (100, 50, 4))
        self.assertEqual(out.dtype, np.uint8)
        small = rgba(40, 30)
        self.assertEqual(particles.fit_sprites([small], 100)[0].shape, (30, 40, 4), "a small sprite is not enlarged")
        self.assertEqual(particles.fit_sprites([big], 200)[0].shape, (200, 100, 4), "scale 2 of 100 px")

    def test_colour_does_not_bleed_from_transparent_pixels_and_a_blank_sprite_is_left_alone(self):
        a = np.zeros((300, 300, 4), np.uint8)
        a[..., :3] = (0, 255, 0)                                          # invisible green everywhere
        a[100:200, 100:200] = (255, 0, 0, 255)
        out = particles.fit_sprites([a], 50)[0]
        solid = out[out[..., 3] == 255]
        self.assertTrue(len(solid) and (solid[:, 0] > 240).all() and (solid[:, 1] < 20).all(), "the edge carries the sprite's colour, not the green under the alpha")
        blank = np.zeros((64, 64, 4), np.uint8)
        self.assertIs(particles.fit_sprites([blank], 32)[0], blank)
        with self.assertRaises(ValueError):
            particles.fit_sprites([np.zeros((4, 4, 3), np.uint8)], 32)
        with self.assertRaises(ValueError):
            particles.fit_sprites([blank], 0)


class SpriteResizeParamsTests(SetBase):
    def test_the_params_are_validated_like_the_others(self):
        for p in ({"sprite_px": 31}, {"sprite_px": 513}, {"sprite_px": "100"}, {"sprite_px": True}, {"sprite_px": 100.5}, {"sprite_px": None}, {"scale": 0.9}, {"scale": 4.1}, {"scale": "2"}, {"scale": False},
                  {"scale": None}, {"bogus": 1}):
            for act, body in (("preview", {"sticker_id": self.s1, "params": p}), ("render", {"sticker_id": self.s1, "params": p})):
                self.assertEqual(self.req("POST", f"/api/effects/{self.eid}/{act}", body)[0], 400, (act, p))
        for p in ({"sprite_px": 32}, {"sprite_px": 512, "scale": 4}, {"sprite_px": 100.0}, {"scale": 1.5}):
            self.assertEqual(self.req("POST", f"/api/effects/{self.eid}/preview", {"sticker_id": self.s1, "params": p})[0], 200, p)

    def test_the_defaults_are_100_px_and_scale_1_and_the_answer_says_what_made_the_picture(self):
        s, pv = self.req("POST", f"/api/effects/{self.eid}/preview", {"sticker_id": self.s1, "params": {"gravity": 1.2}})
        self.assertEqual((s, pv["params"]["sprite_px"], pv["params"]["scale"], pv["params"]["gravity"]), (200, 100, 1, 1.2))
        self.assertEqual(fx.split_fit(None), (100, 1, {}))
        self.assertEqual(fx.split_fit({"sprite_px": 64, "scale": 2.5, "gravity": 1}), (64, 2.5, {"gravity": 1}))
        s, r = self.req("POST", f"/api/effects/{self.eid}/render", {"sticker_id": self.s1, "params": {"sprite_px": 64, "scale": 2}})
        self.assertEqual((s, r["status"], r["params"]["sprite_px"], r["params"]["scale"]), (200, "READY", 64, 2), r)

    def test_the_cache_digest_includes_the_resize_so_other_settings_are_other_files(self):
        def f(**p):
            return self.req("POST", f"/api/effects/{self.eid}/preview", {"sticker_id": self.s1, "params": p})[1]["file"]
        a, b, c_, d = f(), f(sprite_px=100), f(sprite_px=200), f(scale=2)
        self.assertEqual(a, b, "the default is 100 px, scale 1: the same file")
        self.assertEqual(len({a, c_, d}), 3)
        self.assertEqual(f(sprite_px=50, scale=2), f(sprite_px=50, scale=2), "the same settings are a cache hit")

    def test_the_digest_sees_every_pixel_not_a_prefix(self):
        p = particles.preset("burst")
        one, two = np.zeros((64, 64, 4), np.uint8), np.zeros((64, 64, 4), np.uint8)
        one[40:60, 20:40] = (255, 0, 0, 255)
        two[40:60, 20:40] = (0, 0, 255, 255)                            # same shape, same first rows (all transparent), another colour
        self.assertNotEqual(fx._digest([one], p), fx._digest([two], p))
        self.assertNotEqual(fx._digest([one], p, (100, 1)), fx._digest([one], p, (100, 2)))

    def test_picked_cells_are_other_pixels_so_another_pick_is_another_preview(self):
        d = self.out / "G040" / "slices"
        self.batch(40, [("READY", {}, True)] * 4)
        for i, col in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)], 1):
            a = np.zeros((64, 64, 4), np.uint8)
            a[16:48, 16:48] = (*col, 255)
            Image.fromarray(a, "RGBA").save(d / f"S{i}.png")
        fx.link_particles(self.out, self.eid, 40, grid=[2, 2])
        fx.pick_particles(self.out, self.eid, [1])
        a = fx.sim_preview(self.out, self.c.lib, self.eid, self.s1, {})["file"]
        fx.pick_particles(self.out, self.eid, [3])
        b = fx.sim_preview(self.out, self.c.lib, self.eid, self.s1, {})["file"]
        self.assertNotEqual(a, b)


class WordingTests(unittest.TestCase):
    def test_the_notes_a_person_reads_say_particles_and_a_note_may_name_them(self):
        r = effect_plan.analyse([{"id": "a", "name": "x", "emoji": ["🍓"], "png": None}], allowed=None, note="particles: gold bars, diamonds")
        self.assertTrue(all("piece" not in n for n in r["notes"]), r["notes"])
        self.assertTrue(any("particles" in n for n in r["notes"]))
        self.assertEqual(r["groups"][0]["elements"], ["gold bars", "diamonds"])
        self.assertEqual(effect_plan._pieces_from_note("pieces: gold bars and diamonds"), ["gold bars", "diamonds"], "the older phrasing still works")


if __name__ == "__main__":
    unittest.main()
