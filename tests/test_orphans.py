"""Empty stale batch folders: a live G### folder with no
result.json is never a batch (Remove refuses it, the Studio skips it). `pipeline.orphan_batches` lists them, `pipeline.prune_orphans`
deletes only the zero-file ones, and `stickerloom prune-orphans` is the operator command (`--apply` deletes, default lists).

Nothing here reaches a provider, a model or Postgres: temp directories only."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from stickerloom.flow import batches, pipeline as pl
from stickerloom.generation import jobs


def make_batch(out: Path, gid: int) -> Path:
    d = out / f"G{gid:03d}"
    (d / "source").mkdir(parents=True)
    (d / "result.json").write_text(json.dumps({"generation_id": f"G{gid:03d}", "number": gid, "stickers": []}), encoding="utf-8")
    return d


class OrphanBatches(unittest.TestCase):
    def setUp(self):
        self.td = TemporaryDirectory()
        self.out = Path(self.td.name)
        make_batch(self.out, 1)                                        # a real batch: never listed, never touched
        (self.out / "G002" / "slices").mkdir(parents=True)            # a zero-file orphan: stale clutter
        (self.out / "G003" / "slices").mkdir(parents=True)
        (self.out / "G003" / "source").mkdir(parents=True)
        d = self.out / "G004"                                          # an interrupted start: files but no result.json
        (d / "slices").mkdir(parents=True)
        (d / "prompts.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.td.cleanup()

    def test_orphan_batches_lists_folders_without_result_json_only(self):
        ids = [r["id"] for r in pl.orphan_batches(self.out)]
        self.assertEqual(ids, ["G002", "G003", "G004"])
        by_id = {r["id"]: r for r in pl.orphan_batches(self.out)}
        self.assertEqual((by_id["G002"]["files"], by_id["G004"]["files"]), (0, 1))

    def test_prune_removes_only_the_empty_ones_and_is_idempotent(self):
        res = pl.prune_orphans(self.out)
        self.assertEqual((res["removed"], [k["id"] for k in res["kept"]]), (["G002", "G003"], ["G004"]))
        self.assertTrue((self.out / "G001" / "result.json").is_file(), "the real batch is untouched")
        self.assertTrue((self.out / "G004" / "prompts.json").is_file(), "an interrupted start is never auto-deleted")
        again = pl.prune_orphans(self.out)
        self.assertEqual((again["removed"], [k["id"] for k in again["kept"]]), ([], ["G004"]))

    def test_a_folder_named_by_an_open_job_is_kept(self):
        jobs.create(self.out, "video", generation="G002", request={})  # REQUESTED = in flight
        res = pl.prune_orphans(self.out)
        self.assertEqual(res["removed"], ["G003"])
        self.assertEqual([k["id"] for k in res["kept"]], ["G002", "G004"])
        self.assertIn("open job", next(k["why"] for k in res["kept"] if k["id"] == "G002"))

    def test_remove_names_prune_orphans_for_an_empty_folder(self):
        with self.assertRaises(pl.PipelineError) as cm:
            batches.remove(self.out, 2)
        self.assertEqual(cm.exception.code, 404)
        self.assertIn("prune-orphans", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
