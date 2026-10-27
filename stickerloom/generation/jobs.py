"""Phase 2: jobs as files. stickerloom writes the job; it is fulfilled by `fulfil()` (the active generation provider, see stickerloom/providers/) or by an operator
session using the same claim/done/fail calls. The engine stays ignorant of who fulfils the job.

Ticket first (CLAUDE.md rule 10): claim() writes external_task_id BEFORE the operator waits, so a
crashed operator re-run resumes by ticket instead of paying twice."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

KINDS = ("sheet", "video", "single")
STATUSES = ("REQUESTED", "CLAIMED", "DONE", "FAILED", "TIMEOUT")
PROVIDER = "higgsfield-cli"

KIND_CALL = {"sheet": "IMAGE_SHEET", "video": "VIDEO", "single": "IMAGE_SINGLE"}


class JobError(Exception):
    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.code = code


class JobBusy(JobError):
    """Another run owns this job now (it was claimed, finished or failed while this one waited for the paid lock): nothing was paid and nothing is marked failed."""

    def __init__(self, msg: str):
        super().__init__(msg, 409)


def jobs_dir(out: Path) -> Path:
    d = Path(out) / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def timeout_s() -> int:
    try:
        return max(60, int(os.environ.get("STICKERLOOM_JOB_TIMEOUT", 20 * 60)))
    except ValueError:
        return 20 * 60


def _now() -> float:
    return round(time.time(), 3)


def next_id(out: Path) -> str:
    from ..runtime import names
    n = 0
    for f in jobs_dir(out).glob("J*.json"):
        i = names.folder_id(f.stem)             # J057.json, or J058-dog_as_banana-sheet.json since 2026-10-04
        if i and i[0] == "J":
            n = max(n, int(i[1:]))
    return f"J{n + 1:03d}"


def _path(out: Path, jid: str) -> Path:
    """The file of job `jid`: the bare `J057.json` of an older job, else its labelled `J058-dog_as_banana-sheet.json`."""
    jid = str(jid or "").upper()
    if not re.fullmatch(r"J\d{3,}", jid):
        raise JobError(f"no job {jid}", 404)
    d = jobs_dir(out)
    p = d / f"{jid}.json"
    if not p.is_file():
        p = next((f for f in sorted(d.glob(f"{jid}-*.json")) if f.is_file()), p)
    if p.resolve().parent != d.resolve() or not p.is_file():
        raise JobError(f"no job {jid}", 404)
    return p


def job_dir(out: Path, jid: str) -> Path:
    """The folder of job `jid`'s files (result, download): named like its json file, `J058-dog_as_banana-sheet/` (bare `J057/` for an older job)."""
    try:
        return jobs_dir(out) / _path(out, jid).stem
    except JobError:
        return jobs_dir(out) / str(jid).upper()


def _label(out: Path, kind: str, task: str | None, generation: str | None, request: dict | None) -> list[str]:
    """What a new job's file name says after its id: the subject (the task's, else the batch's, else the request's label) and the kind."""
    subject = None
    try:
        if task:
            from . import tasks as _t
            subject = _t.read_task(out, str(task)).get("name_key")
    except Exception:
        pass
    if not subject and generation:
        try:
            from ..flow import pipeline as pl
            subject = pl.read_result(Path(out), int(str(generation).upper().lstrip("G")))["task_slug"]
        except Exception:
            pass
    if not subject:
        subject = (request or {}).get("label")
    return [w for w in (subject, kind) if w]


def _write(p: Path, job: dict) -> dict:
    from ..runtime import activity, atomic
    try:
        old = json.loads(p.read_text(encoding="utf-8")).get("status") if p.is_file() else None
    except (OSError, ValueError):
        old = None
    atomic.write_text(p, json.dumps(job, indent=2, ensure_ascii=False))
    activity.job(old, job)            # one line in the terminal when the status changed: generating / received / failed
    try:  # Phase 3A write-through: a claimed job is a provider task row; best effort, never raises
        from ..store import sync
        sync.sync_job(p.parent.parent, job)
    except Exception:
        pass
    return job


def _expired(job: dict) -> bool:
    if job["status"] not in ("REQUESTED", "CLAIMED"):
        return False
    ref = job.get("claimed_at") or job.get("created_at") or 0
    return _now() - ref > timeout_s()


def read(out: Path, jid: str) -> dict:
    job = json.loads(_path(out, jid).read_text(encoding="utf-8"))
    if _expired(job):
        job = {**job, "status": "TIMEOUT"}
    return job


def list(out: Path, status: str | None = None) -> list[dict]:
    rows = []
    for f in sorted(jobs_dir(out).glob("J*.json")):
        try:
            job = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if _expired(job):
            job = {**job, "status": "TIMEOUT"}
        if status and job["status"] != status.upper():
            continue
        rows.append(job)
    return rows


def create(out: Path, kind: str, task: str | None = None, generation: str | None = None,
           request: dict | None = None, provider: str | None = None) -> dict:
    if kind not in KINDS:
        raise JobError(f"kind must be one of {KINDS}")
    if provider is None:                                   # a job belongs to the provider that is active when it is created
        from .. import providers as _providers
        provider = _providers.current().job_provider
    from ..runtime import names
    jid = next_id(out)
    return _write(jobs_dir(out) / f"{names.folder(jid, *_label(out, kind, task, generation, request))}.json", {
        "id": jid, "kind": kind, "task": task, "generation": generation,
        "provider": provider, "model": None,
        "request": request or {},
        "status": "REQUESTED", "external_task_id": None,
        "result": None, "cost": None, "error": None,
        "created_at": _now(), "claimed_at": None, "completed_at": None})


def claim(out: Path, jid: str, ticket: str) -> dict:
    """Store the provider ticket BEFORE waiting (idempotent retry). Mirrors it into the task file."""
    p = _path(out, jid)
    job = json.loads(p.read_text(encoding="utf-8"))
    if job["status"] not in ("REQUESTED", "TIMEOUT"):
        raise JobError(f"{job['id']} is {job['status']}, cannot claim", 409)
    if not str(ticket or "").strip():
        raise JobError("a ticket is required (the provider job id)", 400)
    job.update(status="CLAIMED", external_task_id=str(ticket).strip(), claimed_at=_now(), error=None)
    _write(p, job)
    if job.get("task"):  # the task ticket is stored first too (CLAUDE.md rule 10, "Hard truth", on the file store)
        try:
            from . import tasks as _t
            t = _t.read_task(out, str(job["task"]))
            t["external_task_id"] = job["external_task_id"]
            t["status"] = "running"
            _t._write(_t.tasks_dir(Path(out)) / f"{int(t['number']):03d}.json", t)
        except Exception:
            pass
    return read(out, jid)


def done(out: Path, jid: str, file: str, model: str, cost=None) -> dict:
    """Attach the fulfilled result. A sheet job then starts the stills run exactly as a prepared sheet does (S3)."""
    from . import model_calls as _mc
    p = _path(out, jid)
    job = json.loads(p.read_text(encoding="utf-8"))
    if job["status"] != "CLAIMED":
        raise JobError(f"{job['id']} is {job['status']}, claim it first", 409)
    src = Path(str(file or ""))
    if not src.is_file():
        raise JobError(f"result file not found: {file}", 400)
    if not str(model or "").strip():
        raise JobError("the model name is required", 400)
    dest = job_dir(out, job["id"]) / ("result" + src.suffix.lower())
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    t0 = _now()
    job.update(status="DONE", model=str(model).strip(), completed_at=t0, error=None,
               result={"file": str(dest.relative_to(Path(out))).replace("\\", "/"),
                       "sha256": digest, "bytes": dest.stat().st_size})
    if cost is not None:
        try:
            job["cost"] = float(cost)
        except (TypeError, ValueError):
            raise JobError("cost must be a number", 400)
    _write(p, job)
    _mc.append(out, KIND_CALL[job["kind"]], job["provider"], job["model"], status="OK",
               latency_ms=int((t0 - (job.get("claimed_at") or t0)) * 1000),
               cost=job["cost"], extra={"job": job["id"], "sha256": digest, "external_task_id": job.get("external_task_id"),
                                        "task": job.get("task"), "params": job.get("params"), "output": job["result"]["file"]})
    return job


def fail(out: Path, jid: str, reason: str) -> dict:
    from . import model_calls as _mc
    p = _path(out, jid)
    job = json.loads(p.read_text(encoding="utf-8"))
    if job["status"] not in ("REQUESTED", "CLAIMED", "TIMEOUT"):
        raise JobError(f"{job['id']} is {job['status']}", 409)
    job.update(status="FAILED", error=str(reason or "unknown")[:500], completed_at=_now())
    _write(p, job)
    _mc.append(out, KIND_CALL[job["kind"]], job["provider"], job.get("model") or "unknown",
               status="ERROR", error=job["error"], extra={"job": job["id"]})
    try:                                             # a failed paid job is a problem record (flow/problems.py); never stops the failure from being recorded
        from ..flow import problems
        problems.open_auto(out, source="job", issue="stuck_job", what=f"{job['kind']} job failed: {job['error']}", where=f"{job['kind']}/{job.get('model') or 'unknown'}",
                          context={"job": job["id"], "generation": job.get("generation"), "paid": bool(job.get("external_task_id"))})
    except Exception:
        pass
    return job


def requeue(out: Path, jid: str) -> dict:
    """A human re-queues a TIMEOUT or FAILED job; a new claim starts it again (never an automatic retry). A job that already holds a provider ticket
    is paid for: it goes back to CLAIMED (`resume`) so the SAME provider job is waited for, never created and charged a second time."""
    p = _path(out, jid)
    job = json.loads(p.read_text(encoding="utf-8"))
    if _expired(job):
        job = {**job, "status": "TIMEOUT"}
    if job["status"] not in ("TIMEOUT", "FAILED"):
        raise JobError(f"{job['id']} is {job['status']}, nothing to re-queue", 409)
    if job.get("external_task_id"):
        return resume(out, jid)
    job.update(status="REQUESTED", error=None, claimed_at=None, created_at=_now())  # a fresh waiting period
    return _write(p, job)


WAIT_RETRIES = 3           # a 503 while WAITING is usually one poll: retry it here before declaring anything, so a paid video is not lost to a single hiccup (J048)


def _wait_with_retry(hf, ticket: str, timeout_s: int, tries: int = WAIT_RETRIES) -> dict:
    """`generate wait`, retried while the provider answers with a transient failure (5xx, timeout, rate limit). The ticket never changes, so this can never create a second paid job. A final failure is re-raised for the caller to classify."""
    from ..providers.base import ProviderError          # a local import, as in fulfil(): nothing here is needed unless a wait really fails
    last: Exception | None = None
    for attempt in range(1, max(1, tries) + 1):
        try:
            return hf.wait(ticket, timeout_s=timeout_s)
        except ProviderError as e:
            if not getattr(e, "transient", False) or attempt >= max(1, tries):
                raise
            last = e
            time.sleep(min(30, 2 ** attempt))
    raise last  # unreachable in practice: the loop either returns or raises above


def resume(out: Path, jid: str) -> dict:
    """A human retry of a FAILED or TIMEOUT job. With a Higgsfield ticket the job goes back to CLAIMED, so `fulfil` waits for the SAME Higgsfield job again (a transient
    503 while waiting must not lose a paid video, and must not pay twice). Without a ticket nothing was created yet: it is simply requested again."""
    cur = read(out, jid)
    if cur["status"] not in ("FAILED", "TIMEOUT"):
        raise JobError(f"{cur['id']} is {cur['status']}, nothing to retry", 409)
    p = _path(out, jid)
    job = json.loads(p.read_text(encoding="utf-8"))
    if job.get("external_task_id"):
        job.update(status="CLAIMED", error=None, claimed_at=_now(), completed_at=None, stage="working")
    else:
        job.update(status="REQUESTED", error=None, claimed_at=None, completed_at=None, created_at=_now())
    return _write(p, job)


def update(out: Path, jid: str, **fields) -> dict:
    """Merge bookkeeping fields (params, cost_estimate, generation) into a job file."""
    p = _path(out, jid)
    job = json.loads(p.read_text(encoding="utf-8"))
    job.update(fields)
    return _write(p, job)


def dismiss(out: Path, jid: str, by: str = "local") -> dict:
    """Take a finished or failed job off the queue for good, for every browser and every person (`dismissed: {at, by}` on the job). The job itself stays:
    its file, its ticket, its cost and the Users page keep it. A job still running cannot be dismissed (409): it would vanish while it can still spend."""
    p = _path(out, jid)
    job = json.loads(p.read_text(encoding="utf-8"))
    if job["status"] in ("REQUESTED", "CLAIMED") and not _expired(job):          # an expired one is listed as TIMEOUT: it can be removed like one
        raise JobError(f"{job['id']} is still {job['status']}: wait for it to finish before removing it", 409)
    job.setdefault("dismissed", {"at": _now(), "by": str(by or "local")})
    return _write(p, job)


def attach_generation(out: Path, jid: str, gid: int) -> dict:
    return update(out, jid, generation=f"G{int(gid):03d}")


_PAID = threading.Lock()      # CREATING a paid provider job (the cap check, the call, the stored ticket) is one at a time, whoever asks
_SLOT_GUARD = threading.Lock()
_SLOTS: dict = {}


def paid_parallel() -> int:
    """How many provider jobs may be in flight (created, being waited for) at once. "all at once" for several sheets / effects. STICKERLOOM_PAID_PARALLEL, default 3, 1 = the old one-at-a-time."""
    try:
        return max(1, min(8, int(os.environ.get("STICKERLOOM_PAID_PARALLEL", "3"))))
    except ValueError:
        return 3


@contextlib.contextmanager
def _slot():
    """One of the `paid_parallel()` places for a provider job that is being waited for."""
    n = paid_parallel()
    with _SLOT_GUARD:
        sem = _SLOTS.get(n) or _SLOTS.setdefault(n, threading.BoundedSemaphore(n))
    with sem:
        yield


_WAITING: set = set()


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def _begin_wait(out: Path, jid: str) -> None:
    """One waiter per provider job. A resume of a job whose ticket another thread (or another live process) is already waiting for is refused as busy, the way the old whole-call lock
    refused it: the waiting is no longer under a lock, so it is marked on the job."""
    with _SLOT_GUARD:
        pid = _raw(out, jid).get("waiting_pid")
        if jid in _WAITING or (pid and int(pid) != os.getpid() and _pid_alive(pid)):
            raise JobBusy(f"{jid} is already being waited for")
        _WAITING.add(jid)
    try:
        update(out, jid, waiting_pid=os.getpid())
    except Exception:
        pass


def _end_wait(out: Path, jid: str) -> None:
    with _SLOT_GUARD:
        _WAITING.discard(jid)
    try:
        update(out, jid, waiting_pid=None)
    except Exception:
        pass


def _inflight(out: Path) -> float:
    """Credits of the provider jobs that are created but not finished: the daily cap counts them, or three jobs started together would each see an empty ledger."""
    total = 0.0
    for f in jobs_dir(out).glob("J*.json"):
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        # CLAIMED and TIMEOUT both mean "created at the provider, not finished": a job parked as TIMEOUT by a 5xx is still rendering there and will still be charged, so it must keep counting (J048, 2026-10-03). A FAILED job with a ticket is deliberately NOT counted: resume() puts it back on the same ticket, so charging the cap for it again would double-count.
        if j.get("status") in ("CLAIMED", "TIMEOUT") and j.get("external_task_id"):
            total += float(j.get("cost_estimate") or 0)
    return total


@contextlib.contextmanager
def _paid(out: Path):
    """The in-process lock covers threads; the file lock under out/ covers worker processes (`stickerloom worker`), so two paid CREATIONS never overlap on one machine. It is held only while
    a job is decided, priced, created and its ticket stored; the wait for the result happens outside it (`_slot` bounds how many wait at once)."""
    from ..runtime.writer_lock import WriterLock
    with _PAID:
        lock = WriterLock(Path(out), "a paid provider call", ".paid.lock").acquire(wait=True)
        try:
            yield
        finally:
            lock.release()


def _daily_cap() -> float | None:
    try:
        v = float(os.environ.get("STICKERLOOM_DAILY_CREDITS", ""))
        return v if v > 0 else None
    except ValueError:
        return None


def _raw(out: Path, jid: str) -> dict:
    """The job file as stored (`read` turns an old CLAIMED into a virtual TIMEOUT; the paid-call decisions need what is really on disk)."""
    return json.loads(_path(out, jid).read_text(encoding="utf-8"))


def fulfil(out: Path, jid: str, hf=None, on_done=None) -> dict:
    """Run one job through its provider (`job["provider"]`, else the active one). Ticket first: the job is created WITHOUT waiting, its id is stored with `claim` before the
    wait begins, and a job that is already CLAIMED (a crashed run) resumes by its ticket instead of paying twice. Never retries a paid call.
    Returns the job (DONE or FAILED); `on_done(job)` runs after a successful completion."""
    from . import model_calls as _ledger, model_catalog as _mcat
    from .. import providers as _providers
    from ..providers.base import ProviderError, ProviderJob
    out = Path(out)
    job = read(out, jid)
    raw = _raw(out, jid)
    hf = hf or _providers.for_job(job.get("provider"))
    prov_name = getattr(hf, "name", None)          # a test Fake has none: the catalog falls back to the active provider
    resume = raw["status"] == "CLAIMED" and bool(raw.get("external_task_id"))        # a stored ticket is waited for, even when the wait timed out meanwhile
    if not resume and job["status"] not in ("REQUESTED", "TIMEOUT"):
        raise JobError(f"{job['id']} is {job['status']}, nothing to run", 409)
    if not resume and raw.get("external_task_id"):                                    # paid for already: only `retry` (resume) may wait for it, nothing creates a second one
        raise JobError(f"{job['id']} already holds the provider job {raw['external_task_id']}: use Retry, it waits for the same job without a second charge", 409)
    req = job.get("request") or {}
    kind = "video" if job["kind"] == "video" else "image"
    try:
        model, params = _mcat.resolve(kind, req.get("model"), req.get("options"), provider=prov_name)
        prompt = str(req.get("prompt") or "")
        if not prompt.strip():
            raise JobError("the job has no prompt", 400)
        media = {}
        refs = [Path(r) if Path(r).is_absolute() else out / r for r in (req.get("refs") or [])]
        if refs:
            if not _mcat.find(kind, model, provider=prov_name).get("refs"):
                raise JobError(f"{_mcat.find(kind, model, provider=prov_name)['label']} does not take reference images", 400)
            missing = [str(r) for r in refs if not r.is_file()]
            if missing:
                raise JobError(f"reference image missing: {missing[0]}", 400)
            media["image_references"] = [str(r) for r in refs]
        if kind == "video" and req.get("t2v"):             # a text-only video (the particle effects): no start image, the clip begins and ends on an empty screen
            pass
        elif kind == "video":
            start = Path(str(req.get("start_image") or ""))
            start = start if start.is_absolute() else out / start
            if not start.is_file():
                raise JobError(f"the start image is missing: {start}", 400)
            media["start_image"] = str(start)
            if _mcat.find("video", model, provider=prov_name).get("end_image") and req.get("loop", False):
                media["end_image"] = str(start)
        pjob = ProviderJob(kind=kind, model=model, params=params, prompt=prompt, media=media)
        with _slot():                                                # at most paid_parallel() provider jobs exist at once: the place is taken BEFORE a job is created
            with _paid(out):
                cur = _raw(out, jid)                                                      # read again: another run may have taken this job while we waited for the lock
                if resume and not (cur["status"] == "CLAIMED" and cur.get("external_task_id") == raw["external_task_id"]):
                    raise JobBusy(f"{jid} was taken by another run")
                if not resume and (cur["status"] not in ("REQUESTED", "TIMEOUT") or cur.get("external_task_id")):
                    raise JobBusy(f"{jid} was taken by another run")
                if resume:
                    ticket, est = job["external_task_id"], job.get("cost_estimate")
                else:
                    est = hf.cost(pjob)
                    if req.get("approved_cost") is not None and est != req["approved_cost"]:
                        raise JobError("The Retry price changed. No request was created; refresh the price and confirm again.", 409)
                    cap = _daily_cap()
                    if cap is not None and _ledger.spent_today(out) + _inflight(out) + est > cap:
                        raise JobError(f"the daily credit cap ({cap:g}) would be exceeded by this {est:g}-credit call (counting the jobs already running)", 402)
                    ticket = hf.create_video(pjob) if kind == "video" else hf.create_image(pjob)
                    claim(out, jid, ticket)                      # IMMEDIATELY, before waiting
                    update(out, jid, params=dict(params, **({"references": len(refs)} if refs else {})), cost_estimate=est, model=model, stage="working")
                if resume:
                    update(out, jid, stage="working")
            # waiting is outside the creation lock: several jobs wait together, up to paid_parallel()
            _begin_wait(out, jid)
            try:
                res = _wait_with_retry(hf, ticket, timeout_s=max(120, min(timeout_s(), 3600) - 60))
                update(out, jid, stage="downloading")
                ext = (res["result_url"].split("?")[0].rsplit(".", 1)[-1] or "bin")[:5].lower()
                tmp = job_dir(out, jid) / f"download.{ext}"
                hf.download(res["result_url"], tmp)
                with _paid(out):
                    job = done(out, jid, str(tmp), model, cost=est)         # under the lock: the next cap check reads a spend that includes this one
            finally:
                _end_wait(out, jid)
        try:
            tmp.unlink()
        except OSError:
            pass
    except JobBusy:
        return read(out, jid)                                         # the other run owns it: report what is true, pay nothing, fail nothing
    except (JobError, _mcat.CatalogError, ProviderError) as e:
        code = getattr(e, "code", 500)
        # A 5xx / timeout / rate limit while WAITING is not a failure: the provider may still be rendering the job we already paid for (J048,2026-10-03: a 503 wrote FAILED over a video that completed). Such a job goes to TIMEOUT, which resume() puts back on the SAME ticket, and which _inflight keeps counting so the daily cap still sees it. A 4xx, a crash in a check, or a failure with no ticket stays final.
        if getattr(e, "transient", False) and read(out, jid).get("external_task_id"):
            from . import model_calls as _mc
            cur = update(out, jid, status="TIMEOUT", stage="waiting", error=str(e)[:500])
            _mc.append(out, KIND_CALL[cur.get("kind") or "video"], cur.get("provider") or "higgsfield-cli",
                       cur.get("model") or "unknown", status="ERROR", error=str(e),
                       extra={"job": jid, "transient": True})
            return read(out, jid)
        if job["status"] in ("REQUESTED", "CLAIMED", "TIMEOUT") or read(out, jid)["status"] in ("REQUESTED", "CLAIMED", "TIMEOUT"):
            fail(out, jid, str(e))
        return read(out, jid)
    if on_done:
        try:
            on_done(job)
        except Exception as e:                             # the sheet is safe in out/jobs; say what did not follow
            job = update(out, jid, follow_up_error=str(e)[:300])
    return job
