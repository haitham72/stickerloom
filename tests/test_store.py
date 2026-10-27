"""Phase 3A: Postgres round trip, idempotent import, history, search, engine boundary, trace seam.
Uses a throwaway database (stickerloom_test) on the same container; skipped when it is not running."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

TEST_URL = "postgresql://stickerloom:stickerloom_local@localhost:5434/stickerloom_test"

os.environ["STICKERLOOM_DATABASE_URL"] = TEST_URL


def _result(gid, parent=None, full=True):
    stickers = []
    for i in range(1, 10):
        s = {"index": i, "key": f"tedy_book_{i}" if i == 1 else f"sticker_{i}",
             "name": f"img-{gid:03d}-tedy-tedy_book_{i}" if i == 1 else f"img-{gid:03d}-tedy-sticker_{i}",
             "prompt": "a teddy bear holding a book" if i == 1 else f"a teddy bear pose {i}",
             "emoji": "\U0001F4DA" if i == 1 else "\U0001F600",
             "status": "READY" if i != 4 else "FAILED", "reason": None if i != 4 else "empty_subject",
             "report": [], "metrics": {}, "png": None, "webm": None,
             "anim_status": "NOT_REQUESTED", "anim_reason": None, "anim_metrics": {}}
        if full:
            s["tags"] = [s["key"], "book"] if i == 1 else [s["key"]]
            s["review"] = {"still": "APPROVED", "anim": "NONE"}
            s["history"] = [
                {"ts": 1700000000.0 + i, "stage": "sliced", "actor": "python",
                 "decision": "PASS", "reason": None, "ref": None, "detail": {}},
                {"ts": 1700000100.0 + i, "stage": "still", "actor": "human",
                 "decision": "APPROVE", "reason": "good", "ref": None, "detail": None}]
        stickers.append(s)
    res = {"generation_id": f"G{gid:03d}", "number": gid, "parent": parent, "prompt": "tedy with a book",
           "task": "tedy with a book", "task_slug": "tedy", "stage": "sliced",
           "source": {"subject": "tedy", "subject_id": "099", "variant": 1},
           "stickers": stickers}
    if full:
        res.update({"grid": [3, 3], "verify_version": "2", "template_id": "sheet_3x3", "template_version": 1,
                    "slots": {"cells": []}, "outline_px": 12, "erode_px": 0, "name_key": "tedy",
                    "reviews": {"plan": {"decision": "APPROVE", "by": "human", "ts": 1700000000.0, "note": "go"}},
                    "video_sheets": [], "verify": {}})
    return res


def _write_gen(td, gid, res):
    gd = Path(td) / f"G{gid:03d}"
    gd.mkdir(parents=True, exist_ok=True)
    (gd / "result.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    (gd / "prompts.json").write_text(json.dumps({"task_slug": res["task_slug"]}), encoding="utf-8")
    (gd / "events.jsonl").write_text(
        json.dumps({"ts": 1700000000.0, "stage": "requested", "status": "done", "ms": 1, "detail": {}}) + "\n",
        encoding="utf-8")


class StoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        base = TEST_URL.rsplit("/", 1)[0] + "/stickerloom"
        try:
            boot = psycopg.connect(base, connect_timeout=5, autocommit=True)
        except Exception:
            raise unittest.SkipTest("stickerloom-db is not running (python -m stickerloom db up)")
        with boot:
            with boot.cursor() as cur:
                cur.execute("DROP DATABASE IF EXISTS stickerloom_test")
                cur.execute("CREATE DATABASE stickerloom_test")
        from stickerloom.store import db
        db.reset_cache()
        db.migrate()
        cls.tmp = Path(tempfile.mkdtemp())
        _write_gen(cls.tmp, 1, _result(1, full=False))   # pre-1F shape: no tags/history
        _write_gen(cls.tmp, 2, _result(2, parent=1))     # int parent -> G001
        with db.connect() as c:
            from stickerloom.store import repo
            repo.save_generation(c, cls.tmp, 1)
            repo.save_generation(c, cls.tmp, 2)

    def _conn(self):
        from stickerloom.store import db
        return db.connect()

    def test_round_trip(self):
        from stickerloom.store import repo
        with self._conn() as c:
            g = repo.get_generation(c, "G002")
        self.assertEqual((g["task_slug"], len(g["stickers"]), g["parent_id"]), ("tedy", 9, "G001"))
        s1 = next(s for s in g["stickers"] if s["idx"] == 1)
        self.assertEqual((s1["key"], s1["tags"][0], s1["emoji"]), ("tedy_book_1", "tedy_book_1", ["\U0001F4DA"]))
        self.assertEqual(s1["still_review"], "APPROVED")

    def test_pre_1f_import(self):
        """Results written before 1F have no tags/history: tags = [key], python verdicts from status, gates PENDING."""
        from stickerloom.store import repo
        with self._conn() as c:
            g = repo.get_generation(c, "G001")
            h = repo.history(c, "G001/S1")
        s1 = next(s for s in g["stickers"] if s["idx"] == 1)
        self.assertEqual(s1["tags"], ["tedy_book_1"])
        self.assertEqual(s1["still_review"], "PENDING")
        self.assertEqual(h, [])

    def test_import_is_idempotent(self):
        from stickerloom.store import repo
        from stickerloom.store import db
        with db.connect() as c:
            before = {t: c.execute(f"select count(*) from {t}").fetchone()[0]
                      for t in ("generations", "stickers", "reviews", "generation_events", "assets", "tasks")}
            repo.save_generation(c, self.tmp, 2)
            repo.save_generation(c, self.tmp, 2)
            after = {t: c.execute(f"select count(*) from {t}").fetchone()[0]
                     for t in ("generations", "stickers", "reviews", "generation_events", "assets", "tasks")}
        self.assertEqual(before, after)

    def test_history_order(self):
        from stickerloom.store import repo
        with self._conn() as c:
            h = repo.history(c, "G002/S1")
        self.assertEqual([(r["gate"], r["decision"], r["actor"]) for r in h],
                         [("still", "PASS", "python"), ("still", "APPROVE", "human")])

    def test_search_and_trigram(self):
        from stickerloom.store import repo
        with self._conn() as c:
            self.assertTrue(any(r["id"] == "G002/S1" for r in repo.search(c, "tedy book")))
            self.assertTrue(any(r["id"] == "G002/S1" for r in repo.search(c, "tedy bok")))  # typo fallback
            self.assertEqual(repo.search(c, "penguin skiing"), [])                          # nothing -> nothing
            self.assertEqual(repo.search(c, "tedy", approved=True)[0]["id"], "G002/S1")

    def _phase2_out(self):
        """An out/ with a manual task whose ticket a Higgsfield job claimed, a not-yet-claimed job, and a ledger."""
        td = Path(tempfile.mkdtemp())
        (td / "tasks").mkdir()
        (td / "jobs").mkdir()
        (td / "tasks" / "007.json").write_text(json.dumps(
            {"id": "007", "number": 7, "provider": "higgsfield-manual", "external_task_id": "hf-job-777",
             "name_key": "zzz_trip", "status": "running", "request": {"grid": [3, 3]}}), encoding="utf-8")
        job = {"id": "J001", "kind": "sheet", "task": "7", "generation": "G002", "provider": "higgsfield-cli",
               "model": "nano_banana_2", "request": {"prompt": "a teddy", "model": "nano_banana_2"},
               "status": "DONE", "external_task_id": "hf-job-777", "result": {"file": "jobs/J001/result.png"},
               "cost": 12.5, "error": None, "created_at": 1700000000.0, "claimed_at": 1700000010.0,
               "completed_at": 1700000090.0}
        (td / "jobs" / "J001.json").write_text(json.dumps(job), encoding="utf-8")
        waiting = dict(job, id="J002", status="REQUESTED", external_task_id=None, result=None, cost=None)
        (td / "jobs" / "J002.json").write_text(json.dumps(waiting), encoding="utf-8")
        calls = [{"ts": 1700000090.0, "kind": "IMAGE_SHEET", "provider": "higgsfield-cli", "model": "nano_banana_2",
                  "status": "OK", "attempt": 1, "latency_ms": 80000, "cost": 12.5, "seed": None,
                  "generation_id": None, "job": "J001", "output": "jobs/J001/result.png"},
                 {"ts": 1700000100.0, "kind": "LLM_PLAN", "provider": "openai", "model": "gpt-4.1-mini",
                  "status": "OK", "attempt": 1, "latency_ms": 900, "tokens_in": 300, "tokens_out": 200, "task": "teddy"}]
        (td / "model_calls.jsonl").write_text("\n".join(json.dumps(c) for c in calls) + "\n", encoding="utf-8")
        return td

    def test_phase2_jobs_update_their_task_row_and_never_duplicate(self):
        from stickerloom.store import repo
        td = self._phase2_out()
        with self._conn() as c:
            repo.import_tasks(c, td)
            self.assertEqual(repo.import_jobs(c, td), 1)          # the REQUESTED job has no ticket: not a task yet
            self.assertEqual(repo.import_jobs(c, td), 1)
            repo.import_tasks(c, td)                              # re-import of the task file after the job took it
            rows = repo.find_task(c, external_id="hf-job-777")
        self.assertEqual(len(rows), 1)
        t = rows[0]
        self.assertEqual((t["status"], t["job_id"], t["model"], float(t["cost_credits"]), t["generation_id"], t["kind"]),
                         ("DONE", "J001", "nano_banana_2", 12.5, "G002", "sheet"))
        self.assertEqual(t["name_key"], "zzz_trip")
        self.assertEqual(t["result_ref"], {"file": "jobs/J001/result.png"})

    def test_model_calls_import_once_by_line(self):
        from stickerloom.store import repo
        td = self._phase2_out()
        with self._conn() as c:
            c.execute("delete from model_calls")
            c.commit()
            self.assertEqual(repo.import_model_calls(c, td), 2)
            self.assertEqual(repo.import_model_calls(c, td), 0)
            rows = c.execute("select kind, model, cost_credits, tokens_in, job_id, extra from model_calls order by ts").fetchall()
        self.assertEqual([r[0] for r in rows], ["IMAGE_SHEET", "LLM_PLAN"])
        self.assertEqual((float(rows[0][2]), rows[0][4], rows[0][5]["output"]), (12.5, "J001", "jobs/J001/result.png"))
        self.assertEqual((rows[1][3], rows[1][5]["task"]), (300, "teddy"))

    def test_a_chat_session_mirrors_with_feedback_and_references_once(self):
        from stickerloom.store import repo
        sess = {"id": "S900", "title": "teddy", "created": 1700000000.0, "updated": 1700000100.0, "settings": {"grid": "3x3"},
                "focus": {"generation": "G002", "stickers": []}, "subjects": [], "preferences": {"persistent": []}, "summary": {},
                "interactions": [{"seq": 1, "ts": 1700000050.0, "user": "make 5 like 2", "assistant": "ok", "intents": ["EDIT_STICKERS"],
                                  "resolved": {"references": [{"source": "G002/S2", "target": "G002/S5", "role": "STYLE"}]}, "generation_id": "G002"}],
                "feedback": [{"ts": 1700000060.0, "polarity": "POSITIVE", "scope": "TEMPORARY", "sticker_ids": ["G002/S2", "G002/S7"], "text": "I like 2 and 7"}]}
        with self._conn() as c:
            repo.save_session(c, sess)
            repo.save_session(c, sess)                                    # idempotent
            n = lambda t: c.execute(f"select count(*) from {t} where session_id = 'S900'").fetchone()[0]
            self.assertEqual((n("interactions"), n("feedback"), n("generation_references")), (1, 2, 1))
            c.execute("delete from sessions where id = 'S900'")
            c.commit()

    def test_task_prefix(self):
        from stickerloom.store import repo
        with self._conn() as c:
            rows = repo.find_task(c, key_prefix="ted")
        self.assertTrue(rows and all(r["provider"] == "prepared" for r in rows))

    def test_tmp_out_never_touches_the_shared_db(self):
        """The golden-path suite serves temp dirs with the DB up: search and write-through must stay on files."""
        from stickerloom.store import sync
        flag = os.environ.pop("STICKERLOOM_DB_WRITE", None)  # the real .env sets =1; it must not leak into the suite
        try:
            self.assertFalse(sync.is_default_out(self.tmp))
            self.assertFalse(sync.enabled(self.tmp))
        finally:
            if flag is not None:
                os.environ["STICKERLOOM_DB_WRITE"] = flag

    def test_engine_boundary(self):
        code = ("import sys, stickerloom.engine.sheet, stickerloom.engine.video;"
                "bad=[m for m in ('fastapi','psycopg','langgraph','anthropic','pydantic','redis') if m in sys.modules];"
                "sys.exit(1 if bad else 0)")
        self.assertEqual(subprocess.run([sys.executable, "-c", code]).returncode, 0)

class ComposeFileTests(unittest.TestCase):
    """`db up` finds the compose file that is tracked in git (needs no database)."""

    def test_compose_path_exists(self):
        from stickerloom.cli import out_root_compose
        self.assertTrue(out_root_compose().is_file(), out_root_compose())


if __name__ == "__main__":
    unittest.main()
