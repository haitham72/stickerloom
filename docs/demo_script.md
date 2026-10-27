# Demo script (60 seconds, offline, no account)

Run this exactly; it needs nothing but the repo and Python.

```powershell
$env:STICKERLOOM_PROVIDER = "fake"
stickerloom create "a happy cactus" --auto          # 0:00-0:25
stickerloom export <pack-id> --to dir --dest demo  # 0:25-0:35
stickerloom import demo/cactus                     # 0:35-0:45
```

What to say while it runs:

- 0:00 — "One prompt. No account, no credits, no network."
- 0:10 — "The sheet renders, the engine cuts and keys four stickers,
  the verifier runs its checks, the gates approve."
- 0:25 — "There is the pack. Now it leaves the machine as plain
  files: PNGs plus a checksummed manifest."
- 0:35 — "And back in: verify-before-write, fresh ids. That is the
  whole loop — prompt in, verified pack out."

For the Studio UI instead of the CLI: `stickerloom serve`,
open the printed address, pick the `fake` provider chip, Generate.
The 30-second cut is `assets/launch/script-30s.md`; the loop in
`assets/launch/demo.gif` is the sample pack's own animation.
