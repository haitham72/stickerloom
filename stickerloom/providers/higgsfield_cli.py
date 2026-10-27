"""The Higgsfield CLI as a `GenerationProvider`: a thin adapter that calls the
`generation/higgsfield.py` module at call time (never `from ... import <function>`),
so the tests' `mock.patch.object(higgsfield, "RUN"/"wait"/"download"/"binary")`
keep working. No provider logic lives here."""
from __future__ import annotations

from pathlib import Path

from ..generation import higgsfield as _hf
from .base import ProviderJob


class HiggsfieldProvider:
    name = "higgsfield"
    job_provider = "higgsfield-cli"
    setup_hint = "The Higgsfield CLI is not installed (npm i -g @higgsfield/cli, then higgsfield auth login)."

    def available(self) -> bool:
        return _hf.available()

    def account(self) -> dict:
        return _hf.account()

    def list_models(self, kind: str) -> list[dict]:
        from ..generation import model_catalog as _mcat
        return _mcat.catalog(provider="higgsfield")[kind]

    def cost(self, job: ProviderJob) -> float:
        return _hf.cost(job.model, job.params, job.prompt, **job.media)

    def create_image(self, job: ProviderJob) -> str:
        return _hf.create(job.model, job.params, job.prompt, **job.media)

    def create_video(self, job: ProviderJob) -> str:
        return _hf.create(job.model, job.params, job.prompt, **job.media)

    def get(self, job_id: str) -> dict:
        return _hf.get(job_id)

    def wait(self, job_id: str, timeout_s: int = 1200) -> dict:
        return _hf.wait(job_id, timeout_s=timeout_s)

    def download(self, source: str, dest: Path, max_bytes: int | None = None) -> tuple[str, int]:
        if max_bytes is None:              # the module default, and the shape of the tests' download patch
            return _hf.download(source, dest)
        return _hf.download(source, dest, max_bytes=max_bytes)
