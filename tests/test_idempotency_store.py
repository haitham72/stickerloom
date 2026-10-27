"""The durable idempotency backstop (store/idem.py + migration 008): the cache answers first, but when it forgets (Redis down and the server restarted) Postgres still knows the
key, so a repeated click or a retried request never starts and pays for a second job. Tests that need the database skip when it is down; the real out/ is never touched
(`sync.enabled` is switched on for a temp out and the test's own rows are deleted)."""
import unittest
from pathlib import Path
from unittest import mock

from stickerloom.runtime import cache as cachemod
from stickerloom.store import db, idem, sync

SCOPE = "test:idempotency_store"


class PureTests(unittest.TestCase):
    def test_the_key_is_stored_hashed_never_in_the_clear(self):
        a = idem.key_sha("my-secret-key")
        self.assertEqual(len(a), 64)
        self.assertNotIn("secret", a)
        self.assertEqual(a, idem.key_sha("  my-secret-key "))                               # the same key however it was typed
        self.assertNotEqual(a, idem.key_sha("another"))

    def test_without_the_database_it_is_a_quiet_no_op(self):
        with mock.patch.object(db, "available", lambda: False), mock.patch.object(sync, "enabled", lambda out: True):
            self.assertIsNone(idem.get(Path("x"), SCOPE, "k"))
            idem.put(Path("x"), SCOPE, "k", {"id": 1})                                      # never raises

    def test_a_temp_out_never_touches_the_shared_database(self):
        with mock.patch.object(sync, "enabled", lambda out: False), mock.patch.object(db, "connect", side_effect=AssertionError("must not connect")):
            self.assertIsNone(idem.get(Path("x"), SCOPE, "k"))
            idem.put(Path("x"), SCOPE, "k", {"id": 1})


@unittest.skipUnless(db.available(), "stickerloom-db is down (python -m stickerloom db up)")
class DatabaseTests(unittest.TestCase):
    def setUp(self):
        db.migrate()
        self.patch = mock.patch.object(sync, "enabled", lambda out: True)
        self.patch.start()
        self.clean()

    def tearDown(self):
        self.patch.stop()
        self.clean()

    def clean(self):
        with db.connect() as c:
            c.execute("delete from idempotency_keys where scope = %s", (SCOPE,))

    def test_put_then_get_returns_the_first_answer_and_the_first_writer_wins(self):
        out = Path("ignored")
        self.assertIsNone(idem.get(out, SCOPE, "k1"))
        idem.put(out, SCOPE, "k1", {"job": "J001", "estimate": 2.0})
        idem.put(out, SCOPE, "k1", {"job": "J002"})                                          # a second writer never replaces the first answer
        self.assertEqual(idem.get(out, SCOPE, "k1"), {"job": "J001", "estimate": 2.0})
        self.assertIsNone(idem.get(out, SCOPE, "k2"))
        self.assertIsNone(idem.get(out, SCOPE + "x", "k1"))                                  # scoped: another user's key never collides

    def test_old_records_are_not_answered(self):
        out = Path("ignored")
        idem.put(out, SCOPE, "old", {"job": "J009"})
        with db.connect() as c:
            c.execute("update idempotency_keys set created_at = now() - interval '25 hours' where scope = %s", (SCOPE,))
        self.assertIsNone(idem.get(out, SCOPE, "old"))                                       # past 24 h a key may be used again

    def test_console_idem_answers_from_postgres_after_the_cache_forgot(self):
        """The case the backstop exists for: Redis down, the server restarted, the page's retry arrives with the same key."""
        import shutil
        import tempfile
        from stickerloom.console.server import Console
        tmp = Path(tempfile.mkdtemp())
        (tmp / "in").mkdir()
        try:
            runs = []

            def make_console():
                return Console(tmp / "out", tmp / "in")
            first = make_console()
            with mock.patch.object(cachemod, "default", lambda: cachemod.Cache(force_memory=True)):
                a = first.idem(SCOPE, "click-1", lambda: runs.append(1) or {"job": "J042"})
                first.release_writer()
                second = make_console()                                                      # "the server restarted": a new process, an empty memory cache
                with mock.patch.object(cachemod, "default", lambda: cachemod.Cache(force_memory=True)):
                    b = second.idem(SCOPE, "click-1", lambda: runs.append(1) or {"job": "J999"})
                second.release_writer()
            self.assertEqual((a, runs), ({"job": "J042"}, [1]))
            self.assertEqual((b["job"], b["idempotent"]), ("J042", True))                    # the first answer, and fn was not run again
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
