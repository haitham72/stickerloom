"""PhaseDirSource: discovers the prepared inputs by the inputs/ naming convention (read-only, never renames).

  Images_gen/img-NNN-<subject>/<file>.jpg            one folder per variant; NNN is the variant's folder number
  videos_gen/vid-NNN-<subject>/<file>.mp4            the 3x3 video of the SAME NNN
  videos_gen/vid-NNN-<subject>/slices/{quicktime,webm}/<anything> (n).<ext>    pre-sliced clips, n = grid cell 1..9
Optional hand-written prompts: "<sheet file name>.json" beside the sheet, or "<folder>/prompts.json".
All variants of one subject are ordered by folder number (then by take number inside a folder)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import os

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VID_EXT = {".mp4", ".mov", ".webm", ".mkv"}
CLIP_EXT = {".mov": "mov", ".webm": "webm", ".mp4": "mp4"}
DIR_RE = re.compile(r"^(img|vid)-(\d{3})-(.+)$")
TAKE_RE = re.compile(r"\((\d+)\)")
CLIP_RE = re.compile(r"\((\d+)\)$")


@dataclass
class Pick:
    subject: str            # teddy_bear
    subject_id: str         # folder number of this variant, e.g. 002
    variant: int            # 1-based over ALL folders of the subject
    n_variants: int
    sheet: Path
    video: Path | None      # the 3x3 mp4, if any
    plan: Path | None = None
    pairing: str = "folder"  # folder = same NNN folder number | number = same (K) | order = GUESSED by position | none
    clips: dict = field(default_factory=dict)   # {cell: {"mov": Path, "webm": Path}} pre-sliced clips
    clips_dup_of: str | None = None              # folder number whose clips these are copies of (then ignored)

    @property
    def has_video(self) -> bool:
        return bool(self.video or self.clips)


def take_of(f: Path) -> int | None:
    m = TAKE_RE.search(f.stem)
    return int(m[1]) if m else None


def _files(folder: Path | None, exts) -> list[Path]:
    if not folder:
        return []
    fs = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in exts]
    return sorted(fs, key=lambda f: (take_of(f) or 0, f.name))


def _plan_for(sheet: Path) -> Path | None:
    for c in (sheet.with_suffix(".json"), sheet.parent / "prompts.json"):
        if c.is_file():
            return c
    return None


def _clips_for(vdir: Path | None) -> dict:
    out: dict = {}
    root = vdir / "slices" if vdir else None
    if not root or not root.is_dir():
        return out
    for f in sorted(root.rglob("*")):
        m = CLIP_RE.search(f.stem)
        if f.is_file() and f.suffix.lower() in CLIP_EXT and m and 1 <= int(m[1]) <= 9:
            out.setdefault(int(m[1]), {}).setdefault(CLIP_EXT[f.suffix.lower()], f)
    return out


def _clip_fingerprint(clips: dict) -> tuple:
    """File sizes per cell/format (stat only). A clip set copied from another variant has the same fingerprint."""
    return tuple(sorted((n, f, p.stat().st_size) for n, d in clips.items() for f, p in d.items()))


def scan(root: Path) -> dict[str, list[Pick]]:
    """{subject: [Pick per variant]} - filenames only, never opens media."""
    folders: dict = {}
    for kind, sub in (("img", "Images_gen"), ("vid", "videos_gen")):
        base = root / sub
        if base.is_dir():
            for d in sorted(base.iterdir()):
                m = DIR_RE.match(d.name)
                if d.is_dir() and m and m[1] == kind:
                    folders.setdefault((m[3], m[2]), {})[kind] = d
    out: dict[str, list[Pick]] = {}
    for (subject, sid), v in sorted(folders.items()):
        imgs, vids, clips = _files(v.get("img"), IMG_EXT), _files(v.get("vid"), VID_EXT), _clips_for(v.get("vid"))
        vby = {take_of(x): x for x in vids if take_of(x) is not None}
        for n, img in enumerate(imgs):
            if len(imgs) == 1 and len(vids) <= 1:
                video, pairing = (vids[0] if vids else None), ("folder" if (vids or clips) else "none")
            elif vby and set(vby) <= {take_of(i) for i in imgs}:
                video, pairing = vby.get(take_of(img)), "number"
            else:
                video, pairing = (vids[n] if n < len(vids) else None), ("order" if vids else "none")
            out.setdefault(subject, []).append(Pick(subject, sid, 0, 0, img, video, _plan_for(img), pairing, clips))
    for picks in out.values():
        seen: dict = {}
        for i, p in enumerate(picks):
            p.variant, p.n_variants = i + 1, len(picks)
            if p.clips:
                fp = _clip_fingerprint(p.clips)
                if fp in seen and seen[fp] != p.subject_id:
                    # copies of another variant's clips would animate the wrong stickers: use this variant's own mp4
                    p.clips, p.clips_dup_of = {}, seen[fp]
                else:
                    seen.setdefault(fp, p.subject_id)
    return out


def grid_of(g) -> tuple[int, int] | None:
    """"2x2" / [2, 2] / (2, 2) -> (2, 2); None for anything else."""
    if isinstance(g, str):
        m = re.fullmatch(r"\s*(\d)\s*[x×]\s*(\d)\s*", g)
        return (int(m.group(1)), int(m.group(2))) if m else None
    if isinstance(g, (list, tuple)) and len(g) == 2 and all(isinstance(x, int) for x in g):
        return (int(g[0]), int(g[1]))
    return None


_GRIDS: dict = {}


def sheet_grid(sheet: Path) -> tuple[int, int] | None:
    """The grid a prepared sheet is drawn in (rows, cols), measured from its gutters on a green or a blue screen, once per file version (cached by
    path and mtime). A sheet that cannot be measured counts as 3x3, as pipeline.start cuts it (the watch folders' default)."""
    try:
        key = (str(sheet), Path(sheet).stat().st_mtime)
    except OSError:
        return None
    if key not in _GRIDS:
        try:
            import numpy as np
            from PIL import Image
            from ..engine.grid import detect_grid
            im = Image.open(sheet).convert("RGB")
            im.thumbnail((1024, 1024))
            from ..generation import prompter
            arr, got = np.asarray(im), None
            for chroma in ("green", "blue"):
                g = tuple(int(x) for x in detect_grid(arr, chroma))
                if g in prompter.GRIDS and g != (1, 1):            # 1x1 = not measured (a single cell is never a prepared sheet)
                    got = g
                    break
            _GRIDS[key] = got or (3, 3)
        except Exception:
            _GRIDS[key] = (3, 3)
    return _GRIDS[key]


def find(root: Path, prompt: str, variant: int = 1, grid=None) -> Pick | None:
    subject = match_subject(root, prompt, grid)
    if not subject:
        return None
    picks = scan(root)[subject]
    return picks[min(max(variant, 1), len(picks)) - 1]


STOPWORDS = {"generic"}         # words of a folder name that never match on their own ("generic emojis" is matched by "emoji")


def words_of(text: str) -> set[str]:
    """Whole singularised words of a request or a folder name (`emojis` and `emoji` are the same word)."""
    out = set()
    for w in re.findall(r"[a-z0-9]+", str(text or "").lower()):
        out.add(w)
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            out.add(w[:-1])
    return out


def match_subject(root: Path, prompt: str, grid=None) -> str | None:
    """The prepared subject a request names, or None. A whole signature word of the folder name (not a shared
    filler word) must appear as a whole word of the request: "teddy bear", "teddy" and "emoji"/"emojis" all match,
    and so do the near-misses "bear in a teddy costume" and "emoji keyboard". Best (most shared words) wins; ties keep scan order. `grid` ("2x2"): only a subject whose sheet
    is drawn in that grid can serve the request; None = any grid."""
    want, g = words_of(prompt), grid_of(grid)
    best, best_score = None, 0
    for subject, picks in scan(root).items():
        sig = {w for w in words_of(subject.replace("_", " ")) if w not in STOPWORDS}
        score = len(sig & want)
        if score > best_score and (g is None or any(sheet_grid(p.sheet) == g for p in picks)):
            best, best_score = subject, score
    return best


def _pref_file(out: Path) -> Path:
    return Path(out) / "prepared.json"


def prefer_prepared(out: Path) -> bool:
    """Serve a matching request from the watch folder instead of a paid call (default on). `STICKERLOOM_PREFER_PREPARED`
    wins when it is set (0/no/off/false = off, anything else = on); else the owner's Settings switch
    (`out/prepared.json`, written by POST /api/prepared/setting); else on."""
    raw = str(os.environ.get("STICKERLOOM_PREFER_PREPARED") or "").strip().lower()
    if raw:
        return raw not in ("0", "no", "off", "false")
    try:
        import json
        d = json.loads((_pref_file(out)).read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("prefer"), bool):
            return d["prefer"]
    except (OSError, ValueError):
        pass
    return True


def set_prefer_prepared(out: Path, prefer: bool) -> bool:
    """The owner's Settings switch (rule 6: it is read by every prepared decision). Returns what was stored."""
    import json
    from ..runtime import atomic
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    atomic.write_text(_pref_file(out), json.dumps({"prefer": bool(prefer)}))
    return bool(prefer)


def variant_of(root: Path, subject: str, variant: int) -> Pick | None:
    picks = scan(root).get(subject, [])
    return picks[variant - 1] if 0 < variant <= len(picks) else None


def known_subjects(root: Path) -> list[str]:
    return sorted(scan(root))
