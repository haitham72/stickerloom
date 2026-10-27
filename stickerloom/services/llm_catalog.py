"""Shared, cached model catalog and provider routing. Lists never spend credits."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.request

DEFAULT_MODEL = "gpt-6-luna"
MODEL_ALIASES = {alias: "openai:" + mid
                 for mid in (DEFAULT_MODEL, "gpt-5.6-luna")
                 for alias in (mid,)}
# Documented defaults only (review G3.3): the curated OpenAI chat set and its
# image-capable subset. Everything else comes from configuration, not code:
# STICKERLOOM_OPENAI_MODELS / _VISION, STICKERLOOM_FALLBACK_MODELS / _VISION.
OPENAI_MODELS = ("gpt-6-luna", "gpt-6.1-sol")
OPENAI_VISION = ("gpt-4o-mini", "gpt-6.1-sol")
_CACHE: dict = {}
_LOCK = threading.Lock()


def _csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """A comma-separated env list, or `default` when unset. An explicitly empty value means none."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def openai_models() -> tuple[str, ...]:
    return _csv("STICKERLOOM_OPENAI_MODELS", OPENAI_MODELS)


def openai_vision() -> tuple[str, ...]:
    return _csv("STICKERLOOM_OPENAI_VISION", OPENAI_VISION)


def fallback_models() -> tuple[str, ...] | None:
    """The fallback endpoint's curated set, or None (show whatever it lists)."""
    raw = os.environ.get("STICKERLOOM_FALLBACK_MODELS")
    if raw is None:
        return None
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def fallback_vision() -> tuple[str, ...] | None:
    """The fallback endpoint's image-capable set, or None (assume the shown ones are)."""
    raw = os.environ.get("STICKERLOOM_FALLBACK_VISION")
    if raw is None:
        return None
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def default_provider() -> str:
    """Embeddings (services/embed.py) go to official OpenAI when the local server is down."""
    return "openai"


def key_setting(provider: str) -> str:
    """Credentials belong to their provider."""
    from . import llm
    if provider == "openai":
        return llm.KEY_VAR
    if provider == "fallback":
        return llm.FALLBACK_KEY_VAR
    return llm.FALLBACK_KEY_VAR


def endpoint(provider: str) -> tuple[str | None, str | None]:
    """(base URL, key) for a cloud provider. Either may be None when unconfigured."""
    from . import llm
    llm._load_dotenv()
    if provider == "local":
        return llm.local_url(), None
    if provider == "fallback":
        url = (os.environ.get(llm.FALLBACK_URL_VAR) or "").strip().rstrip("/") or None
        return url, os.environ.get(llm.FALLBACK_KEY_VAR) or None
    if provider == "openai":
        return "https://api.openai.com/v1", os.environ.get(llm.KEY_VAR) or None
    raise llm.LLMError("Unknown model provider: choose Local, OpenAI or Fallback")


def ready(provider: str) -> bool:
    """Can this cloud provider be called: official OpenAI needs its key, the fallback endpoint needs its URL."""
    url, key = endpoint(provider)
    if provider == "openai":
        return bool(key)
    if provider == "fallback":
        return bool(url)
    return False


def normalize_model(value: str) -> str:
    """Luna uses official OpenAI, including selections saved before provider separation."""
    value = str(value or "")
    return MODEL_ALIASES.get(value, value)


def split_model(value: str, *, legacy_local: bool = False) -> tuple[str, str]:
    from . import llm
    value = normalize_model(value)
    head, sep, tail = value.partition(":")
    if sep and head in ("local", "openai", "fallback"):
        if not tail:
            raise llm.LLMError("Choose a model id after the provider")
        return head, tail
    if legacy_local or value in llm.list_local_models():
        return "local", value
    return default_provider(), value


def _fetch(url: str, key: str | None) -> dict:
    from .telegram import _ssl_context
    headers = {"accept": "application/json"}
    if key:
        headers["authorization"] = "Bearer " + key
    req = urllib.request.Request(url + "/models", headers=headers)
    with urllib.request.urlopen(req, timeout=2.0, context=_ssl_context()) as response:
        return json.loads(response.read().decode("utf-8"))


def cloud_models(provider: str, force: bool = False) -> list[dict]:
    from . import llm
    if provider == "local":
        return []
    url, key = endpoint(provider)
    # Tests and disabled installations must never contact an external endpoint.
    if not ready(provider) or os.environ.get("STICKERLOOM_LLM_PROVIDER") == "none":
        return []
    cache_key = (provider, url, hashlib.sha256((key or "").encode()).hexdigest())
    with _LOCK:
        previous = _CACHE.get(cache_key)
        if previous and not force and time.monotonic() - previous[0] < previous[1]:
            return [dict(row) for row in previous[2]]
        rows, ttl = [], 10.0
        try:
            data = _fetch(url, key)
            if provider == "openai":
                shown, allow = set(openai_models()), set(openai_vision())
            else:
                configured, vision = fallback_models(), fallback_vision()
                listed = [str(r["id"]) for r in data.get("data", [])
                          if isinstance(r, dict) and r.get("id")]
                shown = set(configured) if configured is not None else set(listed)
                allow = set(vision) if vision is not None else set(shown)
            rows = []
            for row in data.get("data", []):
                if not isinstance(row, dict) or not row.get("id"):
                    continue
                mid = str(row["id"])
                if mid not in shown:
                    continue
                rows.append({"id": f"{provider}:{mid}", "model": mid, "label": mid, "provider": provider,
                           "vision": mid in allow, "free": provider == "fallback"})
            ttl = 30.0
        except Exception:
            # No speculative options when the configured catalog cannot be read.
            pass
        _CACHE[cache_key] = (time.monotonic(), ttl, rows)
        return [dict(row) for row in rows]


def groups(force: bool = False) -> dict:
    from . import llm
    return {"local": [{"id": "local:" + mid, "model": mid, "label": mid, "provider": "local",
                       "vision": None, "free": True} for mid in llm.list_local_models(force)],
            "fallback": cloud_models("fallback", force), "openai": cloud_models("openai", force)}


def choices(force: bool = False) -> list[dict]:
    return [row for rows in groups(force).values() for row in rows]


def validate(value: str) -> str:
    from . import llm
    provider, mid = split_model(value)
    if provider != "local" and not ready(provider):
        if provider == "fallback":
            raise llm.LLMError(f"No fallback endpoint: set {llm.FALLBACK_URL_VAR} in .env and restart.")
        raise llm.LLMError(f"No AI key for {provider}: set {key_setting(provider)} in .env and restart.")
    qualified = provider + ":" + mid
    if qualified not in {row["id"] for row in choices()}:
        raise llm.LLMError("That model is not available in the model catalog; refresh the model list and choose a listed model")
    return qualified


def supports_images(provider: str, mid: str) -> bool:
    # Local models remain server-selected; local capability discovery is not standardized.
    if provider == "local":
        return True
    if provider == "openai":
        return mid in openai_vision()
    shown_models = fallback_models()
    vision = fallback_vision()
    if vision is not None:
        return mid in vision
    if shown_models is not None:
        return mid in shown_models
    return True
