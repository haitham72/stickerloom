"""VisionJudge: a pre-reviewer, never the gate.

Division of labour (strict): Python judges what is CORRECT (dimensions, alpha, bounds, chroma, cut: engine/verify.py, final),
the VLM judges what is GOOD (concept, expression, style, identity, artefacts). The judge never overrides Python and Python never
judges "funny" or "cute". Its verdict is a history line with actor `vlm` at the same gate (`still` before G2, `anim` before G4)
and a copy on the sticker (`judge`); the human still approves or rejects (CLAUDE.md rule 10). There is no auto-approve.

Backend: any OpenAI-compatible vision endpoint through `llm.complete_vision(images=...)`, with one configured fallback on failure: the local LM Studio (default,
free; a thinking model, so local calls get a large token floor) or the cloud. `STICKERLOOM_VISION_BASE_URL`, `STICKERLOOM_VISION_MODEL`,
`STICKERLOOM_VISION_TIMEOUT`, `STICKERLOOM_VISION_MAX_TOKENS`, `STICKERLOOM_VISION_CONCURRENCY` (bare `VISION_*` still works as a legacy
fallback), `STICKERLOOM_VISION_PROVIDER=local|openai|auto`, `STICKERLOOM_VISION_POLICY=FAIL_CLOSED|DETERMINISTIC_ONLY`.
Never trust `response_format` from a local model: the answer is parsed, validated, repaired once, and the raw output is logged
when that fails. Policy when the model is down or keeps answering nonsense: FAIL_CLOSED (default) keeps the sticker READY and marks it
"unjudged"; DETERMINISTIC_ONLY says nothing. Every call is a line in out/model_calls.jsonl; verdicts are cached in Redis
(asset sha256 + model + judge version + the cell's context)."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

JUDGE_VERSION = "judge_v1"
SHEET_VERSION = "sheet_v1"
CACHE_TTL = 30 * 24 * 3600

REASONS = ("DUPLICATE", "WEAK_CONCEPT", "AMBIGUOUS_ACTION", "STYLE_DRIFT", "IDENTITY_DRIFT", "SEVERE_ARTIFACT",
           "POOR_COMPOSITION", "ANIMATION_RISK", "CHROMA_RISK", "MISSING_REQUIRED_ELEMENT", "ANATOMY_ERROR",
           "OBJECT_DEFORMATION", "EMOJI_MISMATCH", "UNWANTED_TEXT")
SCORES = ("concept_match", "emoji_fit", "character_match", "style_match")

SYSTEM = f"""You are the quality reviewer of a sticker studio. You see ONE sticker (a cut-out character on a grey background) and what it was meant to be.
Judge only what is visible: does it show the intended action and feeling, is it clearly readable at small size, is the character consistent with the pack, is it free of artefacts.
You are strict about artefacts and generous about taste: reject only for a real problem.
Reply with ONE JSON object and nothing else:
{{"decision": "APPROVE" or "REJECT", "confidence": 0.0-1.0,
 "scores": {{"concept_match": 0.0-1.0, "emoji_fit": 0.0-1.0, "character_match": 0.0-1.0 or null, "style_match": 0.0-1.0 or null}},
 "reasons": [codes], "suggested_emoji": "one emoji or null", "notes": "one short sentence"}}
Allowed reason codes: {", ".join(REASONS)}.
A REJECT must carry at least one reason code. Use only the codes; never invent one. If a reference sticker is given as the second image, character_match and style_match compare against it; otherwise use null."""

SHEET_SYSTEM = """You check a generated sticker sheet before it is cut. You see ONE image that should show a grid of separate characters on a plain background.
Reply with ONE JSON object and nothing else:
{"ok": true or false, "count": number of separate characters you see, "isolated": true if no two touch or overlap, "missing": [grid positions 1..N that are empty], "duplicated": [position pairs that are the same pose], "notes": "one short sentence"}"""


class JudgeError(Exception):
    pass


@dataclass
class Judgement:
    decision: str                     # APPROVE | REJECT | UNJUDGED
    confidence: float = 0.0
    scores: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)
    suggested_emoji: str | None = None
    notes: str = ""
    model: str = ""
    version: str = JUDGE_VERSION
    error: str | None = None
    cached: bool = False

    @property
    def reject(self) -> bool:
        return self.decision == "REJECT"

    def to_dict(self) -> dict:
        return {"decision": self.decision, "confidence": self.confidence, "scores": self.scores, "reasons": self.reasons,
                "suggested_emoji": self.suggested_emoji, "notes": self.notes, "model": self.model,
                "version": self.version, "error": self.error}

    @classmethod
    def from_dict(cls, d: dict) -> "Judgement":
        return cls(d["decision"], d.get("confidence", 0.0), d.get("scores") or {}, d.get("reasons") or [],
                   d.get("suggested_emoji"), d.get("notes", ""), d.get("model", ""), d.get("version", JUDGE_VERSION), d.get("error"))


# ---- configuration -----------------------------------------------------------------------------------------------------------
def _env(name: str, default):
    from ..services import llm
    llm._load_dotenv()
    if name.startswith("VISION_"):
        # Canonical names are STICKERLOOM_VISION_*; the bare VISION_* spelling
        # is a legacy fallback (pre-release configs and the test suite use it).
        return os.environ.get("STICKERLOOM_" + name, os.environ.get(name, default))
    return os.environ.get(name, default)


def policy() -> str:
    p = str(_env("STICKERLOOM_VISION_POLICY", "FAIL_CLOSED")).upper()
    return p if p in ("FAIL_CLOSED", "DETERMINISTIC_ONLY") else "FAIL_CLOSED"


def target() -> dict:
    """Where the judge calls: {provider, model, base_url}. Local first (free); OpenAI when only a key exists."""
    from ..services import llm
    base = _env("VISION_BASE_URL", "") or None
    from ..runtime import envfile
    want = envfile.choice("STICKERLOOM_VISION_PROVIDER")
    if want == "auto":
        want = "local" if base else llm.provider()
    cloud_model = os.environ.get("STICKERLOOM_VISION_OPENAI_MODEL") or (llm.forced() or {}).get("model") or llm.cloud_model()
    model = _env("VISION_MODEL", "") or ((llm.forced() or {}).get("model") or llm.local_model() if want == "local" else cloud_model)
    return {"provider": want, "model": model, "base_url": base if want == "local" else None}


def status() -> dict:
    t = target()
    return {"configured": t["provider"] != "none", **t, "policy": policy(), "version": JUDGE_VERSION}


# ---- parsing (never trust response_format) -----------------------------------------------------------------------------------
def _first_json(text: str):
    """The first balanced {...} in a model answer, tolerant of code fences, <think> blocks and chatter around it."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    raise ValueError("no JSON object in the answer")


def _score(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"score {v!r} is not a number")
    return round(min(1.0, max(0.0, f)), 3)


def parse_judgement(text: str, model: str = "") -> Judgement:
    d = _first_json(text)
    dec = str(d.get("decision", "")).strip().upper()
    if dec not in ("APPROVE", "REJECT"):
        raise ValueError(f"decision must be APPROVE or REJECT, got {d.get('decision')!r}")
    raw = d.get("reasons") or []
    if not isinstance(raw, list):
        raise ValueError("reasons must be a list")
    reasons = [str(r).strip().upper() for r in raw]
    bad = [r for r in reasons if r not in REASONS]
    if bad:
        raise ValueError(f"unknown reason code(s): {', '.join(bad)}; use only {', '.join(REASONS)}")
    if dec == "REJECT" and not reasons:
        raise ValueError("a REJECT must carry at least one reason code")
    sc = d.get("scores") if isinstance(d.get("scores"), dict) else {}
    scores = {k: _score(sc.get(k)) for k in SCORES}
    emoji = d.get("suggested_emoji")
    emoji = str(emoji).strip() if emoji and str(emoji).strip().lower() not in ("null", "none") else None
    return Judgement(dec, _score(d.get("confidence")) or 0.0, scores, list(dict.fromkeys(reasons)), emoji,
                     str(d.get("notes") or "")[:300], model)


# ---- images ---------------------------------------------------------------------------------------------------------------------
def _flatten(png: bytes, size: int = 512) -> bytes:
    """A transparent sticker is shown on a neutral grey so the model sees the same thing a person does."""
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    im.thumbnail((size, size))
    bg = Image.new("RGBA", im.size, (200, 200, 200, 255))
    bg.alpha_composite(im)
    out = io.BytesIO()
    bg.convert("RGB").save(out, "PNG")
    return out.getvalue()


def anim_strip(webm: Path, frames: int = 4, size: int = 256) -> bytes:
    """Four frames of an animation side by side (start to end), for ANIMATION_RISK and identity drift. Decoded by ffmpeg; no file is kept."""
    import numpy as np
    from PIL import Image
    from ..engine import ffmpeg as ff
    info = ff.probe(webm, vp9_native=True)
    w, h = int(info.get("width") or 512), int(info.get("height") or 512)
    clip = ff.decode_full(webm, w, h, 90, None)
    idx = sorted({int(round(i * (len(clip) - 1) / max(1, frames - 1))) for i in range(frames)})
    tiles = []
    for i in idx:
        im = Image.fromarray(np.ascontiguousarray(clip[i]), "RGBA")
        im.thumbnail((size, size))
        bg = Image.new("RGBA", (size, size), (200, 200, 200, 255))
        bg.alpha_composite(im, ((size - im.width) // 2, (size - im.height) // 2))
        tiles.append(bg.convert("RGB"))
    strip = Image.new("RGB", (size * len(tiles), size), (200, 200, 200))
    for k, t in enumerate(tiles):
        strip.paste(t, (k * size, 0))
    out = io.BytesIO()
    strip.save(out, "PNG")
    return out.getvalue()


# ---- the judge -----------------------------------------------------------------------------------------------------------------------
class VisionJudge:
    def __init__(self, complete=None, out: Path | None = None, cache=None, pol: str | None = None):
        """`complete(system, user, images=[bytes], ...) -> (text, meta)`; defaults to llm.complete on the configured target."""
        self.out = Path(out) if out is not None else None
        self._complete = complete
        self.policy = pol or policy()
        if cache is None:
            from ..runtime import cache as _c
            cache = _c.default()
        self.cache = cache
        self.calls = 0

    # -- one model call, logged ---------------------------------------------------------------------------------------------
    def _ask(self, kind: str, system: str, user: str, images: list, gen: str | None, sticker: str | None,
             version: str) -> tuple[str, dict]:
        from ..generation import model_calls
        from ..services import llm
        t = target()
        t0 = time.perf_counter()
        try:
            if self._complete is not None:
                text, meta = self._complete(system, user, images=images)
            else:
                if t["provider"] == "none":
                    raise llm.LLMError("No vision backend: start LM Studio (STICKERLOOM_LOCAL_URL) or set OPENAI_API_KEY.")
                text, meta = llm.complete_vision(system, user, images=images, json_mode=False, temperature=0,
                                          max_tokens=int(_env("VISION_MAX_TOKENS", 700)),
                                          timeout=float(_env("VISION_TIMEOUT", 90)), provider_=t["provider"],
                                          model_=t["model"], base_url=t["base_url"])
        except llm.LLMError as e:
            self._log(kind, t, "ERROR", int((time.perf_counter() - t0) * 1000), None, gen, sticker, version, error=str(e))
            raise JudgeError(str(e))
        self.calls += 1
        meta = dict(meta or {})
        meta.setdefault("model", t["model"])
        meta.setdefault("provider", t["provider"])
        return text, meta

    def _log(self, kind, t, status, ms, meta, gen, sticker, version, error=None, extra=None):
        if self.out is None:
            return
        from ..generation import model_calls
        meta = meta or {}
        recovery = {k: meta[k] for k in ("fallback_from", "fallback_reason") if k in meta}
        if recovery:
            extra = {**(extra or {}), **recovery}
        model_calls.append(self.out, kind, meta.get("provider") or t["provider"], meta.get("model") or t["model"], status=status,
                           latency_ms=meta.get("ms", ms), tokens_in=meta.get("tokens_in"), tokens_out=meta.get("tokens_out"),
                           prompt_version=version, generation_id=gen, sticker_id=sticker, error=error, extra=extra)

    def _structured(self, kind: str, system: str, user: str, images: list, gen, sticker, version, parse):
        """Ask, parse, validate; on a bad answer ONE repair round that quotes the problem; then fail with the raw output logged."""
        t = target()
        text, meta = self._ask(kind, system, user, images, gen, sticker, version)
        try:
            val = parse(text, meta.get("model", ""))
            self._log(kind, t, "OK", None, meta, gen, sticker, version, extra={"decision": getattr(val, "decision", None)})
            return val, meta
        except ValueError as first:
            self._log(kind, t, "INVALID_OUTPUT", None, meta, gen, sticker, version, error=str(first),
                      extra={"raw_output": (text or "")[:2000], "repaired": False})
            repair = (f"{user}\n\nYour previous answer was not usable: {first}.\nPrevious answer:\n{(text or '')[:1500]}\n"
                      "Reply again with ONE valid JSON object only, exactly in the required shape.")
            text2, meta2 = self._ask(kind, system, repair, images, gen, sticker, version)
            try:
                val = parse(text2, meta2.get("model", ""))
                self._log(kind, t, "OK", None, meta2, gen, sticker, version, extra={"repaired": True,
                                                                                      "decision": getattr(val, "decision", None)})
                return val, meta2
            except ValueError as second:
                self._log(kind, t, "INVALID_OUTPUT", None, meta2, gen, sticker, version, error=str(second),
                          extra={"raw_output": (text2 or "")[:2000], "repaired": True})
                raise JudgeError(f"the model answered nonsense twice: {second}")

    # -- public -----------------------------------------------------------------------------------------------------------------------
    def judge_sticker(self, png: bytes, cell: dict, pack_ctx: dict | None = None, *, generation_id: str | None = None,
                      sticker_id: str | None = None, reference_png: bytes | None = None, animation: bool = False) -> Judgement:
        """cell: {name, action/concept, emoji, tags}; pack_ctx: {subject, style, notes}. Returns a Judgement (UNJUDGED under the policy when the model fails)."""
        ctx = pack_ctx or {}
        slot = {"cell": {k: cell.get(k) for k in ("name", "action", "concept", "emoji", "tags", "prompt")}, "ctx": ctx,
                "anim": animation, "ref": bool(reference_png)}
        key = self.cache.key("vlm", "judge", hashlib.sha256(png).hexdigest(), target()["model"], JUDGE_VERSION,
                             hashlib.sha256(json.dumps(slot, sort_keys=True, default=str).encode()).hexdigest()[:16])
        hit = self.cache.get(key, "vlm_judge")
        if hit:
            j = Judgement.from_dict(hit)
            j.cached = True
            return j
        lines = [f"Intended sticker: {cell.get('name') or cell.get('concept') or cell.get('action') or 'unknown'}",
                 f"Action / feeling: {cell.get('action') or cell.get('concept') or cell.get('prompt') or 'unspecified'}",
                 f"Telegram emoji tag: {''.join(cell.get('emoji') or []) if isinstance(cell.get('emoji'), list) else cell.get('emoji') or 'none'}",
                 f"Subject of the pack: {ctx.get('subject') or 'unspecified'}", f"Style: {ctx.get('style') or 'unspecified'}"]
        if animation:
            lines.append("The image is a strip of 4 frames of the animation from start to end: also report ANIMATION_RISK "
                         "if the character changes shape, loses parts or drifts in identity between frames.")
        images = [_flatten(png) if not animation else png]
        if reference_png:
            images.append(_flatten(reference_png))
            lines.append("The second image is the reference sticker of this pack (the first approved one).")
        try:
            j, meta = self._structured("VLM_STICKER", SYSTEM, "\n".join(lines), images, generation_id, sticker_id, JUDGE_VERSION,
                                       parse_judgement)
        except JudgeError as e:
            if self.policy == "DETERMINISTIC_ONLY":
                return Judgement("UNJUDGED", error=str(e), notes="skipped (DETERMINISTIC_ONLY)")
            return Judgement("UNJUDGED", error=str(e), notes="unjudged: the vision model is unavailable")
        j.model = meta.get("model", j.model)
        self.cache.set(key, j.to_dict(), CACHE_TTL)
        return j

    def check_sheet(self, sheet_png: bytes, grid: tuple, *, generation_id: str | None = None) -> dict:
        """One call per sheet: count, isolated, missing, duplicated. {ok: None} when the model is down (never blocks)."""
        n = int(grid[0]) * int(grid[1])

        def parse(text, model):
            d = _first_json(text)
            if not isinstance(d.get("ok"), bool):
                raise ValueError("ok must be true or false")
            return type("Sheet", (), {"d": {"ok": d["ok"], "count": int(d.get("count", 0)), "isolated": bool(d.get("isolated", True)),
                                            "missing": [int(x) for x in (d.get("missing") or []) if str(x).isdigit()],
                                            "duplicated": d.get("duplicated") or [], "notes": str(d.get("notes") or "")[:300],
                                            "model": model}, "decision": "OK" if d["ok"] else "BAD"})()
        user = f"The sheet should show a grid of {grid[0]} rows by {grid[1]} columns: {n} separate characters."
        try:
            v, _ = self._structured("VLM_SHEET", SHEET_SYSTEM, user, [_flatten(sheet_png, 768)], generation_id, None, SHEET_VERSION, parse)
        except JudgeError as e:
            return {"ok": None, "error": str(e), "notes": "unchecked: the vision model is unavailable"}
        d = v.d
        d["ok"] = bool(d["ok"] and d["count"] == n and d["isolated"] and not d["missing"])
        return d

    def judge_many(self, items: list, workers: int | None = None) -> list:
        """items: [(png, cell, ctx, kwargs)] -> [Judgement], at most VISION_CONCURRENCY calls at once (default 2)."""
        n = int(workers or _env("VISION_CONCURRENCY", 2))
        with ThreadPoolExecutor(max_workers=max(1, n)) as ex:
            return list(ex.map(lambda it: self.judge_sticker(it[0], it[1], it[2], **(it[3] if len(it) > 3 else {})), items))


# ---- over one generation ---------------------------------------------------------------------------------------------------------------
def judge_generation(out: Path, gid: int, scope: str = "still", judge: VisionJudge | None = None, force: bool = False, allowed=True) -> dict:
    """Pre-review every READY sticker of a generation. Writes, per sticker: a history line (actor `vlm`, decision APPROVE/REJECT, reason = the
    first code, detail = the whole judgement) at gate `still` or `anim`, and `judge` / `judge_anim` on the sticker; one event per run; the plan of
    bounded recovery for the rejections. Never touches `review.*`: the human decides. Returns a summary."""
    from ..flow import pipeline as pl
    from . import consent
    from .recovery import plan_recovery
    consent.require(allowed)                  # `allowed` is the person's yes to AI vision (vision/consent.py); the operator's CLI passes True. Without it no image is read for a model.
    out = Path(out)
    judge = judge or VisionJudge(out=out)
    res = pl.read_result(out, gid)
    d = pl.gen_dir(out, gid)
    stage, field_ = ("anim", "judge_anim") if scope == "anim" else ("still", "judge")
    if scope not in ("still", "anim"):
        raise pl.PipelineError("scope must be still or anim", 400)
    gid_s = f"G{gid:03d}"
    ok_status = "anim_status" if scope == "anim" else "status"
    targets = [s for s in res["stickers"] if s.get(ok_status) == "READY" and (s.get("webm") if scope == "anim" else s.get("png"))]
    if not targets:
        raise pl.PipelineError("nothing to judge: " + ("no READY animation" if scope == "anim" else "no READY sticker"), 409)
    plan = res.get("plan") or {}
    ctx = {"subject": (plan.get("slots") or {}).get("subject_description") or res.get("task") or res.get("prompt"),
           "style": (plan.get("slots") or {}).get("style_id") or res.get("style")}
    ref_png = None
    first_ok = next((s for s in res["stickers"] if s.get("review", {}).get("still") == "APPROVED" and s.get("png")), None)
    if first_ok:
        try:
            ref_png = (d / first_ok["png"]).read_bytes()
        except OSError:
            ref_png = None
    t0 = time.perf_counter()
    items, kept = [], []
    for s in targets:
        if s.get(field_) and not force and s[field_].get("version") == JUDGE_VERSION and s[field_].get("decision") != "UNJUDGED":
            continue
        try:
            png = anim_strip(d / s["webm"]) if scope == "anim" else (d / s["png"]).read_bytes()
        except Exception as e:                                   # an unreadable file is one UNJUDGED sticker, not a failed run
            s[field_] = Judgement("UNJUDGED", error=str(e)[:200]).to_dict()
            continue
        ref = ref_png if (first_ok and s["index"] != first_ok["index"]) else None
        cell = {"name": s.get("name"), "action": s.get("concept") or s.get("key"), "concept": s.get("concept"),
                "emoji": s.get("emoji"), "tags": s.get("tags"), "prompt": s.get("prompt")}
        items.append((png, cell, ctx, {"generation_id": gid_s, "sticker_id": f"{gid_s}/S{s['index']}", "reference_png": ref,
                                       "animation": scope == "anim"}))
        kept.append(s)
    results = judge.judge_many(items) if items else []
    rejected, approved, unjudged, reasons = [], [], [], {}
    for s, j in zip(kept, results):
        s[field_] = j.to_dict()
        if j.decision == "UNJUDGED":
            unjudged.append(s["index"])
            continue
        pl.hist(s, stage, "vlm", j.decision, j.reasons[0] if j.reasons else None, None,
                {"reasons": j.reasons, "confidence": j.confidence, "scores": j.scores, "notes": j.notes,
                 "model": j.model, "version": j.version, "suggested_emoji": j.suggested_emoji})
        (rejected if j.reject else approved).append(s["index"])
        reasons[s["index"]] = j.reasons
    if kept:
        pl.write_result(out, gid, res)
        pl.emit(out, gid, "vlm_" + stage, "done", int((time.perf_counter() - t0) * 1000),
                {"judged": len(results) - len(unjudged), "rejected": rejected, "unjudged": unjudged,
                 "model": target()["model"], "version": JUDGE_VERSION, "gate": stage}, "vlm", "REJECT" if rejected else "APPROVE")
    all_rejected = [s["index"] for s in targets if (s.get(field_) or {}).get("decision") == "REJECT"]
    all_approved = [s["index"] for s in targets if (s.get(field_) or {}).get("decision") == "APPROVE"]
    plan_ = plan_recovery(len(res["stickers"]), all_rejected, all_approved, reasons={s["index"]: (s.get(field_) or {}).get("reasons") for s in targets},
                          key_colour=res.get("key_colour") or "green")
    return {"generation": gid_s, "scope": scope, "judged": len(results) - len(unjudged), "cached": sum(1 for j in results if j.cached),
            "approved": all_approved, "rejected": all_rejected, "unjudged": unjudged, "calls": judge.calls,
            "recovery": plan_.to_dict(), "model": target()["model"], "version": JUDGE_VERSION}
