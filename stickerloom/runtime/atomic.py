"""One way to replace a file: write a temp file that is UNIQUE to this write (process, thread, counter) next to it, then `os.replace` it over the target.

The files that matter (result.json, chat sessions, tasks, the library, accounts, assets) used to share one fixed `<name>.tmp`. That is safe for threads of one process (they
hold a lock) but not for two processes: the second writer's `os.replace` could find its temp file already gone (`FileNotFoundError`) or replace the first writer's half-written
one. `writer_lock.py` keeps a second writer out in normal use; this makes the files safe even when something bypasses it. On Windows `os.replace` fails with WinError 5 while
anything (a polling request, an antivirus scan) has the target open, so that is retried for about 2 seconds."""
from __future__ import annotations

import itertools
import os
import threading
import time
from pathlib import Path

_counter = itertools.count()


def tmp_name(path: Path) -> Path:
    return path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.{next(_counter)}.tmp")


def write_bytes(path, data: bytes, retries: int = 40) -> None:
    path = Path(path)
    tmp = tmp_name(path)
    try:
        tmp.write_bytes(data)
        for attempt in range(retries):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == retries - 1:
                    raise
                time.sleep(0.05)
    finally:
        try:
            tmp.unlink()                       # only still there when the replace failed
        except OSError:
            pass


def write_text(path, text: str, retries: int = 40) -> None:
    write_bytes(path, text.encode("utf-8"), retries)


def read_text(path, retries: int = 20) -> str:
    """Read a file another process may be replacing right now: on Windows that is a `PermissionError` for an instant, so retry for about a second before giving up."""
    path = Path(path)
    for attempt in range(retries):
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError:
            if attempt == retries - 1:
                raise
            time.sleep(0.05)
    raise OSError(path)
