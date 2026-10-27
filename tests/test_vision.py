"""The vision judge (S6): parsing, one repair round, policies, caching, ledger, bounded recovery. A fake model: no network, no spend."""
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from stickerloom.flow import pipeline as pl
from stickerloom.runtime import cache as cachemod
from stickerloom.services import llm
from stickerloom.vision import judge as J
from stickerloom.vision.recovery import Plan, plan_recovery


def png(color=(220, 40, 40, 255), size=64) -> bytes:
    b = io.BytesIO()
    Image.new("RGBA", size and (size, size), color).save(b, "PNG")
    return b.getvalue()


GOOD = json.dumps({"decision": "APPROVE", "confidence": 0.9, "scores": {"concept_match": 0.9, "emoji_fit": 0.8,
                   "character_match": None, "style_match": 0.7}, "reasons": [], "suggested_emoji": None, "notes": "fine"})
BAD = json.dumps({"decision": "REJECT", "confidence": 0.8, "scores": {"concept_match": 0.2}, "reasons": ["WEAK_CONCEPT", "UNWANTED_TEXT"],
                  "suggested_emoji": "😴", "notes": "no action"})


class Fake:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, system, user, images=None, **k):
        self.calls.append((user, len(images or [])))
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a, {"model": "fake-vlm", "provider": "local", "ms": 5, "tokens_in": 10, "tokens_out": 20}


def judge(*answers, out=None, pol="FAIL_CLOSED"):
    return J.VisionJudge(complete=Fake(*answers), out=out, cache=cachemod.Cache(force_memory=True), pol=pol)


CELL = {"name": "teddy_waving", "action": "waving hello", "emoji": ["👋"], "tags": ["teddy_waving"]}


class ParsingTests(unittest.TestCase):
    def test_tolerates_fences_think_blocks_and_chatter(self):
        wrapped = "<think>hmm</think>Sure!\n```json\n" + BAD + "\n```\nHope that helps"
        j = J.parse_judgement(wrapped)
        self.assertEqual((j.decision, j.reasons, j.suggested_emoji), ("REJECT", ["WEAK_CONCEPT", "UNWANTED_TEXT"], "😴"))

    def test_rejects_unknown_codes_missing_reasons_and_bad_decisions(self):
        for bad in ('{"decision":"REJECT","reasons":[]}', '{"decision":"REJECT","reasons":["TOO_UGLY"]}',
                    '{"decision":"MAYBE","reasons":[]}', "no json at all", '{"decision":"APPROVE","reasons":"WEAK_CONCEPT"}'):
            with self.assertRaises(ValueError, msg=bad):
                J.parse_judgement(bad)

    def test_scores_are_clamped_and_nullable(self):
        j = J.parse_judgement('{"decision":"APPROVE","confidence":7,"scores":{"concept_match":-1,"style_match":null},"reasons":[]}')
        self.assertEqual((j.confidence, j.scores["concept_match"], j.scores["style_match"]), (1.0, 0.0, None))


class JudgeTests(unittest.TestCase):
    def test_approve_and_reject(self):
        self.assertEqual(judge(GOOD).judge_sticker(png(), CELL, {"subject": "teddy"}).decision, "APPROVE")
        j = judge(BAD).judge_sticker(png(), CELL)
        self.assertTrue(j.reject and j.reasons[0] == "WEAK_CONCEPT")

    def test_one_repair_round_quotes_the_problem_then_succeeds(self):
        j = judge("I think it is nice", GOOD)
        r = j.judge_sticker(png(), CELL)
        self.assertEqual(r.decision, "APPROVE")
        self.assertEqual(len(j._complete.calls), 2)
        self.assertIn("not usable", j._complete.calls[1][0])

    def test_two_bad_answers_fail_closed_and_log_the_raw_output(self):
        with tempfile.TemporaryDirectory() as td:
            j = judge("nonsense one", "nonsense two", out=Path(td))
            r = j.judge_sticker(png(), CELL, generation_id="G001", sticker_id="G001/S1")
            self.assertEqual(r.decision, "UNJUDGED")
            lines = [json.loads(l) for l in (Path(td) / "model_calls.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([l["status"] for l in lines], ["INVALID_OUTPUT", "INVALID_OUTPUT"])
            self.assertEqual(lines[0]["raw_output"], "nonsense one")
            self.assertEqual((lines[0]["kind"], lines[0]["generation_id"], lines[0]["prompt_version"]), ("VLM_STICKER", "G001", "judge_v1"))

    def test_a_model_that_is_down_is_unjudged_under_both_policies(self):
        for pol in ("FAIL_CLOSED", "DETERMINISTIC_ONLY"):
            r = judge(llm.LLMError("Cannot reach the AI service"), pol=pol).judge_sticker(png(), CELL)
            self.assertEqual(r.decision, "UNJUDGED", pol)
        self.assertIn("unavailable", judge(llm.LLMError("x")).judge_sticker(png(), CELL).notes)
        self.assertIn("DETERMINISTIC_ONLY", judge(llm.LLMError("x"), pol="DETERMINISTIC_ONLY").judge_sticker(png(), CELL).notes)

    def test_an_identical_sticker_is_never_judged_twice(self):
        j = judge(GOOD)
        a = j.judge_sticker(png(), CELL, {"subject": "teddy"})
        b = j.judge_sticker(png(), CELL, {"subject": "teddy"})
        self.assertEqual((len(j._complete.calls), a.cached, b.cached), (1, False, True))
        c = judge(GOOD, GOOD)
        c.judge_sticker(png(), CELL, {"subject": "teddy"})
        c.judge_sticker(png(), CELL, {"subject": "a different pack"})          # the context is part of the key
        self.assertEqual(len(c._complete.calls), 2)

    def test_the_reference_sticker_is_sent_as_a_second_image(self):
        j = judge(GOOD)
        j.judge_sticker(png(), CELL, reference_png=png((0, 200, 0, 255)))
        self.assertEqual(j._complete.calls[0][1], 2)
        self.assertIn("reference", j._complete.calls[0][0])

    def test_sheet_check_counts_and_flags_missing(self):
        ok = json.dumps({"ok": True, "count": 4, "isolated": True, "missing": [], "duplicated": []})
        short = json.dumps({"ok": True, "count": 3, "isolated": True, "missing": [4], "duplicated": []})
        self.assertTrue(judge(ok).check_sheet(png(size=128), (2, 2))["ok"])
        r = judge(short).check_sheet(png(size=128), (2, 2))
        self.assertFalse(r["ok"])                                    # the model said ok, the count says no
        self.assertIsNone(judge(llm.LLMError("down")).check_sheet(png(size=128), (2, 2))["ok"])

    def test_concurrency_is_bounded(self):
        import threading
        live, peak, lock = [0], [0], threading.Lock()

        def slow(system, user, images=None, **k):
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            import time
            time.sleep(0.05)
            with lock:
                live[0] -= 1
            return GOOD, {"model": "m"}
        j = J.VisionJudge(complete=slow, cache=cachemod.Cache(force_memory=True))
        items = [(png((i, 0, 0, 255)), dict(CELL, name=f"c{i}"), {}) for i in range(8)]
        j.judge_many(items, workers=2)
        self.assertLessEqual(peak[0], 2)


class GenerationTests(unittest.TestCase):
    def _gen(self, td):
        d = Path(td) / "G001"
        (d / "slices").mkdir(parents=True)
        stickers = []
        for i in range(1, 4):
            (d / "slices" / f"img-001-t-s{i}.png").write_bytes(png((i * 40, 20, 20, 255)))
            stickers.append({"index": i, "name": f"img-001-t-s{i}", "key": f"s{i}", "tags": [f"s{i}"], "emoji": ["😀"], "status": "READY",
                             "png": f"slices/img-001-t-s{i}.png", "review": {"still": "PENDING", "anim": "NONE"}, "history": [],
                             "anim_status": "NOT_REQUESTED"})
        res = {"generation_id": "G001", "number": 1, "stage": "sliced", "prompt": "teddy", "task": "teddy", "task_slug": "teddy",
               "grid": [3, 3], "stickers": stickers, "source": {"subject": "teddy"}}
        (d / "result.json").write_text(json.dumps(res), encoding="utf-8")
        (d / "events.jsonl").write_text("", encoding="utf-8")
        return Path(td)

    def test_history_lines_are_vlm_and_the_human_review_is_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            out = self._gen(td)
            import os
            os.environ["VISION_CONCURRENCY"] = "1"                          # the fake answers in call order
            self.addCleanup(os.environ.pop, "VISION_CONCURRENCY", None)
            j = J.VisionJudge(complete=Fake(GOOD, BAD, GOOD), out=out, cache=cachemod.Cache(force_memory=True))
            r = J.judge_generation(out, 1, "still", j)
            res = pl.read_result(out, 1)
            self.assertEqual((r["judged"], len(r["approved"]), r["rejected"]), (3, 2, [2]))
            s2 = res["stickers"][1]
            self.assertEqual(s2["review"]["still"], "PENDING")                  # a human decides, never the VLM
            h = s2["history"][-1]
            self.assertEqual((h["stage"], h["actor"], h["decision"], h["reason"]), ("still", "vlm", "REJECT", "WEAK_CONCEPT"))
            self.assertEqual(s2["judge"]["reasons"], ["WEAK_CONCEPT", "UNWANTED_TEXT"])
            ev = pl.read_events(out, 1)[-1]
            self.assertEqual((ev["stage"], ev["actor"], ev["decision"]), ("vlm_still", "vlm", "REJECT"))
            self.assertEqual(r["recovery"]["action"], "REGEN_CELLS")
            # a second run does not call the model again
            again = J.judge_generation(out, 1, "still", J.VisionJudge(complete=Fake(), out=out, cache=cachemod.Cache(force_memory=True)))
            self.assertEqual(again["judged"], 0)

    def test_unjudged_leaves_no_history_line(self):
        with tempfile.TemporaryDirectory() as td:
            out = self._gen(td)
            j = J.VisionJudge(complete=Fake(*[llm.LLMError("down")] * 3), out=out, cache=cachemod.Cache(force_memory=True))
            r = J.judge_generation(out, 1, "still", j)
            self.assertEqual((r["judged"], r["unjudged"]), (0, [1, 2, 3]))
            res = pl.read_result(out, 1)
            self.assertTrue(all(not s["history"] for s in res["stickers"]))
            self.assertEqual(res["stickers"][0]["judge"]["decision"], "UNJUDGED")


class RecoveryTests(unittest.TestCase):
    def test_one_or_two_rejected_regenerate_only_those(self):
        p = plan_recovery(9, [3, 7], [1, 2, 4, 5, 6, 8, 9])
        self.assertEqual((p.action, p.cells, p.attempt), ("REGEN_CELLS", [3, 7], 1))

    def test_three_rejected_is_a_new_sheet_bounded_by_three_attempts(self):
        self.assertEqual(plan_recovery(9, [1, 2, 3], [4, 5, 6, 7, 8, 9]).action, "NEW_SHEET")
        self.assertEqual(plan_recovery(9, [1, 2, 3], [4], sheet_attempts=2).attempt, 3)
        self.assertEqual(plan_recovery(9, [1, 2, 3], [4], sheet_attempts=3).action, "KEEP_PARTIAL")
        self.assertEqual(plan_recovery(9, [1, 2, 3], [], sheet_attempts=3).action, "FAILED")

    def test_a_failed_sheet_check_or_an_uncut_grid_is_a_new_sheet(self):
        self.assertEqual(plan_recovery(9, [], [1, 2], sheet_ok=False).action, "NEW_SHEET")
        self.assertEqual(plan_recovery(9, [], [], python_flags={"grid_detected"}).action, "NEW_SHEET")

    def test_chroma_risk_switches_the_key_once(self):
        p = plan_recovery(9, [2], [1], reasons={2: ["CHROMA_RISK"]}, key_colour="green")
        self.assertEqual((p.action, p.key_colour), ("SWITCH_KEY", "blue"))
        again = plan_recovery(9, [2], [1], reasons={2: ["CHROMA_RISK"]}, key_colour="blue", key_switched=True)
        self.assertEqual(again.action, "REGEN_CELLS")                          # never twice
        self.assertEqual(plan_recovery(9, [], [1], python_flags={"holes"}).action, "SWITCH_KEY")

    def test_cells_stop_after_two_attempts_and_approved_are_never_touched(self):
        p = plan_recovery(9, [3, 7], [1, 2], cell_attempts={3: 2, 7: 1})
        self.assertEqual((p.cells, p.gave_up), ([7], [3]))
        done = plan_recovery(9, [3], [1, 2], cell_attempts={3: 2})
        self.assertEqual((done.action, done.gave_up), ("KEEP_PARTIAL", [3]))
        self.assertEqual(plan_recovery(9, [], [1]).action, "NONE")

    def test_a_2x2_sheet_scales_the_threshold(self):
        self.assertEqual(plan_recovery(4, [1], [2, 3, 4]).action, "REGEN_CELLS")
        self.assertEqual(plan_recovery(4, [1, 2], [3, 4]).action, "NEW_SHEET")


if __name__ == "__main__":
    unittest.main()
