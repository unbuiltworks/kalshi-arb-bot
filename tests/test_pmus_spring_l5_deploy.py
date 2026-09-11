"""Structural deployment proof for the dedicated PMUS spring probe image."""
from pathlib import Path


DOCKERFILE = Path("deploy/pmus_spring_l5/Dockerfile")


def test_spring_probe_image_is_dedicated_and_fail_closed():
    text = DOCKERFILE.read_text()
    assert "src/pmus_spring_l5_session.py" in text
    assert "exec python3 -m src.pmus_spring_l5_session" in text
    assert "PMUS_SERVICE_ROLE=spring-l5-probe" in text
    assert "SERVICE_ROLE_MISMATCH" in text
    # The deployment artifact must never bake in a live arm or market.
    assert "PMUS_SPRING_LIVE=YES" not in text
    assert "PMUS_SPRING_ARM=" not in text
    assert "PMUS_SPRING_SESSION_ID=" not in text
    assert "PMUS_SPRING_MARKET_SLUG=" not in text
    assert "PMUS_SPRING_GATE_EVIDENCE=" not in text


def test_probe1_keeps_stricter_five_action_bound():
    text = DOCKERFILE.read_text()
    assert "PMUS_SPRING_MAX_ACTIONS=6" in text   # 5525966568: default 6


def test_image_does_not_start_other_workers():
    text = DOCKERFILE.read_text()
    cmd_lines = [line for line in text.splitlines() if line.startswith("CMD ")]
    assert len(cmd_lines) == 1
    cmd = cmd_lines[0]
    assert "pmus_ws_evidence_recorder" not in cmd
    assert "pmus_sports_collector" not in cmd
    assert "pmus_q1_executor" not in cmd
    assert "gemini" not in cmd.lower()


def test_image_ships_the_evidence_composer_it_now_imports():
    """The admission guard imports the composer when a status path is
    configured. An image without it would raise at admission rather than
    refuse -- a crash is not a fail-closed refusal."""
    docker = DOCKERFILE.read_text(encoding="utf-8")
    for module in ("src/pmus_spring_evidence_transfer.py",
                   "src/pmus_spring_shadow_replay.py",
                   "src/pmus_status_report.py",
                   "src/pmus_sports_l5_decision.py"):
        assert f"COPY {module} {module}" in docker, module


def test_a_missing_composer_refuses_rather_than_raising(monkeypatch):
    """Belt and braces: even if the image loses the module, admission
    must return a reason, not propagate ImportError."""
    import builtins
    from src import pmus_spring_l5_probe_entry as entry
    real_import = builtins.__import__

    def fake(name, globals=None, locals=None, fromlist=(), level=0):
        # `from src import pmus_spring_evidence_transfer` imports "src"
        # with the submodule in fromlist -- not the dotted name.
        if name == "src" and "pmus_spring_evidence_transfer" in (fromlist or ()):
            raise ImportError("no module",
                              name="src.pmus_spring_evidence_transfer")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake)
    ev, source = entry.evidence_from_status_path("mkt", "/tmp/whatever.jsonl")
    assert ev is None
    assert source.startswith("evidence_composer_unavailable")


# ---- the one-shot probe manifest ----

RAILWAY = Path("deploy/pmus_spring_l5/railway.json")


def _manifest():
    import json
    return json.loads(RAILWAY.read_text(encoding="utf-8"))


def test_probe_never_restarts_itself():
    """Probe #1 is ONE bounded session. An auto-restart would silently
    turn a one-shot campaign into a repeating one against the same caps."""
    assert _manifest()["deploy"]["restartPolicyType"] == "NEVER"


def test_probe_never_starts_the_bare_session():
    """Starting the session directly would bypass the strict-evidence,
    provenance and durable-state guards entirely. The continuous campaign
    (OWNER 5552036210) runs every candidate through that same guard at
    selection and inherits the dual key, credential and durable-state
    checks; it is a shadow selector when disarmed.

    WIDENED 2026-09-11 to allow a read-only settlement sweep, then NARROWED
    AGAIN the same day when the owner said "go 3" and the sweep's work was
    done. The list is back to the campaign alone: a sweep left as the start
    command after arming would mean a money deploy silently ran an audit and
    traded nothing, which is the mirror of the failure this latch exists for.

    THE MARKET ALLOWANCE RIDES IN THE COMMAND, and that is load-bearing
    rather than cosmetic. The durable state persists the R5F campaign, whose
    markets_per_campaign is CONTINUOUS -- so a campaign that boots without
    PMUS_SPRING_RUNG_MARKETS never reaches its advance gate, never consumes
    the R5FT approval, and instead RESUMES R5F trading at the 0.98 ceiling
    the owner is replacing. It is set as a Railway variable too, but that
    write cannot be read back on this service (every config read returns the
    same schema error), so the repo states it where a reviewer can see it and
    a test can hold it."""
    allowed = {"PMUS_SPRING_RUNG_MARKETS=1 python3 -m src.pmus_l5_continuous_session"}
    start = _manifest()["deploy"]["startCommand"]
    assert start in allowed, start
    assert "src.pmus_spring_l5_session" not in start
    assert "src.pmus_settlement_sweep" not in start
    assert start.startswith("PMUS_SPRING_RUNG_MARKETS=1 "), (
        "without the allowance the campaign resumes R5F instead of reaching "
        "the approval gate")
    # the IMAGE default stays the campaign: the start command overrides it,
    # and a sweep left as CMD would be the wrong thing to inherit later
    cmd = [l for l in DOCKERFILE.read_text().splitlines()
           if l.startswith("CMD ")][0]
    assert "src.pmus_l5_continuous_session" in cmd
    assert "src.pmus_spring_l5_session" not in cmd.split("exec")[-1]


def test_probe_manifest_builds_its_own_isolated_image():
    assert _manifest()["build"]["dockerfilePath"] == (
        "deploy/pmus_spring_l5/Dockerfile")


def test_watch_patterns_cover_every_file_the_image_copies():
    """A file in the image but not in the watch list means a change to it
    silently never rebuilds -- which is exactly how the probe image came
    to be missing the evidence composer."""
    import fnmatch
    patterns = _manifest()["build"]["watchPatterns"]
    copied = [line.split()[1] for line in
              DOCKERFILE.read_text(encoding="utf-8").splitlines()
              if line.startswith("COPY ")]
    for path in copied:
        assert any(fnmatch.fnmatch(path, pat) or
                   (pat.endswith("/**") and path.startswith(pat[:-2]))
                   for pat in patterns), f"{path} is built but never watched"


def test_the_probe_manifest_does_not_touch_the_collector():
    """Item 2 of the directive: do not disturb the working collector."""
    text = RAILWAY.read_text(encoding="utf-8")
    assert "sports_collector" not in text
    assert "pmus_sports_live_collector" not in text
