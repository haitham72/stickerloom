import tempfile
import unittest
from pathlib import Path

from stickerloom.store.assets import AssetError, LocalAssetStore


class AssetStoreTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.s = LocalAssetStore(Path(self.td.name), secret=b"k" * 32)

    def tearDown(self):
        self.td.cleanup()

    def test_keys_cannot_leave_the_root(self):
        for bad in ("../x", "G001/../../x", "/etc/passwd", "C:/Windows/x", "", "G001//x", "G001/./x"):
            with self.assertRaises(AssetError, msg=bad):
                self.s.path(bad)
        self.assertFalse(self.s.exists("../x"))

    def test_put_is_append_only(self):
        r = self.s.put("G001/slices/a.png", b"abc")
        self.assertEqual((r["object_key"], r["bytes"]), ("G001/slices/a.png", 3))
        self.assertEqual(self.s.put("G001/slices/a.png", b"abc")["sha256"], r["sha256"])   # same bytes: idempotent
        with self.assertRaises(AssetError) as cm:
            self.s.put("G001/slices/a.png", b"different")
        self.assertEqual(cm.exception.code, 409)
        self.assertEqual(self.s.read("G001/slices/a.png"), b"abc")
        self.assertEqual(self.s.sha256("G001/slices/a.png"), r["sha256"])

    def test_missing_is_404(self):
        with self.assertRaises(AssetError) as cm:
            self.s.read("G001/nope.png")
        self.assertEqual(cm.exception.code, 404)

    def test_signed_url_roundtrip_expiry_and_tamper(self):
        self.s.put("G001/a.png", b"x")
        tok = self.s.sign("G001/a.png", ttl=60, user="u1", now=1000)
        self.assertEqual(self.s.verify(tok, user="u1", now=1030), "G001/a.png")
        with self.assertRaises(AssetError):                       # expired
            self.s.verify(tok, now=1061)
        with self.assertRaises(AssetError):                       # another user
            self.s.verify(tok, user="u2", now=1030)
        body, mac = tok.split(".")
        with self.assertRaises(AssetError):                       # edited body keeps the old signature
            self.s.verify(body[:-2] + "AA." + mac, now=1030)
        with self.assertRaises(AssetError):                       # another store's secret
            LocalAssetStore(Path(self.td.name), secret=b"z" * 32).verify(tok, now=1030)
        self.assertTrue(self.s.url("G001/a.png").startswith("/api/assets/"))


if __name__ == "__main__":
    unittest.main()
