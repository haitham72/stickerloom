"""Phase 5A/5C on the real server: SSE with replay, idempotency keys, signed asset links, the optional API token. Redis is replaced by a private
in-memory cache (the suite never writes to the developer's Redis); Higgsfield is never reached."""
import http.client
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from stickerloom.flow import pipeline as pl
from stickerloom.generation import higgsfield
from stickerloom.runtime import cache as cachemod
from stickerloom.console.server import serve
from stickerloom.engine.config import EngineConfig
from tests.test_console import build_inputs


class Phase5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mem = cachemod.Cache(force_memory=True)
        cls.patches = [mock.patch.object(cachemod, "default", lambda: cls.mem), mock.patch.object(higgsfield, "available", lambda: False)]
        for p in cls.patches:
            p.start()
        cls.tmp = Path(tempfile.mkdtemp())
        build_inputs(cls.tmp / "in")
        cls.srv, cls.c = serve(cls.tmp / "out", cls.tmp / "in", 0, cfg=EngineConfig(min_sheet_px=256), block=False)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.c.release_writer()
        for p in cls.patches:
            p.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, method, path, body=None, headers=None):
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        hdr = {"Content-Type": "application/json", **(headers or {})}
        h.request(method, path, json.dumps(body) if body is not None else None, hdr)
        r = h.getresponse()
        raw = r.read()
        h.close()
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw

    def sse(self, gid, after=None, want=("sticker_ready",), timeout=60, headers=None):
        """Read frames until one of the wanted events arrives (or time is up); returns [(id, event, payload)]."""
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        hdr = dict(headers or {})
        if after:
            hdr["Last-Event-ID"] = after
        h.request("GET", f"/api/generations/{gid}/events", headers=hdr)
        r = h.getresponse()
        assert r.status == 200 and r.getheader("Content-Type").startswith("text/event-stream")
        frames, buf, end = [], b"", time.time() + timeout
        while time.time() < end:
            try:
                ch = r.fp.readline()
            except (TimeoutError, OSError):
                break                                      # nothing more to read within the window
            if not ch:
                break
            buf += ch
            if ch == b"\n" and buf.strip():
                fields = dict(line.split(": ", 1) for line in buf.decode().strip().split("\n") if ": " in line and not line.startswith(":"))
                if "id" in fields:
                    frames.append((fields["id"], fields["event"], json.loads(fields["data"])))
                    if fields["event"] in want and len([f for f in frames if f[1] == fields["event"]]) >= 3:
                        break
                buf = b""
        h.close()
        return frames

    def test_idempotency_key_creates_one_generation(self):
        s1, a = self.req("POST", "/api/generations", {"prompt": "create a blob for school", "variant": 1}, {"Idempotency-Key": "k-1"})
        self.c.wait_jobs()
        s2, b = self.req("POST", "/api/generations", {"prompt": "create a blob for school", "variant": 1}, {"Idempotency-Key": "k-1"})
        self.assertEqual((s1, s2), (202, 202))
        self.assertEqual(a["id"], b["id"])
        self.assertTrue(b.get("idempotent"))
        self.assertNotIn("idempotent", a)
        s3, c = self.req("POST", "/api/generations", {"prompt": "create a blob for school", "variant": 2}, {"Idempotency-Key": "k-2"})
        self.c.wait_jobs()
        self.assertNotEqual(c["id"], a["id"])

    def test_events_stream_names_replay_and_one_event_per_sticker(self):
        s, r = self.req("POST", "/api/generations", {"prompt": "create a blob for school", "variant": 1})
        gid = r["id"]
        self.c.wait_jobs()
        frames = self.sse(gid, want=("sticker_ready",), timeout=20)
        names = [f[1] for f in frames]
        self.assertIn("generation_started", names)
        self.assertIn("sheet_generated", names)
        ready = [f for f in frames if f[1] == "sticker_ready"]
        self.assertGreaterEqual(len(ready), 3)
        self.assertTrue(all(f[2]["asset_url"].startswith(f"/out/G{gid:03d}/") for f in ready))
        self.assertEqual([f[2]["sticker_id"] for f in ready[:3]], [f"G{gid:03d}/S1", f"G{gid:03d}/S2", f"G{gid:03d}/S3"])
        # reconnect with Last-Event-ID: only what was missed, nothing twice
        mid = frames[len(frames) // 2][0]
        again = self.sse(gid, after=mid, want=("never",), timeout=3)
        ids_before = [f[0] for f in frames]
        self.assertTrue(all(f[0] not in ids_before[: ids_before.index(mid) + 1] for f in again))
        self.assertTrue(len(again) >= 1)

    def test_signed_asset_link_serves_expires_and_cannot_be_forged(self):
        s, r = self.req("POST", "/api/generations", {"prompt": "create a blob for school", "variant": 1})
        gid = r["id"]
        self.c.wait_jobs()
        res = json.loads((pl.out_path(self.tmp / "out", f"G{gid:03d}") / "result.json").read_text(encoding="utf-8"))
        key = f"G{gid:03d}/{next(x['png'] for x in res['stickers'] if x['png'])}"
        s, link = self.req("POST", "/api/assets/sign", {"key": key, "ttl": 5})
        self.assertEqual(s, 200)
        got, body = self.req("GET", link["url"])
        self.assertEqual((got, body[:4]), (200, b"\x89PNG"))
        self.assertEqual(self.req("GET", link["url"][:-3] + "AAA")[0], 403)                 # a forged signature
        self.assertEqual(self.req("POST", "/api/assets/sign", {"key": "../secret"})[0], 404)          # a key outside the store is just "no such asset"
        self.assertEqual(self.req("POST", "/api/assets/sign", {"key": "G999/x.png"})[0], 404)
        s, link2 = self.req("POST", "/api/assets/sign", {"key": key, "ttl": 5})
        from stickerloom.store.assets import LocalAssetStore
        old = LocalAssetStore(self.tmp / "out").sign(key, ttl=5, now=time.time() - 60)       # a link that expired a minute ago
        self.assertEqual(self.req("GET", "/api/assets/" + old)[0], 403)

    def test_api_token_is_required_for_other_callers_not_for_the_studio_page(self):
        os.environ["STICKERLOOM_API_TOKEN"] = "s3cret-token"
        try:
            self.assertEqual(self.req("GET", "/api/health")[0], 401)
            self.assertEqual(self.req("GET", "/api/health", headers={"Authorization": "Bearer wrong"})[0], 401)
            self.assertEqual(self.req("GET", "/api/health", headers={"Authorization": "Bearer s3cret-token"})[0], 200)
            self.assertEqual(self.req("GET", "/api/health", headers={"Sec-Fetch-Site": "same-origin"})[0], 200)      # the Studio's own page
        finally:
            os.environ.pop("STICKERLOOM_API_TOKEN", None)
        self.assertEqual(self.req("GET", "/api/health")[0], 200)                                                      # unset: as before


if __name__ == "__main__":
    unittest.main()
