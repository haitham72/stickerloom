"""The Studio's Download .zip (flow/batches.export_zip, GET /api/generations/{id}/export.zip): the accepted stickers only, the animation where it is
ready, renamed to the export contract, a manifest v1, 409 with nothing accepted, a stranger's batch is a 404. Isolated out/, no provider."""
import http.client
import io
import json
import shutil
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

from stickerloom.flow import batches
from stickerloom.media import export_names as xn


def make_batch(out: Path, n: int, owner: str = "local") -> None:
    d = out / f"G{n:03d}"
    (d / "slices").mkdir(parents=True)
    sts = []
    for i, (st, still, anim) in enumerate([("READY", "APPROVED", "READY"), ("READY", "APPROVED", None), ("READY", "REJECTED", None), ("BLOCKED", None, None)], 1):
        (d / "slices" / f"img-{i}.png").write_bytes(b"png" * 10)
        s = {"index": i, "key": f"cat_{i}", "name": f"cat {i}", "emoji": "🐱", "tags": ["cat"], "status": st, "png": f"slices/img-{i}.png", "webm": None,
             "anim_status": "NOT_REQUESTED", "review": {"still": still, "anim": "NONE"}}
        if anim:
            (d / "slices" / f"vid-{i}.webm").write_bytes(b"webm" * 10)
            s.update(webm=f"slices/vid-{i}.webm", anim_status="READY")
        sts.append(s)
    (d / "result.json").write_text(json.dumps({"generation_id": f"G{n:03d}", "number": n, "prompt": "a cat", "task_slug": "cat", "owner": owner, "grid": [2, 2],
                                               "stage": "sliced", "stickers": sts, "history": []}), encoding="utf-8")


class BatchZipTests(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    def test_only_accepted_stickers_go_in_animated_where_ready_with_a_manifest(self):
        make_batch(self.out, 1)
        data, stem = batches.export_zip(self.out, 1)
        z = zipfile.ZipFile(io.BytesIO(data))
        names = sorted(z.namelist())
        self.assertIn("manifest.json", names)
        files = [n for n in names if n != "manifest.json"]
        self.assertEqual(len(files), 2, "the rejected and the blocked stickers stay out")
        for n in files:
            p = xn.parse(n)
            self.assertIsNotNone(p, f"{n} follows the export contract")
            self.assertEqual((p["emoji"], p["slug"], p["tag"], p["gid"]), ("🐱", "cat", "cat", 1))
        by_cell = {xn.parse(n)["index"]: n for n in files}
        self.assertTrue(by_cell[1].endswith(".webm") and by_cell[2].endswith(".png"), "the animation where ready, else the still")
        m = json.loads(z.read("manifest.json"))
        self.assertEqual(m["schema_version"], 1)
        self.assertEqual([(a["source_cell"], a["media"]) for a in m["assets"]], [("S1", "video"), ("S2", "static")])
        self.assertEqual(sorted(a["filename"] for a in m["assets"]), sorted(files))
        self.assertTrue(all(len(a["sha256"]) == 64 for a in m["assets"]))
        self.assertEqual(stem, "g001-cat")

    def test_a_batch_with_nothing_accepted_says_so(self):
        make_batch(self.out, 2)
        r = json.loads((self.out / "G002" / "result.json").read_text(encoding="utf-8"))
        for s in r["stickers"]:
            s["review"]["still"] = "REJECTED"
        (self.out / "G002" / "result.json").write_text(json.dumps(r), encoding="utf-8")
        with self.assertRaises(ValueError):
            batches.export_zip(self.out, 2)


class BatchZipRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from stickerloom.console.server import serve
        cls.tmp = Path(tempfile.mkdtemp())
        (cls.tmp / "in").mkdir()
        cls.srv, cls.c = serve(cls.tmp / "out", cls.tmp / "in", 0, block=False, stdlib=False)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        make_batch(cls.tmp / "out", 3)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.c.release_writer()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, path):
        h = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=20)
        h.request("GET", path)
        r = h.getresponse()
        body = r.read()
        h.close()
        return r, body

    def test_the_owner_downloads_it_and_an_unknown_batch_is_a_404(self):
        r, body = self.get("/api/generations/3/export.zip")
        self.assertEqual((r.status, r.getheader("Content-Type")), (200, "application/zip"))
        self.assertIn('filename="g003-cat.zip"', r.getheader("Content-Disposition"))
        self.assertIn("manifest.json", zipfile.ZipFile(io.BytesIO(body)).namelist())
        self.assertEqual(self.get("/api/v1/generations/G003/export.zip")[0].status, 200)
        self.assertEqual(self.get("/api/generations/99/export.zip")[0].status, 404)


if __name__ == "__main__":
    unittest.main()
