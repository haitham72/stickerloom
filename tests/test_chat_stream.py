"""Streaming chat: GET /api/chat/sessions/{id}/stream on the FastAPI server sends `turn` events while the turn works and `done` when it ends,
with the session route's own access rules (a stranger's or unknown session, a foreign Host). Rules only: no provider, no language model."""
import http.client
import json
import threading
import unittest

from tests import test_agent_server as _srv           # a module, not its TestCase: unittest would run that class here too


class ChatStreamTests(unittest.TestCase):
    setUpClass = classmethod(_srv.ChatServerTests.setUpClass.__func__)          # the same rules-only server, without re-running that file's tests
    tearDownClass = classmethod(_srv.ChatServerTests.tearDownClass.__func__)
    req = _srv.ChatServerTests.req

    def events(self, sid, timeout=60):
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        h.request("GET", f"/api/chat/sessions/{sid}/stream", headers={"X-Request-Id": "stream-1"})
        r = h.getresponse()
        self.assertEqual(r.status, 200)
        self.assertEqual((r.getheader("Content-Type"), r.getheader("X-API-Version") is not None, r.getheader("X-Request-Id")),
                         ("text/event-stream; charset=utf-8", True, "stream-1"))
        out, ev = [], None
        while True:
            line = r.readline().decode()
            if not line:
                break
            line = line.rstrip("\n")
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                out.append((ev, json.loads(line[6:])))
                if ev == "done":
                    break
        h.close()
        return out

    def test_a_turn_streams_its_last_message_then_done(self):
        code, s = self.req("POST", "/api/chat/sessions", {})
        self.assertEqual(code, 201 if code == 201 else 200, s)
        sid = s["id"]
        got = {}
        t = threading.Thread(target=lambda: got.setdefault("ev", self.events(sid)))
        code, j = self.req("POST", f"/api/chat/sessions/{sid}/messages", {"text": "hi"})
        self.assertIn(code, (200, 202), j)
        t.start()
        t.join(60)
        ev = got.get("ev") or []
        self.assertTrue(ev, "the stream sent something")
        self.assertEqual(ev[-1][0], "done")
        turns = [d for e, d in ev if e == "turn"]
        self.assertTrue(turns, "at least the current turn")
        self.assertTrue(all(set(d) == {"working", "count", "message"} for d in turns), "only the turn in progress, never the whole session")
        final = self.req("GET", f"/api/chat/sessions/{sid}")[1]
        self.assertEqual(turns[-1]["message"], final["messages"][-1], "the last turn event is the message the session holds")
        self.assertEqual(ev[-1][1], {"working": False, "count": len(final["messages"])})

    def test_access_is_the_session_routes_own(self):
        code, j = self.req("GET", "/api/chat/sessions/S999/stream")
        self.assertEqual(code, self.req("GET", "/api/chat/sessions/S999")[0], "an unknown chat answers like the session route")
        self.assertIn("error", j)
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h.request("GET", "/api/chat/sessions/S001/stream", headers={"Host": "evil.example"})
        r = h.getresponse()
        self.assertEqual((r.status, json.loads(r.read())), (403, {"error": "unexpected Host header"}))
        h.close()
