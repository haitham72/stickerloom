"""Readable folder names: a new batch is `G111-dog_as_banana-<UTC time>/` and a new job `J058-dog_as_banana-sheet.json`, while the id
(G111, J058) stays the address everywhere else: gen_dir, list_ids, the trash, `/out/G111/...` URLs, asset keys and job lookups all reach the labelled folder,
and the bare `G110/` and `J057.json` made before keep working untouched."""
import json
import tempfile
import unittest
from pathlib import Path

from stickerloom.console.server import _out_batch
from stickerloom.flow import batches, pipeline as pl
from stickerloom.generation import jobs
from stickerloom.runtime import names
from stickerloom.store.assets import LocalAssetStore


def old(out: Path, gid: int) -> Path:
    d = out / f"G{gid:03d}"
    (d / "slices").mkdir(parents=True)
    (d / "result.json").write_text(json.dumps({"generation_id": f"G{gid:03d}", "number": gid, "task_slug": "falcon", "stickers": []}), encoding="utf-8")
    return d


class FolderNames(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(); self.out = Path(self.td.name)
        old(self.out, 1)

    def tearDown(self):
        self.td.cleanup()

    def new(self, slug="dog_as_banana") -> tuple[int, Path]:
        gid, d = pl._allocate(self.out, slug, 1791105700.0)
        (d / "slices").mkdir()
        (d / "slices" / "a.png").write_bytes(b"png")
        (d / "result.json").write_text(json.dumps({"generation_id": f"G{gid:03d}", "number": gid, "task_slug": slug, "stickers": []}), encoding="utf-8")
        return gid, d

    def test_names_round_trip(self):
        self.assertEqual(names.folder("G111", "Dog as banana!", when=1791105700), "G111-dog_as_banana-20261004T092140")
        self.assertEqual(names.folder("J058", "dog_as_banana", "sheet"), "J058-dog_as_banana-sheet")
        self.assertEqual([names.folder_id(n) for n in ("G111-dog_as_banana-20261004T092140", "G110", "J058-x-sheet", "G110.meta.json", "library")],
                         ["G111", "G110", "J058", None, None])

    def test_a_new_batch_is_labelled_and_found_by_its_number(self):
        gid, d = self.new()
        self.assertEqual((gid, d.name), (2, "G002-dog_as_banana-20261004T092140"))
        self.assertEqual(pl.gen_dir(self.out, 2), d)
        self.assertEqual(pl.gen_dir(self.out, 1), self.out / "G001")                              # an older batch keeps its bare folder
        self.assertEqual(pl.list_ids(self.out), [1, 2])
        self.assertEqual(pl.read_result(self.out, 2)["generation_id"], "G002")
        self.assertEqual(pl.next_gid(self.out), 3)

    def test_urls_and_asset_keys_name_the_id_and_reach_the_labelled_folder(self):
        _, d = self.new()
        self.assertEqual(pl.out_path(self.out, "G002/slices/a.png"), d / "slices" / "a.png")
        self.assertEqual(_out_batch(self.out, "/out/G002/slices/a.png"), 2)
        self.assertEqual(_out_batch(self.out, f"/out/{d.name}/slices/a.png"), 2)
        self.assertIsNone(_out_batch(self.out, "/out/G002/../users.json"))
        self.assertEqual(LocalAssetStore(self.out).read("G002/slices/a.png"), b"png")

    def test_the_trash_keeps_the_label_and_restore_puts_it_back(self):
        _, d = self.new()
        batches.remove(self.out, 2)
        self.assertEqual(pl.list_ids(self.out), [1])
        self.assertTrue((self.out / "trash" / "batches" / d.name / "slices" / "a.png").is_file())
        self.assertEqual([r["subject"] for r in batches.list_removed(self.out)], ["dog_as_banana"])
        self.assertEqual(pl.next_gid(self.out), 3)
        batches.restore(self.out, 2)
        self.assertEqual(pl.gen_dir(self.out, 2), d)
        self.assertTrue((d / "slices" / "a.png").is_file())

    def test_a_new_job_is_labelled_and_old_ones_still_read(self):
        (self.out / "jobs").mkdir()
        (self.out / "jobs" / "J057.json").write_text(json.dumps({"id": "J057", "kind": "sheet", "status": "DONE"}), encoding="utf-8")
        gid, _ = self.new()
        job = jobs.create(self.out, "video", generation=f"G{gid:03d}", request={})
        self.assertEqual(job["id"], "J058")
        self.assertTrue((self.out / "jobs" / "J058-dog_as_banana-video.json").is_file())
        self.assertEqual(jobs.read(self.out, "j058")["generation"], "G002")
        self.assertEqual(jobs.read(self.out, "J057")["status"], "DONE")
        self.assertEqual(jobs.job_dir(self.out, "J058").name, "J058-dog_as_banana-video")
        self.assertEqual(jobs.next_id(self.out), "J059")
        self.assertEqual(sorted(j["id"] for j in jobs.list(self.out)), ["J057", "J058"])
        with self.assertRaises(jobs.JobError):
            jobs.read(self.out, "J05")


if __name__ == "__main__":
    unittest.main()
