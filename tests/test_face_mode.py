"""Face-only emoji mode: `expand` detects emoji requests,
renders template v4 with the face-only bank, and v1-v3 stay byte-identical. No provider, no files outside the repo."""
import re
import unittest

from stickerloom.generation import emotions, prompter

LIMB = re.compile(r"\b(" + "|".join(re.escape(w) for w in emotions.LIMB_WORDS) + r")\b", re.IGNORECASE)


def limbs(text: str) -> list[str]:
    return sorted(set(LIMB.findall(text or "")))


class FaceModeTests(unittest.TestCase):
    def test_ordinary_requests_are_untouched_v3(self):
        p = prompter.expand("teddy bear")
        self.assertEqual((p["template_version"], p["slots"].get("face")), (prompter.TEMPLATE_VERSION, False))
        self.assertIn("full body", p["sheet_prompt"])
        self.assertIn(prompter.MARGIN.split(",")[0], p["stickers"][0]["prompt"])

    def test_emoji_requests_render_face_v4(self):
        for task in ("generic emojis", "an emoji pack", "cat emoticons", "my dog as a smiley"):
            p = prompter.expand(task, (3, 3))
            self.assertEqual((p["template_version"], p["slots"].get("face")), (4, True), task)
            self.assertIn("Faces only", p["sheet_prompt"])
            self.assertIn("face and head alone", p["stickers"][0]["prompt"])

    def test_images_stay_face_only_while_video_may_use_a_detached_gesture(self):
        p = prompter.expand("generic emojis", (3, 3))
        self.assertIn("Faces only: never a body", p["sheet_prompt"])          # the template's own negation steers the model…
        self.assertIn("Faces only: no bodies", p["video_prompt"])
        self.assertIn('detached hand', p['video_prompt'])
        self.assertIn('no visible arm connecting it to a body', p['video_prompt'])
        texts = [p["sheet_prompt"]] + [s["prompt"] for s in p["stickers"]]
        texts += [c["label"] for c in p["slots"]["cells"]]
        texts = [t.replace("Faces only: never a body, arms, hands, legs, feet, ears, tails or props.", "")
                  .replace("Faces only: no bodies, no hands, no ears, no tails, no props.", "")
                  .replace("Faces only: no bodies, no hands.", "") for t in texts]      # the short video prompt's negation (video_short_v1)  # …everywhere else a limb word is a bug
        for t in texts:
            self.assertEqual(limbs(t), [], t[:120])
        for group in emotions.FACE_GROUPS.values():
            for suffix, label, emoji, motion in group:
                self.assertEqual(limbs(label + " " + motion), [], suffix)
        from stickerloom.generation import actions
        self.assertEqual(len(actions.PRESETS), 4)
        for preset, toks in actions.PRESETS.items():
            self.assertEqual(len(toks), 9, preset)
            for tok in toks:
                label, motion = actions.FACE_SENTENCES[tok]
                self.assertEqual(limbs(label + " " + motion), [], f"{preset}/{tok}")

    def test_v4_files_exist_and_v1_v3_are_untouched(self):
        for name in ("sheet_3x3_v4", "sheet_2x2_v4", "single_1x1_v4", "video_v4"):
            self.assertTrue((prompter.TEMPLATES / f"{name}.txt").is_file(), name)
        v3 = (prompter.TEMPLATES / "sheet_3x3_v3.txt").read_text(encoding="utf-8")
        self.assertIn("full body", v3)
        self.assertNotIn("Faces only", v3)

    def test_a_saved_v4_plan_rebuilds_to_v4(self):
        import json
        p = prompter.expand("generic emojis", (2, 2))
        again = prompter.validate_plan(json.loads(json.dumps(p)))
        self.assertEqual((again["template_version"], "Faces only" in again["sheet_prompt"]), (4, True))
        self.assertEqual([c["label"] for c in p["slots"]["cells"]],
                         [c["label"] for c in prompter.expand("generic emojis", (2, 2))["slots"]["cells"]])

    def test_face_sheets_claim_the_preset_grid_in_order(self):
        from stickerloom.generation import actions
        p = prompter.expand("generic emojis", (3, 3))
        self.assertEqual(p["slots"].get("preset"), "core-v1")
        self.assertEqual([s["key"] for s in p["stickers"]],
                         [f"generic_emojis_{t}" for t in actions.PRESETS["core-v1"]])
        self.assertEqual([s["emoji"] for s in p["stickers"]],
                         [actions.ACTION_BANK[t]["emoji"] for t in actions.PRESETS["core-v1"]])
        first = p["stickers"][0]
        self.assertEqual(first["tags"][:3], [first["key"], "happy", "smile"])
        for s in p["stickers"]:
            self.assertEqual(limbs(" ".join(s["tags"])), [])

    def test_an_explicit_preset_wins_and_other_flows_are_untouched(self):
        p = prompter.expand("social-v1 falcon", (3, 3))
        self.assertEqual(([s["key"] for s in p["stickers"]][0], p["slots"].get("preset")), ("falcon_hello", "social-v1"))
        q = prompter.expand("teddy bear", (3, 3))
        self.assertIsNone(q["slots"].get("preset"))
        r = prompter.expand("generic emojis", (2, 2))
        self.assertEqual((r["template_version"], len(r["stickers"])), (4, 4))


if __name__ == "__main__":
    unittest.main()
