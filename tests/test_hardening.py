"""Backlog hardening (HANDOFF "Backlog"): each test failed before its fix and passes after."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from stickerloom.engine import video as V
from stickerloom.engine.config import EngineConfig as Config


class CrfFitIsDeterministic(unittest.TestCase):
    """VP9 size is not strictly monotonic in crf, so where the walk starts decides the result.
    The start used to be a module global that parallel workers mutated: the same clip could
    get a different crf depending on which clip another thread had just fitted."""

    def _fit(self, sizes, **kw):
        cfg = Config()
        budget = cfg.video_max_bytes
        ladder = cfg.crf_ladder
        calls = []

        def fake_encode(frames, fps, crf, path):
            calls.append(crf)
            Path(path).write_bytes(b"x" * sizes[crf](budget))

        with mock.patch.object(V.ff, "encode_webm", fake_encode), tempfile.TemporaryDirectory() as td:
            return V._encode_fit([np.zeros((4, 4, 4), np.uint8)], 30, cfg, Path(td) / "a.webm", **kw), ladder, calls

    def test_same_clip_same_crf_whatever_was_fitted_before(self):
        cfg = Config()
        ladder = cfg.crf_ladder
        # a non-monotonic size curve: the first rung fits, the second does not, the third does again
        def curve(budget):
            return {c: (budget - 10 if k in (0, 2, 3) else budget + 10) for k, c in enumerate(ladder)}

        sizes = {c: (lambda b, c=c: curve(b)[c]) for c in ladder}
        first, _, _ = self._fit(sizes)
        # a different clip fitted in between (what another worker thread does)
        other = {c: (lambda b: b - 10) for c in ladder}
        self._fit(other)
        second, _, _ = self._fit(sizes)
        self.assertEqual(first[0], second[0])
        self.assertEqual(first[2], second[2])

    def test_no_module_global_hint(self):
        self.assertFalse(hasattr(V, "_CRF_HINT"))


class OneWriterOfResults(unittest.TestCase):
    """`stickerloom recheck` while the server runs could interleave a read and a write of result.json and lose a decision."""

    def test_second_writer_is_refused_and_release_frees_it(self):
        from stickerloom.runtime.writer_lock import WriterBusy, WriterLock
        with tempfile.TemporaryDirectory() as td:
            first = WriterLock(Path(td), "serve").acquire()
            with self.assertRaises(WriterBusy) as cm:
                WriterLock(Path(td), "recheck").acquire()
            self.assertIn("serve", str(cm.exception))
            first.release()
            WriterLock(Path(td), "recheck").acquire().release()

    def test_another_process_is_refused(self):
        import subprocess
        import sys
        from stickerloom.runtime.writer_lock import WriterLock
        code = ("import sys\nfrom pathlib import Path\nfrom stickerloom.runtime.writer_lock import WriterBusy, WriterLock\n"
                "try:\n    WriterLock(Path(sys.argv[1]), 'child').acquire()\nexcept WriterBusy:\n    sys.exit(3)\n")
        with tempfile.TemporaryDirectory() as td:
            with WriterLock(Path(td), "serve"):
                r = subprocess.run([sys.executable, "-c", code, td], cwd=str(Path(__file__).resolve().parent.parent))
                self.assertEqual(r.returncode, 3)
            r = subprocess.run([sys.executable, "-c", code, td], cwd=str(Path(__file__).resolve().parent.parent))
            self.assertEqual(r.returncode, 0)

    def test_server_holds_it_and_a_second_server_on_the_same_out_refuses(self):
        from stickerloom.console.server import serve
        from stickerloom.runtime.writer_lock import WriterBusy
        with tempfile.TemporaryDirectory() as td:
            out, inp = Path(td) / "out", Path(td) / "in"
            inp.mkdir()
            srv, c = serve(out, inp, 0, block=False)
            try:
                with self.assertRaises(WriterBusy):
                    serve(out, inp, 0, block=False)
            finally:
                c.release_writer()
                srv.server_close()


class ServerRefusesForeignPages(unittest.TestCase):
    """A web page the owner visits could POST to 127.0.0.1 (trash, the Telegram config, bulk delete) and a
    DNS-rebinding page could read it. The server answers only its own Host and Origin."""

    @classmethod
    def setUpClass(cls):
        from stickerloom.console.server import serve
        cls.tmp = Path(tempfile.mkdtemp())
        (cls.tmp / "in").mkdir()
        cls.srv, cls.c = serve(cls.tmp / "out", cls.tmp / "in", 0, block=False)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, method, path, headers=None, body=None):
        import http.client
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        h.request(method, path, body, dict(headers or {}))
        r = h.getresponse()
        raw = r.read()
        h.close()
        return r.status, raw

    def test_own_origin_and_no_origin_pass(self):
        self.assertEqual(self.req("GET", "/api/telegram")[0], 200)
        self.assertEqual(self.req("GET", "/api/telegram", {"Origin": f"http://127.0.0.1:{self.port}"})[0], 200)
        self.assertEqual(self.req("GET", "/api/telegram", {"Host": f"localhost:{self.port}"})[0], 200)

    def test_foreign_host_refused(self):
        s, raw = self.req("GET", "/api/telegram", {"Host": "evil.example"})
        self.assertEqual(s, 403)
        self.assertIn("Host", json.loads(raw)["error"])

    def test_foreign_origin_cannot_post(self):
        for origin in ("http://evil.example", "http://localhost:3000", "null"):
            s, _ = self.req("POST", "/api/library/packs", {"Origin": origin, "Content-Type": "application/json"}, "{}")
            self.assertEqual(s, 403, origin)

    def test_sec_fetch_cross_site_cannot_post(self):
        s, _ = self.req("POST", "/api/library/packs", {"Sec-Fetch-Site": "cross-site", "Content-Type": "application/json"}, "{}")
        self.assertEqual(s, 403)

    def test_an_unexpected_exception_is_a_json_500_and_never_shows_a_path(self):
        """only typed errors were caught, so an OSError dropped the socket (and printed a traceback nobody reads)."""
        import contextlib
        import io
        from stickerloom.generation import jobs
        console = io.StringIO()
        with mock.patch.object(jobs, "list", side_effect=OSError(r"cannot read D:\secret\keys\token.txt")), contextlib.redirect_stderr(console):
            s, raw = self.req("GET", "/api/jobs")
        self.assertEqual(s, 500)
        self.assertEqual(json.loads(raw)["error"], "internal error")
        self.assertNotIn(b"secret", raw)
        self.assertRegex(console.getvalue(), r"500 GET /api/jobs \[[A-Za-z0-9._-]+\]: OSError")   # the detail is on the server console, with the request id ...
        self.assertIn("token.txt", console.getvalue())
        self.assertNotIn("secret", console.getvalue())                                         # ... with the absolute path scrubbed to its file name
        self.assertEqual(self.req("GET", "/api/jobs")[0], 200)                                  # the server is still up and answers the next request

    def test_every_answer_is_json_even_for_a_method_the_server_does_not_have(self):
        s, raw = self.req("PUT", "/api/jobs", {"Content-Type": "application/json"}, "{}")
        self.assertEqual(s, 501)
        self.assertIn("error", json.loads(raw))                                                  # it used to be an HTML page


class AuditFindings(unittest.TestCase):
    """Triage of the 2026-10-02 independent report: the findings that held up are fixed here, the ones that did not are pinned."""

    def test_write_through_failures_are_counted_and_visible(self):
        from unittest import mock
        from stickerloom.store import db, sync
        before = sync.status()["failed"]
        with tempfile.TemporaryDirectory() as td, mock.patch.object(sync, "enabled", lambda out: True),                 mock.patch.object(db, "available", lambda: True), mock.patch.object(db, "connect", side_effect=RuntimeError("boom")):
            self.assertFalse(sync.sync_model_call(Path(td), json.dumps({"kind": "X"})))
        st = sync.status()
        self.assertEqual(st["failed"], before + 1)
        self.assertIn("boom", st["last_error"])

    def test_health_snapshot_reports_every_part_and_never_raises(self):
        from stickerloom.runtime import health
        with tempfile.TemporaryDirectory() as td:
            h = health.snapshot(Path(td))
        for k in ("database", "redis", "models", "providers", "storage", "warnings", "ok"):
            self.assertIn(k, h)
        self.assertIn("write_through", h["database"])
        self.assertIn(h["redis"]["engine"], ("redis", "memory"))

    def test_verifier_has_44_checks_over_9_stages(self):
        """The report counted 36 by hand; the catalogue the doctor prints is the truth."""
        from stickerloom.engine import verify
        self.assertEqual((sum(len(v) for v in verify.CATALOGUE.values()), len(verify.CATALOGUE)), (44, 9))

    def test_a_symlink_inside_out_cannot_reach_outside_it(self):
        """The server resolves the path, THEN checks the root: a link that points out of out/ is refused, never followed."""
        import http.client, os
        from stickerloom.console.server import serve
        with tempfile.TemporaryDirectory() as td:
            out, outside = Path(td) / "out", Path(td) / "secret"
            (out / "G001").mkdir(parents=True)
            outside.mkdir()
            (outside / "x.txt").write_text("secret", encoding="utf-8")
            paths = ["/out/../secret/x.txt", "/out/G001/%2e%2e/%2e%2e/secret/x.txt"]
            try:
                os.symlink(outside, out / "G001" / "link", target_is_directory=True)
                paths.append("/out/G001/link/x.txt")
            except (OSError, NotImplementedError):
                pass                                    # this account cannot create symlinks: the traversal paths still run
            (Path(td) / "in").mkdir()
            srv, c = serve(out, Path(td) / "in", 0, block=False)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                for path in paths:
                    h = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
                    h.request("GET", path)
                    r = h.getresponse()
                    body = r.read()
                    h.close()
                    self.assertNotEqual(r.status, 200, path)
                    self.assertNotIn(b"secret", body, path)
            finally:
                srv.shutdown()
                c.release_writer()
                srv.server_close()


if __name__ == "__main__":
    unittest.main()
