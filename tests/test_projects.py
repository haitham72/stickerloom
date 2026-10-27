"""The project map (flow/projects.py): Project > Batch > Sheet > Video as ONE read, on hand-made result files (nothing is generated),
and its native route GET /api/generations/{id}/map through the real server."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from stickerloom.flow import projects
from tests.test_groups import batch


class ProjectMapTests(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        batch(self.out, 104, "superhero paris", owner="U1")
        batch(self.out, 105, "superhero paris", parent=104, owner="U1", prompt_x=1)              # an edit of 104: the same batch, a second sheet
        batch(self.out, 107, "superhero paris social-v1", created=1200.0, owner="U1",             # the next batch of the same request: the same project
              video_sheets=[{"id": "A1", "slots": [1], "file": "video_sheet/A1/sheet.png", "video": "video_sheet/A1/video.mp4", "status": "SLICED"},
                            {"id": "A2", "slots": [1], "file": "video_sheet/A2/sheet.png", "status": "REJECTED"}])
        batch(self.out, 120, "a cat", owner="U2")                                                # someone else's idea: another project
        (self.out / "jobs").mkdir()
        (self.out / "jobs" / "J001.json").write_text(json.dumps({"id": "J001", "kind": "video", "generation": "G105", "status": "CLAIMED", "created_at": 9e12}), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    def test_one_tree_project_batch_sheet_video(self):
        m = projects.project_map(self.out, 105)
        self.assertEqual((m["project"]["id"], m["current"]), ("G104", "G105"))
        self.assertEqual([b["root"] for b in m["batches"]], ["G104", "G107"], "the batches of one request are one project")
        first = m["batches"][0]
        self.assertEqual([(s["id"], s["label"]) for s in first["sheets"]], [("G104", "First try"), ("G105", "Edited")])
        self.assertEqual(first["sheets"][1]["jobs"], [{"id": "J001", "kind": "video", "status": "CLAIMED"}], "a job in flight rides on its sheet")
        vids = m["batches"][1]["sheets"][0]["videos"]
        self.assertEqual([(v["id"], v["used"]) for v in vids], [("A1", True)], "a removed video is not in the map; the one in use is marked")
        self.assertEqual(vids[0]["thumb"], "/out/G107/video_sheet/A1/sheet.png")

    def test_a_viewer_sees_only_their_batches(self):
        m = projects.project_map(self.out, 104, viewer=lambda g: g != 107)
        self.assertEqual([b["root"] for b in m["batches"]], ["G104"])
        self.assertEqual(projects.project_map(self.out, 120)["batches"][0]["title"], "A cat")

    def test_an_unknown_batch_is_an_error(self):
        with self.assertRaises(Exception):
            projects.project_map(self.out, 999)


class ProjectMapRouteTests(unittest.TestCase):
    def test_the_route_answers_the_tree_and_404s(self):
        import http.client
        import threading
        from stickerloom.console.server import serve
        from stickerloom.engine.config import EngineConfig
        out = Path(tempfile.mkdtemp())
        try:
            batch(out, 104, "superhero paris")
            srv, _ = serve(out, out / "in", 0, cfg=EngineConfig(), block=False)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            port = srv.server_address[1]

            def get(path):
                h = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
                h.request("GET", path)
                r = h.getresponse()
                body = json.loads(r.read() or b"{}")
                h.close()
                return r.status, body
            s, j = get("/api/generations/G104/map")
            self.assertEqual((s, j["project"]["id"], j["batches"][0]["sheets"][0]["status"]), (200, "G104", "ready"))
            self.assertEqual(get("/api/v1/generations/104/map")[0], 200)
            self.assertEqual(get("/api/generations/G999/map")[0], 404)
            srv.shutdown()
        finally:
            shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
