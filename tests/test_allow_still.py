"""'Use it anyway'.

Every Python BLOCK is TECHNICAL (Telegram itself would reject the file: format, size, codec ...) and final, or a JUDGEMENT (a character touching its cell, a hole, a slot, a loop)
that a person may allow with a recorded click that can be taken back. A cell with no picture at all can never be allowed. This file pins that for stills and animations through
the real server on synthetic sheets: the allow route, the verifier honouring the permission on later cuts, the G2 gate approving an allowed still, gating of members.
Nothing here reaches a provider."""
import http.client
import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

import cv2

from stickerloom.console.server import serve
from stickerloom.engine.config import EngineConfig
from stickerloom.engine import verify
from stickerloom.flow import gates, pipeline as pl
from tests import synth
from tests.test_golden import Api, build_inputs as golden_inputs, shape_sheet

PAGE = {"Sec-Fetch-Site": "same-origin"}


def edgy_sheet():
    """A 3x3 sheet whose first three cells are each blocked by Python for a different reason, the rest are fine:
    S1 a big hole through the character (`holes`, a judgement), S2 nothing at all (`blank_cell`: no picture), S3 a character crossing the sheet's right border (`inside_cell`, a judgement)."""
    s = shape_sheet(1200, [(x, y) for y in (200, 600, 1000) for x in (200, 600, 1000)])
    key = tuple(int(v) for v in s[2, 2])
    cv2.circle(s, (200, 200), 80, key, -1)
    s[0:400, 400:800] = s[2, 2]
    s[0:400, 800:1200] = s[2, 2]
    cv2.circle(s, (1130, 200), 90, synth.YELLOW, -1, cv2.LINE_AA)
    return s


def build_inputs(root: Path):
    d = root / "Images_gen" / "img-001-edgy"
    d.mkdir(parents=True)
    cv2.imwrite(str(d / "img-001-edgy (1).png"), cv2.cvtColor(edgy_sheet(), cv2.COLOR_RGB2BGR))
    v = root / "videos_gen" / "vid-001-edgy"                    # a prepared 3x3 video: every cell is a circle drifting right, so a loop that is not closed has a seam
    v.mkdir(parents=True)
    synth.make_video(v / "vid-001-edgy (1).mp4")


class Rig:
    """A real server on a private folder, with the small helpers every test here needs."""

    def __init__(self, cfg, build=build_inputs):
        self.tmp = Path(tempfile.mkdtemp())
        build(self.tmp / "in")
        self.srv, self.c = serve(self.tmp / "out", self.tmp / "in", 0, cfg=cfg, block=False)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.c.release_writer()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def req(self, method, path, body=None, headers=None):
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json", **(headers or {})})
        r = h.getresponse()
        raw = r.read()
        h.close()
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw

    def wait(self, gid, pred, timeout=120, headers=None):
        end = time.time() + timeout
        while time.time() < end:
            s, j = self.req("GET", f"/api/generations/{gid}", headers=headers)
            if s == 200 and pred(j) and not j["busy"]:
                return j
            time.sleep(0.2)
        raise AssertionError(f"timeout waiting for G{gid}")

    def new(self, prompt, headers=None, **kw):
        s, j = self.req("POST", "/api/generations", {"prompt": prompt, **kw}, headers)
        assert s == 202, j
        return j["id"], self.wait(j["id"], lambda x: x["stage"] == "sliced", headers=headers)

    def review(self, gid, gate, decision, index=None, expect=200):
        s, j = self.req("POST", f"/api/generations/{gid}/review", {"gate": gate, "decision": decision, "index": index})
        assert s == expect, (s, j)
        return j


class StillsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rig = Rig(EngineConfig(min_sheet_px=256, loop_fade_frames=0))        # loop_fade_frames=0: a clip that does not loop is not closed for us, so its seam BLOCKs

    @classmethod
    def tearDownClass(cls):
        cls.rig.close()

    def allow(self, gid, **body):
        return self.rig.req("POST", f"/api/generations/{gid}/allow", body)

    def S(self, g):
        return {s["index"]: s for s in g["stickers"]}

    # ---- what the page is told ----------------------------------------------------------------------------------------------
    def test_a_batch_says_what_can_be_allowed_and_why_the_rest_cannot(self):
        gid, g = self.rig.new("create an edgy one")
        S = self.S(g)
        self.assertEqual([(S[i]["status"], S[i]["reason"]) for i in (1, 2, 3)], [("FAILED", "holes"), ("FAILED", "empty_subject"), ("FAILED", "inside_cell")])
        self.assertTrue(all(S[i]["status"] == "READY" for i in range(4, 10)))
        al = g["allow"]["still"]
        self.assertEqual((al["can"], al["allowed"], al["undo"]), ([1, 3], [], []))          # S2 has no picture: nothing to allow
        self.assertIn("cell", al["why"]["3"])                                                 # one plain line for the tile
        self.assertIn("hole", al["why"]["1"])
        self.assertIn("Nothing was cut", al["final"]["2"])                                    # and why S2 is final
        self.assertEqual(g["allow"]["animation"]["can"], [])

    # ---- the whole life of an allowed still ------------------------------------------------------------------------------------
    def test_a_still_blocked_by_inside_cell_can_be_allowed_approved_kept_and_taken_back(self):
        gid, g = self.rig.new("create an edgy one")
        base = f"/api/generations/{gid}"
        self.assertEqual(self.rig.req("POST", base + "/review", {"gate": "still", "decision": "APPROVE", "index": 3})[0], 409)       # final until a person allows it
        s, j = self.allow(gid, kind="still", index=3)
        self.assertEqual((s, j["kind"], j["indexes"], j["allow"]), (202, "still", [3], True), j)
        g = self.rig.wait(gid, lambda x: x["stickers"][2]["status"] == "READY")
        s3 = g["stickers"][2]
        self.assertEqual(s3["still_override"], ["inside_cell"])
        chk = next(c for c in s3["report"] if c["name"] == "inside_cell")
        self.assertEqual((chk["ok"], chk["severity"]), (False, "WARN"))                       # still in the report, a warning now, marked as allowed
        self.assertIn("allowed by you", chk["detail"])
        self.assertEqual(s3["metrics"]["waived"], ["inside_cell"])
        self.assertNotIn("inside_cell", s3["metrics"].get("warnings", []))
        self.assertTrue(s3["png"] and (pl.out_path(self.rig.tmp / "out", g["generation_id"]) / s3["png"]).is_file())
        self.assertTrue((pl.out_path(self.rig.tmp / "out", g["generation_id"]) / "source" / "plain" / "S3.png").is_file())      # the video sheet can be built from it
        self.assertEqual(s3["review"]["still"], "PENDING")                                    # an ordinary pending still now
        h = next(x for x in reversed(s3["history"]) if x["actor"] == "human")
        self.assertEqual((h["stage"], h["decision"], h["reason"], h["detail"]["override"]), ("still", "APPROVE", "allowed anyway: inside_cell", ["inside_cell"]))
        al = g["allow"]["still"]
        self.assertEqual((al["can"], al["allowed"], al["undo"]), ([1], [3], [3]))
        self.assertEqual((self.S(g)[4]["review"]["still"], self.S(g)[1]["status"]), ("PENDING", "FAILED"))     # the other cells were not touched
        self.assertEqual((g["stickers"][1]["png"], g["stickers"][1]["status"]), (None, "FAILED"))              # S2 (no picture) stays without one

        self.rig.review(gid, "still", "APPROVE", 3)                                           # the G2 gate lets the person approve it normally
        g = self.rig.req("GET", base)[1]
        self.assertEqual(g["stickers"][2]["review"]["still"], "APPROVED")

        # a re-render of the edge goes through the verifier again and keeps the permission (the cell is not blocked again behind the person's back)
        s, j = self.rig.req("POST", base + "/appearance", {"outline": 4, "erode": 0})
        self.assertEqual(s, 200, j)
        g = self.rig.req("GET", base)[1]
        self.assertEqual((g["stickers"][2]["status"], g["stickers"][2]["still_override"], g["stickers"][2]["review"]["still"]), ("READY", ["inside_cell"], "APPROVED"))

        # taken back: Python's block is back, the approval it stood on is void, the file goes
        s, j = self.allow(gid, kind="still", index=3, allow=False)
        self.assertEqual((s, j["allow"]), (202, False), j)
        g = self.rig.wait(gid, lambda x: x["stickers"][2]["status"] == "FAILED")
        s3 = g["stickers"][2]
        self.assertEqual((s3["reason"], s3["still_override"], s3["review"]["still"], s3["png"]), ("inside_cell", [], "BLOCKED", None))
        self.assertTrue(any(x["actor"] == "human" and x["decision"] == "REJECT" and x["reason"] == "allowance withdrawn: inside_cell" for x in s3["history"]))
        self.assertEqual((g["allow"]["still"]["can"], g["allow"]["still"]["allowed"]), ([1, 3], []))
        self.assertEqual(self.rig.req("POST", base + "/review", {"gate": "still", "decision": "APPROVE", "index": 3})[0], 409)    # blocked again
        self.assertEqual(self.allow(gid, kind="still", index=3, allow=False)[0], 409)         # nothing left to take back

    def test_the_permission_survives_cutting_the_sheet_again(self):
        """'Cut it anyway' (recut) cuts the whole sheet again from scratch: the cells a person allowed come out allowed, not blocked again."""
        gid, g = self.rig.new("create an edgy one")
        self.assertEqual(self.allow(gid, kind="still", index=3)[0], 202)
        self.rig.wait(gid, lambda x: x["stickers"][2]["status"] == "READY")
        self.assertEqual(self.rig.req("POST", f"/api/generations/{gid}/recut")[0], 202)
        g = self.rig.wait(gid, lambda x: sum(1 for h in x["stickers"][0]["history"] if h["reason"] == "cut anyway") == 1 and x["stage"] == "sliced")
        s3 = g["stickers"][2]
        self.assertEqual((s3["status"], s3["still_override"], s3["metrics"]["waived"]), ("READY", ["inside_cell"], ["inside_cell"]))
        self.assertEqual((g["stickers"][0]["status"], g["stickers"][1]["status"]), ("FAILED", "FAILED"))     # what nobody allowed is blocked as before

    def test_one_click_allows_every_judgement_block_but_never_the_empty_cell(self):
        gid, g = self.rig.new("create an edgy one")
        s, j = self.allow(gid, kind="still", all=True)
        self.assertEqual((s, j["indexes"]), (202, [1, 3]), j)                                 # S2 has no picture: not offered, not allowed
        g = self.rig.wait(gid, lambda x: x["stickers"][0]["status"] == "READY" and x["stickers"][2]["status"] == "READY")
        self.assertEqual(g["stickers"][1]["status"], "FAILED")
        self.assertEqual(self.allow(gid, kind="still", all=True)[0], 409)                     # nothing left to allow
        s, j = self.allow(gid, kind="still", index=2)
        self.assertEqual(s, 409)
        self.assertIn("no picture", j["error"])
        s, j = self.allow(gid, kind="still", indexes=[2, 3])
        self.assertEqual(s, 409, j)                                                           # one that cannot be allowed refuses the whole request
        self.assertEqual(self.allow(gid, kind="still", all=True, allow=False)[1]["indexes"], [1, 3])      # take all back: only what was allowed
        self.rig.wait(gid, lambda x: x["stickers"][0]["status"] == "FAILED" and x["stickers"][2]["status"] == "FAILED")

    def test_a_hole_is_a_judgement_call_too_and_a_new_edge_keeps_the_permission(self):
        gid, g = self.rig.new("create an edgy one")
        self.assertEqual(self.allow(gid, kind="still", index=1)[0], 202)
        g = self.rig.wait(gid, lambda x: x["stickers"][0]["status"] == "READY")
        s1 = g["stickers"][0]
        self.assertEqual((s1["still_override"], s1["metrics"]["waived"]), (["holes"], ["holes"]))
        self.assertEqual(next(c for c in s1["report"] if c["name"] == "holes")["severity"], "WARN")
        # a re-render of the edge re-judges `holes` on the finished sticker: with the permission it is written, without it the old file would have stood
        s, j = self.rig.req("POST", f"/api/generations/{gid}/appearance", {"outline": 4, "erode": 0})
        self.assertEqual((s, j["rerendered"] >= 1), (200, True), j)
        g = self.rig.req("GET", f"/api/generations/{gid}")[1]
        self.assertEqual((g["stickers"][0]["metrics"]["outline_px"], g["stickers"][0]["still_override"]), (4, ["holes"]))

    def test_bad_requests_say_why(self):
        gid, g = self.rig.new("create an edgy one")
        s, j = self.allow(gid, kind="still", index=4)                                         # a READY sticker
        self.assertEqual(s, 409)
        self.assertIn("nothing to allow", j["error"])
        self.assertEqual(self.allow(gid, kind="sticker", index=3)[0], 400)
        self.assertEqual(self.allow(gid, kind="still")[0], 400)                               # which sticker?
        self.assertEqual(self.allow(gid, kind="still", index=99)[0], 400)
        self.assertEqual(self.allow(gid, kind="still", index=3, allow=False)[0], 409)         # nothing was allowed yet
        s, j = self.allow(gid, index=3)                                                       # no kind: the route's old meaning, animations
        self.assertEqual(s, 409, j)
        self.assertEqual(self.rig.req("POST", "/api/generations/9999/allow", {"kind": "still", "index": 1})[0], 404)

    def test_the_stills_are_locked_once_a_video_sheet_was_built_from_them(self):
        gid, g = self.rig.new("create an edgy one")
        self.rig.review(gid, "still", "APPROVE", "ready")
        s, j = self.rig.req("POST", f"/api/generations/{gid}/video_sheet")
        self.assertEqual(s, 200, j)
        s, j = self.allow(gid, kind="still", index=3)
        self.assertEqual(s, 409)
        self.assertIn("Locked", j["error"])                                                   # the same rule as G2: reject the sheet to change the decisions
        self.assertEqual(self.rig.req("GET", f"/api/generations/{gid}")[1]["allow"]["still"]["can"], [])

    # ---- animations ----------------------------------------------------------------------------------------------------------
    def test_a_loop_that_does_not_close_can_be_allowed_on_a_prepared_video_and_taken_back(self):
        gid, g = self.rig.new("create an edgy one", variant=1)
        base = f"/api/generations/{gid}"
        self.assertEqual(self.rig.req("POST", base + "/animate", {"scope": "slice", "index": 4})[0], 202)
        g = self.rig.wait(gid, lambda x: x["stickers"][3]["anim_status"] in ("READY", "FAILED"))
        a1 = g["stickers"][3]
        self.assertEqual((a1["anim_status"], a1["anim_reason"]), ("FAILED", "loop_seam"), a1["anim_metrics"])
        self.assertEqual(g["allow"]["animation"]["can"], [4])                                 # a judgement call, so it can be allowed ...
        self.assertIn("loop", g["allow"]["animation"]["why"]["4"])
        self.rig.review(gid, "anim", "APPROVE", 4, expect=409)                                # ... and until it is, the gate refuses it
        s, j = self.allow(gid, index=4)                                                       # no kind: animations
        self.assertEqual((s, j["kind"], j["indexes"]), (202, "animation", [4]), j)
        g = self.rig.wait(gid, lambda x: x["stickers"][3]["anim_status"] == "READY")
        a1 = g["stickers"][3]
        self.assertEqual(a1["anim_override"], ["loop_seam"])
        chk = next(c for c in a1["anim_report"] if c["name"] == "loop_seam")
        self.assertEqual((chk["ok"], chk["severity"]), (False, "WARN"))
        self.assertIn("allowed by you", chk["detail"])
        self.assertTrue(a1["webm"] and a1["review"]["anim"] == "PENDING")
        h = next(x for x in reversed(a1["history"]) if x["actor"] == "human")
        self.assertEqual((h["stage"], h["decision"], h["reason"]), ("video", "APPROVE", "allowed anyway: loop_seam"))
        self.assertEqual((g["allow"]["animation"]["allowed"], g["allow"]["animation"]["undo"]), ([4], [4]))
        # animating the same cell again (the edge changed: the animation is STALE) keeps the permission
        self.assertEqual(self.rig.req("POST", base + "/appearance", {"outline": 4, "erode": 0})[0], 200)
        g = self.rig.wait(gid, lambda x: x["stickers"][3]["anim_status"] == "STALE")
        self.assertEqual(self.rig.req("POST", base + "/animate", {"scope": "slice", "index": 4})[0], 202)
        g = self.rig.wait(gid, lambda x: x["stickers"][3]["anim_status"] in ("READY", "FAILED"))
        self.assertEqual((g["stickers"][3]["anim_status"], g["stickers"][3]["anim_override"]), ("READY", ["loop_seam"]))
        # taken back: blocked again
        self.assertEqual(self.allow(gid, index=4, allow=False)[0], 202)
        g = self.rig.wait(gid, lambda x: x["stickers"][3]["anim_status"] == "FAILED")
        self.assertEqual((g["stickers"][3]["anim_reason"], g["stickers"][3]["anim_override"]), ("loop_seam", []))
        self.assertEqual(g["allow"]["animation"]["can"], [4])

    # ---- the classification itself -------------------------------------------------------------------------------------------
    def test_the_classification_of_every_block(self):
        """The table in engine/verify.py: every BLOCK check of the still and animation stages is either overridable or technical, never both, never neither."""
        for kind, stages in (("still", ("still",)), ("animation", ("slot", "anim"))):
            blocks = {cid for stage in stages for cid, sev, _fn, _g in verify.CATALOGUE[stage] if sev == verify.BLOCK}
            over, tech = set(verify.OVERRIDABLE[kind]), set(verify.TECHNICAL[kind])
            self.assertEqual(over & tech, set(), kind)
            self.assertEqual(blocks - over - tech, set(), f"a {kind} BLOCK nobody classified")
            self.assertLessEqual(over | tech, blocks | {"holes"}, kind)                                       # `holes` is a WARN that turns into a BLOCK past a share
        for cid in ("static_file", "size_budget", "codec_vp9", "dimensions", "fps", "duration"):
            self.assertNotIn(cid, gates.OVERRIDABLE + gates.OVERRIDABLE_STILL, cid)             # Telegram's own limits stay final

    def test_a_crashed_check_and_a_missing_picture_are_never_allowed_by_the_rules(self):
        crash = {"name": "inside_cell", "ok": False, "severity": "BLOCK", "data": {"error": "boom"}}
        st = {"status": "FAILED", "metrics": {"bbox": [1, 1, 9, 9], "fg_px": 500}, "report": [crash]}
        self.assertIn("technical", gates.still_problem(st))
        st["report"] = [{"name": "inside_cell", "ok": False, "severity": "BLOCK", "data": {}}]
        self.assertIsNone(gates.still_problem(st))
        st["metrics"] = {"bbox": None, "fg_px": 0}
        self.assertIn("no picture", gates.still_problem(st))
        st["metrics"] = {"bbox": [1, 1, 9, 9], "fg_px": 500, "sheet_blocked": True}
        self.assertIn("Cut it anyway", gates.still_problem(st))
        # a mixed failure: one judgement and one technical block together is final
        st = {"status": "FAILED", "metrics": {"bbox": [1, 1, 9, 9], "fg_px": 500},
              "report": [{"name": "inside_cell", "ok": False, "severity": "BLOCK", "data": {}}, {"name": "static_file", "ok": False, "severity": "BLOCK", "data": {}}]}
        self.assertIn("static_file", gates.still_problem(st))
        # waiving inside the verifier: only what is waived, only a BLOCK, never a crash
        c = lambda cid, sev, ok=False, reason=None: verify.Check(cid, "still", sev, ok, reason=reason)
        checks = [c("a", verify.BLOCK), c("b", verify.BLOCK), c("c", verify.BLOCK, reason="verifier_error")]
        for cid, fn in (("a", lambda i, cfg: checks[0]), ("b", lambda i, cfg: checks[1]), ("c", lambda i, cfg: checks[2])):
            verify.CATALOGUE.setdefault("_t", []).append((cid, verify.BLOCK, fn, False))
        try:
            m = {}
            out = verify.run("_t", {"metrics": m, "waive": ["a", "c"]}, EngineConfig())
        finally:
            del verify.CATALOGUE["_t"]
        self.assertEqual([(x.id, x.severity) for x in out], [("a", verify.WARN), ("b", verify.BLOCK), ("c", verify.BLOCK)])
        self.assertEqual(m, {"waived": ["a"]})
        self.assertTrue(out[0].note.endswith("(allowed by you)"))


class TechnicalBlocksTests(unittest.TestCase):
    """Telegram's own limits are final: nothing here can be allowed, with the reason in the answer."""

    def test_a_still_over_the_size_limit_cannot_be_allowed(self):
        rig = Rig(EngineConfig(min_sheet_px=256, static_max_bytes=300))
        try:
            gid, g = rig.new("create an edgy one", outline=12)     # the stroke keeps every fake cell over 300 B (a flat cell PNGs to ~100 B without it)
            S = {s["index"]: s for s in g["stickers"]}
            self.assertEqual({S[i]["reason"] for i in range(4, 10)}, {"static_file"})
            al = g["allow"]["still"]
            self.assertEqual(al["can"], [], "every cell failed a Telegram limit (S1 and S3 also failed a judgement check, which does not make them allowable): nothing to offer")
            self.assertTrue(all("technical" in al["final"][str(i)] for i in (1, 3, 4)))
            s, j = rig.req("POST", f"/api/generations/{gid}/allow", {"kind": "still", "index": 4})
            self.assertEqual(s, 409)
            self.assertIn("static_file", j["error"])
            self.assertIn("cannot be allowed", j["error"])
            self.assertEqual(rig.req("POST", f"/api/generations/{gid}/allow", {"kind": "still", "all": True})[0], 409)
            s, j = rig.req("POST", f"/api/generations/{gid}/review", {"gate": "still", "decision": "APPROVE", "index": 4})
            self.assertEqual(s, 409)                                                           # and the gate still refuses a blocked sticker
        finally:
            rig.close()

    def test_an_animation_over_the_size_limit_cannot_be_allowed(self):
        rig = Rig(EngineConfig(min_sheet_px=256, video_max_bytes=3000))
        try:
            gid, g = rig.new("create an edgy one", variant=1)
            self.assertEqual(rig.req("POST", f"/api/generations/{gid}/animate", {"scope": "slice", "index": 4})[0], 202)
            g = rig.wait(gid, lambda x: x["stickers"][3]["anim_status"] in ("READY", "FAILED"))
            self.assertEqual((g["stickers"][3]["anim_status"], g["stickers"][3]["anim_reason"]), ("FAILED", "size_budget"))
            self.assertEqual(g["allow"]["animation"]["can"], [])
            s, j = rig.req("POST", f"/api/generations/{gid}/allow", {"index": 4})
            self.assertEqual(s, 409)
            self.assertIn("size_budget", j["error"])
            self.assertIn("cannot be allowed", j["error"])
            self.assertEqual(rig.req("POST", f"/api/generations/{gid}/allow", {"kind": "animation", "all": True})[0], 409)
        finally:
            rig.close()


class VideoSheetAnimationTests(Api):
    """The returned-video path of the golden path: a loop that does not close is a judgement call there too, next to inside_slot / cross_slot."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        golden_inputs(cls.tmp / "in")
        cls.srv, cls.c = serve(cls.tmp / "out", cls.tmp / "in", 0, cfg=EngineConfig(loop_fade_frames=0), block=False)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    def test_loop_seam_on_a_returned_video_can_be_allowed_and_taken_back_and_inside_slot_still_can(self):
        gid, g = self.new("blob")
        self.review(gid, "still", "APPROVE", "ready")
        aid, g = self.drive_video(gid, drift={1: (-10, 0), 2: (-70, 0)})
        S = {s["index"]: s for s in g["stickers"]}
        self.assertEqual((S[1]["anim_status"], S[1]["anim_reason"]), ("FAILED", "loop_seam"), S[1]["anim_metrics"])
        self.assertEqual((S[2]["anim_status"], S[2]["anim_reason"]), ("FAILED", "inside_slot"))
        self.assertEqual(S[3]["anim_status"], "READY")                                         # a steady wiggle that loops: nothing to allow
        self.assertEqual(g["allow"]["animation"]["can"], [1, 2])
        self.review(gid, "anim", "APPROVE", 1, expect=409)
        s_, j = self.req("POST", f"/api/generations/{gid}/allow", {"kind": "animation", "all": True})
        self.assertEqual((s_, j["indexes"]), (202, [1, 2]), j)
        g = self.wait(gid, lambda x: x["stickers"][0]["anim_status"] == "READY" and x["stickers"][1]["anim_status"] in ("READY", "FAILED"))
        self.assertEqual(g["stickers"][0]["anim_override"], ["loop_seam"])
        # S2 left its slot AND its loop does not close; Python stops at the first (before the encode), so the loop shows up only now: a second, separate click, never allowed unseen
        self.assertEqual((g["stickers"][1]["anim_status"], g["stickers"][1]["anim_reason"], g["stickers"][1]["anim_override"]), ("FAILED", "loop_seam", ["inside_slot"]))
        self.assertEqual(g["allow"]["animation"]["can"], [2])
        s_, j = self.req("POST", f"/api/generations/{gid}/allow", {"kind": "animation", "all": True})
        self.assertEqual((s_, j["indexes"]), (202, [2]), j)
        g = self.wait(gid, lambda x: x["stickers"][1]["anim_status"] == "READY")
        self.assertEqual([g["stickers"][i]["anim_override"] for i in (0, 1)], [["loop_seam"], ["inside_slot", "loop_seam"]])
        self.review(gid, "anim", "APPROVE", "ready")                                           # the gate approves them like any other animation
        self.assertEqual(self.req("GET", f"/api/generations/{gid}")[1]["stickers"][0]["review"]["anim"], "APPROVED")
        # an edge change re-cuts every animation from the stored video and keeps both permissions
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/appearance", {"outline": 4, "erode": 0, "reslice": True})[0], 200)
        g = self.wait(gid, lambda x: not any(t["anim_status"] in ("STALE", "PROCESSING") for t in x["stickers"]))
        self.assertEqual([(g["stickers"][i]["anim_status"], g["stickers"][i]["anim_override"]) for i in (0, 1)], [("READY", ["loop_seam"]), ("READY", ["inside_slot", "loop_seam"])])
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/allow", {"index": 1, "allow": False})[0], 202)
        g = self.wait(gid, lambda x: x["stickers"][0]["anim_status"] == "FAILED")
        self.assertEqual((g["stickers"][0]["anim_reason"], g["stickers"][0]["anim_override"], g["stickers"][1]["anim_status"]), ("loop_seam", [], "READY"))


class GatingTests(unittest.TestCase):
    """The route is a member route like the other gate decisions: a member allows on their own batch only (a stranger's is a 404), exactly as before."""

    @classmethod
    def setUpClass(cls):
        import os
        cls.env = os.environ.get("STICKERLOOM_API_TOKEN")
        os.environ.pop("STICKERLOOM_API_TOKEN", None)
        cls.rig = Rig(EngineConfig(min_sheet_px=256))
        cls.alice, cls.ta = cls.rig.c.users.create("Alice")
        cls.bob, cls.tb = cls.rig.c.users.create("Bob")
        cls.A, cls.B = ({"Authorization": f"Bearer {t}"} for t in (cls.ta, cls.tb))

    @classmethod
    def tearDownClass(cls):
        import os
        cls.rig.close()
        if cls.env is not None:
            os.environ["STICKERLOOM_API_TOKEN"] = cls.env

    def test_the_route_is_a_member_route_and_a_batch_belongs_to_its_owner(self):
        gid, g = self.rig.new("create an edgy one", self.A)
        self.assertEqual(g["allow"]["still"]["can"], [1, 3])
        body = {"kind": "still", "index": 3}
        self.assertEqual(self.rig.req("POST", f"/api/generations/{gid}/allow", body, self.B)[0], 404)      # not Bob's: not even revealed to exist
        self.assertEqual(self.rig.req("POST", f"/api/generations/{gid}/allow", body)[0], 401)              # no credential at all
        s, j = self.rig.req("POST", f"/api/generations/{gid}/allow", body, self.A)
        self.assertEqual(s, 202, j)
        g = self.rig.wait(gid, lambda x: x["stickers"][2]["status"] == "READY", headers=self.A)
        self.assertEqual(g["stickers"][2]["still_override"], ["inside_cell"])
        self.assertEqual(self.rig.req("POST", f"/api/generations/{gid}/allow", {**body, "allow": False}, self.B)[0], 404)
        self.assertEqual(self.rig.req("POST", f"/api/generations/{gid}/allow", {**body, "allow": False}, PAGE)[0], 202)      # the owner (the Studio's own page) may too
        self.rig.wait(gid, lambda x: x["stickers"][2]["status"] == "FAILED", headers=self.A)


class ContractTests(unittest.TestCase):
    def test_the_spec_describes_kind_and_the_answer(self):
        from stickerloom.console import openapi
        op = openapi.build()["paths"]["/api/generations/{id}/allow"]["post"]
        body = op["requestBody"]["content"]["application/json"]["schema"]["properties"]
        self.assertEqual(body["kind"]["enum"], ["still", "animation", "video_sheet"])
        self.assertEqual(body["sheet"]["type"], "string")
        self.assertIn("indexes", body)
        self.assertIn("kind", op["responses"]["202"]["content"]["application/json"]["schema"]["properties"])


if __name__ == "__main__":
    unittest.main()
