"""One writer of result.json per out/ folder, across processes.

result.json is read-modify-write under an in-process lock only (pipeline._IO_LOCK). A second process
that writes generations (`stickerloom recheck`, `create`, `more`, `animate`) while `stickerloom serve` runs could
interleave a read and a write and lose a decision. So the server, and every CLI command that writes
results, holds this advisory lock for its lifetime: the second one refuses with the name of the first.
Read-only commands (list, show, search, doctor, db, jobs, ...) never take it.

The lock is a byte far beyond the file's text (so the holder note stays readable while it is held),
taken with msvcrt on Windows and fcntl elsewhere, and the OS drops it when the process dies."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_OFFSET = 1 << 20


class WriterBusy(Exception):
    pass


class WriterLock:
    def __init__(self, out: Path, who: str = "stickerloom", name: str = ".writer.lock"):
        self.path = Path(out) / name
        self.who = who
        self._fh = None

    def _try(self, fh) -> bool:
        fh.seek(_OFFSET)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def holder(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8").strip() or "another stickerloom process"
        except OSError:
            return "another stickerloom process"

    def acquire(self, wait: bool = False) -> "WriterLock":
        """Take the lock or raise WriterBusy; with `wait` block (polling) until the holder lets go (the paid-call lock of generation/jobs.py waits for the other worker's job)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")
        while not self._try(fh):
            if not wait:
                fh.close()
                raise WriterBusy(f"{self.holder()} is writing {self.path.parent}: stop it first, or use it instead "
                                 f"(two writers of result.json can lose a decision)")
            time.sleep(0.5)
        fh.seek(0)
        fh.truncate(0)
        fh.write(f"{self.who} (pid {os.getpid()}, since {time.strftime('%H:%M:%S')})".encode())
        fh.flush()
        self._fh = fh
        return self

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            fh.seek(_OFFSET)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()

    def __enter__(self) -> "WriterLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()
