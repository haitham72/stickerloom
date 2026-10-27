"""The agentic creator: ONE go-ahead from a request to a sticker pack on Telegram.

    request -> plan (the price of the whole run, shown once) -> [Create and send to Telegram] -> sheet -> cut and check -> vision check -> approve -> (video) -> pack -> Telegram

Two choices, both in the chat's settings:

- **Scope.** `images`: the stills go to Telegram as a static pack (one sheet is paid for). `video`: the stills are animated first (a second paid call) and the animated pack goes.
- **Bypass.** `on`: the person's standing approval is used at every gate (G2 stills, G4 animations, G5 pack); each is recorded as a human decision with the note "agentic creator: standing
  approval" and the run never stops to ask. `off`: the run stops at each gate and waits for ONE click ("Approve and continue").

What never changes (`CLAUDE.md` rules 10 and 13): Python's blocks are final; the vision model only pre-reviews and never approves; nothing is deleted; no paid call is made beyond the
price the person saw (a video that would cost more than a quarter above its estimate stops the run first). **Any rejection stops the run**, with or without bypass: a cell Python blocked, a
sticker the vision judge rejected, an animation that is blocked or out of bounds, a failed job, a pack Telegram would refuse, Telegram not connected. It says what and why and offers
the next step as buttons (continue without those stickers, redo, stop). Nothing is half-sent: Telegram is the last step.

The module is a state machine over the `tools` interface of `agent/tools.py` (the same engine functions the Studio's buttons call; `FakeTools` in the tests). `advance` does every
step that is possible right now and returns; the server calls it again until the run is `done`, `stopped` or `failed` (`Console.drive_creator`). A checkpoint saves each step; tagged video jobs
recover a lost run/job pointer. Restart driving begins on the next poll; other external-side-effect crash windows may still need Queue recovery."""
from __future__ import annotations

import time

STEPS = {"images": [("sheet", "Draw the sheet"), ("cut", "Cut and check"), ("look", "Look at the pictures"), ("approve", "Approve the stickers"), ("pack", "Make the pack"),
                    ("telegram", "Send to Telegram")],
         "video": [("sheet", "Draw the sheet"), ("cut", "Cut and check"), ("look", "Look at the pictures"), ("approve", "Approve the stickers"), ("video", "Animate"),
                   ("approve_anim", "Approve the animations"), ("pack", "Make the pack"), ("telegram", "Send to Telegram")]}
NOTE = "agentic creator: the person's standing approval"
VIDEO_OVER = 1.25                 # a video priced above 125% of what the person was shown stops the run before anything is sent
DEFAULTS = {"on": False, "scope": "images", "bypass": False}


def settings_of(sess: dict) -> dict:
    c = {**DEFAULTS, **(sess.get("settings", {}).get("creator") or {})}
    c["scope"] = c["scope"] if c["scope"] in ("images", "video") else "images"
    return c


def new_run(*, prompt: str, subject: str, grid: str, style_id: str, scope: str, bypass: bool, estimate: float | None, video_estimate: float | None,
            end: str = "export") -> dict:
    """`end` (the chat's stage, agent/stages.py): 'export' runs to the Telegram send;
    'animation' ends after the animations are approved (G4), with no pack and no Telegram;
    'pack' ends after the pack is made, with no Telegram (`create --auto`)."""
    total = round((estimate or 0) + ((video_estimate or 0) if scope == "video" else 0), 2)
    end = "animation" if end == "animation" and scope == "video" else "pack" if end == "pack" else "export"
    return {"id": f"C{int(time.time() * 1000) % 10**9}", "prompt": prompt, "subject": subject, "grid": grid, "style_id": style_id, "scope": scope, "bypass": bool(bypass), "end": end,
            "estimate": estimate, "video_estimate": video_estimate if scope == "video" else None, "approved_credits": total or None,
            "step": "sheet", "status": "running", "generation": None, "job": None, "video_job": None, "pack_id": None, "skip": [], "waiting": None, "stop": None,
            "telegram": None, "log": [], "started": round(time.time(), 3), "updated": round(time.time(), 3)}


def steps_of(run: dict) -> list:
    """The steps this run goes through: a run that ends at the animation has no pack and no Telegram step; one that ends at the pack has no Telegram step."""
    steps = STEPS[run["scope"]]
    if run.get("end") == "animation":
        return [s for s in steps if s[0] not in ("pack", "telegram")]
    if run.get("end") == "pack":
        return [s for s in steps if s[0] != "telegram"]
    return steps


def labels(run: dict) -> list:
    """[{id, label, state: done | now | todo}] for the card."""
    steps = steps_of(run)
    order = [k for k, _ in steps]
    cur = order.index(run["step"]) if run["step"] in order else len(order)
    done = run["status"] == "done"
    return [{"id": k, "label": lab, "state": "done" if done or i < cur else "now" if i == cur else "todo"} for i, (k, lab) in enumerate(steps)]


def _log(run: dict, text: str) -> None:
    run["log"].append({"ts": round(time.time(), 3), "text": text})
    run["log"] = run["log"][-40:]


def _stop(run: dict, why: str, chips: list | None = None, kind: str = "rejection") -> dict:
    run.update(status="stopped", stop={"why": why, "kind": kind, "chips": chips or []}, updated=round(time.time(), 3))
    _log(run, "stopped: " + why)
    return run


def _wait(run: dict, why: str, action: str, label: str) -> dict:
    run.update(status="waiting", waiting={"why": why, "chips": [{"label": label, "action": action}, {"label": "Stop", "action": "creator_stop"}]}, updated=round(time.time(), 3))
    _log(run, "waiting: " + why)
    return run


def _go(run: dict, step: str, text: str = "") -> None:
    run.update(step=step, status="running", stop=None, waiting=None, updated=round(time.time(), 3))
    if text:
        _log(run, text)


def _stop_blocked(tools, run: dict, gid: str, kind: str, bad: list, why_of) -> dict:
    """A Python block stopped the run. **A judgement call is never a dead end (CLAUDE.md rule 10):** every blocked sticker Python may allow
    gets `Use it anyway`; a technical block (Telegram's own limits) or a cell with no picture cannot be allowed and says so in words. Either way the
    picture is in the card the stop message carries, and dropping the sticker stays one click away."""
    idx = [s["index"] for s in bad]
    nums = ", ".join(f"S{i}" for i in idx)
    can = [i for i in (tools.allowable(gid, kind, True) or []) if i in idx]
    final = [i for i in idx if i not in can]
    reason = why_of(bad[0]) if len(bad) == 1 else "; ".join(why_of(s) for s in bad[:4])
    what = "a judgement call, so you can use it anyway" if can else ("final: Telegram's own limits or nothing was cut, so it cannot be allowed" if final else "final")
    chips = []
    if can:
        chips.append({"label": f"Use it anyway ({', '.join('S%d' % i for i in can)})", "action": "creator_allow", "indexes": can, "kind": kind})
    chips.append({"label": f"Continue without {nums}", "action": "creator_skip", "indexes": idx})
    chips.append({"label": "Stop", "action": "creator_stop"})
    return _stop(run, f"Python blocked {nums} ({reason}). {what}.", chips)


def _run_allows(tools, run: dict) -> dict:
    """Do the permission the person just gave: `tools.allow` cuts the allowed stickers again in the background (free). Returns what happened."""
    p = run.pop("pending_allow", None)
    if not p:
        return {}
    try:
        r = tools.allow(run["generation"], p["indexes"], p.get("kind", "still"), bool(p.get("allow", True)))
    except Exception as e:                                    # the engine refused with its own words: the run stops and says them, never a crash
        return _stop(run, str(e), [{"label": "Stop", "action": "creator_stop"}], "error")
    run["allow"] = {**(run.get("allow") or {}), p.get("kind", "still"): {"indexes": p["indexes"], "allow": bool(p.get("allow", True)), "done": r.get("done", [])}}
    if p.get("kind") == "video_sheet":
        run["awaiting_sheet_allow"] = True
    _log(run, f"you allowed {', '.join(str(i) if p.get('kind') == 'video_sheet' else 'S%d' % i for i in p['indexes'])} ({p.get('kind', 'still')}): applying the permission")
    return run


def _stop_sheet(tools, run, card):
    info = (card.get("allow") or {}).get("video_sheet") or {}
    can = info.get("can") or []
    why = info.get("why") or {}
    if not why:
        return False
    chips = ([{"label": "Use it anyway · free", "action": "creator_allow_sheet", "indexes": can, "kind": "video_sheet"}] if can else [])
    chips.append({"label": "Stop", "action": "creator_stop"})
    _stop(run, "Python blocked the video sheet (" + "; ".join(f"{aid}: {reason}" for aid, reason in why.items()) + "). " +
          ("It is a judgement call: use it anyway without buying another video." if can else "; ".join(info.get("final", {}).values())), chips, "video_sheet")
    return True


def advance(tools, run: dict, vision_allowed: bool, telegram_ready, checkpoint=None) -> dict:
    """Advance possible steps; checkpoint(run) persists before/after work. Ordinary tool failures stop the run; persistence failures may raise."""
    for _ in range(40):                                            # a bound: one call never loops for ever
        if run["status"] in ("done", "stopped", "failed") or run["status"] == "waiting":
            return run
        if checkpoint:
            checkpoint(run)
        if run.get("awaiting_sheet_allow"):
            if getattr(tools, "processing", lambda: False)():
                return run
            run.pop("awaiting_sheet_allow", None)
        if run.get("pending_allow"):                              # a permission the person gave: do it, then judge what came back
            _run_allows(tools, run)
            if checkpoint:
                checkpoint(run)
            if run["status"] != "running" or run.get("awaiting_sheet_allow"):
                return run
        before = (run["step"], run["status"])
        try:
            _step(tools, run, vision_allowed, telegram_ready, checkpoint)
        except Exception as e:                                     # an engine error is a stop with its words, not a crash of the server
            code = getattr(e, "code", 500)
            if code < 500 and run.get("generation"):
                try:
                    if _stop_sheet(tools, run, tools.generation(run["generation"])):
                        if checkpoint:
                            checkpoint(run)
                        return run
                except Exception:
                    pass
            _stop(run, f"{e}" if code < 500 else f"something went wrong ({type(e).__name__}); nothing more was spent", [{"label": "Stop", "action": "creator_stop"}], "error")
            run["status"] = "failed" if code >= 500 else "stopped"
            if checkpoint:
                checkpoint(run)
            return run
        if checkpoint:
            checkpoint(run)
        if (run["step"], run["status"]) == before:                 # nothing moved: it is waiting for something outside (a job, an animation)
            return run
    return run


def _ready(card: dict) -> list:
    return [s for s in card["stickers"] if s["status"] == "READY"]


def _step(tools, run, vision_allowed, telegram_ready, checkpoint=None):
    step, gid = run["step"], run["generation"]
    if step == "sheet":
        if run["generation"]:
            return _go(run, "cut")
        j = tools.job(run["job"])
        if j.get("status") in ("FAILED", "TIMEOUT"):
            return _stop(run, f"the sheet could not be made: {j.get('error') or j.get('status')}. Nothing more was spent.", [{"label": "Stop", "action": "creator_stop"}], "error")
        if j.get("generation"):
            run["generation"] = j["generation"]
            _go(run, "cut", f"the sheet arrived as {j['generation']}")
        return
    card = tools.generation(gid)
    if step == "cut":
        if card.get("problem"):
            p = card["problem"]
            return _stop(run, f"{p['title']}. {p['why']} The sheet was paid for; a new one usually fixes it.",
                         [{"label": "Try the sheet again", "action": "retry_sheet", "generation": gid}, {"label": "Stop", "action": "creator_stop"}])
        if not card["stickers"] or any(s["status"] == "PENDING" for s in card["stickers"]):
            return
        failed = [s for s in card["stickers"] if s["status"] == "FAILED" and s["index"] not in run["skip"]]
        if failed:
            return _stop_blocked(tools, run, gid, "still", failed, lambda n: f"S{n['index']}: {n.get('reason') or 'blocked by Python'}")
        if not _ready(card):
            return _stop(run, "no sticker came out of this sheet", [{"label": "Stop", "action": "creator_stop"}], "error")
        return _go(run, "look", f"{len(_ready(card))} stickers cut and checked")
    if step == "look":
        if not vision_allowed:
            return _go(run, "approve", "the vision check was skipped (AI vision is not allowed in this chat)")
        r = tools.judge(gid)
        bad = [i for i in r.get("rejected", []) if i not in run["skip"]]
        if bad:
            nums = ", ".join(f"S{i}" for i in bad)
            return _stop(run, f"the vision model would reject {nums}. It only advises, so you decide.",
                         [{"label": f"Continue without {nums}", "action": "creator_skip", "indexes": bad}, {"label": "Continue with them", "action": "creator_force"},
                          {"label": "Stop", "action": "creator_stop"}])
        return _go(run, "approve", f"the vision model found nothing to reject ({len(r.get('approved', []))} looked at)")
    if step == "approve":
        if not run["bypass"] and not run.get("g2_ok"):
            return _wait(run, "the stickers are ready for your approval", "creator_go", "Approve and continue")
        ready = [s["index"] for s in _ready(card) if s["index"] not in run["skip"] and s.get("still") == "PENDING"]
        dropped = [i for i in run["skip"] if any(s["index"] == i and s["status"] == "READY" and s.get("still") == "PENDING" for s in card["stickers"])]
        if dropped:
            tools.review(gid, "REJECT", dropped, "dropped from the creator run by you")
        if ready:
            tools.review(gid, "APPROVE", ready, NOTE)
        return _go(run, "video" if run["scope"] == "video" else "pack", f"approved {len(ready)} stickers")
    if step == "video":
        if not run["video_job"]:
            existing = getattr(tools, "creator_job", lambda rid: None)(run["id"])
            if existing:
                run["video_job"] = existing["id"]
        if _stop_sheet(tools, run, card):
            return
        if not run["video_job"]:
            est = tools.estimate("video")
            budget = run.get("video_estimate")
            if est is not None and budget is not None and est > budget * VIDEO_OVER + 0.5:
                return _stop(run, f"the animation now costs about {est:g} credits, more than the {budget:g} you were shown. Nothing was spent on it.",
                             [{"label": f"Animate for about {est:g}", "action": "creator_force_video"}, {"label": "Stop", "action": "creator_stop"}], "price")
            def created(job):
                run["video_job"] = job["id"]
                if checkpoint:
                    checkpoint(run)

            r = tools.animate(gid, creator_run=run["id"], on_job=created)
            run["video_job"] = r.get("job")
            _log(run, "animation started" + (f" (about {r['estimate']:g} credits)" if r.get("estimate") else ""))
            return
        j = tools.job(run["video_job"])
        if j.get("status") in ("FAILED", "TIMEOUT"):
            return _stop(run, f"the animation could not be made: {j.get('error') or j.get('status')}. Nothing more was spent.", [{"label": "Stop", "action": "creator_stop"}], "error")
        sts = [s for s in card["stickers"] if s["status"] == "READY" and s["index"] not in run["skip"]]
        if j.get("status") != "DONE" or not sts or any(s.get("anim_status") in (None, "NOT_REQUESTED", "PENDING", "RUNNING", "PROCESSING") for s in sts):
            return
        bad = [s for s in sts if s.get("anim_status") != "READY"]
        if bad:
            return _stop_blocked(tools, run, gid, "animation", bad, lambda n: f"S{n['index']}: {n.get('anim_reason') or 'blocked by Python'}")
        return _go(run, "approve_anim", "the animations arrived and passed Python's checks")
    if step == "approve_anim":
        if not run["bypass"] and not run.get("g4_ok"):
            return _wait(run, "the animations are ready for your approval", "creator_go", "Approve and continue")
        ready = [s["index"] for s in card["stickers"] if s.get("anim_status") == "READY" and s["index"] not in run["skip"] and s.get("anim") == "PENDING"]
        if ready:
            tools.review(gid, "APPROVE", ready, NOTE, gate="anim")
        return _go(run, "pack", f"approved {len(ready)} animations")
    if step == "pack":
        if run.get("end") == "animation":                          # the chat's stage is Animation: the run ends here, nothing is packed or sent
            run.update(status="done", step="done", updated=round(time.time(), 3))
            _log(run, "done: the animations are approved (the stage ends at the animation; no pack, no Telegram)")
            return
        pack_name = run.get("pack_name") or run["subject"]
        r = tools.pack_add(gid, pack_name)
        run["pack_id"] = r["pack_id"]
        if run.get("end") == "pack":                             # `create --auto`: the pack is the product, no Telegram
            run.update(status="done", step="done", updated=round(time.time(), 3))
            _log(run, f"done: {r['added']} stickers are in the pack '{pack_name}' (no Telegram send)")
            return
        return _go(run, "telegram", f"{r['added']} stickers are in the pack '{pack_name}'")
    if step == "telegram":
        ok, reason = telegram_ready()
        if not ok:
            return _stop(run, reason + " The pack is ready in the library; connect Telegram in Settings and continue.",
                         [{"label": "Try again", "action": "creator_go"}, {"label": "Stop", "action": "creator_stop"}], "telegram")
        rep = tools.telegram_send(run["pack_id"])
        run["telegram"] = rep
        sent = "sent: " + ", ".join(s["link"] for s in rep.get("sets", []))
        run.update(status="done", step="done", updated=round(time.time(), 3))
        _log(run, sent)
        return


def resume(run: dict, action: str, indexes: list | None = None) -> dict:
    """The person's button: continue (an approval at a gate), skip some stickers, accept something the creator stopped for, or stop."""
    if action == "creator_stop":
        run.update(status="stopped", waiting=None, stop={"why": "stopped by you", "kind": "stopped", "chips": []}, updated=round(time.time(), 3))
        _log(run, "stopped by you")
        return run
    if run["status"] not in ("stopped", "waiting"):
        return run
    if action == "creator_skip":
        run["skip"] = sorted(set(run["skip"]) | set(int(i) for i in (indexes or [])) | set(run.get("failed_hint") or []))
    if action == "creator_go":
        if run["step"] == "approve":
            run["g2_ok"] = True
        if run["step"] == "approve_anim":
            run["g4_ok"] = True
    if action == "creator_force":
        run["skip"] = sorted(set(run["skip"]))
        run["step_forced"] = "look"
    if action == "creator_allow_sheet":
        run["pending_allow"] = {"indexes": sorted(set(indexes or [])), "kind": "video_sheet", "allow": True}
    if action in ("creator_allow", "creator_unallow"):
        kind = "animation" if run.get("step") == "video" else "still"
        run["pending_allow"] = {"indexes": sorted({int(i) for i in (indexes or run.get("failed_hint") or [])}), "kind": kind, "allow": action == "creator_allow"}
        run["failed_hint"] = []                      # allowing is not skipping: those stickers must come back into the set, so the hint is spent
        run["step"] = "cut" if kind == "still" and run.get("step") != "approve" else run.get("step", "cut")
    if action == "creator_force_video":
        run["video_estimate"] = None                            # the person saw the new price on the button and accepted it
    run.update(status="running", stop=None, waiting=None, updated=round(time.time(), 3))
    if action == "creator_force" and run["step"] == "look":
        run["step"] = "approve"
    _log(run, f"you chose: {action}")
    return run
