"""Better names for stickers, proposed after looking at the picture (2026-10-02).

Once the person has allowed AI vision (`settings.allow_vlm`), the model that already writes a one-sentence caption per sticker (`transcribe.captions_for`) is asked, in ONE more
text call for the whole batch, whether each current name still fits what the picture shows ("eid mubarak greetings in love" on a boy holding a heart), and for a short better
name only where it does not. The result is a PROPOSAL stored on the sticker (`title_proposal`); nothing changes until the person applies it (`pipeline.set_titles`,
a history line `naming`). The file name, the key and the search fields are never touched: a title is only what people read.

Consent is the caption step's (`consent.require`): nothing is sent to a model without the person's yes, and a caption already stored is reused for free."""
from __future__ import annotations

from pathlib import Path

from ..flow import pipeline as pl
from . import transcribe

MAX_TITLE = 60


def readable_current(s: dict) -> str:
    """The name a person sees now: the applied title, else the sticker's key in words."""
    return " ".join(str(s.get("title") or str(s.get("key") or "").replace("_", " ")).split())


def propose(out, gid, *, allowed, ask) -> list[dict]:
    """[{index, current, caption, fits, name}] for every READY sticker of the batch, in sheet order.
    `ask(items) -> {index: {"fits": bool, "name": str}} | None` is the model call (`Brain.name_check`); None (no model, or an unusable answer) means nothing is proposed and every
    name is kept. A proposal is stored on the sticker only when the name does not fit and the new one is non-empty and different."""
    out = Path(out)
    gid_n = int(str(gid).upper().lstrip("G"))
    caps = transcribe.captions_for(out, gid_n, allowed=allowed)
    res = pl.read_result(out, gid_n)
    by = {s["index"]: s for s in res.get("stickers") or []}
    items = [{"index": c.index, "current": readable_current(by[c.index]), "caption": c.caption} for c in caps if c.caption and c.index in by]
    if not items:
        return []
    answer = ask(items) or {}
    rows, store = [], {}
    for it in items:
        a = answer.get(it["index"]) or {}
        name = " ".join(str(a.get("name") or "").split())[:MAX_TITLE]
        fits = bool(a.get("fits", True)) or not name or name.lower() == it["current"].lower()
        rows.append({**it, "fits": fits, "name": None if fits else name})
        if not fits:
            store[it["index"]] = {"name": name, "caption": it["caption"]}
    if store:
        _store(out, gid_n, store)
    return rows


@pl.serialized
def _store(out: Path, gid_n: int, proposals: dict) -> None:
    res = pl.read_result(out, gid_n)
    for s in res.get("stickers") or []:
        p = proposals.get(s["index"])
        if p:
            s["title_proposal"] = {**p, "ts": round(__import__("time").time(), 3)}
    pl.write_result(out, gid_n, res)


def pending(out, gid) -> dict:
    """{index: proposed name} of the proposals still waiting for a yes."""
    res = pl.read_result(Path(out), int(str(gid).upper().lstrip("G")))
    return {s["index"]: s["title_proposal"]["name"] for s in res.get("stickers") or [] if (s.get("title_proposal") or {}).get("name")}


def apply(out, gid, indexes: list | None = None, actor: str = "human") -> dict:
    """Apply the waiting proposals (all, or only `indexes`) as titles. Returns {index: title}."""
    gid_n = int(str(gid).upper().lstrip("G"))
    waiting = pending(out, gid_n)
    pick = {i: n for i, n in waiting.items() if not indexes or i in {int(x) for x in indexes}}
    return pl.set_titles(Path(out), gid_n, pick, actor=actor, via="AI vision proposal") if pick else {}
