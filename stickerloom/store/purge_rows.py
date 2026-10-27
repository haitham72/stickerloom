"""The database half of purging a batch (flow/purge.py is the caller): what a purge would remove from Postgres, and removing it.

What the database holds of a batch G### (`migrations/001_init.sql`, `003_pool.sql`): its `generations` row, its `stickers` rows, their `sticker_index` pool rows WITH the `subject_vec` / `action_vec`
embeddings, `assets`, `generation_events`, `video_sheets`, and `reviews` / `tasks` that point at them. There is NO `packs` table: a pack is library.json, so purging a pack never reaches this file.

What goes and what stays:
  * gone: the `sticker_index` rows (embeddings included, so the sticker leaves search), the `assets`, the `generation_events`, every `stickers` row no review points at, every `video_sheets` row
    no review or task points at, and the `generations` row itself when nothing at all points at it any more;
  * stays: the `reviews` rows (their foreign keys keep the rows they name alive) and the `tasks` rows (the provider's external task id is the join key to the spend ledger). The kept `stickers` /
    `generations` rows become TOMBSTONES: `status` (and for a sticker `still_review`, `anim_review`, `animation_status`) read `PURGED`, so the pool never indexes them again
    (`pool.reindex` only takes `still_review = 'APPROVED'`) and a search never offers a picture whose file is gone. A purged batch number is never reused (`pipeline.next_gid` reads the ledger).

Everything is best effort in the way the rest of the store is: no database configured, `STICKERLOOM_DB_WRITE=0`, or an out/ that is not the default one (tests, scratch copies) means `available: False`
and nothing is touched. One transaction; every statement is idempotent, so a purge that stopped half-way is simply run again."""
from __future__ import annotations

from pathlib import Path

from . import db, sync

COUNTS = (
    ("stickers", "SELECT count(*) FROM stickers WHERE generation_id = %s"),
    ("tombstones", "SELECT count(*) FROM stickers WHERE generation_id = %s AND status = 'PURGED'"),
    ("indexed", "SELECT count(*) FROM sticker_index i JOIN stickers s ON s.id = i.sticker_id WHERE s.generation_id = %s"),
    ("shared_in_pool", "SELECT count(*) FROM sticker_index i JOIN stickers s ON s.id = i.sticker_id WHERE s.generation_id = %s AND i.shared AND NOT i.hidden"),
    ("vectors", "SELECT count(*) FROM sticker_index i JOIN stickers s ON s.id = i.sticker_id WHERE s.generation_id = %s AND (i.subject_vec IS NOT NULL OR i.action_vec IS NOT NULL)"),
    ("assets", "SELECT count(*) FROM assets WHERE generation_id = %s"),
    ("events", "SELECT count(*) FROM generation_events WHERE generation_id = %s"),
    ("video_sheets", "SELECT count(*) FROM video_sheets WHERE generation_id = %s"),
    ("reviews_kept", "SELECT count(*) FROM reviews WHERE generation_id = %s"),
    ("tasks_kept", "SELECT count(*) FROM tasks WHERE generation_id = %s"),
)


def gen_id(gid: int) -> str:
    return f"G{int(gid):03d}"


def usable(out) -> tuple[bool, str | None]:
    """(True, None) when this out/ writes to Postgres and it answers; else (False, the reason in words)."""
    try:
        if not sync.enabled(Path(out)):
            return False, "database writes are off for this out/ (STICKERLOOM_DB_WRITE=0, or not the default out/)"
        if not db.available():
            return False, "Postgres is not reachable"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    return True, None


def describe(out, gid: int) -> dict:
    """What the database holds of batch `gid` (counts), or {available: False, reason}. Read only."""
    ok, why = usable(out)
    if not ok:
        return {"available": False, "reason": why}
    try:
        g = gen_id(gid)
        with db.connect() as c, c.cursor() as cur:
            cur.execute("SELECT status FROM generations WHERE id = %s", (g,))
            row = cur.fetchone()
            res = {"available": True, "present": row is not None, "status": row[0] if row else None}
            for name, sql in COUNTS:
                cur.execute(sql, (g,))
                res[name] = int(cur.fetchone()[0])
        return res
    except Exception as e:
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}


def purge_generation(out, gid: int) -> dict:
    """Remove the batch's rows as described at the top. Idempotent. Raises on a database error (the caller reports it and the purge can be run again); returns the counts it removed
    and tombstoned, or {available: False, reason} when there is nothing to do."""
    ok, why = usable(out)
    if not ok:
        return {"available": False, "reason": why}
    g = gen_id(gid)
    done: dict = {"available": True}
    with db.connect() as c:
        with c.cursor() as cur:
            cur.execute("DELETE FROM sticker_index WHERE sticker_id IN (SELECT id FROM stickers WHERE generation_id = %s)", (g,))
            done["indexed"] = cur.rowcount
            cur.execute("DELETE FROM assets WHERE generation_id = %s", (g,))
            done["assets"] = cur.rowcount
            cur.execute("DELETE FROM generation_events WHERE generation_id = %s", (g,))
            done["events"] = cur.rowcount
            cur.execute("""DELETE FROM stickers s WHERE s.generation_id = %s
                           AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.sticker_id = s.id)
                           AND NOT EXISTS (SELECT 1 FROM stickers x WHERE x.inherited_from = s.id AND x.id <> s.id)""", (g,))
            done["stickers_deleted"] = cur.rowcount
            cur.execute("""UPDATE stickers SET status = 'PURGED', still_review = 'PURGED', anim_review = 'PURGED', animation_status = 'PURGED'
                           WHERE generation_id = %s AND status <> 'PURGED'""", (g,))
            done["stickers_tombstoned"] = cur.rowcount
            cur.execute("""DELETE FROM video_sheets v WHERE v.generation_id = %s
                           AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.video_sheet_id = v.id)
                           AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.video_sheet_id = v.id)""", (g,))
            done["video_sheets_deleted"] = cur.rowcount
            cur.execute("""DELETE FROM generations x WHERE x.id = %s
                           AND NOT EXISTS (SELECT 1 FROM stickers s WHERE s.generation_id = x.id)
                           AND NOT EXISTS (SELECT 1 FROM video_sheets v WHERE v.generation_id = x.id)
                           AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.generation_id = x.id)
                           AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.generation_id = x.id)
                           AND NOT EXISTS (SELECT 1 FROM generations y WHERE y.parent_id = x.id)""", (g,))
            gone = cur.rowcount
            done["generation_deleted"] = bool(gone)
            if not gone:
                cur.execute("UPDATE generations SET status = 'PURGED' WHERE id = %s AND status <> 'PURGED'", (g,))
                done["generation_tombstoned"] = bool(cur.rowcount)
            cur.execute("SELECT count(*) FROM reviews WHERE generation_id = %s", (g,))
            done["reviews_kept"] = int(cur.fetchone()[0])
        c.commit()
    return done
