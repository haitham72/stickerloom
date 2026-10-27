"""The AssetStore seam (Phase 3A, A3): how the rest of the program reaches a stored file.

An object key is a path relative to the store root (`G004/slices/img-004-teddy-teddy_with_book.png`): the same
string `assets.object_key` holds in Postgres. Nothing outside this module turns a key into a path, so a later
S3-compatible store replaces `LocalAssetStore` without touching the callers. Rules the local store enforces:

- a key never leaves the root (no `..`, no absolute path, no drive letter);
- files are never overwritten with different bytes (history is append-only): `put` of an existing key returns
  the existing record when the bytes are equal and refuses otherwise;
- bytes are hashed, never opened as media;
- a signed URL token (HMAC-SHA256 over key + expiry + user) expires and cannot be edited to name another file.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path


class AssetError(Exception):
    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.code = code


def _clean(key: str) -> str:
    k = str(key or "").replace("\\", "/")
    parts = k.split("/")
    if not k or any(p in ("", ".", "..") or ":" in p for p in parts):       # also refuses "/abs", "a//b", "a/", "C:/x"
        raise AssetError(f"bad object key: {key!r}")
    return k


class LocalAssetStore:
    def __init__(self, root: Path, secret: bytes | None = None):
        self.root = Path(root).resolve()
        self._secret = secret

    # ---- keys and files -------------------------------------------------------------------
    def path(self, key: str) -> Path:
        from ..flow.pipeline import out_path                 # a key names its batch by id (G111/...); the folder on disk may carry a label (G111-dog_as_banana-...)
        p = out_path(self.root, _clean(key)).resolve()
        if self.root != p and self.root not in p.parents:
            raise AssetError(f"bad object key: {key!r}")
        return p

    def exists(self, key: str) -> bool:
        try:
            return self.path(key).is_file()
        except AssetError:
            return False

    def read(self, key: str) -> bytes:
        p = self.path(key)
        if not p.is_file():
            raise AssetError(f"no such asset: {key}", 404)
        return p.read_bytes()

    def sha256(self, key: str) -> str:
        return hashlib.sha256(self.read(key)).hexdigest()

    def put(self, key: str, data: bytes) -> dict:
        """Store bytes under a new key. An existing key with the same bytes is fine (idempotent); different bytes are refused."""
        p = self.path(key)
        digest = hashlib.sha256(data).hexdigest()
        if p.is_file():
            if hashlib.sha256(p.read_bytes()).hexdigest() != digest:
                raise AssetError(f"{key} already exists with different bytes: assets are never overwritten", 409)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            from ..runtime import atomic
            atomic.write_bytes(p, data)
        return {"object_key": _clean(key), "sha256": digest, "bytes": len(data)}

    # ---- signed URLs ------------------------------------------------------------------------
    def _key(self) -> bytes:
        if self._secret is None:
            f = self.root / ".asset_secret"
            try:
                self._secret = f.read_bytes()
            except OSError:
                self._secret = os.urandom(32)
                try:
                    self.root.mkdir(parents=True, exist_ok=True)
                    f.write_bytes(self._secret)
                except OSError:
                    pass
        return self._secret

    def sign(self, key: str, ttl: int = 300, user: str = "local", now: float | None = None) -> str:
        body = json.dumps({"k": _clean(key), "u": user, "e": int((now if now is not None else time.time()) + ttl)},
                          separators=(",", ":")).encode()
        mac = hmac.new(self._key(), body, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(body).decode().rstrip("=") + "." + base64.urlsafe_b64encode(mac).decode().rstrip("=")

    def verify(self, token: str, user: str | None = None, now: float | None = None) -> str:
        """The object key a valid, unexpired token names (and, when `user` is given, belongs to that user)."""
        try:
            b, m = str(token).split(".", 1)
            body = base64.urlsafe_b64decode(b + "=" * (-len(b) % 4))
            mac = base64.urlsafe_b64decode(m + "=" * (-len(m) % 4))
        except Exception:
            raise AssetError("bad asset token", 403)
        if not hmac.compare_digest(mac, hmac.new(self._key(), body, hashlib.sha256).digest()):
            raise AssetError("bad asset token", 403)
        d = json.loads(body)
        if (now if now is not None else time.time()) > d["e"]:
            raise AssetError("asset link expired", 403)
        if user is not None and d.get("u") != user:
            raise AssetError("this asset belongs to another user", 403)
        return _clean(d["k"])

    def url(self, key: str, ttl: int = 300, user: str = "local") -> str:
        return f"/api/assets/{self.sign(key, ttl, user)}"
