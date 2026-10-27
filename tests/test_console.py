import http.client
import json
import shutil
import tempfile
import threading
import time
import unittest
from stickerloom.runtime import names
from pathlib import Path

import cv2

from stickerloom.flow import pipeline as pl
from stickerloom.engine.config import EngineConfig
from stickerloom.console.server import serve
from tests import synth


def build_inputs(root: Path):
    """blob: variant 1 has a video, variant 2 has none (like a missing video take)."""
    d = root / "Images_gen" / "img-001-blob"; d.mkdir(parents=True)
    v = root / "videos_gen" / "vid-001-blob"; v.mkdir(parents=True)
    for k, seed in ((1, 0), (2, 7)):
        cv2.imwrite(str(d / f"img-001-blob ({k}).png"), cv2.cvtColor(synth.make_sheet(seed=seed), cv2.COLOR_RGB2BGR))
    synth.make_video(v / "vid-001-blob (1).mp4")


class ConsoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        build_inputs(cls.tmp / "in")
        cls.srv, cls.c = serve(cls.tmp / "out", cls.tmp / "in", 0, cfg=EngineConfig(min_sheet_px=256), block=False)   # the synthetic sheets are small
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, method, path, body=None):
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
        r = h.getresponse(); raw = r.read(); h.close()
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw

    def wait(self, gid, pred, timeout=90):
        end = time.time() + timeout
        while time.time() < end:
            s, j = self.req("GET", f"/api/generations/{gid}")
            if pred(j) and not j["busy"]:
                return j
            time.sleep(0.2)
        self.fail("timeout")

    def test_live_preview_source_and_ui_names(self):
        s, j = self.req("POST", "/api/generations", {"prompt": "create a blob for school", "variant": 1})
        gid = j["id"]; self.wait(gid, lambda x: x["stage"] == "video_sliced" or x["stickers"])
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h.request("GET", f"/src/{gid}/video", headers={"Range": "bytes=0-99"})
        r = h.getresponse(); body = r.read(); h.close()
        self.assertEqual(r.status, 206); self.assertEqual(len(body), 100)
        etag = r.getheader("ETag"); self.assertTrue(etag); self.assertEqual(r.getheader("Cache-Control"), "private, no-cache")
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)          # the browser asks again with its copy's tag: a 304, no body
        h.request("GET", f"/src/{gid}/video", headers={"If-None-Match": etag})
        r = h.getresponse(); body = r.read(); h.close()
        self.assertEqual(r.status, 304); self.assertEqual(body, b"")
        self.assertEqual(self.req("GET", f"/src/{gid}/clip/1")[0], 404)
        self.assertIn("health", self.req("GET", "/api/generations")[1])
        # inline onclick handlers resolve names on the element first: `animate` is Element.animate and silently broke the button once
        html = (Path(__file__).parents[1] / "stickerloom" / "console" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("onclick=\"animate(", html)

    def test_library_api_and_ui_files(self):
        import io as _io
        from PIL import Image
        for f in ("studio.css", "app.js", "packs.js", "editor.js", "animate.js", "chat.js", "prepare.js"):
            self.assertEqual(self.req("GET", f"/ui/{f}")[0], 200)
        from stickerloom.console.assets import ASSETS
        for name in ("app.js", "lifecycle.js", "studio-core.js", "reaction-preview-cache.js", "agent.css"):
            legacy = self.req("GET", f"/ui/{name}")
            canonical = self.req("GET", f"/ui/{ASSETS[name]}")
            self.assertEqual(legacy[0], 200)
            self.assertEqual(canonical, legacy, name)
        self.assertEqual(self.req("GET", "/ui/server.py")[0], 404)
        self.assertEqual(self.req("GET", "/ui/features/../../server.py")[0], 404)
        s, pk = self.req("POST", "/api/packs", {"name": "API Pack"}); self.assertEqual(s, 200)
        png = _io.BytesIO(); Image.new("RGBA", (512, 512), (0, 0, 0, 0)).save(png, "PNG")
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h.request("POST", f"/api/packs/{pk['id']}/render?name=x&emoji=%F0%9F%99%82", png.getvalue()); r = h.getresponse(); r.read(); h.close()
        self.assertEqual(r.status, 400)                                   # empty sticker refused
        self.assertEqual(self.req("POST", "/api/packs/nope", {"name": "z"})[0], 404)
        s, lib = self.req("GET", "/api/library"); self.assertTrue(any(p["id"] == pk["id"] for p in lib["packs"]))
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h.request("GET", "/lib/..%2F..%2Fpipeline.py"); r = h.getresponse(); r.read(); h.close()
        self.assertIn(r.status, (400, 404))

    def test_lifecycle(self):
        s, j = self.req("POST", "/api/generations", {"prompt": "create a blob for school", "outline": 12})
        self.assertEqual(s, 202); gid = j["id"]
        g = self.wait(gid, lambda j: j["stage"] == "sliced")
        self.assertEqual(g["task_slug"], "blob_school")
        ready = [t for t in g["stickers"] if t["status"] == "READY"]
        # cell 5 blank. Cell 6 touches the equal-thirds line but has a background gutter beside it, so the measured
        # gutter cut keeps it whole (inside_cell on a really crossing subject is covered in test_engine).
        self.assertEqual(len(ready), 8)
        self.assertEqual(g["grid"], [3, 3]); self.assertEqual(g["source"]["grid"]["method"], "gutter")
        for t in ready:
            self.assertTrue(t["png"].startswith("slices/img-blob_school-"), t["png"])
            self.assertEqual(names.parse(t["png"].split("/")[1])["subject"], "blob_school")
            self.assertTrue((pl.out_path(self.c.out, g["generation_id"]) / t["png"]).exists())
        stages = [(e["stage"], e["status"]) for e in g["events"]]
        self.assertEqual(stages, [("requested", "done")] + [(x, y) for x in ("sheet_picked", "keyed", "sliced") for y in ("start", "done")])

        # animate one slice -> exactly one webm; repeat is a no-op
        s, j = self.req("POST", f"/api/generations/{gid}/animate", {"scope": "slice", "index": 1})
        self.assertEqual(s, 202)
        g = self.wait(gid, lambda j: j["stickers"][0]["anim_status"] in ("READY", "FAILED"))
        self.assertEqual(g["stickers"][0]["anim_status"], "READY", g["stickers"][0]["anim_metrics"])
        self.assertEqual(g["stickers"][0]["webm"], "slices/" + names.as_media(g["stickers"][0]["name"], "vid") + ".webm")
        self.assertEqual(len(list((pl.out_path(self.c.out, g["generation_id"]) / "slices").glob("*.webm"))), 1)
        s, j = self.req("POST", f"/api/generations/{gid}/animate", {"scope": "slice", "index": 1})
        self.assertEqual((s, j["noop"]), (200, True))
        s, j = self.req("POST", f"/api/generations/{gid}/animate", {"scope": "slice", "index": 5})
        self.assertEqual(s, 409)   # blank cell has no READY still

        # more -> variant 2 has no video; a third more says "that's all"
        s, j = self.req("POST", f"/api/generations/{gid}/more"); self.assertEqual(s, 202)
        g2 = self.wait(j["id"], lambda j: j["stage"] == "sliced")
        self.assertEqual(g2["source"]["variant"], 2)
        s, j2 = self.req("POST", f"/api/generations/{g2['number']}/animate", {"scope": "pack"})
        self.assertEqual((s, j2["error"]), (409, "No animation prepared for this variation."))
        s, j2 = self.req("POST", f"/api/generations/{g2['number']}/more")
        self.assertEqual(s, 409); self.assertIn("That's all 2 prepared variations of blob", j2["error"])
        # same input -> same event order (timestamps aside)
        seq = lambda g: [(e["stage"], e["status"]) for e in g["events"]]
        s, j = self.req("POST", "/api/generations", {"prompt": "create a blob for school"})
        g3 = self.wait(j["id"], lambda j: j["stage"] == "sliced")
        self.assertEqual(seq(g3), seq(g2))

    def test_video_project_api(self):
        import base64
        from tests.test_video_project import make_clip, overlay_png
        clip = self.tmp / "clip.mp4"; make_clip(clip)
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h.request("POST", "/api/projects?name=clip.mp4", clip.read_bytes(), {"Content-Type": "application/octet-stream"})
        r = h.getresponse(); pj = json.loads(r.read()); h.close()
        self.assertEqual(r.status, 200); pid = pj["id"]
        s, fr = self.req("GET", f"/proj/{pid}/f/0"); self.assertEqual((s, fr[:2]), (200, b"\xff\xd8"))   # a JPEG preview frame
        self.assertEqual(self.req("GET", f"/proj/{pid}/f/99999")[0], 404)
        self.assertEqual(self.req("GET", "/proj/../f/0")[0], 404)
        s, j = self.req("POST", f"/api/projects/{pid}", {"layers": [{"id": "t1", "type": "text", "timing": {"startMs": 0, "endMs": 500}, "payload": {"text": "hi"}}],
                                                        "video": {"trimStartMs": 0, "trimEndMs": 800, "fps": 10}})
        self.assertEqual((s, j["layers"][0]["timing"]["endMs"]), (200, 500))
        self.assertEqual(self.req("GET", f"/api/projects/{pid}")[1]["video"]["fps"], 10)
        self.assertIn(pid, [x["id"] for x in self.req("GET", "/api/projects")[1]["projects"]])
        body = {"format": "gif", "overlays": {"t1": "data:image/png;base64," + base64.b64encode(overlay_png()).decode()}}
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h.request("POST", f"/api/projects/{pid}/render", json.dumps(body), {"Content-Type": "application/json"})
        r = h.getresponse(); data = r.read(); h.close()
        self.assertEqual((r.status, data[:6]), (200, b"GIF89a")); self.assertEqual(json.loads(r.getheader("X-Render"))["frames"], 8)
        self.assertEqual(self.req("POST", f"/api/projects/{pid}/render", {"format": "webp", "overlays": {}, "save": {"pack_id": "x"}})[0], 400)   # only WebM goes into packs
        self.assertEqual(self.req("POST", "/api/projects?name=x.txt")[0], 400)
        self.assertEqual(self.req("POST", f"/api/projects/{pid}/delete")[0], 200); self.assertEqual(self.req("GET", f"/api/projects/{pid}")[0], 404)

    def test_the_simple_flow_animate_then_add(self):
        """type -> stickers -> drop one -> Animate -> Add: the gates are decided behind those clicks and each decision is on record."""
        s, j = self.req("POST", "/api/generations", {"prompt": "create a blob for school", "variant": 1, "outline": 0})
        gid = j["id"]
        g = self.wait(gid, lambda x: x["stage"] == "sliced")
        self.assertEqual((g["outline_px"], g["reviews"]["plan"]["note"]), (0, "approved by pressing Generate"))
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/drop", {"index": 5})[0], 409)       # a blocked sticker cannot be dropped
        s, j = self.req("POST", f"/api/generations/{gid}/drop", {"index": 3})
        self.assertEqual((s, j.get("gate"), j.get("error")), (200, "still", None))
        s, j = self.req("POST", f"/api/generations/{gid}/animate", {"scope": "slice", "index": 1})
        self.assertEqual(s, 202)
        g = self.wait(gid, lambda x: x["stickers"][0]["anim_status"] == "READY")
        pk = self.req("POST", "/api/packs", {"name": "Flow"})[1]
        s, r = self.req("POST", f"/api/generations/{gid}/add", {"pack_id": pk["id"]})
        self.assertEqual((s, r["kind"], r["added"], r["indices"]), (200, "animated", 1, [1]))          # only what was animated and kept
        s, r = self.req("POST", f"/api/generations/{gid}/add", {"pack_id": pk["id"]})
        self.assertEqual((s, r["added"], r["already"]), (200, 0, 1))                                   # pressing Add twice adds nothing twice
        g = self.req("GET", f"/api/generations/{gid}")[1]
        self.assertEqual(g["final"], [1])
        self.assertEqual([(h["actor"], h["decision"]) for h in g["stickers"][2]["history"] if h["stage"] == "still"], [("human", "REJECT")])   # S3: dropped, on record
        lib = self.req("GET", "/api/library")[1]
        pack = next(x for x in lib["packs"] if x["id"] == pk["id"])
        self.assertEqual([x["type"] for x in pack["stickers"]], ["animated"])
        # a drop after the pack was approved reopens it instead of failing
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/drop", {"index": 1, "dropped": True})[1]["gate"], "anim")
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/add", {"pack_id": pk["id"]})[0], 409)   # nothing left to add

    def test_add_without_a_video_adds_the_stills(self):
        s, j = self.req("POST", "/api/generations", {"prompt": "blob", "variant": 2, "outline": 12})
        gid = j["id"]
        self.wait(gid, lambda x: x["stage"] == "sliced")
        s, r = self.req("POST", f"/api/generations/{gid}/add", {"pack_name": "Stills"})
        self.assertEqual((s, r["kind"], r["added"]), (200, "static", 8))
        self.assertEqual(len(next(x for x in self.req("GET", "/api/library")[1]["packs"] if x["id"] == r["pack_id"])["stickers"]), 8)

    def _still_then_animated_add(self, mode):
        s, j = self.req("POST", "/api/generations", {"prompt": "create a blob for school", "variant": 1, "outline": 0})
        gid = j["id"]
        self.wait(gid, lambda x: x["stage"] == "sliced")
        pk = self.req("POST", "/api/packs", {"name": "Mixed " + mode})[1]
        s, r = self.req("POST", f"/api/generations/{gid}/add", {"pack_id": pk["id"]})
        self.assertEqual(r["kind"], "static"); stills = r["added"]
        self.req("POST", f"/api/generations/{gid}/animate", {"scope": "slice", "index": 1})
        self.wait(gid, lambda x: x["stickers"][0]["anim_status"] == "READY")
        s, r = self.req("POST", f"/api/generations/{gid}/add", {"pack_id": pk["id"], "mode": mode})
        self.assertEqual((s, r["kind"], r["added"]), (200, "animated", 1))
        pack = next(x for x in self.req("GET", "/api/library")[1]["packs"] if x["id"] == pk["id"])
        return r, pack, stills

    def test_add_animated_can_replace_the_still_in_its_place(self):
        r, pack, stills = self._still_then_animated_add("replace")
        self.assertEqual(r["replaced"], 1)
        self.assertEqual([x["type"] for x in pack["stickers"]].count("static"), stills - 1)
        first = pack["stickers"][0]
        self.assertEqual(first["type"], "animated")                                      # it took the still's position
        self.assertFalse(first["name"].startswith("img-"))                              # readable name ({subject} {action}) ...
        self.assertTrue(names.parse(first["file_name"]) and names.parse(first["file_name"])["media"] == "img")                             # ... and the generator's file name kept as metadata

    def test_add_animated_next_to_the_still(self):
        r, pack, stills = self._still_then_animated_add("add")
        self.assertEqual(r["replaced"], 0)
        self.assertEqual([x["type"] for x in pack["stickers"]].count("static"), stills)
        self.assertEqual(len(pack["stickers"]), stills + 1)

    def test_edit_a_still_in_place_then_names_and_bulk_delete(self):
        import base64, io
        from PIL import Image, ImageDraw
        s, j = self.req("POST", "/api/generations", {"prompt": "create a blob for school", "variant": 1, "outline": 0})
        gid = j["id"]
        g = self.wait(gid, lambda x: x["stage"] == "sliced")
        st = g["stickers"][0]
        f = pl.out_path(self.tmp / "out", g["generation_id"]) / st["png"]
        before = f.read_bytes()
        im = Image.open(io.BytesIO(before)).convert("RGBA"); ImageDraw.Draw(im).rectangle([200, 200, 300, 300], fill=(255, 0, 0, 255))
        buf = io.BytesIO(); im.save(buf, "PNG")
        s, r = self.req("POST", f"/api/generations/{gid}/edit", {"index": 1, "png": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()})
        self.assertEqual(s, 200, r)
        self.assertNotEqual(f.read_bytes(), before)                                                       # saved in place, same file name
        self.assertEqual((pl.out_path(self.tmp / "out", g["generation_id"]) / "source" / "orig" / "S1.png").read_bytes(), before)   # the original is kept
        g2 = self.req("GET", f"/api/generations/{gid}")[1]
        self.assertEqual((g2["stickers"][0]["png"], g2["stickers"][0]["edited"]), (st["png"], True))
        self.assertEqual(g2["stickers"][0]["history"][-1]["decision"], "EDIT")
        # P8: the edited slice is merged back into one sheet, same layout, so the same S# by position
        self.assertEqual((g2["source"]["sheet_fixed"], g2["source"]["sheet_fixed_cells"]), ("source/sheet_fixed.png", [1]))
        fixed = pl.out_path(self.tmp / "out", g["generation_id"]) / "source" / "sheet_fixed.png"
        raw = pl.out_path(self.tmp / "out", g["generation_id"]) / g2["source"]["sheet_copy"]
        self.assertEqual(Image.open(fixed).size, Image.open(raw).size, "the same sheet, not a new layout")
        self.assertEqual([x["index"] for x in g2["stickers"]], [x["index"] for x in g["stickers"]], "every sticker keeps its S#")
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/edit", {"index": 1, "png": "data:image/png;base64,AAAA"})[0], 409)   # not a valid sticker
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/appearance", {"outline": 6})[1]["rerendered"], len(g["stickers"]) - sum(x["status"] != "READY" for x in g["stickers"]) - 1)   # the edited one keeps its pixels
        # Add with the names the user typed in the wizard
        pk = self.req("POST", "/api/packs", {"name": "Named"})[1]
        s, r = self.req("POST", f"/api/generations/{gid}/add", {"pack_id": pk["id"], "names": {"1": {"name": "My own name", "emoji": "🎉"}}})
        self.assertEqual((s, r["kind"]), (200, "static"))
        pack = next(x for x in self.req("GET", "/api/library")[1]["packs"] if x["id"] == pk["id"])
        first = next(x for x in pack["stickers"] if x["source"]["index"] == 1)
        self.assertEqual((first["name"], first["emoji"]), ("My own name", "🎉"))
        self.assertTrue(names.parse(first["file_name"]) and names.parse(first["file_name"])["media"] == "img")                                                # the generator's name stays as metadata
        other = next(x for x in pack["stickers"] if x["source"]["index"] != 1)
        self.assertFalse(other["name"].startswith("img-"))                                                  # untouched ones get the readable default
        # bulk delete: the square markers on the library
        items = [{"pack_id": pk["id"], "id": x["id"]} for x in pack["stickers"][:3]]
        self.assertEqual(self.req("POST", "/api/stickers/delete", {"items": items})[1]["deleted"], 3)
        pack = next(x for x in self.req("GET", "/api/library")[1]["packs"] if x["id"] == pk["id"])
        self.assertEqual(len(pack["stickers"]), len(g["stickers"]) - 3 - sum(x["status"] != "READY" for x in g["stickers"]))

    def test_recheck_judges_animations_made_before_the_border_check(self):
        s, j = self.req("POST", "/api/generations", {"prompt": "create a blob for school", "variant": 1, "outline": 0})
        gid = j["id"]
        self.wait(gid, lambda x: x["stage"] == "sliced")
        self.req("POST", f"/api/generations/{gid}/animate", {"scope": "pack"})
        g = self.wait(gid, lambda x: all(t["anim_status"] in ("READY", "FAILED") for t in x["stickers"] if t["status"] == "READY"))
        ready = [t["index"] for t in g["stickers"] if t["anim_status"] == "READY"]
        had = {t["index"]: next((c["ok"] for c in t["anim_report"] if c["name"] == "inside_frame"), None) for t in g["stickers"] if t["anim_status"] == "READY"}
        self.assertTrue(ready and all(v is not None for v in had.values()))
        # make them look like an older server made them: no verdict, nothing blocked
        f = pl.out_path(self.tmp / "out", g["generation_id"]) / "result.json"
        r = json.loads(f.read_text(encoding="utf-8"))
        for t in r["stickers"]:
            if t["anim_status"] == "READY":
                t["anim_report"] = [c for c in t["anim_report"] if c["name"] != "inside_frame"]
                t["anim_metrics"]["warnings"] = [w for w in t["anim_metrics"].get("warnings", []) if w != "inside_frame"]
                t["review"]["anim"] = "PENDING"; t.pop("bounds_checked", None)
        f.write_text(json.dumps(r), encoding="utf-8")
        s, out = self.req("POST", f"/api/generations/{gid}/recheck")
        self.assertEqual(s, 200, out)
        self.assertEqual(out["checked"], len(ready))
        self.assertEqual(sorted(out["flagged"]), sorted(i for i, ok in had.items() if not ok))      # the same verdict a new animation gets
        g2 = self.req("GET", f"/api/generations/{gid}")[1]
        for t in g2["stickers"]:
            if t["index"] in ready:
                self.assertTrue(t["bounds_checked"])
                self.assertEqual(t["review"]["anim"], "BLOCKED" if t["index"] in out["flagged"] else "PENDING")
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/recheck")[1]["checked"], 0)          # once is enough

    def test_a_soft_blocked_animation_can_be_included_anyway_but_a_real_block_cannot(self):
        s, j = self.req("POST", "/api/generations", {"prompt": "create a blob for school", "variant": 1, "outline": 0})
        gid = j["id"]
        self.wait(gid, lambda x: x["stage"] == "sliced")
        self.req("POST", f"/api/generations/{gid}/animate", {"scope": "pack"})
        g = self.wait(gid, lambda x: all(t["anim_status"] in ("READY", "FAILED") for t in x["stickers"] if t["status"] == "READY"))
        a, b = [t["index"] for t in g["stickers"] if t["anim_status"] == "READY"][:2]
        f = pl.out_path(self.tmp / "out", g["generation_id"]) / "result.json"
        r = json.loads(f.read_text(encoding="utf-8"))
        for i, name, sev in ((a, "inside_frame", "WARN"), (b, "loop_seam", "BLOCK")):         # a: leaves its cell (a warning); b: a real failed check
            t = r["stickers"][i - 1]
            t["anim_report"] = [c for c in t["anim_report"] if c["name"] != name] + [{"name": name, "ok": False, "severity": sev, "detail": "forced for the test"}]
            t["review"]["anim"] = "BLOCKED"
        f.write_text(json.dumps(r), encoding="utf-8")
        s, out = self.req("POST", f"/api/generations/{gid}/drop", {"index": a, "dropped": False})
        self.assertEqual(s, 200, out)                                                         # the human includes it anyway
        t = self.req("GET", f"/api/generations/{gid}")[1]["stickers"][a - 1]
        self.assertEqual(t["review"]["anim"], "APPROVED")
        self.assertEqual((t["history"][-1]["decision"], t["history"][-1]["reason"]), ("APPROVE", "included anyway: inside_frame"))
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/drop", {"index": b, "dropped": False})[0], 409)      # Python's real block stays final
        self.assertEqual(self.req("GET", f"/api/generations/{gid}")[1]["stickers"][b - 1]["review"]["anim"], "BLOCKED")

    def test_a_server_running_older_code_than_the_disk_says_so(self):
        import os, time, tempfile
        from stickerloom.console.server import Console
        c = Console(Path(tempfile.mkdtemp()) / "out", self.tmp / "in")        # its own out/: the running server already holds the writer lock of self.tmp/out
        self.addCleanup(c.release_writer)
        src = Path(tempfile.mkdtemp())
        (src / "a.py").write_text("x = 1"); old = time.time() - 100
        os.utime(src / "a.py", (old, old))
        self.assertFalse(c.stale(src))                                   # nothing changed since this server started
        c._stale_checked = 0
        os.utime(src / "a.py", (time.time() + 5, time.time() + 5))       # a file is edited after the start
        self.assertTrue(c.stale(src))
        self.assertIn("stale", self.req("GET", "/api/generations")[1])   # the page asks for it with every poll

    def test_studio_edit_updates_the_image_and_the_animation_together(self):
        import base64, io
        import numpy as np
        from PIL import Image, ImageDraw
        s, j = self.req("POST", "/api/generations", {"prompt": "create a blob for school", "variant": 1, "outline": 0})
        gid = j["id"]
        self.wait(gid, lambda x: x["stage"] == "sliced")
        self.req("POST", f"/api/generations/{gid}/animate", {"scope": "slice", "index": 1})
        g = self.wait(gid, lambda x: x["stickers"][0]["anim_status"] == "READY")
        st = g["stickers"][0]
        pk = self.req("POST", "/api/packs", {"name": "Studio copies"})[1]["id"]
        self.req("POST", f"/api/packs/{pk}/stickers", {"from_generation": {"id": gid, "index": 1, "kind": "static"}})
        self.req("POST", f"/api/packs/{pk}/stickers", {"from_generation": {"id": gid, "index": 1, "kind": "animated"}})
        base = pl.out_path(self.tmp / "out", g["generation_id"])
        png0, webm0 = (base / st["png"]).read_bytes(), (base / st["webm"]).read_bytes()
        # a sticker without an animation cannot be edited as a video
        no_anim = next(t for t in g["stickers"] if t["status"] == "READY" and t["anim_status"] != "READY")
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/studio_edit", {"index": no_anim["index"], "action": "open"})[0], 409)
        s, o = self.req("POST", f"/api/generations/{gid}/studio_edit", {"index": 1, "action": "open"})
        self.assertEqual(s, 200, o)
        pid = o["project"]
        self.assertEqual(self.req("POST", f"/api/generations/{gid}/studio_edit", {"index": 1, "action": "open"})[1], {"project": pid, "reused": True})
        layer = {"id": "t1", "type": "text", "visible": True, "zIndex": 1, "transform": {"x": 256, "y": 430, "scale": 1, "rotation": 0}, "opacity": 1,
                 "payload": {"text": "HELLO"}, "timing": {"startMs": 200, "endMs": 900}}
        self.assertEqual(self.req("POST", f"/api/projects/{pid}", {"layers": [layer]})[0], 200)
        im = Image.new("RGBA", (512, 512), (0, 0, 0, 0)); ImageDraw.Draw(im).rectangle([20, 440, 200, 500], fill=(255, 0, 0, 255))
        b = io.BytesIO(); im.save(b, "PNG")
        s, r = self.req("POST", f"/api/generations/{gid}/studio_edit", {"index": 1, "action": "commit", "overlays": {"t1": "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()}})
        self.assertEqual(s, 200, r)
        self.assertEqual((r["layers"], r["pack_copies"]), (1, 2))
        self.assertNotEqual((base / st["png"]).read_bytes(), png0)                      # the image changed ...
        self.assertNotEqual((base / st["webm"]).read_bytes(), webm0)                    # ... and so did the animation, from the one edit
        red = np.array(Image.open(base / st["png"]).convert("RGBA"))[440:500, 20:200]
        self.assertTrue(((red[..., 0] > 200) & (red[..., 3] > 200)).mean() > 0.5)      # the layer is in the image (all layers, timing ignored)
        self.assertEqual(r["sheet_fixed"]["cells"], [1], "the image of the edit is merged back into the sheet as well (the chat sends that sheet as its picture)")
        self.assertEqual((base / "source" / "orig" / "S1.png").read_bytes(), png0)      # originals kept: a re-edit starts from them
        g2 = self.req("GET", f"/api/generations/{gid}")[1]["stickers"][0]
        self.assertEqual((g2["edited"], g2["edit"]["project"], g2["history"][-1]["decision"]), (True, pid, "EDIT"))
        lib = next(p for p in self.req("GET", "/api/library")[1]["packs"] if p["id"] == pk)
        for sk in lib["stickers"]:                                                      # the pack copies follow the Studio
            f = self.tmp / "out" / "library" / "files" / sk["file"]
            self.assertEqual(f.read_bytes(), (base / (st["png"] if sk["type"] == "static" else st["webm"])).read_bytes())

    def test_errors_and_traversal(self):
        self.assertEqual(self.req("POST", "/api/generations", {"prompt": "spaceship"})[0], 404)
        self.assertEqual(self.req("POST", "/api/generations", {"prompt": ""})[0], 400)
        self.assertEqual(self.req("GET", "/out/%2e%2e/%2e%2e/in/Images_gen")[0], 400)
        self.assertEqual(self.req("GET", "/")[0], 200)


if __name__ == "__main__":
    unittest.main()
