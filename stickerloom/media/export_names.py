"""Export-as-ZIP display names:

    {emoji}-{pack_slug}-{multi_action_tag}-{sNN}-{G###}-{date}[.{ext}]

e.g. `🤣-falcon-laugh_rofl_lmao-s08-G112-20261008.webm`. `build` makes the stem (no extension);
`parse` reads it back right-to-left on the fixed tail (`sNN`, `G###`, date) and is best-effort on the
hyphenated middle (the manifest stays authoritative). Pure: stdlib only."""
from __future__ import annotations

import re
import time

from ..generation import actions as _actions
from ..runtime import names as _names

DEFAULT_EMOJI = "🙂"
_STEM_TAIL = re.compile(r"^(?P<mid>.+)-s(?P<index>\d{1,2})-G(?P<gid>\d{3,})-(?P<date>\d{8})$")
_EXT = re.compile(r"\.[a-z0-9]{2,5}$")


def slug_snake(text: str | None, limit: int = 40) -> str:
    """lowercase snake_case ascii (`Royal Falcon` -> `royal_falcon`). The filename's `-` separates identifiers,
    so a slug never contains one; `_` connects words inside an identifier."""
    return _names.slug(text, limit)


def datestamp(ts=None) -> str:
    """UTC YYYYMMDD of a timestamp (a generation's creation time: frozen per batch, so re-exports keep it)."""
    try:
        v = float(ts)
    except (TypeError, ValueError):
        v = time.time()
    return time.strftime("%Y%m%d", time.gmtime(v))


from ..generation.emoji import first_emoji, split_emoji, valid as valid_emoji


def _clean_tag(tag: str | None) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]+", "", str(tag or "").lower())).strip("_")


def build(*, emoji: str | None, slug: str | None, tag: str | None, index: int, gid: int, date: str) -> str:
    """The file stem (no extension). Never raises on ordinary input: every field has a deterministic fallback."""
    e = first_emoji(emoji)
    e = e if valid_emoji(e) else DEFAULT_EMOJI
    s = slug_snake(slug) or "pack"
    t = _clean_tag(tag) or "sticker"
    return f"{e}-{s}-{t}-s{max(1, int(index)):02d}-G{max(0, int(gid)):03d}-{re.sub(r'[^0-9]', '', str(date or ''))[:8] or '00000000'}"


def parse(name: str | None) -> dict | None:
    """`{emoji, slug, tag, index, gid, date}` or `None` when the stem fits no export shape. The middle is positional
    (`{slug}-{tag}`: a slug never holds `-`, so exactly two segments). Older hyphenated slugs parse best-effort:
    the tag is then the trailing segment when it holds `_`, else the trailing segment as-is (the manifest stays
    authoritative)."""
    stem = _EXT.sub("", str(name or ""))
    head, sep, tail = stem.partition("-")
    if not sep or not head:
        return None
    m = _STEM_TAIL.match(tail)
    if not m:
        return None
    parts = m["mid"].split("-")
    if len(parts) == 2:
        slug, tag = parts
    elif len(parts) > 2 and "_" in parts[-1]:
        tag, slug = parts[-1], "-".join(parts[:-1])
    else:
        slug, tag = ("-".join(parts[:-1]), parts[-1]) if len(parts) > 1 else ("", parts[0])
    if not slug or not tag:
        return None
    return {"emoji": head, "slug": slug, "tag": tag, "index": int(m["index"]), "gid": int(m["gid"]), "date": m["date"]}


def describe(*, emoji: str | None, key: str | None = None, tags: list | tuple | None = None,
             slug: str | None, index: int, gid: int, date: str, exclude: list | tuple | None = None, action: str | None = None) -> dict:
    """Resolved export fields for one asset: `{emoji, emojis, slug, tag, tokens, action, unresolved, index, gid,
    date, stem}`. A bank hit gives `action` + its aliases; otherwise the tag falls back to the key/tags words minus
    the subject words (`exclude`), repeats collapsed, and `unresolved` is True (a human picks from the bank later;
    nothing is silently called an action)."""
    resolution = _actions.resolve(key, tags, emoji=emoji, action=action)
    hits = resolution["hits"]
    if hits:
        action = hits[0][0]
        tokens = []
        for tok, aliases in hits:                                             # primary first, then whole-tag hits (bus+happy+laugh)
            for t in [tok, *aliases]:                                         # bank tokens may hold "-" (eye-roll): not a valid in-tag char
                n = re.sub(r"[^a-z0-9]+", "", t.lower())
                if n and n not in tokens:
                    tokens.append(n)
        seg, unresolved = "_".join(tokens), False
    else:
        seg = _actions.fallback_tag(key, list(tags) if tags else None, exclude)
        tokens, action, aliases, unresolved = seg.split("_"), seg.split("_")[0], [], True
    reason = resolution["reason"]
    e = first_emoji(emoji)
    if not emoji:
        e = _actions._BANK_OF[hits[0][0]]["emoji"] if hits else DEFAULT_EMOJI
    elif not valid_emoji(e):
        reason, e = "invalid_emoji", DEFAULT_EMOJI
    associations = list(dict.fromkeys(g for g in split_emoji(emoji) if valid_emoji(g)))
    associations = list(dict.fromkeys([e, *associations]))
    unresolved = unresolved or bool(reason)
    stem = build(emoji=e, slug=slug, tag=seg, index=index, gid=gid, date=date)
    return {"emoji": e, "emojis": associations, "slug": slug_snake(slug) or "pack",
            "tag": _clean_tag(seg) or "sticker", "tokens": tokens, "action": action,
            "unresolved": unresolved, "resolution_reason": reason, "candidates": resolution["candidates"],
            "bank_version": _actions.BANK_VERSION, "index": max(1, int(index)), "gid": max(0, int(gid)), "date": date, "stem": stem}
