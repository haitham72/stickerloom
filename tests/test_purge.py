"""Cleaning the trash (HANDOFF.md section 0, "Cleaning the trash: a purge for batches and packs"): Delete pack is SOFT, GET /api/trash shows exactly what a purge would remove, and the purge
of a removed batch or a deleted pack is a real, complete, recorded, idempotent delete (flow/purge.py, store/purge_rows.py, media/library.py, flow/batches.py).

Nothing here reaches a real database, provider or model: the database half is checked with a recording fake connection, the rest runs on temp directories and the real server on a temp out/.
Written 2026-10-03 and NOT RUN when it was written: run `stickerloom test area flow/purge.py` first, expect to fix small things."""
import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from stickerloom.console.server import serve
from stickerloom.engine.config import EngineConfig
from stickerloom.flow import batches, pipeline as pl, purge
from stickerloom.generation import jobs
from stickerloom.media.library import Library, LibraryError
from stickerloom.store import purge_rows

PNG = b"\x89PNG\r\n\x1a\n"


def make_batch(out: Path, gid: int, stickers: int = 2) -> Path:
    d = out / f"G{gid:03d}"
    (d / "source").mkdir(parents=True)
    (d / "slices").mkdir()
    (d / "result.json").write_text(json.dumps({"generation_id": f"G{gid:03d}", "number": gid, "source": {"subject": f"subject {gid}"}, "stickers": [{"index": i + 1} for i in range(stickers)]}), encoding="utf-8")
    (d / "source" / "sheet.png").write_bytes(PNG + b"s" * 100)
    for i in range(stickers):
        (d / "slices" / f"S{i + 1}.png").write_bytes(PNG + b"x" * (10 + i))
    return d


def ledger(out: Path) -> list[dict]:
    f = pl.purge_ledger(out)
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()] if f.is_file() else []


def wait_done(out: Path, view: dict) -> dict:
    """The view of a purge once its thread is finished (the purge normally answers within the request; a slow one is polled)."""
    if view["status"] == "running":
        t = purge.running(out)
        if t:
            t.event.wait(10)
        return purge.status(out, view["id"])
    return view


def referenced(lib: Library) -> set[str]:
    db = lib._load()
    return {s["file"] for p in db["packs"] + (db.get("trash") or {}).get("packs", []) for s in p["stickers"]}


def on_disk(lib: Library) -> set[str]:
    return {f.relative_to(lib.files).as_posix() for f in lib.files.rglob("*") if f.is_file()}


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.out = Path(self.td.name) / "out"
        self.out.mkdir()
        self.lib = Library(self.out)
        self._env = mock.patch.dict(os.environ, {"STICKERLOOM_DB_WRITE": "0"})          # no database, whatever the machine has
        self._env.start()
        purge._TASKS.clear()

    def tearDown(self):
        self._env.stop()
        purge._TASKS.clear()
        self.td.cleanup()

    def pack_with(self, name: str, n: int = 2, generation: str | None = None) -> tuple[str, list[dict]]:
        pid = self.lib.create_pack(name)["id"]
        rows = [self.lib.add_bytes(pid, PNG + name.encode() + bytes([i]) * 20, "png", f"{name} {i}", "static", "🙂",
                                   source={"generation": generation, "index": i + 1} if generation else None) for i in range(n)]
        return pid, rows

    def remove_batch(self, gid: int, **kw) -> Path:
        d = make_batch(self.out, gid, **kw)
        batches.remove(self.out, gid)
        return d


class DeletePackIsSoft(Base):
    """The discrepancy HANDOFF flagged: the docs said Delete pack was soft, the code unlinked the files. It is soft now, and Restore brings it back untouched."""

    def test_delete_pack_moves_the_record_to_the_trash_and_touches_no_file(self):
        pid, rows = self.pack_with("Cats", 3)
        before = on_disk(self.lib)
        r = self.lib.delete_pack(pid, by="U1")
        self.assertEqual((r["ok"], r["trashed"], r["stickers"]), (True, True, 3))
        self.assertEqual(self.lib.snapshot()["packs"], [], "it is gone from the library")
        self.assertEqual(on_disk(self.lib), before, "every file is exactly where it was")
        t = self.lib.trashed_packs()
        self.assertEqual((len(t), t[0]["id"], t[0]["deleted_by"]), (1, pid, "U1"))
        self.assertTrue(t[0]["deleted"])

    def test_restore_puts_the_same_pack_back_with_its_cover_and_order(self):
        pid, rows = self.pack_with("Cats", 3)
        before = self.lib.snapshot()["packs"][0]
        self.lib.delete_pack(pid)
        back = self.lib.restore_pack(pid)
        self.assertEqual((back["restored"], back["id"]), (True, pid))
        after = self.lib.snapshot()["packs"][0]
        self.assertEqual([s["id"] for s in after["stickers"]], [s["id"] for s in before["stickers"]])
        self.assertEqual((after["cover"], after["name"]), (before["cover"], before["name"]))
        self.assertNotIn("deleted", after)
        self.assertEqual(self.lib.trashed_packs(), [])
        with self.assertRaises(LibraryError) as cm:
            self.lib.restore_pack(pid)
        self.assertEqual(cm.exception.code, 404)

    def test_a_sticker_deleted_from_another_pack_does_not_unlink_a_file_a_trashed_pack_holds(self):
        a, rows = self.pack_with("A", 1)
        b = self.lib.create_pack("B")["id"]
        with self.lib.lock:                                   # the same file also sits in B
            db = self.lib._load()
            next(p for p in db["packs"] if p["id"] == b)["stickers"].append({**rows[0], "id": "dup"})
            self.lib._save(db)
        self.lib.delete_pack(a)
        self.lib.delete_sticker(b, "dup")
        self.assertIn(rows[0]["file"], on_disk(self.lib), "the trashed pack still needs it")


class PurgeAPack(Base):
    def test_a_purge_removes_the_files_and_the_record_and_leaves_nothing_orphaned(self):
        keep, keep_rows = self.pack_with("Keep", 2, generation="G001")
        gone, gone_rows = self.pack_with("Gone", 3, generation="G002")
        self.lib.delete_pack(gone)
        r = self.lib.purge_pack(gone)
        self.assertEqual((r["stickers"], r["files_removed"], r["kept_shared"]), (3, 3, []))
        self.assertGreater(r["bytes"], 0)
        self.assertEqual(self.lib.trashed_packs(), [])
        self.assertEqual([p["id"] for p in self.lib.snapshot()["packs"]], [keep])
        self.assertEqual(on_disk(self.lib), referenced(self.lib), "no row points at a missing file and no file is orphaned")
        self.assertFalse((self.lib.files / "G002").exists(), "the batch folder with nothing left in it went away")

    def test_a_shared_file_is_refused_in_words_naming_the_packs_until_confirmed(self):
        a, rows = self.pack_with("Alpha", 2)
        b = self.lib.create_pack("Beta")["id"]
        with self.lib.lock:
            db = self.lib._load()
            next(p for p in db["packs"] if p["id"] == b)["stickers"].append({**rows[0], "id": "dup"})
            self.lib._save(db)
        self.lib.delete_pack(a)
        rep = self.lib.trash_report(a)
        self.assertEqual([(s["sticker"], [h["name"] for h in s["also_in"]]) for s in rep["shared"]], [(rows[0]["id"], ["Beta"])])
        with self.assertRaises(LibraryError) as cm:
            self.lib.purge_pack(a)
        self.assertEqual(cm.exception.code, 409)
        self.assertIn("Beta", str(cm.exception))
        self.assertIn("Alpha 0", str(cm.exception))
        self.assertEqual(len(self.lib.trashed_packs()), 1, "nothing was deleted by the refusal")
        self.assertEqual(len(on_disk(self.lib)), 2)
        r = self.lib.purge_pack(a, confirm_shared=True)
        self.assertEqual(r["kept_shared"], [rows[0]["file"]])
        self.assertIn(rows[0]["file"], on_disk(self.lib), "the shared file stays for the pack that still uses it")
        self.assertNotIn(rows[1]["file"], on_disk(self.lib))
        self.assertEqual(on_disk(self.lib), referenced(self.lib))

    def test_sharing_with_another_trashed_pack_counts_too(self):
        a, rows = self.pack_with("Alpha", 1)
        b = self.lib.create_pack("Beta")["id"]
        with self.lib.lock:
            db = self.lib._load()
            next(p for p in db["packs"] if p["id"] == b)["stickers"].append({**rows[0], "id": "dup"})
            self.lib._save(db)
        self.lib.delete_pack(a)
        self.lib.delete_pack(b)
        with self.assertRaises(LibraryError) as cm:
            self.lib.purge_pack(a)
        self.assertIn("in the trash", str(cm.exception))

    def test_a_purge_that_stopped_half_way_is_simply_run_again(self):
        pid, rows = self.pack_with("Half", 3)
        self.lib.delete_pack(pid)
        (self.lib.files / rows[0]["file"]).unlink()                      # as if the first run got this far and stopped before the record went
        self.assertTrue(next(f for f in self.lib.trash_report(pid)["files"] if f["file"] == rows[0]["file"])["missing"])
        r = self.lib.purge_pack(pid)
        self.assertEqual((r["stickers"], r["files_removed"]), (3, 2))
        self.assertEqual(on_disk(self.lib), set())
        with self.assertRaises(LibraryError) as cm:
            self.lib.purge_pack(pid)
        self.assertEqual(cm.exception.code, 404)

    def test_only_a_trashed_pack_can_be_purged(self):
        pid, _ = self.pack_with("Live", 1)
        with self.assertRaises(LibraryError) as cm:
            self.lib.purge_pack(pid)
        self.assertEqual(cm.exception.code, 409)
        self.assertIn("delete it first", str(cm.exception))
        self.assertEqual(len(on_disk(self.lib)), 1)


class PurgeABatchFiles(Base):
    def test_describe_says_exactly_what_would_go(self):
        self.remove_batch(7, stickers=3)
        d = batches.describe(self.out, 7)
        self.assertEqual((d["id"], d["subject"], d["stickers"], d["by"]), ("G007", "subject 7", 3, "human"))
        files = batches.tree(batches.trash_entry(self.out, 7))
        self.assertEqual((d["files_total"], d["bytes"]), (len(files), sum(b for _, b in files)))
        self.assertIn("source/sheet.png", [f["path"] for f in d["files"]])
        self.assertEqual(d["in_flight"], [])
        with self.assertRaises(pl.PipelineError) as cm:
            batches.describe(self.out, 8)
        self.assertEqual(cm.exception.code, 404)

    def test_purge_files_removes_the_entry_and_its_meta_and_runs_again_harmlessly(self):
        self.remove_batch(7)
        r = batches.purge_files(self.out, 7)
        self.assertGreater(r["files"], 0)
        self.assertFalse(batches.trash_entry(self.out, 7).exists())
        self.assertFalse((pl.trash_batches_dir(self.out) / "G007.meta.json").exists())
        again = batches.purge_files(self.out, 7)
        self.assertEqual((again["files"], again["already"]), (0, True))

    def test_a_job_in_flight_refuses_in_words_and_deletes_nothing(self):
        self.remove_batch(7)
        jobs.create(self.out, "video", generation="G007", request={})
        with self.assertRaises(pl.PipelineError) as cm:
            batches.purge_files(self.out, 7)
        self.assertEqual(cm.exception.code, 409)
        self.assertIn("G007", str(cm.exception))
        self.assertTrue(batches.trash_entry(self.out, 7).is_dir())

    def test_a_live_batch_of_the_same_number_is_never_touched(self):
        self.remove_batch(7)
        make_batch(self.out, 7)
        with self.assertRaises(pl.PipelineError) as cm:
            batches.purge_files(self.out, 7)
        self.assertEqual(cm.exception.code, 409)
        self.assertTrue((self.out / "G007" / "result.json").is_file())
        self.assertTrue(batches.trash_entry(self.out, 7).is_dir())


class PurgeFlow(Base):
    """flow/purge.py end to end on files (the database part is replaced by a recorder)."""

    def setUp(self):
        super().setUp()
        self.rows = []
        self.shared_in_pool = 0

        def fake_purge(out, gid):
            self.rows.append(gid)
            return {"available": True, "indexed": 5}
        self.p1 = mock.patch.object(purge_rows, "purge_generation", fake_purge)
        self.p2 = mock.patch.object(purge_rows, "describe", lambda out, gid: {"available": True, "present": True, "stickers": 2, "indexed": 2, "shared_in_pool": self.shared_in_pool, "vectors": 2})
        self.p1.start(); self.p2.start()

    def tearDown(self):
        self.p1.stop(); self.p2.stop()
        super().tearDown()

    def test_listing_shows_batches_and_packs_with_exactly_what_a_purge_removes(self):
        self.remove_batch(4, stickers=2)
        pid, _ = self.pack_with("Cats", 2, generation="G004")
        self.lib.delete_pack(pid)
        lst = purge.listing(self.out, self.lib)
        b, p = lst["batches"][0], lst["packs"][0]
        self.assertEqual((b["type"], b["id"], b["stickers"], b["db"]["available"]), ("batch", "G004", 2, True))
        self.assertEqual([c["name"] for c in b["copies_in_packs"]], ["Cats"], "the pack that holds copies of the batch is named: they stay")
        self.assertTrue(b["copies_in_packs"][0]["trashed"])
        self.assertEqual((p["type"], p["id"], p["stickers"], p["from_batches"]), ("pack", pid, 2, ["G004"]))
        self.assertEqual(len(p["files"]), 2)
        self.assertGreater(p["bytes"], 0)
        self.assertEqual(lst["totals"]["items"], 2)
        self.assertEqual(lst["purge_all"], {"count": 2, "phrase": "purge 2", "skipped": []})
        self.assertEqual(lst["purge_batches"], {"count": 1, "phrase": "purge 1"})

    def test_listing_with_the_database_off_says_so_and_nothing_needs_a_confirmation(self):
        self.p2.stop()
        try:
            self.remove_batch(4)
            lst = purge.listing(self.out, self.lib)
        finally:
            self.p2.start()
        self.assertFalse(lst["batches"][0]["db"]["available"])
        self.assertIn("off", lst["database"])                                         # a reason in words, never a crash
        self.assertFalse(lst["batches"][0]["needs_confirm"])

    def test_purging_a_batch_is_complete_recorded_and_never_reuses_the_number(self):
        self.remove_batch(5)
        self.remove_batch(6)
        v = wait_done(self.out, purge.purge_one(self.out, self.lib, "batch", "G006", by="U1"))
        self.assertEqual((v["status"], v["results"][0]["ok"], v["results"][0]["id"]), ("done", True, "G006"))
        self.assertEqual(self.rows, [6], "the database part ran")
        self.assertFalse(batches.trash_entry(self.out, 6).exists())
        self.assertTrue(batches.trash_entry(self.out, 5).is_dir(), "only the chosen one")
        lines = [(r["kind"], r["id"], r["phase"], r["actor"], r["by"]) for r in ledger(self.out)]
        self.assertEqual(lines, [("batch", "G006", "begun", "human", "U1"), ("batch", "G006", "done", "human", "U1")])
        self.assertEqual(pl.next_gid(self.out), 7, "G006 was the highest number and is gone, yet it is not handed out again")
        self.assertEqual(pl.purged_ids(self.out), [6])

    def test_running_the_same_purge_again_is_not_an_error_and_writes_nothing_twice(self):
        self.remove_batch(5)
        wait_done(self.out, purge.purge_one(self.out, self.lib, "batch", "5"))
        again = purge.purge_one(self.out, self.lib, "batch", "G005")
        self.assertEqual((again["status"], again["results"][0].get("already")), ("done", True))
        self.assertEqual(len(ledger(self.out)), 2)
        self.assertEqual(self.rows, [5], "the database was not asked twice")
        with self.assertRaises(pl.PipelineError) as cm:
            purge.purge_one(self.out, self.lib, "batch", "G099")
        self.assertEqual(cm.exception.code, 404)

    def test_a_batch_with_stickers_in_the_shared_pool_is_refused_until_confirmed(self):
        self.remove_batch(5)
        self.shared_in_pool = 4
        with self.assertRaises(pl.PipelineError) as cm:
            purge.purge_one(self.out, self.lib, "batch", "G005")
        self.assertEqual(cm.exception.code, 409)
        self.assertIn("shared pool", str(cm.exception))
        self.assertIn("4 stickers", str(cm.exception))
        self.assertTrue(batches.trash_entry(self.out, 5).is_dir())
        self.assertEqual(self.rows, [])
        v = wait_done(self.out, purge.purge_one(self.out, self.lib, "batch", "G005", confirm_shared=True))
        self.assertEqual(v["status"], "done")
        self.assertTrue(ledger(self.out)[0]["confirmed_shared"])

    def test_purging_a_pack_keeps_a_ledger_line_and_a_shared_one_is_refused(self):
        a, rows = self.pack_with("Alpha", 2)
        b = self.lib.create_pack("Beta")["id"]
        with self.lib.lock:
            db = self.lib._load()
            next(p for p in db["packs"] if p["id"] == b)["stickers"].append({**rows[0], "id": "dup"})
            self.lib._save(db)
        self.lib.delete_pack(a)
        with self.assertRaises(pl.PipelineError) as cm:
            purge.purge_one(self.out, self.lib, "pack", a)
        self.assertEqual(cm.exception.code, 409)
        self.assertIn("Beta", str(cm.exception))
        self.assertEqual(ledger(self.out), [])
        v = wait_done(self.out, purge.purge_one(self.out, self.lib, "pack", a, by="U1", confirm_shared=True))
        self.assertEqual(v["status"], "done")
        line = ledger(self.out)[0]
        self.assertEqual((line["kind"], line["id"], line["phase"], line["actor"], line["by"], line["name"], line["kept_shared"]), ("pack", a, "done", "human", "U1", "Alpha", [rows[0]["file"]]))
        self.assertEqual(on_disk(self.lib), referenced(self.lib))
        again = purge.purge_one(self.out, self.lib, "pack", a)
        self.assertTrue(again["results"][0]["already"])

    def test_a_job_in_flight_is_409_in_words_for_one_and_the_server_busy_flag_for_all(self):
        self.remove_batch(5)
        jobs.create(self.out, "video", generation="G005", request={})
        with self.assertRaises(pl.PipelineError) as cm:
            purge.purge_one(self.out, self.lib, "batch", "G005")
        self.assertEqual(cm.exception.code, 409)
        self.assertIn("still working on it", str(cm.exception))
        with self.assertRaises(pl.PipelineError) as cm:
            purge.purge_one(self.out, self.lib, "batch", "G005", busy=True)
        self.assertIn("a job is running", str(cm.exception))
        lst = purge.listing(self.out, self.lib)
        self.assertEqual(lst["purge_all"]["count"], 0, "an item with a job in flight is not in delete all")
        self.assertIn("still working on it", lst["purge_all"]["skipped"][0]["why"])

    def test_remove_all_from_the_batch_column_deletes_only_the_removed_batches_and_their_rows(self):
        self.remove_batch(1); self.remove_batch(2)
        pid, _ = self.pack_with("P", 1)
        self.lib.delete_pack(pid)
        self.assertEqual(purge.listing(self.out, self.lib)["purge_batches"], {"count": 2, "phrase": "purge 2"})
        with self.assertRaises(pl.PipelineError) as cm:
            purge.purge_all(self.out, self.lib, "purge 3", kind="batch")
        self.assertIn("purge 2", str(cm.exception), "the count is the batches only")
        v = wait_done(self.out, purge.purge_all(self.out, self.lib, "purge 2", kind="batch"))
        self.assertEqual((v["status"], v["done"]), ("done", 2))
        self.assertEqual(pl.removed_ids(self.out), [])
        self.assertEqual(sorted(self.rows), [1, 2], "their database rows are deleted too")
        self.assertEqual([p_["id"] for p_ in self.lib.trashed_packs()], [pid], "a trashed pack is not a batch: it stays")

    def test_delete_all_needs_the_typed_phrase_naming_the_count_and_compares_it_with_the_trash_now(self):
        self.remove_batch(1); self.remove_batch(2)
        pid, _ = self.pack_with("P", 1)
        self.lib.delete_pack(pid)
        for bad in ("", "purge", "purge 2", "delete 3", "PURGE 4"):
            with self.assertRaises(pl.PipelineError) as cm:
                purge.purge_all(self.out, self.lib, bad)
            self.assertEqual(cm.exception.code, 409)
            self.assertIn("purge 3", str(cm.exception))
        self.assertEqual(len(pl.removed_ids(self.out)), 2, "a wrong phrase deletes nothing")
        v = wait_done(self.out, purge.purge_all(self.out, self.lib, "  Purge   3 ", by="U1"))
        self.assertEqual((v["status"], v["total"], v["done"]), ("done", 3, 3))
        self.assertEqual((pl.removed_ids(self.out), self.lib.trashed_packs()), ([], []))
        self.assertEqual(sorted(self.rows), [1, 2])
        self.assertEqual(pl.next_gid(self.out), 3)

    def test_delete_all_skips_what_needs_its_own_confirmation_and_says_so(self):
        self.remove_batch(1); self.remove_batch(2)
        self.p2.stop()
        self.p2 = mock.patch.object(purge_rows, "describe", lambda out, gid: {"available": True, "shared_in_pool": 3 if gid == 2 else 0})
        self.p2.start()
        lst = purge.listing(self.out, self.lib)
        self.assertEqual(lst["purge_all"]["count"], 1)
        self.assertEqual([(s["kind"], s["id"]) for s in lst["purge_all"]["skipped"]], [("batch", "G002")])
        v = wait_done(self.out, purge.purge_all(self.out, self.lib, "purge 1"))
        self.assertEqual([r["id"] for r in v["results"]], ["G001"])
        self.assertEqual([r["id"] for r in v["refused"]], ["G002"])
        self.assertIn("shared pool", v["refused"][0]["why"])
        self.assertTrue(batches.trash_entry(self.out, 2).is_dir(), "left in the trash for its own confirmation")

    def test_delete_all_run_again_is_idempotent(self):
        self.remove_batch(1)
        wait_done(self.out, purge.purge_all(self.out, self.lib, "purge 1"))
        again = purge.purge_all(self.out, self.lib, "purge 0")
        self.assertEqual((again["status"], again["total"], again.get("nothing_to_do")), ("done", 0, True))
        self.assertEqual(len(ledger(self.out)), 2)

    def test_busy_server_and_a_running_purge_refuse_a_second_one(self):
        self.remove_batch(1)
        for fn in (lambda: purge.purge_one(self.out, self.lib, "batch", "G001", busy=True), lambda: purge.purge_all(self.out, self.lib, "purge 1", busy=True)):
            with self.assertRaises(pl.PipelineError) as cm:
                fn()
            self.assertEqual(cm.exception.code, 409)
            self.assertIn("job is running", str(cm.exception))
        fake = purge.PurgeTask(self.out, self.lib, [{"kind": "batch", "id": "G001"}], "human", False, [])
        purge._TASKS[fake.id] = fake
        with self.assertRaises(pl.PipelineError) as cm:
            purge.purge_one(self.out, self.lib, "batch", "G001")
        self.assertIn(fake.id, str(cm.exception))
        self.assertIn("already running", str(cm.exception))

    def test_a_long_purge_does_not_hold_the_request_it_answers_running_and_is_polled(self):
        self.remove_batch(1)
        gate = threading.Event()
        real = batches.purge_files

        def slow(out, gid):
            gate.wait(10)
            return real(out, gid)
        with mock.patch.object(purge, "WAIT", 0.05), mock.patch.object(batches, "purge_files", slow):
            v = purge.purge_one(self.out, self.lib, "batch", "G001")
            self.assertEqual(v["status"], "running")
            self.assertEqual(purge.status(self.out, v["id"])["status"], "running")
            self.assertTrue(batches.trash_entry(self.out, 1).is_dir(), "still there while the thread works")
            gate.set()
            done = wait_done(self.out, v)
        self.assertEqual(done["status"], "done")
        self.assertFalse(batches.trash_entry(self.out, 1).exists())

    def test_a_failing_item_is_reported_stays_in_the_trash_and_the_next_run_finishes_it(self):
        self.remove_batch(1)
        real = batches.purge_files
        with mock.patch.object(batches, "purge_files", side_effect=OSError("disk said no")):
            v = wait_done(self.out, purge.purge_one(self.out, self.lib, "batch", "G001"))
        self.assertEqual(v["status"], "failed")
        self.assertIn("disk said no", v["results"][0]["error"])
        self.assertIn("run the purge again", v["error"].lower())
        self.assertTrue(batches.trash_entry(self.out, 1).is_dir())
        self.assertEqual([r["phase"] for r in ledger(self.out)], ["begun"], "begun, not done")
        self.assertEqual(pl.next_gid(self.out), 2)
        v = wait_done(self.out, purge.purge_one(self.out, self.lib, "batch", "G001"))
        self.assertEqual(v["status"], "done")
        self.assertEqual([r["phase"] for r in ledger(self.out)], ["begun", "done"], "the resumed run adds only the missing line")
        self.assertFalse(batches.trash_entry(self.out, 1).exists())

    def test_a_state_left_running_by_a_dead_server_reads_interrupted(self):
        self.remove_batch(1)
        purge.state_file(self.out).write_text(json.dumps({"id": "PG009", "status": "running", "total": 2, "done": 1}), encoding="utf-8")
        s = purge.last(self.out)
        self.assertEqual(s["status"], "interrupted")
        self.assertIn("run the purge again", s["error"])
        self.assertEqual(purge.status(self.out, "PG009")["status"], "interrupted")
        self.assertEqual(purge.listing(self.out, self.lib)["purge"]["status"], "interrupted")
        with self.assertRaises(pl.PipelineError) as cm:
            purge.status(self.out, "PG404")
        self.assertEqual(cm.exception.code, 404)

    def test_reviews_files_outside_the_trash_and_pack_copies_are_left_alone(self):
        keep_pid, keep_rows = self.pack_with("Keeper", 2, generation="G003")
        self.remove_batch(3)
        wait_done(self.out, purge.purge_one(self.out, self.lib, "batch", "G003"))
        self.assertEqual(on_disk(self.lib), referenced(self.lib))
        self.assertEqual(len(self.lib.snapshot()["packs"][0]["stickers"]), 2, "stickers already in a pack are copies and stay in their pack")
        self.assertEqual(len(on_disk(self.lib)), 2)


class FakeCursor:
    def __init__(self, log, counts, no_rows=()):
        self.log, self.counts, self.rowcount, self._row, self.no_rows = log, counts, 3, (7,), tuple(no_rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        sql = " ".join(sql.split())
        self.log.append(sql)
        self.rowcount = 0 if sql.startswith(self.no_rows) else 3

    def fetchone(self):
        return self._row


class FakeConn:
    def __init__(self, log, no_rows=()):
        self.log, self.commits, self.no_rows = log, 0, tuple(no_rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return FakeCursor(self.log, None, self.no_rows)

    def commit(self):
        self.commits += 1


class DatabaseHalf(unittest.TestCase):
    """store/purge_rows.py: which rows go and which stay, checked on the SQL it sends (a real Postgres is not reached)."""

    def run_purge(self, no_rows=()):
        log = []
        conn = FakeConn(log, no_rows)
        with mock.patch.object(purge_rows, "usable", lambda out: (True, None)), mock.patch.object(purge_rows.db, "connect", lambda: conn):
            res = purge_rows.purge_generation(Path("x"), 12)
        return res, log, conn

    def test_pool_rows_with_their_vectors_go_first_and_reviews_are_never_deleted_or_rewritten(self):
        res, log, conn = self.run_purge()
        self.assertTrue(res["available"])
        self.assertTrue(log[0].startswith("DELETE FROM sticker_index"), "the embeddings leave search before anything else")
        text = "\n".join(log)
        self.assertNotRegex(text, r"(?i)DELETE FROM reviews")
        self.assertNotRegex(text, r"(?i)UPDATE reviews")
        self.assertNotRegex(text, r"(?i)DELETE FROM tasks|UPDATE tasks")
        self.assertIn("DELETE FROM assets", text)
        self.assertIn("DELETE FROM generation_events", text)
        self.assertEqual(conn.commits, 1, "one transaction")

    def test_a_row_a_review_points_at_is_never_deleted_it_becomes_a_purged_tombstone(self):
        _, log, _ = self.run_purge(no_rows=("DELETE FROM generations",))  # something still points at the generation row: the DELETE matches nothing, so it is tombstoned
        dels = [s for s in log if s.startswith("DELETE FROM stickers")]
        self.assertEqual(len(dels), 1)
        self.assertIn("NOT EXISTS (SELECT 1 FROM reviews", dels[0])
        gen = next(s for s in log if s.startswith("DELETE FROM generations"))
        for needle in ("FROM reviews", "FROM tasks", "FROM stickers", "FROM video_sheets", "parent_id"):
            self.assertIn(needle, gen, "the generation row goes only when nothing points at it")
        self.assertTrue(any("status = 'PURGED'" in s and s.startswith("UPDATE stickers") and "still_review = 'PURGED'" in s for s in log), "the pool never re-indexes a purged sticker")
        self.assertTrue(any(s.startswith("UPDATE generations SET status = 'PURGED'") for s in log))

    def test_without_a_database_nothing_is_touched_and_the_answer_says_why(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"STICKERLOOM_DB_WRITE": "0"}):
            self.assertFalse(purge_rows.usable(Path(td))[0])
            r = purge_rows.purge_generation(Path(td), 3)
            self.assertEqual(r["available"], False)
            self.assertIn("off", r["reason"])
            d = purge_rows.describe(Path(td), 3)
            self.assertEqual(d["available"], False)

    def test_a_database_error_raises_so_the_purge_stops_and_can_be_run_again(self):
        class Boom(FakeConn):
            def cursor(self):
                raise RuntimeError("connection lost")
        with mock.patch.object(purge_rows, "usable", lambda out: (True, None)), mock.patch.object(purge_rows.db, "connect", lambda: Boom([])):
            with self.assertRaises(RuntimeError):
                purge_rows.purge_generation(Path("x"), 1)


class TrashRoutes(unittest.TestCase):
    """The routes over the real server (owner-only like Remove batch)."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory(); self.tmp = Path(self.td.name)
        self._env = mock.patch.dict(os.environ, {"STICKERLOOM_DB_WRITE": "0"}); self._env.start()
        self.out = self.tmp / "out"; self.out.mkdir()
        make_batch(self.out, 1); make_batch(self.out, 2)
        purge._TASKS.clear()
        self.srv, self.c = serve(self.out, self.tmp / "in", 0, cfg=EngineConfig(), block=False)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.c.wait_jobs(30)
        self.srv.shutdown()
        self._env.stop()
        purge._TASKS.clear()
        self.td.cleanup()

    def req(self, method, path, body=None, token=None):
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        hdr = {"Content-Type": "application/json"}
        if token:
            hdr["Authorization"] = f"Bearer {token}"
        h.request(method, path, json.dumps(body) if body is not None else None, hdr)
        r = h.getresponse(); data = r.read(); h.close()
        return r.status, json.loads(data)

    def test_delete_pack_is_soft_listed_restorable_and_purgeable_over_http(self):
        pid = self.req("POST", "/api/packs", {"name": "Cats"})[1]["id"]
        self.c.lib.add_bytes(pid, PNG + b"c" * 30, "png", "Cat", "static", "🐱")
        s, j = self.req("POST", f"/api/packs/{pid}/delete", {})
        self.assertEqual((s, j["trashed"]), (200, True))
        self.assertEqual(self.req("GET", "/api/library")[1]["packs"], [])
        s, lst = self.req("GET", "/api/trash")
        self.assertEqual((s, [p["id"] for p in lst["packs"]], lst["packs"][0]["stickers"]), (200, [pid], 1))
        s, j = self.req("POST", f"/api/packs/{pid}/restore", {})
        self.assertEqual((s, j["restored"]), (200, True))
        self.assertEqual(self.req("GET", "/api/trash")[1]["packs"], [])
        self.req("POST", f"/api/packs/{pid}/delete", {})
        s, v = self.req("POST", "/api/trash/purge", {"type": "pack", "id": pid})
        self.assertIn(s, (200, 202))
        t = wait_done(self.out, v)
        self.assertEqual(t["status"], "done")
        self.assertEqual(self.req("GET", "/api/trash")[1]["packs"], [])
        self.assertEqual(self.req("POST", f"/api/packs/{pid}/restore", {})[0], 404, "purged for good: there is nothing to restore")

    def test_batch_purge_and_delete_all_over_http_with_errors_in_words(self):
        self.req("POST", "/api/generations/1/remove", {}); self.req("POST", "/api/generations/2/remove", {})
        s, lst = self.req("GET", "/api/trash")
        self.assertEqual((s, [b["id"] for b in lst["batches"]], lst["purge_all"]["phrase"]), (200, ["G001", "G002"], "purge 2"))
        s, j = self.req("POST", "/api/trash/purge", {"type": "batch", "id": "G009"})
        self.assertEqual(s, 404)
        s, j = self.req("POST", "/api/trash/purge", {"type": "nonsense", "id": "x"})
        self.assertEqual(s, 400)
        s, j = self.req("POST", "/api/trash/purge_all", {"confirm": "purge 1"})
        self.assertEqual(s, 409)
        self.assertIn("purge 2", j["error"])
        self.assertEqual(len(pl.removed_ids(self.out)), 2)
        with self.c.lock:                                               # a job is running
            s, j = self.req("POST", "/api/trash/purge", {"type": "batch", "id": "G001"})
            self.assertEqual(s, 409)
            self.assertIn("job is running", j["error"])
            s, j = self.req("POST", "/api/trash/purge_all", {"confirm": "purge 2"})
            self.assertEqual(s, 409)
        s, v = self.req("POST", "/api/trash/purge", {"type": "batch", "id": "G001"})
        wait_done(self.out, v)
        s, v = self.req("POST", "/api/trash/purge_all", {"confirm": "purge 1"})
        self.assertIn(s, (200, 202))
        t = wait_done(self.out, v)
        self.assertEqual(t["status"], "done")
        s, p = self.req("GET", f"/api/trash/purges/{t['id']}")
        self.assertEqual((s, p["status"]), (200, "done"))
        self.assertEqual(self.req("GET", "/api/trash/purges/PG999")[0], 404)
        self.assertEqual(pl.removed_ids(self.out), [])
        self.assertEqual(pl.next_gid(self.out), 3)
        s, lst = self.req("GET", "/api/trash")
        self.assertEqual([r["id"] for r in lst["record"] if r["phase"] == "done"][:2], ["G002", "G001"])

    def test_a_member_cannot_reach_the_trash(self):
        _, tok = self.c.users.create("Amira", "member", False)
        self.assertEqual(self.req("GET", "/api/trash", token=tok)[0], 403)
        self.assertEqual(self.req("POST", "/api/trash/purge", {"type": "batch", "id": "G001"}, token=tok)[0], 403)
        self.assertEqual(self.req("POST", "/api/trash/purge_all", {"confirm": "purge 1"}, token=tok)[0], 403)
        self.assertEqual(self.req("GET", "/api/trash/purges/PG001", token=tok)[0], 403)
        self.assertEqual(self.req("POST", "/api/packs/abc/restore", {}, token=tok)[0], 403)


if __name__ == "__main__":
    unittest.main()
