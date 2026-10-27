"""The FastAPI legacy bridge never hangs a request: every _handle path queues _END."""
import queue
import threading
import unittest

from stickerloom.api import legacy_adapter as la


class HandleTests(unittest.TestCase):
    def test_setup_failure_still_ends_the_request(self):
        """A header line past http.client's 64 KiB limit raises inside setup; the request must still end, not hang the adapter's queue read."""
        pipe = la._Pipe()
        t = threading.Thread(target=la._handle, args=(type("H", (), {}), "GET", "/api/health",
                                                      b"X-Huge: " + b"A" * 70000 + b"\r\n\r\n", b"", "127.0.0.1", 8770, pipe),
                             daemon=True)
        t.start()
        self.assertIs(pipe.q.get(timeout=10), la._END)
        t.join(timeout=10)


if __name__ == "__main__":
    unittest.main()
