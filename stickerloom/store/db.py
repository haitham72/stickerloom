"""psycopg 3 connection. DATABASE_URL from the environment or .env (git-ignored).
Default is the local stickerloom-db on 5434 (never the workspace's other databases)."""
from __future__ import annotations

import os
import time
from pathlib import Path

DEFAULT_URL = "postgresql://stickerloom:stickerloom_local@localhost:5434/stickerloom"

_state: dict = {"ok": None, "at": 0.0}   # process-cached availability: None = untried, True/False; at = last probe


def _load_dotenv() -> None:
    from ..runtime import envfile
    envfile.load()

def url() -> str:
    _load_dotenv()
    return os.environ.get("STICKERLOOM_DATABASE_URL") or os.environ.get("DATABASE_URL") or DEFAULT_URL


def connect():
    try:
        import psycopg
    except ImportError:
        raise RuntimeError("psycopg is not installed: pip install -r requirements.txt")
    return psycopg.connect(url(), connect_timeout=2)


REPROBE_AFTER = 30.0   # a cached miss re-probes after this long, so a database started after the server is picked up without a restart


def available() -> bool:
    """Cached per process: is Postgres reachable right now? A miss re-probes after REPROBE_AFTER seconds."""
    now = time.monotonic()
    if _state["ok"] is None or (_state["ok"] is False and now - _state.get("at", 0.0) > REPROBE_AFTER):
        try:
            with connect() as c:
                c.execute("select 1")
            _state["ok"] = True
        except Exception:
            _state["ok"] = False
        _state["at"] = now
    return _state["ok"]


def reset_cache() -> None:
    _state["ok"] = None


SCRATCH = "_stickerloom_check"            # the throwaway schema `check` builds inside a transaction it rolls back


def migration_files() -> list[Path]:
    return sorted((Path(__file__).resolve().parent.parent.parent / "migrations").glob("*.sql"))


def vector_notes(before: int | None, after: int | None) -> list[str]:
    """005_vectors.sql changes the embedding dimension only while a column is not 768-d yet, and a vector of another dimension cannot be converted: it is cleared.
    That is correct, but silent; say how many went and which command fills them again."""
    if before and (after or 0) < before:
        return [f"{before - (after or 0)} stored vector(s) were cleared because the embedding dimension changed (005_vectors.sql): fill them again with `python -m stickerloom pool reindex`"]
    return []


def _vectors(cur) -> int | None:
    if not cur.execute("select to_regclass('sticker_index')").fetchone()[0]:
        return None
    return cur.execute("select count(*) from sticker_index where subject_vec is not null or action_vec is not null").fetchone()[0]


def migrate_report() -> dict:
    """Apply stickerloom/migrations/*.sql in order. Re-runnable. {applied_now: [files run now], notes: [what a human should know, e.g. vectors cleared by a dimension change]}."""
    done, notes = [], []
    with connect() as c:
        for f in migration_files():
            with c.cursor() as cur:
                before = _vectors(cur) if f.name == "005_vectors.sql" else None
                cur.execute(f.read_text(encoding="utf-8"))
                cur.execute("INSERT INTO schema_migrations (name) VALUES (%s) ON CONFLICT DO NOTHING", (f.name,))
                if f.name == "005_vectors.sql":
                    notes += vector_notes(before, _vectors(cur))
            done.append(f.name)
    reset_cache()
    return {"applied_now": done, "notes": notes}


def migrate() -> list[str]:
    """Apply stickerloom/migrations/*.sql in order. Re-runnable. Returns the files run now (`migrate_report` also says what to know)."""
    return migrate_report()["applied_now"]


def applied() -> list[str]:
    """The names recorded in schema_migrations, in order (empty when the table does not exist yet)."""
    with connect() as c:
        if not c.execute("select to_regclass('schema_migrations')").fetchone()[0]:
            return []
        return [r[0] for r in c.execute("select name from schema_migrations order by name").fetchall()]


def status() -> dict:
    """What the files declare against what schema_migrations recorded: {declared, applied, pending, unknown, ok}."""
    declared, done = [f.name for f in migration_files()], applied()
    pending, unknown = [n for n in declared if n not in done], [n for n in done if n not in declared]
    return {"declared": declared, "applied": done, "pending": pending, "unknown": unknown, "ok": not pending and not unknown}


def normalise(text, schema: str) -> str:
    """A definition without the schema it was read from, whitespace collapsed: `public.stickers` and `_stickerloom_check.stickers` are the same table."""
    out = str(text if text is not None else "")
    for sch in {schema, "public"}:                    # `public.` too: an extension's operator class (public.gin_trgm_ops) lives there in both schemas
        for q in (f'"{sch}".', f"{sch}."):
            out = out.replace(q, "")
    return " ".join(out.split())


def read_shape(cur, schema: str) -> dict:
    """The columns, constraints and indexes of one schema, keyed `table.name`, as comparable plain values."""
    columns, constraints, indexes = {}, {}, {}
    for t, col, typ, nullable, default, gen in cur.execute(
            "select table_name, column_name, udt_name, is_nullable, column_default, generation_expression from information_schema.columns where table_schema = %s", (schema,)).fetchall():
        columns[f"{t}.{col}"] = (typ, nullable, normalise(default, schema) or None, normalise(gen, schema) or None)
    for t, name, definition in cur.execute(
            "select cl.relname, c.conname, pg_get_constraintdef(c.oid) from pg_constraint c join pg_class cl on cl.oid = c.conrelid "
            "join pg_namespace n on n.oid = cl.relnamespace where n.nspname = %s", (schema,)).fetchall():
        constraints[f"{t}.{name}"] = normalise(definition, schema)
    for t, name, definition in cur.execute("select tablename, indexname, indexdef from pg_indexes where schemaname = %s", (schema,)).fetchall():
        indexes[f"{t}.{name}"] = normalise(definition, schema)
    return {"columns": columns, "constraints": constraints, "indexes": indexes}


def diff_shapes(live: dict, declared: dict) -> list[str]:
    """Human-readable differences between the live schema and what the migration files declare. Only the tables the files declare are compared: another project's table
    in the same database is not drift."""
    tables = {k.split(".")[0] for k in declared["columns"]}
    out = []
    for kind, label in (("columns", "column"), ("constraints", "constraint"), ("indexes", "index")):
        want, have = declared[kind], {k: v for k, v in live[kind].items() if k.split(".")[0] in tables}
        for k in sorted(want):
            if k not in have:
                out.append(f"{label} {k} is declared but missing in the database")
            elif have[k] != want[k]:
                out.append(f"{label} {k} differs: live: {have[k]} | declared: {want[k]}")
        for k in sorted(set(have) - set(want)):
            out.append(f"{label} {k} is in the database but no file declares it")
    return out


def check() -> dict:
    """Is this database what the migration files declare? Reads schema_migrations back (pending / unknown files) and compares the live schema with a fresh build of every file
    in a throwaway schema inside a transaction that is rolled back (nothing is kept). A CHECK constraint or a default created inside `IF NOT EXISTS` is frozen at its first
    version, so editing a migration later never reached an existing database: that shows up here. {status, drift: [lines], pending, ok}."""
    st = status()
    with connect() as c:
        with c.cursor() as cur:
            schema = cur.execute("select current_schema()").fetchone()[0]
            live = read_shape(cur, schema)
            cur.execute(f'CREATE SCHEMA "{SCRATCH}"')
            cur.execute(f'SET LOCAL search_path TO "{SCRATCH}", public')
            for f in migration_files():
                cur.execute(f.read_text(encoding="utf-8"))
            declared = read_shape(cur, SCRATCH)
        c.rollback()
    drift = diff_shapes(live, declared)
    return {"status": st, "drift": drift, "pending": st["pending"], "ok": st["ok"] and not drift}


def reset(confirm: str = "") -> None:
    """DEV ONLY: drop all stickerloom tables. The caller must pass confirm='yes'."""
    if confirm != "yes":
        raise RuntimeError("refusing: pass confirm='yes' (dev only, destroys the local stickerloom database)")
    tables = ["claim_generations", "claims", "pack_sessions", "idempotency_keys", "job_queue", "generation_references", "feedback", "interactions", "sessions", "sticker_index", "model_calls", "search_log", "tasks", "generation_events", "reviews", "assets",
              "video_sheets", "stickers", "generations", "schema_migrations"]
    with connect() as c:
        with c.cursor() as cur:
            for t in tables:
                cur.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
            cur.execute("DROP SEQUENCE IF EXISTS generation_seq")
            cur.execute("DROP FUNCTION IF EXISTS array_to_text(text[])")
            cur.execute("DROP FUNCTION IF EXISTS stickerloom_words(text)")
    reset_cache()
