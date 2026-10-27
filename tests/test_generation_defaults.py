"""New-plan defaults and independent local video timelines; no providers."""
import copy
import tempfile
import unittest
from pathlib import Path

from stickerloom.generation import prompter, expander, tasks
from stickerloom.agent.memory import SessionStore
from stickerloom.runtime.cache import Cache


class GenerationDefaults(unittest.TestCase):
    def test_new_claim_default_is_four_cells(self):
        from stickerloom.generation import claims
        with tempfile.TemporaryDirectory() as td:
            row = claims.claim_next(Path(td), 'generic emojis')
            self.assertEqual(row['claim']['grid'], [2, 2])
            self.assertEqual(len(row['claim']['plan']['stickers']), 4)

    def test_new_entrypoints_default_to_four_cells_but_explicit_nine_survives(self):
        for build in (prompter.expand, expander.expand, tasks.preview):
            with self.subTest(build=build.__module__):
                plan = build('generic emojis')
                self.assertEqual(plan['grid'], [2, 2])
                self.assertEqual(len(plan['stickers']), 4)
        with tempfile.TemporaryDirectory() as td:
            store = SessionStore(Path(td), Cache(force_memory=True))
            self.assertEqual(store.create()['settings']['grid'], '2x2')
        self.assertEqual(len(prompter.expand('generic emojis', (3, 3))['stickers']), 9)

    def test_spatial_narratives_run_concurrently(self):
        plan = prompter.expand('generic emojis', (2, 2))
        text = plan['video_prompt']
        for position in ('Top-left', 'Top-right', 'Bottom-left', 'Bottom-right'):
            line = next((line for line in text.splitlines() if line.startswith(position)), '')
            self.assertIn('(cell ', line)
            self.assertIn(', then ', line)
            self.assertNotIn('Action 1:', line)
        self.assertIn('concurrently', text)
        self.assertIn('one connected sequence for that cell alone', text)
        self.assertIn('Never apply one cell', text)
        self.assertEqual(plan['slots']['video_template'], {'id': 'video_independent', 'version': 2})
        self.assertNotIn('Faces only: no bodies, no hands.', text, 'the detached gesture must not conflict with a blanket hand ban')

    def test_three_by_three_positions_third_gesture_and_loop_are_preserved(self):
        plan = prompter.expand('generic emojis', (3, 3))
        slots = plan['slots']
        slots['loop'] = True
        slots['cells'] = [
            {'pos': 3, 'label': 'sad and drooping face with a single tear',
             'motion': 'cries softly then starts sobbing in a cute way, finally wipes away the tear with a small detached hand'},
            {'pos': 4, 'label': 'playful wink', 'motion': 'smiles while its eyes sparkle then winks'},
        ]
        text = prompter.render_plan(slots, plan['template_id'], plan['template_version'])['video_prompt']
        lines = [line for line in text.splitlines() if ' (cell ' in line]
        self.assertTrue(lines[0].startswith('Top-right (cell 3): The sad and drooping face'))
        self.assertIn('cries softly, then starts sobbing in a cute way, and finally wipes away the tear', lines[0])
        self.assertIn('emerges from behind the face', lines[0])
        self.assertIn('withdraws behind it', lines[0])
        self.assertIn('no visible arm connecting it to a body', lines[0])
        self.assertTrue(lines[1].startswith('Mid-left (cell 4):'))
        self.assertIn('smiles while its eyes sparkle, then winks', lines[1])
        self.assertNotIn('detached hand', lines[1], 'the other cell must not inherit the gesture')
        self.assertTrue(all('returns smoothly to its exact first-frame' in line for line in lines))
        self.assertNotIn('(cell 2)', text, 'unoccupied cells have no sequence')
        slots['loop'] = False
        no_loop = prompter.render_plan(slots, plan['template_id'], plan['template_version'])['video_prompt']
        self.assertNotIn('returns smoothly to its exact first-frame', no_loop)

    def test_saved_independent_v1_keeps_numbered_two_action_text(self):
        plan = prompter.expand('generic emojis')
        plan['slots']['video_template']['version'] = 1
        plan['slots']['cells'] = [{'pos': 2, 'label': 'happy', 'motion': 'smiles then winks then laughs'}]
        text = prompter.render_plan(plan['slots'], plan['template_id'], plan['template_version'])['video_prompt']
        self.assertIn('Top-right (cell 2): State: happy. Action 1: smiles. Then Action 2: winks.', text)
        self.assertNotIn('laughs', text)

    def test_saved_legacy_slots_rebuild_the_unchanged_short_template(self):
        plan = prompter.expand('generic emojis', (2, 2))
        slots = copy.deepcopy(plan['slots'])
        slots.pop('video_template', None)
        old = prompter.render_plan(slots, plan['template_id'], plan['template_version'])['video_prompt']
        self.assertTrue(old.startswith('Animate this 2x2 sheet of emojis'))
        self.assertNotIn('Action 1:', old)
        self.assertNotIn('concurrently', old)
        self.assertEqual(prompter.render_plan(slots, plan['template_id'], plan['template_version'])['video_prompt'], old)

    def test_loop_choice_and_custom_text_still_win(self):
        plan = tasks.preview('generic emojis', loop=True)
        self.assertIn('End on the starting pose', plan['video_prompt'])
        plan['custom'] = {'video_prompt': 'My exact approved wording'}
        self.assertEqual(prompter.apply_custom(plan)['video_prompt'], 'My exact approved wording')
        self.assertNotIn('End on the starting pose', tasks.preview('generic emojis', loop=False)['video_prompt'])
