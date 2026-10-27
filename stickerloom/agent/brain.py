"""The model behind the agent: a few SMALL, schema-checked calls. The rules decide first (resolver.py); the model only classifies what the
rules are unsure about, picks a sticker the words do not name, answers a question from the structured state, and writes the narrative
summary. Every call goes through `llm.complete` (OpenAI or the local LM Studio), is logged to out/model_calls.jsonl, gets one repair
round, and on failure returns None so the graph falls back to the deterministic answer. The model never generates media and never
judges pixels."""
from __future__ import annotations

import json
import os
import re
import time

from ..services import llm

INTENTS = ("NEW", "NEW_MULTI", "REFINE", "ANOTHER", "EDIT_STICKERS", "ANIMATE", "FEEDBACK", "REVIEW", "ASK", "CHANGE_SETTINGS", "SEARCH", "SMALLTALK", "PROFILE", "AMBIGUOUS")

SUBJECTS_SYSTEM = f"""You choose subjects for sticker packs. Given a category and a number N, answer with N DIFFERENT concrete subjects of that category: varied (not just the most obvious N),
each one to three words, something a pack of stickers can be about; never a brand, a real person or the word "sticker". Reply with ONE JSON object only: {{"subjects": ["...", "..."]}}
{llm.DATA_RULE}"""

REFINE_SYSTEM = f"""A person gives feedback about the look of one sticker subject. Say what they want changed. Reply with ONE JSON object only:
{{"style_id": one of flat_vector, toon_shade, glossy_3d, clay_3d, realistic, hand_drawn, minimal, pixel_art, watercolor, paper_cut, pop_comic, kawaii, or null, "size": "larger" or "smaller" or null, "colour": a colour word or null}}
"too realistic, make it cartoonish" = toon_shade. "too small" = larger. "too big" = smaller. Use null for anything they did not ask to change.
{llm.DATA_RULE}"""

CLASSIFY_SYSTEM = f"""You route one chat message in a sticker studio to intents. Reply with ONE JSON object only:
{{"intents": [..], "confidence": 0.0-1.0}}
Intents: {", ".join(INTENTS)}.
NEW: a request for a new set ("make me falcon stickers", "teddy bear with a book"). NEW_MULTI: a request for SEVERAL sets from a category ("three sticker packs of fruits"). REFINE: a change to a whole batch or subject ("the cherries were too realistic, make them cartoonish", "the banana was too small"). ANOTHER: more of the same subject. EDIT_STICKERS: change specific stickers
("make number 3 happier"). ANIMATE: make them move. FEEDBACK: likes/dislikes ("I like 2 but not 3"). REVIEW: approve or reject ("approve all but 5").
ASK: a question about what exists ("which one is the shocked banana?"). CHANGE_SETTINGS: grid, style, animation, asking before spending. SEARCH: find an old
sticker. SMALLTALK: greetings and thanks. PROFILE: the person says something about THEMSELVES (their name, where they live, age, what they like) or answers a question I asked about them ("noor" right after "what should I call you?", "hello from noor", "it is 'noor'"): never NEW, a name is not a subject. AMBIGUOUS: you cannot tell. Use the context: a short reply to MY LAST QUESTION answers it; a plan I am holding stays held unless they ask for something new. A message can carry two intents ("I like 2 but make 5 happier" = FEEDBACK + EDIT_STICKERS).
{llm.DATA_RULE}"""

PROFILE_SYSTEM = f"""A person in a sticker studio says something about THEMSELVES. Write down only what they state about themselves (not about anyone else, not a sticker request). Reply with ONE JSON object only,
with only these keys and only the ones they stated: {{"name": "...", "place": "...", "age": 30, "likes": ["..."], "dislikes": ["..."], "extra": {{"short_key": "short value"}}}}.
A correction replaces the old value ("my name is not Sam, it is Noor" = {{"name": "Noor"}}). Values are short plain words (at most 60 characters). Nothing they did not say. {{}} when there is nothing.
{llm.DATA_RULE}"""

PICK_SYSTEM = """You map a phrase to sticker numbers. You get the numbered stickers of one batch and the user's phrase. Reply with ONE JSON object only:
{"numbers": [..], "confidence": 0.0-1.0}. Use only numbers from the list. If the phrase names none of them, return an empty list.
""" + llm.DATA_RULE

ANSWER_SYSTEM = """You answer a question about the user's sticker batches, using ONLY the facts you are given. Be short and warm (one or two sentences).
Always quote ids as G012/S3. If the facts do not answer it, say so plainly. Never invent stickers.
""" + llm.DATA_RULE

SUMMARY_SYSTEM = """You compress the older turns of a sticker chat into 2-4 short sentences: what the user asked for, what they liked and rejected, what they
want next. Keep every generation and sticker id (G012, G012/S3) exactly. Do not invent anything.
""" + llm.DATA_RULE

EDIT_SYSTEM = """Interpret a requested edit of existing stickers in its conversational context.
Read the ENTIRE instruction, including every clause. Replacement ('replace X with Y') changes X to Y, preserving unrelated stickers.
Distinguish a detail (tweak), a new pose/action (action), and a new character/design (redesign).
If any phrase is nonsense, any target is unclear, or the replacement/change is missing, clear=false; acknowledge only the understood part and ask ONE focused question.
Never invent a meaning for unknown words, discard an unclear clause, or treat show/list requests as generation.
Reply with JSON: {"clear": true or false, "confidence": 0.0-1.0,
"case": "tweak" or "action" or "redesign", "change": "the requested change only",
"understood": "short understood part or empty", "clarification": "short question or empty"}.
""" + llm.DATA_RULE


NAMES_SYSTEM = """You check the names of stickers against what their pictures show. You get numbered stickers, each with its CURRENT name and a one-sentence description of the picture.
For each sticker say whether the name fits the picture. Keep a name that is roughly right. If it does not fit, give a better one: 2 to 5 plain English words naming the character and the feeling or action,
no quotes, no emoji, no numbers (for example "Happy boy holding a red heart"). Reply with ONE JSON object only:
{"stickers": [{"index": 1, "fits": true}, {"index": 3, "fits": false, "name": "Happy boy holding a red heart"}]}
""" + llm.DATA_RULE


def target() -> dict:
    """Which model runs the agent: STICKERLOOM_AGENT_PROVIDER local|openai|auto. Auto prefers the local model (free) when it answers."""
    llm._load_dotenv()
    from ..runtime import envfile
    want = envfile.choice("STICKERLOOM_AGENT_PROVIDER")
    f = llm.forced()
    if f and want != "none":                                    # this chat pinned its AI model (a Telegram chat: gpt-4o)
        return {"provider": llm.forced_provider(), "model": f["model"]}
    if want == "auto":
        want = llm.resolve()                                    # the person's choice (auto / local / cloud) in one place, the same for the plan, the chat and the vision judge
    model = os.environ.get("STICKERLOOM_AGENT_MODEL") or (llm.local_model() if want == "local" else llm.cloud_model())
    return {"provider": want, "model": model}


LAST: dict = {"error": None, "at": 0.0, "ok_at": 0.0}     # the process-wide last model failure (any chat, any Brain) and the last success after it
LAST_ERROR_TTL = 600.0                                      # a failure older than this is no longer "the model is down"


def reset_status() -> None:
    LAST.update(error=None, at=0.0, ok_at=0.0)


def status() -> dict:
    """{fallback, reason}: is the chat on its rules only right now (no model behind it), and in plain words why. A configured model that cannot answer is a fallback just like no model at
    all: the pill and `GET /api/chat/agent` say so, and a turn that fell back says so in its steps (`graph.Trace.rules_note`)."""
    t = target()
    if t["provider"] == "none":
        from ..runtime import envfile
        if envfile.choice("STICKERLOOM_AGENT_PROVIDER") == "none":
            return {"fallback": True, "reason": "the assistant's language model is switched off (STICKERLOOM_AGENT_PROVIDER=none)"}
        av, pref = llm.availability(), llm.preference()
        why = [av[k]["why"] for k in (("local",) if pref == "local" else ("cloud",) if pref == "cloud" else ("local", "cloud")) if av[k]["why"]]
        return {"fallback": True, "reason": "; ".join(why) or "no language model is configured"}
    av = llm.availability()
    side = "local" if t["provider"] == "local" else "cloud"
    if t["provider"] in ("local", "openai") and not av[side]["ok"]:
        return {"fallback": True, "reason": av[side]["why"]}
    if LAST["error"] and LAST["at"] > LAST["ok_at"] and time.time() - LAST["at"] < LAST_ERROR_TTL:
        return {"fallback": True, "reason": "the last call to the model failed: " + str(LAST["error"])[:200]}
    return {"fallback": False, "reason": None}


def _first_json(text: str):
    return llm.extract_json(text)


class Brain:
    def __init__(self, complete=None, out=None):
        """`complete(system, user, **kw) -> (text, meta)`. Tests pass a fake; production uses llm.complete on `target()`."""
        self._complete = complete
        self.out = out
        self.calls = 0
        self.last_error = None                                       # why a model call failed in the current turn (the graph resets it per turn and says so in the steps)

    @property
    def available(self) -> bool:
        return self._complete is not None or target()["provider"] != "none"

    def _ask(self, kind: str, system: str, user: str, max_tokens: int = 400):
        t = target()
        t0 = time.perf_counter()
        try:
            if self._complete is not None:
                text, meta = self._complete(system, user)
            else:
                text, meta = llm.complete(system, user, temperature=0, max_tokens=max_tokens, timeout=45,
                                          provider_=t["provider"], model_=t["model"])
        except llm.LLMError as e:
            llm.note_failure(t["provider"])
            self.last_error = str(e)
            if self._complete is None:                                 # a real model call (a test's fake is no news about the model)
                LAST.update(error=str(e), at=time.time())
            if self.out is not None:
                from ..generation import model_calls
                model_calls.append(self.out, kind, t["provider"], t["model"], status="ERROR", latency_ms=int((time.perf_counter() - t0) * 1000),
                                   prompt_version="agent_v1", error=str(e))
            return None
        self.calls += 1
        if self._complete is None:
            LAST["ok_at"] = time.time()
        if self.out is not None and self._complete is None:
            from ..generation import model_calls
            model_calls.append(self.out, kind, meta.get("provider", t["provider"]), meta.get("model", t["model"]), status="OK",
                               latency_ms=meta.get("ms"), tokens_in=meta.get("tokens_in"), tokens_out=meta.get("tokens_out"), prompt_version="agent_v1")
        return text

    def _json(self, kind: str, system: str, user: str):
        text = self._ask(kind, system, user)
        if text is None:
            return None
        try:
            return _first_json(text)
        except ValueError as first:                                  # one repair round
            text = self._ask(kind, system, f"{user}\n\nYour previous answer was not valid JSON ({first}). Reply with the JSON object only.")
            try:
                return _first_json(text) if text else None
            except ValueError:
                return None

    # ---- the four jobs ----------------------------------------------------------------------------------------------------------
    def classify(self, text: str, summary: str, focus: dict | None = None) -> list | None:
        compact = {k: focus.get(k) for k in ("generation", "grid", "style_id", "stickers")} if focus else {}
        if focus:
            compact["stickers"] = (focus.get("stickers") or [])[:3]
            selected = set(compact["stickers"])
            items = focus.get("items") or []
            compact["items"] = [{"id": item.get("id"), "key": str(item.get("key") or "")[:80]}
                                for item in ([x for x in items if x.get("id") in selected] if selected else items)[:3]]
        context = llm.fence('FOCUS', json.dumps(compact, ensure_ascii=False), 1000) + "\n\n" if focus else ""
        d = self._json("LLM_INTENT", CLASSIFY_SYSTEM, f"{context}What this chat has so far:\n{llm.fence('CHAT', summary)}\n\n{llm.fence('MESSAGE', text, 600)}")
        if not isinstance(d, dict):
            return None
        got = [str(i).upper() for i in (d.get("intents") or []) if str(i).upper() in INTENTS]
        return got or None

    def extract_profile(self, text: str, known: str = "") -> dict | None:
        """The facts a person states about themselves when the rules could not read them (several at once, a correction, a reference). The answer is a PROPOSAL:
        the caller passes it through profile.validate_facts before anything is written, so unknown keys and long values never reach storage."""
        d = self._json("LLM_PROFILE", PROFILE_SYSTEM, f"{llm.fence('KNOWN', known or 'nothing', 600)}\n\n{llm.fence('MESSAGE', text, 600)}")
        if not isinstance(d, dict):
            return None
        from .profile import validate_facts
        return validate_facts(d) or None

    def pick_stickers(self, phrase: str, stickers: list) -> list | None:
        listing = "\n".join(f"{s['index']}: {s.get('key', '')} {''.join(s.get('emoji') or []) if isinstance(s.get('emoji'), list) else s.get('emoji') or ''}"
                            for s in stickers)
        d = self._json("LLM_RESOLVE", PICK_SYSTEM, f"{llm.fence('STICKERS', listing)}\n\n{llm.fence('PHRASE', phrase, 400)}")
        if not isinstance(d, dict):
            return None
        valid = {s["index"] for s in stickers}
        nums = [int(n) for n in (d.get("numbers") or []) if str(n).isdigit() and int(n) in valid]
        return nums or None

    def pick_subjects(self, category: str, n: int, avoid: list) -> list | None:
        """N different, concrete subjects of a category for sticker packs (varied, not the N most obvious): a list of names, or None (no model, or an answer that is not usable)."""
        d = self._json("LLM_SUBJECTS", SUBJECTS_SYSTEM, f"{llm.fence('CATEGORY', category, 80)}\nN = {n}\nAlready made, do not repeat: {', '.join(avoid) or 'none'}")
        rows = d.get("subjects") if isinstance(d, dict) else d if isinstance(d, list) else None
        return [str(x) for x in rows] if isinstance(rows, list) else None

    def refine_delta(self, clause: str, subject: str) -> dict | None:
        """What a person wants changed about one subject when the rules could not tell: {"style_id", "size", "colour"} (any may be None), checked against the real presets by the caller."""
        d = self._json("LLM_REFINE", REFINE_SYSTEM, f"{llm.fence('SUBJECT', subject, 80)}\n{llm.fence('FEEDBACK', clause, 400)}")
        return d if isinstance(d, dict) else None

    def edit_meaning(self, text: str, context: str) -> dict | None:
        d = self._json("LLM_EDIT_MEANING", EDIT_SYSTEM,
                       llm.fence("CONTEXT", context, 1800) + "\n" + llm.fence("REQUEST", text, 600))
        if not isinstance(d, dict) or not isinstance(d.get("clear"), bool):
            return None
        try:
            confidence = float(d.get("confidence", 0))
        except (ValueError, TypeError):
            return None
        if d["clear"] and (confidence < 0.8 or d.get("case") not in ("tweak", "action", "redesign") or not str(d.get("change") or "").strip()):
            d = {**d, "clear": False, "clarification": d.get("clarification") or "What exactly should I change?"}
        return {"clear": d["clear"], "case": d.get("case"), "change": str(d.get("change") or "")[:300],
                "understood": str(d.get("understood") or "")[:200], "clarification": str(d.get("clarification") or "")[:300]}

    def name_check(self, items: list) -> dict | None:
        """items = [{index, current, caption}] -> {index: {"fits": bool, "name": str}} for the stickers the model answered about, or None (no model, or an answer that is not usable).
        A proposal only: nothing is renamed here (vision/naming.py)."""
        listing = "\n".join(f"{i['index']}: name={i['current']} | picture={i['caption']}" for i in items)
        d = self._json("LLM_NAMES", NAMES_SYSTEM, llm.fence("STICKERS", listing, 4000))
        rows = d.get("stickers") if isinstance(d, dict) else d if isinstance(d, list) else None
        if not isinstance(rows, list):
            return None
        valid = {i["index"] for i in items}
        out = {}
        for r in rows:
            if isinstance(r, dict) and str(r.get("index", "")).isdigit() and int(r["index"]) in valid:
                out[int(r["index"])] = {"fits": r.get("fits") is not False, "name": str(r.get("name") or "").strip()}
        return out or None

    def answer(self, question: str, facts: str) -> str | None:
        text = self._ask("LLM_ANSWER", ANSWER_SYSTEM, f"{llm.fence('FACTS', facts, 4000)}\n\n{llm.fence('QUESTION', question, 600)}", 300)
        return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip() or None

    def summarise(self, digest: str, previous: str) -> str | None:
        text = self._ask("LLM_SUMMARY", SUMMARY_SYSTEM, f"{llm.fence('EARLIER', previous or '(none)')}\n\n{llm.fence('TURNS', digest, 4000)}", 300)
        return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip() or None
