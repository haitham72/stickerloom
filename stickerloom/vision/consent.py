"""Consent for AI vision: "Allow AI vision of generated media?", asked ONCE (per chat session, per browser), never per run.

Sending a sticker to a vision model is the one thing in the app that moves image content to a model (the local LM Studio, or OpenAI when the vision provider is the
cloud), so it needs a person's yes. The rule is enforced where the model would be called (`transcribe.captions_for`, `judge.judge_generation`), not in the screen:
`allowed` must be exactly `True`. The CLI is the operator's explicit command and passes it. Reading a caption that is already stored needs no consent."""
from __future__ import annotations


class ConsentRequired(Exception):
    """Raised before any image is sent: the caller has not been allowed to use AI vision. HTTP 409 with `consent_required: true`."""
    code = 409


def require(allowed) -> None:
    if allowed is not True:
        raise ConsentRequired("AI vision of generated media is not allowed yet: ask the person once (\"Allow AI vision of generated media?\") and pass allow_vlm=true. Nothing was sent.")


def asked(settings) -> bool:
    """Has the question been answered (yes or no) in these chat settings? `allow_vlm` is None until it is."""
    return isinstance(settings, dict) and isinstance(settings.get("allow_vlm"), bool)
