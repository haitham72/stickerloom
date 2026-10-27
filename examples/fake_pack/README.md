# fake_pack: offline sample made with the fake provider

A complete 2x2 sticker run with no account, no credits, and no network:
prompt in, verified sticker pack out. The sheet, the four stills, and the
four animations all come from `STICKERLOOM_PROVIDER=fake` (deterministic
offline renders), so anyone can reproduce an equivalent pack by running the
commands below.

## Commands (PowerShell, run from the repo root with the project Python)

```powershell
$env:STICKERLOOM_PROVIDER="fake"
$env:STICKERLOOM_NO_REAL_CLI="1"
$env:STICKERLOOM_LLM_PROVIDER="none"
$env:STICKERLOOM_VISION_PROVIDER="none"
$env:STICKERLOOM_OUT="$env:TEMP\sl-wp13-out"
$env:STICKERLOOM_INPUT="$env:TEMP\sl-wp13-in"
python -m stickerloom create "a cheerful round cartoon mushroom, flat vector style, four happy moods" --auto --grid 2x2
```

This run ends with `pack becdd332 from G001 (...)`, exit 0; its console
output is [gates.log](gates.log). It covers sheet, stills, gates G1/G2, and
the pack (G5). There was no Telegram send.

The CLI has no video subcommand for a live batch (`stickerloom animate`
only handles prepared-import batches and answers such a batch with
"No animation prepared for this variation"), so the animation step ran the
same calls the Studio "Make a video" button makes, headless, with the same
env as above (save as a scratch file outside the repo and run it):

```python
import os
from pathlib import Path

out = Path(os.environ["STICKERLOOM_OUT"])
gid = 1

from stickerloom.engine.config import EngineConfig
from stickerloom.flow import gates, pipeline as pl
from stickerloom.generation import jobs, model_catalog
from stickerloom.runtime.writer_lock import WriterLock

cfg = EngineConfig()

with WriterLock(out, "wp13 video step"):
    aid = gates.quick_sheet(out, gid, cfg)["sheet"]
    res = pl.read_result(out, gid)
    entry = gates.sheet_of(res, aid)
    assert entry["status"] in ("APPROVED", "VIDEO_BLOCKED"), entry
    start = pl.gen_dir(out, gid) / entry["file"]
    model, params = model_catalog.resolve("video", None, None)
    job = jobs.create(out, "video", task=res.get("task"), generation="G001", request={
        "model": model, "options": {},
        "prompt": "gentle looping motion for each of the four mushroom stickers",
        "start_image": str(start), "sheet": aid,
        "label": res.get("prompt", ""), "loop": True, "user": "local"})
    job = jobs.fulfil(out, job["id"])
    assert job["status"] == "DONE", job
    job = jobs.read(out, job["id"])
    data = (out / job["result"]["file"]).read_bytes()
    gates.attach_video(out, gid, aid, data, f"{job['id']}.mp4",
                       sent_prompt="gentle looping motion for each of the four mushroom stickers",
                       model=job.get("model"))
    gates.slice_video(out, gid, aid, cfg, 0.0)
```

Its console output is [video.log](video.log): video job J002 DONE,
`G001 animations cut: 4 ready`, all four stickers `still=READY anim=READY`.

## Files

| file | demonstrates |
|---|---|
| [prompt.txt](prompt.txt) | the exact prompt the pack was created with |
| [sheet.png](sheet.png) | the generated sheet: 2x2 mushroom shapes on the green screen |
| [result.json](result.json) | the per-stage batch record: plan, sheet/still checks, gate reviews, video sheet, stickers, pack id |
| [gates.log](gates.log) | console output of the create run (sheet, stills, gates, pack) |
| [video.log](video.log) | console output of the animation run (video job, slicing, per-sticker results) |
| [stills/](stills) | the four 512x512 Telegram-ready stills |
| [animated/](animated) | one animation (sticker 1) cut from the returned video |
| [manifest.json](manifest.json) | the WP9 exchange manifest (`schema_version: 1` + `assets[]` with sha256/dims) — this whole folder imports as-is: `stickerloom import examples/fake_pack` |

## Notes

- Rerunning the commands gives an equivalent pack: same structure, same
  still and animation bytes for the same prompt and grid (the fake renderer
  is seeded); only ids and timestamps differ.
- The only edit to the recorded run is in `result.json`: run-directory
  prefixes read `<OUT>` so the sample carries no machine paths.
- Nothing here spent money or touched a real provider: both provider calls
  report `0.0 credits`, and no Telegram send was made.
