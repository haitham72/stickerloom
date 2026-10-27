"""Particle sets `P###` over the real HTTP server (docs/architecture.md ("Reactions")): a set belongs to pack(s) or stands alone,
*Use as particle set* saves what a run drew, unpicking never deletes a file, assign/unassign/duplicate are list edits, and a delete moves the
folder to the trash with Restore (nothing is destroyed on a click). The engine half is `tests/test_particle_set.py`; nothing here reaches a provider."""
import http.client
import json
import threading
import unittest
from unittest import mock

from stickerloom.engine.config import EngineConfig
from stickerloom.flow import particle_sets as ps
from stickerloom.generation import tasks
from stickerloom.flow import sources
from tests.test_effects_flow import sticker_png
from tests.test_golden import shape_sheet
from tests.test_live import Base, png_bytes
from tests.test_particle_set import SetBase, new_batch, blobs


class SetRoutesTests(SetBase):
    """A real console, a real pack, a real drawn 2x2 sheet linked to an effect (exactly what the wizard does)."""

    def setUp(self):
        super().setUp()
        self.base = self.req("POST", f"/api/effects/{self.eid}/particles", {"grid": "2x2", "elements": ["pink heart", "rose flower"], "go": True})
        self.assertEqual(self.base[0], 202, self.base[1])
        self.job = self.base[1]["job"]
        self.until(lambda: self.req("GET", f"/api/jobs/{self.job}")[1].get("status") in ("DONE", "FAILED"), "the sheet job")
        self.gen = self.req("GET", f"/api/jobs/{self.job}")[1].get("generation")
        self.until(lambda: self.effect().get("set", {}).get("status") == "DRAWN", "the sheet to be cut")

    def effect(self):
        return self.req("GET", f"/api/effects/{self.eid}")[1]

    def set_from_effect(self, **body):
        code, j = self.req("POST", "/api/particles", {"from_effect": self.eid, **body})
        return code, j

    # ---------- Use as particle set ----------
    def test_the_drawn_sheet_becomes_a_durable_set_kept_under_out_particles(self):
        code, j = self.set_from_effect(name="Barbie hearts")
        self.assertEqual(code, 201)
        self.assertEqual(j["name"], "Barbie hearts")
        self.assertEqual(j["packs"], [self.pack["id"]], "it defaults to the effect's own pack")
        self.assertEqual((j["n_cells"], j["n_picked"]), (4, 4))
        self.assertEqual(j["source"]["effect"], self.eid)
        self.assertTrue(all(c["url"] and not c["missing"] for c in j["cells"]))
        f = self.out / "particles" / j["id"] / "cells" / "c01.png"
        self.assertTrue(f.is_file(), "the cells are copied in, so the set outlives the batch")
        self.assertEqual(j["used_in"], [{"id": self.pack["id"], "name": "Fruits"}])
        self.assertFalse(j["trashed"])

    # ---------- P6: particles are particles, not 512 px stickers ----------
    def sprite_of(self, set_id, cell):
        import numpy as np
        from PIL import Image
        im = np.asarray(Image.open(self.out / "particles" / set_id / cell["file"]).convert("RGBA"))
        ys, xs = np.where(im[..., 3] > 4)
        return im, (int(ys.min()), int(xs.min()), im.shape[0] - 1 - int(ys.max()), im.shape[1] - 1 - int(xs.max()))

    def test_the_cells_of_a_set_are_tight_particles_not_512_px_stickers(self):
        _, j = self.set_from_effect()
        self.assertTrue(j["source"]["particles"], "the set says its cells are particles")
        for c in j["cells"]:
            im, air = self.sprite_of(j["id"], c)
            self.assertNotEqual(im.shape[:2], (512, 512), "a sticker canvas is the deliverable of a sticker, not a particle")
            self.assertTrue(c["sprite"] and (c["w"], c["h"]) == (im.shape[1], im.shape[0]), c)
            self.assertLessEqual(max(air), 2, f"cropped to the particle with at most 2 px of air: {air}")

    def test_the_cells_added_by_generate_more_are_tight_too(self):
        _, s = self.set_from_effect()
        self.req("POST", f"/api/particles/{s['id']}/more", {"grid": "2x2", "elements": ["glitter"], "go": True})
        got = self.until(lambda: (lambda x: x if x["n_cells"] >= 8 else None)(self.req("GET", f"/api/particles/{s['id']}")[1]), "the new cells")
        for c in got["cells"][4:]:
            self.assertTrue(c["sprite"])
            self.assertLessEqual(max(self.sprite_of(s["id"], c)[1]), 2)

    def test_cells_of_a_particle_batch_cannot_be_added_to_a_pack_as_stickers(self):
        """The 'Batman Lego Pieces' case: a sheet of particles must not become 512 px stickers of a pack. The way for them is Use as particle set."""
        j = self.req("GET", f"/api/jobs/{self.job}")[1]
        gn = int(j["generation"][1:])
        code, r = self.req("POST", f"/api/packs/{self.pack['id']}/stickers", {"from_generation": {"id": gn, "index": 1, "kind": "static"}})
        self.assertEqual(code, 409, r)
        self.assertIn("particles", r["error"])
        self.assertIn("particle set", r["error"], "and it says where they belong")
        code, r = self.req("POST", f"/api/generations/{gn}/add", {"pack_id": self.pack["id"]})
        self.assertEqual(code, 409, "the bulk Add of the Studio is refused too")
        self.assertEqual(next(p for p in self.c.lib.snapshot()["packs"] if p["id"] == self.pack["id"])["stickers"][2:], [], "nothing was added")

    def test_a_set_can_be_made_straight_from_a_particle_batch(self):
        gn = int(self.req("GET", f"/api/jobs/{self.job}")[1]["generation"][1:])
        code, j = self.req("POST", "/api/particles", {"from_generation": f"G{gn:03d}", "name": "Straight from the sheet"})
        self.assertEqual(code, 201, j)
        self.assertEqual((j["name"], j["n_cells"], j["packs"], j["source"]["generation"], j["source"]["particles"]), ("Straight from the sheet", 4, [], f"G{gn:03d}", True))
        self.assertTrue(all(c["sprite"] for c in j["cells"]))
        code, j = self.req("POST", "/api/particles", {"from_generation": f"G{gn:03d}", "packs": [self.pack["id"]], "picked": [1, 3]})
        self.assertEqual((code, [c["cell"] for c in j["cells"]], j["packs"]), (201, [1, 3], [self.pack["id"]]))

    def test_a_sheet_cut_as_stickers_is_not_made_into_particles_by_guessing(self):
        """G100 was cut with gutter detection as a sticker batch. The set is refused with the reason and the free way out (cut it again as particles); nothing is changed."""
        from tests.test_particle_set import new_batch
        gn = new_batch(self.out, self.tmp / "in", self.sheet)
        code, j = self.req("POST", "/api/particles", {"from_generation": f"G{gn:03d}"})
        self.assertEqual(code, 409)
        self.assertIn("cut", j["error"].lower())
        self.assertIn("particles", j["error"])
        self.assertEqual(self.req("POST", "/api/particles", {"from_generation": "G999"})[0], 404)

    def test_the_run_knows_the_sets_that_were_saved_from_it(self):
        """The wizard continues with the set (its motion step is the set's), so the run must point at it: addressable by id, not remembered by the browser."""
        _, a = self.set_from_effect()
        e = self.effect()
        self.assertEqual(e["sets"], [a["id"]])
        self.assertEqual(e["history"][-1]["decision"], "SAVE")
        _, b = self.set_from_effect(name="again")
        self.assertEqual(self.effect()["sets"], [a["id"], b["id"]])

    def test_only_the_picked_cells_are_saved(self):
        _, j = self.set_from_effect(picked=[1, 3])
        self.assertEqual([c["cell"] for c in j["cells"]], [1, 3])
        self.assertEqual(j["n_cells"], 2)

    def test_a_set_can_be_made_for_several_packs_or_none(self):
        other = self.c.lib.create_pack("Princess")
        self.c.lib.add_bytes(other["id"], sticker_png(), "png", "Owner", "static", "🙂")
        _, one = self.set_from_effect(packs=[self.pack["id"], other["id"]])
        self.assertEqual(one["packs"], [self.pack["id"], other["id"]])
        self.assertEqual([u["name"] for u in one["used_in"]], ["Fruits", "Princess"])
        _, none = self.set_from_effect(packs=[])
        self.assertEqual(none["packs"], [], "stand-alone is allowed")
        self.assertEqual(none["used_in"], [])

    def test_a_pack_that_does_not_exist_is_refused_not_silently_stored(self):
        code, j = self.set_from_effect(packs=["nope"])
        self.assertEqual(code, 404)
        self.assertEqual(self.req("GET", "/api/particles")[1]["sets"], [], "nothing was created")

    # ---------- the library's list and one set ----------
    def test_the_list_is_the_library_cards(self):
        _, a = self.set_from_effect(name="Barbie hearts")
        _, b = self.set_from_effect(name="Bat signals")
        rows = {r["id"]: r for r in self.req("GET", "/api/particles")[1]["sets"]}
        self.assertEqual(set(rows), {a["id"], b["id"]})
        self.assertEqual(rows[a["id"]]["name"], "Barbie hearts")
        self.assertEqual((rows[a["id"]]["n_cells"], rows[a["id"]]["n_picked"]), (4, 4))
        self.assertEqual(rows[a["id"]]["used_in"][0]["name"], "Fruits")
        self.assertEqual(rows[a["id"]]["picked"], [1, 2, 3, 4])

    def test_rename_and_motion_are_stored_and_linted(self):
        _, a = self.set_from_effect()
        code, j = self.req("POST", f"/api/particles/{a['id']}", {"name": "  Hearts  and  flowers ", "motion": {"preset": "confetti", "params": {"count": 40}}})
        self.assertEqual((code, j["name"]), (200, "Hearts and flowers"))
        self.assertEqual(j["motion"], {"preset": "confetti", "params": {"count": 40}})
        code, j = self.req("POST", f"/api/particles/{a['id']}", {"motion": {"preset": "explode"}})
        self.assertEqual(code, 400, "a preset the engine does not have is refused")
        code, j = self.req("POST", f"/api/particles/{a['id']}", {"motion": {"params": {"warp": 1}}})
        self.assertEqual(code, 400, "a parameter the engine does not read is refused")

    # ---------- unpicking never deletes a file (generate more appends) ----------
    def test_unpicking_a_cell_keeps_its_file(self):
        _, a = self.set_from_effect()
        _, j = self.req("POST", f"/api/particles/{a['id']}", {"picked": [1, 2]})
        self.assertEqual(j["n_picked"], 2)
        self.assertEqual(len(j["cells"]), 4, "the cells stay listed")
        self.assertEqual([c["picked"] for c in j["cells"]], [True, True, False, False])
        self.assertTrue((self.out / "particles" / a["id"] / "cells" / "c04.png").is_file(), "the unpicked cell's file is kept")
        for c in j["cells"]:
            self.assertFalse(c["missing"])
        code, _ = self.req("POST", f"/api/particles/{a['id']}", {"picked": []})
        self.assertEqual(code, 400, "a set with nothing picked cannot be saved")
        code, _ = self.req("POST", f"/api/particles/{a['id']}", {"picked": [9]})
        self.assertEqual(code, 400)

    # ---------- assign / unassign / duplicate ----------
    def test_assigning_is_a_list_edit_and_unassigning_keeps_the_set(self):
        other = self.c.lib.create_pack("Princess")
        self.c.lib.add_bytes(other["id"], sticker_png(), "png", "Owner", "static", "🙂")
        _, a = self.set_from_effect(packs=[])
        _, j = self.req("POST", f"/api/particles/{a['id']}/assign", {"packs": [self.pack["id"], other["id"]]})
        self.assertEqual(j["packs"], [self.pack["id"], other["id"]])
        self.assertEqual(j["id"], a["id"], "assigning never copies the set")
        _, j = self.req("POST", f"/api/particles/{a['id']}/unassign", {"packs": [other["id"]]})
        self.assertEqual(j["packs"], [self.pack["id"]])
        self.assertEqual(j["n_cells"], 4, "the cells are still there")

    def test_assigning_keeps_the_order_the_packs_were_given_in_not_the_order_of_their_random_ids(self):
        """The HANDOFF flake: pack ids are random hex, and `assign` sorted them, so a card's "used in" line came out in a different order from one run to the next. The order is the
        person's (first assigned, first listed); a later assign appends."""
        hi, lo = sorted([self.pack["id"], self.c.lib.create_pack("Princess")["id"]], reverse=True)      # `hi` sorts AFTER `lo`, whatever the uuids are
        third = self.c.lib.create_pack("Third")["id"]
        for pid in (hi, lo, third):
            if pid != self.pack["id"]:
                self.c.lib.add_bytes(pid, sticker_png(), "png", "Owner", "static", "🙂")
        _, a = self.set_from_effect(packs=[])
        _, j = self.req("POST", f"/api/particles/{a['id']}/assign", {"packs": [hi, lo]})
        self.assertEqual(j["packs"], [hi, lo], "the order given, not the alphabet")
        _, j = self.req("POST", f"/api/particles/{a['id']}/assign", {"packs": [third, hi]})
        self.assertEqual(j["packs"], [hi, lo, third], "a pack that is already there keeps its place; a new one goes last")

    def test_duplicate_makes_an_independent_copy(self):
        _, a = self.set_from_effect(name="Hearts")
        code, b = self.req("POST", f"/api/particles/{a['id']}/duplicate")
        self.assertEqual(code, 201)
        self.assertNotEqual(b["id"], a["id"])
        self.assertEqual(b["packs"], a["packs"])
        self.assertEqual(b["n_cells"], a["n_cells"])
        self.assertEqual(b["source"]["duplicated_from"], a["id"])
        self.assertTrue((self.out / "particles" / b["id"] / "cells" / "c01.png").is_file())
        self.req("POST", f"/api/particles/{b['id']}", {"picked": [1]})
        self.assertEqual(self.req("GET", f"/api/particles/{a['id']}")[1]["n_picked"], 4, "the copy is independent")

    # ---------- delete / restore: nothing is destroyed on a click ----------
    def test_a_set_in_use_says_which_packs_and_is_refused_until_confirmed(self):
        _, a = self.set_from_effect()
        code, j = self.req("POST", f"/api/particles/{a['id']}/delete", {})
        self.assertEqual(code, 409)
        self.assertIn("Fruits", j["error"])
        self.assertTrue((self.out / "particles" / a["id"] / "set.json").is_file(), "nothing moved")
        code, j = self.req("POST", f"/api/particles/{a['id']}/delete", {"confirm": True})
        self.assertEqual((code, j["trashed"]), (200, True))
        self.assertEqual(j["was_in"], [self.pack["id"]])
        self.assertFalse((self.out / "particles" / a["id"]).exists())
        self.assertTrue((self.out / "trash" / "particles" / a["id"] / "set.json").is_file())
        self.assertEqual([r["id"] for r in self.req("GET", "/api/particles")[1]["sets"]], [])

    def test_restore_puts_it_back_under_the_same_id_and_packs(self):
        _, a = self.set_from_effect()
        self.req("POST", f"/api/particles/{a['id']}/delete", {"confirm": True})
        self.assertEqual(self.req("GET", f"/api/particles/{a['id']}")[0], 404, "a deleted set is not served")
        code, j = self.req("POST", f"/api/particles/{a['id']}/restore")
        self.assertEqual(code, 200)
        self.assertEqual((j["id"], j["packs"], j["n_cells"]), (a["id"], [self.pack["id"]], 4))
        self.assertEqual([r["id"] for r in self.req("GET", "/api/particles")[1]["sets"]], [a["id"]])

    def test_restore_refuses_when_the_id_is_taken_again(self):
        """A live set must never be overwritten by a restore (the ids never collide by construction, so this guards a hand-edited folder)."""
        import shutil
        _, a = self.set_from_effect()
        self.req("POST", f"/api/particles/{a['id']}/delete", {"confirm": True})
        shutil.copytree(self.out / "trash" / "particles" / a["id"], self.out / "particles" / a["id"])   # a folder put back by hand
        code, j = self.req("POST", f"/api/particles/{a['id']}/restore")
        self.assertEqual(code, 409)
        self.assertIn("duplicate", j["error"].lower())
        self.assertTrue((self.out / "trash" / "particles" / a["id"] / "set.json").is_file(), "the deleted copy is still there to retry")

    def test_the_trash_is_listed_so_a_restore_does_not_depend_on_the_moment_after_a_delete(self):
        """Rule 9's spirit: Restore must be reachable later, not only on the notice shown right after the click."""
        _, a = self.set_from_effect(name="Barbie hearts")
        _, b = self.set_from_effect(name="Bat signals")
        self.req("POST", f"/api/particles/{a['id']}/delete", {"confirm": True})
        self.req("POST", f"/api/particles/{b['id']}/delete", {"confirm": True})
        code, j = self.req("GET", "/api/particles/deleted")
        self.assertEqual(code, 200)
        self.assertEqual([r["id"] for r in j["sets"]], [b["id"], a["id"]], "the one deleted last comes first")
        row = j["sets"][1]
        self.assertEqual((row["name"], row["n_cells"], row["n_picked"], row["packs"], row["deleted"], row["trashed"]), ("Barbie hearts", 4, 4, [self.pack["id"]], True, True))
        self.assertEqual(row["used_in"], [{"id": self.pack["id"], "name": "Fruits"}], "the card can say where it was")
        self.assertIsInstance(row["deleted_at"], float)
        self.assertEqual(len(row["cells"]), 4)
        self.assertTrue(all(c["url"] and not c["missing"] for c in row["cells"]), "the strip of a deleted set still shows its pictures")
        code, png = self.req("GET", row["cells"][0]["url"])
        self.assertEqual((code, bytes(png[:4])), (200, b"\x89PNG"), "the url points into the trash folder, where the files really are")
        self.assertEqual(self.req("GET", "/api/particles")[1]["sets"], [], "the live list does not show them")
        code, back = self.req("POST", f"/api/particles/{row['id']}/restore")
        self.assertEqual((code, back["id"], back["n_cells"]), (200, a["id"], 4), "restoring from the list works long after the delete")
        self.assertEqual([r["id"] for r in self.req("GET", "/api/particles/deleted")[1]["sets"]], [b["id"]])

    def test_an_empty_trash_is_an_empty_list_and_a_member_cannot_see_it(self):
        self.assertEqual(self.req("GET", "/api/particles/deleted"), (200, {"sets": []}))
        _, token = self.c.users.create("Amira", "member", False)
        for method, path in (("GET", "/api/particles/deleted"), ("POST", "/api/particles/P001/more")):
            h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
            h.request(method, path, json.dumps({}) if method == "POST" else None, {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
            r = h.getresponse(); r.read(); h.close()
            self.assertEqual(r.status, 403, f"{method} {path} is the owner's")

    def test_a_deleted_id_is_never_handed_out_again(self):
        _, a = self.set_from_effect()
        self.req("POST", f"/api/particles/{a['id']}/delete", {"confirm": True})
        _, b = self.req("POST", "/api/particles", {"name": "new"})
        self.assertNotEqual(b["id"], a["id"], "the number space is shared with the trash so a restore cannot collide")

    # ---------- the pack's studio ----------
    def test_the_pack_route_gives_the_sets_and_the_bursts_in_one_read(self):
        _, a = self.set_from_effect(name="Barbie hearts")
        j = self.req("GET", f"/api/packs/{self.pack['id']}/particles")[1]
        self.assertEqual(j["pack_id"], self.pack["id"])
        self.assertEqual([s["id"] for s in j["sets"]], [a["id"]])
        self.assertEqual(j["bursts"], [], "no burst has been rendered from this set yet")
        self.assertIn("counts", j, "the per-sticker counts the old answer carried are still there")

    def test_the_pack_route_only_lists_the_sets_of_that_pack(self):
        _, a = self.set_from_effect()
        other = self.c.lib.create_pack("Princess")
        self.c.lib.add_bytes(other["id"], sticker_png(), "png", "Owner", "static", "🙂")
        self.c.lib.add_bytes(other["id"], sticker_png(), "png", "Owner", "static", "🙂")
        j = self.req("GET", f"/api/packs/{other['id']}/particles")[1]
        self.assertEqual(j["sets"], [])

    def test_a_pack_that_does_not_exist_is_a_404(self):
        self.assertEqual(self.req("GET", "/api/packs/nope/particles")[0], 404)

    # ---------- a stand-alone set, made without a run ----------
    def test_an_empty_set_can_be_made_by_hand_for_generate_more(self):
        code, j = self.req("POST", "/api/particles", {"name": "Hearts", "elements": ["pink heart", "red heart"], "kind": "stickers"})
        self.assertEqual(code, 201)
        self.assertEqual((j["n_cells"], j["n_picked"]), (0, 0))
        self.assertEqual(j["elements"], ["pink heart", "red heart"])
        code, _ = self.req("POST", "/api/particles", {"name": "x", "kind": "explode"})
        self.assertEqual(code, 400, "only the three kinds the plan has")


class SetMoreTests(SetBase):
    """*Generate more* (docs/architecture.md ("Reactions")): price first (409 until `go`), an ordinary sheet job, and the cut cells are APPENDED to the set. Nothing existing is deleted or
    overwritten, an unpicked cell stays unpicked, and a set made by hand (no run behind it) can gain cells too. Real server, fake CLI: nothing is spent."""

    def made(self, **body):
        """A set saved from a drawn run (4 cells)."""
        self.drawn()
        code, j = self.req("POST", "/api/particles", {"from_effect": self.eid, **body})
        self.assertEqual(code, 201, j)
        return j

    def more(self, pid, **body):
        return self.req("POST", f"/api/particles/{pid}/more", {"grid": "2x2", **body})

    def one(self, pid):
        return self.req("GET", f"/api/particles/{pid}")[1]

    def wait_cells(self, pid, n):
        return self.until(lambda: (lambda s: s if s["n_cells"] >= n else None)(self.one(pid)), f"{n} cells in {pid}")

    # ---------- the price comes first ----------
    def test_it_shows_the_price_and_spends_nothing_without_a_go(self):
        s = self.made()
        before = len(self.creates())
        code, j = self.more(s["id"], elements=["glitter"])
        self.assertEqual(code, 409)
        self.assertEqual(j["estimate"]["credits"], 2.0)
        self.assertIn("2.0 credits", j["error"])
        self.assertEqual(len(self.creates()), before, "no provider job was created")
        self.assertEqual((self.one(s["id"])["n_cells"], self.one(s["id"])["sheets"] if "sheets" in self.one(s["id"]) else []), (4, []), "and the set says nothing is on its way")

    def test_the_quote_is_free_and_says_what_would_be_drawn(self):
        s = self.made(name="Barbie hearts")
        before = len(self.creates())
        code, j = self.more(s["id"], elements=["glitter", "stars"], estimate=True)
        self.assertEqual((code, j["credits"], j["n"], j["grid"], j["kind"]), (200, 2.0, 4, [2, 2], "particles"))
        self.assertEqual(j["picks"], ["glitter", "stars"])
        self.assertIn("glitter", j["prompt"])
        self.assertEqual(len(self.creates()), before)
        self.assertEqual(self.one(s["id"])["n_cells"], 4)

    def test_more_particles_than_the_sheet_has_cells_is_refused_before_any_price(self):
        s = self.made()
        code, j = self.more(s["id"], elements=["a", "b", "c", "d", "e"], go=True)
        self.assertEqual(code, 400)
        self.assertIn("at most 4", j["error"])

    def test_a_set_with_nothing_to_draw_is_a_400_not_a_charge(self):
        _, s = self.req("POST", "/api/particles", {"name": "Empty"})
        before = len(self.creates())
        code, j = self.more(s["id"], go=True)
        self.assertEqual(code, 400)
        self.assertIn("particle", j["error"])
        self.assertEqual(len(self.creates()), before)

    def test_an_unknown_or_a_deleted_set_is_a_404(self):
        code, j = self.more("P999", elements=["x"], go=True)
        self.assertEqual((code, "particle set P999" in j["error"]), (404, True), "a missing SET, not a missing route")
        s = self.made()
        self.req("POST", f"/api/particles/{s['id']}/delete", {"confirm": True})
        code, j = self.more(s["id"], elements=["x"], go=True)
        self.assertEqual((code, f"particle set {s['id']}" in j["error"]), (404, True), "a set in the trash is restored first")
        self.assertEqual(len(self.creates()), 1, "only the run's own sheet was ever created")

    # ---------- the cells are appended, nothing is touched ----------
    def test_the_new_cells_are_appended_and_nothing_existing_is_touched(self):
        s = self.made()
        self.req("POST", f"/api/particles/{s['id']}", {"picked": [1, 2]})
        old = {c["n"]: (self.out / "particles" / s["id"] / c["file"]).read_bytes() for c in s["cells"]}
        code, j = self.more(s["id"], elements=["glitter", "stars"], go=True)
        self.assertEqual(code, 202, j)
        self.assertEqual((j["id"], j["grid"], j["estimate"]), (s["id"], [2, 2], 2.0))
        got = self.wait_cells(s["id"], 8)
        self.assertEqual([c["n"] for c in got["cells"]], list(range(1, 9)), "the old cells keep their numbers; the new ones follow")
        self.assertEqual([c["picked"] for c in got["cells"]], [True, True, False, False, True, True, True, True], "an unpicked cell stays unpicked; the new ones start picked")
        for n, data in old.items():
            self.assertEqual((self.out / "particles" / s["id"] / f"cells/c{n:02d}.png").read_bytes(), data, f"cell {n} is byte for byte what it was")
        for c in got["cells"]:
            self.assertTrue(c["url"] and not c["missing"], c)
        sh = got["sheets"][0]
        self.assertEqual((sh["n"], sh["status"], sh["job"], sh["grid"], sh["elements"], sh["appended"], sh["skipped"]), (1, "DONE", j["job"], [2, 2], ["glitter", "stars"], [5, 6, 7, 8], []))
        self.assertRegex(sh["generation"], r"^G\d{3,}$")
        self.assertEqual([c.get("sheet") for c in got["cells"]], [None] * 4 + [1] * 4, "each new cell says which sheet it came from")
        self.assertEqual((got["n_cells"], got["n_picked"], got["drawing"]), (8, 6, False))
        self.assertIn("glitter", got["elements"], "what the set is made of grows with what was drawn for it")
        self.assertEqual(got["credits"], 4.0, "the run's own sheet (2.0) and this one (2.0)")
        self.assertEqual(got["history"][-1]["decision"], "APPEND")
        self.assertIn("MORE", [h["decision"] for h in got["history"]])

    def test_the_sheet_is_a_particle_batch_not_a_sticker_batch(self):
        s = self.made()
        self.more(s["id"], elements=["glitter"], go=True)
        got = self.wait_cells(s["id"], 8)
        res = self.req("GET", "/api/generations/" + str(int(got["sheets"][0]["generation"][1:])))[1]
        self.assertEqual(res.get("kind"), "particles", "cut by the exact equal grid; only an empty cell can fail")

    def test_a_second_more_appends_after_the_first(self):
        s = self.made()
        self.more(s["id"], elements=["glitter"], go=True)
        self.wait_cells(s["id"], 8)
        self.more(s["id"], elements=["stars"], go=True)
        got = self.wait_cells(s["id"], 12)
        self.assertEqual([c["n"] for c in got["cells"]], list(range(1, 13)))
        self.assertEqual([sh["status"] for sh in got["sheets"]], ["DONE", "DONE"])
        self.assertEqual([sh["appended"] for sh in got["sheets"]], [[5, 6, 7, 8], [9, 10, 11, 12]])
        self.assertEqual(got["credits"], 6.0, "the run's sheet and the two that were added")

    def test_a_set_made_by_hand_gains_cells(self):
        """`POST /api/particles` without `from_effect` makes an empty set; before this route it could never get a cell."""
        _, s = self.req("POST", "/api/particles", {"name": "Hearts", "elements": ["pink heart", "red heart"]})
        self.assertEqual(s["n_cells"], 0)
        code, j = self.more(s["id"], go=True)
        self.assertEqual(code, 202, j)
        got = self.wait_cells(s["id"], 4)
        self.assertEqual([c["n"] for c in got["cells"]], [1, 2, 3, 4])
        self.assertEqual(got["sheets"][0]["elements"], ["pink heart", "red heart"], "without `elements` it draws the set's own")
        self.assertEqual((got["n_picked"], got["credits"]), (4, 2.0))

    # ---------- in flight, failed, settled twice ----------
    def test_a_sheet_in_flight_is_visible_and_changes_nothing(self):
        s = self.made()
        with mock.patch.object(type(self.c), "fulfil_async", lambda self_, jid, after=None: None):       # the job exists and is handed over; running it is the queue's business
            code, j = self.more(s["id"], elements=["glitter"], go=True)
        self.assertEqual(code, 202, j)
        got = self.one(s["id"])
        self.assertEqual((got["n_cells"], got["drawing"], got["credits"]), (4, True, 2.0), "no cell yet, and nothing more is billed to the set before the sheet is back (2.0 is the run's own)")
        self.assertEqual((got["sheets"][0]["status"], got["sheets"][0]["job"], got["sheets"][0]["estimate"]), ("REQUESTED", j["job"], 2.0))
        row = next(r for r in self.req("GET", "/api/particles")[1]["sets"] if r["id"] == s["id"])
        self.assertTrue(row["drawing"], "the Library card can show it is being drawn")

    def test_a_failed_sheet_job_is_recorded_and_the_set_is_unharmed(self):
        from stickerloom.generation import jobs
        s = self.made()
        with mock.patch.object(type(self.c), "fulfil_async", lambda self_, jid, after=None: None):
            _, j = self.more(s["id"], elements=["glitter"], go=True)
        jobs.fail(self.out, j["job"], "the provider said no")
        got = self.one(s["id"])
        self.assertEqual((got["sheets"][0]["status"], got["n_cells"], got["drawing"]), ("FAILED", 4, False))
        self.assertIn("the provider said no", got["sheets"][0]["error"])

    def test_settling_again_adds_nothing_twice(self):
        s = self.made()
        self.more(s["id"], elements=["glitter"], go=True)
        self.wait_cells(s["id"], 8)
        for _ in range(3):
            ps.settle(self.out, s["id"])
            self.one(s["id"])
        self.assertEqual(self.one(s["id"])["n_cells"], 8)
        self.assertEqual(len(list((self.out / "particles" / s["id"] / "cells").glob("c*.png"))), 8)

    def test_a_cell_with_no_picture_is_skipped_and_the_rest_arrive(self):
        from stickerloom.generation import jobs
        _, s = self.req("POST", "/api/particles", {"name": "Hearts", "elements": ["heart"]})
        j = jobs.create(self.out, "sheet", request={"model": "x", "options": {}, "prompt": "p", "user": "local"})
        ps.request_more(self.out, s["id"], j["id"], [2, 2], ["heart"], 2.0, "local")
        ps.link_more(self.out, s["id"], j["id"], 70)
        self.batch(70, [("READY", {"still": "PENDING", "anim": "NONE"}, True), ("FAILED", {"still": "BLOCKED", "anim": "NONE"}, False),
                        ("READY", {"still": "PENDING", "anim": "NONE"}, True), ("READY", {"still": "PENDING", "anim": "NONE"}, True)], kind="particles")
        got = ps.view(self.out, self.c.lib, ps.settle(self.out, s["id"])["id"])
        self.assertEqual([c["cell"] for c in got["cells"]], [1, 3, 4])
        self.assertEqual((got["sheets"][0]["status"], got["sheets"][0]["skipped"]), ("DONE", [2]), "a cell with no picture is the one thing that cannot be used (rule 10)")

    def test_a_sheet_that_came_back_with_no_cell_waits_for_the_free_recut(self):
        """A sheet with no key screen has one free "Cut it anyway" on its batch (rule 10). The set must not close the request on an empty cut, so the cells arrive after it."""
        from stickerloom.generation import jobs
        _, s = self.req("POST", "/api/particles", {"name": "Hearts", "elements": ["heart"]})
        j = jobs.create(self.out, "sheet", request={"model": "x", "options": {}, "prompt": "p", "user": "local"})
        ps.request_more(self.out, s["id"], j["id"], [2, 2], ["heart"], 2.0, "local")
        ps.link_more(self.out, s["id"], j["id"], 71)
        blocked = ("FAILED", {"still": "BLOCKED", "anim": "NONE"}, False)
        self.batch(71, [blocked] * 4, kind="particles")
        first = ps.settle(self.out, s["id"])
        self.assertEqual((first["sheets"][0]["status"], len(first["cells"])), ("NO_CELLS", 0), "not DONE: it is looked at again")
        res = json.loads((self.out / "G071" / "result.json").read_text(encoding="utf-8"))          # the person pressed "Cut it anyway": the cells now have pictures
        import numpy as np
        from PIL import Image
        for st in res["stickers"]:
            a = np.zeros((64, 64, 4), np.uint8)
            a[16:48, 16:48] = (200, 160, 30, 255)
            Image.fromarray(a, "RGBA").save(self.out / "G071" / "slices" / f"S{st['index']}.png")
            st.update(status="READY", png=f"slices/S{st['index']}.png")
        (self.out / "G071" / "result.json").write_text(json.dumps(res), encoding="utf-8")
        again = ps.settle(self.out, s["id"])
        self.assertEqual((again["sheets"][0]["status"], len(again["cells"])), ("DONE", 4))

    def test_the_credits_of_a_set_saved_from_a_run_include_the_sheet_that_made_it(self):
        s = self.made()
        self.assertEqual(s["credits"], 2.0, "the run's own sheet cost 2.0 and the card said 'nothing spent yet'")
        self.more(s["id"], elements=["glitter"], go=True)
        self.assertEqual(self.wait_cells(s["id"], 8)["credits"], 4.0)


class SetBurstTests(SetBase):
    """A burst is made from a SET's picked cells for a PACK (docs/architecture.md ("Reactions")): `preview` (a looping WebP), `render` (the 512 px WebM, stored under `renders/` with its checks)
    and `add` (an animated sticker in the pack, tagged with the pack's emoji). Only Telegram's own limits may make a render FAILED (rule 10). Nothing here spends: the simulation is the engine's own."""

    def made(self, **body):
        self.drawn()
        code, j = self.req("POST", "/api/particles", {"from_effect": self.eid, **body})
        self.assertEqual(code, 201, j)
        return j

    def post(self, pid, act, **body):
        return self.req("POST", f"/api/particles/{pid}/{act}", body)

    def one(self, pid):
        return self.req("GET", f"/api/particles/{pid}")[1]

    # ---------- preview ----------
    def test_a_preview_is_a_looping_webp_made_from_the_picked_cells_of_the_set(self):
        s = self.made()
        code, j = self.post(s["id"], "preview", pack_id=self.pack["id"], preset="fountain", params={"magnitude": 1.5})
        self.assertEqual(code, 200, j)
        self.assertTrue(j["url"].startswith(f"/out/particles/{s['id']}/previews/") and j["url"].endswith(".webp"))
        self.assertEqual((j["preset"], j["sprites"], j["source"], j["params"]["magnitude"], j["pack_id"]), ("fountain", 4, "cells", 1.5, self.pack["id"]))
        self.assertEqual((j["params"]["sprite_px"], j["params"]["scale"]), (100, 1), "the particles are fitted to 100 px before they fly, as in the effect's own preview")
        code, webp = self.req("GET", j["url"])
        self.assertEqual((code, bytes(webp[:4])), (200, b"RIFF"))
        again = self.post(s["id"], "preview", pack_id=self.pack["id"], preset="fountain", params={"magnitude": 1.5})[1]
        self.assertEqual(again["url"], j["url"], "the same cells and sliders are the same file: previews are cached by what they were made from")
        other = self.post(s["id"], "preview", pack_id=self.pack["id"], preset="fountain", params={"magnitude": 1.5, "seed": 7})[1]
        self.assertNotEqual(other["url"], j["url"])

    def test_the_preview_uses_only_the_picked_cells(self):
        s = self.made()
        self.req("POST", f"/api/particles/{s['id']}", {"picked": [1]})
        self.assertEqual(self.post(s["id"], "preview", pack_id=self.pack["id"])[1]["sprites"], 1, "an unpicked cell stays on disk but does not fly")

    def test_a_preset_or_a_parameter_the_engine_does_not_have_is_a_400(self):
        s = self.made()
        for body in ({"preset": "explode"}, {"params": {"bogus": 1}}, {"params": {"sprite_px": 5}}, {"params": {"scale": 9}}):
            code, j = self.post(s["id"], "preview", pack_id=self.pack["id"], **body)
            self.assertEqual(code, 400, body)
            self.assertTrue(j["error"], body)

    def test_the_sets_own_motion_is_the_default(self):
        s = self.made()
        self.req("POST", f"/api/particles/{s['id']}", {"motion": {"preset": "confetti", "params": {"count": 40}}})
        j = self.post(s["id"], "preview", pack_id=self.pack["id"])[1]
        self.assertEqual((j["preset"], j["params"]["count"]), ("confetti", 40))
        j = self.post(s["id"], "preview", pack_id=self.pack["id"], preset="rain", params={"count": 12})[1]
        self.assertEqual((j["preset"], j["params"]["count"]), ("rain", 12), "what the person asks for wins over the default")

    def test_the_pack_defaults_to_the_only_pack_of_the_set(self):
        s = self.made()
        code, j = self.post(s["id"], "render")
        self.assertEqual((code, j["pack_id"]), (200, self.pack["id"]))
        other = self.c.lib.create_pack("Princess")
        self.c.lib.add_bytes(other["id"], sticker_png(), "png", "Owner", "static", "🙂")
        self.req("POST", f"/api/particles/{s['id']}/assign", {"packs": [other["id"]]})
        code, j = self.post(s["id"], "render")
        self.assertEqual(code, 400, "two packs: which one is the burst for?")
        self.assertIn("pack_id", j["error"])
        self.assertEqual(self.post(s["id"], "preview")[0], 200, "a preview of drawn cells does not depend on the pack")
        self.assertEqual(self.post(s["id"], "render", pack_id="nope")[0], 404)

    # ---------- render ----------
    def test_a_render_is_stored_with_its_checks_under_the_set(self):
        s = self.made()
        code, r = self.post(s["id"], "render", pack_id=self.pack["id"], preset="burst")
        self.assertEqual(code, 200, r)
        self.assertEqual((r["id"], r["set"], r["pack_id"], r["preset"], r["status"], r["added_to"]), ("R001", s["id"], self.pack["id"], "burst", "READY", None))
        self.assertGreater(r["bytes"], 0)
        self.assertLessEqual(r["bytes"], 256 * 1024, "Telegram's video limit")
        self.assertTrue(isinstance(r["checks"], list) and isinstance(r["warnings"], list) and isinstance(r["blocks"], list) and r["metrics"])
        f = self.out / "particles" / s["id"] / "renders" / "R001.webm"
        self.assertEqual(f.read_bytes()[:4], b"\x1a\x45\xdf\xa3", "a WebM (EBML) file")
        code, webm = self.req("GET", r["url"])
        self.assertEqual((code, bytes(webm[:4])), (200, b"\x1a\x45\xdf\xa3"))
        got = self.one(s["id"])
        self.assertEqual([x["id"] for x in got["renders"]], ["R001"])
        self.assertEqual(got["renders"][0]["url"], r["url"])
        self.assertEqual(self.req("GET", "/api/particles")[1]["sets"][0]["renders"], 1)
        r2 = self.post(s["id"], "render", pack_id=self.pack["id"], preset="rain")[1]
        self.assertEqual(r2["id"], "R002", "every render is a new burst; none replaces another")
        self.assertEqual(self.one(s["id"])["history"][-1]["decision"], "RENDER")

    def test_a_render_is_judged_by_telegrams_limits_only(self):
        """The engine's checks decide FAILED, and only for a Telegram limit; a render with warnings is READY, stored, and can be added. A FAILED one is stored too (rejection never deletes)."""
        from stickerloom.engine import effect_video as ev
        s = self.made()
        warn = {"status": "READY", "data": b"\x1a\x45\xdf\xa3warn", "checks": [{"id": "effect_tail_faded", "verdict": "WARN", "detail": "x", "value": 1, "limit": 0}],
                "blocks": [], "warnings": ["effect_tail_faded"], "metrics": {"kb": 1}}
        bad = {"status": "FAILED", "data": b"\x1a\x45\xdf\xa3bad", "checks": [{"id": "size_budget", "verdict": "BLOCK", "detail": "x", "value": 300, "limit": 256}],
               "blocks": ["size_budget"], "warnings": [], "metrics": {"kb": 300}}
        with mock.patch.object(ev, "encode_and_check", return_value=warn):
            ok = self.post(s["id"], "render", pack_id=self.pack["id"])[1]
        with mock.patch.object(ev, "encode_and_check", return_value=bad):
            no = self.post(s["id"], "render", pack_id=self.pack["id"])[1]
        self.assertEqual((ok["status"], ok["warnings"]), ("READY", ["effect_tail_faded"]))
        self.assertEqual((no["status"], no["blocks"]), ("FAILED", ["size_budget"]))
        self.assertTrue((self.out / "particles" / s["id"] / "renders" / f"{no['id']}.webm").is_file(), "a failed render is kept, not thrown away")
        code, j = self.post(s["id"], "add", renders=[no["id"]], pack_id=self.pack["id"])
        self.assertEqual(code, 409)
        self.assertIn("Telegram", j["error"])
        code, j = self.post(s["id"], "add", renders=[ok["id"]], pack_id=self.pack["id"])
        self.assertEqual(code, 200, "a warning is a warning: the person decides")

    # ---------- add ----------
    def test_add_puts_the_burst_in_the_pack_as_an_animated_sticker_tagged_with_the_packs_emoji(self):
        s = self.made()
        r = self.post(s["id"], "render", pack_id=self.pack["id"], preset="fountain")[1]
        code, a = self.post(s["id"], "add", renders=[r["id"]], pack_id=self.pack["id"])
        self.assertEqual(code, 200, a)
        self.assertEqual((a["pack_id"], [x["render"] for x in a["added"]]), (self.pack["id"], [r["id"]]))
        pack = next(p for p in self.c.lib.snapshot()["packs"] if p["id"] == self.pack["id"])
        st = next(x for x in pack["stickers"] if x["id"] == a["added"][0]["sticker"])
        self.assertEqual(st["type"], "animated")
        self.assertIn("🍓", st["emoji"])
        self.assertIn("❤", st["emoji"], "tagged with the emoji of the pack (Telegram needs at least one)")
        self.assertEqual((st["source"]["particle_set"], st["source"]["render"], st["source"]["preset"]), (s["id"], r["id"], "fountain"))
        self.assertTrue(st["name"].startswith(s["name"]))
        self.assertEqual(self.one(s["id"])["renders"][0]["added_to"], self.pack["id"])
        self.assertEqual(self.one(s["id"])["history"][-1]["decision"], "APPROVE", "the click is the person's approval, recorded")
        code, j = self.post(s["id"], "add", renders=[r["id"]], pack_id=self.pack["id"])
        self.assertEqual(code, 409, "the same burst is not put in the same pack twice by accident")
        self.assertIn("already", j["error"])

    def test_add_without_ids_takes_every_ready_burst_of_that_pack_not_yet_added(self):
        s = self.made()
        a = self.post(s["id"], "render", pack_id=self.pack["id"], preset="burst")[1]
        b = self.post(s["id"], "render", pack_id=self.pack["id"], preset="rain")[1]
        self.post(s["id"], "add", renders=[a["id"]], pack_id=self.pack["id"])
        code, j = self.post(s["id"], "add", pack_id=self.pack["id"])
        self.assertEqual((code, [x["render"] for x in j["added"]]), (200, [b["id"]]))
        code, j = self.post(s["id"], "add", pack_id=self.pack["id"])
        self.assertEqual(code, 409, "nothing is left to add")

    def test_a_burst_that_does_not_exist_is_a_404(self):
        s = self.made()
        code, j = self.post(s["id"], "add", renders=["R009"], pack_id=self.pack["id"])
        self.assertEqual(code, 404)
        self.assertIn("R009", j["error"])

    # ---------- the pack's studio ----------
    def test_the_pack_studio_lists_the_bursts_rendered_for_it(self):
        s = self.made()
        r = self.post(s["id"], "render", pack_id=self.pack["id"], preset="vortex")[1]
        self.post(s["id"], "add", renders=[r["id"]], pack_id=self.pack["id"])
        j = self.req("GET", f"/api/packs/{self.pack['id']}/particles")[1]
        b = j["bursts"][0]
        self.assertEqual((b["set"], b["id"], b["preset"], b["status"], b["added_to"], b["set_name"]), (s["id"], r["id"], "vortex", "READY", self.pack["id"], s["name"]))
        self.assertTrue(b["url"] and not b["missing"])
        self.assertIn("params", b)

    def test_a_burst_belongs_to_the_pack_it_was_rendered_for_even_when_the_set_is_not_assigned_there(self):
        s = self.made(packs=[])
        r = self.post(s["id"], "render", pack_id=self.pack["id"])[1]
        j = self.req("GET", f"/api/packs/{self.pack['id']}/particles")[1]
        self.assertEqual(j["sets"], [], "the set is stand-alone")
        self.assertEqual([b["id"] for b in j["bursts"]], [r["id"]], "but the burst made for this pack is listed")

    # ---------- a set whose particles are the pack's own stickers ----------
    def test_a_set_of_the_packs_own_stickers_bursts_those_stickers(self):
        code, s = self.req("POST", "/api/particles", {"from_effect": self.eid})
        self.assertEqual((code, s["source"]["kind"], s["n_cells"]), (201, "stickers", 0), s)
        self.assertEqual(s["source"]["sticker_ids"], [self.s1, self.s2], "it remembers WHICH stickers the run was made with")
        j = self.post(s["id"], "preview", pack_id=self.pack["id"])[1]
        self.assertEqual((j["sprites"], j["source"]), (2, "stickers"))
        r = self.post(s["id"], "render")[1]
        self.assertEqual(r["status"], "READY")

    def test_a_drawn_set_with_no_cell_yet_has_no_particles_and_says_so(self):
        """Its pack's stickers must not fly in their place: that would show particles the set does not have."""
        _, s = self.req("POST", "/api/particles", {"name": "Empty"})
        code, j = self.post(s["id"], "preview", pack_id=self.pack["id"])
        self.assertEqual(code, 409)
        self.assertIn("Generate more", j["error"])

    def test_a_set_made_by_hand_of_the_packs_stickers_needs_the_pack(self):
        _, s = self.req("POST", "/api/particles", {"name": "Fruit bursts", "kind": "stickers"})
        code, j = self.post(s["id"], "preview")
        self.assertEqual((code, "pack_id" in j["error"]), (400, True))
        j = self.post(s["id"], "preview", pack_id=self.pack["id"])[1]
        self.assertEqual((j["sprites"], j["source"]), (2, "stickers"), "every sticker of the pack when the set names none")

    def test_bursts_are_the_owners(self):
        s = self.made()
        _, token = self.c.users.create("Amira", "member", False)
        for act in ("preview", "render", "add"):
            h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
            h.request("POST", f"/api/particles/{s['id']}/{act}", json.dumps({"pack_id": self.pack["id"]}), {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
            r = h.getresponse(); r.read(); h.close()
            self.assertEqual(r.status, 403, act)


class SetUnitTests(unittest.TestCase):
    """The storage rules on their own, without a server."""

    def setUp(self):
        import shutil
        import tempfile
        from pathlib import Path
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "out"
        self.out.mkdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_the_id_is_a_P_and_the_folder_is_out_particles(self):
        s = ps.create(self.out, None, name="Hearts")
        self.assertRegex(s["id"], r"^P\d{3}$")
        self.assertTrue((self.out / "particles" / s["id"] / "set.json").is_file())
        self.assertEqual(ps.read(self.out, s["id"])["name"], "Hearts")

    def test_a_set_with_no_name_gets_a_default_and_a_long_one_is_cut(self):
        self.assertEqual(ps.create(self.out, None, name="   ")["name"], "particles")
        self.assertLessEqual(len(ps.create(self.out, None, name="x" * 200)["name"]), ps.MAX_NAME)

    def test_duplicate_element_names_are_folded(self):
        self.assertEqual(ps.create(self.out, None, elements=["heart", "Heart", " HEART ", "rose"])["elements"], ["heart", "rose"])

    def test_reading_a_set_that_does_not_exist_is_a_404_not_a_crash(self):
        with self.assertRaises(ps.SetError) as e:
            ps.read(self.out, "P999")
        self.assertEqual(e.exception.code, 404)
        for bad in ("nope", "", "X001", "P1x"):
            with self.assertRaises(ps.SetError):
                ps.read(self.out, bad)

    def test_a_path_that_looks_like_an_id_cannot_escape_the_folder(self):
        with self.assertRaises(ps.SetError):
            ps.read(self.out, "P001/../../etc")

class ChatAllowPayloadTests(unittest.TestCase):
    """Rule 10 on the chat surface needs the SERVER to say what may be allowed: a tile that cannot know cannot offer the override (no stubs, rule 6).
    No server and no provider: `ConsoleTools.generation` reads one result.json, so this is the payload contract alone."""

    def setUp(self):
        import shutil
        import tempfile
        from pathlib import Path
        from stickerloom.engine.config import EngineConfig
        from stickerloom.flow import pipeline as pl
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.out = self.tmp / "out"
        pl.gen_dir(self.out, 1).mkdir(parents=True)
        res = {"generation_id": "G001", "prompt": "p", "grid": [2, 2], "stage": "sliced", "owner": "local", "source": {}, "reviews": {},
               "stickers": [{"index": i, "key": f"k{i}", "name": f"S{i}", "emoji": "X", "tags": [], "status": "READY", "review": {"still": "PENDING", "anim": "NONE"},
                             "report": [], "metrics": {"bbox": [0, 0, 40, 40], "fg_px": 900}, "anim_status": "NOT_REQUESTED", "anim_report": [], "history": [], "png": None, "webm": None} for i in (1, 2)]}
        res["stickers"][0].update(status="FAILED", reason="holes", review={"still": "BLOCKED", "anim": "NONE"},
                                  report=[{"name": "holes", "ok": False, "severity": "BLOCK", "value": 1, "limit": 0}])
        # a cell with nothing cut may never be allowed, and a cell with one may: the metrics say so (gates.still_problem)
        res["stickers"][1].update(still_override=["inside_cell"])          # a person already allowed this one
        pl.write_result(self.out, 1, res)

    def tools(self):
        from stickerloom.agent.tools import ConsoleTools
        return ConsoleTools(type("C", (), {"out": self.out, "lib": None})(), {"id": "local", "role": "owner", "can_spend": True})

    def test_the_batch_carries_the_one_allow_block_of_the_engine_and_stickers_carry_no_copy(self):
        """One source of truth: `gates.allow_info` (index lists per kind), exactly as the Studio's route sends it. The per-sticker booleans that used to mirror it are gone."""
        from stickerloom.flow import gates, pipeline as pl
        card = self.tools().generation("G001")
        self.assertEqual(card["allow"], gates.allow_info(pl.read_result(self.out, 1)))
        self.assertEqual(sorted(card["allow"]), ["animation", "still", "video_sheet"], "item 2 added the G3 video-sheet block to the same allow_info")
        for kind in ("still", "animation", "video_sheet"):
            self.assertEqual(sorted(card["allow"][kind]), ["allowed", "can", "final", "undo", "why"], "the tile needs all five to draw the override")
        for c in card["stickers"]:
            self.assertNotIn("allow", c, "no second copy on the sticker")
            self.assertIn("waived", c)

    def test_a_judgement_call_is_allowable_and_an_allowed_one_can_be_taken_back(self):
        card = self.tools().generation("G001")
        cells = {c["index"]: c for c in card["stickers"]}
        still = card["allow"]["still"]
        self.assertEqual(still["can"], [1], "holes is a judgement call: the chat tile may offer 'use it anyway'")
        self.assertIn("hole", still["why"]["1"].lower() + (cells[1]["reason"] or ""))
        self.assertEqual(still["allowed"], [2], "the permission is visible, so the tile can say 'allowed by you'")
        self.assertEqual(cells[2]["waived"], ["inside_cell"])
        self.assertNotIn(2, still["can"], "what was allowed is not offered twice")

    def test_the_fake_says_the_same_shape(self):
        from stickerloom.agent.tools import FakeTools
        f = FakeTools({"G001": {"generation": "G001", "stickers": [{"id": "G001/S1", "index": 1, "status": "FAILED", "reason": "holes", "still": "BLOCKED"}]}})
        card = f.generation("G001")
        self.assertEqual(sorted(card["allow"]["still"]), ["allowed", "can", "final", "undo", "why"])
        self.assertEqual((card["allow"]["still"]["can"], card["allow"]["still"]["allowed"]), ([1], []))
        self.assertEqual(card["stickers"][0]["waived"], [])
        self.assertNotIn("allow", card["stickers"][0])
        self.assertEqual(f.gens["G001"]["stickers"][0].get("allow"), None, "the fixture is never mutated")


class EditReferenceTests(unittest.TestCase):
    """P11b / P12 of the UI/UX spec with the REAL tools: the picture of a tweak or a new action is the batch's sheet, and after a slice was edited and saved it is the sheet rebuilt with that slice
    fixed; one slice goes as its own cell at the sheet's resolution, scaled up only under the provider's minimum. The console is a double with the same save_ref."""

    def setUp(self):
        import shutil
        import tempfile
        from pathlib import Path
        from stickerloom.flow import pipeline as pl
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.out = self.tmp / "out"
        d = pl.gen_dir(self.out, 1)
        (d / "source").mkdir(parents=True)
        self.d = d
        import cv2
        import numpy as np
        self.raw = np.full((1200, 1200, 3), (30, 200, 40), np.uint8)
        cv2.imwrite(str(d / "source" / "sheet.png"), self.raw)
        self.fixed = self.raw.copy()
        self.fixed[:600, :600] = (200, 30, 30)
        cv2.imwrite(str(d / "source" / "sheet_fixed.png"), self.fixed)
        res = {"generation_id": "G001", "prompt": "p", "grid": [2, 2], "stage": "sliced", "owner": "local", "reviews": {},
               "source": {"sheet_copy": "source/sheet.png", "sheet_fixed": "source/sheet_fixed.png"},
               "stickers": [{"index": i, "key": f"k{i}", "status": "READY", "review": {"still": "PENDING", "anim": "NONE"}, "metrics": {"cell": [(i - 1) % 2 * 600, (i - 1) // 2 * 600, 600, 600]},
                             "png": None} for i in (1, 2, 3, 4)]}
        pl.write_result(self.out, 1, res)
        self.saved = []

        class C:
            pass
        c = C()
        c.out, c.lib = self.out, None
        c.save_ref = lambda data, name: (self.saved.append((name, data)) or {"id": f"R{len(self.saved):03d}"})
        from stickerloom.agent.tools import ConsoleTools
        self.tools = ConsoleTools(c, {"id": "local", "role": "owner", "can_spend": True})

    def test_the_picture_is_the_sheet_the_chat_works_on_and_the_fixed_one_after_an_edit(self):
        import io
        import numpy as np
        from PIL import Image
        r = self.tools.sheet_reference("G001")
        self.assertEqual((r["file"], r["px"]), ("source/sheet_fixed.png", 1200))
        im = np.asarray(Image.open(io.BytesIO(self.saved[-1][1])).convert("RGB"))
        self.assertTrue(np.array_equal(im, np.asarray(Image.open(self.d / "source" / "sheet_fixed.png").convert("RGB"))), "the bytes sent are the fixed sheet")
        from stickerloom.flow import pipeline as pl
        res = pl.read_result(self.out, 1)
        res["source"].pop("sheet_fixed")
        pl.write_result(self.out, 1, res)
        self.assertEqual(self.tools.sheet_reference("G001")["file"], "source/sheet.png", "no edit yet: the raw sheet")

    def test_one_slice_goes_as_its_own_cell_and_is_scaled_only_under_the_minimum(self):
        import io
        from PIL import Image
        r = self.tools.slice_reference("G001/S2")
        self.assertEqual((r["px"], r["from_px"], r["scaled"], r["min_px"]), (600, 600, False, 400))
        sent = Image.open(io.BytesIO(self.saved[-1][1]))
        self.assertEqual(sent.size, (600, 600))
        self.assertEqual(sent.convert("RGB").getpixel((10, 10)), (40, 200, 30), "S2 is the right-hand cell of the sheet: untouched green")
        r = self.tools.slice_reference("G001/S1", min_px=800)
        self.assertEqual((r["px"], r["from_px"], r["scaled"]), (800, 600, True), "under the minimum: scaled up, and it says so")
        self.assertEqual(Image.open(io.BytesIO(self.saved[-1][1])).convert("RGB").getpixel((10, 10)), (30, 30, 200), "S1 is the cell of the FIXED sheet, not of the raw one")

    def test_a_batch_with_no_sheet_says_so(self):
        import shutil
        from stickerloom.agent.tools import ToolError
        shutil.rmtree(self.d / "source")
        with self.assertRaises(ToolError):
            self.tools.sheet_reference("G001")

    def test_the_chat_tile_picture_changes_url_when_the_slice_is_edited(self):
        """Save in the editor returns to the AI with the slice updated: the browser must not show the old picture, so the url carries the edit time (as the Studio's does)."""
        from stickerloom.flow import pipeline as pl
        res = pl.read_result(self.out, 1)
        res["stickers"][0].update(png="slices/S1.png", rendered_at=5.5)
        res["stickers"][1].update(png="slices/S2.png", edited_at=123.4)
        res["stickers"][2].update(png="slices/S3.png")
        pl.write_result(self.out, 1, res)
        urls = [s["png"] for s in self.tools.generation("G001")["stickers"][:3]]
        self.assertEqual(urls, ["/out/G001/slices/S1.png?e=5.5", "/out/G001/slices/S2.png?e=123.4", "/out/G001/slices/S3.png?e=0"])
