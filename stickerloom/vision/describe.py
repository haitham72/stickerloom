"""Stream an answer grounded in the requested still, with the existing consent and model ledger."""
from pathlib import Path

from ..flow import pipeline as pl
from ..services import llm
from . import consent
from .judge import JudgeError, VisionJudge, _env, _flatten, target

VERSION = "describe_v1"
SYSTEM = """Answer the person's question about ONE sticker, using only the attached still image.
Explain the visible pose, gesture, expression and relevant objects in two or three natural sentences.
The sticker's stored name is not evidence. Describe what you can actually see; say when a detail is unclear.
A still does not prove movement over time: do not invent animation, a storyline or hidden motives.
Return plain text, without JSON, headings or hidden reasoning.
""" + llm.DATA_RULE


def sticker(out, gid, index: int, question: str, *, allowed=None, on_delta=None, complete=None) -> str:
    consent.require(allowed)
    out = Path(out)
    gid_n = int(str(gid).upper().lstrip("G"))
    gid_s = f"G{gid_n:03d}"
    res = pl.read_result(out, gid_n)
    item = next((s for s in res.get("stickers") or [] if s["index"] == index), None)
    if not item or item.get("status") != "READY" or not item.get("png"):
        raise JudgeError("That sticker has no finished still yet.")
    try:
        image = _flatten((pl.gen_dir(out, gid_n) / item["png"]).read_bytes())
    except (OSError, ValueError) as e:
        raise JudgeError("I couldn't read that sticker's still image.") from e
    judge = VisionJudge(out=out)
    config = target()
    receive = on_delta or (lambda delta: None)
    try:
        text, meta = (complete or llm.complete_vision)(
            SYSTEM, llm.fence("QUESTION", question, 600), images=[image], on_delta=receive,
            temperature=0, max_tokens=int(_env("VISION_MAX_TOKENS", 700)),
            timeout=float(_env("VISION_TIMEOUT", 90)), provider_=config["provider"],
            model_=config["model"], base_url=config["base_url"],
        )
        text = llm._visible_text(text).strip()
        if not text:
            raise llm.LLMError("The vision model returned no description.", kind="empty")
    except llm.LLMError as e:
        judge._log("VLM_DESCRIBE", config, "ERROR", None, None, gid_s, f"{gid_s}/S{index}", VERSION, error=str(e))
        raise JudgeError(str(e)) from e
    judge._log("VLM_DESCRIBE", config, "OK", None, meta, gid_s, f"{gid_s}/S{index}", VERSION)
    return text
