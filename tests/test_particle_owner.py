"""Sticker ownership regressions; temporary files only, no provider."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from stickerloom.flow import particle_sets as ps


class Library:
    def __init__(self):
        self.lock = threading.RLock()
        self.db = {"packs": [{"id": "a", "name": "A", "stickers": [
            {"id": "s1", "source": {"generation": "G001", "index": 1}},
            {"id": "s2", "source": {"generation": "G001", "index": 2}}]}]}
    def _load(self): return self.db
    def _save(self, db): self.db = db
    def _pack(self, db, pid): return next(p for p in db["packs"] if p["id"] == pid)


class OwnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)
        self.lib = Library()

    def test_owner_and_derived_pack(self):
        s = ps.create(self.out, self.lib, owners=[{"pack_id": "a", "sticker_id": "s1"}])
        self.assertEqual(s["packs"], ["a"])
        self.assertNotIn("packs", ps.read(self.out, s["id"]))
        self.assertEqual(self.lib.db["packs"][0]["stickers"][0]["particles"], [s["id"]])

    def test_migration_preserves_backup_and_is_idempotent(self):
        s = ps.create(self.out, self.lib)
        f = self.out/"particles"/s["id"]/"set.json"
        old = ps.read(self.out, s["id"])
        old.pop("owner")
        old.update(packs=["a"], source={"sticker_ids": ["s2"]})
        f.write_text(json.dumps(old))
        ps.view(self.out, self.lib, s["id"])
        first = f.read_bytes()
        ps.view(self.out, self.lib, s["id"])
        self.assertEqual(first, f.read_bytes())
        self.assertEqual(json.loads(f.with_name("set.json.pre-owner").read_text()), old)
        self.assertEqual(ps.read(self.out, s["id"])["owner"][0]["sticker_id"], "s2")

    def test_unresolvable_migration_detaches(self):
        s = ps.create(self.out, self.lib)
        self.assertTrue(s["detached"])

    def test_aliases_link_and_unlink_stickers(self):
        s = ps.create(self.out, self.lib)
        self.assertEqual(len(ps.assign(self.out, self.lib, s["id"], ["a"])["owner"]), 2)
        self.assertTrue(ps.unassign(self.out, self.lib, s["id"], ["a"])["detached"])

    def test_counts_are_versions_an_old_run_counts_once_not_once_per_slice(self):
        ps.create(self.out, self.lib, owners=["s1"])
        slices = [{"effect": "E009", "usable": True}, {"effect": "E009", "usable": True}]
        with patch('stickerloom.flow.effects._gallery', return_value={"s1": {"created": slices, "saved": [{"missing": False}]}}):
            counts = ps.counts_for_pack(self.out, self.lib, "a")
        self.assertEqual(counts["s1"], {"created": 2, "saved": 1, "sets": 1}, "one set + one run of two slices = two versions")

    def test_purge_detaches_without_deleting(self):
        s = ps.create(self.out, self.lib, owners=["s1"])
        ps.detach_pack(self.out, self.lib, "a")
        self.assertTrue(ps.view(self.out, self.lib, s["id"])["detached"])

    def test_delete_removes_live_link_restore_recovers_owner(self):
        s = ps.create(self.out, self.lib, owners=['s1'])
        ps.delete(self.out, self.lib, s['id'], confirm_packs=True)
        self.assertNotIn(s['id'], self.lib.db['packs'][0]['stickers'][0].get('particles', []))
        restored = ps.restore(self.out, self.lib, s['id'])
        self.assertEqual(restored['owner'][0]['sticker_id'], 's1')
        self.assertIn(s['id'], self.lib.db['packs'][0]['stickers'][0]['particles'])

    def test_kling_set_keeps_clip_sprite_and_cost(self):
        from PIL import Image
        d = self.out/'effects'/'E001'
        (d/'results').mkdir(parents=True)
        Image.new('RGBA', (12, 10), (255, 0, 0, 255)).save(d/'results'/'peak.png')
        (d/'results'/'clip.webm').write_bytes(b'fake fixture clip')
        e = {"id": "E001", "pack_id": "a", "pack_name": "A", "mode": "video", "groups": [],
             "stickers": [{"sticker_id": "s1"}], "video": {"g": {"job": "J001"}},
             "results": [{"id": "R001", "job":"J001", "mode": "video", "cell": 1, "status": "READY", "sprite_file": "results/peak.png", "file": "results/clip.webm", "warnings": ["effect_tail_faded"]}]}
        (d/'effect.json').write_text(json.dumps(e))
        with patch('stickerloom.flow.effects.view', return_value=e), patch('stickerloom.flow.particle_sets._job_cost', return_value=4.5):
            s = ps.set_from_effect(self.out, self.lib, 'E001')
        self.assertEqual((s['source']['kind'], s['source']['job'], s['credits']), ('video', 'J001', 4.5))
        self.assertEqual((s['cells'][0]['w'], s['cells'][0]['h']), (12, 10))
        self.assertTrue((self.out/'particles'/s['id']/s['cells'][0]['clip']).is_file())
        with patch('stickerloom.flow.effects.view', return_value=e), patch('stickerloom.flow.particle_sets._job_cost', return_value=4.5):
            repeated = ps.set_from_effect(self.out, self.lib, 'E001', target=s['id'])
            self.assertEqual((repeated['n_cells'], repeated['credits']), (1, 4.5))
            e['results'].append({**e['results'][0], 'id':'R002', 'cell':2, 'job':'J002'})
            more = ps.set_from_effect(self.out, self.lib, 'E001', target=s['id'])
        self.assertEqual((more['id'], more['n_cells'], more['credits']), (s['id'], 2, 9.0))

    def test_unknown_owner_refused_before_creating_record(self):
        with self.assertRaises(ps.SetError):
            ps.create(self.out, self.lib, owners=['absent'])

    def test_slice_creation_again_appends_and_keeps_previous_cell(self):
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new('RGBA', (10, 12), (10, 20, 30, 255)).save(buf, format='PNG')
        f = self.out/'slice.png'
        f.write_bytes(buf.getvalue())
        rows = [(1, f, {'index':1,'status':'READY'})]
        with patch('stickerloom.flow.particle_sets._batch_rows', return_value=(rows, {}, {})):
            first = ps.set_from_slices(self.out, self.lib, [{'generation':'G001','index':1}], owners=['s1'])
            old = (self.out/'particles'/first['id']/first['cells'][0]['file']).read_bytes()
            second = ps.set_from_slices(self.out, self.lib, [{'generation':'G001','index':1}], target=first['id'])
        self.assertEqual((first['id'],second['id'],second['n_cells']), (first['id'],first['id'],2))
        self.assertEqual(old,(self.out/'particles'/first['id']/first['cells'][0]['file']).read_bytes())


if __name__ == '__main__': unittest.main()
