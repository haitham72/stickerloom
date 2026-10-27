"""Phase 3B pool, lexical pass: parse, index, search-first, hide, gap math. Own data (G101/G102)
so it is independent of test order; skipped when stickerloom-db is not running."""
import json
import os
import tempfile
import unittest
from pathlib import Path

TEST_URL = "postgresql://stickerloom:stickerloom_local@localhost:5434/stickerloom_test"

os.environ["STICKERLOOM_DATABASE_URL"] = TEST_URL


def _result(gid, keys):
    stickers = [{"index": i + 1, "key": k, "name": f"img-{gid:03d}-poolxyz-{k}",
                 "prompt": f"a poolxyz {k.replace('poolxyz_', '').replace('_', ' ')}",
                 "emoji": "\U0001F600", "status": "READY", "reason": None, "report": [], "metrics": {},
                 "png": None, "webm": None, "anim_status": "NOT_REQUESTED", "anim_reason": None, "anim_metrics": {},
                 "tags": [k], "review": {"still": "APPROVED", "anim": "NONE"},
                 "history": [{"ts": 1700000000.0, "stage": "sliced", "actor": "python",
                              "decision": "PASS", "reason": None, "ref": None, "detail": {}}]}
                for i, k in enumerate(keys)]
    return {"generation_id": f"G{gid:03d}", "number": gid, "parent": None, "prompt": "poolxyz",
            "task": "poolxyz", "task_slug": "poolxyz", "stage": "sliced", "grid": [3, 3],
            "source": {"subject": "poolxyz", "subject_id": "100", "variant": 1},
            "stickers": stickers, "verify_version": "2", "reviews": {}, "video_sheets": []}


class PoolTests(unittest.TestCase):
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
                cur.execute("SELECT 1 FROM pg_database WHERE datname = 'stickerloom_test'")
                if not cur.fetchone():
                    cur.execute("CREATE DATABASE stickerloom_test")
        from stickerloom.store import db, repo
        db.reset_cache()
        db.migrate()
        td = Path(tempfile.mkdtemp())
        for gid, keys in ((101, ["poolxyz_dancing", "poolxyz_waving", "poolxyz_sleeping"]),
                          (102, ["poolxyz_dancing_hard"])):
            gd = td / f"G{gid:03d}"
            gd.mkdir(parents=True)
            (gd / "result.json").write_text(json.dumps(_result(gid, keys)), encoding="utf-8")
            (gd / "prompts.json").write_text("{}", encoding="utf-8")
            (gd / "events.jsonl").write_text("", encoding="utf-8")
        with db.connect() as c:
            repo.save_generation(c, td, 101)
            repo.save_generation(c, td, 102)

    def _conn(self):
        from stickerloom.store import db
        return db.connect()

    def test_parse(self):
        from stickerloom.store import pool
        self.assertEqual(pool.parse_query("falcon dancing"), {"subject": "falcon", "action": "dancing", "topics": []})
        self.assertEqual(pool.parse_query("falcon doing a flip")["action"], "a flip")
        self.assertEqual(pool.parse_query("dog as banana")["subject"], "dog as banana")  # 4B refines this
        self.assertEqual(pool.parse_query("sakr yarkos")["action"], "")

    def test_search_first_reuses(self):
        from stickerloom.store import pool
        with self._conn() as c:
            pool.reindex(c)
            c.execute("UPDATE sticker_index SET hidden = false WHERE sticker_id = 'G101/S1'")
            c.commit()
            res = pool.search(c, "poolxyz dancing", count=9, vectors=False)
        self.assertEqual(res["parsed"]["action"], "dancing")
        self.assertTrue(any(h["sticker_id"] == "G101/S1" for h in res["hits"]))
        self.assertEqual((res["found"], res["missing"]), (len(res["hits"]), 9 - len(res["hits"])))

    def test_diversity_and_zero_rule(self):
        from stickerloom.store import pool
        with self._conn() as c:
            pool.reindex(c)
            res = pool.search(c, "poolxyz", count=9, vectors=False)
            per_gen = {}
            for h in res["hits"]:
                if "poolxyz" in h["key"]:
                    per_gen[h["generation_id"]] = per_gen.get(h["generation_id"], 0) + 1
            self.assertTrue(all(v <= 2 for v in per_gen.values()))
            self.assertEqual(pool.search(c, "penguin skiing", vectors=False)["hits"], [])

    def test_hide_removes_without_deleting(self):
        from stickerloom.store import pool
        from stickerloom.store import repo
        with self._conn() as c:
            pool.reindex(c)
            self.assertTrue(pool.hide(c, "G101/S1"))
            res = pool.search(c, "poolxyz dancing", count=9, vectors=False)
            self.assertFalse(any(h["sticker_id"] == "G101/S1" for h in res["hits"]))
            self.assertIsNotNone(repo.get_generation(c, "G101"))  # the sticker still exists


    # ---- the vector pass (a fake bag-of-words embedder: no model, no network) -------------------------------------------------------------
    @staticmethod
    def _fake_embedder():
        import hashlib
        import math
        import re

        def embedder(texts, kind="document"):
            out = []
            for t in texts:
                v = [0.0] * 768
                for w in re.findall(r"[a-z0-9]+", t.lower()):
                    v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 768] += 1.0
                n = math.sqrt(sum(x * x for x in v)) or 1.0
                out.append([x / n for x in v])
            return out
        embedder.model = "fake-bow"
        return embedder

    def test_vectors_fill_once_and_status_counts_them(self):
        from stickerloom.store import pool
        emb = self._fake_embedder()
        with self._conn() as c:
            c.execute("UPDATE sticker_index SET subject_vec = NULL, action_vec = NULL")
            c.commit()
            pool.reindex(c)
            before = pool.status(c)
            self.assertGreater(before["missing_vectors"], 0)
            self.assertEqual(pool.embed_missing(c, emb), before["indexed"])
            after = pool.status(c)
            self.assertEqual((after["missing_vectors"], after["with_vectors"]), (0, before["indexed"]))
            self.assertEqual(pool.embed_missing(c, emb), 0)                      # idempotent: nothing left to embed
            c.execute("UPDATE sticker_index SET subject_vec = NULL, action_vec = NULL")
            c.commit()

    def test_vector_search_ranks_gates_and_never_returns_junk(self):
        from stickerloom.store import pool
        emb = self._fake_embedder()
        with self._conn() as c:
            pool.reindex(c)
            pool.embed_missing(c, emb)
            try:
                res = pool.search(c, "poolxyz dancing", count=9, embedder=emb)
                self.assertEqual(res["mode"], "vector")
                ids = [h["sticker_id"] for h in res["hits"]]
                self.assertEqual(ids[0], "G101/S1")                              # exact subject + exact action first
                self.assertIn("G102/S1", ids)                                    # "dancing hard" is close enough to "dancing"
                self.assertNotIn("G101/S2", ids)                                 # "waving" is not dancing: the action gate drops it
                self.assertTrue(all(h["cos_subject"] >= pool.SUBJECT_MIN for h in res["hits"]))
                self.assertEqual(pool.search(c, "penguin skiing", embedder=emb)["hits"], [])        # nothing, never the closest junk
                self.assertEqual(pool.search(c, "poolxyz", count=9, embedder=emb, per_gen=9)["found"], 4)   # a whole pack when asked
                self.assertEqual(pool.search(c, "poolxyz", count=9, embedder=emb)["found"], 3)             # the default diversity cap (2 per generation)
            finally:
                c.execute("UPDATE sticker_index SET subject_vec = NULL, action_vec = NULL")
                c.commit()

    def test_a_row_without_vectors_is_still_found_lexically(self):
        from stickerloom.store import pool
        emb = self._fake_embedder()
        with self._conn() as c:
            pool.reindex(c)
            c.execute("UPDATE sticker_index SET subject_vec = NULL, action_vec = NULL")
            c.commit()
            pool.embed_missing(c, emb, limit=2)                                  # only two rows have vectors
            try:
                res = pool.search(c, "poolxyz dancing", count=9, embedder=emb)
                self.assertEqual(res["mode"], "hybrid")
                self.assertTrue(any(h["sticker_id"] == "G101/S1" for h in res["hits"]))
            finally:
                c.execute("UPDATE sticker_index SET subject_vec = NULL, action_vec = NULL")
                c.commit()

    def test_lexical_words_do_not_match_inside_other_words(self):
        """The first real search on real data returned old-man stickers for "batman": trigram overlap of "man"."""
        from stickerloom.store import pool
        self.assertLess(pool._close("batman", "man"), 0.75)
        self.assertFalse(pool._cover(["batman"], ["old", "man", "sleepy"])[0])
        self.assertTrue(pool._cover(["tedy", "bok"], ["teddy", "bear", "book"])[0])          # typos still match
        self.assertTrue(pool._cover(["danc"], ["dance"])[0])


if __name__ == "__main__":
    unittest.main()
