"""A sticker's gallery groups sprite cells under one set/run, retaining aliases."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from stickerloom.flow import particle_sets as ps
from tests.test_particle_owner import Library


class ParticleGalleryGroups(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out, self.lib = Path(self.tmp.name), Library()
        self.cells = [{'effect': 'E002', 'result': f'R{i:03d}', 'mode': 'video', 'cell': i} for i in range(1, 5)]
        self.saved = [{'effect': 'E002', 'sticker_id': 'slice01'}, {'effect': 'E002', 'sticker_id': 'slice02'}]
        self.legacy = {'created': self.cells, 'saved': self.saved}

    def gallery(self):
        with patch('stickerloom.flow.effects.for_sticker', return_value=self.legacy):
            return ps.for_sticker(self.out, self.lib, 'a', 's1')

    def test_four_slices_and_saved_clips_are_one_run(self):
        gallery = self.gallery()
        self.assertEqual(len(gallery['runs']), 1)
        self.assertEqual(len(gallery['runs'][0]['cells']), 4)
        self.assertEqual(gallery['runs'][0]['saved'], self.saved)
        self.assertEqual(gallery['created'], self.cells)
        self.assertEqual(gallery['saved'], self.saved)

    def test_saved_set_is_the_group_identity_not_each_slice(self):
        s = ps.create(self.out, self.lib, owners=['s1'])
        record = ps.read(self.out, s['id'])
        record['source']['effect'] = 'E002'
        ps._write(self.out, record)
        gallery = self.gallery()
        self.assertEqual([s['id'] for s in gallery['sets']], [s['id']])
        self.assertEqual(gallery['runs'][0]['imported_as'], [s['id']])

    def test_mixed_set_also_absorbs_its_appended_video_run(self):
        s = ps.create(self.out, self.lib, owners=['s1'])
        record = ps.read(self.out, s['id'])
        record['source']['effect'] = 'E001'
        record['cells'] = [{'n': 1, 'picked': True, 'import': 'E002/R001', 'file': 'cells/absent.png'}]
        ps._write(self.out, record)
        self.assertEqual(self.gallery()['runs'][0]['imported_as'], [s['id']])
