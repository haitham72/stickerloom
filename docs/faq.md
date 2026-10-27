# faq

Answers from observed behavior. Every command below was run as written; the
report that accepted these docs pastes the runs.

## which provider should I use?

`fake` to try everything offline, `higgsfield` for real generation,
`openai` only if you know it is experimental (stills only, your own key).
Switch with one variable (`fake` | `higgsfield` | `openai`, default
`higgsfield`):

```powershell
$env:STICKERLOOM_PROVIDER="fake"
```

The model catalog follows the provider: `fake_sheet` / `fake_video`,
the provider catalog, or `gpt_image_1`. The Studio chip shows the active
provider, and `python -m stickerloom doctor` names what is reachable.

## does it work offline?

Yes, with the fake provider. Nothing leaves the machine: no account, no key,
no network. The full pipeline — sheet, stills, checks, pack — runs on
deterministic renders:

```powershell
$env:STICKERLOOM_PROVIDER="fake"
$env:STICKERLOOM_NO_REAL_CLI="1"
$env:STICKERLOOM_OUT="$env:TEMP\sl_out"
$env:STICKERLOOM_INPUT="$env:TEMP\sl_in"
python -m stickerloom create "a happy cactus" --auto
```

Observed: exit 0, `4 stickers cut and checked`, `approved 4 stickers`,
`done: 4 stickers are in the pack`. The fake sheet costs 0.0 credits and the
vision pre-review is skipped with a logged note when no vision model is
configured (those stickers stay "unjudged": the judge never approves).

## how do the approval gates work?

A person approves at G1 (plan), G2 (stills), G3 (video sheet), G4
(animations) and G5 (pack), in the Studio or over the API. A rejected picture
is never a dead end: every technical failure is final (Telegram would refuse
the file), and every judgement failure carries a recorded "Use it anyway"
click that later cuts keep. Rejection never deletes; a sticker keeps its
number through every stage.

## will it spend money without asking?

No. A paid start first answers the price (`409: "This costs N credits. Send
go: true to start it."`), a retry whose price changed is refused instead of
charged, and `STICKERLOOM_DAILY_CREDITS` caps the day's spend counting the
jobs already running. Every provider call is a line in
`out/model_calls.jsonl`, so the ledger always says what went where.

## how do I send a pack to Telegram?

Set the bot token (optional: the user id whose name ends the set name) and
connect from the Studio or the API:

```powershell
$env:STICKERLOOM_TELEGRAM_TOKEN="<bot token>"
$env:STICKERLOOM_TELEGRAM_USER="<bot user id>"
python -m stickerloom doctor
```

Without a token, `doctor` reports `Telegram: not connected` and everything
else keeps working — the verifier's `telegram` stages still judge every
sticker offline, so a pack is known-good before any network call. The token
is never returned by the API or written to logs.

## do I need Postgres or Redis?

No. Files under `STICKERLOOM_OUT` (default `./out`) are the truth; Postgres
mirrors them and Redis caches them, both optionally. With neither running,
`doctor` reports two plain notes (`Postgres: not connected`, `Redis:
memory (not reachable: the in-memory fallback is used)`) and every feature
that does not need them works. Start them only when you want search or
durable queues: `python -m stickerloom db up`.

## where do the files go?

`$env:STICKERLOOM_OUT` (default `./out`): one `G###[-label]/` folder per
batch with its `result.json`, one `jobs/J###[-label].json` per provider job,
`library/` for packs, `model_calls.jsonl` for the spend ledger.
`$env:STICKERLOOM_INPUT` (default `./inputs`): prepared `img-NNN-<subject>/`
sheets, the zero-credit path. Point both at fresh temp folders to keep a run
isolated, as the quickstart above does.

## what if there is no language model?

Everything that does not need one works. The subject expander falls back to
its built-in sets, the vision pre-review marks stickers "unjudged", and the
chat panel shows an install hint until the `agent` extra is installed. The
offline quickstart above is the proof: no model, exit 0, verified pack.
