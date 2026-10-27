"""The golden path end to end through the console API (docs/architecture.md): plan -> stills -> video sheet -> returned video -> animations -> pack.
Synthetic inputs only. The scenario of the plan: G2 rejects 5 and 6, the video blocks 1 and 2 (inside_slot), the final pack is 3, 4, 7, 8, 9."""
import http.client
import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np
from PIL import Image

from stickerloom.console.server import serve
from stickerloom.engine.config import EngineConfig
from stickerloom.flow import pipeline as pl
from tests import synth


def shape_sheet(size, centres, r=90, seed=11):
    """One distinct, fully READY subject per cell on a noisy green sheet (so no cell is blocked by Python)."""
    s = synth.bg(size, seed).copy()
    for i, (cx, cy) in enumerate(centres):
        kind = i % 4
        colour = [synth.YELLOW, synth.RED, (60, 90, 220), (240, 140, 30)][i % 4]
        if kind == 0:
            cv2.circle(s, (cx, cy), r, colour, -1, cv2.LINE_AA)
        elif kind == 1:
            cv2.rectangle(s, (cx - r // 2, cy - r - 10), (cx + r // 2, cy + r + 10), colour, -1)
        elif kind == 2:
            cv2.rectangle(s, (cx - r - 10, cy - r // 2), (cx + r + 10, cy + r // 2), colour, -1)
        else:
            cv2.fillPoly(s, [np.array([[cx, cy - r], [cx - r, cy + r - 20], [cx + r, cy + r - 20]])], colour, cv2.LINE_AA)
    return s


def build_inputs(root: Path):
    def put(subject, sheet):
        d = root / "Images_gen" / f"img-001-{subject}"
        d.mkdir(parents=True)
        cv2.imwrite(str(d / f"img-001-{subject} (1).png"), cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR))
    put("blob", shape_sheet(1200, [(x, y) for y in (200, 600, 1000) for x in (200, 600, 1000)]))
    put("quad", shape_sheet(1200, [(x, y) for y in (300, 900) for x in (300, 900)], r=110))
    put("solo", shape_sheet(1200, [(600, 600)], r=140))
    touch = shape_sheet(1200, [(x, y) for y in (200, 600, 1000) for x in (200, 600, 1000)])         # two characters of the middle row are joined across the gutter
    cv2.rectangle(touch, (200, 560), (600, 640), synth.YELLOW, -1)
    put("touch", touch)
    d = root / "Images_gen" / "img-001-bad"; d.mkdir(parents=True)
    grey = np.full((1200, 1200, 3), 128, np.uint8)
    cv2.circle(grey, (600, 600), 200, synth.YELLOW, -1)
    cv2.imwrite(str(d / "img-001-bad (1).png"), cv2.cvtColor(grey, cv2.COLOR_RGB2BGR))


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        build_inputs(cls.tmp / "in")
        cls.srv, cls.c = serve(cls.tmp / "out", cls.tmp / "in", 0, cfg=EngineConfig(), block=False)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, method, path, body=None, raw=None):
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
        h.request(method, path, raw if raw is not None else (json.dumps(body) if body is not None else None), {"Content-Type": "application/json"})
        r = h.getresponse(); data = r.read(); h.close()
        try:
            return r.status, json.loads(data)
        except ValueError:
            return r.status, data

    def wait(self, gid, pred, timeout=180, stuck=12):
        """Poll until pred holds. A failure here used to cost 180 s of silence: an injected
        AssertionError was swallowed by the broad `except Exception` in the cut path, the cell
        stayed PROCESSING, and the timeout was the only symptom. Fail on NO PROGRESS rather than
        on elapsed time, and always say why."""
        end, last, since = time.time() + timeout, None, time.time()
        while time.time() < end:
            s, j = self.req("GET", f"/api/generations/{gid}")
            if pred(j) and not j["busy"]:
                return j
            snap = (j.get("stage"), j.get("error"),
                    tuple(sorted((t["index"], t.get("anim_status"), t.get("anim_reason")) for t in j.get("stickers", []))))
            if snap != last:
                last, since = snap, time.time()
            elif not j.get("busy") and time.time() - since > stuck:
                self.fail(f"stuck at stage={snap[0]!r} for {stuck}s on {gid}: error={snap[1]!r}\n"
                          + "\n".join(f"   S{i[0]} {i[1]} {i[2] or ''}" for i in snap[2]))
            time.sleep(0.25)
        self.fail(f"timeout after {timeout}s on {gid}: stage={last[0]!r} error={last[1]!r}")

    def new(self, prompt, **kw):
        s, j = self.req("POST", "/api/generations", {"prompt": prompt, **kw})
        self.assertEqual(s, 202, j)
        return j["id"], self.wait(j["id"], lambda x: x["stage"] == "sliced")

    def review(self, gid, gate, decision, index=None, note=None, expect=200):
        s, j = self.req("POST", f"/api/generations/{gid}/review", {"gate": gate, "decision": decision, "index": index, "note": note})
        self.assertEqual(s, expect, j)
        return j

    def drive_video(self, gid, drift=None, size=600, frames=45):
        """Build + approve the video sheet, 'make' a video from it, upload it, wait for the slicing."""
        s, j = self.req("POST", f"/api/generations/{gid}/video_sheet")
        self.assertEqual(s, 200, j)
        aid = j["sheet"]
        st = self.req("GET", f"/api/generations/{gid}")[1]
        e = next(v for v in st["video_sheets"] if v["id"] == aid)
        base = pl.out_path(self.tmp / "out", st["generation_id"])
        s, j = self.req("POST", f"/api/generations/{gid}/video_sheet/{aid}/video?name=v.mp4", raw=b"x")
        self.assertEqual(s, 409, j)                                        # G3 not approved yet
        self.review(gid, "video_sheet", "APPROVE", aid)
        sheet = np.array(Image.open(base / e["file"]).convert("RGB"))
        layout = json.loads((base / e["layout"]).read_text())
        mp4 = self.tmp / f"{st['generation_id']}.mp4"
        synth.make_layout_video(mp4, sheet, layout, size=size, frames=frames, drift=drift)
        s, j = self.req("POST", f"/api/generations/{gid}/video_sheet/{aid}/video?name=v.mp4", raw=mp4.read_bytes())
        self.assertEqual(s, 202, j)
        return aid, self.wait(gid, lambda x: next(v for v in x["video_sheets"] if v["id"] == aid)["status"] in ("SLICED", "VIDEO_BLOCKED"))


class GoldenPathTests(Api):
    def test_the_scenario_of_the_plan(self):
        gid, g = self.new("create a blob for school")
        self.assertEqual([s["status"] for s in g["stickers"]], ["READY"] * 9, [(s["index"], s["reason"]) for s in g["stickers"]])
        for s in g["stickers"]:
            self.assertTrue(1 <= len(s["tags"]) <= 5 and s["tags"][0] == s["key"], s["tags"])
            self.assertIn("generous empty margin on every side", s["prompt"])
            self.assertEqual(s["history"][0]["actor"], "python")
        self.assertEqual((g["gate"]["active"], g["template_id"]), ("still", "sheet_3x3"))       # pressing Generate approved the plan (G1)
        self.assertEqual(g["reviews"]["plan"]["note"], "approved by pressing Generate")
        self.assertTrue((pl.out_path(self.tmp / "out", g["generation_id"]) / "source" / "plain" / "S5.png").exists())

        # gate order, server side
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/video_sheet")[0], 409)       # no plan approval, no still decided
        self.review(gid, "anim", "APPROVE", "ready", expect=409)                # nothing sliced yet
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/video_sheet")[0], 409)   # no still decided yet

        # G2: approve all READY, then reject 5 and 6 (a decision can change until A1 is built)
        self.review(gid, "still", "APPROVE", "ready")
        self.review(gid, "still", "REJECT", 5, "pose repeats")
        self.review(gid, "still", "REJECT", 6)
        g = self.req("GET", f"/api/generations/{gid}")[1]
        self.assertEqual([s["review"]["still"] for s in g["stickers"]], ["APPROVED"] * 4 + ["REJECTED"] * 2 + ["APPROVED"] * 3)

        # G3 video sheet: 7 filled slots, 5 and 6 blank key colour, no outline
        aid, g = self.drive_video(gid, drift={1: (-70, 0), 2: (0, -70)})
        e = next(v for v in g["video_sheets"] if v["id"] == aid)
        self.assertEqual((e["slots"], e["status"], e["blocked"]), ([1, 2, 3, 4, 7, 8, 9], "SLICED", None))
        self.assertTrue(all(c["ok"] for c in e["verify"]), e["verify"])
        self.assertTrue(all(c["ok"] for c in e["video_checks"]), [c for c in e["video_checks"] if not c["ok"]])
        self.review(gid, "still", "REJECT", 3, expect=409)                       # locked: A1 was built from these decisions

        # 8: Python blocks 1 and 2 with inside_slot; the rest is READY; 5 and 6 never entered the video
        S = {s["index"]: s for s in g["stickers"]}
        for i in (1, 2):
            self.assertEqual((S[i]["anim_status"], S[i]["anim_reason"]), ("FAILED", "inside_slot"), S[i]["anim_metrics"])
            self.assertEqual(S[i]["review"]["anim"], "BLOCKED")
            self.review(gid, "anim", "APPROVE", i, expect=409)                   # Python's block is final
        for i in (3, 4, 7, 8, 9):
            self.assertEqual(S[i]["anim_status"], "READY", (i, S[i]["anim_reason"], S[i]["anim_report"]))
            self.assertLessEqual(S[i]["anim_metrics"]["kb"], 256)
        for i in (5, 6):
            self.assertEqual(S[i]["anim_status"], "NOT_REQUESTED")

        # G4 + G5
        self.review(gid, "pack", "APPROVE", expect=409)                          # animations still pending
        self.review(gid, "anim", "APPROVE", "ready")
        j = self.review(gid, "pack", "APPROVE")
        self.assertEqual(j["final"], [3, 4, 7, 8, 9])
        g = self.req("GET", f"/api/generations/{gid}")[1]
        self.assertEqual((g["final"], g["stage"], g["gate"]["active"]), ([3, 4, 7, 8, 9], "pack_final", "done"))

        # every sticker's history tells its path
        h = lambda i: [(x["stage"], x["actor"], x["decision"], x["reason"]) for x in g["stickers"][i - 1]["history"]]
        self.assertIn(("still", "human", "REJECT", "pose repeats"), h(5))
        self.assertNotIn("video", [x[0] for x in h(5)])
        blocked = [x for x in g["stickers"][0]["history"] if x["stage"] == "video"][0]
        self.assertEqual((blocked["actor"], blocked["decision"], blocked["reason"], blocked["ref"]), ("python", "BLOCK", "inside_slot", "A1"))
        self.assertEqual(blocked["detail"]["check"], "inside_slot")
        self.assertGreater(blocked["detail"]["data"]["over_px"], 0)
        self.assertEqual([x[0] for x in h(3)], ["sliced", "still", "video_sheet", "video_sheet", "video", "anim", "pack"])
        self.assertEqual([(e["stage"], e.get("actor")) for e in g["events"] if e["stage"] == "pack_final"], [("pack_final", "human")])

        # the final WEBMs carry no forced outline: at most a fringe of near-white opaque pixels
        from stickerloom.engine import ffmpeg as ff
        for i in (3, 4, 7, 8, 9):
            a = ff.decode_alpha(pl.out_path(self.tmp / "out", g["generation_id"]) / g["stickers"][i - 1]["webm"], 2)[0]
            opaque = a[..., 3] > 127
            white = float((opaque & (a[..., :3].min(-1) > 235)).sum()) / max(float(opaque.sum()), 1)
            self.assertLess(white, 0.02, (i, white))

        # G5 -> Library
        pk = self.req("POST", "/api/packs", {"name": "Golden"})[1]
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/pack_add", {"pack_id": pk["id"]})[1]["added"], 5)
        lib = self.req("GET", "/api/library")[1]
        self.assertEqual(len(next(p for p in lib["packs"] if p["id"] == pk["id"])["stickers"]), 5)

        # search finds it by key and by tag, with its review state
        key = g["stickers"][2]["key"]
        found = self.req("GET", f"/api/search?q={key}")[1]["results"]
        self.assertTrue(any(r["id"] == gid and r["index"] == 3 and r["final"] for r in found))

    def test_python_block_is_final_and_sheet_gate(self):
        s, j = self.req("POST", "/api/generations", {"prompt": "create a bad one"})
        self.assertEqual(s, 202)
        g = self.wait(j["id"], lambda x: x["stage"] == "sliced")
        self.assertEqual({x["reason"] for x in g["stickers"]}, {"background_is_key"})        # the sheet has no green screen
        self.assertEqual(g["verify"]["sheet"][2]["name"], "background_is_key")
        self.review(j["id"], "plan", "APPROVE")
        self.review(j["id"], "still", "APPROVE", 1, expect=409)                                # nobody can approve a FAILED sticker
        self.assertEqual(self.req("POST", f"/api/generations/{j['id']}/video_sheet")[0], 409)

    def test_a_layout_problem_cuts_the_sheet_anyway_and_a_stopped_sheet_has_one_free_click(self):
        """: "Python blocking the imported images is stupid, it does not let me bypass it". Two characters touching across the gutter used to throw the whole
        sheet away (9 of 9 FAILED, no file). Now the sheet is cut, every cell is judged on its own, the problem is a warning on every sticker, and a human decides at G2. A sheet
        with no key screen still stops, but one free click ("Cut it anyway") cuts it; a file that does not open is the only hard stop."""
        gid, g = self.new("create a touch one")
        sheet = next(c for c in g["verify"]["sheet"] if c["name"] == "grid_detected")
        self.assertFalse(sheet["ok"], "the sheet really has a layout problem (the grid found is not the planned one)")
        ready = [x for x in g["stickers"] if x["status"] == "READY"]
        self.assertGreaterEqual(len(ready), 5, "the good cells survive")
        self.assertFalse(any((x.get("metrics") or {}).get("sheet_blocked") for x in g["stickers"]), "nothing is thrown away")
        issue = [h for h in ready[0]["history"] if h["stage"] == "sheet"]
        self.assertEqual((issue[0]["decision"], issue[0]["reason"]), ("WARN", "grid_detected"), "the problem is a warning, in the sticker's own history")
        self.assertTrue(all(x["png"] for x in ready))
        self.review(gid, "plan", "APPROVE")
        self.review(gid, "still", "APPROVE", ready[0]["index"])                              # and a human can decide on a cell of such a sheet like on any other

        bad_id, bad = self.new("create a bad one")                                          # no key screen at all: stopped, with the click
        self.assertEqual({x["reason"] for x in bad["stickers"]}, {"background_is_key"})
        self.assertEqual(self.req("POST", f"/api/generations/{bad_id}/recut")[0], 202)
        cut = self.wait(bad_id, lambda x: any((h["decision"] == "APPROVE" and h["reason"] == "cut anyway") for h in x["stickers"][0]["history"]) and x["stage"] == "sliced")
        self.assertTrue(any(x["png"] for x in cut["stickers"]) or all(x["reason"] != "background_is_key" or not (x.get("metrics") or {}).get("sheet_blocked") for x in cut["stickers"]),
                        "the sheet was cut: the cells are judged one by one now")
        self.assertFalse(any((x.get("metrics") or {}).get("sheet_blocked") for x in cut["stickers"]))
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/recut")[0], 409, "a batch with decisions is not cut again behind them")

    def test_the_outline_is_a_choice(self):
        """The white die-cut stroke is opt-in: omitted and 0 give the plain sticker, a width gives that stroke, all stored with the generation."""
        alpha = lambda g, rel: np.array(Image.open(pl.out_path(self.tmp / "out", g["generation_id"]) / rel).convert("RGBA"))[..., 3]
        opaque = lambda a: int((a > 127).sum())
        gid0, g0 = self.new("create a blob for school", outline=0)
        gidD, gD = self.new("create a blob for school")
        gid24, g24 = self.new("create a blob for school", outline=24)
        self.assertEqual([g0["outline_px"], gD["outline_px"], g24["outline_px"]], [0, 0, 24])
        self.assertEqual(g0["stickers"][0]["status"], "READY")
        plain = alpha(g0, "source/plain/S1.png")
        self.assertTrue((alpha(g0, g0["stickers"][0]["png"]) == plain).all())             # outline 0: the sticker IS the plain cutout
        self.assertTrue((alpha(gD, gD["stickers"][0]["png"]) == plain).all())             # omitted: no stroke unless asked
        a24 = opaque(alpha(g24, g24["stickers"][0]["png"]))
        self.assertGreater(a24, opaque(plain) * 1.1)                                       # a wider stroke is a wider stroke
        self.assertEqual(self.req("POST", "/api/generations", {"prompt": "blob", "outline": 99})[0], 400)

    def test_appearance_renders_the_edge_without_rekeying(self):
        """POST .../appearance stores outline+erode, re-renders the stills from the plain twins, and
        marks animations STALE so Animate redraws them. Bad values never touch the files."""
        from stickerloom.engine.render import apply_edge
        gid, g = self.new("create a blob for school")
        before = {s["png"]: (pl.out_path(self.tmp / "out", g["generation_id"]) / s["png"]).read_bytes() for s in g["stickers"]}
        plain_a = np.array(Image.open(pl.out_path(self.tmp / "out", g["generation_id"]) / "source" / "plain" / "S1.png").convert("RGBA"))[..., 3]
        s, j = self.req("POST", f"/api/generations/{gid}/appearance", {"outline": 12, "erode": 2})
        self.assertEqual(s, 200, j)
        self.assertEqual((j["outline_px"], j["erode_px"], j["rerendered"]), (12, 2, 9))
        g = self.req("GET", f"/api/generations/{gid}")[1]
        self.assertEqual((g["outline_px"], g["erode_px"]), (12, 2))
        after = np.array(Image.open(pl.out_path(self.tmp / "out", g["generation_id"]) / g["stickers"][0]["png"]).convert("RGBA"))
        ring_free = int((((after[..., :3].min(-1) < 128) & (after[..., 3] > 127)).sum()))
        plain_rgb = np.array(Image.open(pl.out_path(self.tmp / "out", g["generation_id"]) / "source" / "plain" / "S1.png").convert("RGBA"))[..., :3]
        plain_dark = int(((plain_rgb.min(-1) < 128) & (plain_a > 127)).sum())
        self.assertLess(ring_free, plain_dark)                                 # erosion trimmed fringe, what remains is mostly the ring
        solid = (plain_a > 200)
        self.assertGreater(int(((after[..., 3] > 200) & solid).sum()), int(solid.sum()) * 0.90)   # the solid subject survives
        self.assertIn("appearance", [h["stage"] for h in g["stickers"][0]["history"]])
        self.assertIn("edge_trimmed", [c["name"] for c in g["stickers"][0]["report"]])
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/appearance", {"erode": 99})[0], 400)
        s, j = self.req("POST", f"/api/generations/{gid}/appearance", {"outline": 0, "erode": 0})
        self.assertEqual(s, 200, j)
        self.assertTrue((np.array(Image.open(pl.out_path(self.tmp / "out", g["generation_id"]) / g["stickers"][0]["png"]).convert("RGBA"))[..., 3]
                                 == plain_a).all())                                            # outline 0 + erode 0 = the plain twin
        # the subject renders exactly once through alpha, even on white: composited over white == white where transparent
        fin = apply_edge(np.full((4, 4, 3), 128.0, np.float32), np.pad(np.ones((2, 2)), 1)[..., None].reshape(4, 4) / 1.0, 4, 0)
        white = fin[..., :3].astype(np.float32) * (fin[..., 3:4] / 255.0) + 255.0 * (1 - fin[..., 3:4] / 255.0)
        self.assertTrue((white[fin[..., 3] == 0] == 255).all())

    def test_create_more_keeps_the_edge_finish(self):
        """Create more inherits outline+erode from the parent batch instead of silently reverting to the defaults.
        The solo subject ships its 1x1 sheet twice (variants 1 and 2) so /more has somewhere to go."""
        import shutil as _shutil
        src = self.tmp / "in" / "Images_gen" / "img-001-solo"
        dst = self.tmp / "in" / "Images_gen" / "img-002-solo"
        if not dst.exists():
            _shutil.copytree(src, dst)
            (dst / "img-001-solo (1).png").rename(dst / "img-002-solo (1).png")
        s, j = self.req("POST", "/api/generations", {"prompt": "create a solo for school", "outline": 0, "erode": 2})
        self.assertEqual(s, 202, j)
        gid = j["id"]
        g = self.wait(gid, lambda x: x["stage"] == "sliced")
        self.assertEqual((g["outline_px"], g["erode_px"]), (0, 2))
        s, j = self.req("POST", f"/api/generations/{gid}/more")
        self.assertEqual(s, 202, j)
        nxt = self.wait(j["id"], lambda x: x["stage"] == "sliced")
        self.assertEqual((nxt["outline_px"], nxt["erode_px"]), (0, 2))

    def test_rejected_plan_stops_everything(self):
        gid, g = self.new("create a blob for school")
        self.review(gid, "plan", "REJECT", note="wrong subject")
        self.review(gid, "still", "APPROVE", "ready", expect=409)
        self.review(gid, "plan", "APPROVE")                                                    # a plan decision can change until stills are reviewed
        self.review(gid, "still", "APPROVE", 1)
        self.review(gid, "plan", "REJECT", expect=409)                                         # ...not after the next stage started from it

    def test_two_by_two_goes_through_every_gate(self):
        gid, g = self.new("create a quad for school")
        self.assertEqual((g["grid"], len(g["stickers"]), g["template_id"]), ([2, 2], 4, "sheet_2x2"))
        self.assertEqual([s["status"] for s in g["stickers"]], ["READY"] * 4)
        self.review(gid, "plan", "APPROVE")
        self.review(gid, "still", "APPROVE", "ready")
        self.review(gid, "still", "REJECT", 2)
        aid, g = self.drive_video(gid)
        self.assertEqual(next(v for v in g["video_sheets"] if v["id"] == aid)["slots"], [1, 3, 4])
        self.assertEqual([s["anim_status"] for s in g["stickers"]], ["READY", "NOT_REQUESTED", "READY", "READY"], [s["anim_reason"] for s in g["stickers"]])
        self.review(gid, "anim", "APPROVE", "ready")
        self.assertEqual(self.review(gid, "pack", "APPROVE")["final"], [1, 3, 4])

    def test_one_by_one_regeneration_goes_through_every_gate(self):
        gid, g = self.new("create a blob for school")
        self.review(gid, "plan", "APPROVE")
        s, j = self.req("POST", f"/api/generations/{gid}/regen", {"index": 5, "subject": "nothing"})
        self.assertEqual(s, 404, j)                                                            # no such subject
        s, j = self.req("POST", f"/api/generations/{gid}/regen", {"index": 5})
        self.assertEqual(s, 409, j)                                                            # the parent's own 3x3 sheet is not a 1x1 sheet
        s, j = self.req("POST", f"/api/generations/{gid}/regen", {"index": 5, "subject": "solo"})
        self.assertEqual(s, 202, j)
        r = self.wait(j["id"], lambda x: x["stage"] == "sliced")
        self.assertEqual((r["grid"], r["regen_of"], r["parent"], r["template_id"]), ([1, 1], f"{g['generation_id']}/S5", gid, "single_1x1"))
        self.assertEqual((len(r["stickers"]), r["stickers"][0]["key"], r["stickers"][0]["status"]), (1, g["stickers"][4]["key"], "READY"))
        self.assertEqual(r["reviews"]["plan"]["decision"], "APPROVE")                          # inherited from the parent
        self.review(r["number"], "still", "APPROVE", "ready")
        aid, r = self.drive_video(r["number"])
        self.assertEqual(r["stickers"][0]["anim_status"], "READY", r["stickers"][0]["anim_reason"])
        self.review(r["number"], "anim", "APPROVE", "ready")
        self.assertEqual(self.review(r["number"], "pack", "APPROVE")["final"], [1])
        self.assertEqual(self.req("GET", f"/api/generations/{gid}")[1]["stickers"][4]["review"]["still"], "PENDING")   # the parent is never modified

    def test_a_human_can_allow_a_slot_block_and_take_it_back(self):
        gid, g = self.new("blob")
        self.review(gid, "still", "APPROVE", "ready")
        aid, g = self.drive_video(gid, drift={1: (-70, 0)})
        S = {s["index"]: s for s in g["stickers"]}
        self.assertEqual((S[1]["anim_status"], S[1]["anim_reason"]), ("FAILED", "inside_slot"))
        self.review(gid, "anim", "APPROVE", 1, expect=409)                                   # the review gate still refuses a blocked animation
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/allow", {"index": 3})[0], 409)   # a READY one has nothing to allow
        s_, j = self.req("POST", f"/api/generations/{gid}/allow", {"index": 1, "allow": True})
        self.assertEqual(s_, 202, j)
        g = self.wait(gid, lambda x: x["stickers"][0]["anim_status"] == "READY")
        s1 = g["stickers"][0]
        self.assertEqual(s1["anim_override"], ["inside_slot"])
        chk = next(c for c in s1["anim_report"] if c["name"] == "inside_slot")
        self.assertEqual((chk["ok"], chk["severity"]), (False, "WARN"))                       # still listed, downgraded to a warning, marked as allowed
        self.assertIn("allowed by you", chk["detail"])
        self.assertTrue(s1["webm"] and s1["review"]["anim"] == "PENDING")                     # it exists and enters the set like any other animation
        h = next(x for x in reversed(s1["history"]) if x["actor"] == "human" and x["stage"] == "video")
        self.assertEqual((h["decision"], h["reason"]), ("APPROVE", "allowed anyway: inside_slot"))
        # an edge change re-cuts every animation from the stored video and keeps the permission
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/appearance", {"outline": 4, "erode": 0, "reslice": True})[0], 200)
        g = self.wait(gid, lambda x: not any(t["anim_status"] in ("STALE", "PROCESSING") for t in x["stickers"]))
        self.assertEqual((g["stickers"][0]["anim_status"], g["stickers"][0]["anim_override"]), ("READY", ["inside_slot"]))
        # withdrawn: blocked again
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/allow", {"index": 1, "allow": False})[0], 202)
        g = self.wait(gid, lambda x: x["stickers"][0]["anim_status"] == "FAILED")
        self.assertEqual((g["stickers"][0]["anim_reason"], g["stickers"][0]["anim_override"]), ("inside_slot", []))

    def test_switching_a_verdict_back_swaps_the_finished_clip_and_renders_nothing(self):
        """The clip is the same under both verdicts, only the verdict differs: the first allow renders (no clip existed), every later allow / take-back of the same cell restores the finished
        state at once."""
        from stickerloom.flow import gates as G
        gid, g = self.new("blob")
        self.review(gid, "still", "APPROVE", "ready")
        aid, g = self.drive_video(gid, drift={1: (-70, 0)})
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/allow", {"index": 1, "allow": True})[0], 202)
        g = self.wait(gid, lambda x: x["stickers"][0]["anim_status"] == "READY")
        webm = g["stickers"][0]["webm"]
        with mock.patch.object(G, "process_video", side_effect=AssertionError("a verdict switch must not render again")):
            self.assertEqual(self.req("POST", f"/api/generations/{gid}/allow", {"index": 1, "allow": False})[0], 202)
            g = self.wait(gid, lambda x: x["stickers"][0]["anim_status"] == "FAILED")
            s1 = g["stickers"][0]
            self.assertEqual((s1["anim_reason"], s1["anim_override"], s1["webm"]), ("inside_slot", [], webm), "blocked again, and the clip is still there to look at")
            self.assertEqual(s1["review"]["anim"], "BLOCKED")
            self.assertEqual(self.req("POST", f"/api/generations/{gid}/allow", {"index": 1, "allow": True})[0], 202)
            g = self.wait(gid, lambda x: x["stickers"][0]["anim_status"] == "READY")
            s1 = g["stickers"][0]
            self.assertEqual((s1["anim_override"], s1["webm"], s1["review"]["anim"]), (["inside_slot"], webm, "PENDING"))
            chk = next(c for c in s1["anim_report"] if c["name"] == "inside_slot")
            self.assertEqual((chk["ok"], chk["severity"]), (False, "WARN"))
            human = [x for x in s1["history"] if x["actor"] == "human" and x["stage"] == "video"]
            self.assertEqual([x["decision"] for x in human], ["APPROVE", "REJECT", "APPROVE"], "every click is still a recorded decision")
        # a new cut of the video throws the remembered state away: the next switch renders again
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/appearance", {"outline": 4, "erode": 0, "reslice": True})[0], 200)
        g = self.wait(gid, lambda x: not any(t["anim_status"] in ("STALE", "PROCESSING") for t in x["stickers"]))
        rendered = []
        real = G.process_video
        with mock.patch.object(G, "process_video", side_effect=lambda *a, **k: (rendered.append(1), real(*a, **k))[1]):
            self.assertEqual(self.req("POST", f"/api/generations/{gid}/allow", {"index": 1, "allow": False})[0], 202)
            self.wait(gid, lambda x: x["stickers"][0]["anim_status"] == "FAILED")
        self.assertTrue(rendered, "after a re-cut nothing is remembered, so the switch renders")

    def test_allow_all_and_every_cell_toggles(self):
        gid, g = self.new("blob")
        self.review(gid, "still", "APPROVE", "ready")
        aid, g = self.drive_video(gid, drift={1: (-70, 0), 2: (0, -70)})
        st = lambda x: {s["index"]: s for s in x["stickers"]}
        self.assertEqual([st(g)[i]["anim_status"] for i in (1, 2)], ["FAILED", "FAILED"])
        s_, j = self.req("POST", f"/api/generations/{gid}/allow", {"all": True, "allow": False})
        self.assertEqual(s_, 409, j)                                                              # nothing has been allowed yet
        s_, j = self.req("POST", f"/api/generations/{gid}/allow", {"all": True})
        self.assertEqual((s_, j["indexes"]), (202, [1, 2]), j)                                    # one click, both blocked cells, one re-cut
        g = self.wait(gid, lambda x: all(st(x)[i]["anim_status"] == "READY" for i in (1, 2)))
        self.assertEqual([st(g)[i]["anim_override"] for i in (1, 2)], [["inside_slot"], ["inside_slot"]])
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/allow", {"all": True})[0], 409)   # nothing left to allow
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/allow", {"index": 1, "allow": False})[0], 202)    # a cell toggles back on its own
        g = self.wait(gid, lambda x: st(x)[1]["anim_status"] == "FAILED" and st(x)[2]["anim_status"] == "READY")
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/allow", {"all": True, "allow": False})[1]["indexes"], [2])   # take all back: only what was allowed
        g = self.wait(gid, lambda x: st(x)[2]["anim_status"] == "FAILED")
        self.assertEqual([st(g)[i]["anim_override"] for i in (1, 2)], [[], []])
        # and the very same two again with one click
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/allow", {"all": True})[1]["indexes"], [1, 2])
        self.wait(gid, lambda x: all(st(x)[i]["anim_status"] == "READY" for i in (1, 2)))

    def test_wrong_video_is_blocked_before_slicing(self):
        gid, g = self.new("create a blob for school")
        self.review(gid, "plan", "APPROVE"); self.review(gid, "still", "APPROVE", "ready")
        s, j = self.req("POST", f"/api/generations/{gid}/video_sheet")
        aid = j["sheet"]
        self.review(gid, "video_sheet", "APPROVE", aid)
        other = self.tmp / "other.mp4"
        layout = json.loads((pl.out_path(self.tmp / "out", g["generation_id"]) / "video_sheet" / aid / "layout.json").read_text())
        synth.make_layout_video(other, np.array(Image.open(pl.out_path(self.tmp / "out", g["generation_id"]) / "video_sheet" / aid / "sheet.png").convert("RGB"))[::-1, ::-1].copy(), layout, size=600, frames=30)
        s, j = self.req("POST", f"/api/generations/{gid}/video_sheet/{aid}/video", raw=other.read_bytes())
        self.assertEqual(s, 202)
        g = self.wait(gid, lambda x: x["video_sheets"][0]["status"] in ("SLICED", "VIDEO_BLOCKED"))
        e = g["video_sheets"][0]
        self.assertEqual((e["status"], e["block"]), ("VIDEO_BLOCKED", "layout_match"))
        self.assertTrue(all(s["anim_status"] == "NOT_REQUESTED" for s in g["stickers"]))
        self.assertEqual(g["gate"]["active"], "video_upload")                                  # a corrected video can be uploaded to the same sheet


if __name__ == "__main__":
    unittest.main()
