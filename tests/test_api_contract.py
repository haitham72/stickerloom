"""The HTTP contract's small promises: a version and a request id on every answer, the `/api/v1` prefix, opt-in pagination that reports its total,
`no-store` on a 429, an SSE `retry:` hint, and a JSON access log when asked for."""
import contextlib
import http.client
import io
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from stickerloom.console import openapi
from stickerloom.console.server import serve
from stickerloom.runtime import cache as cachemod


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        (cls.tmp / "in").mkdir()
        cls.mem = cachemod.Cache(force_memory=True)
        cls.patch = mock.patch.object(cachemod, "default", lambda: cls.mem)
        cls.patch.start()
        out = cls.tmp / "out"
        for gid in (1, 2, 3):                                                                    # three small batches, newest id last
            d = out / f"G{gid:03d}"
            d.mkdir(parents=True)
            (d / "result.json").write_text(json.dumps({"id": gid, "generation_id": f"G{gid:03d}", "prompt": f"cat {gid}", "stage": "sliced", "error": None, "grid": [3, 3], "stickers": [],
                                                       "source": {"subject": "cat", "variant": gid}}), encoding="utf-8")
        cls.srv, cls.c = serve(out, cls.tmp / "in", 0, block=False)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.c.release_writer()
        cls.srv.server_close()
        cls.patch.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, path, headers=None, method="GET", body=None):
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        h.request(method, path, body, dict(headers or {}))
        r = h.getresponse()
        raw = r.read()
        hdr = {k.lower(): v for k, v in r.getheaders()}
        h.close()
        try:
            return r.status, json.loads(raw), hdr
        except ValueError:
            return r.status, raw, hdr


class HeaderTests(Base):
    def test_every_answer_carries_the_api_version_and_a_request_id(self):
        for path, code in (("/api/health", 200), ("/api/generations", 200), ("/api/nope", 404), ("/api/generations/99", 404)):
            s, body, h = self.get(path)
            self.assertEqual(s, code, path)
            self.assertEqual(h["x-api-version"], openapi.VERSION)
            self.assertRegex(h["x-request-id"], r"^[A-Za-z0-9._-]{8,64}$")
        s, _, h = self.get("/api/generations", method="PUT", body=b"{}")                          # even the base class's error page
        self.assertEqual((s, h["x-api-version"]), (501, openapi.VERSION))

    def test_a_safe_incoming_request_id_is_echoed_and_an_unsafe_one_is_replaced(self):
        _, _, h = self.get("/api/health", {"X-Request-Id": "trace-42.abc_DEF"})
        self.assertEqual(h["x-request-id"], "trace-42.abc_DEF")
        _, _, h = self.get("/api/health", {"X-Request-Id": "bad id with spaces & <script>"})
        self.assertRegex(h["x-request-id"], r"^[A-Za-z0-9]{8,64}$")
        self.assertNotIn("script", h["x-request-id"])
        ids = {self.get("/api/health")[2]["x-request-id"] for _ in range(5)}
        self.assertEqual(len(ids), 5)                                                            # a fresh id per request

    def test_an_internal_error_answers_with_its_request_id_so_the_console_line_can_be_found(self):
        from stickerloom.generation import jobs
        console = io.StringIO()
        with mock.patch.object(jobs, "list", side_effect=RuntimeError("boom")), contextlib.redirect_stderr(console):
            s, body, h = self.get("/api/jobs", {"X-Request-Id": "find-me-1"})
        self.assertEqual((s, body["error"], body["request_id"]), (500, "internal error", "find-me-1"))
        self.assertIn("find-me-1", console.getvalue())

    def test_the_v1_prefix_is_the_same_api(self):
        a, b = self.get("/api/health"), self.get("/api/v1/health")
        self.assertEqual((a[0], b[0]), (200, 200))
        self.assertEqual(set(a[1]), set(b[1]))
        self.assertEqual(self.get("/api/v1/generations")[1]["generations"], self.get("/api/generations")[1]["generations"])
        self.assertEqual(self.get("/api/v1/nope")[0], 404)
        s, body, _ = self.get("/api/v1/generations/2/history")
        self.assertEqual((s, body["generation_id"]), (200, "G002"))


class PaginationTests(Base):
    def ids(self, q=""):
        s, d, _ = self.get("/api/generations" + q)
        self.assertEqual(s, 200)
        return [g["id"] for g in d["generations"]], d

    def test_without_limit_or_offset_nothing_changes(self):
        ids, d = self.ids()
        self.assertEqual(len(ids), 3)
        self.assertNotIn("total", d)                                                             # the old shape, for everyone who has not asked for pages

    def test_limit_and_offset_slice_and_report_the_total(self):
        all_ids, _ = self.ids()
        page, d = self.ids("?limit=2")
        self.assertEqual(page, all_ids[:2])
        self.assertEqual((d["total"], d["limit"], d["offset"]), (3, 2, 0))
        page, d = self.ids("?limit=2&offset=2")
        self.assertEqual(page, all_ids[2:])
        self.assertEqual((d["total"], d["limit"], d["offset"]), (3, 2, 2))
        page, d = self.ids("?offset=5")
        self.assertEqual((page, d["total"]), ([], 3))                                            # past the end is an empty page, not an error

    def test_bad_values_are_a_400(self):
        for q in ("?limit=0", "?limit=-1", "?limit=999", "?limit=x", "?offset=-1", "?offset=y"):
            self.assertEqual(self.get("/api/generations" + q)[0], 400, q)

    def test_jobs_and_chat_sessions_page_the_same_way(self):
        from stickerloom.generation import jobs
        for _ in range(3):
            jobs.create(self.c.out, "sheet", request={"prompt": "p"})
        s, d, _ = self.get("/api/jobs?limit=2")
        self.assertEqual((s, len(d["jobs"]), d["total"]), (200, 2, 3))
        for _ in range(3):
            self.get("/api/chat/sessions", method="POST", body=b"{}", headers={"Content-Type": "application/json"})
        s, d, _ = self.get("/api/chat/sessions?limit=2&offset=1")
        self.assertEqual((s, len(d["sessions"]), d["total"], d["offset"]), (200, 2, 3, 1))


class StreamAndLogTests(Base):
    def test_the_event_stream_starts_with_a_retry_hint(self):
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h.request("GET", "/api/generations/1/events")
        r = h.getresponse()
        self.assertEqual((r.status, r.getheader("X-API-Version")), (200, openapi.VERSION))
        first = r.readline().decode()                    # through the HTTP layer: uvicorn frames an open stream as chunked (HTTP/1.1), the stdlib server as close-delimited
        h.close()
        self.assertEqual(first.strip(), "retry: 3000")                                         # a browser reconnects after 3 s when the 10 minute stream ends

    def test_the_access_log_is_json_lines_and_off_by_default(self):
        quiet = io.StringIO()
        with contextlib.redirect_stderr(quiet):
            self.get("/api/health")
        self.assertEqual(quiet.getvalue(), "")
        loud = io.StringIO()
        with mock.patch.dict(os.environ, {"STICKERLOOM_ACCESS_LOG": "1"}), contextlib.redirect_stderr(loud):
            self.get("/api/generations/2/history?secret=1", {"X-Request-Id": "log-1"})
            self.get("/api/chat/sessions/S001")
        rows = [json.loads(x) for x in loud.getvalue().splitlines() if x.startswith("{")]
        first = next(r for r in rows if r["request_id"] == "log-1")
        self.assertEqual((first["method"], first["path"], first["status"], first["generation_id"]), ("GET", "/api/generations/2/history", 200, "G002"))
        self.assertNotIn("secret", json.dumps(first))                                            # the query string is never logged
        self.assertGreaterEqual(first["ms"], 0)
        second = next(r for r in rows if r["path"] == "/api/chat/sessions/S001")
        self.assertEqual((second["session_id"], second["status"]), ("S001", 404))


if __name__ == "__main__":
    unittest.main()
