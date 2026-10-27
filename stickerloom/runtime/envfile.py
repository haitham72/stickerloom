""".env, read the way every dotenv reader reads it: `KEY=value`, optional quotes, and a trailing ` # comment` is a comment, not part of the value.

This exists because four modules each had their own loader that kept the comment: `STICKERLOOM_AGENT_PROVIDER=auto   # the chat assistant; ...` became the provider name
"auto   # the chat assistant; ...", which is neither local nor openai, so the chat's model calls went to the local server under a cloud model name and failed (the 'alternating
between local and cloud' and the red 'AI output did not pass' of 2026-10-02). One parser, used by `services/llm.py`, `runtime/cache.py`, `store/db.py` and `obs/trace.py`.
The real environment always wins (`setdefault`)."""
from __future__ import annotations

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


def parse_line(line: str):
    """-> (key, value) or None for a blank line, a comment or a line without `=`."""
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    k, v = line.split("=", 1)
    k, v = k.strip(), v.strip()
    if k.lower().startswith("export "):
        k = k[7:].strip()
    if v[:1] in ("'", '"'):
        end = v.find(v[0], 1)
        v = v[1:end] if end > 0 else v[1:]
    else:
        for i, ch in enumerate(v):                       # an unquoted value ends at the first ` #`
            if ch == "#" and (i == 0 or v[i - 1] in " \t"):
                v = v[:i]
                break
        v = v.strip()
    return (k, v) if k else None


def parse(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        kv = parse_line(line)
        if kv:
            out[kv[0]] = kv[1]
    return out


def load(path: Path | None = None) -> None:
    """Put the file's values into the environment where nothing is set yet."""
    try:
        for k, v in parse((path or ENV_FILE).read_text(encoding="utf-8")).items():
            os.environ.setdefault(k, v)
    except OSError:
        pass


def choice(name: str, default: str = "auto") -> str:
    """A setting that is one word (a provider): lower case, and never more than its first word even if a comment slipped into the environment."""
    raw = str(os.environ.get(name) or default).split("#")[0].strip().lower()
    return raw.split()[0] if raw else default
