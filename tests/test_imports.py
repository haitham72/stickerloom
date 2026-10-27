"""Imports use isolated output and fake provider responses; no paid calls."""
import io
import http.client
import os
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import cv2
from stickerloom.flow import imports as im, pipeline as pl
from stickerloom.generation import higgsfield as hf
from stickerloom.engine.config import EngineConfig
from tests import synth

TICKET = "b16dc424-22fe-4ad2-8242-e8a36f10209b"


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.c = SimpleNamespace(out=self.out, inp=self.out / "inputs", lock=threading.Lock(), cfg=EngineConfig(min_sheet_px=256), pace=0)
        self.user = {"id": "local"}
        self.data = cv2.imencode(".png", cv2.cvtColor(synth.make_sheet(), cv2.COLOR_RGB2BGR))[1].tobytes()

    def tearDown(self):
        with self.c.lock:
            pass
        self.tmp.cleanup()

    def wait(self):
        with self.c.lock:
            pass

    def test_task_lookup_failed_job_and_malformed_ledger(self):
        (self.out / "tasks").mkdir()
        (self.out / "tasks" / "001.json").write_text(json.dumps({"id": "001", "external_task_id": TICKET}), encoding="utf-8")
        self.assertEqual(im.known(self.out, name=f"hf_{TICKET}.mp4")["task"], "001")
        (self.out / "tasks" / "001.json").unlink()
        (self.out / "jobs").mkdir()
        (self.out / "jobs" / "J001.json").write_text(json.dumps({"id": "J001", "external_task_id": TICKET, "status": "FAILED"}), encoding="utf-8")
        (self.out / "imports.jsonl").write_text("null\n[]\nmalformed\n", encoding="utf-8")
        self.assertIsNone(im.known(self.out, job_id=TICKET))

    def test_import_grid_auto_and_manual_override(self):
        rgb = synth.make_sheet()[:400, :400].copy()
        rgb[:] = (0, 255, 0)
        for y in (100, 300):
            for x in (100, 300):
                cv2.circle(rgb, (x, y), 50, (255, 210, 0), -1)
        data = cv2.imencode('.png', cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))[1].tobytes()
        for grid, expected in ((None, [2, 2]), ('3x3', [3, 3])):
            _, got = im.import_file(self.c, self.user, 'two.png', data, grid=grid, again=True)
            self.wait()
            result = pl.read_result(self.out, got['id'])
            self.assertEqual(result['grid'], expected)
            self.assertEqual(len(result['stickers']), expected[0] * expected[1])

    def test_batch_manual_grid_creates_new_batch_and_preserves_original(self):
        _, original = im.import_file(self.c, self.user, 'sheet.png', self.data)
        self.wait()
        _, got = im.import_file(self.c, self.user, 'sheet.png', self.data,
                                batch=f"G{original['id']:03d}", grid='2x2', again=True)
        self.wait()
        self.assertNotEqual(got['id'], original['id'])
        self.assertEqual(pl.read_result(self.out, original['id'])['grid'], [3, 3])
        self.assertEqual(pl.read_result(self.out, got['id'])['grid'], [2, 2])

    def test_filenames_and_ledger_ids_do_not_collide(self):
        with patch.object(im.time, "strftime", return_value="fixed"):
            a = im.save(self.out, "same.png", b"first")
            b = im.save(self.out, "same.png", b"second")
        self.assertNotEqual(a, b)
        self.assertEqual(a.read_bytes(), b"first")
        with ThreadPoolExecutor(max_workers=2) as ex:
            rows = list(ex.map(lambda n: im.record(self.out, "same.png", str(n).encode(), None, "local"), range(2)))
        self.assertEqual(len({r["id"] for r in rows}), 2)
        self.assertEqual(len(list(im._lines(self.out / "imports.jsonl"))), 2)

    def test_identical_uploads_allocate_one_batch_and_renaming_deduplicates(self):
        with patch.object(pl, "start", wraps=pl.start) as start:
            with ThreadPoolExecutor(max_workers=2) as ex:
                results = list(ex.map(lambda _: im.import_file(self.c, self.user, "sheet.png", self.data), range(2)))
            self.wait()
            self.assertEqual(sorted(code for code, _ in results), [200, 202])
            self.assertEqual(start.call_count, 1)
        code, hit = im.import_file(self.c, self.user, "renamed.png", self.data)
        self.assertEqual((code, hit["generation"], hit["status"]), (200, "G001", "READY"))
        self.assertEqual(pl.read_result(self.out, 1)["owner"], "local")

    def test_failed_processing_retries_the_same_batch(self):
        with patch.object(pl, "run_stills", side_effect=RuntimeError("interrupted")), patch.object(pl, "start", wraps=pl.start) as start:
            _, first = im.import_file(self.c, self.user, "sheet.png", self.data)
            self.wait()
            self.assertEqual(start.call_count, 1)
        self.assertTrue(im.known(self.out, data=self.data)["recoverable"])
        with patch.object(pl, "start", wraps=pl.start) as start:
            _, again = im.import_file(self.c, self.user, "sheet.png", self.data, retry=True)
            self.wait()
            self.assertEqual(start.call_count, 0)
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(len(pl.list_ids(self.out)), 1)

    def test_invalid_media_and_destination_fail_before_mutation(self):
        with self.assertRaises(im.ImportError):
            im.import_file(self.c, self.user, "bad.png", b"not an image")
        with self.assertRaises(im.ImportError):
            im.import_file(self.c, self.user, "bad.mp4", b"video", generation="bad")
        self.assertEqual(pl.list_ids(self.out), [])
        self.assertFalse((self.out / "imports.jsonl").exists())
        self.assertEqual(im.generation_id("G001"), 1)

    def test_video_gate_is_checked_before_downloading(self):
        with patch.object(hf, "_json", return_value={"status": "completed", "result_url": "https://example.invalid/video.mp4"}), patch.object(hf, "download") as download:
            with self.assertRaises((im.ImportError, pl.PipelineError)):
                im.import_job(self.c, self.user, TICKET, generation="G001")
            download.assert_not_called()

    def test_a_video_alone_becomes_its_own_batch(self):
        """Library > + > Import pack: no destination, so the first frame is the sheet and the video animates it."""
        vid = self.out / "src.mp4"
        synth.make_video(vid)
        code, got = im.import_file(self.c, self.user, "emojis.mp4", vid.read_bytes())
        self.wait()
        self.assertEqual((code, got["kind"]), (202, "video"))
        self.assertNotIn("sheet", got)
        res = pl.read_result(self.out, got["id"])
        self.assertTrue(res["source"]["sheet_path"].endswith("-frame0.png"))
        self.assertTrue(res["source"]["has_video"])
        self.assertTrue(any(s["anim_status"] in ("READY", "FAILED") for s in res["stickers"]))
        self.assertEqual(im.known(self.out, data=vid.read_bytes())["status"], "READY")

    def test_import_inside_a_batch_picture_is_the_next_generation_video_matches_its_sheet(self):
        """Import inside a batch: a picture is the batch's next generation (its plan and naming, parent + regen_of); a video attaches to the main
        generation, and its first frame decides which sheet it came from (image sheet -> the batch's 3x3 video; video sheet -> the next animation)."""
        import numpy as np
        from PIL import Image
        from stickerloom.flow import gates, groups
        im.import_file(self.c, self.user, "sheet.png", self.data)
        self.wait()
        other = cv2.imencode(".png", cv2.cvtColor(synth.make_sheet(seed=1), cv2.COLOR_RGB2BGR))[1].tobytes()
        code, got = im.import_file(self.c, self.user, "mine.png", other, batch="G001")
        self.wait()
        self.assertEqual((code, got["kind"], got["batch"], got["generation"]), (202, "sheet", "G001", "G002"))
        two, one = pl.read_result(self.out, 2), pl.read_result(self.out, 1)
        self.assertEqual((two["regen_of"], two["parent"], two["task_slug"]), ("G001", 1, one["task_slug"]))        # the batch's own naming
        self.assertEqual([s["key"] for s in two["stickers"]], [s["key"] for s in one["stickers"]])               # the batch's own cells and tags
        self.assertEqual([m["id"] for m in groups.family(self.out, 1)["members"]], [1, 2])
        self.assertEqual(im.import_file(self.c, self.user, "again.png", other, batch="G001")[1]["duplicate"], True)   # never twice by accident
        code, got = im.import_file(self.c, self.user, "again.png", other, batch="G001", again=True)                     # ... but on purpose (2026-10-15)
        self.wait()
        self.assertEqual((code, got["kind"], got["generation"]), (202, "sheet", "G003"))
        # a video drawn from the image sheet: the main generation (G002, the newest) gets it as its 3x3 video
        vid = self.out / "src.mp4"
        synth.make_video(vid)
        code, got = im.import_file(self.c, self.user, "anim.mp4", vid.read_bytes(), batch="G001")
        self.wait()
        self.assertEqual((code, got["kind"], got["generation"], got["match"]["sheet"]), (202, "video", "G003", "image"))
        self.assertIn("image sheet", got["message"])
        two = pl.read_result(self.out, 3)
        self.assertTrue(two["source"]["has_video"])
        self.assertTrue(any(s["anim_status"] == "READY" for s in two["stickers"]))
        # a video drawn from G001's video sheet: G001 is made the main, the video is attached to A1 and cut
        groups.pick(self.out, 1)
        gates.quick_sheet(self.out, 1, self.c.cfg)
        d = pl.gen_dir(self.out, 1)
        v = pl.read_result(self.out, 1)["video_sheets"][0]
        lay = self.out / "lay.mp4"
        synth.make_layout_video(lay, np.array(Image.open(d / v["file"]).convert("RGB")), json.loads((d / v["layout"]).read_text()), size=600, frames=30)
        code, got = im.import_file(self.c, self.user, "lay.mp4", lay.read_bytes(), batch="G003")
        self.wait()
        self.assertEqual((code, got["generation"], got["match"]["sheet"], got["sheet"]), (202, "G001", "A1", "A1"), got)
        self.assertLess(got["match"]["distances"]["A1"], got["match"]["distances"]["image"])
        one = pl.read_result(self.out, 1)
        self.assertEqual(one["video_sheets"][0]["status"], "SLICED")
        self.assertTrue(any(s["anim_status"] == "READY" for s in one["stickers"]))

    def test_provider_errors_and_response_validation(self):
        with patch.object(hf, "_json", return_value={"status": "completed", "result_url": "https://example.invalid/sheet.png"}), patch.object(hf, "download", side_effect=hf.HiggsError("download failed")):
            with self.assertRaises(im.ImportError) as got:
                im.import_job(self.c, self.user, TICKET)
            self.assertEqual(got.exception.code, 502)
        with patch.object(hf, "available", return_value=True), patch.object(hf, "_json", return_value={"jobs": []}):
            with self.assertRaises(im.ImportError) as got:
                im.history(self.c)
            self.assertEqual(got.exception.code, 502)

    def test_bounded_download_uses_project_tls_context(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {}
        response.read.return_value = b"x" * 9
        with patch("urllib.request.urlopen", return_value=response) as urlopen, patch("stickerloom.services.telegram._ssl_context", return_value=None):
            with self.assertRaises(hf.HiggsError):
                hf.download("https://example.invalid/file", self.out / "download.png", max_bytes=8)
            self.assertEqual(response.read.call_args.args, (9,))
            self.assertIn("context", urlopen.call_args.kwargs)
        self.assertFalse((self.out / "download.png").exists())

    def test_video_retry_resumes_the_saved_sheet_without_attaching_twice(self):
        from stickerloom.flow import gates
        res = {"error": None, "video_sheets": [{"id": "A1", "status": "APPROVED"}]}
        def attach(*args, **kw):
            res["video_sheets"][0]["status"] = "VIDEO_RETURNED"
        with patch.object(pl, "read_result", return_value=res), patch("stickerloom.engine.ffmpeg.probe", return_value={"codec": "vp9", "width": 512, "height": 512}), patch.object(gates, "attach_video", side_effect=attach) as put:
            with patch.object(gates, "slice_video", side_effect=RuntimeError("interrupted")):
                _, first = im.import_file(self.c, self.user, "video.mp4", b"stored video", generation="G001")
                self.wait()
            with patch.object(gates, "slice_video") as cut:
                _, again = im.import_file(self.c, self.user, "video.mp4", b"stored video", retry=True)
                self.wait()
                self.assertEqual(cut.call_count, 1)
            self.assertEqual(put.call_count, 1)
            self.assertEqual((first["id"], first["sheet"]), (again["id"], again["sheet"]))

    def test_video_retry_recovers_interruption_during_attachment_and_after_slicing(self):
        from stickerloom.flow import gates
        res = {"error": None, "video_sheets": [{"id": "A1", "status": "APPROVED"}]}
        def attach(*args, **kw):
            res["video_sheets"][0]["status"] = "VIDEO_RETURNED"
            raise RuntimeError("stopped before import checkpoint")
        with patch.object(pl, "read_result", return_value=res), patch("stickerloom.engine.ffmpeg.probe", return_value={"codec": "vp9", "width": 512, "height": 512}), patch.object(gates, "attach_video", side_effect=attach) as put:
            with self.assertRaises(RuntimeError):
                im.import_file(self.c, self.user, "video.mp4", b"stored video", generation="G001")
            self.assertEqual(im.known(self.out, data=b"stored video")["generation"], "G001")
            with patch.object(gates, "slice_video") as cut:
                code, _ = im.import_file(self.c, self.user, "video.mp4", b"stored video", retry=True)
                self.wait()
                self.assertEqual((code, put.call_count, cut.call_count), (202, 1, 1))
                row = next(im._lines(self.out / "imports.jsonl"))
                im._update(self.out, row["id"], status="PROCESSING")
                res["video_sheets"][0]["status"] = "SLICED"
                code, hit = im.import_file(self.c, self.user, "video.mp4", b"stored video", retry=True)
                self.assertEqual((code, hit["status"], hit["recoverable"]), (200, "READY", False))
                self.assertEqual(cut.call_count, 1)

    def test_sheet_recovery_skips_legacy_folders_without_results(self):
        (self.out / "G001").mkdir()
        f = im.save(self.out, "sheet.png", self.data)
        row = im.record(self.out, "sheet.png", self.data, None, "local")
        im._update(self.out, row["id"], status="PREPARING", file=str(f))
        from stickerloom.flow import sources
        gid = pl.start("imported sheet", self.out, self.c.inp, pick=sources.Pick(subject="import", subject_id="import", variant=1, n_variants=1, sheet=f, video=None))
        with patch.object(pl, "start", wraps=pl.start) as start:
            code, recovered = im.import_file(self.c, self.user, "sheet.png", self.data, retry=True)
            self.wait()
            self.assertEqual((code, recovered["id"]), (202, gid))
            start.assert_not_called()


class ImportRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from stickerloom.console.server import serve
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = patch.dict(os.environ, {"STICKERLOOM_API_TOKEN": "import-test-owner"})
        cls.hf = patch.object(hf, "available", return_value=False)
        cls.env.start(); cls.hf.start()
        cls.srv, cls.c = serve(Path(cls.tmp.name) / "out", Path(cls.tmp.name) / "in", 0, cfg=EngineConfig(min_sheet_px=256), block=False, stdlib=False)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        _, cls.member = cls.c.users.create("Member")

    @classmethod
    def tearDownClass(cls):
        cls.c.wait_jobs()
        cls.srv.shutdown(); cls.c.release_writer()
        cls.hf.stop(); cls.env.stop(); cls.tmp.cleanup()

    def req(self, path, body=b"", token="import-test-owner"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        conn.request("GET" if "history" in path else "POST", path, body, {"Authorization": "Bearer " + token, "Content-Type": "application/octet-stream"})
        r = conn.getresponse()
        result = r.status, json.loads(r.read())
        conn.close()
        return result

    def test_native_auth_validation_and_versioned_upload(self):
        self.assertEqual(self.req("/api/import?name=a.png", b"bad", self.member)[0], 403)
        self.assertEqual(self.req("/api/higgsfield/history?size=bad")[0], 400)
        self.assertEqual(self.req("/api/higgsfield/import", b'{"id":"bad"}')[0], 400)
        self.assertEqual(self.req("/api/import?name=bad.png", b"bad")[0], 400)
        data = cv2.imencode(".png", cv2.cvtColor(synth.make_sheet(), cv2.COLOR_RGB2BGR))[1].tobytes()
        code, created = self.req("/api/v1/import?name=own.png", data)
        self.assertEqual(code, 202)
        self.c.wait_jobs()
        code, hit = self.req("/api/import?name=renamed.png", data)
        self.assertEqual((code, hit["generation"]), (200, f"G{created['id']:03d}"))
