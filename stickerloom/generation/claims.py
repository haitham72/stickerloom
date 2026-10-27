"""The pack claim ledger: which preset grid each pack session has taken, and which generations
each claim produced. File store leads, Postgres mirrors (`store/repo.save_pack_session`, `migrations/012_pack_claims.sql`).

  session  out/pack_sessions/<slug>.json      `pack-<slug>`, one per pack subject ("generate sticker pack for falcon" -> pack-falcon)
  claim    inside the session file: claims[]  `C###`, one global sequence over every session; one preset grid (core-v1, social-v1, ...) per claim
  lineage  claim.generations[]                {generation: G###, revision: 1.., ts}; regenerate appends, nothing is ever removed

A claim only moves forward: CLAIMED -> PLANNED -> REQUESTED -> DONE, and DONE -> REQUESTED again on regenerate (a failed job may be requested again too).
Every write happens under one lock (in-process + `out/.claims.lock` across processes), so two simultaneous `generate more` clicks serialize: the second
reads the first's claim and takes the next preset. A claim is written BEFORE anything is spent. Free: nothing here calls a provider."""
from __future__ import annotations

import json
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from ..runtime import atomic
from . import actions, prompter

ORDER = list(actions.PRESETS)                   # the claim queue: core-v1, social-v1, reactions-v1, daily-v1
STATUSES = ("CLAIMED", "PLANNED", "REQUESTED", "DONE")
GRIDS = {(3, 3), (2, 2)}                        # a preset holds nine actions: 3x3 takes all nine, 2x2 the first four (never reordered)
CLAIM_ID = re.compile(r"^C(\d{3,})$")
_LOCK = threading.RLock()


class ClaimError(Exception):
    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.code = code


def sessions_dir(out: Path) -> Path:
    return Path(out) / "pack_sessions"


def slug_of(subject: str) -> str:
    s = prompter.slug(str(subject or ""))
    if not s:
        raise ClaimError("Name the pack's subject first (for example: falcon)")
    return s


def _path(out: Path, slug: str) -> Path:
    return sessions_dir(out) / f"{slug}.json"


@contextmanager
def _held(out: Path):
    """One writer of the ledger: this process's threads, then other processes (`out/.claims.lock`, waited for; the OS drops it if a holder dies)."""
    from ..runtime.writer_lock import WriterLock
    with _LOCK:
        lock = WriterLock(Path(out), "a pack claim", ".claims.lock").acquire(wait=True)
        try:
            yield
        finally:
            lock.release()


def _read(p: Path) -> dict | None:
    try:
        s = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return s if isinstance(s, dict) else None


def _write(out: Path, s: dict) -> None:
    p = _path(out, s["slug"])
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(p, json.dumps(s, indent=2, ensure_ascii=False))
    try:
        from ..store import sync
        sync.sync_pack_session(out, s)          # best effort, never raises; only the real out/ writes through
    except Exception:
        pass


def list_sessions(out: Path) -> list[dict]:
    d = sessions_dir(out)
    rows = [s for f in sorted(d.glob("*.json")) if (s := _read(f))] if d.is_dir() else []
    return sorted(rows, key=lambda s: s.get("created", 0))


def get_session(out: Path, slug: str) -> dict:
    s = _read(_path(out, slug_of(slug.removeprefix("pack-"))))
    if s is None:
        raise ClaimError(f"There is no pack session called pack-{slug.removeprefix('pack-')}", 404)
    return s


def _next_claim_number(out: Path) -> int:
    nums = [int(m[1]) for s in list_sessions(out) for c in s.get("claims", []) if (m := CLAIM_ID.match(str(c.get("id", ""))))]
    return max(nums, default=0) + 1


def _locate(out: Path, claim_id: str) -> tuple[dict, dict]:
    for s in list_sessions(out):
        for c in s.get("claims", []):
            if c.get("id") == claim_id:
                return s, c
    raise ClaimError(f"There is no claim {claim_id}", 404)


def find_claim(out: Path, claim_id: str) -> dict:
    """The claim with its session's slug: {..claim, session: <slug>}."""
    s, c = _locate(out, str(claim_id).upper())
    return {**c, "session": s["slug"]}


def unclaimed(session: dict) -> list[str]:
    taken = {c.get("preset") for c in session.get("claims", [])}
    return [p for p in ORDER if p not in taken]


def current(claim: dict) -> str | None:
    """The claim's current generation: the latest one linked (= the latest DONE revision), else None."""
    gens = claim.get("generations") or []
    return gens[-1]["generation"] if gens else None


def _step(claim: dict, status: str, **extra) -> None:
    now = round(time.time(), 3)
    claim["status"] = status
    claim["updated"] = now
    claim.setdefault("history", []).append({"status": status, "ts": now, **{k: v for k, v in extra.items() if v is not None}})


def _new_session(out: Path, subject: str, owner: str | None, title: str | None) -> dict:
    slug = slug_of(subject)
    return {"id": f"pack-{slug}", "slug": slug, "subject": str(subject).strip(), "title": (title or str(subject).strip()).strip(),
            "owner": owner, "pack_id": None, "created": round(time.time(), 3), "claims": []}


def _plan(subject: str, preset: str, grid: tuple) -> dict:
    """The sheet plan for one claim: the preset's stored actions through the existing templates (face v4), never guessed labels."""
    return prompter.expand(subject, grid, face=True, preset=preset)


def _claim_next(out: Path, s: dict, grid: tuple, plan: bool) -> dict | None:
    free = unclaimed(s)
    if not free:
        return None
    n = _next_claim_number(out)
    claim = {"id": f"C{n:03d}", "preset": free[0], "grid": list(grid), "status": "CLAIMED", "created": round(time.time(), 3),
             "jobs": [], "generations": [], "history": []}
    _step(claim, "CLAIMED")
    s["claims"].append(claim)
    _write(out, s)                                   # the claim is on disk before anything else happens
    if plan:
        claim["plan"] = _plan(s["subject"], claim["preset"], grid)
        _step(claim, "PLANNED")
        _write(out, s)
    return claim


def _grid(grid) -> tuple:
    g = tuple(grid) if not isinstance(grid, str) else tuple(int(x) for x in grid.lower().split("x"))
    if g not in GRIDS:
        raise ClaimError("A preset pack's grid is 3x3 or 2x2")
    return g


def claim_next(out: Path, subject: str, grid=(2, 2), owner: str | None = None, title: str | None = None, plan: bool = True) -> dict:
    """Open the subject's pack session when it does not exist yet and claim its next unclaimed preset, in one lock hold.
    {session, claim, created: bool (the session is new), complete: bool}. When all four presets are claimed, `claim` is None and `complete` True:
    the caller says so in words with the next choices (a custom pick from the bank, or a new pack); nothing is repeated silently."""
    g = _grid(grid)
    with _held(out):
        slug = slug_of(subject)
        s = _read(_path(out, slug))
        created = s is None
        if created:
            s = _new_session(out, subject, owner, title)
            _write(out, s)
        claim = _claim_next(out, s, g, plan)
        return {"session": s, "claim": claim, "created": created, "complete": claim is None and not unclaimed(s)}


def _update(out: Path, claim_id: str, fn) -> dict:
    with _held(out):
        s, c = _locate(out, str(claim_id).upper())
        fn(s, c)
        _write(out, s)
        return {**c, "session": s["slug"]}


def plan_claim(out: Path, claim_id: str) -> dict:
    """CLAIMED -> PLANNED (a claim made with plan=False, or one whose plan failed)."""
    def go(s, c):
        if c["status"] != "CLAIMED":
            raise ClaimError(f"{c['id']} is already {c['status'].lower()}", 409)
        c["plan"] = _plan(s["subject"], c["preset"], tuple(c["grid"]))
        _step(c, "PLANNED")
    return _update(out, claim_id, go)


def mark_requested(out: Path, claim_id: str, job: str) -> dict:
    """A paid job was created for this claim: PLANNED -> REQUESTED, DONE -> REQUESTED (regenerate), REQUESTED -> REQUESTED (the last job failed)."""
    if not str(job or "").strip():
        raise ClaimError("Which job? The request needs its job id")

    def go(s, c):
        if c["status"] == "CLAIMED":
            raise ClaimError(f"{c['id']} has no plan yet; plan it first", 409)
        if job in c.get("jobs", []):
            return                                     # the same job twice is one request
        c.setdefault("jobs", []).append(job)
        _step(c, "REQUESTED", job=job, regenerate=True if c["generations"] else None)
    return _update(out, claim_id, go)


def link_generation(out: Path, claim_id: str, gid) -> dict:
    """The job's sheet became a generation: REQUESTED -> DONE, appended as the next revision. Linking the same generation twice is one link."""
    g = gid if isinstance(gid, str) and gid.upper().startswith("G") else f"G{int(gid):03d}"

    def go(s, c):
        if any(x["generation"] == g for x in c.get("generations", [])):
            return
        if c["status"] != "REQUESTED":
            raise ClaimError(f"{c['id']} is {c['status'].lower()}, not waiting for a generation", 409)
        rev = len(c["generations"]) + 1
        c["generations"].append({"generation": g, "revision": rev, "ts": round(time.time(), 3)})
        _step(c, "DONE", generation=g, revision=rev)
    return _update(out, claim_id, go)
