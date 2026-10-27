# providers

stickerloom never calls a model vendor directly. `stickerloom/generation/jobs.py`
builds a `ProviderJob` and hands it to the active `GenerationProvider`
(`stickerloom/providers/base.py`). The registry (`stickerloom/providers/__init__.py`)
picks the provider from one variable:

```powershell
$env:STICKERLOOM_PROVIDER="fake"   # fake | higgsfield | openai; default: higgsfield
```

A job is stamped with the provider that owns it, so resuming a job later uses
the same provider even if the variable has changed since.

| provider | models | cost | needs |
|---|---|---|---|
| `fake` | `fake_sheet`, `fake_video` | 0, always available | nothing: deterministic offline renders (coloured shapes on a flat green screen, 2x2/3x3/1x1 from the prompt grid) for the full pipeline, the tests and the demo |
| `higgsfield` | the provider catalog in `generation/model_catalog.py` | shown before every call; nothing is spent without a yes | the provider CLI plus `higgsfield auth login` |
| `openai` | `gpt_image_1` (stills only) | 0 through stickerloom: your own key is billed directly | `stickerloom[openai]` extra plus `STICKERLOOM_OPENAI_API_KEY`; experimental, no live test |

Paid safety is in the provider layer, not in each caller: the server answers a
paid start with the price first (`409: "This costs N credits. Send go: true to
start it."`), a retry that changed price is refused, and
`generation/jobs.py` enforces the daily cap `STICKERLOOM_DAILY_CREDITS`
(counting the jobs already running). Every call is a line in
`out/model_calls.jsonl`. The fake provider's renders are deterministic:
same spec, same pixels. Stills and sheets are byte-deterministic (seeded
`numpy` draws, same spec → same bytes); animation bytes additionally depend
on the OpenCV mp4v encoder build, so videos are pixel-stable but not
byte-pinned across machines.

Check what the active provider sees from here:

```powershell
$env:STICKERLOOM_NO_REAL_CLI="1"
python -m stickerloom hf status
python -m stickerloom doctor
```

## how to write a provider

Implement the `GenerationProvider` shape (method names and signatures below
match `stickerloom/providers/base.py` exactly), then add the class to `get()`
in `stickerloom/providers/__init__.py`. 42 lines:

```python
"""A minimal provider: stills only, billed to your own key (42 lines)."""
from pathlib import Path

from stickerloom.providers.base import GenerationProvider, ProviderError, ProviderJob


class MyProvider:
    name = "mine"  # chosen with STICKERLOOM_PROVIDER=mine
    job_provider = "mine"  # stored on job["provider"]
    setup_hint = "Set MY_API_KEY."

    def available(self) -> bool:
        import os
        return bool(os.environ.get("MY_API_KEY"))

    def account(self) -> dict:
        return {"credits": 0.0, "plan": "mine (own key)"}

    def list_models(self, kind: str) -> list[dict]:
        if kind == "image":
            return [{"id": "mine_sheet", "label": "Mine sheet"}]
        return []

    def cost(self, job: ProviderJob) -> float:
        return 0.0

    def create_image(self, job: ProviderJob) -> str:
        return "file:///tmp/mine.png"

    def create_video(self, job: ProviderJob) -> str:
        raise ProviderError("stills only", code=400)

    def get(self, job_id: str) -> dict:
        return {"status": "done", "result_url": "file://" + job_id}

    def wait(self, job_id: str, timeout_s: int = 1200) -> dict:
        return self.get(job_id)

    def download(self, source: str, dest: Path, max_bytes: int | None = None) -> tuple[str, int]:
        data = Path(source.replace("file://", "")).read_bytes()
        dest.write_bytes(data)
        return ("png", len(data))
```
