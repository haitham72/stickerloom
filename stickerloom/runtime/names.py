"""ONE naming convention for every media file stickerloom makes.

    {media}-{subject}-{action}[-{pack}]-{YYYYMMDDTHHMMSS}-{hash6}.{ext}

    img-falcon_stickers-open_arms-20261002T135100-a3f9c1.png
    vid-falcon_stickers-open_arms-20261002T135100-a3f9c1.webm
    img-my_dog-wave-summer_pack-20261003T081512-5be201.png        (a file made inside the library: the pack is stamped at creation)

Readable and traceable: what it is (media, subject, action), when it was made (UTC), and a short fingerprint that makes the name
non-repeatable even when the same subject and action are made twice in the same second (the fingerprint also carries the generation id,
so two batches never collide). Fields are separated by `-`; inside a field words are joined by `_`, so a name always splits back into its
parts (`parse`). The pack is optional on purpose: it is only known for files born in the library, and a sticker can later move to another
pack, so the pack of a generation's sticker lives in the library's data, never in its file name.

Names made before this convention (`img-005-barbie_love-barbie_blow_kiss`: media, the generation number, the task, the key) are never renamed;
`parse` reads both and says which (`legacy`). The still and its animation share one stem, only the media letters and the extension differ."""
from __future__ import annotations

import hashlib
import re
import time

SUBJECT_MAX = 28
ACTION_MAX = 36
PACK_MAX = 24
MEDIA = ("img", "vid")
STAMP = re.compile(r"^\d{8}T\d{6}$")
FINGERPRINT = re.compile(r"^[0-9a-f]{6,12}$")
_LEGACY = re.compile(r"^(?P<media>img|vid)-(?P<n>\d{3,})-(?P<subject>[a-z0-9_]+)-(?P<action>.+)$")


def slug(text: str, limit: int = 40) -> str:
    """lower snake_case ascii; empty when nothing usable is left."""
    s = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    return s[:limit].strip("_")


def stamp(ts: float | None = None) -> str:
    """UTC, to the second, sortable: 20261002T135100."""
    return time.strftime("%Y%m%dT%H%M%S", time.gmtime(time.time() if ts is None else float(ts)))


def action_of(subject: str, key: str) -> str:
    """The sticker's key without the subject's own words in front ('falcon_stickers_open_arms' -> 'open_arms'); the whole key if nothing would be left."""
    words, own = slug(key, 80).split("_"), set(slug(subject, 80).split("_"))
    i = 0
    while i < len(words) - 1 and words[i] in own:
        i += 1
    return "_".join(words[i:]) or slug(key, ACTION_MAX) or "sticker"


def fingerprint(*parts, size: int = 6) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:size]


def build(media: str, subject: str, action: str, *, pack: str | None = None, when: float | None = None, seed="", size: int = 6) -> str:
    """The file stem (no extension). `seed` is whatever makes this file unique inside its moment (the generation id and the sticker key; for a library file
    the sticker id): the same inputs always give the same name, so a regenerated plan never renames a file."""
    if media not in MEDIA:
        raise ValueError(f"media must be one of {MEDIA}")
    subj = slug(subject, SUBJECT_MAX) or "sticker"
    act = slug(action, ACTION_MAX) or "sticker"
    stp = stamp(when)
    parts = [media, subj, act]
    if pack:
        parts.append(slug(pack, PACK_MAX) or "pack")
    parts += [stp, fingerprint(subj, act, pack or "", stp, seed, size=size)]
    return "-".join(parts)


def as_media(name: str, media: str) -> str:
    """The same stem with other media letters: img-... <-> vid-... (a name without a recognised prefix is returned unchanged)."""
    return media + name[3:] if name[:4] in ("img-", "vid-") else name


def parse(name: str) -> dict | None:
    """-> {media, subject, action, pack, stamp, hash, legacy}; None when the stem fits neither convention. The extension, if present, is dropped."""
    stem = re.sub(r"\.[a-z0-9]{2,5}$", "", str(name or ""))
    f = stem.split("-")
    if len(f) in (5, 6) and f[0] in MEDIA and STAMP.match(f[-2]) and FINGERPRINT.match(f[-1]):
        return {"media": f[0], "subject": f[1], "action": f[2], "pack": f[3] if len(f) == 6 else None, "stamp": f[-2], "hash": f[-1], "legacy": False}
    m = _LEGACY.match(stem)
    if m:
        return {"media": m["media"], "subject": m["subject"], "action": m["action"], "pack": None, "stamp": None, "hash": None, "legacy": True,
                "generation": int(m["n"])}
    return None


FOLDER = re.compile(r"^(?P<id>[GJ]\d{3,})(?:-(?P<label>[A-Za-z0-9_-]+))?$")


def folder(ident: str, *words, when: float | None = None) -> str:
    """A batch or job folder on disk: the id, then what it is and when, so a folder list reads by itself:
    `G111-dog_as_banana-20261005T093012`, `J058-dog_as_banana-sheet`. The id stays the address everywhere else (API, URLs, chat, Postgres);
    only the disk carries the label, and `folder_id` reads the id back. Batches and jobs made before keep their bare `G110` / `J057`, never renamed."""
    parts = [ident] + [s for s in (slug(w, SUBJECT_MAX) for w in words) if s]
    if when is not None:
        parts.append(stamp(when))
    return "-".join(parts)


def folder_id(name: str) -> str | None:
    """'G111-dog_as_banana-20261005T093012' -> 'G111', 'G110' -> 'G110'; None for anything else."""
    m = FOLDER.match(str(name or ""))
    return m["id"] if m else None


def readable(name: str) -> str:
    """'Open arms' for a name of either convention, '' if it fits neither."""
    p = parse(name)
    if not p:
        return ""
    t = p["action"].replace("_", " ")
    return t[:1].upper() + t[1:]
