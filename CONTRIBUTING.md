# contributing

How to change stickerloom without breaking its promises. Four rules carry
over from the design; everything else is normal care.

## 1. the engine stays deterministic and pure

`stickerloom/engine/` is numpy/OpenCV only: same bytes in, same verdict out,
never raises, never looks at meaning, never repairs pixels. Thresholds live in
`EngineConfig` only. The engine never imports a web framework, a database
driver, an agent library or a model client — `SheetTests.test_engine_boundary`
in `tests/test_engine.py` imports the engine in a subprocess and fails on any
of them. Keep it that way: new checks go in the verifier catalogue with a
threshold in config, and the docs table is regenerated, never hand-edited:

```powershell
python scripts/gen_verify_docs.py
```

## 2. a rejection is never a dead end

A block is final only where Telegram itself would refuse the file (format,
size, codec, dimensions, rate, duration, audio, alpha) or where nothing was
cut. Every other block is a judgement call and gets a visible "Use it anyway":
register it in `OVERRIDABLE` in `stickerloom/engine/verify.py`, keep the check
in the report as a warning once allowed, and make every later cut keep the
waiver (`verify.run` already threads `waive` through). Never add a block a
person cannot get past; never loosen a technical block. The vision judge only
pre-reviews and never approves.

## 3. names are code, not hand-built paths

Inputs are one folder per variant, `img-NNN-<subject>/` (paired `vid-NNN-`
for video), and the watch-folder names are final: never rename them in code.
Batch and job folders carry labels (`G111-<slug>-<time>`,
`J058-<subject>-<kind>.json`); the id stays the address everywhere. Never
build `out / "G111"` by hand: resolve through `pipeline.gen_dir`,
`pipeline.out_path` and `jobs.job_dir`. Files are the truth and Postgres
mirrors them; the default install runs with neither Postgres nor Redis.

## 4. the product is the api

Every feature is an engine function plus a stable JSON contract first, a
screen second; never put logic only in the browser. New routes appear in
`stickerloom/api/openapi.py` (the one table `tests/test_openapi.py` guards
against drift) and surface in `docs/api.md`. Paid calls happen only after the
price is shown and agreed to; tests never reach a real provider
(`STICKERLOOM_NO_REAL_CLI=1`, fakes).

## running the checks

```powershell
python -m pytest tests/test_engine.py -q
node --test (Get-ChildItem tests/js/*.test.js | % FullName)
python -m stickerloom doctor
```

Line endings are LF, no BOM. Keep the wordmark lowercase (`stickerloom`)
everywhere, including prose.
