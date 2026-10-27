"""store/db availability: a cached miss re-probes, so a database started after the server is picked up without a restart."""
import unittest
from unittest.mock import patch

from stickerloom.store import db


class AvailableTests(unittest.TestCase):
    def setUp(self):
        db.reset_cache()

    def tearDown(self):
        db.reset_cache()

    def test_failure_is_cached_then_reprobed(self):
        with patch("stickerloom.store.db.connect", side_effect=OSError("down")) as m:
            self.assertFalse(db.available())
            self.assertFalse(db.available())
            self.assertEqual(m.call_count, 1)
        db._state["at"] = db._state.get("at", 0) - 3600
        with patch("stickerloom.store.db.connect") as m:
            self.assertTrue(db.available())
            self.assertEqual(m.call_count, 1)


if __name__ == "__main__":
    unittest.main()
