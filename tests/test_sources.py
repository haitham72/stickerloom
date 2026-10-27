import tempfile
import unittest
from pathlib import Path

from stickerloom.flow import sources


def make(root, imgs, vids):
    for sub, pre, nums, ext in (("Images_gen", "img", imgs, ".jpg"), ("videos_gen", "vid", vids, ".mp4")):
        d = root / sub / f"{pre}-001-blob"; d.mkdir(parents=True)
        for n in nums:
            (d / f"{pre}-001-blob ({n}){ext}").write_bytes(b"")     # names only; scan never opens media


class PairingTests(unittest.TestCase):
    def picks(self, imgs, vids):
        with tempfile.TemporaryDirectory() as td:
            make(Path(td), imgs, vids)
            return sources.scan(Path(td))["blob"]

    def test_same_number_pairs_explicitly(self):
        p = self.picks([1, 2, 3], [2, 3])
        self.assertEqual([x.pairing for x in p], ["number"] * 3)
        self.assertEqual([x.video.stem[-3:] if x.video else None for x in p], [None, "(2)", "(3)"])

    def test_mismatched_numbers_are_flagged_as_guessed(self):
        p = self.picks([4, 5, 6, 7], [1, 2, 3, 4])   # partial overlap must NOT silently pair 4<->4
        self.assertEqual({x.pairing for x in p}, {"order"})
        self.assertEqual(p[0].video.stem[-3:], "(1)")


if __name__ == "__main__":
    unittest.main()


class FolderVariantTests(unittest.TestCase):
    def test_folder_per_variant_with_presliced_clips(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for n in (1, 2):
                for sub, pre, ext in (("Images_gen", "img", ".jpg"), ("videos_gen", "vid", ".mp4")):
                    d = root / sub / f"{pre}-00{n}-blob"; d.mkdir(parents=True)
                    (d / f"{pre}-00{n}-blob{ext}").write_bytes(b"")
            for fmt, ext, stem in (("quicktime", ".mov", "blob_ ({})"), ("webm", ".webm", "blob_01_ ({})")):
                cd = root / "videos_gen" / "vid-002-blob" / "slices" / fmt; cd.mkdir(parents=True)
                for k in range(1, 10):
                    (cd / (stem.format(k) + ext)).write_bytes(b"")
            p = sources.scan(root)["blob"]
            self.assertEqual([(x.variant, x.n_variants, x.subject_id, x.pairing) for x in p], [(1, 2, "001", "folder"), (2, 2, "002", "folder")])
            self.assertEqual(p[0].clips, {})
            self.assertEqual(sorted(p[1].clips), list(range(1, 10)))
            self.assertEqual(sorted(p[1].clips[3]), ["mov", "webm"])     # grid number comes from the trailing (n)


class PlanFileTests(unittest.TestCase):
    def test_plan_next_to_sheet_is_used(self):
        import json
        from stickerloom.flow import pipeline as pl
        from stickerloom.generation import prompter
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "in"; make(root, [1], [1])
            import cv2, numpy as np
            from tests import synth
            sheet = root / "Images_gen" / "img-001-blob" / "img-001-blob (1).jpg"
            cv2.imwrite(str(sheet), cv2.cvtColor(synth.make_sheet(), cv2.COLOR_RGB2BGR))
            plan = {"task": "blob", "task_slug": "Blob Pack", "stickers": [
                {"index": i, "prompt": f"blob pose {i}", "key": f"Blob Pose {i}", "emoji": "😀"} for i in range(1, 10)]}
            sheet.with_suffix(".json").write_text(json.dumps(plan), encoding="utf-8")
            out = Path(td) / "out"
            gid = pl.start("blob", out, root)
            r = pl.read_result(out, gid)
            self.assertEqual(r["plan_source"], "img-001-blob (1).json")
            self.assertEqual(r["task_slug"], "blob_pack")
            self.assertRegex(r["stickers"][0]["name"], r"^img-blob_pack-pose_1-\d{8}T\d{6}-[0-9a-f]{6}$")      # runtime/names.py: {media}-{subject}-{action}-{UTC time}-{hash}
            plan["stickers"].pop()
            sheet.with_suffix(".json").write_text(json.dumps(plan), encoding="utf-8")
            with self.assertRaises(pl.PipelineError):
                pl.start("blob", out, root)


class RotationTests(unittest.TestCase):
    def test_generate_never_advances_to_the_next_folder_by_itself(self):
        """One press is one folder. (It used to rotate 001 -> 002 -> 003 -> 001 on every press, which made a pre-generated test set unusable.)"""
        import cv2
        from stickerloom.flow import pipeline as pl
        from tests import synth
        with tempfile.TemporaryDirectory() as td:
            root, out = Path(td) / "in", Path(td) / "out"
            for n in (1, 2, 3):
                d = root / "Images_gen" / f"img-00{n}-blob"; d.mkdir(parents=True)
                cv2.imwrite(str(d / f"img-00{n}-blob.png"), cv2.cvtColor(synth.make_sheet(seed=n), cv2.COLOR_RGB2BGR))
                v = root / "videos_gen" / f"vid-00{n}-blob"; v.mkdir(parents=True); (v / f"vid-00{n}-blob.mp4").write_bytes(b"")
            got = []
            for _ in range(4):
                gid = pl.start("blob", out, root)
                s = pl.read_result(out, gid)["source"]
                got.append((s["variant"], s["subject_id"], Path(s["video_path"]).name))
            self.assertEqual(got, [(1, "001", "vid-001-blob.mp4")] * 4)
            self.assertEqual(pl.read_result(out, pl.start("blob", out, root, variant=3))["source"]["variant"], 3)   # an explicit folder is honoured
            self.assertEqual(pl.read_result(out, pl.start("blob", out, root, variant=2))["source"]["subject_id"], "002")
            self.assertEqual(pl.generations_by_folder(out)[("blob", "001")], ["G001", "G002", "G003", "G004"])


class DuplicateClipTests(unittest.TestCase):
    def test_copied_clip_sets_are_ignored(self):
        """vid-002's clips are byte copies of vid-001's: they would animate the wrong stickers, so 002 uses its own mp4."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for n, size in (("001", 10), ("002", 10), ("003", 11)):
                (root / "Images_gen" / f"img-{n}-blob").mkdir(parents=True)
                (root / "Images_gen" / f"img-{n}-blob" / "s.jpg").write_bytes(b"")
                vd = root / "videos_gen" / f"vid-{n}-blob"
                (vd / "slices" / "quicktime").mkdir(parents=True)
                (vd / f"vid-{n}-blob.mp4").write_bytes(b"")
                for cell in (1, 2):
                    (vd / "slices" / "quicktime" / f"c ({cell}).mov").write_bytes(b"x" * (size + cell))
            p = sources.scan(root)["blob"]
            self.assertEqual([bool(x.clips) for x in p], [True, False, True])
            self.assertEqual([x.clips_dup_of for x in p], [None, "001", None])
            self.assertTrue(p[1].has_video)          # still animatable from its own 3x3 mp4
