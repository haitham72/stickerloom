"""The agentic creator through the REAL console server: chat message -> plan -> one click -> fake Higgsfield sheet -> cut and check -> approve -> pack -> fake Telegram.
No provider, no language model, no network: the FakeCLI of tests/test_live.py and the fake Bot API of tests/fake_telegram.py."""
import http.client
import json
import os
import threading
import time
import unittest
from unittest import mock
from pathlib import Path

import numpy as np
from PIL import Image

from stickerloom.console.server import serve
from stickerloom.engine.config import EngineConfig
from stickerloom.services import telegram as tg
from tests.fake_telegram import TOKEN, USER, FakeServer
from tests.test_live import Base, png_bytes
from tests.test_live import shape_sheet
from tests import synth
from stickerloom.flow import pipeline as pl
from stickerloom.generation import tasks


class CreatorThroughTheServer(Base):
    def setUp(self):
        super().setUp()
        self.fake = FakeServer().__enter__()
        self.env = {k: os.environ.get(k) for k in ("STICKERLOOM_TELEGRAM_API", "STICKERLOOM_TELEGRAM_TOKEN", "STICKERLOOM_TELEGRAM_USER")}
        os.environ["STICKERLOOM_TELEGRAM_API"] = self.fake.url
        for k in ("STICKERLOOM_TELEGRAM_TOKEN", "STICKERLOOM_TELEGRAM_USER"):
            os.environ.pop(k, None)
        self.srv, self.c = serve(self.out, self.tmp / "in", 0, cfg=EngineConfig(), block=False)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.files["png"] = png_bytes(shape_sheet(1200, [(x, y) for y in (200, 600, 1000) for x in (200, 600, 1000)]))

    def tearDown(self):
        self.c.wait_jobs(90)
        self.c.wait_chat(30)
        self.srv.shutdown()
        self.fake.__exit__()
        for k, v in self.env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        super().tearDown()

    def req(self, method, path, body=None):
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
        r = h.getresponse()
        data = r.read()
        h.close()
        try:
            return r.status, json.loads(data)
        except ValueError:
            return r.status, data

    def until(self, fn, what, timeout=180):
        end = time.time() + timeout
        while time.time() < end:
            v = fn()
            if v:
                return v
            time.sleep(0.3)
        self.fail("timeout: " + what)

    def say(self, sid, text="", action=None):
        s, r = self.req("POST", f"/api/chat/sessions/{sid}/messages", {"text": text, "action": action} if action else {"text": text})
        self.assertEqual(s, 202, r)
        return self.until(lambda: (lambda j: j if not j["working"] else None)(self.req("GET", f"/api/chat/sessions/{sid}")[1]), "the turn")

    def test_one_click_from_a_request_to_a_pack_on_telegram(self):
        tg.save_config(self.out, TOKEN, USER)
        s, sess = self.req("POST", "/api/chat/sessions", {"settings": {"ai": False}})
        sid = sess["id"]
        s, r = self.req("POST", f"/api/chat/sessions/{sid}/settings", {"creator": {"on": True, "scope": "images", "bypass": True}, "allow_vlm": False})
        self.assertEqual(r["settings"]["creator"], {"on": True, "scope": "images", "bypass": True})
        j = self.say(sid, "make me owl stickers")
        plan = j["messages"][-1]["cards"][0]
        self.assertEqual((plan["type"], plan["creator"]["scope"]), ("plan", "images"))
        self.assertEqual(len([c for c in self.cli.calls if c[:2] == ["generate", "create"]]), 0, "nothing is spent before the click")
        self.say(sid, action={"type": "confirm"})
        run = self.until(lambda: (lambda j: j["creator_run"] if (j.get("creator_run") or {}).get("status") in ("done", "stopped", "failed") else None)(
            self.req("GET", f"/api/chat/sessions/{sid}")[1]), "the creator run", 240)
        self.assertEqual((run["status"], run["step"]), ("done", "done"), run.get("stop") or run["log"])
        self.assertEqual(len([c for c in self.cli.calls if c[:2] == ["generate", "create"]]), 1, "one paid sheet, nothing else")
        self.assertEqual(len(self.fake.f.sets), 1)
        stickers = next(iter(self.fake.f.sets.values()))["stickers"]
        self.assertEqual(len(stickers), 9)
        lib = self.req("GET", "/api/library")[1]
        self.assertEqual([len(p["stickers"]) for p in lib["packs"]], [9])
        self.assertRegex(lib["packs"][0]["stickers"][0]["file"], r"^G001-[a-z0-9_]+-\d{8}T\d{6}/img-", "library files are grouped per batch (its labelled folder name) and carry the new names")
        last = self.req("GET", f"/api/chat/sessions/{sid}")[1]["messages"][-1]
        self.assertIn("t.me/addstickers", last["text"])

    def test_cut_batches_post_one_followup_and_one_confirmation_starts_two_more(self):
        _, sess = self.req('POST', '/api/chat/sessions', {'settings': {'ai': False, 'stage': 'emojis', 'allow_vlm': False}})
        sid = sess['id']
        self.say(sid, 'make me falcon stickers')
        self.say(sid, action={'type': 'confirm'})
        self.c.wait_jobs(60)
        def followup():
            j = self.req('GET', f'/api/chat/sessions/{sid}')[1]
            return j if any(c.get('action') == 'batch_more' for m in j['messages'] for c in m.get('chips', [])) else None
        j = self.until(followup, 'the cut-batch followup')
        messages = [m for m in j['messages'] if any(c.get('action') == 'batch_more' for c in m.get('chips', []))]
        self.assertEqual(len(messages), 1)
        self.assertEqual([c.get('action') for c in messages[0]['chips']], ['regenerate', 'batch_more', 'batch_more', 'batch_more'])
        self.assertEqual([c.get('to') for c in messages[0]['chips']], [None, 2, 3, 4])
        for _ in range(3):
            polled = self.req('GET', f'/api/chat/sessions/{sid}')[1]
            self.assertEqual(len(polled['messages']), len(j['messages']))
        self.say(sid, action={'type': 'batch_more', 'to': 3})
        creates = lambda: [c for c in self.cli.calls if c[:2] == ['generate', 'create']]
        self.assertEqual(len(creates()), 1)
        self.say(sid, action={'type': 'confirm'})
        self.c.wait_jobs(60)
        j = self.until(lambda: (lambda s: s if len(s['pack']['made']) == 3 and all(r.get('generation') for r in s['pack']['made']) else None)(
            self.req('GET', f'/api/chat/sessions/{sid}')[1]), 'the three batches')
        self.assertEqual(len(creates()), 3)
        rows = j['pack']['made']
        self.assertEqual([r['no'] for r in rows], [1, 2, 3])
        self.assertEqual(tasks.session_state(self.out, [r['generation'] for r in rows])['existing'], len(rows))

    def test_creator_driver_finishes_two_batches_in_order_at_animation_and_export(self):
        tg.save_config(self.out, TOKEN, USER)
        original_download = self._download
        def download(url, dest):
            if not url.endswith('.mp4'):
                return original_download(url, dest)
            ticket = url.rsplit('/', 1)[-1].split('.')[0]
            creates = [c for c in self.cli.calls if c[:2] == ['generate', 'create']]
            call = creates[int(ticket.rsplit('-', 1)[-1])-1]
            start = Path(call[call.index('--start-image')+1])
            for gid in pl.list_ids(self.out):
                result = pl.read_result(self.out, gid)
                base = pl.gen_dir(self.out, gid)
                for v in result.get('video_sheets', []):
                    if (base / v['file']).resolve() == start.resolve():
                        sheet = np.array(Image.open(start).convert('RGB'))
                        layout = json.loads((base / v['layout']).read_text())
                        Path(dest).parent.mkdir(parents=True, exist_ok=True)
                        synth.make_layout_video(Path(dest), sheet, layout, size=600, frames=45)
                        import hashlib
                        data = Path(dest).read_bytes()
                        return hashlib.sha256(data).hexdigest(), len(data)
            self.fail('No prepared layout for the fake video')
        from stickerloom.generation import higgsfield
        with mock.patch.object(higgsfield, 'download', download):
            for end in ['animation', 'export']:
                with self.subTest(end=end):
                    _, sess = self.req('POST', '/api/chat/sessions', {'settings': {'ai': False, 'stage': 'prompt', 'allow_vlm': False, 'creator': {'bypass': True}}})
                    sid = sess['id']
                    self.req('POST', f'/api/chat/sessions/{sid}/settings', {'stage': 'prompt'})
                    self.say(sid, 'make me falcon stickers')
                    self.req('POST', f'/api/chat/sessions/{sid}/settings', {'stage': end})
                    planned = self.say(sid, action={'type': 'batch_more', 'to': 2})
                    self.assertEqual((planned.get('pending') or {}).get('type'), 'batch_more', planned['messages'][-1]['text'])
                    self.assertEqual(planned['pending']['stage'], end)
                    # Use the blocking real creator driver, instead of its background entry point.
                    with mock.patch.object(self.c, 'drive_creator'):
                        self.say(sid, action={'type': 'confirm'})
                    store, _, agent, _ = self.c.chat_parts(None, sid)
                    begun = store.load(sid)
                    self.assertEqual(len(begun['creator_queue']), 1)
                    self.assertEqual(begun['creator_run']['end'], end)
                    self.c.drive_creator(None, sid, block=True)
                    j = store.load(sid)
                    self.assertEqual(j['creator_run']['status'], 'done', j['creator_run'].get('stop'))
                    self.assertEqual(j['creator_run']['end'], end)
                    self.assertFalse(j.get('creator_queue'))
                    rows = j['pack']['made']
                    self.assertEqual([r['no'] for r in rows], [1, 2])
                    completed = [m for m in j['messages'] if m['text'].startswith('Done:')]
                    self.assertEqual(len(completed), 2)
                    self.assertFalse(completed[0].get('chips'))
                    self.assertEqual([c['action'] for c in completed[-1]['chips']], ['regenerate', 'batch_more', 'batch_more'])
                    self.assertEqual([c.get('to') for c in completed[-1]['chips']], [None, 3, 4])
                    self.assertEqual(tasks.session_state(self.out, [r['generation'] for r in rows])['existing'], 2)
                    if end == 'animation':
                        self.assertFalse(self.fake.f.sets)
                    else:
                        self.assertEqual(len(self.fake.f.sets), 2)
                        self.assertEqual(sorted(len(p['stickers']) for p in self.fake.f.sets.values()), [9, 9])
                        self.assertEqual(len({p['title'] for p in self.fake.f.sets.values()}), 2)


if __name__ == "__main__":
    unittest.main()
