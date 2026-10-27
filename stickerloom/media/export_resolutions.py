"""File-first export snapshots shared by the batch and library exporters.

Only interpretation is frozen. Pack slug, position and date remain contextual.
Metadata edits create another snapshot; restoring metadata restores its snapshot.
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from . import export_names
from ..runtime import atomic
from ..runtime.writer_lock import WriterLock

_LOCK = threading.RLock()
_FIELDS = ("emoji", "emojis", "tag", "tokens", "action", "unresolved",
           "resolution_reason", "candidates", "bank_version")


def describe(out: Path, identity: str, **kwargs) -> dict:
    inputs = {k: kwargs.get(k) for k in ("emoji", "key", "tags", "action", "exclude")}
    fingerprint = hashlib.sha256(json.dumps([identity, inputs], sort_keys=True,
                                           ensure_ascii=False).encode("utf-8")).hexdigest()
    path = Path(out) / "export_resolutions.json"
    with _LOCK:
        lock = WriterLock(Path(out), "export resolutions", ".export-resolutions.lock").acquire(wait=True)
        try:
            ledger = json.loads(atomic.read_text(path)) if path.exists() else {"schema_version": 1, "snapshots": {}}
            saved = ledger["snapshots"].get(fingerprint)
            if saved is None:
                desc = export_names.describe(**kwargs)
                saved = {k: desc[k] for k in _FIELDS}
                ledger["snapshots"][fingerprint] = saved
                atomic.write_text(path, json.dumps(ledger, ensure_ascii=False, indent=2))
            desc = dict(saved)
            desc.update(slug=export_names.slug_snake(kwargs["slug"]) or "pack",
                        index=max(1, int(kwargs["index"])), gid=max(0, int(kwargs["gid"])), date=kwargs["date"])
            desc["stem"] = export_names.build(emoji=desc["emoji"], slug=desc["slug"], tag=desc["tag"],
                                               index=desc["index"], gid=desc["gid"], date=desc["date"])
            return desc
        finally:
            lock.release()


def manifest_fields(desc: dict) -> dict:
    fields = {"bank_version": desc["bank_version"]}
    if desc["unresolved"]:
        fields.update(unresolved=True, resolution_reason=desc["resolution_reason"], candidates=desc["candidates"])
    return fields
