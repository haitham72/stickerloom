"""Manual batches on fake providers and isolated libraries: no paid calls."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from stickerloom.generation import actions, emoji_batches as batches, tasks
from stickerloom.flow import pipeline as pl, gates
from stickerloom.media.library import Library, LibraryError
from tests.test_agent import Base

GLYPHS = ['😀', '🏫', '🚌', '🍒', '🐱', '🐶', '🦋', '🎂', '🚀']


class EngineTests(unittest.TestCase):
    def test_full_bank_preserves_sequences_and_skips_used_tokens(self):
        selected, words = batches.extract('banana 👍🏽 👨‍👩‍👧‍👦 🇦🇪 🏫 🚌 🍒 🐱 🎂 🚀')
        self.assertEqual(len(selected), 9)
        self.assertEqual(selected[:3], ['👍🏽', '👨‍👩‍👧‍👦', '🇦🇪'])
        self.assertEqual(words, 'banana')
        bank = actions.generation_bank()
        self.assertGreater(len(bank), 1800)
        self.assertEqual(len({batches.identity(r['emoji']) for r in bank.values()}), len(bank))
        after_core = actions.next_tokens(set(actions.ACTION_BANK), 9)
        self.assertFalse(set(after_core) & set(actions.ACTION_BANK))
        self.assertEqual(len(tasks._token_plan('banana', (3,3), False, after_core, 'flat_vector', False)['stickers']), 9)

    def test_repeat_choices_and_skip_require_an_unused_replacement(self):
        draft = batches.plan('banana', GLYPHS, ['😀'])
        self.assertFalse(draft['ready'])
        self.assertEqual(draft['waiting'][0]['version'], 2)
        for decision, version in (('overwrite', 1), ('add', 2)):
            ready = batches.plan('banana', GLYPHS, ['😀'], {'1': decision})
            self.assertTrue(ready['ready'])
            self.assertEqual(ready['plan']['slots']['emoji_batch']['entries'][0]['version'], version)
        self.assertFalse(batches.plan('banana', GLYPHS, ['😀'], {'1':'skip'})['ready'])
        with self.assertRaises(pl.PipelineError):
            batches.plan('banana', GLYPHS, ['😀'], {'1':'skip'}, {'1':'🚌'})
        ready = batches.plan('banana', GLYPHS, ['😀'], {'1':'skip'}, {'1':'🌈'})
        self.assertEqual(ready['plan']['stickers'][0]['emoji'], '🌈')
        self.assertNotIn('😀', [s['emoji'] for s in ready['plan']['stickers']])

    def test_duplicate_within_batch_has_unique_keys_and_survives_preview_validation(self):
        glyphs = ['😀', '😀', *GLYPHS[2:]]
        self.assertFalse(batches.plan('banana', glyphs)['ready'])
        plan = batches.plan('banana', glyphs, choices={'2':'add'}, pack_id='P1')['plan']
        restored = tasks.plan_from_preview(plan, 'banana')
        self.assertEqual(restored['slots']['emoji_batch'], plan['slots']['emoji_batch'])
        self.assertEqual([s['emoji'] for s in restored['stickers']], glyphs)
        self.assertEqual(len({s['key'] for s in restored['stickers']}), 9)
        forged = json.loads(json.dumps(plan))
        forged['slots']['emoji_batch']['entries'][0]['emoji'] = '🐶'
        with self.assertRaises(pl.PipelineError):
            tasks.plan_from_preview(forged, 'banana')


class ChatTests(Base):
    def target(self):
        self.seed(subject='banana')
        for sticker, glyph in zip(self.tools.gens['G012']['stickers'], GLYPHS):
            sticker['emoji'] = [glyph]
        self.tools.plans['G012'] = {'stickers': self.tools.gens['G012']['stickers']}
        self.tools.pack_list = [
            {'id':'P1', 'name':'Banana', 'stickers':[{'id':'s1','emoji':'😀','source':{'generation':'G012','index':1}}]},
            {'id':'P2', 'name':'Other', 'stickers':[{'id':'s2','emoji':'🌈','source':{'generation':'G128','index':1}}]}]

    def test_pack_scoped_decisions_persist_and_no_generation_starts_while_unresolved(self):
        self.target()
        glyphs = ['😀', '🌈', '💎', '🏆', '🔑', '🍕', '🌻', '🎸', '⚽']
        result = self.say(' '.join(glyphs))
        self.assertIn('already used', result['text'])
        self.assertFalse(self.tools.sent)
        self.assertEqual(self.sess()['pending']['pack_id'], 'P1')
        self.say('', action={'type':'confirm'})
        self.assertFalse(self.tools.sent)
        self.say('', action={'type':'emoji_batch_choice', 'to':'add', 'indexes':[1]})
        approved = self.sess()['pending']['plan']
        self.assertEqual(approved['slots']['emoji_batch']['entries'][0]['version'], 2)
        self.assertEqual([s['emoji'] for s in approved['stickers']], glyphs)
        self.say('', action={'type':'confirm'})
        self.assertEqual(self.tools.sent_plans[-1], approved)

    def test_skip_holds_nine_cells_and_rejects_repeated_replacement(self):
        self.target()
        self.say('😀 🌈 💎 🏆 🔑 🍕 🌻 🎸 ⚽')
        self.say('', action={'type':'emoji_batch_choice', 'to':'skip', 'indexes':[1]})
        result = self.say('🚌')
        self.assertIn('already used', result['text'])
        self.assertFalse(self.tools.sent)
        result = self.say('🦄')
        self.assertEqual(self.sess()['pending']['type'], 'create')
        plan = self.sess()['pending']['plan']
        self.assertEqual(plan['stickers'][0]['emoji'], '🦄')
        self.assertEqual(len(plan['stickers']), 9)

    def test_multiple_related_packs_require_a_target_and_animation_stage_is_preserved(self):
        self.target()
        self.tools.pack_list[1]['stickers'][0]['source']['generation'] = 'G012'
        session = self.sess(); session['settings']['stage']='animation'; self.store.save(session)
        self.say('😀 🌈 💎 🏆 🔑 🍕 🌻 🎸 ⚽')
        self.assertIn('choose_pack', self.sess()['pending'])
        self.say('', action={'type':'emoji_batch_pack','to':'P1'})
        self.say('', action={'type':'emoji_batch_choice','to':'overwrite','indexes':[1]})
        pending = self.sess()['pending']
        self.assertEqual(pending['type'], 'creator')
        self.assertEqual(pending['end'], 'animation')
        self.assertEqual(pending['plan']['slots']['emoji_batch']['pack_id'], 'P1')
        self.assertFalse(self.tools.sent)


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name); self.lib = Library(self.out)
        self.pid = self.lib.create_pack('Banana')['id']
        self.other = self.lib.create_pack('Other')['id']
        self.old = self.lib.add_bytes(self.pid, b'old', 'png', 'Banana happy', emoji='😀', source={'generation':'G127','index':1})
        self.lib.add_bytes(self.other, b'unrelated', 'png', 'Other happy', emoji='😀', source={'generation':'G127','index':1})
        self.dir = self.out/'G128'; self.dir.mkdir(); (self.dir/'new.png').write_bytes(b'new'); (self.dir/'new.webm').write_bytes(b'animated')
        self.state={'generation_id':'G128','stickers':[{'key':'banana_happy','emoji':'😀','name':'new','status':'READY','anim_status':'READY','png':'new.png','webm':'new.webm'}]}
        patch=mock.patch.object(pl,'state',return_value=self.state); patch.start(); self.addCleanup(patch.stop)
        patch=mock.patch.object(pl,'gen_dir',return_value=self.dir); patch.start(); self.addCleanup(patch.stop)

    def test_overwrite_preserves_id_order_and_other_pack_and_undo_restores_source(self):
        self.lib.update_pack(self.pid, cover=self.old['id'])
        result=self.lib.add_emoji_from_generation(self.out,self.pid,128,1,'static','overwrite')
        self.assertEqual(result['id'],self.old['id'])
        self.assertEqual(result['source']['generation'],'G128')
        self.assertEqual((self.lib.files/result['file']).read_bytes(),b'new')
        self.assertEqual(len(self.lib.snapshot()['packs'][0]['stickers']),1)
        other=self.lib.snapshot()['packs'][1]['stickers'][0]
        self.assertEqual((self.lib.files/other['file']).read_bytes(),b'unrelated')
        undone=self.lib.undo_replace(self.pid,self.old['id'])
        self.assertEqual(undone['source']['generation'],'G127')
        self.assertEqual((self.lib.files/undone['file']).read_bytes(),b'old')

    def test_numbered_add_is_idempotent_and_animation_upgrades_the_same_variant(self):
        variant=self.lib.add_emoji_from_generation(self.out,self.pid,128,1,'static','add')
        self.assertEqual(variant['emoji_version'],2)
        self.assertTrue(variant['name'].endswith(' 02'))
        again=self.lib.add_emoji_from_generation(self.out,self.pid,128,1,'static','add')
        self.assertEqual(again['id'],variant['id'])
        animation=self.lib.add_emoji_from_generation(self.out,self.pid,128,1,'animated','add','replace')
        self.assertEqual(animation['id'],variant['id'])
        self.assertEqual(animation['emoji_version'],2)
        self.assertEqual(len(self.lib.snapshot()['packs'][0]['stickers']),2)

    def test_new_duplicates_and_foreign_targets_are_refused_before_delivery_or_spending(self):
        with self.assertRaises(LibraryError):
            self.lib.add_emoji_from_generation(self.out,self.pid,128,1,'static','new')
        metadata=batches.plan('banana',GLYPHS,pack_id=self.pid)['plan']['slots']['emoji_batch']
        with self.assertRaises(pl.PipelineError):
            batches.validate_target(metadata,self.lib,'local')
        with self.assertRaises(pl.PipelineError):
            batches.validate_target(metadata,self.lib,'someone-else')

    def test_json_preview_checks_owned_pack_and_returns_a_runnable_plan_only_after_choices(self):
        from stickerloom.console.server import make_handler
        handler=object.__new__(make_handler(SimpleNamespace(lib=self.lib)))
        handler.path='/api/plan'; handler.user={'id':'local','role':'owner'}; handler._json=mock.Mock()
        handler._body=mock.Mock(return_value={'emojis':GLYPHS,'pack_id':self.pid,'prompt':'banana'})
        handler._post()
        status, result=handler._json.call_args.args
        self.assertEqual(status,200); self.assertFalse(result['ready']); self.assertNotIn('plan',result)
        handler._body.return_value['decisions']={'1':'add'}
        handler._post()
        self.assertTrue(handler._json.call_args.args[1]['ready'])
        handler.user={'id':'another','role':'member'}
        with self.assertRaises(pl.PipelineError) as error:
            handler._post()
        self.assertEqual(error.exception.code,404)

    def test_delivery_uses_the_planned_pack_and_rejects_an_unrelated_pack_before_approving(self):
        metadata=batches.plan('banana',GLYPHS,['😀'],{'1':'overwrite'},pack_id=self.pid)['plan']['slots']['emoji_batch']
        res={**self.state,'slots':{'emoji_batch':metadata},'owner':'local','reviews':{},
             'stickers':[{**self.state['stickers'][0],'index':1,'anim_status':'NOT_REQUESTED','review':{'still':'APPROVED','anim':'NONE'}}]}
        with mock.patch.object(pl,'read_result',return_value=res), mock.patch.object(gates,'_ensure_plan',return_value=res) as approve, mock.patch.object(gates,'_approve_stills'), mock.patch.object(pl,'write_result'), mock.patch.object(pl,'emit'):
            with self.assertRaises(pl.PipelineError):
                gates.quick_add(self.out,128,self.lib,self.other,owner='local')
            approve.assert_not_called()
            result=gates.quick_add(self.out,128,self.lib,owner='local')
            self.assertEqual(result['pack_id'],self.pid)
            self.assertEqual(result['added'],1)
            self.assertEqual(self.lib.snapshot()['packs'][0]['stickers'][0]['id'],self.old['id'])
