"""Batch groups: variations of one idea are ONE family. Earlier batches shows a family as one entry with a strip of its variations, and the
family shares its particles.

A batch's group is derived when it is read, so nothing needs migrating:
- `group: "G104"` in result.json (written by Add to group, or by dragging a batch onto another) names the family's root;
- otherwise the batch follows its recorded `parent` (a chat edit, "more", a redo) to that parent's family;
- otherwise it is its own root.
The target is always the parent: "add X to Y" (or drag X onto Y) puts X and everything in X's family under Y's root. The family is shown with its root's title.
`relation` says why a batch is in its family: `joined` (by hand), `redo` (`regen_of`), `edit` (a `parent`), or None for the root."""
from __future__ import annotations

import time
from pathlib import Path

from . import pipeline as pl


def _num(v) -> int | None:
    s = str(v or "").upper().lstrip("G")
    return int(s) if s.isdigit() else None


def _alive(out: Path, gid: int) -> bool:
    return (pl.gen_dir(out, gid) / "result.json").is_file()


def root_of(out: Path, gid: int) -> int:
    """The family root of a batch: its explicit group, else its parent's root, else itself. A parent that was removed or a loop stops the walk."""
    seen, cur = set(), int(gid)
    while cur not in seen:
        seen.add(cur)
        res = pl.read_result(out, cur)
        nxt = _num(res.get("group"))
        if nxt == cur:                          # left its family: its own root on purpose
            return cur
        if nxt is None:
            nxt = _num(res.get("parent"))
        if nxt is None or not _alive(out, nxt):
            return cur
        cur = nxt
    return cur


def relation(res: dict) -> str | None:
    gid = res.get("number")
    if _num(res.get("group")) not in (None, gid):
        return "joined"
    if res.get("regen_of"):
        return "redo"
    if res.get("parent"):
        return "edit"
    return None


def families(out: Path, ids=None) -> dict[int, list[int]]:
    """root -> its members (root first, then by number) for the batches given (default: every batch on disk)."""
    fam: dict[int, list[int]] = {}
    for gid in (ids if ids is not None else pl.list_ids(out)):
        try:
            fam.setdefault(root_of(out, gid), []).append(gid)
        except Exception:
            continue
    return {r: sorted(m, key=lambda g: (g != r, g)) for r, m in fam.items()}


def pack_groups(out: Path, packs) -> dict[str, str]:
    """pack id -> the batch group it belongs to (`G104`, the family root), so the Library sits the packs of one group together as the Studio sits the batches.
    A pack follows the group most of its stickers came from (`source.generation`); a pack whose group has no other pack, or whose stickers came from no
    batch (photos, imports), has none. A pack nested by hand (`parent`, a sub-pack) sits in its top parent's group: that group's key, or `P-<pack id>`
    when the parent has no batch group. A pure read of the batches: a join or leave in the Studio shows at the next read."""
    roots: dict[int, int] = {}
    def root(gid: int) -> int | None:
        if gid not in roots:
            try:
                roots[gid] = root_of(out, gid)
            except Exception:
                return None
        return roots[gid]
    picked: dict[str, int] = {}
    for p in packs or []:
        votes: dict[int, int] = {}
        for s in p.get("stickers") or []:
            g = _num((s.get("source") or {}).get("generation"))
            r = root(g) if g is not None else None
            if r is not None:
                votes[r] = votes.get(r, 0) + 1
        if votes:
            picked[p["id"]] = max(votes, key=lambda r: (votes[r], -r))
    keys: dict[str, str] = {pid: f"G{r:03d}" for pid, r in picked.items()}
    by = {p["id"]: p for p in packs or []}

    def top(pid: str) -> str:                                             # the explicit parent chain (`parent`, a sub-pack), to its top
        seen = {pid}
        while (q := by[pid].get("parent")) in by and q not in seen:
            seen.add(q); pid = q
        return pid
    for pid in by:                                                        # a sub-pack sits in its parent's group, whatever its own batch says
        t = top(pid)
        if t != pid:
            keys[t] = keys.get(t) or f"P-{t}"
            keys[pid] = keys[t]
    count: dict[str, int] = {}
    for k in keys.values():
        count[k] = count.get(k, 0) + 1
    return {pid: k for pid, k in keys.items() if count[k] > 1}


def pack_leads(packs, groups_of: dict[str, str]) -> set[str]:
    """The parent pack of each group: the one most recently *Assigned as parent* in the Studio (`lead_at`), else the group's first pack."""
    best: dict[str, tuple] = {}
    for i, p in enumerate(packs or []):
        g = groups_of.get(p["id"])
        if g:
            key = (float(p.get("lead_at") or 0), -i)
            if g not in best or key > best[g][0]:
                best[g] = (key, p["id"])
    return {pid for _, pid in best.values()}


def members(out: Path, gid: int) -> list[int]:
    r = root_of(out, gid)
    return families(out).get(r, [r])


@pl.serialized
def join(out: Path, gid: int, to: int, by: str = "human") -> dict:
    """Add batch `gid` (and its whole family) to the family of `to`: `to`'s root becomes the parent of all of them (written on `gid`'s old root only). Free, reversible (`leave`)."""
    gid, to = int(gid), int(to)
    for g in (gid, to):
        if not _alive(out, g):
            raise pl.PipelineError(f"G{g:03d} is not a batch on disk", 404)
    new_root, old_root = root_of(out, to), root_of(out, gid)
    if new_root == old_root:
        raise pl.PipelineError(f"G{gid:03d} is already in the family of G{new_root:03d}", 409)
    res = pl.read_result(out, old_root)          # only the old root points at the new one: its members follow it (their parent or an earlier join), so one can still leave with its own edits
    res["group"] = f"G{new_root:03d}"
    res.setdefault("group_history", []).append({"ts": round(time.time(), 3), "actor": by, "decision": "JOIN", "to": f"G{new_root:03d}", "via": f"G{gid:03d}"})
    pl.write_result(out, old_root, res)
    return {"id": f"G{gid:03d}", "root": f"G{new_root:03d}", "members": [f"G{g:03d}" for g in members(out, new_root)]}


@pl.serialized
def leave(out: Path, gid: int, by: str = "human") -> dict:
    """Take a batch out of its family: it becomes its own root again (its own edits follow it). The root itself cannot leave: its family would have no parent."""
    gid = int(gid)
    if not _alive(out, gid):
        raise pl.PipelineError(f"G{gid:03d} is not a batch on disk", 404)
    root = root_of(out, gid)
    if root == gid:
        raise pl.PipelineError(f"G{gid:03d} is the parent of its family; move the others out instead", 409)
    res = pl.read_result(out, gid)
    res["group"] = f"G{gid:03d}"
    res.setdefault("group_history", []).append({"ts": round(time.time(), 3), "actor": by, "decision": "LEAVE", "from": f"G{root:03d}"})
    pl.write_result(out, gid, res)
    return {"id": f"G{gid:03d}", "root": f"G{gid:03d}", "left": f"G{root:03d}", "members": [f"G{g:03d}" for g in members(out, gid)]}


def _picked_stored(out: Path, root: int) -> int | None:
    try:
        return _num(pl.read_result(out, root).get("picked"))
    except Exception:
        return None


def family(out: Path, gid: int) -> dict:
    """The batch a generation belongs to, as the Studio's row shows it: the root, every
    generation of the family in order (root first, then by number) with its number in the row, and the ONE picked generation: the stored pick
    when it is still a member, else the newest. {root, picked, members: [{id, generation_id, n, relation, stage, ready, sheet_model, created}]}."""
    r = root_of(out, int(gid))
    ids = members(out, r)
    rows = []
    for k, g in enumerate(ids, 1):
        try:
            res = pl.read_result(out, g)
        except Exception:
            continue
        rows.append({"id": g, "generation_id": res["generation_id"], "n": k, "relation": relation(res), "stage": res.get("stage"),
                     "ready": sum(1 for s in res["stickers"] if s.get("status") == "READY"), "sheet_model": res.get("sheet_model"), "created": res.get("created")})
    alive = [m["id"] for m in rows]
    p = _picked_stored(out, r)
    picked = p if p in alive else (max(alive) if alive else r)
    return {"root": f"G{r:03d}", "picked": f"G{picked:03d}", "members": rows}


@pl.serialized
def pick(out: Path, gid: int, by: str = "human") -> dict:
    """Pick this generation for its batch: the one the batch is animated and packed from. One pick per batch, stored on the family root
    (`picked`, with `pick_history`); a recorded, reversible click (pick another one to change it). Free."""
    gid = int(gid)
    if not _alive(out, gid):
        raise pl.PipelineError(f"G{gid:03d} is not a batch on disk", 404)
    r = root_of(out, gid)
    res = pl.read_result(out, r)
    res["picked"] = f"G{gid:03d}"
    res.setdefault("pick_history", []).append({"ts": round(time.time(), 3), "actor": by, "decision": "PICK", "generation": f"G{gid:03d}"})
    pl.write_result(out, r, res)
    return family(out, gid)


def hand_over_plan(out: Path, gid: int) -> dict | None:
    """Before generation `gid` leaves (Delete moves it to the trash): how its batch stays ONE batch. Every other generation is pointed at the surviving
    root, so a removed root or a removed middle parent never splits the family; when the root itself leaves, the oldest survivor becomes the root (gen numbers stay
    in order) and the main pick moves there (the old main, else the newest). None for a generation alone. Read only: `hand_over` applies it once the removal really happened."""
    gid = int(gid)
    others = [m for m in members(out, gid) if m != gid]
    if not others:
        return None
    r = root_of(out, gid)
    picked = _picked_stored(out, r)
    new_root = r if r != gid else min(others)                # the oldest survivor: gen numbers stay in order
    main = picked if picked in others else (None if r != gid else max(others))
    return {"gone": gid, "root": new_root, "others": others, "main": main, "root_left": r == gid}


@pl.serialized
def hand_over(out: Path, plan: dict | None, by: str = "human") -> dict | None:
    """Apply `hand_over_plan` after the generation went to the trash. Returns {root, members} of the batch that stays."""
    if not plan:
        return None
    now, new_root = round(time.time(), 3), plan["root"]
    for m in plan["others"]:
        if not _alive(out, m):
            continue
        res = pl.read_result(out, m)
        if _num(res.get("group")) != new_root:
            res["group"] = f"G{new_root:03d}"
            res.setdefault("group_history", []).append({"ts": now, "actor": by, "decision": "JOIN", "to": f"G{new_root:03d}", "via": f"G{plan['gone']:03d} removed"})
        if m == new_root and plan["root_left"] and plan["main"]:
            res["picked"] = f"G{plan['main']:03d}"
        pl.write_result(out, m, res)
    return {"root": f"G{new_root:03d}", "members": [f"G{m:03d}" for m in plan["others"]]}


@pl.serialized
def set_pack(out: Path, gid: int, to: int | None, by: str = "human") -> dict:
    """Earlier batches' drag and drop: put batch `gid` (its whole family) into the PACK of batch `to` as another batch of it, or with
    `to=None` take it out (a pack of its own, even beside batches of the same request). Stored on the family root as `pack` (+ `pack_history`): an explicit link beats the
    derived grouping (pipeline._packs). A batch dropped on itself or its own pack changes nothing. Free and reversible; never a regeneration (that is `join`)."""
    gid = int(gid)
    if not _alive(out, gid):
        raise pl.PipelineError(f"G{gid:03d} is not a batch on disk", 404)
    root = root_of(out, gid)
    target = None
    if to is not None:
        to = int(to)
        if not _alive(out, to):
            raise pl.PipelineError(f"G{to:03d} is not a batch on disk", 404)
        target = root_of(out, to)
        if target == root:
            raise pl.PipelineError("That is the same batch", 409)
    res = pl.read_result(out, root)
    res["pack"] = f"G{(target if target is not None else root):03d}"        # out of a pack = a link to itself, so the derived grouping does not pull it back in
    res.setdefault("pack_history", []).append({"ts": round(time.time(), 3), "actor": by, "decision": "PACK" if target is not None else "UNPACK",
                                               "to": res["pack"], "via": f"G{gid:03d}"})
    pl.write_result(out, root, res)
    return {"id": f"G{gid:03d}", "root": f"G{root:03d}", "pack": res["pack"]}
