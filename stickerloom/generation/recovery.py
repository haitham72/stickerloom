"""Explicit human recovery: look locally, check once, continue the ticket, or buy a new request."""
from pathlib import Path
from .. import providers as _providers
from ..providers.base import ProviderJob
from . import jobs, model_catalog


def history(out, jid, action, detail, by="local"):
    job = jobs.read(out, jid)
    rows = job.get("history") or []
    return jobs.update(out, jid, history=rows + [{"ts": jobs._now(), "actor": "human", "by": by,
                                               "action": action, "detail": detail}])


def check(out, jid, hf=None, on_done=None, by="local"):
    job = jobs.read(out, jid)
    hf = hf or _providers.for_job(job.get("provider"))
    ticket = job.get("external_task_id")
    if not ticket:
        raise jobs.JobError("There is no provider ticket to check. Refresh locally, or price a new Retry.", 409)
    provider = hf.get(ticket)                         # exactly one read-only lookup; no cost/create/wait
    status = str(provider.get("status") or "unknown").lower()
    divergent = job["status"] in ("FAILED", "TIMEOUT") and status == "completed"
    message = ("Divergence, not a failure: the ticket says " + job["status"] +
               " while the provider says completed.") if divergent else "Provider says " + status + "."
    with jobs._paid(out):
        history(out, jid, "CHECK", message, by)
        jobs.update(out, jid, provider_check={"status": status, "checked_at": jobs._now(),
                                             "classification": "DIVERGENCE" if divergent else "MATCH",
                                             "message": message})
    if status != "completed":
        return jobs.read(out, jid)
    current = jobs.read(out, jid)
    held = current.get("result") or {}
    if held.get("file") and (Path(out) / held["file"]).is_file():
        return current
    url = provider.get("result_url")
    if not url:
        raise jobs.JobError(message + " The provider supplied no result URL; Check again later.", 409)
    jobs._begin_wait(out, jid)                        # the normal waiter cannot attach or charge again
    try:
        ext = Path(str(url).split("?")[0]).suffix.lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm"):
            ext = ".mp4" if job["kind"] == "video" else ".png"
        tmp = jobs.job_dir(out, job["id"]) / ("checked" + ext)
        hf.download(url, tmp)
        with jobs._paid(out):
            current = jobs.read(out, jid)
            if current["status"] == "DONE" and current.get("result") and (Path(out) / current["result"]["file"]).is_file():
                return current
            jobs.update(out, jid, status="CLAIMED", error=None, completed_at=None)
            result = jobs.done(out, jid, str(tmp), current.get("model") or (current.get("request") or {}).get("model") or "unknown",
                               cost=current.get("cost") if current.get("cost") is not None else current.get("cost_estimate"))
            history(out, jid, "RECONCILE", message + " Download attached to the same ticket; no new spend.", by)
        tmp.unlink(missing_ok=True)
    finally:
        jobs._end_wait(out, jid)
    if on_done:
        try:
            on_done(result)
        except Exception as e:
            jobs.update(out, jid, follow_up_error=str(e)[:300])
    return jobs.read(out, jid)


def continue_job(out, jid, by="local"):
    with jobs._paid(out):
        job = jobs.read(out, jid)
        if not job.get("external_task_id"):
            raise jobs.JobError("Continue needs a stored provider ticket. No job has been paid for; price a new Retry.", 409)
        raw = jobs._raw(out, jid)
        if jid in jobs._WAITING or (raw.get("waiting_pid") and jobs._pid_alive(raw["waiting_pid"])):
            raise jobs.JobBusy("This ticket is already being continued. Refresh or Check; nothing was paid twice.")
        job = jobs.resume(out, jid)
        return history(out, jid, "CONTINUE", "Wait for the same provider ticket; no new spend.", by)


def quote(out, jid, hf=None):
    job = jobs.read(out, jid)
    hf = hf or _providers.for_job(job.get("provider"))
    if job["status"] not in ("FAILED", "TIMEOUT"):
        raise jobs.JobError("Only a stalled job can be requested again. Refresh its local state.", 409)
    req = job.get("request") or {}
    kind = "video" if job["kind"] == "video" else "image"
    prov_name = getattr(hf, "name", None)
    model, params = model_catalog.resolve(kind, req.get("model"), req.get("options"), provider=prov_name)
    media = {}
    if kind == "video" and not req.get("t2v"):
        start = Path(req.get("start_image") or "")
        start = start if start.is_absolute() else Path(out) / start
        media["start_image"] = str(start)
        if req.get("loop") and model_catalog.find(kind, model, provider=prov_name).get("end_image"):
            media["end_image"] = str(start)
    if req.get("refs"):
        media["image_references"] = [str(Path(out) / r) if not Path(r).is_absolute() else r for r in req["refs"]]
    price = hf.cost(ProviderJob(kind=kind, model=model, params=params, prompt=req.get("prompt") or "", media=media))
    jobs.update(out, jid, retry_quote={"credits": price, "at": jobs._now()})
    return {"credits": price, "message": "Retry starts a new paid provider request. The original ticket may still be charged."}


def retry(out, jid, go=False, estimate=None, by="local"):
    with jobs._paid(out):
        job = jobs.read(out, jid)
        if job.get("retried_as"):
            return jobs.read(out, job["retried_as"])
        price = job.get("retry_quote") or {}
        if go is not True or estimate != price.get("credits") or not price or jobs._now() - price["at"] > 300:
            raise jobs.JobError("Retry SPENDS. Show a current retry estimate, then explicitly confirm go: true and that estimate.", 409)
        if job["status"] not in ("FAILED", "TIMEOUT"):
            raise jobs.JobError("This job is no longer stalled. Refresh before retrying.", 409)
        new = jobs.create(out, job["kind"], task=job.get("task"), generation=job.get("generation"),
                          request={**(job.get("request") or {}), "approved_cost": estimate}, provider=job.get("provider"))
        jobs.update(out, new["id"], retry_of=jid)
        jobs.update(out, jid, retried_as=new["id"])
        history(out, jid, "RETRY", f"New paid request {new['id']}; {estimate} credits explicitly approved.", by)
        return jobs.read(out, new["id"])
