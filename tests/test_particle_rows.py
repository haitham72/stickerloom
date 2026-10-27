"""Particle rows: every SAVED version of a sticker's particles is one row under that sticker, oldest first (v1, v2...).
A new pass starts as a draft and becomes a row on Save; Save on a row replaces its motion; Save as new is another row with the same sprites;
Add to pack is the only thing that makes a pack sticker, and that sticker remembers the row and the sticker it belongs to. Older effect runs
that made something are adopted once as rows (a Kling run is ONE row, never one particle per slice). Over the real HTTP server, fake CLI only."""
import json

from stickerloom.flow import particle_sets as ps
from tests.test_particle_set import SetBase


class ParticleRowsTests(SetBase):
    def made(self):
        self.drawn()
        code, j = self.req("POST", "/api/particles", {"from_effect": self.eid})
        self.assertEqual(code, 201, j)
        return j

    def post(self, pid, act=None, **body):
        return self.req("POST", f"/api/particles/{pid}" + (f"/{act}" if act else ""), body)

    def rows(self, sid=None):
        code, j = self.req("GET", f"/api/packs/{self.pack['id']}/stickers/{sid or self.s1}/particles")
        self.assertEqual(code, 200, j)
        return j

    def test_a_pass_is_a_draft_until_save_then_one_numbered_row_and_save_as_new_is_another(self):
        s = self.made()
        j = self.rows()
        self.assertEqual(([r["id"] for r in j["rows"]], [d["id"] for d in j["drafts"]]), ([], [s["id"]]), "a pass nobody saved is a draft, never lost")

        code, saved = self.post(s["id"], motion={"preset": "rain", "params": {"count": 60}}, save=True)
        self.assertEqual(code, 200, saved)
        j = self.rows()
        self.assertEqual([(r["version"], r["id"]) for r in j["rows"]], [(1, s["id"])])
        self.assertEqual(j["drafts"], [])
        self.assertEqual(j["rows"][0]["motion"]["preset"], "rain")
        self.assertEqual(j["rows"][0]["n_sprites"], 4, "the sheet's cells live INSIDE the row, not as rows of their own")

        r = self.post(s["id"], "render", pack_id=self.pack["id"])[1]
        code, new = self.post(s["id"], "save-as-new", motion={"preset": "confetti", "params": {"count": 12}})
        self.assertEqual(code, 201, new)
        j = self.rows()
        self.assertEqual([(r_["version"], r_["id"]) for r_ in j["rows"]], [(1, s["id"]), (2, new["id"])])
        self.assertEqual((j["rows"][0]["motion"]["preset"], j["rows"][1]["motion"]["preset"]), ("rain", "confetti"), "the first row is unchanged")
        self.assertEqual((j["rows"][0]["renders"], j["rows"][1]["renders"]), (1, 0), "a burst made with the old motion stays with the old row")
        self.assertEqual(j["rows"][0]["addable"], {"render": r["id"], "pack_id": self.pack["id"]})

        self.post(new["id"], motion={"preset": "confetti", "params": {"count": 80}}, save=True)
        self.assertEqual(self.rows()["rows"][1]["motion"]["params"]["count"], 80, "Save on a row replaces that row's motion")
        self.assertEqual(len(self.rows()["rows"]), 2, "and adds no row")

    def test_add_to_pack_links_the_new_pack_sticker_to_its_row_and_sticker(self):
        s = self.made()
        self.post(s["id"], save=True)
        r = self.post(s["id"], "render", pack_id=self.pack["id"])[1]
        code, a = self.post(s["id"], "add", renders=[r["id"]], pack_id=self.pack["id"], sticker_id=self.s2)
        self.assertEqual(code, 200, a)
        made = a["added"][0]["sticker"]
        st = next(x for p in self.c.lib.snapshot()["packs"] for x in p["stickers"] if x["id"] == made)
        self.assertEqual((st["source"]["particle_set"], st["source"]["parent_sticker"]), (s["id"], self.s2))
        row2 = self.rows(self.s2)["rows"][0]
        self.assertEqual((row2["in_pack"], row2["addable"]), ([{"sticker_id": made, "pack_id": self.pack["id"], "render": r["id"]}], None))
        self.assertEqual(self.rows(self.s1)["rows"][0]["in_pack"], [], "the row of the other sticker sharing the set did not put it there")

    def test_older_runs_are_adopted_once_as_rows_and_runs_that_made_nothing_are_left_alone(self):
        s = self.made()
        burst = self.post(s["id"], "render", pack_id=self.pack["id"])[1]
        code, e = self.req("POST", "/api/effects", {"pack_id": self.pack["id"], "sticker_ids": [self.s1], "mode": "sim"})
        self.assertEqual(code, 202, e)
        _, empty = self.req("POST", "/api/effects", {"pack_id": self.pack["id"], "sticker_ids": [self.s1], "mode": "sim"})
        d = self.out / "effects" / e["id"]
        self.until(lambda: json.loads((d / "effect.json").read_text())["status"] == "READY", "the analysis")
        self.until(lambda: json.loads((self.out / "effects" / empty["id"] / "effect.json").read_text())["status"] == "READY", "the analysis")
        (d / "results").mkdir(exist_ok=True)
        (d / "results" / "R001.webm").write_bytes((self.out / "particles" / s["id"] / burst["file"]).read_bytes())
        rec = json.loads((d / "effect.json").read_text())
        rec["results"] = [{"id": "R001", "mode": "sim", "sticker_id": self.s1, "file": "results/R001.webm", "bytes": 1, "status": "READY",
                           "params": {"count": 28}, "warnings": [], "blocks": [], "checks": []}]
        (d / "effect.json").write_text(json.dumps(rec))

        made = ps.adopt_effects(self.out, self.c.lib)
        self.assertEqual([m["effect"] for m in made], [e["id"]], "the run that produced nothing is not a row")
        self.assertEqual(ps.adopt_effects(self.out, self.c.lib), [], "idempotent")
        row = next(r for r in self.rows()["rows"] if r["effect"] == e["id"])
        self.assertTrue(row["preview"], "the old burst is the row's preview")
        self.assertEqual(row["motion"]["params"]["count"], 28)
        self.assertTrue((self.out / "effects" / e["id"] / "results" / "R001.webm").is_file(), "nothing is moved or deleted")

    def test_a_drawn_sheet_never_saved_becomes_a_row_and_leaves_the_batch_list(self):
        from stickerloom.flow import pipeline as pl
        _, gn = self.drawn()
        self.assertIn(gn, [i["id"] for i in pl.history(self.out, 0, 50)["items"]], "a particle sheet no row holds stays visible")
        made = ps.adopt_effects(self.out, self.c.lib)
        self.assertEqual([(m["effect"], m["mode"]) for m in made], [(self.eid, "drawn")])
        row = self.rows()["rows"][0]
        self.assertEqual((row["label"], row["n_sprites"]), ("AI image sprites", 4))
        self.assertTrue(all(not c["missing"] for c in row["sprites"]))
        self.assertNotIn(gn, [i["id"] for i in pl.history(self.out, 0, 50)["items"]], "held by a row: shown under the sticker, not as a batch")
        self.assertTrue((self.out / f"G{gn:03d}" / "result.json").is_file(), "nothing is moved or renamed")
        self.assertEqual(ps.adopt_effects(self.out, self.c.lib), [], "idempotent")
