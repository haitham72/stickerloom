"""The server's event loop (console/app.py `quiet_loop`, used by `serve` and `serve --lan`): a client that vanishes
mid-connection stays silent, anything else still reaches the default handler."""
import unittest

from stickerloom.console.app import quiet_loop


class QuietLoopTests(unittest.TestCase):
    def test_a_vanished_client_is_silent_and_anything_else_still_reports(self):
        loop = quiet_loop()
        try:
            seen = []
            loop.default_exception_handler = seen.append
            h = loop.get_exception_handler()
            self.assertIsNotNone(h, "the loop carries the filter")
            h(loop, {"exception": ConnectionResetError(10054, "forcibly closed")})
            h(loop, {"exception": BrokenPipeError()})
            h(loop, {"message": "no exception attached"})
            h(loop, {"exception": ValueError("boom")})
            self.assertEqual(len(seen), 2, "only the non-disconnect contexts are reported")
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
