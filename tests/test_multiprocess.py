"""Two processes on one `out/` (review: "no test runs two processes against one result.json or one session file"). The design says there is ONE writer (`writer_lock.py`: the
server and the CLI commands that write refuse to run together); these tests pin both halves: the lock really refuses a second process, and, should anything ever bypass it,
a file is still never seen half-written (every save goes through its own temp file and an atomic replace)."""
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

WRITER = r'''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
out, who, n = Path(sys.argv[2]), sys.argv[3], int(sys.argv[4])
from stickerloom.runtime.cache import Cache
from stickerloom.agent.memory import SessionStore
from stickerloom.flow import pipeline as pl
store = SessionStore(out, Cache(force_memory=True))
big = "x" * 150_000
for i in range(n):
    s = store.load("S001")
    s["title"] = f"{who}-{i}"
    s["blob"] = big
    store.save(s)
    r = pl.read_result(out, 1)
    r["note"] = f"{who}-{i}"
    r["blob"] = big
    pl.write_result(out, 1, r)
'''


def make_out(tmp: Path) -> Path:
    from stickerloom.agent.memory import SessionStore
    from stickerloom.runtime.cache import Cache
    out = tmp / "out"
    store = SessionStore(out, Cache(force_memory=True))
    store.create("shared")
    d = out / "G001"
    d.mkdir(parents=True)
    (d / "result.json").write_text(json.dumps({"id": 1, "generation_id": "G001", "prompt": "cat", "stage": "sliced", "error": None, "grid": [3, 3], "stickers": [],
                                               "source": {"subject": "cat", "variant": 1}}), encoding="utf-8")
    return out


class OneFileTwoProcesses(unittest.TestCase):
    def test_a_reader_never_sees_a_half_written_file_while_two_processes_save_the_same_ones(self):
        with tempfile.TemporaryDirectory() as td:
            out = make_out(Path(td))
            procs = [subprocess.Popen([sys.executable, "-c", WRITER, str(ROOT), str(out), who, "40"], stderr=subprocess.PIPE, text=True) for who in ("A", "B")]
            files = [out / "sessions" / "S001.json", out / "G001" / "result.json"]
            torn, reads = [], 0
            deadline = time.time() + 120
            while any(p.poll() is None for p in procs) and time.time() < deadline:
                for f in files:
                    try:
                        raw = f.read_bytes()
                    except OSError:
                        continue                                                    # Windows: the file is being replaced right now; the next read sees the new one
                    reads += 1
                    try:
                        json.loads(raw)
                    except ValueError as e:
                        torn.append((f.name, len(raw), str(e)[:60]))
                time.sleep(0.002)
            errs = [p.communicate()[1] for p in procs]
            self.assertEqual([p.returncode for p in procs], [0, 0], errs)
            self.assertEqual(torn, [])
            self.assertGreater(reads, 20)
            for f in files:
                json.loads(f.read_text(encoding="utf-8"))                                  # and the last write is whole
            self.assertEqual([p.name for p in out.rglob("*.tmp")], [])                       # no temp file is left behind

    def test_the_writer_lock_refuses_a_second_process_on_the_same_out(self):
        from stickerloom.runtime.writer_lock import WriterBusy, WriterLock
        with tempfile.TemporaryDirectory() as td:
            first = WriterLock(Path(td), "the server").acquire()
            code = ("import sys\nsys.path.insert(0, sys.argv[1])\nfrom pathlib import Path\nfrom stickerloom.runtime.writer_lock import WriterBusy, WriterLock\n"
                    "try:\n    WriterLock(Path(sys.argv[2]), 'a CLI command').acquire()\nexcept WriterBusy as e:\n    print('REFUSED', e)\n    sys.exit(7)\n")
            r = subprocess.run([sys.executable, "-c", code, str(ROOT), td], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 7, r.stderr)
            self.assertIn("REFUSED", r.stdout)
            first.release()
            WriterLock(Path(td), "after release").acquire().release()                         # and it is free again once the holder let go


if __name__ == "__main__":
    unittest.main()
