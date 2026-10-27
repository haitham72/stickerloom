"""The fake provider: deterministic, offline renders for the full pipeline, the tests
and the demo. Sheets are coloured shapes on a flat pure-green screen (parsed grid,
>=20% margins); videos are a 3 s seamless loop (a small periodic shift of the start
image, integer periods per clip). Same spec, same bytes: the seed is a hash of the
model, params, prompt and media, so `create` twice and both downloads are identical.

Job specs live in a module-level registry, so `wait`/`get`/`download` work on any
instance in this process (the server fulfils in one thread and recovers in another).
A different process cannot see them; its `wait` fails cleanly like an unknown ticket.
Always available, always free (cost 0)."""
from __future__ import annotations

import hashlib
import itertools
import json
import re
import threading
from pathlib import Path

import cv2
import numpy as np

from .base import ProviderError, ProviderJob

GREEN = (0, 255, 0)                                            # BGR flat screen
SHAPES = [                                                     # BGR, all far from the key
    (20, 220, 250),                                            # yellow
    (40, 40, 220),                                             # red
    (220, 90, 60),                                             # blue
    (30, 140, 240),                                            # orange
]
GRID_RX = re.compile(r"arranged in (\d+) rows of (\d+)", re.I)
LAYOUT_RX = re.compile(r"(\d+)\s*x\s*(\d+)\s*layout", re.I)
SOURCE_RX = re.compile(r"^fake://([A-Za-z0-9-]+)\.(png|mp4)$")
FPS = 24

_JOBS: dict[str, dict] = {}
_N = itertools.count(1)
_MU = threading.Lock()


def _grid_of(prompt: str) -> tuple[int, int]:
    m = GRID_RX.search(prompt or "") or LAYOUT_RX.search(prompt or "")
    if m:
        r, c = int(m.group(1)), int(m.group(2))
        if (r, c) in ((1, 1), (2, 2), (3, 3)):
            return r, c
    n = len(re.findall(r"(?m)^character \d+:", prompt or ""))
    return {1: (1, 1), 4: (2, 2), 9: (3, 3)}.get(n, (2, 2))


def _seed_of(spec: dict) -> int:
    h = hashlib.sha256(json.dumps(spec, sort_keys=True, default=str).encode()).digest()
    return int.from_bytes(h[:4], "big")          # 32 bits: RandomState's range


def render_sheet(prompt: str, size: int, seed: int) -> np.ndarray:
    """A flat green sheet with one centred shape per cell (BGR uint8)."""
    rows, cols = _grid_of(prompt)
    img = np.empty((size, size, 3), np.uint8)
    img[:] = GREEN
    cell = size / rows
    rng = np.random.RandomState(seed)
    order = rng.permutation(rows * cols)
    for pos in range(rows * cols):
        r, c = divmod(pos, cols)
        cx, cy = int((c + 0.5) * cell), int((r + 0.5) * cell)
        rad = int(cell * 0.183)                                 # cf. tests/test_golden.shape_sheet
        colour = SHAPES[int(order[pos]) % len(SHAPES)]
        kind = int(order[pos]) % 4
        if kind == 0:
            cv2.circle(img, (cx, cy), rad, colour, -1, cv2.LINE_AA)
        elif kind == 1:
            cv2.rectangle(img, (cx - rad // 2, cy - rad - 10), (cx + rad // 2, cy + rad + 10), colour, -1)
        elif kind == 2:
            cv2.rectangle(img, (cx - rad - 10, cy - rad // 2), (cx + rad + 10, cy + rad // 2), colour, -1)
        else:
            cv2.fillPoly(img, [np.array([[cx, cy - rad], [cx - rad, cy + rad - 20], [cx + rad, cy + rad - 20]])], colour, cv2.LINE_AA)
    return img


def render_video(start: Path, seconds: int, seed: int, dest: Path) -> None:
    """A seamless loop: the start image shifted by a small sine, one period per
    second, so the last frame flows back into the first. Edges refill with the key."""
    img = cv2.imread(str(start), cv2.IMREAD_COLOR)
    if img is None:
        raise ProviderError(f"the start image cannot be read: {start}")
    h, w = img.shape[:2]
    h, w = h - h % 2, w - w % 2                                # mp4 needs even sides
    img = img[:h, :w]
    dest.parent.mkdir(parents=True, exist_ok=True)
    out = cv2.VideoWriter(str(dest), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (w, h))
    if not out.isOpened():
        raise ProviderError("the fake video encoder did not open")
    try:
        n = max(1, int(seconds)) * FPS
        amp = max(2, min(w, h) // 200)                          # a few px: motion without leaving a slot
        for t in range(n):
            ph = 2 * np.pi * t / FPS                            # one full period per second
            dx, dy = round(amp * np.sin(ph)), round(amp * np.cos(ph))
            m = np.float32([[1, 0, dx], [0, 1, dy]])
            out.write(cv2.warpAffine(img, m, (w, h), borderMode=cv2.BORDER_CONSTANT, borderValue=GREEN))
    finally:
        out.release()


class FakeProvider:
    name = "fake"
    job_provider = "fake"
    setup_hint = "The fake provider is always available."

    def available(self) -> bool:
        return True

    def account(self) -> dict:
        return {"credits": 0.0, "plan": "fake"}

    def list_models(self, kind: str) -> list[dict]:
        from ..generation import model_catalog as _mcat
        return _mcat.catalog(provider="fake")[kind]

    def cost(self, job: ProviderJob) -> float:
        return 0.0

    def _create(self, kind: str, job: ProviderJob, ext: str) -> str:
        with _MU:
            jid = f"fake-{next(_N):04d}"
            _JOBS[jid] = {"kind": kind, "model": job.model, "params": dict(job.params),
                          "prompt": job.prompt, "media": dict(job.media), "ext": ext}
        return jid

    def create_image(self, job: ProviderJob) -> str:
        return self._create("image", job, "png")

    def create_video(self, job: ProviderJob) -> str:
        return self._create("video", job, "mp4")

    def _result(self, job_id: str) -> dict:
        spec = _JOBS.get(job_id)
        if spec is None:
            raise ProviderError(f"unknown fake job {job_id} (fake tickets live in this process only)", code=404)
        return {"id": job_id, "status": "completed", "result_url": f"fake://{job_id}.{spec['ext']}"}

    def get(self, job_id: str) -> dict:
        return self._result(job_id)

    def wait(self, job_id: str, timeout_s: int = 1200) -> dict:
        return self._result(job_id)

    def download(self, source: str, dest: Path, max_bytes: int | None = None) -> tuple[str, int]:
        m = SOURCE_RX.match(str(source or ""))
        spec = _JOBS.get(m.group(1)) if m else None
        if spec is None:
            raise ProviderError(f"the fake provider cannot download {source!r}", code=404)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        seed = _seed_of({k: spec[k] for k in ("model", "params", "prompt", "media")})
        if spec["kind"] == "image":
            size = 2048 if str(spec["params"].get("resolution", "1k")).lower() == "2k" else 1024
            ok, buf = cv2.imencode(".png", render_sheet(spec["prompt"], size, seed))
            if not ok:
                raise ProviderError("the fake sheet did not encode")
            data = bytes(buf)
            dest.write_bytes(data)
        else:
            start = spec["media"].get("start_image")
            if not start:
                raise ProviderError("the fake video needs a start image")
            render_video(Path(start), int(spec["params"].get("duration", 3) or 3), seed, dest)
            data = dest.read_bytes()
        if max_bytes is not None and len(data) > max_bytes:
            raise ProviderError("result file too large")
        return hashlib.sha256(data).hexdigest(), len(data)
