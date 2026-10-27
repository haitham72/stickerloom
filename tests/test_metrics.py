"""Quality and timing numbers from what is already on disk (flow/metrics.py): time to the first sticker, approval rates at the two gates, how often a batch is a redo."""
import http.client
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from stickerloom.flow import metrics
from stickerloom.runtime import cache as cachemod


def sticker(i, still="PENDING", anim="PENDING", status="READY", anim_status="NOT_REQUESTED"):
    return {"index": i, "key": f"k{i}", "status": status, "anim_status": anim_status, "review": {"still": still, "anim": anim}}


def write_batch(out: Path, gid: int, stickers, t0: float, cut_after: float | None, parent=None, regen_of=None, owner="local"):
    d = out / f"G{gid:03d}"
    d.mkdir(parents=True)
    res = {"id": gid, "generation_id": f"G{gid:03d}", "prompt": "cat", "stage": "sliced", "error": None, "grid": [3, 3], "stickers": stickers, "parent": parent,
           "regen_of": regen_of, "owner": owner, "source": {"subject": "cat", "variant": 1}}
    (d / "result.json").write_text(json.dumps(res), encoding="utf-8")
    ev = [{"ts": t0, "stage": "requested", "status": "done", "ms": 0, "detail": None}, {"ts": t0 + 1, "stage": "keyed", "status": "done", "ms": 5, "detail": None}]
    if cut_after is not None:
        ev += [{"ts": t0 + cut_after - 0.5, "stage": "sliced", "status": "start", "ms": 0, "detail": None},
               {"ts": t0 + cut_after, "stage": "sliced", "status": "done", "ms": 500, "detail": None}, {"ts": t0 + cut_after + 99, "stage": "sliced", "status": "done", "ms": 1, "detail": None}]
    (d / "events.jsonl").write_text("\n".join(json.dumps(e) for e in ev), encoding="utf-8")


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "out"
        # G1: 9 ready, 6 approved 2 rejected 1 pending; 4 animated: 3 approved 1 rejected; first sticker after 10 s
        write_batch(self.out, 1, [sticker(i, "APPROVED", "APPROVED", anim_status="READY") for i in range(1, 4)] + [sticker(4, "APPROVED", "REJECTED", anim_status="READY")]
                    + [sticker(i, "APPROVED") for i in (5, 6)] + [sticker(7, "REJECTED"), sticker(8, "REJECTED"), sticker(9)], 1000.0, 10.0)
        # G2: a redo of one sticker of G1 (1x1): 1 ready, approved; first sticker after 30 s
        write_batch(self.out, 2, [sticker(1, "APPROVED")], 2000.0, 30.0, parent="G001", regen_of="G001/S7")
        # G3: nothing cut yet (failed): no ready stickers, no time
        write_batch(self.out, 3, [sticker(1, status="FAILED")], 3000.0, None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_numbers(self):
        m = metrics.collect(self.out)
        self.assertEqual(m["batches"], 3)
        self.assertEqual(m["stickers"], {"ready": 10, "approved": 7, "rejected": 2, "undecided": 1, "animated": 4, "anim_approved": 3, "anim_rejected": 1})
        self.assertEqual(m["approval_rate"], {"still": round(7 / 9, 3), "animation": round(3 / 4, 3)})          # decided stickers only: pending ones count for neither side
        self.assertEqual(m["regeneration_rate"], {"redo_batches": 1, "batches": 3, "rate": round(1 / 3, 3)})
        t = m["time_to_first_sticker_s"]
        self.assertEqual((t["n"], t["median"], t["max"]), (2, 20.0, 30.0))                                        # G1 10 s, G2 30 s; the failed G3 has none
        self.assertEqual(m["failed_batches"], 1)

    def test_per_batch_lines_and_nothing_to_divide_by(self):
        rows = {r["id"]: r for r in metrics.collect(self.out)["per_batch"]}
        self.assertEqual((rows["G001"]["ready"], rows["G001"]["approved"], rows["G001"]["rejected"], rows["G001"]["first_sticker_s"]), (9, 6, 2, 10.0))
        self.assertIsNone(rows["G003"]["first_sticker_s"])
        empty = metrics.collect(self.tmp / "nowhere")
        self.assertEqual((empty["batches"], empty["approval_rate"], empty["time_to_first_sticker_s"]["n"]), (0, {"still": None, "animation": None}, 0))

    def test_an_unreadable_batch_is_skipped_not_fatal(self):
        (self.out / "G001" / "result.json").write_text("{ not json", encoding="utf-8")
        self.assertEqual(metrics.collect(self.out)["batches"], 2)

    def test_a_member_sees_only_their_own_batches(self):
        write_batch(self.out, 4, [sticker(1, "APPROVED")], 4000.0, 5.0, owner="U007")
        self.assertEqual(metrics.collect(self.out, owner="U007")["batches"], 1)
        self.assertEqual(metrics.collect(self.out)["batches"], 4)


class RouteTests(unittest.TestCase):
    def test_the_route_is_owner_only_and_the_cli_prints_it(self):
        from stickerloom.console.server import serve
        tmp = Path(tempfile.mkdtemp())
        (tmp / "in").mkdir()
        out = tmp / "out"
        write_batch(out, 1, [sticker(1, "APPROVED")], 100.0, 4.0)
        mem = cachemod.Cache(force_memory=True)
        with mock.patch.object(cachemod, "default", lambda: mem):
            srv, c = serve(out, tmp / "in", 0, block=False)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            member, tok = c.users.create("Mia")
            try:
                def get(headers):
                    h = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=15)
                    h.request("GET", "/api/metrics", headers=headers)
                    r = h.getresponse()
                    return r.status, json.loads(r.read())
                s, d = get({"Sec-Fetch-Site": "same-origin"})
                self.assertEqual((s, d["batches"], d["time_to_first_sticker_s"]["median"]), (200, 1, 4.0))
                self.assertEqual(get({"Authorization": f"Bearer {tok}"})[0], 403)                                # owner only: it is the studio's own numbers
            finally:
                srv.shutdown()
                c.release_writer()
                srv.server_close()
                shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
