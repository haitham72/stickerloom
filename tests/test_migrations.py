"""The migration runner reads back what it wrote: which files are applied, which are pending, and whether the live schema still matches what the files declare
(a CHECK constraint or a column default created inside `IF NOT EXISTS` is frozen at its first version, so a later edit of a migration never reached an existing database)."""
import unittest
from pathlib import Path

from stickerloom.store import db


def shape(cols=None, cons=None, idx=None):
    return {"columns": dict(cols or {}), "constraints": dict(cons or {}), "indexes": dict(idx or {})}


class DiffTests(unittest.TestCase):
    """The comparison itself, on plain dicts: no database needed."""

    def test_identical_shapes_have_no_drift(self):
        s = shape({"t.a": ("integer", "NO", None, None)}, {"t.t_a_check": "CHECK ((a > 0))"}, {"t.t_idx": "CREATE INDEX t_idx ON t (a)"})
        self.assertEqual(db.diff_shapes(s, s), [])

    def test_a_changed_check_constraint_is_reported_with_both_definitions(self):
        col = {"reviews.decision": ("text", "NO", None, None)}
        live = shape(col, {"reviews.reviews_decision_check": "CHECK ((decision = ANY (ARRAY['APPROVE'::text, 'REJECT'::text])))"})
        declared = shape(col, {"reviews.reviews_decision_check": "CHECK ((decision = ANY (ARRAY['APPROVE'::text, 'REJECT'::text, 'EDIT'::text])))"})
        out = db.diff_shapes(live, declared)
        self.assertEqual(len(out), 1)
        self.assertIn("reviews.reviews_decision_check", out[0])
        self.assertIn("'EDIT'", out[0])                                                       # what the files declare
        self.assertIn("live:", out[0])
        self.assertIn("declared:", out[0])

    def test_missing_and_extra_columns_constraints_and_indexes(self):
        live = shape({"t.a": ("integer", "NO", None, None), "t.old": ("text", "YES", None, None)}, {}, {"t.gone": "CREATE INDEX gone ON t (old)"})
        declared = shape({"t.a": ("bigint", "NO", None, None), "t.b": ("text", "YES", None, None)}, {"t.t_b_key": "UNIQUE (b)"}, {})
        out = "\n".join(db.diff_shapes(live, declared))
        for needle in ("t.a", "bigint", "t.b is declared but missing", "t.old is in the database but no file declares it", "t.t_b_key", "t.gone"):
            self.assertIn(needle, out)

    def test_schema_names_do_not_count_as_a_difference(self):
        a = db.normalise("CREATE INDEX x ON public.stickers USING gin (search_doc public.gin_trgm_ops)", "public")
        b = db.normalise('CREATE INDEX x ON _stickerloom_check.stickers USING gin (search_doc public.gin_trgm_ops)', "_stickerloom_check")
        self.assertEqual(a, b)


class NotesTests(unittest.TestCase):
    def test_the_vector_migration_says_what_it_cleared(self):
        self.assertEqual(db.vector_notes(120, 120), [])
        self.assertEqual(db.vector_notes(0, 0), [])
        notes = db.vector_notes(120, 0)
        self.assertEqual(len(notes), 1)
        self.assertIn("120", notes[0])
        self.assertIn("pool reindex", notes[0])                                                 # the one command that refills them


@unittest.skipUnless(db.available(), "stickerloom-db is down (python -m stickerloom db up)")
class LiveDatabaseTests(unittest.TestCase):
    def test_status_reads_back_every_file_that_was_applied(self):
        db.migrate()
        s = db.status()
        names = [f.name for f in db.migration_files()]
        self.assertEqual((s["declared"], s["pending"], s["unknown"]), (names, [], []))
        self.assertEqual(s["applied"], names)
        self.assertTrue(s["ok"])

    def test_check_is_clean_on_a_migrated_database_and_leaves_no_trace(self):
        db.migrate()
        r = db.check()
        self.assertEqual(r["drift"], [], "\n".join(r["drift"]))                                 # a real drift of this checkout's database fails here, with the lines that differ
        self.assertTrue(r["ok"])
        with db.connect() as c:
            left = c.execute("select count(*) from information_schema.schemata where schema_name = %s", (db.SCRATCH,)).fetchone()[0]
        self.assertEqual(left, 0)                                                                # the scratch schema lived inside a transaction that was rolled back

    def test_migrate_report_lists_what_it_applied_now_and_is_re_runnable(self):
        db.migrate()
        again = db.migrate_report()
        self.assertEqual(again["notes"], [])
        self.assertEqual(sorted(again["applied_now"]), sorted(f.name for f in db.migration_files()))     # every file runs again: they are all idempotent


if __name__ == "__main__":
    unittest.main()
