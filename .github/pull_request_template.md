# Short, specific title

## What this changes

<!-- one paragraph; name the stage(s): sheet, stills, video, pack, gates, export -->

## How I checked it

- [ ] `STICKERLOOM_PROVIDER=fake` quickstart still produces a verified pack
- [ ] `python scripts/gen_verify_docs.py` passes (no doc drift)
- [ ] New/changed behaviour has a test, or says here why not:
- [ ] `.env.example` updated if a new `STICKERLOOM_*` var was added

## No-go checklist

- [ ] No credentials, tokens, or personal paths anywhere in the diff
- [ ] No new hard dependency outside `pyproject.toml` base without discussion
- [ ] Wordmark stays lowercase `stickerloom` in user-facing text
