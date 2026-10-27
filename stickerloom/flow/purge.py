"""Emptying the trash: the purge of removed batches and deleted packs. Remove batch, Delete pack and a deleted particle set are all SOFT; this is the
one place where something is deleted for good, and it only ever reaches what is already in the trash.

What it does, per item (the order is the resume story: the last thing to go is the thing that makes the item show up in the list, so a purge that stops half-way is simply run again):
  * a BATCH: (1) its Postgres rows (`store/purge_rows.py`: the pool rows with their embeddings, assets, events, stickers and video sheets nothing points at; `reviews` and `tasks` stay,
    the kept rows become `PURGED` tombstones), (2) a ledger line, (3) its whole trash entry `out/trash/batches/G###` + `G###.meta.json`, (4) a second ledger line. Stickers a person
    already added to a pack are copies in the library and stay in their packs.
  * a PACK: (1) its files under `out/library/files/` (a file another pack, live or trashed, also holds stays for that pack), (2) its record in library.json, (3) a ledger line.
    There is no pack row in Postgres (a pack is library.json), so there is nothing to clean there.
Never silent, never partial about what it asks: a sticker file another pack holds, or a batch whose stickers sit in the shared pool (search can offer them to other requests), is REFUSED in words
until the person confirms THAT item (`confirm_shared`); "delete all" never carries that confirmation, it skips such items and says so. A job in flight refuses a batch (409 in words).
"Delete all" needs the typed phrase `purge N` naming how many items it will delete, and the server compares N with the trash as it is NOW.

The record: `out/trash/purged.jsonl`, append-only, one line per phase with `actor: "human"`, who, when, and what went. It is also the memory that keeps a purged batch number taken
(`pipeline.next_gid`), because the database keeps the number's history. `reviews` are never touched.

A purge runs in ONE background thread (`PurgeTask`), never in the request: the route validates, starts it and waits a moment; a small purge answers 200 with the result, a long one 202 and the
caller polls `GET /api/trash/purges/{id}`. The task's state is also written to `out/trash/purge-state.json`; a state that says `running` with no live thread reads `interrupted` and the
items are still in the list. Plain functions over files, library.json and the store; the engine never imports this (CLAUDE.md rule 3)."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from ..media.library import LibraryError
from ..runtime import atomic
from ..store import purge_rows
from . import batches
from . import pipeline as pl

WAIT = 1.5                      # seconds a request waits for the purge it started before it answers 202
_LOCK = threading.RLock()
_TASKS: dict[str, "PurgeTask"] = {}
_LEDGER_LOCK = threading.Lock()


def state_file(out: Path) -> Path:
    return Path(out) / "trash" / "purge-state.json"


def phrase(n: int) -> str:
    """The typed confirmation of "delete all": the word and the count it deletes."""
    return f"purge {int(n)}"


# ---------------------------------------------------------------- the ledger
def _ledger_lines(out: Path) -> list[dict]:
    try:
        lines = pl.purge_ledger(out).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict):
            rows.append(r)
    return rows


def ledger(out: Path, limit: int = 50) -> list[dict]:
    """The last `limit` lines of the purge record, newest first."""
    return list(reversed(_ledger_lines(Path(out))))[:limit]


def _record(out: Path, kind: str, phase: str, ident: str, by: str, **fields) -> None:
    """Append one line (once per kind/id/phase: a resumed purge does not write it twice)."""
    f = pl.purge_ledger(out)
    with _LEDGER_LOCK:
        if any(r.get("kind") == kind and r.get("id") == ident and r.get("phase") == phase for r in _ledger_lines(out)):
            return
        f.parent.mkdir(parents=True, exist_ok=True)
        line = {"ts": round(time.time(), 3), "kind": kind, "id": ident, "phase": phase, "actor": "human", "by": by, **fields}
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- what is in the trash
def _copies_in_packs(lib, gid: int) -> list[dict]:
    """The packs (live or trashed) that hold a copy of a sticker of this batch: they keep it."""
    g, rows = f"G{gid:03d}", []
    snap = lib.snapshot()["packs"]
    for trashed, packs in ((False, snap), (True, lib.trashed_packs())):
        for p in packs:
            n = sum(1 for s in p["stickers"] if str((s.get("source") or {}).get("generation") or "") == g)
            if n:
                rows.append({"id": p["id"], "name": p["name"], "stickers": n, "trashed": trashed})
    return rows


def _batch_item(out: Path, lib, gid: int) -> dict:
    row = batches.describe(out, gid)
    row["type"] = "batch"
    row["db"] = purge_rows.describe(out, gid)
    row["copies_in_packs"] = _copies_in_packs(lib, gid)
    pool = int(row["db"].get("shared_in_pool") or 0) if row["db"].get("available") else 0
    row["shared"] = {"pool": pool, "packs": [c["name"] for c in row["copies_in_packs"]]}
    row["needs_confirm"] = pool > 0
    row["confirm_words"] = (f"{row['id']} has {pool} sticker{'s' if pool != 1 else ''} in the shared pool: search can offer them to other requests, and deleting the batch for good takes them out of it. "
                            + ("The copies already in " + ", ".join(f"“{n}”" for n in row["shared"]["packs"]) + " stay where they are. " if row["shared"]["packs"] else "") + "Confirm to go on.") if pool else None
    row["blocked"] = (f"{row['id']} cannot be deleted for good while {', '.join(row['in_flight'])} is still working on it. Wait for it to finish." if row["in_flight"] else None)
    return row


def _sets_by_pack(out: Path, lib) -> dict[str, list[str]]:
    try:
        from . import particle_sets as fx_sets
        by: dict[str, list[str]] = {}
        for s in fx_sets.list_sets(out, lib):
            for pid in s.get("packs") or []:
                by.setdefault(str(pid), []).append(s.get("name") or s.get("id"))
        return by
    except Exception:
        return {}


def _pack_item(lib, p: dict, sets: dict[str, list[str]]) -> dict:
    rep = lib.trash_report(p["id"])
    rep["type"] = "pack"
    rep["files_total"] = sum(1 for f in rep["files"] if not f["missing"] and not f["shared"])
    rep["particle_sets"] = sets.get(p["id"], [])
    rep["needs_confirm"] = bool(rep["shared"])
    rep["confirm_words"] = ("; ".join(f"“{s['name']}” is also in " + ", ".join(h["name"] + (" (in the trash)" if h["trashed"] else "") for h in s["also_in"]) for s in rep["shared"])
                            + f". Deleting “{rep['name']}” for good keeps those files for the other packs. Confirm to go on.") if rep["shared"] else None
    rep["blocked"] = None
    return rep


def listing(out: Path, lib) -> dict:
    """`GET /api/trash`: every removed batch and deleted pack with exactly what a purge would remove, what needs a confirmation first, and what "delete all" would do."""
    out = Path(out)
    bs = []
    for gid in pl.removed_ids(out):
        try:
            bs.append(_batch_item(out, lib, gid))
        except pl.PipelineError:
            continue
    sets = _sets_by_pack(out, lib)
    ps = [_pack_item(lib, p, sets) for p in lib.trashed_packs()]
    items = bs + ps
    deletable = [i for i in items if not i["needs_confirm"] and not i["blocked"]]
    return {"batches": bs, "packs": ps,
            "totals": {"items": len(items), "batches": len(bs), "packs": len(ps), "stickers": sum(i["stickers"] for i in items),
                       "files": sum(i["files_total"] for i in items), "bytes": sum(i["bytes"] for i in items),
                       "needs_confirm": sum(1 for i in items if i["needs_confirm"]), "blocked": sum(1 for i in items if i["blocked"])},
            "purge_batches": {"count": sum(1 for i in deletable if i["type"] == "batch"), "phrase": phrase(sum(1 for i in deletable if i["type"] == "batch"))},
            "purge_all": {"count": len(deletable), "phrase": phrase(len(deletable)),
                          "skipped": [{"kind": i["type"], "id": i["id"], "why": i["blocked"] or i["confirm_words"]} for i in items if i not in deletable]},
            "database": purge_rows.usable(out)[1] or "reachable", "purge": last(out), "record": ledger(out, 20)}


# ---------------------------------------------------------------- the task
class PurgeTask:
    def __init__(self, out: Path, lib, items: list[dict], by: str, confirm_shared: bool, refused: list[dict]):
        n = len(_TASKS) + 1
        existing = _state(out)
        if existing and str(existing.get("id", "")).startswith("PG") and str(existing["id"])[2:].isdigit():
            n = max(n, int(str(existing["id"])[2:]) + 1)
        self.id = f"PG{n:03d}"
        self.out, self.lib, self.items, self.by, self.confirm_shared = Path(out), lib, items, by, confirm_shared
        self.refused, self.results, self.error = refused, [], None
        self.status, self.started, self.finished = "running", round(time.time(), 3), None
        self.event = threading.Event()

    def view(self) -> dict:
        return {"id": self.id, "status": self.status, "total": len(self.items), "done": len(self.results), "items": [{"kind": i["kind"], "id": i["id"]} for i in self.items],
                "results": list(self.results), "refused": list(self.refused), "error": self.error, "by": self.by, "started": self.started, "finished": self.finished}

    def save(self) -> None:
        try:
            atomic.write_text(state_file(self.out), json.dumps(self.view(), indent=2, ensure_ascii=False))
        except OSError:
            pass

    def run(self) -> None:
        self.save()
        failed = 0
        for it in self.items:
            try:
                self.results.append(dict(_purge_item(self.out, self.lib, it["kind"], it["id"], self.by, self.confirm_shared, self.id), ok=True))
            except Exception as e:                  # one item failing never stops the others; the item is still in the trash and a new run finishes it
                failed += 1
                self.results.append({"kind": it["kind"], "id": it["id"], "ok": False, "error": str(e) or type(e).__name__})
            self.save()
        self.status = "failed" if failed else "done"
        if failed:
            self.error = f"{failed} of {len(self.items)} could not be deleted; they are still in the trash. Run the purge again."
        self.finished = round(time.time(), 3)
        self.save()
        self.event.set()


def _purge_item(out: Path, lib, kind: str, ident: str, by: str, confirm_shared: bool, task: str) -> dict:
    if kind == "batch":
        gid = int(ident.lstrip("G"))
        rows = purge_rows.purge_generation(out, gid)
        _record(out, "batch", "begun", f"G{gid:03d}", by, number=gid, database=rows, task=task, confirmed_shared=bool(confirm_shared))
        fl = batches.purge_files(out, gid)
        _record(out, "batch", "done", f"G{gid:03d}", by, number=gid, files=fl["files"], bytes=fl["bytes"], database=rows, task=task)
        return {"kind": "batch", "id": f"G{gid:03d}", "number": gid, "files": fl["files"], "bytes": fl["bytes"], "database": rows}
    from . import particle_sets
    particle_sets.detach_pack(out, lib, ident)
    try:
        r = lib.purge_pack(ident, confirm_shared=confirm_shared)
    except LibraryError as e:
        if e.code == 404:
            return {"kind": "pack", "id": ident, "already": True}
        raise
    _record(out, "pack", "done", ident, by, name=r["name"], stickers=r["stickers"], files=r["files_removed"], bytes=r["bytes"], kept_shared=r["kept_shared"], task=task, confirmed_shared=bool(confirm_shared))
    return {"kind": "pack", "id": ident, **{k: r[k] for k in ("name", "stickers", "bytes", "kept_shared")}, "files": r["files_removed"]}


def _state(out: Path) -> dict | None:
    try:
        r = json.loads(atomic.read_text(state_file(out)))
        return r if isinstance(r, dict) else None
    except (OSError, ValueError):
        return None


def last(out: Path) -> dict | None:
    """The most recent purge: the live task, else the saved state (a `running` one with no thread behind it reads `interrupted`: run the purge again)."""
    with _LOCK:
        live = max(_TASKS.values(), key=lambda t: t.started, default=None) if _TASKS else None
    if live and live.out == Path(out):
        return live.view()
    st = _state(Path(out))
    if st and st.get("status") == "running":
        st["status"] = "interrupted"
        st["error"] = "The server stopped in the middle of this purge. What was not deleted is still in the trash: run the purge again."
    return st


def status(out: Path, task_id: str) -> dict:
    with _LOCK:
        t = _TASKS.get(task_id)
    if t and t.out == Path(out):
        return t.view()
    st = last(out)
    if st and st.get("id") == task_id:
        return st
    raise pl.PipelineError(f"No purge {task_id}.", 404)


def running(out: Path) -> PurgeTask | None:
    with _LOCK:
        return next((t for t in _TASKS.values() if t.out == Path(out) and t.status == "running"), None)


# ---------------------------------------------------------------- the entry points (the routes call these)
def _guard(out: Path, busy: bool) -> None:
    if busy:
        raise pl.PipelineError("busy: a job is running. Wait for it to finish, then empty the trash.", 409)
    t = running(out)
    if t:
        raise pl.PipelineError(f"A purge is already running ({t.id}, {len(t.results)} of {len(t.items)} done). Wait for it to finish.", 409)


def _launch(task: PurgeTask) -> dict:
    with _LOCK:
        _TASKS[task.id] = task
    threading.Thread(target=task.run, name=task.id, daemon=True).start()
    task.event.wait(WAIT)
    return task.view()


def _done_before(out: Path, kind: str, ident: str) -> bool:
    return any(r.get("kind") == kind and r.get("id") == ident and r.get("phase") == "done" for r in _ledger_lines(out))


def _already(kind: str, ident: str) -> dict:
    """Running the same purge again after it finished is not an error: it says so (idempotent)."""
    return {"id": None, "status": "done", "total": 0, "done": 0, "items": [], "results": [{"kind": kind, "id": ident, "ok": True, "already": True}], "refused": [], "error": None}


def purge_one(out: Path, lib, kind: str, ident: str, by: str = "human", confirm_shared: bool = False, busy: bool = False) -> dict:
    """Delete one item of the trash for good. Everything that can be refused is refused HERE, before a thread starts (404 not in the trash, 409 job in flight / needs the shared confirmation).
    Returns the task view: `status: "done"` for a small purge (the route answers 200), `running` for a long one (202, poll `status`)."""
    out = Path(out)
    _guard(out, busy)
    if kind == "batch":
        try:
            gid = int(str(ident).lstrip("Gg"))
        except ValueError:
            raise pl.PipelineError("Say which batch (G012 or 12).", 400)
        ident = f"G{gid:03d}"
        if gid not in pl.removed_ids(out) and _done_before(out, "batch", ident):
            return _already(kind, ident)
        item = _batch_item(out, lib, gid)
    elif kind == "pack":
        try:
            item = _pack_item(lib, next(p for p in lib.trashed_packs() if p["id"] == ident), _sets_by_pack(out, lib))
        except StopIteration:
            if _done_before(out, "pack", ident):
                return _already(kind, ident)
            raise pl.PipelineError("That pack is not in the trash.", 404)
    else:
        raise pl.PipelineError("kind must be batch or pack", 400)
    if item["blocked"]:
        raise pl.PipelineError(item["blocked"], 409)
    if item["needs_confirm"] and not confirm_shared:
        raise pl.PipelineError(item["confirm_words"], 409)
    return _launch(PurgeTask(out, lib, [{"kind": kind, "id": ident}], by, confirm_shared, []))


def purge_all(out: Path, lib, confirm: str, by: str = "human", busy: bool = False, kind: str | None = None) -> dict:
    """Delete every item of the trash that needs no confirmation of its own. `confirm` must be the typed phrase `purge N` with N the number of items this call will delete NOW (the trash may have
    changed since the person looked: then it is 409 and says the new count). Items that need a shared-sticker confirmation or have a job in flight are skipped and listed in `refused`, in words.
    `kind="batch"` is the Earlier-batches column's "Remove all": only the removed batches (their count is `purge_batches` in the listing); trashed packs stay."""
    if kind not in (None, "batch", "pack"):
        raise pl.PipelineError("kind must be batch or pack", 400)
    out = Path(out)
    _guard(out, busy)
    now = listing(out, lib)
    skipped = {(s["kind"], s["id"]) for s in now["purge_all"]["skipped"]}
    pool = [i for i in now["batches"] + now["packs"] if (i["type"], i["id"]) not in skipped and kind in (None, i["type"])]
    n = len(pool)
    if not n:                                       # re-running "delete all" on a finished purge: nothing to do, and the items that need their own confirmation are listed
        return {"id": None, "status": "done", "total": 0, "done": 0, "items": [], "results": [], "refused": now["purge_all"]["skipped"], "error": None, "nothing_to_do": True}
    if " ".join(str(confirm or "").lower().split()) != phrase(n):
        raise pl.PipelineError(f"Type “{phrase(n)}” to delete {n} item{'s' if n != 1 else ''} for good. (The trash holds {n} that can be deleted now; the number you typed does not match.)", 409)
    items = [{"kind": i["type"], "id": i["id"]} for i in pool]
    return _launch(PurgeTask(out, lib, items, by, False, now["purge_all"]["skipped"]))
