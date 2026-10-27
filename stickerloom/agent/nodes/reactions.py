"""Reactions intent handlers. State is owned by Agent; no module state."""
from __future__ import annotations
import re
from .. import refine
from ..resolver import particles_intent
from ..tools import ToolError
from ..lifecycle.turn import Turn, State
from .shared import _credits, _with_pending

class ReactionsNodes:
    _ALONE = re.compile(r"\b(?:on (?:their|its) own|alone|stand-?alone|no pack|without (?:a |any )?(?:pack|sticker)s?)\b", re.I)

    _PSTOP = {"pack", "packs", "set", "sets", "stickers", "sticker", "the", "a", "an", "my", "particle", "particles", "them", "it", "also", "more", "generate", "make", "draw", "delete", "remove",
                  "restore", "use", "for", "on", "in", "to", "some", "few", "create", "add", "bring", "back", "get", "me", "of", "with", "and", "please", "i", "want", "need"}

    def n_effects(self, state: State) -> dict:
        """Legacy Kling intent enters the same scoped wizard; spending stays behind its quote and click."""
        t = state['turn']
        packs = [p for p in self.tools.packs() if p['count']]
        hit = self._named(t.text, packs)
        pick = hit[0] if len(hit)==1 else packs[0] if len(packs)==1 else None
        if pick:
            t.cards.append({'type':'particles_scope', 'pack_id':pick['id'], 'pack':pick['name'], 'kind':'video'})
            t.reply = f"Open Video from scratch for **{pick['name']}**. It starts and ends empty; the price appears before your paid click."
        else:
            return self._particles_make(t)
        return {}

    def _words(self, s: str) -> set:
        return {refine.stem(w) for w in re.findall(r"[a-z\u0600-\u06ff]+", s.lower()) if w not in self._PSTOP}

    def _named(self, text: str, rows: list) -> list:
        """The rows (packs or sets, by their name) that the message names: all the words of a name, else any word of it."""
        said = self._words(text)
        full = [r for r in rows if self._words(r["name"]) and self._words(r["name"]) <= said]
        return full or [r for r in rows if self._words(r["name"]) & said]

    def n_particles(self, state: State) -> dict:
        """"make particles for my Barbie pack" / "generate more" / "delete the bat particles" / "restore ..." / "also use them for the Princess pack". Reads and free list edits happen at once; a sheet
        (credits) is a plan with its price that waits for the go-ahead, like every other paid thing in the chat."""
        t: Turn = state["turn"]
        t.trace.retitle("particles")
        kind = particles_intent(t.text, bool(t.sess.get("particles"))) or "make"
        try:
            return getattr(self, "_particles_" + kind)(t)
        except ToolError as e:
            t.reply = f"I could not do that: {e}"
            t.trace.end("could not", ok=False)
            return {}

    def _particle_scope(self, t):
        focus = t.sess.get('focus') or {}
        gen = t.res.generation or focus.get('generation')
        explicit = re.search(r'\bG(\d+)\b', t.text, re.I)
        if explicit:
            gen = f"G{int(explicit[1]):03d}"
        ids = list(t.selected or t.res.stickers or focus.get('stickers') or [])
        number = re.search(r'\b(?:sticker|S)\s*(\d+)\b', t.text, re.I)
        if number and gen:
            ids = [f"{gen}/S{int(number[1])}"]
        packs = self._named(t.text, self.tools.packs())
        pid = packs[0]['id'] if len(packs)==1 else None
        return self.tools.particle_owners(pack_id=pid, generation=None if pid else gen, sticker_ids=ids or None), gen, ids

    def _focus_set(self, t: Turn, sets: list) -> tuple[dict | None, str]:
        """Explicit set name overrides; otherwise newest owned set, focused set, sole set, then chips."""
        hit = self._named(t.text, sets)
        if len(hit)==1:
            return hit[0], ''
        if len(hit)>1:
            return None, 'several'
        owners, gen, ids = self._particle_scope(t)
        wanted = {o['sticker_id'] for o in owners}
        mine = [s for s in sets if any(o['sticker_id'] in wanted for o in s.get('owner', []))]
        if mine:
            ordered = [pid for o in owners for pid in o.get('particles', [])]
            newest = next((s for pid in reversed(ordered) for s in mine if s['id']==pid), None)
            return newest or max(mine, key=lambda s:(s.get('created') or 0, s['id'])), ''
        fid = (t.sess.get('particles') or {}).get('set')
        pick = next((s for s in sets if s['id']==fid), None)
        words = self._words(t.text) - {'burst', 'render', 'save', 'next'}
        if pick or (len(sets)==1 and not words):
            return pick or sets[0], ''
        return None, '' if ids or owners else ' '.join(sorted(words))

    def _no_set(self, t: Turn, sets: list, why: str, verb: str) -> dict:
        if why == "several":
            t.reply = "Several particle sets match. Which one? " + ", ".join(f"**{s['name']}**" for s in self._named(t.text, sets)[:4])
        elif why:
            t.reply = f"I could not find a particle set called \"{why}\" to {verb}." + (" You have " + ", ".join(f"**{s['name']}**" for s in sets[:4]) + "." if sets else " There are none yet: say \"make particles for my <pack> pack\".")
        else:
            t.reply = f"Which particle set should I {verb}? " + (", ".join(f"**{s['name']}**" for s in sets[:4]) if sets else "There are none yet.")
        t.chips = [{"label":s["name"], "text":f"{verb} {s['name']} particles"} for s in sets[:4]]
        t.trace.end("asked which set")
        return {}

    def _particles_alone(self, t: Turn, why: str = "") -> dict:
        """Particles on their own: no sticker owns them; the set is made from the request when the price is confirmed."""
        from ...flow import particle_sets as ps
        els = ps.request_elements(self._ALONE.sub(" ", t.text), 4)
        if not els:
            t.reply = "Which particles? For example: particles for lipsticks and ribbons."
            return {}
        if not self.tools.live():
            t.reply = "Drawing particles needs live generation, and no provider is available here. Nothing was started."
            t.trace.end("no provider", ok=False)
            return {}
        spec = {"type": "particles", "op": "alone", "request": t.text, "pack": None, "pack_name": None, "set": None, "set_name": None, "grid": "2x2", "elements": els,
                "estimate": self.tools.estimate("image")}
        return self._offer_particles(t, spec, (why or "") + f"I'll draw {len(els)} particles on their own: {', '.join(els)}. No sticker owns them; you can preview, render and download them, or make a pack of them")

    def _particles_make(self, t: Turn) -> dict:
        packs = [p for p in self.tools.packs() if p["count"]]
        owners, gen, ids = self._particle_scope(t)
        if self._ALONE.search(t.text):
            return self._particles_alone(t)
        from ...flow import particle_sets as _ps
        hit0 = self._named(t.text, packs) if packs else []
        named = [] if hit0 else _ps.request_elements(t.text, 4)
        if named and not owners and not gen:                 # "create particles for lipsticks and ribbons": which pack, or on their own?
            if not packs:
                return self._particles_alone(t, "You have no packs yet, so ")
            last = packs[-1]
            t.reply = f"Sure: {', '.join(named)}. For which pack, or on their own?"
            t.chips = ([{"label": p["name"], "text": f"make particles for my {p['name']} pack"} for p in packs[-3:][::-1] if p is not last]
                       + [{"label": f"The last pack, {last['name']}", "text": f"make particles for my {last['name']} pack"},
                          {"label": "On their own", "text": f"create particles on their own for {' and '.join(named)}"}])
            t.trace.end("asked which pack")
            return {}
        if gen and not owners:
            t.cards.append({'type':'particles_approve', 'generation':gen})
            t.reply = 'Approve this batch as a pack first, so the particles have a sticker to live in.'
            return {}
        if not packs:
            t.reply = 'Choose a batch to approve as a pack, then create particles for its stickers.'
            return {}
        hit = self._named(t.text, packs)
        pick = hit[0] if len(hit) == 1 else packs[0] if len(packs) == 1 else None
        if not hit and owners:
            pick = next((p for p in packs if p['id'] == owners[0]['pack_id']), None)
        if not pick:
            t.reply = ("Which pack? " if not hit else "Several packs match. Which one? ") + "Say its name, for example \"make particles for my " + (hit or packs)[0]["name"] + " pack\"."
            t.chips = [{"label": p["name"], "text": f"make particles for my {p['name']} pack"} for p in (hit or packs)[:4]]
            t.trace.end("asked which pack")
            return {}
        if not self.tools.live():
            t.reply = "Drawing particles needs live generation, and no provider is available here. Nothing was started."
            t.trace.end("no provider", ok=False)
            return {}
        owners = owners or self.tools.particle_owners(pack_id=pick["id"])
        current, _ = self._focus_set(t, self.tools.particle_sets())
        if current and owners and "fresh" not in t.text.lower():
            return self._particles_more(t)
        els = self.tools.particle_options(pick["id"], 4)
        spec = {"type": "particles", "op": "make", "pack": pick["id"], "pack_name": pick["name"], "set": None, "grid": "2x2", "elements": els, "estimate": self.tools.estimate("image"), "owners": owners, "fresh": "fresh" in t.text.lower()}
        return self._offer_particles(t, spec, f"I'll draw {len(els)} particles for the **{pick['name']}** pack: {', '.join(els)}. When the sheet is back they join a set owned by its selected stickers")

    def _particles_more(self, t: Turn) -> dict:
        sets = self.tools.particle_sets()
        s, why = self._focus_set(t, sets)
        if not s:
            return self._no_set(t, sets, why, "add particles to")
        if not self.tools.live():
            t.reply = "Drawing particles needs live generation, and no provider is available here. Nothing was started."
            return {}
        if (s.get('source') or {}).get('kind') in ('video', 'stickers'):
            owners = s.get('owner') or []
            t.cards.append({'type':'particles_scope', 'pack_id':owners[0]['pack_id'] if owners else None, 'pack':s['name'], 'kind':'video' if s['source']['kind']=='video' else 'pack', 'set':s['id']})
            t.reply = f"Open Generate more for **{s['name']}**. New cells join the same set; any AI price appears before the paid click."
            return {}
        els = (s.get("elements") or [])[:4]
        if not els:
            t.reply = f"**{s['name']}** has no particle names to draw yet. Tell me which, for example \"generate more hearts and stars for {s['name']}\"."
            return {}
        spec = {"type": "particles", "op": "more", "set": s["id"], "set_name": s["name"], "pack": None, "grid": "2x2", "elements": els, "estimate": self.tools.estimate("image")}
        return self._offer_particles(t, spec, f"I'll draw {len(els)} more particles for **{s['name']}** ({', '.join(els)}). The cells it has stay as they are; the new ones are added")

    def _offer_particles(self, t: Turn, spec: dict, what: str) -> dict:
        card = {"type": "particles_plan", "op": spec["op"], "pack": spec.get("pack_name"), "pack_id": spec.get("pack"), "set": spec.get("set"), "name": spec.get("set_name"),
                "grid": spec["grid"], "elements": spec["elements"], "estimate": spec["estimate"]}
        if spec.get('estimate') is None:
            t.reply = 'The price is unavailable. Try again to get a price before starting.'
            return {}
        t.sess["pending"] = spec
        if self.tools.live() and t.sess["settings"].get("ask_before_spending", True):
            t.cards.append(card)
            t.reply = f"{what} ({_credits(spec['estimate'])}). Go ahead?"
            t.chips = [{"label": "Draw them", "action": "confirm"}, {"label": "Not yet", "action": "cancel"}]
            t.trace.end("ready")
        else:
            self.n_confirm({"turn": _with_pending(t, spec)})
        return {}

    def _do_particles(self, t: Turn, p: dict) -> None:
        if p["op"] == "alone":
            t.trace.retitle("drawing particles")
            try:
                r = self.tools.particles_alone(p["request"], p["grid"])
            except ToolError as e:
                t.sess["pending"] = p
                t.reply = f"I couldn't start the sheet: {e}. I kept the plan: press Draw them to try again, or Not yet to drop it."
                t.chips = [{"label": "Draw them", "action": "confirm"}, {"label": "Not yet", "action": "cancel"}]
                t.trace.end("not started", ok=False)
                return
            t.sess["particles"] = {"set": r["set"], "pack": None}
            t.cards.append({"type": "particles", "set": r["set"], "name": r.get("name") or r["set"], "pack_id": None, "job": r["job"], "estimate": r.get("estimate"), "drawing": True})
            t.reply = f"Drawing {len(p['elements'])} particles on their own" + (f" ({_credits(r.get('estimate'))})" if r.get("estimate") else "") + ". When the sheet is cut they arrive in the set; open it to preview, render and download them, or make a pack of them."
            t.trace.end("sheet started")
            return
        if p["op"] == "delete":
            try:
                self.tools.particles_delete(p["set"], True)
            except ToolError as e:
                t.reply = f"I couldn't delete it: {e}"
                return
            t.reply = f"**{p['set_name']}** is in the trash; nothing is destroyed. Say \"restore the {p['set_name']}\" or press Restore under Library > Particles > Deleted."
            return
        t.trace.retitle("drawing particles")
        try:
            r = self.tools.particles_start(p.get("set"), p.get("pack"), p["grid"], p["elements"], owners=p.get("owners"), fresh=p.get("fresh", False))
        except ToolError as e:
            t.sess["pending"] = p                                    # a refused start must not cost the plan
            t.reply = f"I couldn't start the sheet: {e}. I kept the plan: press Draw them to try again, or Not yet to drop it."
            t.chips = [{"label": "Draw them", "action": "confirm"}, {"label": "Not yet", "action": "cancel"}]
            t.trace.end("not started", ok=False)
            return
        t.sess["particles"] = {"set": r["set"], "pack": p.get("pack")}
        t.cards.append({"type": "particles", "set": r["set"], "name": p.get("set_name") or f"{p.get('pack_name')} particles", "pack_id": p.get("pack"), "job": r["job"], "estimate": r.get("estimate"), "drawing": True})
        t.reply = f"Drawing {len(p['elements'])} particles" + (f" ({_credits(r.get('estimate'))})" if r.get("estimate") else "") + ". When the sheet is cut they join the set by themselves; open the pack's particle studio to move them."
        t.trace.end("sheet started")

    def _particles_render(self, t):
        sets = self.tools.particle_sets()
        s, why = self._focus_set(t, sets)
        if not s:
            return self._no_set(t, sets, why, 'render')
        owners, _, _ = self._particle_scope(t)
        pid = (owners[0] if owners else (s.get('owner') or [{}])[0]).get('pack_id')
        if not pid:
            t.reply = 'Link these particles to a sticker first.'
            return {}
        r = self.tools.particles_burst(s['id'], pid)
        t.sess['particles'] = {'set':s['id'], 'pack':pid}
        t.cards.append({'type':'particles', 'set':s['id'], 'name':s['name'], 'pack_id':pid})
        t.reply = f"Rendered a burst from **{s['name']}**. Open it to review and add it to the pack."
        return {}

    def _particles_add(self, t):
        sets = self.tools.particle_sets()
        s, why = self._focus_set(t, sets)
        if not s:
            return self._no_set(t, sets, why, 'add')
        owners, _, _ = self._particle_scope(t)
        pid = (owners[0] if owners else (s.get('owner') or [{}])[0]).get('pack_id')
        if not pid:
            t.reply = 'Link these particles to a sticker first.'
            return {}
        self.tools.particles_add(s['id'], pid)
        t.reply = f"**{s['name']}**: in the pack ✓."
        return {}

    def _particles_delete(self, t: Turn) -> dict:
        sets = self.tools.particle_sets()
        s, why = self._focus_set(t, sets)
        if not s or (not self._named(t.text, sets) and why == ""):
            return self._no_set(t, sets, why, "delete")
        used = [u["name"] for u in s.get("used_in") or []]
        if used:                                                     # in use: say which packs and wait (the API refuses without confirm, too)
            t.sess["pending"] = {"type": "particles", "op": "delete", "set": s["id"], "set_name": s["name"]}
            t.reply = f"**{s['name']}** is used by {', '.join(used)}. Deleting it takes it off those packs (it goes to the trash and can be restored). Delete it?"
            t.chips = [{"label": "Delete it", "action": "confirm"}, {"label": "Keep it", "action": "cancel"}]
            return {}
        self.tools.particles_delete(s["id"], False)
        t.reply = f"**{s['name']}** is in the trash; nothing is destroyed. Say \"restore the {s['name']}\" or press Restore under Library > Particles > Deleted."
        return {}

    def _particles_restore(self, t: Turn) -> dict:
        gone = self.tools.particle_deleted()
        hit = self._named(t.text, gone) or (gone if len(gone) == 1 else [])
        if len(hit) != 1:
            t.reply = ("Which deleted set? " + ", ".join(f"**{s['name']}**" for s in (hit or gone)[:4])) if gone else "Nothing is in the trash."
            return {}
        self.tools.particles_restore(hit[0]["id"])
        t.sess["particles"] = {"set": hit[0]["id"], "pack": None}
        t.reply = f"**{hit[0]['name']}** is back, with its cells and sticker links."
        return {}

    def _particles_assign(self, t: Turn) -> dict:
        sets = self.tools.particle_sets()
        named = self._named(t.text, sets)
        fid = (t.sess.get('particles') or {}).get('set')
        s = named[0] if len(named)==1 else next((x for x in sets if x['id']==fid), None)
        if not s:
            return self._no_set(t, sets, '', 'reuse')
        owners, _, _ = self._particle_scope(t)
        if not owners:
            t.reply = 'Choose the sticker to link these particles to.'
            return {}
        self.tools.particles_link(s['id'], [o['sticker_id'] for o in owners])
        names = [p['name'] for p in self.tools.packs() if p['id'] in {o['pack_id'] for o in owners}]
        t.reply = f"**{s['name']}** is linked to those stickers ({', '.join(names)}). Nothing was spent; it is the same set."
        return {}
