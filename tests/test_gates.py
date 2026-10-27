"""WP-8 approval gates: STICKERLOOM_GATES=manual|auto|off.

manual: today's behavior (a human approves; Python blocks need "Use it anyway").
auto: gates.review(by="auto") approves what the verifier passes, auto-overrides
judgement failures with a logged reason, and stops on any TECHNICAL failure.
off: no gate pauses (every APPROVE resolves itself), but verification still runs
and a TECHNICAL failure blocks the pack.

Nothing here reaches a provider; the batches are synthetic result.json files.
"""
import pytest

from stickerloom.engine.config import EngineConfig
from stickerloom.flow import gates, pipeline as pl


def _check(name, stage="still", ok=False, severity="BLOCK", detail="", data=None):
    return {"name": name, "ok": ok, "detail": detail, "severity": severity,
            "stage": stage, "value": None, "limit": None, "data": data or {}}


def _sticker(i, status="READY", still="PENDING", anim="NONE", report=None,
             anim_report=None, metrics=None, anim_status="NOT_REQUESTED"):
    return {"index": i, "key": f"s{i}", "emoji": "🙂", "name": f"S{i} sticker",
            "status": status, "reason": None, "anim_status": anim_status, "anim_reason": None,
            "review": {"still": still, "anim": anim},
            "report": report if report is not None else [], "anim_report": anim_report or [],
            "metrics": metrics if metrics is not None else {}, "anim_metrics": {},
            "history": [], "still_override": [], "anim_override": []}


def _res(stickers, stage="sliced", sheets=None, anim_source=False):
    res = {"generation_id": "G001", "task_slug": "t", "grid": [1, len(stickers)],
           "stage": stage, "error": None,
           "reviews": {"plan": {"decision": "APPROVE", "by": "human", "ts": 0.0, "note": None},
                       "video_sheet": {}, "pack": None},
           "stickers": stickers, "video_sheets": sheets or []}
    if anim_source:
        res["source"] = {"has_video": True, "video_path": "videos_gen/vid.mp4"}
    return res


def _write(out, gid, res):
    d = pl.gen_dir(out, gid)
    d.mkdir(parents=True, exist_ok=True)
    pl.write_result(out, gid, res)
    return out, gid


# ---------- mode resolution ----------

def test_default_is_manual(monkeypatch):
    monkeypatch.delenv("STICKERLOOM_GATES", raising=False)
    assert gates.gates_mode() == "manual"


def test_env_selects_mode(monkeypatch):
    monkeypatch.setenv("STICKERLOOM_GATES", "auto")
    assert gates.gates_mode() == "auto"
    monkeypatch.setenv("STICKERLOOM_GATES", "OFF")
    assert gates.gates_mode() == "off"


def test_bad_env_falls_back_to_manual(monkeypatch):
    monkeypatch.setenv("STICKERLOOM_GATES", "sometimes")
    assert gates.gates_mode() == "manual"


def test_bad_explicit_is_refused():
    with pytest.raises(pl.PipelineError):
        gates.gates_mode(explicit="sometimes")


def test_studio_switch_roundtrip(tmp_path, monkeypatch):
    monkeypatch.delenv("STICKERLOOM_GATES", raising=False)
    assert gates.gates_mode(out=tmp_path) == "manual"
    assert gates.set_gates_mode(tmp_path, "auto") == "auto"
    assert gates.gates_mode(out=tmp_path) == "auto"
    with pytest.raises(pl.PipelineError):
        gates.set_gates_mode(tmp_path, "sometimes")


# ---------- technical classification ----------

def test_technical_blocks_lists_only_technical():
    report = [_check("static_file"),                       # TECHNICAL
              _check("inside_cell"),                       # judgement
              _check("chroma_risk", severity="WARN")]      # not a block at all
    assert gates.technical_blocks(report) == ["static_file"]
    assert not gates.is_technical_failure(_check("inside_cell"))
    assert gates.is_technical_failure(_check("size_budget", stage="anim"))
    assert gates.is_technical_failure(_check("loop_seam", stage="anim", data={"error": "boom"}))


# ---------- manual: today's behavior is unchanged ----------

def test_manual_approves_passing_still(tmp_path, monkeypatch):
    monkeypatch.delenv("STICKERLOOM_GATES", raising=False)
    _write(tmp_path, 1, _res([_sticker(1), _sticker(2)]))
    r = gates.review(tmp_path, 1, "still", "APPROVE", "ready")
    assert r["indices"] == [1, 2]
    res = pl.read_result(tmp_path, 1)
    assert [s["review"]["still"] for s in res["stickers"]] == ["APPROVED", "APPROVED"]
    assert res["stickers"][0]["still_override"] == []


def test_manual_blocked_still_needs_override(tmp_path, monkeypatch):
    monkeypatch.delenv("STICKERLOOM_GATES", raising=False)
    rep = [_check("inside_cell", detail="3px on cell border")]
    st = _sticker(1, status="FAILED", still="PENDING", report=rep,
                  metrics={"bbox": [1, 1, 9, 9], "fg_px": 500})
    _write(tmp_path, 1, _res([st]))
    with pytest.raises(pl.PipelineError):
        gates.review(tmp_path, 1, "still", "APPROVE", 1)
    assert pl.read_result(tmp_path, 1)["stickers"][0]["review"]["still"] != "APPROVED"


# ---------- auto: approves passing, overrides judgement, stops on TECHNICAL ----------

def test_auto_approves_passing_stills(tmp_path, monkeypatch):
    monkeypatch.delenv("STICKERLOOM_GATES", raising=False)
    _write(tmp_path, 1, _res([_sticker(1), _sticker(2)]))
    r = gates.review(tmp_path, 1, "still", "APPROVE", "ready", by="auto")
    assert r["indices"] == [1, 2]


def test_auto_overrides_judgement_still(tmp_path, monkeypatch):
    monkeypatch.delenv("STICKERLOOM_GATES", raising=False)
    rep = [_check("inside_cell", detail="3px on cell border")]
    st = _sticker(1, status="FAILED", still="PENDING", report=rep,
                  metrics={"bbox": [1, 1, 9, 9], "fg_px": 500})
    _write(tmp_path, 1, _res([_sticker(2), st]))
    r = gates.review(tmp_path, 1, "still", "APPROVE", "ready", by="auto")
    assert sorted(r["indices"]) == [1, 2]
    got = pl.read_result(tmp_path, 1)["stickers"][1]
    assert got["review"]["still"] == "APPROVED"
    assert got["still_override"] == ["inside_cell"]
    assert got["report"][0]["severity"] == "WARN"
    assert any("auto gates: allowed anyway" in (h.get("reason") or "") for h in got["history"])


def test_auto_stops_on_technical_still(tmp_path, monkeypatch):
    monkeypatch.delenv("STICKERLOOM_GATES", raising=False)
    rep = [_check("static_file", detail="900KB over the limit")]
    st = _sticker(1, status="FAILED", still="PENDING", report=rep, metrics={})
    _write(tmp_path, 1, _res([st]))
    with pytest.raises(pl.PipelineError):
        gates.review(tmp_path, 1, "still", "APPROVE", "ready", by="auto")
    res = pl.read_result(tmp_path, 1)
    assert res["stickers"][0]["review"]["still"] != "APPROVED"
    assert res["stickers"][0].get("still_override") == []


def test_auto_stops_on_empty_cell(tmp_path, monkeypatch):
    monkeypatch.delenv("STICKERLOOM_GATES", raising=False)
    rep = [_check("blank_cell", detail="foreground below minimum")]
    st = _sticker(1, status="FAILED", still="PENDING", report=rep,
                  metrics={"bbox": None, "fg_px": 0})
    _write(tmp_path, 1, _res([st]))
    with pytest.raises(pl.PipelineError):
        gates.review(tmp_path, 1, "still", "APPROVE", "ready", by="auto")


def test_auto_anim_overrides_judgement_and_stops_on_technical(tmp_path, monkeypatch):
    monkeypatch.delenv("STICKERLOOM_GATES", raising=False)
    ok_anim = [_check("size_budget", stage="anim", ok=True),
               _check("loop_seam", stage="anim", ok=True),
               _check("codec_vp9", stage="anim", ok=True)]
    good = _sticker(1, anim="PENDING", anim_status="READY", anim_report=ok_anim)
    good["anim_metrics"] = {}
    good["webm"] = "anim/S1.webm"
    bad_rep = [_check("loop_seam", stage="anim", detail="seam 0.31")]
    bad = _sticker(2, anim="PENDING", anim_status="FAILED", anim_report=bad_rep)
    bad["anim_metrics"] = {}
    bad["webm"] = "anim/S2.webm"
    _write(tmp_path, 1, _res([good, bad], anim_source=True))
    r = gates.review(tmp_path, 1, "anim", "APPROVE", "ready", by="auto")
    assert r["indices"] == [1, 2]
    got = pl.read_result(tmp_path, 1)["stickers"][1]
    assert got["review"]["anim"] == "APPROVED"
    assert got["anim_override"] == ["loop_seam"]

    tech_rep = [_check("size_budget", stage="anim", detail="900KB")]
    tech = _sticker(1, anim="PENDING", anim_status="FAILED", anim_report=tech_rep)
    tech["anim_metrics"] = {}
    tech["webm"] = "anim/S1.webm"
    _write(tmp_path, 2, _res([tech], anim_source=True))
    with pytest.raises(pl.PipelineError):
        gates.review(tmp_path, 2, "anim", "APPROVE", "ready", by="auto")
    assert pl.read_result(tmp_path, 2)["stickers"][0]["review"]["anim"] != "APPROVED"


def test_auto_sheet_allows_judgement_and_stops_on_technical(tmp_path, monkeypatch):
    monkeypatch.delenv("STICKERLOOM_GATES", raising=False)

    def sheet(blocked, verify):
        return {"id": "A1", "slots": [1], "grid": [1, 1], "file": "video_sheet/A1/sheet.png",
                "layout": "video_sheet/A1/layout.json", "video": None, "status": "BUILT",
                "ts": 0.0, "verify": verify, "video_checks": [], "blocked": blocked,
                "canvas": [100, 100], "video_prompt": "", "slot_fill": 0.8}

    st = _sticker(1, still="APPROVED")
    v = sheet("no_outline_on_sheet", [_check("no_outline_on_sheet", stage="video_sheet", detail="ring in slot 1")])
    _write(tmp_path, 1, _res([st], sheets=[v]))
    r = gates.review(tmp_path, 1, "video_sheet", "APPROVE", "A1", by="auto")
    assert r["status"] == "APPROVED"
    got = pl.read_result(tmp_path, 1)["video_sheets"][0]
    assert got["blocked"] is None and got["sheet_override"] == ["no_outline_on_sheet"]

    v2 = sheet("slots_match_approved", [_check("slots_match_approved", stage="video_sheet", detail="filled []")])
    _write(tmp_path, 2, _res([_sticker(1, still="APPROVED")], sheets=[v2]))
    with pytest.raises(pl.PipelineError):
        gates.review(tmp_path, 2, "video_sheet", "APPROVE", "A1", by="auto")


# ---------- off: no pauses, TECHNICAL still blocks ----------

def test_off_resolves_human_calls_like_auto(tmp_path, monkeypatch):
    monkeypatch.setenv("STICKERLOOM_GATES", "off")
    rep = [_check("inside_cell", detail="3px on cell border")]
    st = _sticker(1, status="FAILED", still="PENDING", report=rep,
                  metrics={"bbox": [1, 1, 9, 9], "fg_px": 500})
    _write(tmp_path, 1, _res([_sticker(2), st]))
    r = gates.review(tmp_path, 1, "still", "APPROVE", "ready")   # no by="auto": off never pauses
    assert sorted(r["indices"]) == [1, 2]


def _pack_res(emoji_ok=True, with_technical=False):
    ok_anim = [_check("size_budget", stage="anim", ok=True),
               _check("loop_seam", stage="anim", ok=True),
               _check("codec_vp9", stage="anim", ok=True)]
    anim_rep = list(ok_anim)
    if with_technical:
        anim_rep = [_check("size_budget", stage="anim", detail="900KB")]
    st = _sticker(1, status="READY", still="APPROVED", anim="APPROVED",
                  anim_status="READY", anim_report=anim_rep)
    st["emoji"] = "🙂" if emoji_ok else ""
    st["webm"] = "anim/S1.webm"
    return _res([st], stage="video_sliced")


@pytest.mark.parametrize("mode", ["manual", "auto", "off"])
def test_technical_failure_blocks_pack_in_every_mode(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("STICKERLOOM_GATES", mode)
    # (a) pack_limits itself is TECHNICAL: no emoji, Telegram would reject the set.
    _write(tmp_path, 1, _pack_res(emoji_ok=False))
    (pl.gen_dir(tmp_path, 1) / "anim").mkdir(parents=True, exist_ok=True)
    (pl.gen_dir(tmp_path, 1) / "anim" / "S1.webm").write_bytes(b"x" * 64)
    by = "auto" if mode == "auto" else "human"
    with pytest.raises(pl.PipelineError):
        gates.review(tmp_path, 1, "pack", "APPROVE", by=by)
    # (b) a stored TECHNICAL check on a final sticker stops the pack before pack_limits.
    _write(tmp_path, 2, _pack_res(emoji_ok=True, with_technical=True))
    (pl.gen_dir(tmp_path, 2) / "anim").mkdir(parents=True, exist_ok=True)
    (pl.gen_dir(tmp_path, 2) / "anim" / "S1.webm").write_bytes(b"x" * 64)
    with pytest.raises(pl.PipelineError) as e:
        gates.review(tmp_path, 2, "pack", "APPROVE", by=by)
    assert "TECHNICAL" in str(e.value)


def test_clean_pack_still_passes(tmp_path, monkeypatch):
    monkeypatch.delenv("STICKERLOOM_GATES", raising=False)
    cfg = EngineConfig()
    _write(tmp_path, 1, _pack_res(emoji_ok=True))
    (pl.gen_dir(tmp_path, 1) / "anim").mkdir(parents=True, exist_ok=True)
    (pl.gen_dir(tmp_path, 1) / "anim" / "S1.webm").write_bytes(b"x" * 64)
    orig = cfg.video_max_bytes
    assert orig > 64
    r = gates.review(tmp_path, 1, "pack", "APPROVE")
    assert r["final"] == [1]
