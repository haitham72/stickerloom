"""The durable job queue (Postgres `job_queue`, migration 007) and the workers that drain it.

Why: until now a provider job was a file fulfilled by a thread of the server, so a server restart lost the follow-up (and the wait). With `STICKERLOOM_JOB_MODE=queue` the server only
*enqueues* a job; any number of `python -m stickerloom worker` processes claim it (`FOR UPDATE SKIP LOCKED`: two workers never get the same one), run `jobs.fulfil` on it and mark the row;
the server's ingest loop then follows up every DONE job exactly once (sheet -> the stills run, video -> slice) and survives restarts because that state is in the table.

The rules that keep it safe with a paid provider:
- `out/jobs/J###.json` is the truth for the request, the ticket and the result. The row says only who runs it and when.
- **A provider failure is never retried automatically** (a retry can spend credits): the job file is FAILED, the row is FAILED, and a human retries it (`stickerloom queue retry J004`, or the Studio's retry).
- Only the worker's *own* trouble is retried, with backoff (30 s, 60 s, ...), up to `max_attempts`, then the row is DEAD. A job whose ticket is already stored resumes by that ticket, so a worker that
  died while waiting does not pay twice.
- One paid call at a time across every process (`jobs.fulfil` takes a file lock under out/), exactly as with threads: workers add durability and isolation, not paid concurrency.
"""
from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

from . import jobs

MAX_ATTEMPTS = 3
BACKOFF_S = 30
POLL_S = 2.0


def enabled() -> bool:
    return os.environ.get("STICKERLOOM_JOB_MODE", "").strip().lower() == "queue"


def mode(out) -> str:
    """`queue` when STICKERLOOM_JOB_MODE=queue and Postgres is the write-through target of this out/, else `threads` (the files-and-threads way, which always works)."""
    if not enabled():
        return "threads"
    try:
        from ..store import db, sync
        return "queue" if sync.enabled(out) and db.available() else "threads"
    except Exception:
        return "threads"


def stale_s() -> int:
    """A RUNNING row older than this has lost its worker: a job's wait is bounded by the job timeout, so anything older cannot still be alive."""
    return jobs.timeout_s() + 300


def enqueue(conn, job_id: str, kind: str, owner: str = "local", max_attempts: int = MAX_ATTEMPTS) -> bool:
    """Put a job on the queue. A job already queued, running or done is left alone; a FAILED or DEAD one goes back to QUEUED (a human retry). True when it is waiting now."""
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO job_queue (job_id, kind, owner, max_attempts) VALUES (%s,%s,%s,%s)
                       ON CONFLICT (job_id) DO UPDATE SET status = 'QUEUED', attempts = 0, error = NULL, next_run_at = now(), locked_by = NULL, locked_at = NULL,
                         finished_at = NULL, ingest_locked_at = NULL, ingested_at = NULL
                       WHERE job_queue.status IN ('FAILED','DEAD')
                       RETURNING job_id""", (job_id.upper(), kind, owner, max_attempts))
        ok = cur.fetchone() is not None
    conn.commit()
    return ok


def claim(conn, worker: str, kinds: list | None = None) -> dict | None:
    """The oldest job that is due, locked to this worker. Concurrent workers skip each other's rows instead of waiting."""
    kind_sql, args = ("AND kind = ANY(%s)", [list(kinds)]) if kinds else ("", [])
    with conn.cursor() as cur:
        cur.execute(f"""UPDATE job_queue SET status = 'RUNNING', locked_by = %s, locked_at = now(), attempts = attempts + 1, error = NULL
                        WHERE job_id = (SELECT job_id FROM job_queue WHERE status = 'QUEUED' AND next_run_at <= now() {kind_sql}
                                        ORDER BY next_run_at, enqueued_at LIMIT 1 FOR UPDATE SKIP LOCKED)
                        RETURNING job_id, kind, owner, attempts, max_attempts""", [worker, *args])
        r = cur.fetchone()
    conn.commit()
    return dict(zip(("job_id", "kind", "owner", "attempts", "max_attempts"), r)) if r else None


def complete(conn, job_id: str, ok: bool, error: str | None = None) -> None:
    """The worker finished: DONE (the job file holds the result), or FAILED (the provider said no: a human decides)."""
    with conn.cursor() as cur:
        cur.execute("UPDATE job_queue SET status = %s, finished_at = now(), locked_at = NULL, error = %s WHERE job_id = %s",
                    ("DONE" if ok else "FAILED", None if ok else str(error or "failed")[:500], job_id.upper()))
    conn.commit()


def fail_infra(conn, job_id: str, error: str, base_s: int = BACKOFF_S) -> str:
    """The worker itself failed (not the provider): QUEUED again after a growing wait, or DEAD once the attempts are used up. Returns the new status."""
    with conn.cursor() as cur:
        cur.execute("""UPDATE job_queue SET
                         status = CASE WHEN attempts >= max_attempts THEN 'DEAD' ELSE 'QUEUED' END,
                         next_run_at = now() + (%s * power(2, greatest(attempts - 1, 0))) * interval '1 second',
                         finished_at = CASE WHEN attempts >= max_attempts THEN now() ELSE NULL END,
                         locked_by = NULL, locked_at = NULL, error = %s
                       WHERE job_id = %s AND status = 'RUNNING' RETURNING status""", (base_s, str(error)[:500], job_id.upper()))
        r = cur.fetchone()
    conn.commit()
    return r[0] if r else "UNKNOWN"


def reap(conn, older_than_s: int | None = None) -> int:
    """Rows whose worker is gone (RUNNING for longer than any job can run): back on the queue, or DEAD when they have used their attempts. Returns how many."""
    with conn.cursor() as cur:
        cur.execute("""UPDATE job_queue SET
                         status = CASE WHEN attempts >= max_attempts THEN 'DEAD' ELSE 'QUEUED' END,
                         next_run_at = now(), finished_at = CASE WHEN attempts >= max_attempts THEN now() ELSE NULL END,
                         locked_by = NULL, locked_at = NULL, error = 'the worker was lost (no finish within the job timeout)'
                       WHERE status = 'RUNNING' AND locked_at < now() - make_interval(secs => %s) RETURNING job_id""", (older_than_s if older_than_s is not None else stale_s(),))
        n = len(cur.fetchall())
    conn.commit()
    return n


def take_ingest(conn, limit: int = 5, lease_s: int = 600) -> list:
    """DONE jobs the server has not followed up yet, leased for `lease_s` so two servers never both do it; a crash before `mark_ingested` just lets the lease run out."""
    with conn.cursor() as cur:
        cur.execute("""UPDATE job_queue SET ingest_locked_at = now()
                       WHERE job_id IN (SELECT job_id FROM job_queue WHERE status = 'DONE' AND ingested_at IS NULL
                                          AND (ingest_locked_at IS NULL OR ingest_locked_at < now() - make_interval(secs => %s))
                                        ORDER BY finished_at LIMIT %s FOR UPDATE SKIP LOCKED)
                       RETURNING job_id, kind, owner""", (lease_s, limit))
        rows = [dict(zip(("job_id", "kind", "owner"), r)) for r in cur.fetchall()]
    conn.commit()
    return rows


def mark_ingested(conn, job_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE job_queue SET ingested_at = now(), ingest_locked_at = NULL WHERE job_id = %s", (job_id.upper(),))
    conn.commit()


def stats(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT status, count(*) FROM job_queue GROUP BY status")
        by = {s: n for s, n in cur.fetchall()}
        cur.execute("SELECT coalesce(extract(epoch FROM now() - min(enqueued_at)), 0)::int FROM job_queue WHERE status = 'QUEUED'")
        oldest = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM job_queue WHERE status = 'DONE' AND ingested_at IS NULL")
        pending = cur.fetchone()[0]
    return {"queued": by.get("QUEUED", 0), "running": by.get("RUNNING", 0), "done": by.get("DONE", 0), "failed": by.get("FAILED", 0), "dead": by.get("DEAD", 0),
            "oldest_queued_s": oldest, "awaiting_follow_up": pending}


def sync_from_files(conn, out) -> int:
    """After a database reset or a first switch to queue mode: put every job file that is still REQUESTED or CLAIMED (so unfinished) on the queue. Returns how many were added."""
    n = 0
    for j in jobs.list(Path(out)):
        if j["status"] in ("REQUESTED", "CLAIMED", "TIMEOUT"):
            with conn.cursor() as cur:
                cur.execute("INSERT INTO job_queue (job_id, kind, owner) VALUES (%s,%s,%s) ON CONFLICT (job_id) DO NOTHING RETURNING job_id",
                            (j["id"], j["kind"], (j.get("request") or {}).get("user") or "local"))
                n += 1 if cur.fetchone() else 0
            conn.commit()
    return n


# ---------------------------------------------------------------------------------------------------------------------------- the worker
def worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


def run_one(out, wid: str, hf=None, kinds: list | None = None) -> str | None:
    """Claim one job and run it. Returns its id, or None when nothing was due. The provider's answer decides the row: DONE or FAILED; an exception of our own is retried with backoff."""
    from ..store import db
    out = Path(out)
    with db.connect() as conn:
        row = claim(conn, wid, kinds)
    if not row:
        return None
    jid = row["job_id"]
    try:
        cur = jobs.read(out, jid)
        if cur["status"] in ("DONE", "FAILED"):                     # an operator (or an earlier attempt) already finished it: just record that
            res = cur
        else:
            res = jobs.fulfil(out, jid, hf=hf)                     # no follow-up here: the server's ingest loop does that
        with db.connect() as conn:
            if res["status"] == "DONE":
                complete(conn, jid, True)
            else:
                complete(conn, jid, False, res.get("error") or res["status"])
    except Exception as e:                                          # our own trouble (disk, a bug): retry with backoff, never the provider's failure
        with db.connect() as conn:
            fail_infra(conn, jid, f"{type(e).__name__}: {e}")
    return jid


def work(out, wid: str | None = None, poll: float = POLL_S, once: bool = False, hf=None, kinds: list | None = None, stop: threading.Event | None = None, log=print) -> int:
    """The worker loop: reap lost rows now and then, run what is due, sleep when idle. `once` drains what is due now and returns. Returns how many jobs it ran."""
    from ..store import db
    wid = wid or worker_id()
    ran, last_reap = 0, 0.0
    while not (stop and stop.is_set()):
        if time.time() - last_reap > 60:
            last_reap = time.time()
            try:
                with db.connect() as conn:
                    lost = reap(conn)
                if lost:
                    log(f"reaped {lost} job(s) whose worker was lost")
            except Exception as e:
                log(f"reap failed: {e}")
        try:
            jid = run_one(out, wid, hf=hf, kinds=kinds)
        except Exception as e:                                       # the database went away: wait and try again
            log(f"queue unavailable: {e}")
            jid = None
            if once:
                break
        if jid:
            ran += 1
            log(f"{wid}: {jid} -> {jobs.read(Path(out), jid)['status']}")
            continue
        if once:
            break
        (stop.wait(poll) if stop else time.sleep(poll))
    return ran
