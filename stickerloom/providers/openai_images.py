"""OpenAI Images as a `GenerationProvider` (**experimental**, `[openai]` extra): stills
only, billed to the caller's own key (`STICKERLOOM_OPENAI_API_KEY`). There is no
credit consent to show — OpenAI bills the key directly — so `cost` returns 0 and
every call needs the user's explicit go-ahead at the surface that starts it."""
from __future__ import annotations

import base64
import hashlib
import itertools
import os
import threading
from pathlib import Path

from .base import ProviderError, ProviderJob

MODEL = "gpt-image-1"

_N = itertools.count(1)
_MU = threading.Lock()
_BLOBS: dict[str, bytes] = {}


class OpenAIImagesProvider:
    name = "openai"
    job_provider = "openai"
    setup_hint = "Set STICKERLOOM_OPENAI_API_KEY and install stickerloom[openai]."

    def available(self) -> bool:
        if not os.environ.get("STICKERLOOM_OPENAI_API_KEY"):
            return False
        try:
            import openai  # noqa: F401
            return True
        except ImportError:
            return False

    def account(self) -> dict:
        return {"credits": 0.0, "plan": "openai (own key)"}

    def list_models(self, kind: str) -> list[dict]:
        from ..generation import model_catalog as _mcat
        return _mcat.catalog(provider="openai")[kind]

    def cost(self, job: ProviderJob) -> float:
        return 0.0

    def create_image(self, job: ProviderJob) -> str:
        try:
            from openai import OpenAI
        except ImportError:
            raise ProviderError("stickerloom[openai] is not installed (pip install stickerloom[openai])", code=503)
        key = os.environ.get("STICKERLOOM_OPENAI_API_KEY")
        if not key:
            raise ProviderError("STICKERLOOM_OPENAI_API_KEY is not set", code=503)
        try:
            res = OpenAI(api_key=key).images.generate(model=MODEL, prompt=job.prompt, size="1024x1024")
            b64 = res.data[0].b64_json
        except Exception as e:
            raise ProviderError(f"OpenAI images failed: {e}"[:300], code=502)
        if not b64:
            raise ProviderError("OpenAI returned no image", code=502)
        with _MU:
            jid = f"openai-{next(_N):04d}"
            _BLOBS[jid] = base64.b64decode(b64)
        return jid

    def create_video(self, job: ProviderJob) -> str:
        raise ProviderError("The OpenAI provider renders stills only (experimental).", code=400)

    def get(self, job_id: str) -> dict:
        if job_id not in _BLOBS:
            raise ProviderError(f"unknown OpenAI job {job_id}", code=404)
        return {"id": job_id, "status": "completed", "result_url": f"openai://{job_id}.png"}

    def wait(self, job_id: str, timeout_s: int = 1200) -> dict:
        return self.get(job_id)

    def download(self, source: str, dest: Path, max_bytes: int | None = None) -> tuple[str, int]:
        jid = (str(source or "").removeprefix("openai://").removesuffix(".png"))
        data = _BLOBS.get(jid)
        if data is None:
            raise ProviderError(f"the OpenAI provider cannot download {source!r}", code=404)
        if max_bytes is not None and len(data) > max_bytes:
            raise ProviderError("result file too large")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return hashlib.sha256(data).hexdigest(), len(data)
