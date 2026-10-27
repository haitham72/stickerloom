"""Phase 3A repository: save_generation() is one transaction (generation + stickers + assets +
video sheets + reviews + events). Re-saving the same result is idempotent: generations and
stickers upsert, reviews/events/assets insert ON CONFLICT DO NOTHING. The gate rules stay in
flow/pipeline.py/gates.py; the repo stores decisions, it never decides (principle 8)."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import time
from datetime import datetime, timezone
from pathlib import Path

from . import db

ACTORS = ("python", "human", "vlm")
DECISIONS = ("PASS", "BLOCK", "APPROVE", "REJECT", "EDIT")
GATES = ("plan", "still", "video_sheet", "anim", "pack")

# result.json history stage -> reviews gate
STAGE_GATE = {
    "sheet": "still", "sliced": "still", "still": "still",
    "plan_reviewed": "plan",
    "video_sheet": "video_sheet", "video_sheet_built": "video_sheet",
    "video_sheet_reviewed": "video_sheet",
    "video": "anim", "video_returned": "video_sheet", "video_flag": "video_sheet",
    "anim": "anim", "anim_reviewed": "anim",
    "pack": "pack", "pack_final": "pack",
    "review": None,  # per-decision event: gate comes from detail.gate
}


def _ts(v) -> datetime:
    try:
        return datetime.fromtimestamp(float(v), tz=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(tz=timezone.utc)


def _uuid(v) -> str | None:
    """A trace run id from an event line, or None when it is not a UUID (never fails an import)."""
    import uuid
    try:
        return str(uuid.UUID(str(v))) if v else None
    except ValueError:
        return None


def _grid(res: dict) -> list:
    g = res.get("grid")
    if isinstance(g, (list, tuple)) and len(g) == 2:
        return [int(g[0]), int(g[1])]
    if isinstance(g, dict) and "rows" in g:
        return [int(g["rows"]), int(g.get("cols", g["rows"]))]
    n = len(res.get("stickers", []))
    return [3, 3] if n > 4 else ([2, 2] if n > 1 else [1, 1])


def _emoji_list(s: dict) -> list:
    e = s.get("emoji")
    if isinstance(e, list):
        return [str(x) for x in e if str(x).strip()]
    return [str(e)] if str(e or "").strip() else []


def _gen_status(stickers: list) -> str:
    ok = sum(1 for s in stickers if s.get("status") == "READY")
    return "READY" if ok == len(stickers) and stickers else ("PARTIAL" if ok else "FAILED")


def _parent_id(v) -> str | None:
    """result.json parent is an int (8) or 'G008' or None -> 'G008' or None."""
    if v is None or v == "":
        return None
    s = str(v).strip()
    return f"G{int(s[1:] if s[:1].lower() == 'g' and s[1:].isdigit() else s):03d}" if s[1:].isdigit() or s.isdigit() else s


def _vsid(gen_id: str, v: str | None) -> str | None:
    """Video-sheet ids on disk are short ('A1'); the table key is qualified ('G032/A1')."""
    if not v:
        return None
    s = str(v)
    return s if "/" in s else f"{gen_id}/{s}"


def _file_asset(gd: Path, rel: str | None) -> tuple | None:
    """(object_key, sha256, bytes, mime) for a file under the generation dir. Reads bytes to hash only."""
    if not rel:
        return None
    f = gd / rel
    if not f.is_file():
        return None
    try:
        data = f.read_bytes()
    except OSError:
        return None
    return (rel.replace("\\", "/"), hashlib.sha256(data).hexdigest(),
            mimetypes.guess_type(f.name)[0] or "application/octet-stream", len(data))


def save_generation(conn, out: Path, gid: int) -> str:
    """Upsert one generation from out/G###/{result.json,prompts.json,events.jsonl}. Returns the generation id."""
    from ..flow import pipeline as pl
    out, gd = Path(out), pl.gen_dir(Path(out), gid)
    res = json.loads((gd / "result.json").read_text(encoding="utf-8"))
    res = pl.normalise(res)
    prompts_p = gd / "prompts.json"
    plan = json.loads(prompts_p.read_text(encoding="utf-8")) if prompts_p.is_file() else {}
    events = pl.read_events(out, gid)
    gen_id = res.get("generation_id") or f"G{gid:03d}"
    stickers = res.get("stickers", [])
    status = _gen_status(stickers)
    grid = _grid(res)
    src = res.get("source") or {}

    from ..flow import groups
    try:
        group_id, relation = f"G{groups.root_of(out, gid):03d}", groups.relation(res)
    except Exception:
        group_id, relation = gen_id, None
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO generations (id, parent_id, grid, regen_of, verify_version, prompt, subject,
                  source, source_ref, task, task_slug, sheet_prompt, video_prompt, plan, engine_version,
                  status, name_key, task_id, template_id, template_version, slots, outline_px, erode_px, owner, group_id, relation)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (id) DO UPDATE SET owner=EXCLUDED.owner, status=EXCLUDED.status, plan=EXCLUDED.plan,
                 group_id=EXCLUDED.group_id, relation=EXCLUDED.relation,
                 slots=EXCLUDED.slots, outline_px=EXCLUDED.outline_px, erode_px=EXCLUDED.erode_px,
                 task_id=EXCLUDED.task_id, name_key=EXCLUDED.name_key""",
            (gen_id, _parent_id(res.get("parent")), grid, res.get("regen_of"), str(res.get("verify_version") or ""),
             str(res.get("prompt") or ""), str(src.get("subject") or ""), "prepared",
             json.dumps(src), str(res.get("task") or ""), str(res.get("task_slug") or ""),
             res.get("sheet_prompt"), res.get("video_prompt"), json.dumps(plan),
             str(res.get("engine_version") or ""), status, str(res.get("name_key") or res.get("task_slug") or ""),
             res.get("task_id"), res.get("template_id"),
             (str(res.get("template_version")) if res.get("template_version") is not None else None),
             json.dumps(res.get("slots")) if res.get("slots") is not None else None,
             res.get("outline_px"), res.get("erode_px"), str(res.get("owner") or "local"), group_id, relation))

        # video sheets first (stickers + assets reference them)
        vs_map: dict = {}
        for vs in res.get("video_sheets") or []:
            vid = _vsid(gen_id, vs.get("id") or f"A{vs.get('attempt') or 1}")
            cur.execute(
                """INSERT INTO video_sheets (id, generation_id, attempt, slots, layout, status, ticket)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO UPDATE SET status=EXCLUDED.status, slots=EXCLUDED.slots,
                     layout=EXCLUDED.layout, ticket=EXCLUDED.ticket""",
                (vid, gen_id, int(vs.get("attempt") or 1), list(vs.get("slots") or []),
                 json.dumps(vs.get("layout") or {}), str(vs.get("status") or "BUILT"), vs.get("ticket")))
            vs_map[vid] = vs
            for kind, rel in (("VIDEO_SHEET", (vs.get("file") or "").replace("\\", "/") or None),
                              ("VIDEO_LAYOUT", f"video_sheet/A{vs.get('attempt') or 1}/layout.json"
                               if (gd / f"video_sheet/A{vs.get('attempt') or 1}/layout.json").is_file() else None),
                              ("RETURNED_VIDEO", (vs.get("video") or "").replace("\\", "/") or None)):
                a = _file_asset(gd, rel)
                if a:
                    cur.execute(
                        """INSERT INTO assets (generation_id, video_sheet_id, kind, object_key, sha256, mime, bytes)
                           VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (generation_id, object_key) DO NOTHING""",
                        (gen_id, vid, kind, a[0], a[1], a[2], a[3]))

        for s in stickers:
            sid = f"{gen_id}/S{s['index']}"
            tags = list(s.get("tags") or [s.get("key") or ""])
            rev = s.get("review") or {}
            still = str(rev.get("still") or ("BLOCKED" if s.get("status") == "FAILED" else "PENDING"))
            anim = str(rev.get("anim") or "PENDING")
            if still == "NONE":
                still = "PENDING"
            if anim == "NONE":
                anim = "PENDING"
            vsi = next((vid for vid, vs in vs_map.items() if s["index"] in (vs.get("slots") or [])), None)
            cur.execute(
                """INSERT INTO stickers (id, generation_id, idx, name, file_name, key, tags, concept, prompt,
                      emoji, status, reason, report, metrics, edited, animation_status, animation_reason,
                      still_review, anim_review, video_sheet_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO UPDATE SET status=EXCLUDED.status, reason=EXCLUDED.reason,
                     report=EXCLUDED.report, metrics=EXCLUDED.metrics, edited=EXCLUDED.edited,
                     animation_status=EXCLUDED.animation_status, animation_reason=EXCLUDED.animation_reason,
                     still_review=EXCLUDED.still_review, anim_review=EXCLUDED.anim_review,
                     video_sheet_id=EXCLUDED.video_sheet_id, file_name=EXCLUDED.file_name""",
                (sid, gen_id, int(s["index"]), str(s.get("name") or sid), s.get("file_name"),
                 str(s.get("key") or ""), tags, s.get("concept"), str(s.get("prompt") or ""),
                 _emoji_list(s), str(s.get("status") or "FAILED"), s.get("reason"),
                 json.dumps(s.get("report") or []), json.dumps(s.get("metrics") or {}),
                 bool(s.get("edited")), str(s.get("anim_status") or "NOT_REQUESTED"), s.get("anim_reason"),
                 still, anim, vsi))
            for kind, rel in (("PNG", s.get("png")), ("WEBM", s.get("webm"))):
                a = _file_asset(gd, rel)
                if a:
                    cur.execute(
                        """INSERT INTO assets (generation_id, sticker_id, video_sheet_id, kind, object_key, sha256, mime, bytes)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (generation_id, object_key) DO NOTHING""",
                        (gen_id, sid, vsi, kind, a[0], a[1], a[2], a[3]))
            plain = gd / f"source/plain/S{s['index']}.png"
            if plain.is_file():
                a = _file_asset(gd, f"source/plain/S{s['index']}.png")
                cur.execute(
                    """INSERT INTO assets (generation_id, sticker_id, kind, object_key, sha256, mime, bytes)
                       VALUES (%s,%s,'PLAIN_STICKER',%s,%s,%s,%s) ON CONFLICT (generation_id, object_key) DO NOTHING""",
                    (gen_id, sid, a[0], a[1], a[2], a[3]))

        for kind, rel in (("SOURCE_SHEET", "source/sheet.png"), ("SOURCE_SHEET", "source/sheet.jpg"),
                          ("KEYED_SHEET", "source/keyed.png"), ("PROMPTS", "prompts.json")):
            a = _file_asset(gd, rel)
            if a:
                cur.execute(
                    """INSERT INTO assets (generation_id, kind, object_key, sha256, mime, bytes)
                       VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (generation_id, object_key) DO NOTHING""",
                    (gen_id, kind, a[0], a[1], a[2], a[3]))

        # decisions: sticker history[] -> one reviews row each; generation reviews -> sticker_id NULL
        for s in stickers:
            sid = f"{gen_id}/S{s['index']}"
            for h in s.get("history") or []:
                gate = STAGE_GATE.get(str(h.get("stage") or ""), "still")
                if h.get("stage") == "review":
                    gate = (h.get("detail") or {}).get("gate") or "still"
                if gate not in GATES:
                    continue
                actor = h.get("actor") if h.get("actor") in ACTORS else "python"
                dec = h.get("decision") if h.get("decision") in DECISIONS else "PASS"
                vsid = _vsid(gen_id, h.get("ref"))
                cur.execute(
                    """INSERT INTO reviews (generation_id, sticker_id, video_sheet_id, gate, actor, decision,
                          reason, detail, ts)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (generation_id, sticker_id, gate, actor, ts) DO NOTHING""",
                    (gen_id, sid, vsid if vsid in vs_map else None,
                     gate, actor, dec, h.get("reason"),
                     json.dumps(h.get("detail")) if h.get("detail") is not None else None, _ts(h.get("ts"))))
        for gate, val in (res.get("reviews") or {}).items():
            if gate == "video_sheet" and isinstance(val, dict) and "decision" not in val:
                for aid, v in val.items():  # per-attempt {A1: {decision, by, ts, note}}
                    if isinstance(v, dict) and v.get("decision"):
                        vsid = _vsid(gen_id, aid)
                        _gen_review(cur, gen_id, "video_sheet", v, vsid if vsid in vs_map else None)
                continue
            if isinstance(val, dict) and val.get("decision"):
                _gen_review(cur, gen_id, gate if gate in GATES else "pack", val, None)

        for ev in events:
            cur.execute(
                """INSERT INTO generation_events (generation_id, ts, stage, status, ms, detail, actor, trace_run_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (generation_id, ts, stage, status) DO NOTHING""",
                (gen_id, _ts(ev.get("ts")), str(ev.get("stage") or ""), str(ev.get("status") or ""),
                 ev.get("ms"), json.dumps(ev.get("detail")) if ev.get("detail") is not None else None,
                 ev.get("actor"), _uuid(ev.get("trace_run_id"))))

        # the prepared inputs stand in for the provider: one task row per generation
        ext = f"img-{str(src.get('subject_id') or '000')}-{src.get('subject') or 'unknown'}"
        cur.execute(
            """INSERT INTO tasks (provider, external_task_id, kind, name_key, generation_id, status, request)
               VALUES ('prepared',%s,'sheet',%s,%s,'DONE',%s)
               ON CONFLICT (provider, external_task_id) DO NOTHING""",
            (ext, str(res.get("task_slug") or ""), gen_id, json.dumps({"subject": src.get("subject")})))
    conn.commit()
    return gen_id


def _gen_review(cur, gen_id: str, gate: str, v: dict, vsid: str | None) -> None:
    """Generation-level review (sticker_id NULL: the UNIQUE key never fires on NULLs, so dedupe by hand)."""
    actor = v.get("by") if v.get("by") in ACTORS else "human"
    dec = v.get("decision") if v.get("decision") in DECISIONS else "APPROVE"
    ts = _ts(v.get("ts"))
    cur.execute(
        """INSERT INTO reviews (generation_id, video_sheet_id, gate, actor, decision, reason, ts)
           SELECT %s,%s,%s,%s,%s,%s,%s WHERE NOT EXISTS
             (SELECT 1 FROM reviews WHERE generation_id = %s AND sticker_id IS NULL
                AND gate = %s AND actor = %s AND ts = %s)""",
        (gen_id, vsid, gate, actor, dec, v.get("note"), ts, gen_id, gate, actor, ts))


def import_tasks(conn, out: Path) -> int:
    """Backfill out/tasks/*.json (1G manual tasks) as tasks rows. Idempotent. Returns rows present.
    A provider ticket that a job import already owns (same external id, any provider) is not inserted twice."""
    from ..generation import tasks as _t
    n = 0
    for f in sorted(_t.tasks_dir(Path(out)).glob("*.json")):
        try:
            t = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        ext = str(t.get("external_task_id") or t.get("id"))
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM tasks WHERE external_task_id = %s", (ext,))
            if not cur.fetchone():
                cur.execute(
                    """INSERT INTO tasks (provider, external_task_id, kind, name_key, status, request)
                       VALUES (%s,%s,'sheet',%s,%s,%s)
                       ON CONFLICT (provider, external_task_id) DO NOTHING""",
                    (str(t.get("provider") or "higgsfield-manual"), ext,
                     str(t.get("name_key") or ""), str(t.get("status") or "REQUESTED").upper(),
                     json.dumps(t.get("request") or {})))
        n += 1
    conn.commit()
    return n


CLAIM_PROVIDER = "stickerloom-pack"


def save_pack_session(conn, s: dict) -> int:
    """One out/pack_sessions/<slug>.json as rows: the session, its claims, their lineage, and one `tasks` row per claim (kind 'sheet',
    external_task_id = the claim id, name_key = the session slug). Idempotent: rows upsert, lineage inserts ON CONFLICT DO NOTHING. Returns the claims written."""
    slug = str(s["slug"])
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO pack_sessions (slug, subject, title, owner, pack_id, created) VALUES (%s,%s,%s,%s,%s,%s)
               ON CONFLICT (slug) DO UPDATE SET subject = EXCLUDED.subject, title = EXCLUDED.title, owner = EXCLUDED.owner, pack_id = EXCLUDED.pack_id""",
            (slug, str(s.get("subject") or slug), s.get("title"), s.get("owner"), s.get("pack_id"), _ts(s.get("created") or time.time())))
        n = 0
        for c in s.get("claims", []):
            cid, status = str(c["id"]), str(c.get("status") or "CLAIMED")
            grid = "x".join(str(x) for x in (c.get("grid") or [3, 3]))
            plan = json.dumps(c["plan"]) if c.get("plan") is not None else None
            cur.execute(
                """INSERT INTO claims (id, session, preset, grid, status, plan, created) VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, plan = EXCLUDED.plan""",
                (cid, slug, str(c.get("preset")), grid, status, plan, _ts(c.get("created") or time.time())))
            for g in c.get("generations", []):
                cur.execute("""INSERT INTO claim_generations (claim_id, generation_id, revision, ts) VALUES (%s,%s,%s,%s)
                               ON CONFLICT (claim_id, revision) DO NOTHING""", (cid, str(g["generation"]), int(g["revision"]), _ts(g.get("ts") or time.time())))
            gens = c.get("generations") or []
            gid = _gen_exists(cur, gens[-1]["generation"]) if gens else None
            request = json.dumps({"session": f"pack-{slug}", "preset": c.get("preset"), "grid": c.get("grid"), "jobs": c.get("jobs", []), "plan": c.get("plan")})
            cur.execute("SELECT id FROM tasks WHERE provider = %s AND external_task_id = %s", (CLAIM_PROVIDER, cid))
            row = cur.fetchone()
            if row:
                cur.execute("UPDATE tasks SET status = %s, request = %s, generation_id = COALESCE(%s, generation_id) WHERE id = %s",
                            (status, request, gid, row[0]))
            else:
                cur.execute("""INSERT INTO tasks (provider, external_task_id, kind, name_key, generation_id, status, request, created_at)
                               VALUES (%s,%s,'sheet',%s,%s,%s,%s,%s)""",
                            (CLAIM_PROVIDER, cid, slug, gid, status, request, _ts(c.get("created") or time.time())))
            n += 1
    conn.commit()
    return n


def import_pack_sessions(conn, out: Path) -> int:
    """Every out/pack_sessions/*.json (see save_pack_session). Idempotent re-import, one bad file never poisons the rest. Returns the sessions applied."""
    from ..generation import claims as _c
    n = 0
    for s in _c.list_sessions(Path(out)):
        try:
            save_pack_session(conn, s)
            n += 1
        except Exception:
            conn.rollback()
    return n


JOB_STATUS = {"CLAIMED": "RUNNING", "DONE": "DONE", "FAILED": "FAILED", "TIMEOUT": "TIMEOUT"}


def _gen_exists(cur, gid: str | None) -> str | None:
    if not gid:
        return None
    cur.execute("SELECT 1 FROM generations WHERE id = %s", (gid,))
    return gid if cur.fetchone() else None


def save_job(conn, job: dict, out: Path | None = None) -> bool:
    """One out/jobs/J###.json as a `tasks` row, from CLAIMED on (a REQUESTED job has no provider ticket yet and
    external_task_id is NOT NULL). A claim already mirrors its ticket into out/tasks/NNN.json, so the row usually
    exists: it is UPDATED with the job's status, result, cost and links, never duplicated. Idempotent."""
    ext = str(job.get("external_task_id") or "").strip()
    status = JOB_STATUS.get(str(job.get("status") or ""))
    if not ext or not status:
        return False
    provider = str(job.get("provider") or "higgsfield-cli")
    kind = job.get("kind") if job.get("kind") in ("sheet", "video", "single") else "sheet"
    req = job.get("request") or {}
    with conn.cursor() as cur:
        gid = _gen_exists(cur, _parent_id(job.get("generation")))
        vsid = None
        if gid and kind == "video" and req.get("sheet"):
            vsid = _vsid(gid, req.get("sheet"))
            cur.execute("SELECT 1 FROM video_sheets WHERE id = %s", (vsid,))
            vsid = vsid if cur.fetchone() else None
        name_key = ""
        if job.get("task") and out is not None:
            try:
                from ..generation import tasks as _t
                name_key = str(_t.read_task(Path(out), str(job["task"])).get("name_key") or "")
            except Exception:
                name_key = ""
        if not name_key and gid:
            cur.execute("SELECT task_slug FROM generations WHERE id = %s", (gid,))
            r = cur.fetchone()
            name_key = (r[0] if r else "") or ""
        name_key = name_key or str(job.get("id") or ext)
        done_at = _ts(job["completed_at"]) if job.get("completed_at") else None
        cost = job.get("cost")
        cur.execute("SELECT id FROM tasks WHERE provider = %s AND external_task_id = %s", (provider, ext))
        row = cur.fetchone()
        if row is None:
            cur.execute("SELECT id FROM tasks WHERE external_task_id = %s ORDER BY id LIMIT 1", (ext,))
            row = cur.fetchone()
        if row:
            cur.execute(
                """UPDATE tasks SET provider = CASE WHEN EXISTS (SELECT 1 FROM tasks t2 WHERE t2.provider = %s
                                         AND t2.external_task_id = %s AND t2.id <> tasks.id) THEN provider ELSE %s END,
                          kind = %s, status = %s, request = %s, result_ref = %s, completed_at = %s,
                          generation_id = COALESCE(%s, generation_id), video_sheet_id = COALESCE(%s, video_sheet_id),
                          name_key = CASE WHEN name_key = '' THEN %s ELSE name_key END,
                          job_id = %s, model = %s, cost_credits = %s
                   WHERE id = %s""",
                (provider, ext, provider, kind, status, json.dumps(req),
                 json.dumps(job.get("result")) if job.get("result") is not None else None, done_at,
                 gid, vsid, name_key, job.get("id"), job.get("model"), cost, row[0]))
        else:
            cur.execute(
                """INSERT INTO tasks (provider, external_task_id, kind, name_key, generation_id, video_sheet_id, status,
                                      request, result_ref, created_at, completed_at, job_id, model, cost_credits)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (provider, ext, kind, name_key, gid, vsid, status, json.dumps(req),
                 json.dumps(job.get("result")) if job.get("result") is not None else None,
                 _ts(job.get("claimed_at") or job.get("created_at")), done_at, job.get("id"), job.get("model"), cost))
    conn.commit()
    return True


def import_jobs(conn, out: Path) -> int:
    """Every claimed job under out/jobs/ as a tasks row (see save_job). Idempotent. Returns the jobs applied."""
    from ..generation import jobs as _j
    n = 0
    for job in _j.list(Path(out)):
        try:
            n += 1 if save_job(conn, job, out) else 0
        except Exception:
            conn.rollback()      # one bad job file must not poison the rest
    return n


_CALL_COLS = ("ts", "kind", "provider", "model", "status", "attempt", "latency_ms", "tokens_in", "tokens_out",
              "cost", "seed", "prompt_version", "generation_id", "sticker_id", "error", "job")


def save_model_call(conn, line: str) -> bool:
    """One line of out/model_calls.jsonl -> one model_calls row, keyed by the sha256 of the line (re-import adds
    nothing). Returns True when a row was added."""
    line = line.strip()
    try:
        row = json.loads(line)
    except ValueError:
        return False
    if not isinstance(row, dict) or not row.get("kind"):
        return False

    def _i(v):
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _f(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    extra = {k: v for k, v in row.items() if k not in _CALL_COLS}
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO model_calls (line_sha, ts, kind, provider, model, status, attempt, latency_ms, tokens_in,
                                        tokens_out, cost_credits, seed, prompt_version, generation_id, sticker_id,
                                        job_id, error, extra)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (line_sha) DO NOTHING""",
            (hashlib.sha256(line.encode("utf-8")).hexdigest(), _ts(row.get("ts")), str(row["kind"]),
             str(row.get("provider") or ""), str(row.get("model") or ""), str(row.get("status") or "OK"),
             _i(row.get("attempt")) or 1, _i(row.get("latency_ms")), _i(row.get("tokens_in")), _i(row.get("tokens_out")),
             _f(row.get("cost")), None if row.get("seed") is None else str(row["seed"]),
             row.get("prompt_version"), row.get("generation_id"), row.get("sticker_id"), row.get("job"),
             row.get("error"), json.dumps(extra, ensure_ascii=False, default=str)))
        added = cur.rowcount > 0
    conn.commit()
    return added


def import_model_calls(conn, out: Path) -> int:
    """out/model_calls.jsonl -> model_calls. Idempotent. Returns the rows added now."""
    f = Path(out) / "model_calls.jsonl"
    if not f.is_file():
        return 0
    n = 0
    for line in f.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                n += 1 if save_model_call(conn, line) else 0
            except Exception:
                conn.rollback()
    return n


def get_generation(conn, gid: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM generations WHERE id = %s", (gid,))
        g = cur.fetchone()
        if not g:
            return None
        cols = [d[0] for d in cur.description]
        gen = dict(zip(cols, g))
        cur.execute("SELECT * FROM stickers WHERE generation_id = %s ORDER BY idx", (gid,))
        scols = [d[0] for d in cur.description]
        gen["stickers"] = [dict(zip(scols, r)) for r in cur.fetchall()]
        cur.execute("SELECT object_key, kind, bytes, sticker_id FROM assets WHERE generation_id = %s", (gid,))
        gen["assets"] = [{"object_key": r[0], "kind": r[1], "bytes": r[2], "sticker_id": r[3]} for r in cur.fetchall()]
    return gen


def list_generations(conn, limit: int = 50) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT g.id, g.prompt, g.task_slug, g.status, g.parent_id, g.created_at,
                      COUNT(s.id) AS n, SUM(CASE WHEN s.status='READY' THEN 1 ELSE 0 END) AS ready
               FROM generations g LEFT JOIN stickers s ON s.generation_id = g.id
               GROUP BY g.id ORDER BY g.id DESC LIMIT %s""", (limit,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def history(conn, sticker_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT gate, actor, decision, reason, detail, video_sheet_id, ts
               FROM reviews WHERE sticker_id = %s ORDER BY ts""", (sticker_id,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def search(conn, query: str, approved: bool = False, animated: bool = False,
           generation: str | None = None, since: str | None = None, limit: int = 50) -> list[dict]:
    """Lexical search (3A): full-text on search_doc ranked + exact-tag boost, trigram fallback. Logs to search_log."""
    import time as _t
    t0, filt = _t.perf_counter(), {"approved": approved, "animated": animated,
                                   "generation": generation, "since": since}
    cond, args = ["1=1"], []
    if approved:
        cond.append("s.still_review = 'APPROVED'")
    if animated:
        cond.append("s.anim_review = 'APPROVED'")
    if generation:
        cond.append("s.generation_id = %s")
        args.append(generation)
    if since:
        cond.append("g.created_at >= %s::timestamptz")
        args.append(since)
    where = " AND ".join(cond)
    slug = query.strip().lower().replace(" ", "_")
    rows: list[dict] = []
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT s.id, s.generation_id, s.idx, s.key, s.tags, s.name, g.task_slug, s.status, s.reason,
                       s.still_review, s.anim_review, s.animation_status, s.emoji, s.prompt,
                       (SELECT object_key FROM assets WHERE sticker_id = s.id AND kind IN ('PNG','WEBP') LIMIT 1) AS png,
                       (SELECT object_key FROM assets WHERE sticker_id = s.id AND kind = 'WEBM' LIMIT 1) AS webm,
                       ts_rank(s.search_doc, plainto_tsquery('simple', %s))
                         + CASE WHEN s.tags @> ARRAY[%s]::text[] THEN 0.5 ELSE 0 END AS rank
                FROM stickers s JOIN generations g ON g.id = s.generation_id
                WHERE {where} AND s.search_doc @@ plainto_tsquery('simple', %s)
                ORDER BY rank DESC LIMIT %s""", (query, slug, *args, query, limit))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        if not rows:
            import re as _re
            words = [w for w in _re.findall(r"[a-z0-9]+", query.lower()) if w]
            if words:
                # typo fallback: every query word must resemble a word of key/prompt (trigram %),
                # ranked by the mean of the per-word best similarities ("tedy bok" -> the book sticker).
                cur.execute(
                    f"""SELECT s.id, s.generation_id, s.idx, s.key, s.tags, s.name, g.task_slug, s.status, s.reason,
                               s.still_review, s.anim_review, s.animation_status, s.emoji, s.prompt,
                               (SELECT object_key FROM assets WHERE sticker_id = s.id AND kind IN ('PNG','WEBP') LIMIT 1) AS png,
                               (SELECT object_key FROM assets WHERE sticker_id = s.id AND kind = 'WEBM' LIMIT 1) AS webm,
                               (SELECT avg(m) FROM (
                                  SELECT max(similarity(kw, qw)) AS m
                                  FROM regexp_split_to_table(lower(s.key || ' ' || coalesce(g.prompt, '')), '[^a-z0-9]+') kw
                                  CROSS JOIN unnest(%s::text[]) qw WHERE kw <> '' GROUP BY qw) t) AS rank
                        FROM stickers s JOIN generations g ON g.id = s.generation_id
                        WHERE {where} AND NOT EXISTS (
                          SELECT 1 FROM unnest(%s::text[]) qw WHERE NOT EXISTS (
                            SELECT 1 FROM regexp_split_to_table(
                              lower(s.key || ' ' || coalesce(g.prompt, '')), '[^a-z0-9]+') kw2
                            WHERE kw2 <> '' AND kw2 %% qw))
                        ORDER BY rank DESC LIMIT %s""",
                    (words, *args, words, limit))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.execute("INSERT INTO search_log (query, filters, hit_ids, latency_ms) VALUES (%s,%s,%s,%s)",
                    (query, json.dumps(filt), [r["id"] for r in rows], int((_t.perf_counter() - t0) * 1000)))
    conn.commit()
    return rows


def find_task(conn, external_id: str | None = None, key_prefix: str | None = None) -> list[dict]:
    with conn.cursor() as cur:
        if external_id:
            cur.execute("SELECT * FROM tasks WHERE external_task_id = %s", (external_id,))
        else:
            cur.execute("SELECT * FROM tasks WHERE name_key LIKE %s", (f"{key_prefix}%",))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def save_session(conn, s: dict) -> str:
    """One chat session (out/sessions/S###.json) into sessions + interactions + feedback. Idempotent; the file stays the primary store."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO sessions (id, title, settings, focus, subjects, preferences, summary, created_at, updated_at, user_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (id) DO UPDATE SET user_id = EXCLUDED.user_id, title = EXCLUDED.title, settings = EXCLUDED.settings, focus = EXCLUDED.focus,
                 subjects = EXCLUDED.subjects, preferences = EXCLUDED.preferences, summary = EXCLUDED.summary,
                 updated_at = EXCLUDED.updated_at""",
            (s["id"], s.get("title") or "", json.dumps(s.get("settings") or {}), json.dumps(s.get("focus") or {}),
             json.dumps(s.get("subjects") or [], ensure_ascii=False), json.dumps(s.get("preferences") or {}, ensure_ascii=False),
             json.dumps(s.get("summary") or {}, ensure_ascii=False), _ts(s.get("created")), _ts(s.get("updated")), s.get("user") or "local"))
        for it in s.get("interactions") or []:
            cur.execute(
                """INSERT INTO interactions (session_id, seq, user_message, assistant_message, intents, resolved,
                                             result_generation_id, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (session_id, seq) DO NOTHING""",
                (s["id"], it["seq"], it.get("user") or "", it.get("assistant") or "", json.dumps(it.get("intents") or []),
                 json.dumps(it.get("resolved") or {}, ensure_ascii=False, default=str), it.get("generation_id"), _ts(it.get("ts"))))
        for it in s.get("interactions") or []:          # "make 5 like 2" / "the style from 2 and the pose from 7": which sticker was used, in which role
            for ref in ((it.get("resolved") or {}).get("references") or []):
                cur.execute("SELECT 1 FROM generation_references WHERE session_id = %s AND source_id = %s AND COALESCE(target_id, '') = %s AND role = %s",
                            (s["id"], ref.get("source"), ref.get("target") or "", ref.get("role")))
                if not cur.fetchone():
                    cur.execute("INSERT INTO generation_references (session_id, source_id, target_id, role, created_at) VALUES (%s,%s,%s,%s,%s)",
                                (s["id"], ref.get("source"), ref.get("target"), ref.get("role"), _ts(it.get("ts"))))
        for fb in s.get("feedback") or []:
            for sid in fb.get("sticker_ids") or [None]:
                cur.execute(
                    """INSERT INTO feedback (session_id, ts, generation_id, sticker_id, polarity, scope, text)
                       VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (session_id, ts, sticker_id, polarity) DO NOTHING""",
                    (s["id"], _ts(fb.get("ts")), sid.split("/")[0] if sid else None, sid, fb["polarity"], fb["scope"], fb.get("text") or ""))
    conn.commit()
    return s["id"]
