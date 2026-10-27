"""Temporal particle media, fake provider completion and durable imports; no paid calls."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from stickerloom.engine import particles
from stickerloom.flow import effects, particle_sets as ps
from tests.test_particle_owner import Library


def moving_clip():
    frames = np.zeros((2, 18, 24, 4), np.uint8)
    frames[0, 5:13, 2:8] = (255, 0, 0, 255)
    frames[1, 5:13, 16:22] = (0, 0, 255, 255)
    return particles.AnimatedSprite(frames, 2)


class TemporalEngineTests(unittest.TestCase):
    def params(self):
        return particles.ParticleParams(size=64, count=4, magnitude=0, gravity=0, spin=0,
                                       size_min=.5, size_max=.5, pop=.05, seed=7)

    def test_internal_animation_moves_inside_a_stationary_particle(self):
        out = particles.simulate([moving_clip()], self.params())
        early, later = out[10], out[30]
        self.assertGreater(early[..., 0].sum(), early[..., 2].sum())
        self.assertGreater(later[..., 2].sum(), later[..., 0].sum())
        xe = np.where(early[..., 3] > 127)[1].mean()
        xl = np.where(later[..., 3] > 127)[1].mean()
        self.assertGreater(xl - xe, 10)
        self.assertFalse(out[:3].any())
        self.assertFalse(out[-3:].any())

    def test_union_crop_preserves_travel_and_empty_frames(self):
        fr = np.concatenate([np.zeros((1, 18, 24, 4), np.uint8), moving_clip().frames])
        fitted = particles.fit_sprites([particles.AnimatedSprite(fr, 12)], 10)[0]
        self.assertEqual(fitted.frames.shape, (3, 4, 10, 4))
        self.assertEqual(fitted.fps, 12)
        self.assertFalse(fitted.frames[0].any())
        self.assertLess(np.where(fitted.frames[1, ..., 3] > 127)[1].mean(),
                        np.where(fitted.frames[2, ..., 3] > 127)[1].mean())

    def test_mixed_sprites_are_deterministic(self):
        green = np.full((5, 8, 4), (0, 255, 0, 255), np.uint8)
        sprites = [moving_clip(), green]
        a = particles.simulate(sprites, self.params())
        b = particles.simulate(sprites, self.params())
        np.testing.assert_array_equal(a, b)
        self.assertTrue((a[..., 1] > a[..., 0]).any())
        self.assertTrue((a[..., 2] > a[..., 1]).any())

    def test_constant_timeline_matches_static_sprite(self):
        still = moving_clip().frames[0]
        temporal = particles.AnimatedSprite(np.stack([still, still]), 15)
        np.testing.assert_array_equal(particles.simulate([still], self.params()),
                                      particles.simulate([temporal], self.params()))

    def test_clock_is_part_of_preview_cache_key(self):
        self.assertNotEqual(effects._digest([moving_clip()], self.params()),
                            effects._digest([particles.AnimatedSprite(moving_clip().frames, 20)], self.params()))

    def test_invalid_temporal_media_has_a_reason(self):
        for frames, fps in [(np.zeros((0, 3, 3, 4), np.uint8), 30), (moving_clip().frames, 0),
                            (moving_clip().frames, float('nan'))]:
            with self.assertRaises(ValueError):
                particles.AnimatedSprite(frames, fps)


class DurableTemporalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)
        self.lib = Library()
        self.lib.files = self.out / 'library' / 'files'
        self.lib.files.mkdir(parents=True)
        ps._MEDIA_CACHE.clear()

    def effect(self, timeline=True):
        d = self.out / 'effects' / 'E001'
        (d / 'results').mkdir(parents=True)
        Image.fromarray(moving_clip().frames[0]).save(d / 'results' / 'poster.png')
        (d / 'results' / 'clip.webm').write_bytes(b'fake clip bytes')
        if timeline:
            buf = io.BytesIO()
            np.savez_compressed(buf, frames=moving_clip().frames, fps=2)
            (d / 'results' / 'clip.npz').write_bytes(buf.getvalue())
        result = {'id':'R001', 'group':'g1', 'mode':'video', 'cell':1, 'status':'READY',
                  'file':'results/clip.webm', 'sprite_file':'results/poster.png', 'warnings':[]}
        if timeline:
            result.update(timeline_file='results/clip.npz', fps=2, frames=2)
        e = {'id':'E001', 'pack_id':'a', 'pack_name':'A', 'mode':'video', 'grid':[2,2], 'groups':[],
             'stickers':[{'sticker_id':'s1'}], 'video':{'g1':{'job':'J001'}}, 'results':[result], 'history':[]}
        (d / 'effect.json').write_text(json.dumps(e))
        return e

    def import_effect(self, e, **kwargs):
        with patch.object(effects, 'view', return_value=e), patch.object(ps, '_job_cost', return_value=4.5):
            return ps.set_from_effect(self.out, self.lib, 'E001', **kwargs)

    def test_temporal_import_and_cell_contract(self):
        s = self.import_effect(self.effect())
        cell = s['cells'][0]
        self.assertEqual((cell['type'], cell['fps'], cell['duration']), ('animated', 2, 1))
        self.assertTrue(cell['clip_url'].endswith('.webm'))
        self.assertEqual((s['source']['job'], s['credits']), ('J001', 4.5))
        sprites, _ = ps.sprites_of(self.out, self.lib, ps.read(self.out, s['id']), 'a')
        self.assertIsInstance(sprites[0], particles.AnimatedSprite)
        self.assertEqual(sprites[0].fps, 2)
        self.assertTrue((sprites[0].frames[1, ..., 2] == 255).any())

    def test_cut_completion_keeps_timeline_and_can_feed_simulator(self):
        e = self.effect()
        e['results'] = []
        (self.out/'effects'/'E001'/'effect.json').write_text(json.dumps(e))
        cell = {'index':1, 'frames':moving_clip().frames, 'src_fps':2, 'key':'green'}
        finished = {'data':b'fake encoded clip', 'status':'READY', 'checks':[], 'blocks':[], 'warnings':[], 'metrics':{}}
        job = {'id':'J001', 'result':{'file':'source.mp4'}, 'cost':4.5}
        with patch('stickerloom.engine.effect_video.cut_cells', return_value=[cell]), patch('stickerloom.engine.effect_video.finish_cell', return_value=finished):
            complete = effects.on_video_done(self.out, 'E001', 'g1', job, object())
        result = complete['results'][0]
        self.assertEqual((result['fps'], result['frames']), (30, 90))
        self.assertTrue((self.out/'effects'/'E001'/result['timeline_file']).is_file())
        s = self.import_effect(complete)
        sprites, _ = ps.sprites_of(self.out, self.lib, ps.read(self.out, s['id']), 'a')
        self.assertIsInstance(sprites[0], particles.AnimatedSprite)
        self.assertTrue((sprites[0].frames[25, ..., 2] == 255).any())

    def test_explicit_video_import_from_old_image_session_skips_sheet_cells(self):
        e = self.effect()
        e.update(mode='sim', set={'generation':5})
        with patch.object(ps, '_batch_rows') as sheet:
            s = self.import_effect(e, mode='video')
        sheet.assert_not_called()
        self.assertEqual(s['source']['kind'], 'video')
        self.assertEqual(s['n_cells'], 1)

    def test_legacy_video_cost_survives_missing_global_job_file(self):
        e = self.effect()
        e['video']['g1']['cost'] = 4.5
        with patch.object(effects, 'view', return_value=e):
            s = ps.set_from_effect(self.out, self.lib, 'E001')
        self.assertEqual((s['source']['job'], s['credits']), ('J001', 4.5))

    def test_legacy_clip_is_decoded_temporally_and_cached(self):
        s = self.import_effect(self.effect(timeline=False))
        info = {'width':24, 'height':18, 'fps':30, 'duration':3}
        with patch('stickerloom.engine.ffmpeg.probe', return_value=info), patch('stickerloom.engine.ffmpeg.decode_full', return_value=moving_clip().frames) as decode:
            a, _ = ps.sprites_of(self.out, self.lib, ps.read(self.out, s['id']), 'a')
            b, _ = ps.sprites_of(self.out, self.lib, ps.read(self.out, s['id']), 'a')
        self.assertEqual(decode.call_count, 1)
        self.assertIs(a[0], b[0])
        self.assertIsInstance(a[0], particles.AnimatedSprite)

    def test_selection_import_is_idempotent_and_old_bytes_stay(self):
        e = self.effect()
        e['results'].append({**e['results'][0], 'id':'R002', 'cell':2})
        s = self.import_effect(e, picked=[2])
        self.assertEqual([c['cell'] for c in s['cells']], [2])
        old = (self.out / 'particles' / s['id'] / s['cells'][0]['timeline']).read_bytes()
        again = self.import_effect(e, picked=[1, 2], target=s['id'])
        repeated = self.import_effect(e, picked=[1, 2], target=s['id'])
        self.assertEqual((again['n_cells'], repeated['n_cells'], repeated['credits']), (2, 2, 4.5))
        self.assertEqual(old, (self.out / 'particles' / s['id'] / s['cells'][0]['timeline']).read_bytes())

    def test_append_video_to_image_set_keeps_previous_spend(self):
        e = self.effect()
        s = ps.create(self.out, self.lib, owners=['s1'])
        record = ps.read(self.out, s['id'])
        record['credits'] = 2.0
        record['source']['job'] = 'J099'
        ps._write(self.out, record)
        merged = self.import_effect(e, target=s['id'])
        self.assertEqual(merged['credits'], 6.5)
        self.assertEqual(merged['source']['jobs'], ['J099', 'J001'])

    def test_duplicate_keeps_temporal_files_independent(self):
        s = self.import_effect(self.effect())
        duplicate = ps.duplicate(self.out, self.lib, s['id'])
        ps.delete(self.out, self.lib, s['id'], confirm_packs=True)
        sprites, _ = ps.sprites_of(self.out, self.lib, ps.read(self.out, duplicate['id']), 'a')
        self.assertIsInstance(sprites[0], particles.AnimatedSprite)
        self.assertEqual(duplicate['cells'][0]['type'], 'animated')

    def test_preview_and_render_consume_temporal_media(self):
        s = self.import_effect(self.effect())
        params = {'count':4, 'magnitude':0, 'gravity':0, 'spin':0, 'size_min':.5, 'size_max':.5}
        preview = ps.preview(self.out, self.lib, s['id'], pack_id='a', params=params, size=64)
        self.assertTrue((self.out / 'particles' / s['id'] / preview['file']).is_file())
        captured = []
        def encode(frames, cfg, **kwargs):
            captured.append(frames)
            return {'data':b'fake render', 'status':'READY', 'checks':[], 'blocks':[], 'warnings':[], 'metrics':{}}
        with patch('stickerloom.engine.effect_video.encode_and_check', side_effect=encode):
            rendered = ps.render(self.out, self.lib, s['id'], object(), pack_id='a', params=params)
        self.assertEqual(rendered['status'], 'READY')
        self.assertTrue((captured[0][25, ..., 2] > captured[0][25, ..., 0]).any())

    def test_a_stand_alone_set_renders_on_its_own_and_can_start_a_pack_of_its_own(self):
        s = self.import_effect(self.effect())
        raw = ps.read(self.out, s['id']); raw['owner'] = []; ps._write(self.out, raw)          # no sticker owns it (the Studio's Particles choice)
        ok = lambda frames, cfg, **kw: {'data': b'fake render', 'status': 'READY', 'checks': [], 'blocks': [], 'warnings': [], 'metrics': {}}
        with patch('stickerloom.engine.effect_video.encode_and_check', side_effect=ok):
            r = ps.render(self.out, self.lib, s['id'], object(), params={'count': 2})
        self.assertEqual((r['status'], r['pack_id']), ('READY', None), 'no pack is needed for a stand-alone set')
        made = []
        def create_pack(name):
            p = {'id': 'new', 'name': name, 'stickers': []}; self.lib.db['packs'].append(p); return p
        def add_bytes(pid, data, ext, name, kind, emoji, source=None, **kw):
            st = {'id': f'b{len(made)}', 'name': name, 'emoji': emoji, 'type': kind, 'source': source}; made.append((pid, st)); return st
        self.lib.create_pack, self.lib.add_bytes = create_pack, add_bytes
        out = ps.add(self.out, self.lib, s['id'], [r['id']], new_pack='Lipstick particles')
        self.assertEqual((out['pack_id'], made[0][0], made[0][1]['emoji']), ('new', 'new', '✨'), '"Make a pack of it": a new pack holds the burst')

    def test_recovery_preserves_animation_sources_and_explicit_owners(self):
        st = self.lib.db['packs'][0]['stickers'][0]
        st.update(file='original.webm', type='animated', name='Fire')
        source = self.lib.files / st['file']
        source.write_bytes(b'original animation stays')
        info = {'width':24, 'height':18, 'fps':2, 'duration':1}
        with patch('stickerloom.engine.ffmpeg.probe', return_value=info), patch('stickerloom.engine.ffmpeg.decode_full', return_value=moving_clip().frames):
            s = ps.set_from_stickers(self.out, self.lib, ['s1'], 'a', owners=['s2'])
        self.assertEqual([o['sticker_id'] for o in s['owner']], ['s2'])
        self.assertEqual(s['cells'][0]['source_pack'], 'a')
        self.assertEqual(source.read_bytes(), b'original animation stays')
        sprites, _ = ps.sprites_of(self.out, self.lib, ps.read(self.out, s['id']), 'a')
        self.assertIsInstance(sprites[0], particles.AnimatedSprite)
        self.assertEqual(sprites[0].fps, 2)

    def test_missing_legacy_pack_detaches_without_failing(self):
        s = ps.create(self.out, self.lib)
        f = self.out / 'particles' / s['id'] / 'set.json'
        old = ps.read(self.out, s['id'])
        old.pop('owner')
        old.update(packs=['gone'], source={'sticker_ids':['s1']})
        f.write_text(json.dumps(old))
        self.assertTrue(ps.view(self.out, self.lib, s['id'])['detached'])

    def test_owner_pack_is_derived_after_sticker_move(self):
        s = ps.create(self.out, self.lib, owners=['s1'])
        sticker = self.lib.db['packs'][0]['stickers'].pop(0)
        self.lib.db['packs'].append({'id':'b', 'name':'B', 'stickers':[sticker]})
        self.assertEqual(ps.view(self.out, self.lib, s['id'])['packs'], ['b'])

    def test_purge_legacy_trashed_set_preserves_other_owner(self):
        s = ps.create(self.out, self.lib, owners=['s1'])
        ps.delete(self.out, self.lib, s['id'], confirm_packs=True)
        f = self.out / 'trash' / 'particles' / s['id'] / 'set.json'
        old = json.loads(f.read_text())
        old.pop('owner')
        old.update(packs=['a','b'], source={'sticker_ids':['s1','s3']})
        f.write_text(json.dumps(old))
        self.lib.db['trash'] = {'packs':[self.lib.db['packs'].pop()]}
        self.lib.db['packs'].append({'id':'b', 'name':'B', 'stickers':[{'id':'s3'}]})
        ps.detach_pack(self.out, self.lib, 'a')
        self.assertTrue(f.is_file())
        self.assertEqual([o['sticker_id'] for o in json.loads(f.read_text())['owner']], ['s3'])
        self.assertTrue(f.with_name('set.json.pre-owner').is_file())


if __name__ == '__main__':
    unittest.main()
