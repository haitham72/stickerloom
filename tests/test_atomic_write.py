"""result.json is rewritten after every animated cell while the UI polls it: a write must survive a reader holding the file (WinError 5)."""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stickerloom.flow import pipeline as pl


class AtomicWrite(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.p = self.tmp / "result.json"
        self.p.write_text("{}", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_waits_for_a_foreign_reader(self):
        f = open(self.p, "rb")                      # an antivirus scan or another process: outside our lock
        threading.Timer(0.3, f.close).start()
        t0 = time.time()
        pl._atomic_write(self.p, b'{"a": 1}')
        self.assertEqual(json.loads(self.p.read_text()), {"a": 1})
        self.assertFalse(self.p.with_name("result.json.tmp").exists())
        self.assertLess(time.time() - t0, 5)

    def test_concurrent_readers_and_writer(self):
        stop, errs = threading.Event(), []

        def reader():
            while not stop.is_set():
                try:
                    with pl._IO_LOCK:
                        json.loads(self.p.read_text(encoding="utf-8"))
                except Exception as e:
                    errs.append(e)
                time.sleep(0.001)
        ts = [threading.Thread(target=reader) for _ in range(4)]
        [t.start() for t in ts]
        try:
            for i in range(100):
                pl._atomic_write(self.p, json.dumps({"i": i}).encode())
        finally:
            stop.set()
            [t.join() for t in ts]
        self.assertEqual(errs, [])


class AtomicHelperTests(unittest.TestCase):
    """runtime/atomic.py: a temp file unique to each write, the Windows sharing violation retried, nothing left behind."""

    def setUp(self):
        import tempfile
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_every_write_has_its_own_temp_name_and_leaves_nothing_behind(self):
        from stickerloom.runtime import atomic
        target = self.dir / "a.json"
        names = {atomic.tmp_name(target).name for _ in range(50)}
        self.assertEqual(len(names), 50)
        self.assertTrue(all(n.startswith("a.json.") and n.endswith(".tmp") for n in names))
        atomic.write_text(target, "one")
        atomic.write_bytes(target, b"two")
        self.assertEqual(target.read_bytes(), b"two")
        self.assertEqual([f.name for f in self.dir.iterdir()], ["a.json"])

    def test_a_sharing_violation_is_retried_and_a_stuck_one_is_raised_without_a_leftover(self):
        from unittest import mock
        from stickerloom.runtime import atomic
        target = self.dir / "b.json"
        real = os.replace
        calls = {"n": 0}

        def flaky(a, b):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError("WinError 5")
            return real(a, b)
        with mock.patch("os.replace", flaky), mock.patch("time.sleep", lambda s: None):
            atomic.write_text(target, "ok")
        self.assertEqual((target.read_text(), calls["n"]), ("ok", 3))
        with mock.patch("os.replace", side_effect=PermissionError("stuck")), mock.patch("time.sleep", lambda s: None):
            with self.assertRaises(PermissionError):
                atomic.write_text(target, "never")
        self.assertEqual(target.read_text(), "ok")                                      # the old content is intact
        self.assertEqual([f.name for f in self.dir.iterdir()], ["b.json"])              # and the failed write cleaned its temp file

    def test_read_text_retries_a_file_that_is_being_replaced(self):
        from unittest import mock
        from stickerloom.runtime import atomic
        f = self.dir / "c.json"
        f.write_text("hello", encoding="utf-8")
        real = Path.read_text
        calls = {"n": 0}

        def flaky(self_, *a, **k):
            calls["n"] += 1
            if calls["n"] < 4:
                raise PermissionError("WinError 5")
            return real(self_, *a, **k)
        with mock.patch.object(Path, "read_text", flaky), mock.patch("time.sleep", lambda s: None):
            self.assertEqual(atomic.read_text(f), "hello")
        self.assertEqual(calls["n"], 4)
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("locked")), mock.patch("time.sleep", lambda s: None):
            with self.assertRaises(PermissionError):
                atomic.read_text(f, retries=3)


if __name__ == "__main__":
    unittest.main()
