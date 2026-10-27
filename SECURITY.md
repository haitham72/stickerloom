# security

## secrets live in the environment, never in the repo

Every secret enters through a `STICKERLOOM_*` variable or the local `.env`
file (see `.env.example`; every variable is optional). The tree refuses the
rest: `.gitignore` excludes `.env`, `.env.*`, `**/telegram.json`, `*.pem`,
`*.key` and the `out/` runtime data, and the pre-commit hook runs a secret
scanner before each commit. Never commit a bot token, an API key, a `.env`
file or anything under `out/`. The Telegram token is never returned by the
API and never written to logs — the status route reports "connected or not",
never the token.

## paid calls need consent and a cap

A generation job shows its price before anything is spent: starting a paid
call without agreement answers `409` with the estimate
(`console/server.py`), and a retry whose price changed is refused rather than
charged. `generation/jobs.py` enforces the daily cap:

- `STICKERLOOM_DAILY_CREDITS` bounds the day's spend, counting the jobs
  already running, so parallel starts cannot slip past it;
- a claimed job resumes by its stored ticket instead of paying twice;
- every provider or model call is one line in `out/model_calls.jsonl`.

Run the offline suite with the real CLIs disabled and the fake provider, so
no test can spend:

```powershell
$env:STICKERLOOM_NO_REAL_CLI="1"
$env:STICKERLOOM_PROVIDER="fake"
python -m pytest tests/test_providers.py -q
```

## reporting

Report a suspected secret leak or a spend-safety bug as a problem record
(`flow/problems.py`, `out/problems.jsonl`) with the job ids and ledger lines,
and rotate the affected credential before anything else.
