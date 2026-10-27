"""python -m stickerloom create "<prompt>" | more [G001] | animate [G001] [--slice N] | serve [--port N] [--pace S] [--workers N] | profile [G001] [--sweep 1,4,9] | recheck [G001|all]
   | db (up|migrate|reset --yes|import) | list | show G002 | history G002/S5 | search "<text>" [--approved --animated] | task <external-id> [--key <prefix>]"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
import time

from .flow import pipeline as pl
from .engine.config import EngineConfig
from .runtime.paths import input_root, out_root


def _gid(s):
    return int(s.lstrip("Gg")) if s else None


def _show(out, gid, t0):
    r = pl.read_result(out, gid)
    print(f"{r['generation_id']}  {r['task_slug']}  ({r['source']['subject']} variant {r['source']['variant']}/{r['source']['n_variants']})  {time.perf_counter() - t0:.1f}s")
    for s in r["stickers"]:
        v = f"  video:{s['anim_status']}" if s["anim_status"] != "NOT_REQUESTED" else ""
        print(f"  {s['index']} {s['emoji']} {s['name']:<60} {s['status']}{' ' + s['reason'] if s['reason'] else ''}{v}")
    if r["error"]:
        print("ERROR:", r["error"])


def _dbc():
    """Connect or explain. Returns None (after printing) when Postgres is unreachable."""
    from .store import db as _db
    if not _db.available():
        print(f"Postgres is not reachable at {_db.url()} (start it: python -m stickerloom db up)")
        return None
    return _db.connect()


def db_cmd(out, action: str, yes: bool) -> int:
    from .store import db as _db
    from .store import repo
    if action == "up":
        import subprocess
        yml = out_root_compose()
        # a container that already exists is started by its name, whoever made it (a stickerloom-db made by hand holds its data in its own volume, and a
        # compose "up" would only fail on the name, leaving stickerloom-redis created and never started); compose creates only the missing ones
        services = {"db": "stickerloom-db", "redis": "stickerloom-redis"}
        ls = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"], capture_output=True, text=True)
        if ls.returncode:
            print((ls.stdout or "") + (ls.stderr or "") + "Docker is not answering: start Docker Desktop first")
            return 1
        have = set(ls.stdout.split())
        steps = [["docker", "start", *[n for n in services.values() if n in have]],
                 ["docker", "compose", "-f", str(yml), "up", "-d", *[s for s, n in services.items() if n not in have]]]
        for cmd in steps:
            if cmd[-1] in ("start", "-d"):
                continue                                   # nothing to start / nothing to create
            r = subprocess.run(cmd, capture_output=True, text=True)
            print((r.stdout or "") + (r.stderr or ""))
            if r.returncode:
                return 1
        for _ in range(30):
            _db.reset_cache()
            if _db.available():
                break
            time.sleep(1)
        if not _db.available():
            print("stickerloom-db started but is not answering yet; retry migrate in a few seconds")
            return 1
        action = "migrate"
    if action == "migrate":
        try:
            rep = _db.migrate_report()
        except Exception as e:
            print(f"migrate failed: {e}")
            return 1
        print("migrated: " + (", ".join(rep["applied_now"]) or "nothing new"))
        for n in rep["notes"]:
            print("NOTE    " + n)
        return 0
    if action in ("status", "check"):
        try:
            r = _db.check() if action == "check" else {"status": _db.status(), "drift": [], "ok": None}
        except Exception as e:
            print(f"{action} failed: {e}")
            return 1
        st = r["status"]
        print(f"declared {len(st['declared'])} migration file(s), applied {len(st['applied'])}")
        for n in st["pending"]:
            print(f"PENDING {n}: run python -m stickerloom db migrate")
        for n in st["unknown"]:
            print(f"UNKNOWN {n}: recorded as applied but no file of this checkout declares it")
        for line in r["drift"]:
            print("DRIFT   " + line)
        ok = st["ok"] and not r["drift"]
        print("OK      the database is what the migration files declare" if (ok and action == "check") else "OK      every file is applied" if ok else "NOT OK")
        return 0 if ok else 1
    if action == "reset":
        if not yes:
            print("refusing: `db reset` destroys the local stickerloom database; pass --yes (dev only)")
            return 1
        try:
            _db.reset("yes")
            _db.migrate()
        except Exception as e:
            print(f"reset failed: {e}")
            return 1
        print("reset: local stickerloom database dropped and migrated fresh")
        return 0
    if action == "import":
        c = _dbc()
        if c is None:
            return 1
        with c:
            try:
                _db.migrate()
            except Exception as e:
                print(f"migrate failed: {e}")
                return 1
            nt = repo.import_tasks(c, out)
            nj = repo.import_jobs(c, out)               # Phase 2: claimed jobs update their task rows
            nm = repo.import_model_calls(c, out)        # Phase 2: the model-call ledger
            ns = repo.import_pack_sessions(c, out)       # the pack claim ledger (out/pack_sessions/)
            ok, bad = 0, []
            for gid in pl.list_ids(out):
                try:
                    repo.save_generation(c, out, gid)
                    ok += 1
                except Exception as e:
                    c.rollback()  # one bad generation must not poison the rest of the import
                    bad.append(f"G{gid:03d}: {e}")
        print(f"imported {ok} generation(s), {nt} task file(s), {nj} job(s), {nm} new model call(s), {ns} pack session(s)")
        for b in bad:
            print("FAILED " + b)
        return 1 if bad else 0
    return 1


def worker_cmd(out, args) -> int:
    """`stickerloom worker`: drain the durable queue. One paid call at a time across all workers (a lock under out/); Ctrl-C stops after the current job."""
    from .generation import jobqueue
    from .store import db
    if not db.available():
        print("ERROR   Postgres is not reachable: the queue lives there (python -m stickerloom db up)")
        return 1
    wid = args.wid or jobqueue.worker_id()
    print(f"worker {wid} on {out} (poll {args.poll}s{', once' if args.once else ''}; Ctrl-C to stop)")
    try:
        n = jobqueue.work(out, wid, poll=args.poll, once=args.once, kinds=[k for k in (args.kinds or "").split(",") if k] or None)
    except KeyboardInterrupt:
        print("stopped")
        return 0
    print(f"ran {n} job(s)")
    return 0


def queue_cmd(out, args) -> int:
    from .generation import jobqueue, jobs as _jobs
    from .store import db
    if not db.available():
        print("ERROR   Postgres is not reachable: the queue lives there (python -m stickerloom db up)")
        return 1
    with db.connect() as c:
        if args.action == "status":
            s = jobqueue.stats(c)
            print(f"mode: {jobqueue.mode(out)}  (set STICKERLOOM_JOB_MODE=queue for the server to use it)")
            print("  ".join(f"{k}={v}" for k, v in s.items()))
            with c.cursor() as cur:
                cur.execute("SELECT job_id, kind, status, attempts, max_attempts, locked_by, error FROM job_queue WHERE status IN ('RUNNING','FAILED','DEAD') ORDER BY enqueued_at DESC LIMIT 15")
                for r in cur.fetchall():
                    print(f"  {r[0]} {r[1]:<6} {r[2]:<7} attempt {r[3]}/{r[4]} {r[5] or ''} {r[6] or ''}")
        elif args.action == "reap":
            print(f"reaped {jobqueue.reap(c)} job(s)")
        elif args.action == "sync":
            print(f"enqueued {jobqueue.sync_from_files(c, out)} unfinished job file(s)")
        else:
            if not args.job:
                print("which job? (stickerloom jobs)")
                return 2
            try:
                job = _jobs.resume(out, args.job)               # the same Higgsfield job when it has a ticket (no second charge), else requested again
            except _jobs.JobError as e:
                print(f"ERROR   {e}")
                return 1
            print(f"{job['id']}: {'queued' if jobqueue.enqueue(c, job['id'], job['kind'], (job.get('request') or {}).get('user') or 'local') else 'already on the queue'}")
    return 0


def out_root_compose():
    from pathlib import Path as _P
    return _P(__file__).resolve().parent.parent / "docker-compose.yml"


def store_cmd(args) -> int:
    import json as _json
    from .store import repo
    c = _dbc()
    if c is None:
        return 1
    with c:
        if args.cmd == "list":
            for g in repo.list_generations(c, args.limit):
                print(f"{g['id']}  {g['task_slug'] or g['prompt'][:40]:<42} {g['ready'] or 0}/{g['n'] or 0} {g['status']}"
                      + (f"  parent={g['parent_id']}" if g["parent_id"] else ""))
            return 0
        if args.cmd == "show":
            gid = f"G{_gid(args.gid):03d}"
            g = repo.get_generation(c, gid)
            if not g:
                print(f"no {gid} in Postgres (import: python -m stickerloom db import)")
                return 1
            print(f"{g['id']}  {g['prompt']}\n  task={g['task_slug']} status={g['status']} parent={g['parent_id'] or '-'} "
                  f"template={g['template_id']} v{g['template_version']} assets={len(g['assets'])}")
            for s in g["stickers"]:
                print(f"  S{s['idx']} {''.join(s['emoji'])} {s['key']:<48} {s['status']}"
                      + (f" {s['reason']}" if s["reason"] else "")
                      + f"  still={s['still_review']} anim={s['anim_review']} {s['animation_status']}")
            return 0
        if args.cmd == "history":
            rows = repo.history(c, args.sid.upper())
            if not rows:
                print(f"no decisions for {args.sid} (import first: python -m stickerloom db import)")
                return 1
            for r in rows:
                print(f"{r['ts']}  {r['gate']:<11} {r['decision']:<8} {r['actor']:<6} {r['reason'] or ''}")
            return 0
        if args.cmd == "search":
            rows = repo.search(c, args.query, approved=args.approved, animated=args.animated,
                               generation=args.generation, since=args.since)
            if args.as_json:
                print(_json.dumps(rows, ensure_ascii=False, default=str))
                return 0
            for r in rows:
                print(f"{r['generation_id']}/S{r['idx']} {''.join(r['emoji'])} {r['key']:<48} "
                      f"still={r['still_review']} anim={r['anim_review']}  {r['png'] or r['webm'] or ''}")
            if not rows:
                print("no matches")
            return 0
        if args.cmd == "task":
            rows = repo.find_task(c, external_id=args.ext, key_prefix=args.key)
            if not rows:
                print("no such task (import first: python -m stickerloom db import)")
                return 1
            for t in rows:
                print(f"{t['provider']}:{t['external_task_id']}  kind={t['kind']} name_key={t['name_key']} "
                      f"status={t['status']} generation={t['generation_id'] or '-'}")
            return 0
    return 1


LAB_INPUTS = [  # S1 prompt lab: English, Arabic, Arabizi, occasion, green subject, constraint, blends
    "yellow teddy bear in Pixar 3D iOS style",
    "yellow teddy bear in toon cel shade with ios 3d genmoji style",
    "a cute yellow emoji face, 16 reactions",
    "falcon dancing",
    "banana shocked",
    "camel as cupcake",
    "dog as banana",
    "dog with bananas",
    "teddy bear playing football",
    "teddy bear with no dancing",
    "green frog with big eyes",
    "watermelon sticker pack",
    "paris skyline at night",
    "arabic coffee celebration",
    "eid mubarak stickers",
    "\u062f\u0628 \u064a\u0631\u0642\u0635",
    "\u0635\u0642\u0631 \u0633\u0639\u064a\u062f",
    "\u062a\u062f\u064a \u0628\u064a\u0631 \u0645\u0628\u0633\u0648\u0637",
    "sakr yarkos",
    "teddy bear mabsout",
]


def prompt_cmd(out, args) -> int:
    from .generation import tasks as _t
    if (args.request or "") == "lab":
        ok = 0
        for req in LAB_INPUTS:
            try:
                plan = _t.preview(req, "2x2", args.style, ai=False)
                from .generation import prompter as _pr
                _pr.validate_plan(dict(plan))
                n, key = len(plan["stickers"]), plan["slots"]["key_colour"]
                uniq = len({s["key"] for s in plan["stickers"]})
                good = n == 9 and uniq == 9 and all(s["emoji"] for s in plan["stickers"])
                ok += good
                print(f"{'ok  ' if good else 'FAIL'}  {req[:52]:<54} {n} cells, {uniq} unique keys, key={key}")
            except Exception as e:
                print(f"FAIL  {req[:52]:<54} {e}")
        print(f"{ok}/{len(LAB_INPUTS)} lab inputs pass lint")
        return 0 if ok == len(LAB_INPUTS) else 1
    if not (args.request or "").strip():
        print('usage: stickerloom prompt "<request>" [--grid 3x3|2x2|1x1] [--style ID] [--ai] | stickerloom prompt lab')
        return 1
    try:
        plan = _t.preview(args.request, args.grid, args.style, ai=args.ai)
        if args.review_ai and args.ai:
            from .generation import expander as _ex
            plan = _ex.expand(args.request, tuple(plan["grid"]), use_ai=True, review_ai=True)
    except Exception as e:
        print(f"plan failed: {e}")
        return 1
    from .generation import prompter as _pr
    try:
        _pr.validate_plan(dict(plan))
        lint = "lint: PASS"
    except ValueError as e:
        lint = f"lint: FAIL {e}"
    sl = plan["slots"]
    print(f"task: {plan['task_slug']}  grid: {plan['grid']}  template: {plan['template_id']} v{plan['template_version']}  "
          f"expanded_by: {plan.get('expanded_by', 'deterministic')}  {lint}")
    print(f"subject: {sl.get('subject_description')}  style: {sl.get('style_id')}  key: {sl.get('key_colour')}")
    for s in plan["stickers"]:
        print(f"  {s['index']} {s['emoji']} {s['key']:<48} {' | '.join(s['tags'][1:])}")
    if args.mode == "single":
        for s in plan["stickers"]:
            print(f"--- single S{s['index']} ---\n{s['prompt']}")
    else:
        print(f"--- sheet prompt ---\n{plan['sheet_prompt']}\n--- video prompt ---\n{plan['video_prompt']}")
    return 0


def _job_line(j: dict) -> str:
    el = ""
    if j.get("created_at"):
        import time as _ti
        el = f"  elapsed={int(_ti.time() - j['created_at'])}s"
    return (f"{j['id']}  {j['kind']:<6} {j['status']:<9} task={j.get('task') or '-'} "
            f"ticket={j.get('external_task_id') or '-'}{el}"
            + (f"  error={j['error']}" if j.get("error") else ""))


def jobs_cmd(out, args) -> int:
    import json as _json
    from .generation import jobs as _j
    try:
        rows = _j.list(out, args.status)
    except Exception as e:
        print(e)
        return 1
    if args.as_json:
        print(_json.dumps(rows, ensure_ascii=False))
        return 0
    for j in rows:
        print(_job_line(j))
    if not rows:
        print("no jobs" + (f" with status {args.status}" if args.status else ""))
    return 0


def job_cmd(out, args) -> int:
    import json as _json
    from .generation import jobs as _j
    try:
        if args.action == "show":
            j = _j.read(out, args.jid or "")
            print(_json.dumps(j, indent=2, ensure_ascii=False))
        elif args.action == "create":
            j = _j.create(out, args.kind, task=args.task, generation=args.generation,
                          request={"note": "created from the CLI"})
            print(_job_line(j))
        elif args.action == "claim":
            print(_job_line(_j.claim(out, args.jid or "", args.ticket or "")))
        elif args.action == "done":
            j = _j.done(out, args.jid or "", args.file or "", args.model or "", args.cost)
            print(_job_line(j) + f"  file={j['result']['file']} sha={j['result']['sha256'][:12]}")
        elif args.action == "fail":
            print(_job_line(_j.fail(out, args.jid or "", args.reason)))
        elif args.action == "requeue":
            print(_job_line(_j.requeue(out, args.jid or "")))
        return 0
    except _j.JobError as e:
        print(e)
        return 1


def hf_cmd(out, args) -> int:
    from .generation import higgsfield as _hf, jobs as _j
    try:
        if args.action == "status":
            a = _hf.account()
            print(f"Higgsfield: {a['credits']:g} credits, plan {a['plan'] or '?'}")
        elif args.action == "models":
            d = _hf.load_models(out, refresh=args.refresh)
            if not d:
                print("The Higgsfield CLI is not installed and there is no cached list.")
                return 1
            print("models: " + ", ".join(f"{t} {d['counts'].get(t, 0)}" for t in _hf.TYPES) + f"  ->  {out / _hf.DUMP_NAME}")
            if args.type:
                for m in d.get(args.type, []):
                    ps = ", ".join(p["name"] + ("=" + "/".join(map(str, p["enum"])) if p.get("enum") else "") for p in m["params"] if p["name"] not in ("prompt",))
                    print(f"  {m['job_type']:<34} {m['display_name']:<30} {ps}")
        elif args.action == "run":
            j = _j.fulfil(out, args.jid or "")
            print(_job_line(j) + (f"  cost={j['cost']:g}" if j.get("cost") else "") + (f"  {j['error']}" if j.get("error") else ""))
            return 0 if j["status"] == "DONE" else 1
        return 0
    except (_hf.HiggsError, _j.JobError) as e:
        print(e)
        return 1


def pool_cmd(args) -> int:
    import json as _json
    from .store import pool as _pool
    c = _dbc()
    if c is None:
        return 1
    with c:
        if args.action == "status":
            st = _pool.status(c)
            print(f"{st['indexed']} indexed, {st['with_vectors']} with vectors, {st['missing_vectors']} missing vectors, {st['hidden']} hidden, "
                  f"{st['approved_not_indexed']} approved but not indexed")
            return 0
        if args.action == "reindex":
            vec = not getattr(args, "no_vectors", False)
            try:
                n = _pool.reindex(c, vectors=vec)
            except Exception as e:                       # an embedding backend that is down never blocks the lexical index
                print(f"indexed (vectors skipped: {e})")
                return 1
            print(f"indexed {n} new sticker(s)" + (f", filled vectors for {_pool.reindex.last_embedded}" if vec else ""))
            return 0
        if args.action == "hide":
            print("hidden" if _pool.hide(c, (args.query or "").upper()) else "no such indexed sticker")
            return 0
        res = _pool.search(c, args.query or "", count=args.count, style=getattr(args, "style", None))
        if args.as_json:
            print(_json.dumps(res, ensure_ascii=False, default=str))
            return 0
        print(f"parsed: subject={res['parsed']['subject']!r} action={res['parsed']['action']!r}  mode={res['mode']} {res['ms']} ms")
        for h in res["hits"]:
            print(f"  {h['sticker_id']} {''.join(h['emoji'])} {h['key']:<44} rank={h['rank']}"
                  + (f" cos={h['cos_subject']}/{h['cos_action']}" if "cos_subject" in h else ""))
        if res["missing"]:
            print(f"Found {res['found']} - generate {res['missing']} more? (confirm first: generation is never automatic)")
        elif not res["hits"]:
            print("no pool matches (zero, never the closest junk)")
        return 0


def photo_cmd(out, args, cfg) -> int:
    """3C, offline core: photo -> cutout (existing alpha | chroma | AI matte | GrabCut) -> the Phase 1
    edge finish -> a validated 512 sticker in out/photo/. Private by default (shared=false, no pool row)."""
    import json as _json
    from pathlib import Path as _P
    import numpy as _np
    from .engine.render import bbox_of, fit_scale, render_sticker
    from .media.library import LibraryError, cutout, decode_image, png_bytes, validate_render
    src = _P(str(args.file or ""))
    if not src.is_file():
        print(f"no such photo: {args.file}")
        return 1
    if src.stat().st_size > 50 * 1024 * 1024:
        print("photo is over 50 MB")
        return 1
    try:
        rgba = decode_image(src.read_bytes())
    except LibraryError as e:
        print(e)
        return 1
    if args.outline < 0 or args.outline > 40 or args.erode < 0 or args.erode > 8:
        print("outline is 0-40, erode is 0-8")
        return 1
    try:
        cut, info = cutout(rgba, cfg, args.method)
    except LibraryError as e:
        print(e)
        return 1
    bb = bbox_of(_np.ascontiguousarray(cut[..., 3]))
    if not bb:
        print("no subject found")
        return 1
    sticker = render_sticker(cut, bb, fit_scale(bb, cfg), cfg, args.outline, args.erode)
    try:
        body, ext, checks = validate_render(png_bytes(sticker), cfg)
    except LibraryError as e:
        print(e)
        return 1
    d = _P(out) / "photo"
    d.mkdir(parents=True, exist_ok=True)
    stem = f"photo-{src.stem[:40]}"
    (d / f"{stem}.{ext}").write_bytes(body)
    (d / f"{stem}.json").write_text(_json.dumps(
        {"src": str(src), "method": info.get("method"), "foreground": info.get("foreground"),
         "outline_px": args.outline, "erode_px": args.erode, "shared": False,
         "checks": checks, "warning": info.get("warning")}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{stem}.{ext}  method={info.get('method')} foreground={info.get('foreground')} "
          + " ".join(f"{n}={'ok' if ok else 'FAIL'}({d_})" for n, ok, d_ in checks))
    return 0


def create_auto(out, inp, cfg, prompt: str, grid: str = "2x2", timeout_min: float = 30) -> int:
    """`create --auto`: live generation to a verified pack with no questions. A headless
    console starts the sheet job; the creator state machine (standing approval, no vision
    judge, end at the pack) drives it through stills to a G5-verified library pack."""
    from .agent import creator as _creator
    from .agent.tools import ConsoleTools
    from .console.server import Console
    from .generation import tasks as _tasks
    from . import providers as _providers
    if grid not in ("1x1", "2x2", "3x3"):
        print(f"grid must be 1x1, 2x2 or 3x3, not '{grid}'")
        return 1
    p = _providers.current()
    if not p.available():
        print(f"live generation is not available ({p.setup_hint})")
        return 1
    try:
        c = Console(out, inp, cfg=cfg)
    except Exception as e:
        print(e)
        return 1
    try:
        try:
            r = c.live("sheet", {"prompt": prompt, "grid": grid, "style_id": "flat_vector", "ai": False})
        except pl.PipelineError as e:
            print(e)
            return 1
        print(f"sheet job {r['job']} (task {r['task']}, model {r['model']}, estimate {r.get('estimate')})")
        try:
            subject = _tasks.subject_of(_tasks.read_task(out, r["task"])["plan"])
        except Exception:
            subject = " ".join(str(prompt).split())[:60]
        run = _creator.new_run(prompt=prompt, subject=subject, grid=grid, style_id="flat_vector",
                               scope="images", bypass=True, estimate=r.get("estimate"), video_estimate=None, end="pack")
        run["job"] = r["job"]
        tools = ConsoleTools(c)
        deadline = time.time() + timeout_min * 60
        while run["status"] not in ("done", "stopped", "failed"):
            c.wait_jobs(120)
            run = _creator.advance(tools, run, vision_allowed=False,
                                   telegram_ready=lambda: (False, "Telegram is not connected"))
            if run["status"] not in ("done", "stopped", "failed") and time.time() > deadline:
                print(f"timed out waiting for the pack (still at step '{run['step']}')")
                return 1
        for line in run.get("log") or []:
            print("  " + line["text"])
        if run["status"] != "done":
            stop = run.get("stop") or {}
            print(f"stopped at '{run['step']}': {stop.get('why') or run['status']}")
            return 1
        print(f"pack {run['pack_id']} from {run['generation']} ({run['subject']})")
        return 0
    finally:
        c.release_writer()


def serve_reload(argv: list[str], poll: float = 1.0) -> int:
    """`serve --reload`: run the server as a child process and start it again when a .py file under stickerloom/ changes (stdlib only, any OS).
    The child holds the writer lock; it is stopped before the next one starts, so there is never a second writer. Ctrl+C stops both."""
    import signal
    import subprocess
    import time
    def _stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, _stop)               # explicit: a process started in the background inherits "ignore Ctrl+C"
    signal.signal(signal.SIGTERM, _stop)
    root = Path(__file__).resolve().parent
    def stamp():
        return {f: f.stat().st_mtime_ns for f in root.rglob("*.py") if "__pycache__" not in f.parts}
    def stop(proc):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait()
    seen = stamp()
    proc = subprocess.Popen([sys.executable, "-m", "stickerloom", *argv])
    print(f"reload  watching {root} for .py changes (Ctrl+C stops)", flush=True)
    try:
        while True:
            time.sleep(poll)
            now = stamp()
            if now != seen:
                changed = sorted({f.name for f in set(now) ^ set(seen)} | {f.name for f in now if f in seen and now[f] != seen[f]})
                seen = now
                print(f"reload  {', '.join(changed[:5])} changed: restarting the server", flush=True)
                stop(proc)
                proc = subprocess.Popen([sys.executable, "-m", "stickerloom", *argv])
            elif proc.poll() is not None and proc.returncode not in (0, None):
                print(f"reload  the server stopped (exit {proc.returncode}); fix the error and save: it starts again", flush=True)
                while stamp() == seen:
                    time.sleep(poll)
    except KeyboardInterrupt:
        pass
    finally:
        stop(proc)
    return 0


def export_cmd(out, args) -> int:
    """`stickerloom export <pack> --to dir|telegram` (WP9): the pack's files plus
    a manifest. The telegram target is mocked: the offline send plan, no token,
    no network, nothing sent and nothing recorded."""
    from .media import pack_exchange as _px
    try:
        rep = _px.export_telegram_mock(out, args.pack) if args.to == "telegram" else _px.export_pack(out, args.pack, args.dest)
    except _px.PackExchangeError as e:
        print(e)
        return 1
    for line in _px.cli_summary(rep, "telegram" if args.to == "telegram" else "export"):
        print(line)
    return 0


def import_cmd(out, args) -> int:
    """`stickerloom import <dir|zip>` (WP9): verify-before-write, then a new pack
    with remapped ids."""
    from .media import pack_exchange as _px
    try:
        rep = _px.import_pack(out, args.path, name=args.name, owner=args.owner or "local")
    except _px.PackExchangeError as e:
        print(e)
        return 1
    for line in _px.cli_summary(rep, "import"):
        print(line)
    return 0


def main(argv=None) -> int:
    import sys as _sys
    try:  # Windows consoles default to cp1252, which cannot print emoji: replace, never crash
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="stickerloom")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create"); c.add_argument("prompt")
    c.add_argument("--auto", action="store_true", help="live generation to a verified pack with no questions (sheet -> stills -> pack, no Telegram send)")
    c.add_argument("--grid", default="2x2", help="sheet grid for --auto (2x2, 3x3, 1x1)")
    c.add_argument("--gates", choices=["manual", "auto", "off"], default=None,
                   help="approval gates: manual (a human approves every gate), auto (verifier-passing auto-approves, judgement calls auto-overridden, TECHNICAL stops), "
                        "off (no gate pauses; verification still runs and TECHNICAL failures still block the pack). Default: STICKERLOOM_GATES, else manual.")
    for n in ("more", "another"):
        m = sub.add_parser(n); m.add_argument("gid", nargs="?")
    a = sub.add_parser("animate"); a.add_argument("gid", nargs="?"); a.add_argument("--slice", type=int)
    _doc = sub.add_parser("doctor", help="offline-friendly self check: what is installed, what is missing, and how to fix it")
    _doc.add_argument("--models", action="store_true",
                      help="refresh the cached model list before reporting it (section 4: models available)")
    _doc.add_argument("--since", default="24h",
                      help="freshness window for the cached model list: 24h, 30m, 7d or seconds (default: 24h)")
    tt = sub.add_parser("test", help="explicit test gates: fast smoke, focused regressions, mapped area; `slow` is retired and runs only when asked for by name")
    tt.add_argument("tier", choices=["fast", "focused", "area", "slow"]); tt.add_argument("area", nargs="?")
    rc = sub.add_parser("recheck", help="run the border check on animations made before it existed"); rc.add_argument("gid", nargs="?", default="all")
    rl = sub.add_parser("reloop", help="make the animations again from the stored videos with the current loop (free), and refresh the pack copies"); rl.add_argument("gid", nargs="*", default=["all"])
    pr = sub.add_parser("profile", help="time the animation of a generation stage by stage (nothing is saved)")
    pr.add_argument("gid", nargs="?"); pr.add_argument("--sweep", default="", help="worker counts to compare, e.g. 1,4,9")
    s = sub.add_parser("serve"); s.add_argument("--port", type=int, default=8770); s.add_argument("--pace", type=float, default=0.0); s.add_argument("--stdlib", action="store_true", help="the old stdlib server (kept for one release)")
    s.add_argument("--reload", action="store_true", help="restart the server by itself when a .py file of the app changes (the UI files are read from disk on every request already)")
    for p_ in (s, a):
        p_.add_argument("--workers", type=int, help="cells animated at the same time (default: CPU count up to 9, or STICKERLOOM_ANIM_WORKERS)")
    d = sub.add_parser("db", help="Postgres: up (start stickerloom-db) | migrate | status (applied vs pending files) | check (does the live schema match the files?) | reset --yes (dev only) | import (backfill out/)")
    d.add_argument("action", choices=["up", "migrate", "status", "check", "reset", "import"]); d.add_argument("--yes", action="store_true")
    sub.add_parser("metrics", help="quality and timing numbers from out/: time to the first sticker, approval rates, regeneration rate")
    po_ = sub.add_parser("prune-orphans", help="delete empty stale batch folders (G### with no result.json and no files); lists without --apply")
    po_.add_argument("--apply", action="store_true", help="delete the empty folders (default: list only)")
    li = sub.add_parser("list", help="generations in Postgres, newest first"); li.add_argument("--limit", type=int, default=50)
    sh = sub.add_parser("show", help="one generation: stickers, files, gate decisions"); sh.add_argument("gid")
    hi = sub.add_parser("history", help="every decision for one sticker, in time order"); hi.add_argument("sid")
    se = sub.add_parser("search", help="search stickers in Postgres (full-text, trigram fallback)"); se.add_argument("query")
    se.add_argument("--approved", action="store_true"); se.add_argument("--animated", action="store_true")
    se.add_argument("--generation"); se.add_argument("--since"); se.add_argument("--json", action="store_true", dest="as_json")
    ta = sub.add_parser("task", help="what happened to a provider task id"); ta.add_argument("ext", nargs="?"); ta.add_argument("--key")
    pr = sub.add_parser("prompt", help='print locks, concepts and template-built prompts (S1 lab: "prompt lab")')
    pr.add_argument("request", nargs="?"); pr.add_argument("--grid", default="2x2"); pr.add_argument("--style", default="flat_vector")
    pr.add_argument("--ai", action="store_true"); pr.add_argument("--review-ai", action="store_true")
    pr.add_argument("--mode", default="sheet", choices=["sheet", "single"])
    js = sub.add_parser("jobs", help="pending generation jobs for the operator"); js.add_argument("--status")
    js.add_argument("--json", action="store_true", dest="as_json")
    jo = sub.add_parser("job", help="show|create|claim|done|fail|requeue one job")
    jo.add_argument("action", choices=["show", "create", "claim", "done", "fail", "requeue"]); jo.add_argument("jid", nargs="?")
    jo.add_argument("--kind", default="sheet"); jo.add_argument("--task"); jo.add_argument("--generation")
    jo.add_argument("--ticket"); jo.add_argument("--file"); jo.add_argument("--model"); jo.add_argument("--cost", type=float)
    jo.add_argument("--reason", default="")
    hf = sub.add_parser("hf", help="Higgsfield CLI: status (credits) | models [--type image|video] [--refresh] (full list) | run J### (fulfil a job)")
    hf.add_argument("action", choices=["status", "models", "run"]); hf.add_argument("jid", nargs="?")
    hf.add_argument("--type", choices=["image", "video", "audio", "text"]); hf.add_argument("--refresh", action="store_true")
    jd = sub.add_parser("judge", help="S6: the vision model pre-reviews a generation's stickers (history lines with actor vlm; a human still decides)")
    jd.add_argument("gid", nargs="?"); jd.add_argument("--anim", action="store_true", help="judge the animations (4 frames of each) instead of the stills")
    jd.add_argument("--force", action="store_true", help="ask again even where a verdict of this judge version exists")
    jd.add_argument("--status", action="store_true", help="which model, which policy")
    mc = sub.add_parser("measure-cells", help="S4: the share of video cells that leave their slot, per slot_fill (reads result files only)")
    mc.add_argument("--json", action="store_true", dest="as_json"); mc.add_argument("--record", action="store_true", help="append to docs/architecture.md Measurements section")
    ms_ = sub.add_parser("measure-sharpness", help="how soft each stored animation is next to its own still (edge detail ratio and the blur it equals)")
    ms_.add_argument("--json", action="store_true", dest="as_json"); ms_.add_argument("--record", action="store_true", help="append to docs/architecture.md Measurements section")
    oa = sub.add_parser("openapi", help="the HTTP contract: print it, or write it (--json FILE) and TypeScript types (--ts FILE)")
    oa.add_argument("--json", dest="json_file"); oa.add_argument("--ts", dest="ts_file")
    wk = sub.add_parser("worker", help="run provider jobs from the durable queue (needs Postgres; the server enqueues them when STICKERLOOM_JOB_MODE=queue)")
    wk.add_argument("--once", action="store_true", help="run what is due now, then exit"); wk.add_argument("--poll", type=float, default=2.0, help="seconds to sleep when idle")
    wk.add_argument("--id", dest="wid"); wk.add_argument("--kinds", help="only these kinds, comma separated (sheet,video,single)")
    qu = sub.add_parser("queue", help="the job queue: status | retry JOB | reap | sync (enqueue unfinished job files)")
    qu.add_argument("action", choices=["status", "retry", "reap", "sync"]); qu.add_argument("job", nargs="?")
    po = sub.add_parser("pool", help="search approved stickers first; generate only the gaps")
    po.add_argument("action", choices=["search", "reindex", "hide", "status"]); po.add_argument("query", nargs="?")
    po.add_argument("--no-vectors", action="store_true", help="reindex: lexical rows only (no embedding calls)"); po.add_argument("--style")
    po.add_argument("--count", type=int, default=9); po.add_argument("--json", action="store_true", dest="as_json")
    pa = sub.add_parser("particles", help="adopt: older effect runs that made something become saved particle rows under their stickers (idempotent, free); adopt-emoji-map: migrate random sets onto the per-emoji reaction map")
    pa.add_argument("action", choices=["adopt", "adopt-emoji-map"])
    ph = sub.add_parser("photo", help="a photo -> a cut-out 512 sticker (3C; on-device by default)")
    ph.add_argument("file"); ph.add_argument("--method", default="auto", choices=["auto", "matte", "grabcut"])
    ph.add_argument("--outline", type=int, default=12); ph.add_argument("--erode", type=int, default=0)
    ex = sub.add_parser("export", help="export a library pack: --to dir writes PNG stills + WebM animations + manifest.json; --to telegram shows the mocked send plan (no token, no network, nothing sent)")
    ex.add_argument("pack", help="pack id, slug or exact name"); ex.add_argument("--to", choices=["dir", "telegram"], required=True)
    ex.add_argument("--dest", default=None, help="export folder (default: <out>/exports/<slug>)")
    im = sub.add_parser("import", help="verify a pack export (folder or zip) and import it as a new pack with fresh ids (nothing is written until verification passes)")
    im.add_argument("path"); im.add_argument("--as", dest="name", default=None, help="name of the new pack (default: the exported title)")
    im.add_argument("--owner", default="local")
    args = ap.parse_args(argv)
    if args.cmd == "test":
        if args.tier == "area" and not args.area:
            ap.error("test area requires a module path, for example engine/video")
        if args.tier not in ("area", "focused") and args.area:
            ap.error(f"test {args.tier} takes no module path")
        from . import test_tiers
        return test_tiers.run(args.tier, args.area)
    out, inp, cfg, t0 = out_root(), input_root(), EngineConfig(), time.perf_counter()
    if getattr(args, "workers", None):
        from dataclasses import replace
        cfg = replace(cfg, anim_workers=max(1, args.workers))
    if args.cmd == "doctor":
        return doctor(refresh_models=args.models,
                        models_max_age_s=_parse_since(args.since), since_label=args.since)
    if args.cmd == "prune-orphans":
        orphans = pl.orphan_batches(out)
        if not orphans:
            print("no orphan batch folders (every G### has its result.json)")
            return 0
        if not args.apply:
            for r in orphans:
                print(f"{r['id']}  {r['files']} file(s)  {r['path']}")
            print(f"{len(orphans)} orphan folder(s); re-run with --apply to delete the empty ones (folders with files are kept and reported)")
            return 0
        res = pl.prune_orphans(out)
        for i in res["removed"]:
            print(f"removed {i}")
        for k in res["kept"]:
            print(f"kept {k['id']}: {k['why']}")
        print(f"{len(res['removed'])} removed, {len(res['kept'])} kept")
        return 0
    if args.cmd == "particles":
        from .flow import particle_sets
        from .media.library import Library
        from .runtime.writer_lock import WriterBusy, WriterLock
        if getattr(args, "action", "adopt") == "adopt-emoji-map":
            try:
                with WriterLock(out, "stickerloom particles adopt-emoji-map"):
                    made = particle_sets.adopt_emoji_map(out, Library(out))
            except (WriterBusy, pl.PipelineError) as e:
                print(f"ERROR   {e}")
                return 1
            for m in made:
                print(f"{m['set']} -> {m['token']} ({m['preset']})")
            print(f"{len(made)} set(s) mapped" if made else "nothing to map")
            return 0
        try:
            with WriterLock(out, "stickerloom particles adopt"):      # a drawn sheet may be cut again as particles: result.json has one writer at a time
                made = particle_sets.adopt_effects(out, Library(out), cfg=cfg)
        except (WriterBusy, pl.PipelineError) as e:
            print(f"ERROR   {e}")
            return 1
        for m in made:
            print(f"{m['effect']} -> {m['set']} ({m['mode']}) under {', '.join(m['owners']) or 'no sticker'}")
        print(f"{len(made)} run(s) adopted" if made else "nothing to adopt")
        return 0
    from .runtime.writer_lock import WriterBusy, WriterLock
    if args.cmd == "recheck":
        ids = pl.list_ids(out) if args.gid == "all" else [_gid(args.gid)]
        tot = bad = 0
        try:
            with WriterLock(out, "stickerloom recheck"):          # a running server also writes result.json: one writer at a time
                for gid in ids:
                    r = pl.recheck_bounds(out, gid, cfg)
                    tot += r["checked"]; bad += len(r["flagged"])
                    if r["checked"]:
                        print(f"G{gid:03d}: checked {r['checked']}, out of bounds: {', '.join('S' + str(i) for i in r['flagged']) or 'none'}")
        except WriterBusy as e:
            print(e)
            return 1
        print(f"{tot} animations checked, {bad} leave their cell and are now blocked for review")
        return 0
    if args.cmd == "reloop":
        from .flow import gates
        from .media.library import Library
        ids = pl.list_ids(out) if args.gid in (["all"], []) else [_gid(g) for g in args.gid]
        lib, tot, copies, errors = Library(out), 0, 0, 0
        try:
            with WriterLock(out, "stickerloom reloop"):           # the server also writes result.json and the library: one writer at a time
                for gid in ids:
                    try:
                        r = gates.reloop(out, gid, cfg, lib)
                    except pl.PipelineError as e:
                        if e.code == 404:
                            print(f"G{gid:03d}: skipped: {e}", flush=True)
                            continue
                        errors += 1
                        print(f"G{gid:03d}: failed: {e}", flush=True)
                        continue
                    if r.get("skipped"):
                        print(f"G{gid:03d}: skipped: {r['skipped']}", flush=True)
                    if r.get("error"):
                        errors += 1
                        print(f"G{gid:03d}: failed: {r['error']}", flush=True)
                    if r["animations"]:
                        tot += r["animations"]; copies += r["pack_copies"]
                        print(f"G{gid:03d}: {r['animations']} animations made again, {r['pack_copies']} pack copies refreshed", flush=True)
        except WriterBusy as e:
            print(e)
            return 1
        print(f"{tot} animations made again with the current loop, {copies} pack copies refreshed")
        return 1 if errors else 0
    if args.cmd == "profile":
        return profile(out, _gid(args.gid), cfg, args.sweep)
    if args.cmd == "db":
        return db_cmd(out, args.action, args.yes)
    if args.cmd == "metrics":
        from .flow import metrics as _m
        m = _m.collect(out)
        t, a, r, s = m["time_to_first_sticker_s"], m["approval_rate"], m["regeneration_rate"], m["stickers"]
        pct = lambda v: "n/a" if v is None else f"{v * 100:.0f}%"
        print(f"{m['batches']} batch(es), {s['ready']} sticker(s) ready, {m['failed_batches']} batch(es) with none")
        print(f"time to the first sticker: " + ("n/a" if not t["n"] else f"median {t['median']} s, p90 {t['p90']} s, max {t['max']} s ({t['n']} batches)"))
        print(f"approved at G2 (stills): {pct(a['still'])} of the {s['approved'] + s['rejected']} decided; at G4 (animations): {pct(a['animation'])} of the {s['anim_approved'] + s['anim_rejected']} decided; {s['undecided']} still undecided")
        print(f"redo batches: {r['redo_batches']} of {r['batches']} ({pct(r['rate'])})")
        return 0
    if args.cmd in ("list", "show", "history", "search", "task"):
        return store_cmd(args)
    if args.cmd == "prompt":
        return prompt_cmd(out, args)
    if args.cmd == "jobs":
        return jobs_cmd(out, args)
    if args.cmd == "job":
        return job_cmd(out, args)
    if args.cmd == "hf":
        return hf_cmd(out, args)
    if args.cmd == "judge":
        from .vision import judge as _vj
        if args.status:
            print(_vj.status())
            return 0
        if _vj.status()["provider"] == "none":
            print("No vision backend: start LM Studio (STICKERLOOM_LOCAL_URL) or set OPENAI_API_KEY.")
            return 1
        from .runtime.writer_lock import WriterBusy, WriterLock
        try:
            with WriterLock(out, "stickerloom judge"):
                r = _vj.judge_generation(out, _gid(args.gid) or pl.latest_id(out), "anim" if args.anim else "still", force=args.force)
        except (pl.PipelineError, WriterBusy) as e:
            print(e)
            return 1
        print(f"{r['generation']} {r['scope']}: judged {r['judged']} ({r['cached']} cached), approved {r['approved']}, rejected {r['rejected']}, "
              f"unjudged {r['unjudged']} · model {r['model']} ({r['version']}) · {r['calls']} call(s)")
        rec = r["recovery"]
        print(f"recovery (a recommendation, nothing is spent): {rec['action']}" + (f" {rec['cells']}" if rec["cells"] else "") + f" · {rec['reason']}")
        return 0
    if args.cmd == "measure-cells":
        import json as _json
        from pathlib import Path as _Path
        from .flow import measure
        m = measure.measure(out)
        print(_json.dumps(m, ensure_ascii=False, indent=2) if args.as_json else measure.render(m))
        if args.record and m["cells"]:
            print("recorded in", measure.record(out, m, _Path(__file__).resolve().parent.parent.parent / "docs" / "architecture.md"))
        return 0
    if args.cmd == "openapi":
        import json as _json
        from pathlib import Path as _Path
        from .console import openapi as _oa
        spec = _oa.build()
        if args.json_file:
            _Path(args.json_file).write_text(_json.dumps(spec, indent=2), encoding="utf-8")
        if args.ts_file:
            _Path(args.ts_file).write_text(_oa.typescript(spec), encoding="utf-8")
        if not (args.json_file or args.ts_file):
            print(_json.dumps(spec, indent=2))
        else:
            print(f"{sum(len(v) for v in spec['paths'].values())} operations, {len(spec['components']['schemas'])} schemas")
        return 0
    if args.cmd == "measure-sharpness":
        import json as _json
        from pathlib import Path as _Path
        from .flow import measure
        m = measure.sharpness(out)
        print(_json.dumps(m, ensure_ascii=False, indent=2) if args.as_json else measure.render_sharpness(m))
        if args.record and m["cells"]:
            f = _Path(__file__).resolve().parent.parent.parent / "docs" / "architecture.md"
            import time as _t
            with open(f, "a", encoding="utf-8") as fh:
                fh.write("\n## measure-sharpness " + _t.strftime("%Y-%m-%d %H:%M") + "\n\n```\n" + measure.render_sharpness(m) + "\n```\n")
            print("recorded in", f)
        return 0
    if args.cmd in ("worker", "queue"):
        return worker_cmd(out, args) if args.cmd == "worker" else queue_cmd(out, args)
    if args.cmd == "pool":
        return pool_cmd(args)
    if args.cmd == "photo":
        return photo_cmd(out, args, cfg)
    if args.cmd == "export":
        return export_cmd(out, args)
    if args.cmd == "import":
        return import_cmd(out, args)
    try:
        if args.cmd == "serve":
            if args.reload:
                return serve_reload([x for x in (argv if argv is not None else sys.argv[1:]) if x != "--reload"])
            from .console.server import serve
            tls = None
            cert, key = os.environ.get("STICKERLOOM_TLS_CERT"), os.environ.get("STICKERLOOM_TLS_KEY")
            if cert and key:                                  # optional HTTPS on 127.0.0.1 with your own certificate
                tls = {"cert": cert, "key": key}
            serve(out, inp, args.port, args.pace, cfg, stdlib=True if args.stdlib else None, tls=tls)
            print("Stopped.", flush=True)
            os._exit(0)          # the server has stopped: a worker thread still waiting on a provider must not keep Ctrl+C from ending the process
                                 # (a paid job was written to out/jobs/ at claim and is recovered on the next start)
        if getattr(args, "gates", None):                                  # thin pass-through: the flag wins for this run, the drivers read it via gates.gates_mode()
            from .flow import gates as _gates
            os.environ[_gates.GATES_ENV_VAR] = _gates.gates_mode(explicit=args.gates)
        if args.cmd == "create" and args.auto:
            return create_auto(out, inp, cfg, args.prompt, grid=args.grid)     # outside the lock: the headless console takes it
        with WriterLock(out, f"stickerloom {args.cmd}"):          # create / more / animate write result.json: one writer at a time
            if args.cmd == "create":
                gid = pl.start(args.prompt, out, inp); pl.run_stills(out, gid, cfg)
            elif args.cmd in ("more", "another"):
                gid = pl.more(out, inp, _gid(args.gid)); pl.run_stills(out, gid, cfg)
            else:
                gid = _gid(args.gid) or pl.latest_id(out)
                pl.run_animate(out, gid, cfg, "slice" if args.slice else "pack", args.slice)
        _show(out, gid, t0)
        return 0
    except (pl.PipelineError, WriterBusy) as e:
        print(e)
        return 1


def profile(out, gid, cfg, sweep: str) -> int:
    """Where the time goes when a generation is animated: every cell, every stage (milliseconds), then the whole batch at each worker count.
    Runs the real engine on the prepared source, without the result cache and without saving anything."""
    import os
    from dataclasses import replace
    gid = gid or pl.latest_id(out)
    res = pl.read_result(out, gid)
    if not res["source"]["has_video"]:
        print(f"G{gid:03d} has no prepared video."); return 1
    cfg = pl.cfg_for(res, cfg)
    cells = [s["index"] for s in res["stickers"] if s["status"] == "READY"]
    src = res["source"]
    print(f"G{gid:03d} {res['task_slug']}: {len(cells)} cells, source {'pre-sliced clips' if src.get('clips') else '3x3 mp4'} ({(src.get('video_info') or {}).get('size', '')}), "
          f"{os.cpu_count()} CPUs, default workers {cfg.anim_workers}\n")
    counts = [int(x) for x in sweep.split(",") if x.strip()] or [1, cfg.anim_workers]
    stages = ["decode", "key", "bounds", "render", "seam", "encode", "probe", "verify"]
    for n, w in enumerate(dict.fromkeys(counts)):
        t0 = time.perf_counter()
        results = sorted(pl.animate_cells(res, replace(cfg, anim_workers=w), cells), key=lambda r: r.index)
        wall = time.perf_counter() - t0
        if n == 0:
            print("cell " + "".join(f"{x:>8}" for x in stages) + "   encodes   total  result")
            tot = dict.fromkeys(stages, 0)
            for r in results:
                ms = r.metrics.get("ms", {})
                for x in stages:
                    tot[x] += ms.get(x, 0)
                print(f"S{r.index:<3} " + "".join(f"{ms.get(x, 0):>8}" for x in stages) + f"   {ms.get('encodes', 0):>7}  {sum(ms.get(x, 0) for x in stages):>6}  {r.status}"
                      + (f" {r.reason}" if r.reason else "") + (" (out of bounds)" if (r.report.get('inside_frame') and not r.report.get('inside_frame').ok) else ""))
            grand = sum(tot.values()) or 1
            print("sum  " + "".join(f"{tot[x]:>8}" for x in stages) + f"   {'':>7}  {grand:>6}   ms of work in total")
            print("share" + "".join(f"{100 * tot[x] / grand:>7.0f}%" for x in stages) + "\n")
            print(f"workers  wall time   (work / wall = how many cores were really busy)")
        print(f"{w:>7}  {wall:>7.1f} s   {sum(sum(r.metrics.get('ms', {}).get(x, 0) for x in stages) for r in results) / 1000 / wall:>5.1f}x")
    return 0


def _parse_since(s: str) -> float:
    """`--since 24h|30m|7d|<seconds>` to a freshness window in seconds (default 24h)."""
    s = (s or "").strip().lower()
    try:
        if s.endswith("h"):
            return max(0.0, float(s[:-1]) * 3600)
        if s.endswith("m"):
            return max(0.0, float(s[:-1]) * 60)
        if s.endswith("d"):
            return max(0.0, float(s[:-1]) * 86400)
        return max(0.0, float(s))
    except ValueError:
        return 24 * 3600


def doctor(refresh_models: bool = False, models_max_age_s: float = 24 * 3600,
           since_label: str = "24h") -> int:
    """Offline-friendly self check: what is installed, what is missing, and how to fix it without internet."""
    import importlib, subprocess
    bad = 0
    for mod, pkg in (("numpy", "numpy"), ("cv2", "opencv-python-headless"), ("PIL", "pillow")):
        try:
            m = importlib.import_module(mod); print(f"OK      {pkg} {getattr(m, '__version__', '')}")
        except ImportError:
            bad += 1; print(f"MISSING {pkg}   (offline: pip install --no-index --find-links wheels {pkg})")
    try:
        from .engine import ffmpeg as ff
        exe = ff.ffmpeg_exe()
        enc = subprocess.run([exe, "-hide_banner", "-encoders"], capture_output=True).stdout.decode()
        print(f"OK      ffmpeg {exe}")
        if "libvpx-vp9" not in enc:
            bad += 1; print("MISSING libvpx-vp9 in any ffmpeg found (needed for WEBM alpha). Fix, easiest first: `pip install imageio-ffmpeg` (its bundled build has it; offline: pip download it on a connected PC), or a full build from gyan.dev and set STICKERLOOM_FFMPEG=<path to ffmpeg.exe>")
    except Exception as e:
        bad += 1; print("MISSING ffmpeg:", e, "\n        put ffmpeg.exe on PATH, or set STICKERLOOM_FFMPEG=<path>")
    from .generation import prompter
    from .engine import verify
    tpl = sorted(f.stem for f in prompter.TEMPLATES.glob("*.txt"))
    print(f"OK      verifier v{verify.VERIFY_VERSION}: {sum(len(v) for v in verify.CATALOGUE.values())} checks over {len(verify.CATALOGUE)} stages; prompt templates: {', '.join(tpl)}")
    print("OK      slow test tier: retired: never run or required; `stickerloom test slow` exists only for an explicit request")
    from .services import llm
    print("OK      AI expansion: " + (f"on, model {llm.model()}" if llm.configured() else f"off: add {llm.KEY_VAR} to .env to let the AI expand a subject and name every sticker (the built-in sets are used meanwhile)"))
    from . import providers as _providers
    from .providers.base import ProviderError as _ProviderError
    _p = _providers.current()
    if _p.name == "fake":
        print("OK      provider: fake (deterministic offline renders, no account, no credits)")
    elif not _p.available():
        print(f"NOTE    provider {_p.name}: not available ({_p.setup_hint}): live generation is off, prepared sheets still work")
    elif _p.name == "higgsfield":
        try:
            _a = _p.account()
            print(f"OK      Higgsfield: {_a['credits']:g} credits, plan {_a['plan'] or '?'} (live generation on; Nano Banana 2 at 2k and Kling v3.0 by default, Kling 4k is never used)")
        except _ProviderError as e:
            print(f"NOTE    Higgsfield: installed but not usable ({str(e)[:140]}): run higgsfield auth login")
    else:
        print(f"OK      provider: {_p.name} (experimental, billed to your own key)")
    import os
    print(f"OK      animation workers: {EngineConfig().anim_workers} of {os.cpu_count()} CPUs (STICKERLOOM_ANIM_WORKERS or serve --workers N changes it)")
    try:                                                  # the HTTP server: FastAPI on uvicorn (console/app.py); `serve --stdlib` keeps the old one
        import fastapi, pydantic, uvicorn
        print(f"OK      web: FastAPI {fastapi.__version__}, pydantic {pydantic.__version__}, uvicorn {uvicorn.__version__}"
              + ("  (STICKERLOOM_SERVER=stdlib: the old server is in use)" if os.environ.get("STICKERLOOM_SERVER", "").lower() == "stdlib" else ""))
    except ImportError as e:
        bad += 1
        print(f"MISSING web: {e.name} (pip install -r requirements.txt); `serve --stdlib` still runs the old server")
    try:
        from .store import db as _db
        if _db.available():
            print(f"OK      Postgres: reachable ({_db.url().split('@')[-1]}); write-through "
                  + ("on" if os.environ.get("STICKERLOOM_DB_WRITE", "") not in ("0", "no", "off", "false") and not os.environ.get("STICKERLOOM_OUT") else "off (STICKERLOOM_OUT copy or STICKERLOOM_DB_WRITE=0: use `db import`)"))
            st = _db.status()
            if st["pending"]:
                print(f"WARN    Postgres: {len(st['pending'])} migration file(s) not applied ({', '.join(st['pending'])}): python -m stickerloom db migrate (and `db check` compares the whole schema)")
            elif st["unknown"]:
                print(f"NOTE    Postgres: recorded as applied but not in this checkout: {', '.join(st['unknown'])}")
        else:
            print(f"NOTE    Postgres: not connected ({_db.url().split('@')[-1]}; start it: python -m stickerloom db up)")
    except Exception as e:
        print(f"NOTE    Postgres: store unavailable ({e}; pip install -r requirements.txt)")
    try:
        from .store import sync as _sync
        ws = _sync.status()
        if ws["failed"]:
            print(f"NOTE    Postgres write-through: {ws['failed']} failed, {ws['ok']} ok in this process; last error: {ws['last_error']}")
    except Exception:
        pass
    try:
        from .runtime import cache as _cache
        _c = _cache.default()
        print(f"OK      Redis: {_c.engine}" + (f" ({_c.url})" if _c.engine == 'redis' else " (not reachable: the in-memory fallback is used; start it: python -m stickerloom db up)"))
    except Exception as e:
        print(f"NOTE    Redis: {e}")
    try:
        from .generation import jobqueue as _jq
        print(f"OK      job queue: {_jq.mode(out_root())}" + ("" if _jq.mode(out_root()) == "queue" else "  (jobs run in threads of the server; STICKERLOOM_JOB_MODE=queue + `python -m stickerloom worker` for durable jobs)"))
    except Exception as e:
        print(f"NOTE    job queue: {e}")
    try:
        from .services import llm as _llm
        from .vision import judge as _vj
        ls = _llm.status()
        print(f"OK      language model: {ls['provider']} {ls['model'] or ''}; local server {ls['local']['url']} " + ("reachable" if ls['local']['reachable'] else "not reachable")
              + f"; vision judge: {_vj.status()['provider']} {_vj.status()['model']} ({_vj.status()['policy']})")
    except Exception as e:
        print(f"NOTE    language model: {e}")
    try:
        from .services import llm as _llm
        ids = _llm.list_local_models(force=True)
        rd = _llm.local_ready(force=True)                       # one tiny chat completion: the list answers while no model can
        wish = _llm.configured_local_model()
        if rd["ok"]:
            via = "" if rd["model"] == wish else f" (configured {wish} is not what the server calls it, so {rd['model']} is used)"
            print(f"OK      local model: {rd['model']} answers; the server lists {len(ids)} chat model(s){via}")
        else:
            print(f"NOTE    local model {rd['model']}: {rd['why']}; the server lists: {', '.join(ids) or 'nothing'}; the chat runs on "
                  + ("the cloud model or its rules" if os.environ.get(_llm.KEY_VAR) else "its rules"))
    except Exception as e:
        print(f"NOTE    local model: {e}")
    try:                                                  # automatic failures are recorded in out/problems.jsonl (flow/problems.py)
        from .flow import problems as _pb
        n = len(_pb.listing(out_root()))
        print(f"OK      problems: {n} recorded in out/problems.jsonl" if n else "OK      problems: none recorded")
    except Exception as e:
        print(f"NOTE    problems: {e}")
    from .services import telegram
    try:
        import ssl
        ssl.create_default_context(); print("OK      TLS: system certificate store loads")
    except ssl.SSLError as e:
        telegram._ssl_context(); print(f"NOTE    TLS: this PC's certificate store has malformed entries ({e.reason}); stickerloom skips them, Telegram still works")
    t = telegram.status(out_root())
    print(f"OK      Telegram: connected as @{t['bot']} (user {t['user_id']})" if t["configured"] else "NOTE    Telegram: not connected (optional: Settings -> Telegram, or STICKERLOOM_TELEGRAM_TOKEN and STICKERLOOM_TELEGRAM_USER)")
    from .flow import sources
    subs = sources.known_subjects(input_root())
    from .media import matte
    ms, mf = matte.status(), matte.status(True)
    print(f"OK      AI matte {ms['model']} (photos), {mf['model']} (video)" if ms["ok"] else f"NOTE    AI matte off, photos use GrabCut (optional): {ms['reason']}")
    print(("OK      " if subs else "NOTE    ") + f"prepared sheets in {input_root()}  subjects: {', '.join(subs) or 'none (optional: live generation and Create work without them)'}")
    for subject, picks in sources.scan(input_root()).items():
        clips = sum(1 for p in picks if p.clips)
        print(f"        {subject}: {len(picks)} variant(s), {sum(1 for p in picks if p.video)} with a 3x3 video, {clips} with pre-sliced clips")
        for p in picks:
            if p.clips_dup_of:
                print(f"WARN    {subject} {p.subject_id}: pre-sliced clips are copies of {p.clips_dup_of}'s; ignored, its own 3x3 video is used"
                      + ("" if p.video else " (none: no animation)"))
        if any(p.pairing == "order" for p in picks):
            print(f"WARN    {subject}: image/video pairing is GUESSED by order (folder numbers or take numbers don't match).")
    try:
        from .flow import pipeline as _pl
        orphans = _pl.orphan_batches(out_root())
        empty = sum(1 for r in orphans if not r["files"])
        if not orphans:
            print("OK      batch folders: every G### has its result.json")
        else:
            print(f"NOTE    batch folders: {len(orphans)} G### folder(s) with no result.json ({empty} empty): `python -m stickerloom prune-orphans --apply` deletes the empty ones")
    except Exception as e:
        print(f"NOTE    batch folders: {e}")
    try:                                                  # 4. models available: the cached dump, or the curated list
        from .generation import higgsfield as _hf
        from .generation import model_catalog as _catalog
        import datetime as _dt
        d = _hf.load_models(out_root(), refresh=refresh_models, max_age_s=models_max_age_s)
        if d and sum(int(d.get("counts", {}).get(k, 0)) for k in _hf.TYPES):
            stamp = _dt.datetime.fromtimestamp(d.get("fetched_at") or 0).strftime("%Y-%m-%d %H:%M")
            counts = ", ".join(f"{k} {d['counts'].get(k, 0)}" for k in _hf.TYPES)
            print(f"OK      models available: {counts} (list from {stamp}; `doctor --models` refreshes, `--since {since_label}` is the freshness window)")
        else:
            n, m = len(_catalog.IMAGE), len(_catalog.VIDEO)
            now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
            print(f"NOTE    models available: using the curated list ({n} image, {m} video; defaults {_catalog.DEFAULT_IMAGE}/{_catalog.DEFAULT_VIDEO}) as of {now} (no usable cached list and the fetch is unreachable; `doctor --models` retries it)")
    except Exception as e:
        from .generation import model_catalog as _catalog
        import datetime as _dt
        n, m = len(_catalog.IMAGE), len(_catalog.VIDEO)
        now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        print(f"NOTE    models available: using the curated list ({n} image, {m} video; defaults {_catalog.DEFAULT_IMAGE}/{_catalog.DEFAULT_VIDEO}) as of {now} (the fetch is unreachable: {str(e)[:120]}; `doctor --models` retries it)")
    try:                                                  # 5. export/import smoke: dry run only, never a pack
        from .media import pack_exchange as _px
        missing = [n for n in ("export_pack", "import_pack", "verify_import") if not callable(getattr(_px, n, None))]
        if missing:
            print(f"NOTE    export/import smoke: interface incomplete (missing {', '.join(missing)}); the round trip lives in tests/test_export_import.py")
        else:
            print("OK      export/import smoke: export_pack/import_pack/verify_import present (dry run only; the round trip is tests/test_export_import.py)")
    except ImportError:
        print("NOTE    export/import smoke: skipped — pack_exchange is not available in this tree")
    print("ready" if not bad else f"{bad} problem(s)")
    return 1 if bad else 0
