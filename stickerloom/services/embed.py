"""Text embeddings for the sticker pool (Phase 3B).

The model is HARDCODED: `text-embedding-nomic-embed-text-v1.5` on the local
LM Studio server, 768 dimensions, free and private. When the local server is down and the configured cloud's key exists, `text-embedding-3-small` with
`dimensions=768` answers instead, so one column width serves both. Nomic is trained with task prefixes: documents are embedded as
"search_document: ...", queries as "search_query: ...". Vectors are cached in Redis (a query embedded once is never embedded again).
Nothing here is called unless the pool asks: indexing and search work lexically without it."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

EMBED_MODEL = "text-embedding-nomic-embed-text-v1.5"
OPENAI_EMBED_MODEL = "text-embedding-3-small"
DIMS = 768
PREFIX = {"document": "search_document: ", "query": "search_query: "}
BATCH = 24


class EmbedError(Exception):
    pass


def _post(url: str, body: dict, key: str | None, timeout: float) -> dict:
    headers = {"content-type": "application/json"}
    if key:
        headers["authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST", headers=headers)
    from .telegram import _ssl_context
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
        return json.loads(r.read().decode("utf-8"))


def backend() -> dict:
    """Where embeddings come from now: {provider, model}."""
    from . import llm, llm_catalog
    llm._load_dotenv()
    if os.environ.get("STICKERLOOM_EMBED_PROVIDER", "auto") != "openai" and llm.local_reachable():
        return {"provider": "local", "model": os.environ.get("STICKERLOOM_EMBED_MODEL", EMBED_MODEL)}
    if llm_catalog.endpoint(llm_catalog.default_provider())[1]:
        return {"provider": "openai", "model": OPENAI_EMBED_MODEL}
    return {"provider": "none", "model": None}


def available() -> bool:
    return backend()["provider"] != "none"


def _call(texts: list, b: dict) -> list:
    from . import llm, llm_catalog
    key = None
    try:
        if b["provider"] == "local":
            d = _post(llm.local_url() + "/embeddings", {"model": b["model"], "input": texts}, None, 120)
        else:
            url, key = llm_catalog.endpoint(llm_catalog.default_provider())
            if not key:
                raise EmbedError(f"No embedding key: set {llm_catalog.key_setting(llm_catalog.default_provider())} in .env.")
            d = _post(url + "/embeddings", {"model": b["model"], "input": texts, "dimensions": DIMS}, key, 60)
        vecs = [x["embedding"] for x in sorted(d["data"], key=lambda x: x["index"])]
    except (urllib.error.URLError, OSError, KeyError, ValueError, TypeError) as e:
        raise EmbedError("Cannot get embeddings: " + str(getattr(e, "reason", e)).replace(key or chr(0), "<key>"))
    if len(vecs) != len(texts) or any(len(v) != DIMS for v in vecs):
        raise EmbedError(f"The embedding service answered {[len(v) for v in vecs][:1]}-d vectors, expected {DIMS}")
    return vecs


def embed(texts: list, kind: str = "document", out=None) -> list:
    """One vector per text (768 floats), cached in Redis by text + model. `kind`: 'document' or 'query' (the task prefix)."""
    if not texts:
        return []
    from ..generation import model_calls
    from ..runtime import cache as cachemod
    cc = cachemod.default()
    b = backend()
    if b["provider"] == "none":
        from . import llm_catalog
        raise EmbedError(f"No embedding backend: start LM Studio (STICKERLOOM_LOCAL_URL) or set {llm_catalog.key_setting(llm_catalog.default_provider())}.")
    keys = [cc.key("pool", "vec", cachemod.digest(kind, t), b["model"]) for t in texts]
    got = [cc.get(k, "embed") for k in keys]
    todo = [i for i, v in enumerate(got) if v is None]
    for s in range(0, len(todo), BATCH):
        chunk = todo[s:s + BATCH]
        pre = PREFIX[kind] if b["provider"] == "local" else ""
        import time
        t0 = time.perf_counter()
        vecs = _call([pre + texts[i] for i in chunk], b)
        for i, v in zip(chunk, vecs):
            got[i] = v
            cc.set(keys[i], v, 30 * 24 * 3600)
        try:
            model_calls.append(out, "EMBED", b["provider"], b["model"], latency_ms=int((time.perf_counter() - t0) * 1000), prompt_version=kind,
                               extra={"texts": len(chunk)})
        except Exception:
            pass
    return got


def literal(v: list) -> str:
    """pgvector text form of a vector."""
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"
