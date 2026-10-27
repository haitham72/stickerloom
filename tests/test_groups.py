"""Batch groups (flow/groups.py): variations of one idea are one family. A recorded `parent` (a chat edit, a redo) puts a batch in its
parent's family with no migration; "add X to Y" / dragging X onto Y makes Y's root the parent of X's whole family; leave makes a batch its own root again.
Earlier batches lists families, the root's title first, with every batch as a variant. Hand-made result files only: nothing is generated."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from stickerloom.flow import groups, pipeline as pl


def batch(out: Path, n: int, prompt: str, **extra) -> None:
    d = out / f"G{n:03d}"
    (d / "slices").mkdir(parents=True)
    res = {"generation_id": f"G{n:03d}", "number": n, "prompt": prompt, "created": 1000.0 + n, "stage": "sliced", "error": None, "grid": [2, 2],
           "source": {"subject": prompt.replace(" ", "_")}, "stickers": [{"index": 1, "key": "s1", "status": "READY", "png": "slices/S1.png", "review": {}, "history": []}],
           "reviews": {}, "video_sheets": [], **extra}
    (d / "result.json").write_text(json.dumps(res), encoding="utf-8")


class GroupTests(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        batch(self.out, 103, "superhero in paris")
        batch(self.out, 104, "superhero paris")
        batch(self.out, 105, "superhero paris", parent=104)            # the chat edit of 104, recorded but never shown as one family before
        batch(self.out, 106, "superhero paris", parent=105, regen_of="G105/S1")
        batch(self.out, 12, "ninja in paris")

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    def fam(self, gid):
        return groups.members(self.out, gid)

    def test_a_recorded_parent_is_already_a_family_with_its_relation(self):
        self.assertEqual(self.fam(105), [104, 105, 106])
        self.assertEqual([groups.relation(pl.read_result(self.out, g)) for g in (104, 105, 106)], [None, "edit", "redo"])
        self.assertEqual(self.fam(103), [103], "same topic, nothing linking them: its own family until someone adds it")

    def test_add_x_to_y_makes_y_the_parent_of_xs_whole_family_and_leave_undoes_it(self):
        j = groups.join(self.out, 104, 103, by="U1")
        self.assertEqual((j["root"], j["members"]), ("G103", ["G103", "G104", "G105", "G106"]), "the target is the parent; 104's edits come along")
        self.assertEqual(groups.relation(pl.read_result(self.out, 104)), "joined")
        self.assertEqual(pl.read_result(self.out, 104)["group_history"][-1]["decision"], "JOIN")
        with self.assertRaises(pl.PipelineError) as cm:
            groups.join(self.out, 105, 103)
        self.assertEqual(cm.exception.code, 409, "already in that family")
        with self.assertRaises(pl.PipelineError) as cm:
            groups.leave(self.out, 103)
        self.assertEqual(cm.exception.code, 409, "the parent cannot leave its own family")
        left = groups.leave(self.out, 105)
        self.assertEqual(left["members"], ["G105", "G106"], "a batch that leaves takes its own redo with it")
        self.assertEqual(self.fam(103), [103, 104])
        with self.assertRaises(pl.PipelineError) as cm:
            groups.join(self.out, 999, 103)
        self.assertEqual(cm.exception.code, 404)

    def test_the_library_sits_the_packs_of_one_group_together_and_follows_a_join(self):
        st = lambda g: {"id": f"s{g}", "source": {"generation": f"G{g:03d}", "index": 1}}
        packs = [{"id": "a", "stickers": [st(104)]}, {"id": "b", "stickers": [st(106), st(106), st(103)]}, {"id": "c", "stickers": [st(103)]},
                 {"id": "d", "stickers": [st(12)]}, {"id": "e", "stickers": [{"id": "photo", "source": {}}]}]
        self.assertEqual(groups.pack_groups(self.out, packs), {"a": "G104", "b": "G104"}, "a and b come from one family (b mostly); c, d, e have no other pack")
        groups.join(self.out, 104, 103)
        self.assertEqual(groups.pack_groups(self.out, packs), {"a": "G103", "b": "G103", "c": "G103"}, "a join in the Studio shows in the Library at the next read")
        pg = groups.pack_groups(self.out, packs)
        self.assertEqual(groups.pack_leads(packs, pg), {"a"}, "no parent assigned: the group's first pack leads")
        packs[2]["lead_at"] = 5.0
        self.assertEqual(groups.pack_leads(packs, pg), {"c"}, "Assign as parent in the Studio: that pack leads its group")

    def test_earlier_batches_lists_families_with_the_roots_title_and_every_variant(self):
        h = pl.history(self.out, 0, 50)
        self.assertEqual(h["total"], 3, "103, the 104 family and 012")
        fam = next(i for i in h["items"] if i["id"] == 104)
        self.assertEqual([v["generation_id"] for v in fam["variants"]], ["G104", "G105", "G106"])
        self.assertEqual(fam["prompt"], "superhero paris")
        groups.join(self.out, 12, 104)
        h = pl.history(self.out, 0, 50)
        self.assertEqual(h["total"], 2)
        self.assertEqual([v["id"] for v in next(i for i in h["items"] if i["id"] == 104)["variants"]], [104, 12, 105, 106])
