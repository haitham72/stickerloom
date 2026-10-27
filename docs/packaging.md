# Packaging (WP10)

Base install (a throwaway venv under TEMP, never in the repo):

```powershell
python -m venv $env:TEMP\wp10venv
& "$env:TEMP\wp10venv\Scripts\python.exe" -m pip install -e .
```

Verified 2026-10-26: base install succeeds (numpy, opencv-python-headless,
pillow, imageio-ffmpeg, fastapi, pydantic, uvicorn), then in that venv with
no [agent] extra and no local server:

- `python -m stickerloom doctor` exits 0 (`ready`), sections 4+5 print the
  curated-list fallback and the export guard (see below).
- `STICKERLOOM_PROVIDER=fake python -m stickerloom create 'quad shapes'`
  cuts 4 ready stickers, exit 0, in 0.8 s.

`pip install -e .[agent]` also succeeds in the same venv (langgraph,
langgraph-checkpoint-sqlite, langchain_core, tortoise-orm, redis) and
`stickerloom.agent.lifecycle.builder` imports.

## Extras

| Extra   | Contents | Needed for |
|---------|----------|------------|
| `agent` | langgraph, langgraph-checkpoint-sqlite, langchain_core, tortoise-orm, redis | chat agent (`agent/`), Studio chat panel, durable queue (`STICKERLOOM_JOB_MODE=queue` + `worker`) |
| `openai`| openai | experimental OpenAI stills provider (`STICKERLOOM_PROVIDER=openai` + `STICKERLOOM_OPENAI_API_KEY`) |
| `db`    | psycopg[binary] | Postgres state (`stickerloom db ...`, write-through) |
| `onnx`  | onnxruntime | AI cutout for photos/video (GrabCut is the fallback) |
| `dev`   | pytest, ruff | test tiers, lint |
| `all`   | everything above | one-shot setup |

`ffmpeg-python` is NOT a dependency: nothing in `stickerloom/` imports it
(ffmpeg runs via `imageio-ffmpeg`'s bundled build or a system ffmpeg).
No `pgvector`: nothing imports it. Base deps are minimal per G3.5;
`psycopg`, `redis` and `langgraph` are lazy imports with actionable errors
(`store/db.py`, `runtime/cache.py`, `agent/lifecycle/*`, `console/server.py`).

`[project] license` fields stay EMPTY until the LICENSE file lands in WP14.
There is no `py_modules` entry (no `sheldon` artifact ever existed in this
tree; the package is `stickerloom` via `[tool.setuptools] packages`).

## Env vars (all `STICKERLOOM_*`, see `.env.example`)

- `STICKERLOOM_LLM_PROVIDER=openai|local|auto|none` (default `auto`; the
  test suite pins `none`).
- `STICKERLOOM_HTTP_TIMEOUT` seconds for provider/model calls; unset keeps
  each caller built-in timeout (the agent brain uses 45 s).
- `STICKERLOOM_DAILY_CREDITS`: daily cap; `generation/jobs.py` refuses new
  paid calls once the spend ledgered since local midnight reaches it
  (unset: no cap; the paid-call consent still applies).

## Timings (before -> after, `Measure-Command`, fake provider, temp out/)

| Command | Before | After |
|---------|--------|-------|
| `python -c "import stickerloom.cli"` | 0.53 s | 0.53 s |
| `create 'quad shapes'` (prepared 2x2, stills) | 1.92 s | 2.16 s |
| `doctor` (no server, `LLM_PROVIDER=none`) | 3.6 s | 3.5 s |

All far under the 8 s `create` budget, so the refactor is a no-op by
design: no import was moved (moving `flow.pipeline` out of the top level
would touch every command for no measurable gain). `doctor` needs neither
the [agent] extra nor a local server: every chat/local-model/backend probe
already fails soft to `NOTE`.

## `doctor` sections 4+5

- `doctor [--models] [--since 24h]`: section 4 prints the cached
  Higgsfield model counts with their fetch timestamp; `--models` forces a
  refresh and `--since` sets the freshness window (`24h`, `30m`, `7d` or
  seconds). When the fetch is unreachable (no CLI, `NO_REAL_CLI`, or an
  empty dump) it falls back to `using the curated list` (from
  `generation/model_catalog.py`, defaults `nano_banana_flash`/`kling3_0`)
  with a timestamped note.
- Section 5 (export/import smoke, dry run only, never a pack) is guarded:
  `stickerloom.export` does not exist in this tree (WP9 builds it in
  parallel), so `doctor` reports `export not available in this tree` and
  SKIPs. No fake pass is mocked. The owner runs the true smoke at merge.

Deviation: this file is `docs/packaging.md`, not the plan README/launch
section (README belongs to WP14 and would collide; the venv-build +
export/import round-trip accept is served by this file plus the logs).