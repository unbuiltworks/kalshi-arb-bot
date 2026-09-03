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
    assert "PMUS_SPRING_MAX_ACTIONS=5" in text


def test_image_does_not_start_other_workers():
    text = DOCKERFILE.read_text()
    cmd_lines = [line for line in text.splitlines() if line.startswith("CMD ")]
    assert len(cmd_lines) == 1
    cmd = cmd_lines[0]
    assert "pmus_ws_evidence_recorder" not in cmd
    assert "pmus_sports_collector" not in cmd
    assert "pmus_q1_executor" not in cmd
    assert "gemini" not in cmd.lower()
