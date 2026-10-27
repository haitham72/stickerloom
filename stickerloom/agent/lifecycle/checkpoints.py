"""Process-owned checkpoint connections, shared by agents for one output root."""
import sqlite3
import threading
from pathlib import Path
_SAVERS = {}
_SAVERS_LOCK = threading.Lock()

def acquire(outdir):
    """One SqliteSaver per out/ for the life of the process (the server builds an Agent per message)."""
    key = str(Path(outdir) / "checkpoints.sqlite")
    with _SAVERS_LOCK:
        hit = _SAVERS.get(key)
        if hit is None:
            from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
            from langgraph.checkpoint.sqlite import SqliteSaver
            conn = sqlite3.connect(key, check_same_thread=False, timeout=30)
            saver = SqliteSaver(conn, serde=JsonPlusSerializer(pickle_fallback=True))
            setup = getattr(saver, "setup", None)
            if callable(setup):
                setup()
            hit = _SAVERS[key] = [conn, saver, 1]
        else:
            hit[2] += 1
        return hit[1], key

def release(key):
    with _SAVERS_LOCK:
        hit = _SAVERS.get(key)
        if hit is not None:
            hit[2] -= 1
            if hit[2] > 0:
                hit = None
            else:
                del _SAVERS[key]
    if hit is not None:
        try:
            hit[0].close()
        except Exception:
            pass

def close_all():
    with _SAVERS_LOCK:
        hits = list(_SAVERS.values())
        _SAVERS.clear()
    for hit in hits:
        try:
            hit[0].close()
        except Exception:
            pass
