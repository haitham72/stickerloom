"""The pack claim ledger (generation/claims.py, store/repo.save_pack_session): session files, C### claims, the state machine, lineage, and the SQL its
mirror sends. Temp dirs only; Postgres is never reached (a recording fake connection), no provider is called."""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from stickerloom.generation import actions, claims
from stickerloom.store import repo


class Base(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {"STICKERLOOM_DB_WRITE": "0"})
        self._env.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self._env.stop()
        self.tmp.cleanup()


class ClaimQueue(Base):
    def test_first_request_opens_the_session_and_claims_core_with_its_stored_actions(self):
        r = claims.claim_next(self.out, "falcon")
        self.assertTrue(r["created"])
        self.assertFalse(r["complete"])
        c = r["claim"]
        self.assertEqual((c["id"], c["preset"], c["grid"], c["status"]), ("C001", "core-v1", [3, 3], "PLANNED"))
        self.assertEqual([h["status"] for h in c["history"]], ["CLAIMED", "PLANNED"])
        self.assertEqual(c["plan"]["slots"]["preset"], "core-v1")
        self.assertEqual([s["key"] for s in c["plan"]["stickers"]], [f"falcon_{t}" for t in actions.PRESETS["core-v1"]])
        on_disk = json.loads((self.out / "pack_sessions" / "falcon.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["id"], "pack-falcon")
        self.assertEqual(on_disk["claims"][0]["id"], "C001")

    def test_generate_more_takes_the_next_preset_then_says_complete(self):
        got = [claims.claim_next(self.out, "falcon")["claim"]["preset"] for _ in range(4)]
        self.assertEqual(got, claims.ORDER)
        r = claims.claim_next(self.out, "falcon")
        self.assertIsNone(r["claim"])
        self.assertTrue(r["complete"])
        self.assertFalse(r["created"])
        self.assertEqual(len(claims.get_session(self.out, "pack-falcon")["claims"]), 4, "a complete pack is never claimed again")

    def test_claim_ids_are_one_sequence_over_every_session(self):
        a = claims.claim_next(self.out, "falcon")["claim"]["id"]
        b = claims.claim_next(self.out, "camel")["claim"]["id"]
        c = claims.claim_next(self.out, "falcon")["claim"]["id"]
        self.assertEqual((a, b, c), ("C001", "C002", "C003"))
        self.assertEqual(claims.find_claim(self.out, "c002")["session"], "camel")

    def test_two_by_two_takes_the_first_four_actions_and_other_grids_are_refused(self):
        c = claims.claim_next(self.out, "falcon", grid="2x2")["claim"]
        self.assertEqual(c["grid"], [2, 2])
        self.assertEqual([s["key"] for s in c["plan"]["stickers"]], [f"falcon_{t}" for t in actions.PRESETS["core-v1"][:4]])
        with self.assertRaises(claims.ClaimError):
            claims.claim_next(self.out, "falcon", grid=(1, 1))
        with self.assertRaises(claims.ClaimError):
            claims.claim_next(self.out, "   ")

    def test_two_simultaneous_clicks_take_two_different_presets(self):
        claims.claim_next(self.out, "falcon")
        got, errs = [], []

        def click():
            try:
                got.append(claims.claim_next(self.out, "falcon", plan=False)["claim"])
            except Exception as e:          # pragma: no cover - surfaced below
                errs.append(e)
        ts = [threading.Thread(target=click) for _ in range(2)]
        [t.start() for t in ts]
        [t.join(30) for t in ts]
        self.assertEqual(errs, [])
        self.assertEqual(sorted(c["preset"] for c in got), ["reactions-v1", "social-v1"])
        self.assertEqual(sorted(c["id"] for c in got), ["C002", "C003"])


class StateMachine(Base):
    def test_request_link_regenerate_appends_revisions_and_never_goes_back(self):
        cid = claims.claim_next(self.out, "falcon", plan=False)["claim"]["id"]
        with self.assertRaises(claims.ClaimError):
            claims.mark_requested(self.out, cid, "J100")          # CLAIMED has no plan: nothing may be requested yet
        claims.plan_claim(self.out, cid)
        with self.assertRaises(claims.ClaimError):
            claims.plan_claim(self.out, cid)                     # never backwards
        with self.assertRaises(claims.ClaimError):
            claims.link_generation(self.out, cid, 112)           # PLANNED is not waiting for a generation
        self.assertEqual(claims.mark_requested(self.out, cid, "J100")["status"], "REQUESTED")
        c = claims.link_generation(self.out, cid, 112)
        self.assertEqual((c["status"], c["generations"][0]["generation"], c["generations"][0]["revision"]), ("DONE", "G112", 1))
        self.assertEqual(claims.link_generation(self.out, cid, "G112")["generations"], c["generations"], "the same link twice is one link")
        c = claims.mark_requested(self.out, cid, "J101")             # regenerate: same claim, a newer generation
        self.assertTrue(c["history"][-1]["regenerate"])
        c = claims.link_generation(self.out, cid, 115)
        self.assertEqual([(g["generation"], g["revision"]) for g in c["generations"]], [("G112", 1), ("G115", 2)])
        self.assertEqual(claims.current(c), "G115")
        self.assertEqual(c["jobs"], ["J100", "J101"])
        self.assertEqual([h["status"] for h in c["history"]], ["CLAIMED", "PLANNED", "REQUESTED", "DONE", "REQUESTED", "DONE"])

    def test_unknown_claim_and_session_answer_404_in_words(self):
        with self.assertRaises(claims.ClaimError) as e:
            claims.find_claim(self.out, "C999")
        self.assertEqual(e.exception.code, 404)
        with self.assertRaises(claims.ClaimError) as e:
            claims.get_session(self.out, "pack-nobody")
        self.assertEqual(e.exception.code, 404)


class FakeCursor:
    def __init__(self, log, task_rows):
        self.log, self.task_rows, self._last = log, task_rows, ""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._last = " ".join(sql.split())
        self.log.append((self._last, params))

    def fetchone(self):
        if self._last.startswith("SELECT id FROM tasks"):
            return self.task_rows.pop(0) if self.task_rows else None
        return None                        # no generation is in this fake database


class FakeConn:
    def __init__(self, task_rows=()):
        self.log, self.commits, self.rollbacks, self.task_rows = [], 0, 0, list(task_rows)

    def cursor(self):
        return FakeCursor(self.log, self.task_rows)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class Mirror(Base):
    def session(self):
        cid = claims.claim_next(self.out, "falcon")["claim"]["id"]
        claims.mark_requested(self.out, cid, "J100")
        claims.link_generation(self.out, cid, 112)
        claims.claim_next(self.out, "falcon", grid="2x2")
        return claims.get_session(self.out, "falcon")

    def test_a_session_becomes_session_claim_lineage_and_task_rows(self):
        conn = FakeConn()
        self.assertEqual(repo.save_pack_session(conn, self.session()), 2)
        sqls = [s for s, _ in conn.log]
        self.assertTrue(sqls[0].startswith("INSERT INTO pack_sessions"))
        claims_rows = [p for s, p in conn.log if s.startswith("INSERT INTO claims")]
        self.assertEqual([(p[0], p[2], p[3], p[4]) for p in claims_rows], [("C001", "core-v1", "3x3", "DONE"), ("C002", "social-v1", "2x2", "PLANNED")])
        lineage = [p for s, p in conn.log if s.startswith("INSERT INTO claim_generations")]
        self.assertEqual([p[:3] for p in lineage], [("C001", "G112", 1)])
        tasks = [p for s, p in conn.log if s.startswith("INSERT INTO tasks")]
        self.assertEqual([(p[0], p[1], p[2], p[4]) for p in tasks], [(repo.CLAIM_PROVIDER, "C001", "falcon", "DONE"), (repo.CLAIM_PROVIDER, "C002", "falcon", "PLANNED")])
        self.assertIsNone(tasks[0][3], "G112 is not in this database yet: the task row waits for it instead of breaking the foreign key")
        self.assertEqual(json.loads(tasks[0][5])["jobs"], ["J100"])
        self.assertEqual(conn.commits, 1)

    def test_a_second_import_updates_the_task_row_instead_of_adding_one(self):
        conn = FakeConn(task_rows=[(41,), (42,)])
        repo.save_pack_session(conn, self.session())
        sqls = [s for s, _ in conn.log]
        self.assertFalse(any(s.startswith("INSERT INTO tasks") for s in sqls))
        self.assertEqual(sum(s.startswith("UPDATE tasks") for s in sqls), 2)
        self.assertTrue(all("ON CONFLICT" in s for s in sqls if s.startswith(("INSERT INTO claims", "INSERT INTO claim_generations", "INSERT INTO pack_sessions"))))

    def test_import_reads_every_session_file(self):
        self.session()
        claims.claim_next(self.out, "camel")
        conn = FakeConn()
        self.assertEqual(repo.import_pack_sessions(conn, self.out), 2)

    def test_the_migration_only_adds_tables(self):
        sql = (Path(repo.__file__).resolve().parents[2] / "migrations" / "012_pack_claims.sql").read_text(encoding="utf-8")
        for t in ("pack_sessions", "claims", "claim_generations"):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {t} ", sql)
        self.assertNotIn("ALTER TABLE", sql.upper())


if __name__ == "__main__":
    unittest.main()
