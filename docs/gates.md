# Approval gates

stickerloom pauses for a human at five gates (G1 plan, G2 stills, G3 video sheet,
G4 animations, G5 pack). Python judges what is *correct* at every gate and nobody
can approve a FAILED sticker as it stands; a human judges what is *good*.
`STICKERLOOM_GATES` (or `create --gates ...`, or Studio Settings) selects how the
gates resolve:

| Mode | Meaning |
|---|---|
| `manual` (default) | Today's behavior: a human approves every gate; "Use it anyway" overrides judgement calls with a recorded, reversible click. |
| `auto` | Headless drivers approve each gate with `gates.review(..., by="auto")`: everything the verifier passes approves; overridable (judgement) failures are auto-overridden with a logged `auto gates: allowed anyway: ...` reason; any other failure stops the gate. |
| `off` | No gate pauses: every APPROVE call behaves as the auto gates, whoever makes it. Verification still runs and TECHNICAL failures still block the pack. |

Precedence: `create --gates ...` (this run) wins over `STICKERLOOM_GATES`,
which wins over the Studio switch (`GET`/`POST /api/gates/setting`, owner only,
stored in `out/gates.json`), which wins over `manual`. An unknown env or file
value falls back to `manual` so a typo can never silently auto-approve; an
unknown explicit value is refused. Rejections always stay human, in every mode.

## TECHNICAL always blocks the pack

Every verifier BLOCK is either TECHNICAL — Telegram itself would reject the file
(format, size, codec, dimensions, fps, duration, audio, alpha) or the check
crashed (`verifier_error`) — or a JUDGEMENT (how the picture looks). The full
catalogue is `stickerloom/engine/verify.py` (`OVERRIDABLE`, `TECHNICAL`).

- In `auto` and `off`, a TECHNICAL failure stops the gate with a 409: the run
  waits for a human instead of approving.
- At G5, `_pack_technical_stop` re-scans every final sticker's stored still and
  animation checks for TECHNICAL failures before `pack_limits` runs, so a
  technical failure blocks the pack in all three modes — even `off`.
- A cell with no picture, a sheet-level stop ("cut it anyway" territory) and a
  verifier crash are not judgement calls either: the auto gates stop on those too.

Overridable failures that the auto gates clear stay in the stored report as
warnings (`allowed by you`) with the override on the sticker and in its history —
the same verdict the next cut would measure, without re-rendering.
