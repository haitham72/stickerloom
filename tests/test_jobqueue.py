"""The durable job queue (generation/jobqueue.py, migration 007) and its workers, on a throwaway Postgres database (stickerloom_test; skipped when stickerloom-db is not running).
The provider is the fake CLI of test_live: no credits are ever spent."""
import os
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

TEST_URL = "postgresql://stickerloom:stickerloom_local@localhost:5434/stickerloom_test"
os.environ["STICKERLOOM_DATABASE_URL"] = TEST_URL

from stickerloom.generation import higgsfield, jobqueue, jobs  # noqa: E402
from stickerloom.generation import tasks as _tasks  # noqa: E402
from tests.test_live import Base, FakeCLI                           # noqa: E402


def fresh_database():
    import psycopg
    base = TEST_URL.rsplit("/", 1)[0] + "/stickerloom"
    try:
        boot = psycopg.connect(base, connect_timeout=5, autocommit=True)
    except Exception:
        raise unittest.SkipTest("stickerloom-db is not running (python -m stickerloom db up)")
    with boot:
        with boot.cursor() as cur:
            cur.execute("DROP DATABASE IF EXISTS stickerloom_test")
            cur.execute("CREATE DATABASE stickerloom_test")
    from stickerloom.store import db
    db.reset_cache()
    db.migrate()


class QueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fresh_database()

    def setUp(self):
        from stickerloom.store import db
        self.db = db
        with db.connect() as c:
            c.execute("DELETE FROM job_queue")
            c.commit()

    def row(self, jid):
        with self.db.connect() as c, c.cursor() as cur:
            cur.execute("SELECT status, attempts, locked_by, error, ingested_at IS NOT NULL FROM job_queue WHERE job_id = %s", (jid,))
            return cur.fetchone()

    def sql(self, q, *a):
        with self.db.connect() as c:
            c.execute(q, a)
            c.commit()

    def test_enqueue_claim_complete(self):
        with self.db.connect() as c:
            self.assertTrue(jobqueue.enqueue(c, "J001", "sheet", "U001"))
            self.assertFalse(jobqueue.enqueue(c, "J001", "sheet"))                    # already waiting: left alone
            r = jobqueue.claim(c, "w1")
            self.assertEqual((r["job_id"], r["kind"], r["owner"], r["attempts"]), ("J001", "sheet", "U001", 1))
            self.assertIsNone(jobqueue.claim(c, "w2"))                                   # running: nobody else gets it
            self.assertFalse(jobqueue.enqueue(c, "J001", "sheet"))                    # a running job is not re-queued
            jobqueue.complete(c, "J001", True)
            s = jobqueue.stats(c)
        self.assertEqual((self.row("J001")[0], s["done"], s["awaiting_follow_up"], s["queued"]), ("DONE", 1, 1, 0))

    def test_two_workers_never_get_the_same_job(self):
        with self.db.connect() as c:
            for i in range(30):
                jobqueue.enqueue(c, f"J{i + 1:03d}", "sheet")
        got, lock = [], threading.Lock()

        def drain(name):
            with self.db.connect() as c:
                while True:
                    r = jobqueue.claim(c, name)
                    if not r:
                        return
                    with lock:
                        got.append(r["job_id"])
        ts = [threading.Thread(target=drain, args=(f"w{i}",)) for i in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sorted(got), [f"J{i + 1:03d}" for i in range(30)])                # all of them, each exactly once

    def test_kinds_filter_and_order(self):
        with self.db.connect() as c:
            jobqueue.enqueue(c, "J001", "video")
            jobqueue.enqueue(c, "J002", "sheet")
            self.assertEqual(jobqueue.claim(c, "w", ["sheet"])["job_id"], "J002")
            self.assertEqual(jobqueue.claim(c, "w")["job_id"], "J001")

    def test_the_workers_own_failure_backs_off_then_the_row_is_dead(self):
        with self.db.connect() as c:
            jobqueue.enqueue(c, "J001", "sheet", max_attempts=2)
            jobqueue.claim(c, "w")
            self.assertEqual(jobqueue.fail_infra(c, "J001", "disk full"), "QUEUED")
            self.assertIsNone(jobqueue.claim(c, "w"))                                     # waiting out its backoff
            self.sql("UPDATE job_queue SET next_run_at = now() WHERE job_id = 'J001'")
            self.assertEqual(jobqueue.claim(c, "w")["attempts"], 2)
            self.assertEqual(jobqueue.fail_infra(c, "J001", "disk full again"), "DEAD")
            self.assertIsNone(jobqueue.claim(c, "w"))
        self.assertEqual(self.row("J001")[:2], ("DEAD", 2))
        self.assertIn("disk full again", self.row("J001")[3])

    def test_a_provider_failure_is_never_retried_automatically(self):
        with self.db.connect() as c:
            jobqueue.enqueue(c, "J001", "video")
            jobqueue.claim(c, "w")
            jobqueue.complete(c, "J001", False, "content policy")
            self.assertIsNone(jobqueue.claim(c, "w"))
            self.assertEqual(self.row("J001")[0], "FAILED")
            self.assertEqual(jobqueue.reap(c, 0), 0)                                      # reaping touches only RUNNING rows
            self.assertTrue(jobqueue.enqueue(c, "J001", "video"))                         # a human retry puts it back
        self.assertEqual(self.row("J001")[:2], ("QUEUED", 0))

    def test_a_lost_worker_is_reaped_and_a_tired_job_dies(self):
        with self.db.connect() as c:
            jobqueue.enqueue(c, "J001", "sheet")
            jobqueue.enqueue(c, "J002", "sheet", max_attempts=1)
            jobqueue.claim(c, "gone")
            jobqueue.claim(c, "gone")
            self.assertEqual(jobqueue.reap(c, 3600), 0)                                    # still within the time a job can run
            self.sql("UPDATE job_queue SET locked_at = now() - interval '2 hours'")
            self.assertEqual(jobqueue.reap(c, 3600), 2)
        self.assertEqual((self.row("J001")[0], self.row("J002")[0]), ("QUEUED", "DEAD"))

    def test_follow_up_is_leased_and_happens_once(self):
        with self.db.connect() as c:
            jobqueue.enqueue(c, "J001", "sheet")
            jobqueue.claim(c, "w")
            self.assertEqual(jobqueue.take_ingest(c), [])                                  # not done yet
            jobqueue.complete(c, "J001", True)
            self.assertEqual([r["job_id"] for r in jobqueue.take_ingest(c)], ["J001"])
            self.assertEqual(jobqueue.take_ingest(c), [])                                  # leased: a second server does not take it too
            self.sql("UPDATE job_queue SET ingest_locked_at = now() - interval '1 hour'")
            self.assertEqual([r["job_id"] for r in jobqueue.take_ingest(c)], ["J001"])     # the first server crashed: the lease ran out
            jobqueue.mark_ingested(c, "J001")
            self.assertEqual(jobqueue.take_ingest(c), [])
        self.assertTrue(self.row("J001")[4])

    def test_unfinished_job_files_can_be_put_back_on_the_queue(self):
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        try:
            a = jobs.create(tmp, "sheet", request={"user": "U002"})
            b = jobs.create(tmp, "video")
            jobs.claim(tmp, b["id"], "t-1")
            c_ = jobs.create(tmp, "sheet")
            jobs.fail(tmp, c_["id"], "no")
            with self.db.connect() as c:
                self.assertEqual(jobqueue.sync_from_files(c, tmp), 2)                      # the REQUESTED and the CLAIMED one, not the FAILED one
                self.assertEqual(jobqueue.sync_from_files(c, tmp), 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class WorkerTests(Base):
    @classmethod
    def setUpClass(cls):
        fresh_database()

    def setUp(self):
        super().setUp()
        from stickerloom.store import db
        self.dbm = db
        with db.connect() as c:
            c.execute("DELETE FROM job_queue")
            c.commit()

    def sheet_job(self, **req):
        t = _tasks.reserve(self.out, self.tmp / "in", "falcon dancing")
        j = jobs.create(self.out, "sheet", task=t["id"], request={"model": "nano_banana_flash", "options": {}, "prompt": "p", **req})
        with self.dbm.connect() as c:
            jobqueue.enqueue(c, j["id"], "sheet")
        return j

    def row(self, jid):
        with self.dbm.connect() as c, c.cursor() as cur:
            cur.execute("SELECT status, attempts, error FROM job_queue WHERE job_id = %s", (jid,))
            return cur.fetchone()

    def test_a_worker_runs_the_job_and_leaves_the_follow_up_to_the_server(self):
        j = self.sheet_job()
        with mock.patch.object(jobs, "fulfil", wraps=jobs.fulfil) as f:
            self.assertEqual(jobqueue.run_one(self.out, "w1"), j["id"])
        self.assertIsNone(f.call_args.kwargs.get("on_done"))
        self.assertEqual(jobs.read(self.out, j["id"])["status"], "DONE")
        self.assertEqual(self.row(j["id"])[0], "DONE")
        self.assertEqual(sum(c[:2] == ["generate", "create"] for c in self.cli.calls), 1)
        self.assertIsNone(jobqueue.run_one(self.out, "w1"))                                # nothing else is due

    def test_a_provider_failure_marks_the_row_failed_and_is_not_run_again(self):
        j = self.sheet_job()
        real = self.cli.__call__

        def refuse(args, timeout):
            if args[:2] == ["generate", "create"]:
                return 1, "", "content policy"
            return real(args, timeout)
        with mock.patch.object(higgsfield, "RUN", refuse):
            jobqueue.run_one(self.out, "w1")
        self.assertEqual(jobs.read(self.out, j["id"])["status"], "FAILED")
        st = self.row(j["id"])
        self.assertEqual(st[0], "FAILED")
        self.assertIn("content policy", st[2])
        self.assertIsNone(jobqueue.run_one(self.out, "w1"))                                # never retried by itself: a retry could spend credits again

    def test_the_workers_own_crash_is_retried_with_backoff(self):
        j = self.sheet_job()
        with mock.patch.object(jobs, "fulfil", side_effect=OSError("disk full")):
            jobqueue.run_one(self.out, "w1")
        st = self.row(j["id"])
        self.assertEqual((st[0], st[1]), ("QUEUED", 1))
        self.assertIn("disk full", st[2])
        self.assertIsNone(jobqueue.run_one(self.out, "w1"))                                # backing off

    def test_a_job_an_operator_already_finished_is_just_recorded(self):
        j = self.sheet_job()
        jobs.claim(self.out, j["id"], "op-1")
        f = self.tmp / "r.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        jobs.done(self.out, j["id"], str(f), "m")
        jobqueue.run_one(self.out, "w1")
        self.assertEqual(self.row(j["id"])[0], "DONE")
        self.assertFalse([c for c in self.cli.calls if c[:2] == ["generate", "create"]])

    def test_a_worker_that_died_while_waiting_resumes_by_ticket_and_never_pays_twice(self):
        j = self.sheet_job()
        jobs.claim(self.out, j["id"], "fake-job-7")                                         # the dead worker had stored the ticket
        jobs.update(self.out, j["id"], cost_estimate=2.0)
        self.cli.kind_of["fake-job-7"] = "png"
        with self.dbm.connect() as c:
            jobqueue.claim(c, "dead")
            c.execute("UPDATE job_queue SET locked_at = now() - interval '2 hours'")
            c.commit()
            self.assertEqual(jobqueue.reap(c), 1)
        jobqueue.run_one(self.out, "w2")
        self.assertEqual(jobs.read(self.out, j["id"])["status"], "DONE")
        self.assertFalse([c for c in self.cli.calls if c[:2] == ["generate", "create"]])

    def test_the_paid_call_lock_spans_processes(self):
        """Another process holding out/.paid.lock makes a worker wait: two paid calls never overlap on one machine."""
        from stickerloom.runtime.writer_lock import WriterLock
        j = self.sheet_job()
        other = WriterLock(self.out, "another worker", ".paid.lock").acquire()
        t = threading.Thread(target=jobqueue.run_one, args=(self.out, "w1"))
        t.start()
        time.sleep(1.5)
        self.assertEqual(jobs.read(self.out, j["id"])["status"], "REQUESTED")             # waiting for the other call to finish
        self.assertFalse([c for c in self.cli.calls if c[:2] == ["generate", "create"]])
        other.release()
        t.join(30)
        self.assertEqual(jobs.read(self.out, j["id"])["status"], "DONE")


class ServerQueueTests(Base):
    @classmethod
    def setUpClass(cls):
        fresh_database()

    def setUp(self):
        super().setUp()
        from stickerloom.store import db
        self.dbm = db
        with db.connect() as c:
            c.execute("DELETE FROM job_queue")
            c.commit()
        self.env = {k: os.environ.get(k) for k in ("STICKERLOOM_JOB_MODE", "STICKERLOOM_DB_WRITE")}
        os.environ["STICKERLOOM_JOB_MODE"], os.environ["STICKERLOOM_DB_WRITE"] = "queue", "1"
        from stickerloom.console.server import Console
        self.c = Console(self.out, self.tmp / "in")

    def tearDown(self):
        self.c.release_writer()
        for k, v in self.env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        super().tearDown()

    def test_mode_is_threads_unless_asked_and_possible(self):
        self.assertEqual(jobqueue.mode(self.out), "queue")
        os.environ["STICKERLOOM_JOB_MODE"] = "threads"
        self.assertEqual(jobqueue.mode(self.out), "threads")
        os.environ["STICKERLOOM_JOB_MODE"] = "queue"
        with mock.patch("stickerloom.store.db.available", lambda: False):                        # Postgres unreachable: the files-and-threads way still works
            self.assertEqual(jobqueue.mode(self.out), "threads")

    def test_the_server_enqueues_a_worker_runs_it_and_the_server_follows_up_once(self):
        t = _tasks.reserve(self.out, self.tmp / "in", "falcon dancing")
        j = jobs.create(self.out, "sheet", task=t["id"], request={"model": "nano_banana_flash", "options": {}, "prompt": "p", "user": "U007"})
        followed = []
        self.c.start_from_job = lambda job: followed.append(job["id"])
        self.c.fulfil_async(j["id"], after=lambda job: followed.append("thread-way"))      # queue mode: nothing runs in a thread of the server
        with self.dbm.connect() as c, c.cursor() as cur:
            cur.execute("SELECT status, owner FROM job_queue WHERE job_id = %s", (j["id"],))
            self.assertEqual(cur.fetchone(), ("QUEUED", "U007"))
        time.sleep(0.5)
        self.assertEqual(jobs.read(self.out, j["id"])["status"], "REQUESTED")
        self.assertEqual(jobqueue.run_one(self.out, "w1"), j["id"])                         # the worker process does the paid part
        end = time.time() + 15
        while not followed and time.time() < end:
            time.sleep(0.2)
        self.assertEqual(followed, [j["id"]])                                                # the server's ingest loop did the follow-up
        time.sleep(2.5)
        self.assertEqual(followed, [j["id"]])                                                # ... exactly once
        with self.dbm.connect() as c:
            self.assertEqual(jobqueue.stats(c)["awaiting_follow_up"], 0)

    def test_health_reports_the_queue(self):
        from stickerloom.runtime import health
        d = health.queue(self.out)
        self.assertEqual((d["mode"], d["queued"], d["dead"]), ("queue", 0, 0))


if __name__ == "__main__":
    unittest.main()
