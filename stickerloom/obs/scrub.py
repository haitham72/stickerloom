"""Free text that may leave this machine (a ticket, an error answer, a console line) never carries where the machine keeps its files."""
from __future__ import annotations

import re

_WIN_PATH = re.compile(r"(?<!\w)[A-Za-z]:[\\/](?:[^\\/\s'\"<>|:*?]+[\\/])*([^\\/\s'\"<>|:*?]*)")
_POSIX_PATH = re.compile(r"(?<![\w.:/])/(?:Users|home|tmp|var|private|mnt|opt|root|Volumes|srv)/(?:[^/\s'\"<>|]+/)*([^/\s'\"<>|]*)")


def scrub_paths(text: str) -> str:
    """An absolute file path in free text (an exception message, a log line) becomes `<path>/name`: where a machine keeps its files never leaves it,
    the file name stays so the message is still readable. Relative paths and URL paths are left alone."""
    return _POSIX_PATH.sub(r"<path>/\1", _WIN_PATH.sub(r"<path>/\1", text))


_SECRET = [
    (re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"), "<telegram-token>"),                                  # a bot token
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b"), "<key>"),                                         # OpenAI-style keys
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"), "Bearer <token>"),
    (re.compile(r"(?i)\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD)[A-Z0-9_]*)\s*[=:]\s*['\"]?[^\s'\"#]{6,}"), r"\1=<secret>"),
    (re.compile(r"(?i)(postgres(?:ql)?|redis)://[^\s'\"]+"), r"\1://<url>"),
]
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_IDS = re.compile(r"\b(?:G|J|T|C|S|E|P|R|F)\d{3,}\b")
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)


def scrub_secrets(text: str) -> str:
    """Tokens, keys, passwords and connection URLs become placeholders (what the support index keeps of a doc or a source file)."""
    text = str(text)
    for rx, rep in _SECRET:
        text = rx.sub(rep, text)
    return scrub_paths(text)


def scrub_personal(text: str, names: list[str] | tuple = ()) -> str:
    """What a shared FAQ entry may say: no secrets, paths, e-mail addresses, record ids (G104, T012, ...), uuids or the names given (the people involved)."""
    text = _EMAIL.sub("<email>", scrub_secrets(text))
    text = _UUID.sub("<id>", _IDS.sub("<id>", text))
    for n in names:
        n = str(n or "").strip()
        if len(n) >= 2:
            text = re.sub(r"(?i)\b" + re.escape(n) + r"\b", "<person>", text)
    return text
