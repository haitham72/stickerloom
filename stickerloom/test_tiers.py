"""Explicit test tiers for the checkout. The ordinary unittest discovery command is unchanged."""
from __future__ import annotations

import json
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENGINE = Path(__file__).resolve().parent / "engine"
RESULT_DIR = ROOT / ".test-results"
SLOW_RESULT = RESULT_DIR / "slow.json"

# Tier 3 is deliberately file-based: every test in these six modules runs, including future tests added to them.
SLOW_MODULES = (
    "tests.test_golden",
    "tests.test_effect_video",
    "tests.test_allow_still",
    "tests.test_anim_speed",
    "tests.test_engine",
    "tests.test_verify",
)

# A small deterministic smoke set. It is not the full suite and never claims to verify the app.
FAST_TESTS = (
    "tests.test_names",
    "tests.test_envfile",
    "tests.test_explain",
    "tests.test_jobs.JobsTests",
    "tests.test_jobs.TransientProviderFailureTests",
    "tests.test_jobs.CloseLoopTests",
    "tests.test_sources",
    "tests.test_refine_subjects",
    "tests.test_style_tiles",
    "tests.test_effect_plan",
    "tests.test_api_contract",
    "tests.test_batches.RemoveABatch",
)

# Reviewed regression profiles, not a random sample or a cap that drops tests.
# The exact item-0 gate runs FIRST in every focused profile. Focused is never automatic:
# at most once at the end of a gate/recovery/video-sheet phase (README.md, the test budget).
GOLDEN_GATE = "tests.test_golden.GoldenPathTests.test_switching_a_verdict_back_swaps_the_finished_clip_and_renders_nothing"
FOCUSED_BASE = (GOLDEN_GATE, "tests.test_verify.RunnerTests", "tests.test_js.JavaScriptTests")
FOCUSED_PROFILES = {
    "animation": FOCUSED_BASE,
    "recovery": FOCUSED_BASE + ("tests.test_job_recovery.RecoveryTests",),
    "video-sheet": FOCUSED_BASE + ("tests.test_sheet_allow.SheetAllows", "tests.test_sheet_allow.SheetAllowRoutes", "tests.test_allow_still.ContractTests"),
}
FOCUSED_TESTS = FOCUSED_BASE + (
    "tests.test_job_recovery.RecoveryTests", "tests.test_sheet_allow.SheetAllows",
    "tests.test_sheet_allow.SheetAllowRoutes", "tests.test_allow_still.ContractTests",
)

# One reviewable map from a changed production module to the test classes that import/exercise it.
# Unknown areas fail with the known keys; the runner never guesses from filenames or silently runs nothing.
MODULE_TESTS = {
    "engine/video": (
        "tests.test_golden.GoldenPathTests", "tests.test_effect_video.EffectVideoTests",
        "tests.test_effect_video.ScreenFlatnessTests", "tests.test_allow_still.VideoSheetAnimationTests",
        "tests.test_anim_speed.SameValuesTests", "tests.test_anim_speed.CacheTests",
        "tests.test_engine.VideoTests", "tests.test_verify.VideoStageTests",
        "tests.test_verify.SlotStageTests", "tests.test_verify.AnimExtraTests",
        "tests.test_verify.LayoutSlicingTests", "tests.test_jobs.CloseLoopTests",
    ),
    "engine/verify": (
        "tests.test_verify.SheetStageTests", "tests.test_verify.StillStageTests",
        "tests.test_verify.VideoSheetTests", "tests.test_verify.VideoStageTests",
        "tests.test_verify.SlotStageTests", "tests.test_verify.AnimExtraTests",
        "tests.test_verify.PackTests", "tests.test_verify.RunnerTests",
        "tests.test_verify.LayoutSlicingTests", "tests.test_allow_still.StillsTests",
        "tests.test_allow_still.TechnicalBlocksTests", "tests.test_allow_still.GatingTests",
        "tests.test_verify_fixtures",
    ),
    "flow/gates": (
        "tests.test_golden.GoldenPathTests", "tests.test_allow_still.StillsTests",
        "tests.test_allow_still.VideoSheetAnimationTests", "tests.test_allow_still.GatingTests",
        "tests.test_allow_still.ContractTests", "tests.test_console.ConsoleTests",
    ),
    "flow/pipeline": (
        "tests.test_golden.GoldenPathTests", "tests.test_engine.SheetTests",
        "tests.test_engine.VideoTests", "tests.test_console.ConsoleTests",
        "tests.test_hardening.OneWriterOfResults",
    ),
    "generation/jobs": (
        "tests.test_jobs.JobsTests", "tests.test_jobs.TransientProviderFailureTests",
        "tests.test_tasks.InboxTests",
    ),
    "console/server": (
        "tests.test_console.ConsoleTests", "tests.test_api_contract.Base",
        "tests.test_batches.RemoveRoutes", "tests.test_tasks.InboxTests",
    ),
}


def _imported_areas(path: Path) -> set[str]:
    """The production modules one test file imports directly, as `flow/gates`-style paths.

    `from stickerloom.flow import gates as G` (how the tests reach for a module to patch it) records BOTH
    `flow` and `flow/gates`, because a change to either is a change to what that test exercises."""
    import ast

    areas: set[str] = set()

    def add(dotted: str) -> None:
        parts = [p for p in dotted.split(".") if p]
        if parts and parts[0] != "stickerloom":
            return
        parts = parts[1:]
        if parts:
            areas.add("/".join(parts))

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return areas
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            base = node.module
            if not base.startswith("stickerloom"):
                continue
            add(base)
            if node.level == 0 and node.module == "stickerloom" or base.count(".") >= 1:
                for alias in node.names:
                    add(f"{base}.{alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("stickerloom."):
                    add(alias.name)
    return {a for a in areas if a and not a.startswith("tests")}


_DERIVED: dict[str, tuple[str, ...]] | None = None


def derived_map() -> dict[str, tuple[str, ...]]:
    """Every test file that imports a production module directly is a floor for that module's area.

    This is a LOWER bound, not the whole truth: a test can also reach production code over HTTP
    without importing it, which is exactly why MODULE_TESTS above holds hand-picked areas with the
    classes that matter. Read this map as "these tests certainly touch this file", never as
    "nothing else does"."""
    global _DERIVED
    if _DERIVED is not None:
        return _DERIVED
    found: dict[str, set[str]] = {}
    tests_dir = ROOT / "tests"
    for path in sorted(tests_dir.glob("test_*.py")):
        dotted = f"tests.{path.stem}"
        for area in _imported_areas(path):
            found.setdefault(area, set()).add(dotted)
    _DERIVED = {k: tuple(sorted(v)) for k, v in sorted(found.items())}
    return _DERIVED


def normalise_area(value: str) -> str:
    area = str(value).strip().replace("\\", "/")
    if area.endswith(".py"):
        area = area[:-3]
    area = area.removeprefix("./").removeprefix("stickerloom/")
    if "/" not in area:
        area = area.replace(".", "/")
    return area


def suite_for(tier: str, area: str | None = None) -> unittest.TestSuite:
    loader = unittest.defaultTestLoader
    if tier == "fast":
        names = FAST_TESTS
    elif tier == "focused":
        if area and area not in FOCUSED_PROFILES:
            raise ValueError(f"No focused profile for {area}. Choose: {', '.join(FOCUSED_PROFILES)}")
        names = FOCUSED_PROFILES[area] if area else FOCUSED_TESTS
    elif tier == "slow":
        names = SLOW_MODULES
    elif tier == "area":
        key = normalise_area(area or "")
        if key in MODULE_TESTS:                       # hand-picked first: the classes that matter for that area
            names = MODULE_TESTS[key]
        elif key in derived_map():                    # otherwise every test file that imports it
            names = derived_map()[key]
        else:
            curated = ', '.join(sorted(MODULE_TESTS))
            raise ValueError(f"No area map for {key or '(empty)'}. Hand-picked areas: {curated}. "
                             f"{len(derived_map())} more are derived from the tests' own imports; "
                             f"a module with no test importing it maps to nothing, which means run nothing.")
    else:
        raise ValueError("tier must be fast, focused, area or slow")
    return loader.loadTestsFromNames(names)


def newest_engine_mtime() -> float:
    return max((p.stat().st_mtime for p in ENGINE.rglob("*.py") if p.is_file()), default=0.0)


def _write_slow_result(body: dict) -> None:
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SLOW_RESULT.with_suffix(".tmp")
    tmp.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    tmp.replace(SLOW_RESULT)


def slow_status() -> dict:
    engine_mtime = newest_engine_mtime()
    try:
        body = json.loads(SLOW_RESULT.read_text(encoding="utf-8"))
        result_mtime = SLOW_RESULT.stat().st_mtime
    except (OSError, ValueError, TypeError):
        return {"state": "never", "engine_mtime": engine_mtime, "ran": 0, "skipped": 0}
    current = result_mtime >= engine_mtime
    verified = current and bool(body.get("success")) and not body.get("skipped")
    return {**body, "state": "verified" if verified else "failed" if current else "stale",
            "engine_mtime": engine_mtime, "result_mtime": result_mtime}


def run(tier: str, area: str | None = None) -> int:
    try:
        suite = suite_for(tier, area)
    except ValueError as e:
        print(f"ERROR   {e}")
        return 2
    selected = suite.countTestCases()
    if not selected:
        print(f"ERROR   {tier} selected no tests; this is not a passing verification.")
        return 2
    print(f"TIER {tier.upper()}  selected {selected} test(s)" + (f" for {normalise_area(area or '')}" if area else ""), flush=True)
    if tier == "focused":
        print("FOCUSED REGRESSIONS ONLY — not full-suite or media verification; the exact golden gate runs first.", flush=True)
    started = time.time()
    result = unittest.TextTestRunner(verbosity=1, failfast=tier == "focused").run(suite)
    skipped = len(result.skipped)
    ran = result.testsRun - skipped
    print(f"TIER {tier.upper()}  RAN {ran} test(s)")
    print(f"TIER {tier.upper()}  SKIPPED {skipped} test(s)" + (" — SKIPPED IS NOT PASSED" if skipped else ""))
    success = result.wasSuccessful() and skipped == 0
    print(f"TIER {tier.upper()}  RESULT " + ("PASS" if success else "INCOMPLETE" if result.wasSuccessful() else "FAIL"))
    if tier == "slow":
        _write_slow_result({
            "finished_at": round(time.time(), 3), "duration_s": round(time.time() - started, 3),
            "selected": selected, "ran": ran, "skipped": skipped,
            "failures": len(result.failures), "errors": len(result.errors), "success": success,
        })
    return 0 if success else 1
