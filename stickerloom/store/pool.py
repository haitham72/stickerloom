"""Phase 3B pool: every APPROVED sticker is indexed by topic; a request first fetches matching stickers (free, instant) and only the missing
count goes to paid generation.

Two scorers behind ONE query path (the callers never fork):
- vectors (`subject_vec` / `action_vec`, 768-d, `stickerloom/services/embed.py`): `0.5*cos(subject) + 0.4*cos(action) + 0.1*lexical`, with the quality gate
  `cos(subject) >= SUBJECT_MIN and cos(action) >= ACTION_MIN`, so "penguin skiing" in a pool with no penguins returns nothing, never the nearest junk;
- lexical (trigram + word coverage), used for rows that have no vectors yet and when no embedding backend is running.
The thresholds are provisional until a labelled query set; the measured separation on the first 45 approved stickers is in
`docs/architecture.md ("Measurements")`. Nothing here spends: embeddings are local and free, and generation is never started by a search."""
from __future__ import annotations

import difflib
import json
import os
import re
import time

THRESH = 0.3          # trigram gate for the lexical scorer (pg_trgm's default)
PER_GEN = 2           # diversity: at most 2 hits per generation
SUBJECT_MIN = float(os.environ.get("STICKERLOOM_POOL_SUBJECT_MIN", 0.50))
ACTION_MIN = float(os.environ.get("STICKERLOOM_POOL_ACTION_MIN", 0.40))
DUP_SIM = 0.97        # near-identical search_text collapses to the best-ranked one


def normalize_topic(s: str) -> str:
    s = str(s or "").lower().replace("'", "").replace("’", "")
    s = re.sub(r"[^\w\s\u0600-\u06FF]", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def _trigrams(s: str) -> set:
    p = f"  {s} "
    return {p[i:i + 3] for i in range(len(p) - 2)} if p.strip() else set()


def _sim(a: str, b: str) -> float:
    A, B = _trigrams(a), _trigrams(b)
    return 2 * len(A & B) / (len(A) + len(B)) if (A or B) else 0.0


def _words(s: str) -> list:
    return [w for w in re.findall(r"[a-z0-9\u0600-\u06FF]+", str(s or "").lower()) if w]


def parse_query(q: str) -> dict:
    """Deterministic patterns first; Arabic/Arabizi/unmatched stay whole-as-subject (an LLM
    parser plugs in here later behind the same return shape)."""
    q = str(q or "").strip()
    m = re.match(r"(.+?)\s+doing\s+(.+)", q, re.I)
    if m:
        return {"subject": m.group(1).strip(), "action": m.group(2).strip(), "topics": []}
    m = re.match(r"(.+?)\s+([a-z]+ing)$", q, re.I)
    if m and len(m.group(1).split()) <= 4:
        return {"subject": m.group(1).strip(), "action": m.group(2).strip(), "topics": []}
    return {"subject": q, "action": "", "topics": []}


def _subject_action(gen: dict, st: dict) -> tuple:
    plan = gen.get("plan") or {}
    ext = (plan.get("extraction") or {}) if isinstance(plan, dict) else {}
    subject = ext.get("subject") or str(gen.get("subject") or "").replace("_", " ") or str(gen.get("task") or "")
    key = st.get("key") or ""
    subj_slug = re.sub(r"\W+", "_", subject.lower()).strip("_")
    action = re.sub(r"^" + re.escape(subj_slug) + r"_?", "", key).replace("_", " ") or st.get("concept") or key
    return subject.strip(), action.strip()


def index_row(gen: dict, st: dict) -> dict:
    subject, action = _subject_action(gen, st)
    emoji = "".join(st.get("emoji") or [])
    style = ((gen.get("plan") or {}).get("slots") or {}).get("style_id", "") if isinstance(gen.get("plan"), dict) else ""
    search_text = f"{subject} \u2014 {action} \u2014 {str(st.get('key') or '').replace('_', ' ')} \u2014 {emoji} \u2014 {style}"   # the key's words, never the file name (a name with a date and a fingerprint would only add noise)
    topics = sorted({normalize_topic(t) for t in (st.get("tags") or []) if normalize_topic(t)})
    shared = (gen.get("source") or "") != "photo"  # uploaded-reference stickers never join the shared pool
    return {"sticker_id": st["id"], "subject": subject, "action": action, "search_text": search_text,
            "topics": topics, "shared": shared}


def reindex(conn, vectors: bool = False, embedder=None) -> int:
    """Backfill every APPROVED sticker (incl. Phase 1-2 prepared runs). Idempotent. Returns the new rows.
    vectors=True also fills the missing subject/action vectors (`embedder(texts, kind)` defaults to stickerloom.services.embed.embed); see `last_embedded`."""
    n = 0
    with conn.cursor() as cur:
        cur.execute(
            """SELECT g.*, s.id AS sid, s.key, s.name, s.tags, s.concept, s.emoji, s.generation_id
               FROM stickers s JOIN generations g ON g.id = s.generation_id
               WHERE s.still_review = 'APPROVED' AND s.id NOT IN (SELECT sticker_id FROM sticker_index)""")
        cols = [d[0] for d in cur.description]
        for r in cur.fetchall():
            row = dict(zip(cols, r))
            st = {"id": row.pop("sid"), "key": row["key"], "name": row["name"], "tags": row["tags"],
                  "concept": row["concept"], "emoji": row["emoji"]}
            idx = index_row(row, st)
            cur.execute(
                """INSERT INTO sticker_index (sticker_id, subject, action, search_text, topics, shared)
                   VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (sticker_id) DO NOTHING""",
                (idx["sticker_id"], idx["subject"], idx["action"], idx["search_text"], idx["topics"], idx["shared"]))
            n += 1
    conn.commit()
    reindex.last_embedded = embed_missing(conn, embedder) if vectors else 0
    return n


reindex.last_embedded = 0


def embed_missing(conn, embedder=None, limit: int = 100000) -> int:
    """Fill subject_vec / action_vec where they are NULL. `embedder(texts, kind) -> [[768 floats]]`. Returns the rows filled."""
    if embedder is None:
        from ..services import embed as _e
        embedder = _e.embed
        model = _e.backend()["model"] or "none"
    else:
        model = getattr(embedder, "model", "custom")
    with conn.cursor() as cur:
        cur.execute("SELECT sticker_id, subject, action, search_text FROM sticker_index WHERE subject_vec IS NULL OR action_vec IS NULL "
                    "ORDER BY sticker_id LIMIT %s", (limit,))
        rows = cur.fetchall()
    done = 0
    for i in range(0, len(rows), 24):
        chunk = rows[i:i + 24]
        sv = embedder([r[1] or r[3] for r in chunk], "document")
        av = embedder([(r[2] or r[1] or r[3]) for r in chunk], "document")
        from ..services import embed as _e
        with conn.cursor() as cur:
            for r, a_, b_ in zip(chunk, sv, av):
                cur.execute("UPDATE sticker_index SET subject_vec = %s::vector, action_vec = %s::vector, embed_model = %s WHERE sticker_id = %s",
                            (_e.literal(a_), _e.literal(b_), model, r[0]))
        conn.commit()
        done += len(chunk)
    return done


def status(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*), count(*) FILTER (WHERE subject_vec IS NOT NULL AND action_vec IS NOT NULL), "
                    "count(*) FILTER (WHERE hidden) FROM sticker_index")
        n, v, h = cur.fetchone()
        cur.execute("SELECT count(*) FROM stickers WHERE still_review = 'APPROVED' AND id NOT IN (SELECT sticker_id FROM sticker_index)")
        un = cur.fetchone()[0]
    return {"indexed": n, "with_vectors": v, "missing_vectors": n - v, "hidden": h, "approved_not_indexed": un}


def hide(conn, sticker_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("UPDATE sticker_index SET hidden = true WHERE sticker_id = %s", (sticker_id,))
        hit = cur.rowcount > 0
    conn.commit()
    return hit


def _close(w: str, h: str) -> float:
    """How well a query word matches a stored word: whole-word similarity, not shared trigrams ("batman" is NOT "man"); a typo or a shared stem
    of 4+ letters still counts ("tedy"~"teddy", "danc"~"dance")."""
    if w == h:
        return 1.0
    r = difflib.SequenceMatcher(None, w, h).ratio()
    if min(len(w), len(h)) >= 4 and w[:4] == h[:4]:
        r = max(r, 0.8)
    return r


def _cover(words: list, hay: list) -> tuple:
    """(all_covered, mean_best): every query word must resemble a stored word (the zero-results rule)."""
    if not words:
        return True, 1.0
    best = [max([_close(w, h) for h in hay] or [0.0]) for w in words]
    return all(b >= 0.75 for b in best), sum(best) / len(best)


_SELECT = """SELECT si.sticker_id, si.subject, si.action, si.search_text, si.topics,
                  s.key, s.emoji, s.generation_id, s.animation_status,
                  (SELECT object_key FROM assets WHERE sticker_id = s.id AND kind IN ('PNG','WEBP') LIMIT 1) AS png{extra}
           FROM sticker_index si JOIN stickers s ON s.id = si.sticker_id JOIN generations g ON g.id = s.generation_id
           WHERE si.hidden = false AND si.shared = true AND s.still_review = 'APPROVED'{where}"""


def _style_clause(style: str | None) -> tuple:
    return (" AND si.search_text ILIKE %s", ["%\u2014 " + str(style).strip()]) if style else ("", [])


def _owner_clause(viewer: str | None) -> tuple:
    """A member searches only the generations they own (files are served by owner, so a hit they could not open is never shown)."""
    return (" AND g.owner = %s", [viewer]) if viewer else ("", [])


def _vector_hits(conn, parsed: dict, query: str, style, embedder, viewer: str | None = None) -> list | None:
    """Candidates scored by cosine, or None when vectors cannot be used right now (no vectors stored, or no embedding backend)."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM sticker_index WHERE subject_vec IS NOT NULL AND action_vec IS NOT NULL")
        if not cur.fetchone()[0]:
            return None
    if embedder is None:
        from ..services import embed as _e
        if not _e.available():
            return None
        embedder = _e.embed
    try:
        qs = embedder([parsed["subject"]], "query")[0]
        qa = embedder([parsed["action"]], "query")[0] if parsed["action"] else None
    except Exception:
        return None
    from ..services import embed as _e
    sty, sargs = _style_clause(style)
    oc, oargs = _owner_clause(viewer)
    sql = _SELECT.format(extra=", 1 - (si.subject_vec <=> %s::vector) AS cs, 1 - (si.action_vec <=> %s::vector) AS ca",
                         where=" AND si.subject_vec IS NOT NULL AND si.action_vec IS NOT NULL" + sty + oc) + " ORDER BY si.subject_vec <=> %s::vector LIMIT 300"
    with conn.cursor() as cur:
        cur.execute(sql, [_e.literal(qs), _e.literal(qa or qs), *sargs, *oargs, _e.literal(qs)])
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    out = []
    for r in rows:
        cs, ca = float(r["cs"]), float(r["ca"])
        if cs < SUBJECT_MIN or (qa is not None and ca < ACTION_MIN):
            continue                                         # the quality gate: nothing, never the closest junk
        lex = _sim(query.lower(), str(r["search_text"]).lower())
        r["rank"] = round((0.5 * cs + 0.4 * ca + 0.1 * lex) if qa is not None else (0.9 * cs + 0.1 * lex), 4)
        r["cos_subject"], r["cos_action"] = round(cs, 3), round(ca, 3)
        out.append(r)
    return out


def _lexical_hits(conn, parsed: dict, query: str, style, only_unembedded: bool, viewer: str | None = None) -> list:
    sw, aw = _words(parsed["subject"]), _words(parsed["action"])
    sty, sargs = _style_clause(style)
    oc, oargs = _owner_clause(viewer)
    where = (" AND (si.subject_vec IS NULL OR si.action_vec IS NULL)" if only_unembedded else "") + sty + oc
    out = []
    with conn.cursor() as cur:
        cur.execute(_SELECT.format(extra="", where=where), [*sargs, *oargs])
        cols = [d[0] for d in cur.description]
        for r in cur.fetchall():
            cand = dict(zip(cols, r))
            ok_s, m_s = _cover(sw, _words(cand["subject"]) + _words(" ".join(cand["topics"])))
            if not ok_s:
                continue
            if aw:
                ok_a, m_a = _cover(aw, _words(cand["action"]) + _words(" ".join(cand["topics"])))
                if not ok_a:
                    continue
                rank = 0.5 * m_s + 0.4 * m_a + 0.1 * _sim(query.lower(), cand["search_text"].lower())
            else:
                rank = 0.6 * m_s + 0.1 * _sim(query.lower(), cand["search_text"].lower())
            cand["rank"] = round(rank * (0.9 if only_unembedded else 1.0), 4)
            out.append(cand)
    return out


def search(conn, query: str, count: int = 9, style: str | None = None, embedder=None, vectors: bool | None = None, per_gen: int | None = None,
           viewer: str | None = None) -> dict:
    """Pool-first search. Returns {parsed, hits, found, missing, mode, ms}: never spends, never generates. `mode`: vector (cosine + gate),
    lexical (no vectors / no embedding backend) or hybrid (vectors plus a lexical pass over rows not embedded yet). vectors=False forces lexical.
    `style` keeps only stickers made in that style_id. `per_gen` is the diversity cap (default PER_GEN = 2 hits per generation; the chat passes `count` to list a whole pack)."""
    t0 = time.perf_counter()
    parsed = parse_query(query)
    hits, mode = None, "lexical"
    if vectors is not False:
        hits = _vector_hits(conn, parsed, query, style, embedder, viewer)
    if hits is not None:
        mode = "vector"
        extra = _lexical_hits(conn, parsed, query, style, only_unembedded=True, viewer=viewer)
        if extra:
            mode, hits = "hybrid", hits + extra
    else:
        hits = _lexical_hits(conn, parsed, query, style, only_unembedded=False, viewer=viewer)
    hits.sort(key=lambda h: -h["rank"])
    seen, taken, final, cap = set(), {}, [], (PER_GEN if per_gen is None else max(1, int(per_gen)))
    for h in hits:  # diversity + near-duplicate collapse (same text -> the better rank wins)
        if h["search_text"] in seen:
            continue
        seen.add(h["search_text"])
        taken[h["generation_id"]] = taken.get(h["generation_id"], 0) + 1
        if taken[h["generation_id"]] <= cap:
            final.append(h)
        if len(final) >= count:
            break
    ms = int((time.perf_counter() - t0) * 1000)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO search_log (query, filters, hit_ids, latency_ms, parsed, gap_generated)"
                    " VALUES (%s,%s,%s,%s,%s,%s)",
                    (query, json.dumps({"pool": True, "count": count, "mode": mode, "style": style}),
                     [h["sticker_id"] for h in final], ms, json.dumps(parsed), 0))
    conn.commit()
    return {"parsed": parsed, "hits": final, "found": len(final), "missing": max(0, count - len(final)), "mode": mode, "ms": ms}
