"""Which provider fulfils jobs: `STICKERLOOM_PROVIDER` (`fake` | `higgsfield` |
`openai`, default `higgsfield`). Fresh instances on every call — providers are
stateless apart from the fake/openai in-process result stores, which are
module-level on purpose (see `fake.py`)."""
from __future__ import annotations

import os

from .base import GenerationProvider, ProviderError, ProviderJob
from .fake import FakeProvider
from .higgsfield_cli import HiggsfieldProvider
from .openai_images import OpenAIImagesProvider

NAMES = ("higgsfield", "fake", "openai")

__all__ = ["GenerationProvider", "ProviderError", "ProviderJob", "FakeProvider",
           "HiggsfieldProvider", "OpenAIImagesProvider", "NAMES", "active_name",
           "current", "get", "for_job"]


def active_name() -> str:
    return (os.environ.get("STICKERLOOM_PROVIDER") or "higgsfield").strip().lower()


def get(name: str | None = None) -> GenerationProvider:
    name = (name if name is not None else active_name()).strip().lower()
    if name == "higgsfield":
        return HiggsfieldProvider()
    if name == "fake":
        return FakeProvider()
    if name == "openai":
        return OpenAIImagesProvider()
    raise ProviderError(f"unknown provider '{name}' (STICKERLOOM_PROVIDER=fake|higgsfield|openai)", code=400)


def current() -> GenerationProvider:
    return get()


def for_job(provider: str | None) -> GenerationProvider:
    """The provider that owns a stored `job["provider"]` string; unknown or missing
    falls back to the active one (a job created before providers existed)."""
    return get({"higgsfield-cli": "higgsfield", "higgsfield": "higgsfield",
                "fake": "fake", "openai": "openai"}.get(str(provider or "").strip().lower(), active_name()))
