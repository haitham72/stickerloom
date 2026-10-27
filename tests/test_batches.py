"""Remove batch: a batch G### moves to out/trash/batches/ and can be restored, nothing is deleted, its number is never given to a new
batch while it is in the trash, a batch with a job in flight is refused in words, and the three routes are owner-only JSON."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from stickerloom.flow import batches, pipeline as pl
from stickerloom.generation import jobs
from stickerloom.console.server import serve
from stickerloom.engine.config import EngineConfig
from stickerloom.runtime import atomic


def make(out: Path, gid: int) -> Path:
    d = out / f"G{gid:03d}"
    (d / "source").mkdir(parents=True)
    (d / "result.json").write_text(json.dumps({"generation_id": f"G{gid:03d}", "number": gid, "stickers": []}), encoding="utf-8")
    (d / "source" / "sheet.png").write_bytes(b"png")
    return d


class RemoveABatch(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(); self.out = Path(self.td.name)
        for i in (1, 2, 3):
            make(self.out, i)

    def tearDown(self):
        self.td.cleanup()

    def test_remove_moves_the_folder_and_restore_puts_it_back_untouched(self):
        meta = batches.remove(self.out, 3, by="human")
        self.assertEqual((meta["id"], meta["by"]), ("G003", "human"))
        self.assertFalse((self.out / "G003").exists())
        self.assertEqual(pl.list_ids(self.out), [1, 2])                                           # the Studio and the column stop listing it
        self.assertTrue((self.out / "trash" / "batches" / "G003" / "source" / "sheet.png").is_file(), "moved, not deleted")
        self.assertEqual([r["id"] for r in batches.list_removed(self.out)], ["G003"])
        batches.restore(self.out, 3)
        self.assertEqual(pl.list_ids(self.out), [1, 2, 3])
        self.assertEqual((self.out / "G003" / "source" / "sheet.png").read_bytes(), b"png")
        self.assertEqual(batches.list_removed(self.out), [])

    def test_a_removed_number_is_not_reused_by_the_next_batch(self):
        batches.remove(self.out, 3)
        self.assertEqual(pl.next_gid(self.out), 4)                                                 # 3 is in the trash: a new batch would collide with its Restore
        batches.restore(self.out, 3)
        self.assertEqual(pl.next_gid(self.out), 4)

    def test_a_batch_with_a_job_in_flight_is_refused_in_words(self):
        jobs.create(self.out, "video", generation="G002", request={})                              # REQUESTED = in flight
        with self.assertRaises(pl.PipelineError) as cm:
            batches.remove(self.out, 2)
        self.assertEqual(cm.exception.code, 409)
        self.assertIn("G002", str(cm.exception))
        self.assertTrue((self.out / "G002").is_dir(), "nothing moved")

    def test_unknown_numbers_are_404_and_restore_never_overwrites(self):
        with self.assertRaises(pl.PipelineError) as cm:
            batches.remove(self.out, 9)
        self.assertEqual(cm.exception.code, 404)
        with self.assertRaises(pl.PipelineError) as cm:
            batches.restore(self.out, 9)
        self.assertEqual(cm.exception.code, 404)
        batches.remove(self.out, 3)
        make(self.out, 3)                                                                            # something took the place meanwhile
        with self.assertRaises(pl.PipelineError) as cm:
            batches.restore(self.out, 3)
        self.assertEqual(cm.exception.code, 409)
        self.assertTrue((self.out / "trash" / "batches" / "G003").is_dir(), "the removed one is still safe")


class RemoveRoutes(unittest.TestCase):
    """The three owner-only routes over the real server: remove, the trash listing, restore. Nothing reaches a provider."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory(); self.tmp = Path(self.td.name)
        self.out = self.tmp / "out"; self.out.mkdir()
        for i in (1, 2):
            make(self.out, i)
        self.srv, self.c = serve(self.out, self.tmp / "in", 0, cfg=EngineConfig(), block=False)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.c.wait_jobs(30)
        self.srv.shutdown()
        self.td.cleanup()

    def req(self, method, path, body=None):
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
        r = h.getresponse(); data = r.read(); h.close()
        return r.status, json.loads(data)

    def test_remove_list_restore_round_trip_and_the_errors_are_in_words(self):
        s, j = self.req("POST", "/api/generations/2/remove", {})
        self.assertEqual((s, j["id"]), (200, "G002"))
        self.assertEqual(self.req("GET", "/api/generations/2")[0], 404, "the batch is gone from the Studio's reads")
        s, j = self.req("GET", "/api/generations/removed")
        self.assertEqual((s, [b["id"] for b in j["batches"]]), (200, ["G002"]))
        self.assertEqual(self.req("POST", "/api/generations/2/remove", {})[0], 404, "removing it twice is a plain 404, not a crash")
        s, j = self.req("POST", "/api/generations/2/restore", {})
        self.assertEqual((s, j["restored"]), (200, True))
        self.assertEqual(self.req("GET", "/api/generations/removed")[1]["batches"], [])
        s, j = self.req("POST", "/api/generations/2/restore", {})
        self.assertEqual(s, 404)
        self.assertIn("not in the trash", j["error"])


if __name__ == "__main__":
    unittest.main()
