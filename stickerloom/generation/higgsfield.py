"""Higgsfield through its CLI (`@higgsfield/cli`): a plain subprocess that prints JSON, so stickerloom can fulfil a job without an MCP session.
The CLI keeps its own OAuth login; stickerloom never sees or stores a Higgsfield credential (and never runs `auth token`).

The real executable is called directly with an argv list (no shell): the npm shim is a `.cmd` file on Windows, and a prompt containing `&`,
`%` or a quote must not be interpreted by cmd.exe. If only the shim can be found, prompts with such characters are refused.
Every function takes the CLI's own words (job_type, --param value) so the engine stays ignorant of the provider."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..providers.base import ProviderError

NEVER = {("kling3_0", "mode", "4k")}          # Kling 4k is never used (cost); enforced here and in the catalog
SHIM_UNSAFE = set('&|<>^%"\n\r')


TRANSIENT_CODES = (408, 425, 429, 500, 502, 503, 504)   # a timeout, a rate limit or a provider hiccup: the job may still be rendering there


class HiggsError(ProviderError):
    """A failure of the Higgsfield CLI. `code` is the HTTP status when the CLI reported one (it answers "Higgsfield API error (HTTP 503). request failed with status 503 Service Unavailable"), else 502 for a failure we cannot classify.

    `transient` is what generation/jobs.py branches on: a 5xx, a timeout or a rate limit while WAITING must not finish a paid job as FAILED (the provider may still be rendering it), so the job goes to TIMEOUT and stays resumable on the same ticket. A prompt the provider refused is a 4xx and stays final."""

    def __init__(self, message: str, code: int | None = None, transient: bool | None = None):
        super().__init__(message)
        parsed = code if code is not None else _http_code(message)
        self.code = parsed or 502
        # Only a status we actually recognised may be retried: a CLI failure we cannot classify is FINAL by default, so a refused prompt is never parked as a retryable timeout.
        self.transient = transient if transient is not None else bool(parsed) and parsed in TRANSIENT_CODES


def _http_code(message: str) -> int | None:
    """The HTTP status out of the CLI's own message, or None. Deliberately strict: a loose match would read a duration or a port as a status and turn a final failure into a retry."""
    m = re.search(r"(?:HTTP|status|error)\s+(\d{3})\b", str(message), re.I)
    return int(m.group(1)) if m else None


def _exe_near(shim: Path) -> Path | None:
    """The package's vendored binary, found from the npm shim (Windows layout, then the macOS/Linux symlink layout)."""
    names = ("hf.exe", "hf")
    for base in (shim.parent / "node_modules", shim.resolve().parent.parent.parent):
        for n in names:
            f = base / "@higgsfield" / "cli" / "vendor" / n
            if f.is_file():
                return f
    for n in names:
        f = shim.resolve().parent.parent / "vendor" / n
        if f.is_file():
            return f
    return None


def binary() -> tuple[str | None, bool]:
    """(path, direct): direct is False when only the .cmd shim is available."""
    env = os.environ.get("STICKERLOOM_HIGGSFIELD_BIN")
    if env:
        return env, True
    w = shutil.which("higgsfield")
    if not w:
        return None, False
    exe = _exe_near(Path(w))
    return (str(exe), True) if exe else (w, not w.lower().endswith((".cmd", ".bat", ".ps1")))


def available() -> bool:
    return binary()[0] is not None


def _subprocess_run(args: list[str], timeout: float):
    if os.environ.get("STICKERLOOM_NO_REAL_CLI"):          # set by the test suite: a test must never be able to spend credits
        raise HiggsError("the real Higgsfield CLI is disabled in this process (STICKERLOOM_NO_REAL_CLI)")
    exe, direct = binary()
    if not exe:
        raise HiggsError("The Higgsfield CLI is not installed (npm i -g @higgsfield/cli, then higgsfield auth login).")
    if not direct and any(set(a) & SHIM_UNSAFE for a in args):
        raise HiggsError("Only the Windows shim of the Higgsfield CLI was found; a prompt with & | < > ^ % \" or a line break cannot be passed safely. "
                         "Set STICKERLOOM_HIGGSFIELD_BIN to the vendored hf.exe.")
    try:
        r = subprocess.run([exe] + args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise HiggsError(f"The Higgsfield CLI did not answer within {int(timeout)} s.", code=504)   # a timeout is transient: the provider may still be working
    except OSError as e:
        raise HiggsError(f"Could not run the Higgsfield CLI: {e}")
    return r.returncode, r.stdout, r.stderr


RUN = _subprocess_run          # tests replace this with a fake: RUN(args, timeout) -> (returncode, stdout, stderr)


def _json(args: list[str], timeout: float = 60):
    code, out, err = RUN(args + ["--json"], timeout)
    if code != 0:
        raise HiggsError((err or out or f"exit {code}").strip().splitlines()[0][:300] if (err or out) else f"exit {code}")
    try:
        return json.loads(out)
    except ValueError:
        raise HiggsError(f"unexpected output from the Higgsfield CLI: {out.strip()[:200]!r}")


def _param_args(params: dict | None) -> list[str]:
    a = []
    for k, v in (params or {}).items():
        if v is None or v == "":
            continue
        a += [f"--{k}", str(v).lower() if isinstance(v, bool) else str(v)]
    return a


def _check_allowed(model: str, params: dict | None) -> None:
    for k, v in (params or {}).items():
        if (model, k, str(v)) in NEVER:
            raise HiggsError(f"{model} {k}={v} is not allowed")


def _media(start_image=None, end_image=None, image_references=None) -> list[str]:
    a = []
    if start_image:
        a += ["--start-image", str(start_image)]
    if end_image:
        a += ["--end-image", str(end_image)]
    for r in image_references or []:
        a += ["--image-references", str(r)]
    return a


def account() -> dict:
    """{credits, plan}. A missing workspace is selected automatically only when the account has exactly one."""
    try:
        d = _json(["account", "status"])
    except HiggsError as e:
        if "workspace" not in str(e).lower():
            raise
        ws = _json(["workspace", "list"])
        if not isinstance(ws, list) or len(ws) != 1:
            raise HiggsError("No Higgsfield workspace is selected: run `higgsfield workspace set <id>`.")
        RUN(["workspace", "set", str(ws[0]["id"])], 30)
        d = _json(["account", "status"])
    return {"credits": float(d.get("credits") or 0), "plan": d.get("subscription_plan_type") or ""}


def cost(model: str, params: dict | None = None, prompt: str = "", **media) -> float:
    _check_allowed(model, params)
    d = _json(["generate", "cost", model, "--prompt", prompt] + _param_args(params) + _media(**media))
    return float(d.get("credits") if isinstance(d, dict) else d)


def create(model: str, params: dict | None = None, prompt: str = "", **media) -> str:
    """Start the job WITHOUT waiting and return its id (the ticket). `create --wait` would only reveal the id at the end."""
    _check_allowed(model, params)
    d = _json(["generate", "create", model, "--prompt", prompt] + _param_args(params) + _media(**media), timeout=300)
    first = d[0] if isinstance(d, list) and d else d
    jid = first.get("id") if isinstance(first, dict) else first
    if not isinstance(jid, str) or not jid.strip():
        raise HiggsError(f"the Higgsfield CLI did not return a job id: {str(d)[:200]}")
    return jid.strip()


def get(job_id: str) -> dict:
    """One free, read-only provider lookup; never starts or waits for a job."""
    d = _json(["generate", "get", job_id])
    job = d[0] if isinstance(d, list) and d else d
    if not isinstance(job, dict):
        raise HiggsError(f"unexpected provider result: {str(d)[:200]}")
    return job


def wait(job_id: str, timeout_s: int = 1200, interval_s: int = 3) -> dict:
    d = _json(["generate", "wait", job_id, "--timeout", f"{int(timeout_s)}s", "--interval", f"{int(interval_s)}s", "--quiet"], timeout=timeout_s + 60)
    job = d[0] if isinstance(d, list) and d else d
    if not isinstance(job, dict):
        raise HiggsError(f"unexpected wait result: {str(d)[:200]}")
    status = str(job.get("status") or "")
    if status != "completed" or not job.get("result_url"):
        raise HiggsError(f"the Higgsfield job {job_id} ended as '{status or 'unknown'}' without a result")
    return job


def download(url: str, dest: Path, max_bytes: int | None = None) -> tuple[str, int]:
    """Plain HTTPS GET of the result (no credential needed); returns (sha256, bytes)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    from ..services.telegram import _ssl_context  # the tolerant TLS context: one malformed entry in a Windows certificate store must not lose a result that was already paid for
    try:
        with urllib.request.urlopen(url, timeout=120, context=_ssl_context()) as r:
            if max_bytes is not None and int(r.headers.get("Content-Length") or 0) > max_bytes:
                raise HiggsError("result file too large")
            data = r.read() if max_bytes is None else r.read(max_bytes + 1)
            if max_bytes is not None and len(data) > max_bytes:
                raise HiggsError("result file too large")
    except (OSError, ValueError) as e:
        raise HiggsError(f"could not download the result: {e}")
    dest.write_bytes(data)
    return hashlib.sha256(data).hexdigest(), len(data)


# ---------- the full list of models (so the selector is never limited to what was hand-picked) ----------
TYPES = ("image", "video", "audio", "text")
DUMP_NAME = "higgsfield_models.json"


def dump_models(workers: int = 8) -> dict:
    """Every model Higgsfield lists, by type, with its parameters and rules: {fetched_at, counts, image:[...], video:[...], ...}."""
    def one(m):
        try:
            d = _json(["model", "get", m["job_type"]], 60)
            return {"job_type": m["job_type"], "display_name": d.get("display_name") or m.get("display_name"), "type": d.get("type") or m.get("type"),
                    "params": d.get("params") or [], "rules": d.get("rules") or []}
        except HiggsError as e:
            return {"job_type": m["job_type"], "display_name": m.get("display_name"), "type": m.get("type"), "params": [], "rules": [], "error": str(e)[:200]}
    out = {"fetched_at": round(time.time(), 3), "counts": {}}
    for t in TYPES:
        try:
            listing = _json(["model", "list", f"--{t}"])
        except HiggsError:
            listing = []
        with ThreadPoolExecutor(workers) as ex:
            out[t] = list(ex.map(one, listing))
        out["counts"][t] = len(out[t])
    return out


def load_models(out_dir: Path, refresh: bool = False, max_age_s: float = 24 * 3600) -> dict | None:
    """The cached dump (out/higgsfield_models.json), refreshed when missing, older than a day, or asked for. None when the CLI is not there."""
    f = Path(out_dir) / DUMP_NAME
    if not refresh and f.is_file():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if time.time() - float(d.get("fetched_at") or 0) < max_age_s:
                return d
        except (OSError, ValueError):
            pass
    if not available():
        return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None
    d = dump_models()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
    return d
