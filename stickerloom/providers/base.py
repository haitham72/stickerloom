"""The generation-provider interface (WP6): everything that can fulfil a job behind one shape.

`jobs.fulfil` builds a `ProviderJob` and calls `cost` / `create_image` / `create_video` /
`wait` / `download` on the active provider (`STICKERLOOM_PROVIDER`, default `higgsfield`).
`generation/higgsfield.py` keeps its logic untouched; `providers/higgsfield_cli.py` is the
thin adapter over it, and `HiggsError` subclasses `ProviderError` so every existing
`except` and transient/timeout rule keeps working unchanged."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class ProviderError(Exception):
    """A provider failed. `code` is an HTTP-ish status; `transient` is what
    `jobs.fulfil` branches on: a transient failure while WAITING parks the job as
    TIMEOUT (resumable on the same ticket), anything else fails it."""

    def __init__(self, message: str = "", code: int = 500, transient: bool = False):
        super().__init__(message)
        self.code = code
        self.transient = transient


@dataclass
class ProviderJob:
    """One paid (or free) provider call: the resolved model, its params, the prompt,
    and the media paths (`start_image`, `end_image`, `image_references`)."""

    kind: str                  # "image" | "video"
    model: str
    params: dict
    prompt: str
    media: dict = field(default_factory=dict)
    timeout_s: int = 1200


class GenerationProvider(Protocol):
    """What `jobs.fulfil` and `generation/recovery.py` need. `wait` returns the
    provider's job dict (`status`, `result_url`); `download` fetches `result_url`."""

    name: str                  # "higgsfield" | "fake" | "openai" (STICKERLOOM_PROVIDER)
    job_provider: str          # the string stored on `job["provider"]`
    setup_hint: str            # one line shown when this provider is not available

    def available(self) -> bool: ...
    def account(self) -> dict: ...                                  # {credits, plan}
    def list_models(self, kind: str) -> list[dict]: ...
    def cost(self, job: ProviderJob) -> float: ...
    def create_image(self, job: ProviderJob) -> str: ...             # -> provider job id
    def create_video(self, job: ProviderJob) -> str: ...             # -> provider job id
    def get(self, job_id: str) -> dict: ...                         # one read-only lookup
    def wait(self, job_id: str, timeout_s: int = 1200) -> dict: ...
    def download(self, source: str, dest: Path, max_bytes: int | None = None) -> tuple[str, int]: ...
