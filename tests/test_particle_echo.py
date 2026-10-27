"""Saved motion branches reach the sticker's echo reaction without paid work."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stickerloom.flow import particle_sets as ps
from tests.test_particle_owner import Library


class ParticleEchoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)
        self.lib = Library()

    def test_save_branch_assign_then_echo_uses_saved_motion_and_size(self):
        original = ps.create(self.out, self.lib, owners=['s1'])
        original = ps.update(self.out, self.lib, original['id'], motion={'preset': 'burst'})
        branch = ps.duplicate(self.out, self.lib, original['id'])
        motion = {'preset': 'fountain', 'params': {'magnitude': 1.7, 'sprite_px': 128, 'scale': 2}}
        ps.update(self.out, self.lib, branch['id'], motion=motion)
        ps.link(self.out, self.lib, branch['id'], ['s2'])
        self.assertEqual(ps.read(self.out, original['id'])['motion'], {'preset': 'burst'})
        with patch('stickerloom.flow.particle_sets.preview', return_value={'url': '/preview.webp'}) as preview:
            reply = ps.preview_for_sticker(self.out, self.lib, 'a', 's2')
        self.assertEqual(reply['set'], branch['id'])
        self.assertEqual(reply['motion'], motion)
        preview.assert_called_once_with(self.out, self.lib, branch['id'], pack_id='a', size=ps.ECHO_PX)
        preset, px, scale, params = ps._burst(ps.read(self.out, branch['id']), None, {})
        self.assertEqual((preset, px, scale, params.magnitude), ('fountain', 128, 2, 1.7))

    def test_newest_link_beats_global_newest_and_refreshes_after_reassignment(self):
        old = ps.create(self.out, self.lib, owners=['s1'])
        new = ps.create(self.out, self.lib, owners=['s1'])
        ps.create(self.out, self.lib, owners=['s2'])
        with patch('stickerloom.flow.particle_sets.preview', return_value={'url': '/preview.webp'}):
            self.assertEqual(ps.preview_for_sticker(self.out, self.lib, 'a', 's1')['set'], new['id'])
            ps.link(self.out, self.lib, new['id'], ['s1'], unlink=True)
            self.assertEqual(ps.preview_for_sticker(self.out, self.lib, 'a', 's1')['set'], old['id'])

    def test_the_newest_saved_version_beats_a_newer_draft_and_an_older_link(self):
        v1 = ps.update(self.out, self.lib, ps.create(self.out, self.lib, owners=['s1'])['id'], save=True)
        v2 = ps.update(self.out, self.lib, ps.create(self.out, self.lib, owners=['s1'])['id'], save=True)
        ps.create(self.out, self.lib, owners=['s1'])                                  # a draft started after v2, never saved
        ps.update(self.out, self.lib, v1['id'], motion={'preset': 'burst'})          # editing v1 without Save keeps it v1
        with patch('stickerloom.flow.particle_sets.preview', return_value={'url': '/preview.webp'}):
            self.assertEqual(ps.preview_for_sticker(self.out, self.lib, 'a', 's1')['set'], v2['id'], 'Echo plays the latest saved version (v2)')

    def test_unassigned_sticker_never_uses_other_stickers_set(self):
        ps.create(self.out, self.lib, owners=['s2'])
        with patch('stickerloom.flow.particle_sets.preview') as preview:
            self.assertEqual(ps.preview_for_sticker(self.out, self.lib, 'a', 's1'), {'set': None, 'url': None})
        preview.assert_not_called()

    def test_invalid_saved_size_and_non_object_overrides_are_actionable_errors(self):
        s = ps.create(self.out, self.lib, owners=['s1'])
        with self.assertRaises(ps.SetError):
            ps.update(self.out, self.lib, s['id'], motion={'params': {'sprite_px': 1}})
        with self.assertRaises(ps.SetError):
            ps._burst(s, None, [])

    def test_wrong_sticker_does_not_fall_back(self):
        with self.assertRaises(ps.SetError):
            ps.preview_for_sticker(self.out, self.lib, 'a', 'absent')
