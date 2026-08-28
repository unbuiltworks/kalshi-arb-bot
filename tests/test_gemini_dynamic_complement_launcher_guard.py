from pathlib import Path


LAUNCHER = Path("deploy/aws/run_gemini_dynamic_complement_q1.sh")


def _text() -> str:
    return LAUNCHER.read_text(encoding="utf-8")


def test_launcher_requires_fresh_approval_before_secure_env_discovery():
    text = _text()
    approval = text.index("ARB_GEMINI_DYNAMIC_COMPLEMENT_Q1_APPROVED")
    approval_id = text.index("ARB_GEMINI_DYNAMIC_COMPLEMENT_Q1_APPROVAL_ID")
    env_discovery = text.index('if [ -n "${ENV_FILE:-}" ]')
    assert approval < approval_id < env_discovery
    assert "fresh dynamic-complement q1 approval flag missing" in text


def test_launcher_is_exact_q1_eth5_dynamic_wrapper_no_restart():
    text = _text()
    assert "src.gemini_incentive_mm_dynamic_live_probe_q1" in text
    assert "q=1_per_outcome asset=ETH horizon=5m" in text
    assert "entry_edge_floor=0.05" in text
    assert "final_edge_floor=0.02" in text
    assert "initial_wait_s=60" in text
    assert "rescue_s=30" in text
    assert "max_reprices=6" in text
    assert "taker_rescue=false" in text
    assert "--restart no" in text


def test_launcher_uses_one_use_approval_marker_and_blocks_overlap():
    text = _text()
    assert "dynamic_complement_q1_approval_${SAFE_APPROVAL_ID}.used" in text
    assert "approval ID already consumed" in text
    assert "another Gemini short-crypto live container is running" in text


def test_launcher_runs_focused_safety_suite_before_live_start():
    text = _text()
    test_pos = text.index("DYNAMIC_COMPLEMENT_Q1_SAFETY_TESTS_OK")
    clean_pos = text.index("DYNAMIC_COMPLEMENT_Q1_CLEAN_START_OK")
    marker_pos = text.index('> "$USED_MARKER.tmp"')
    live_pos = text.index("DYNAMIC_COMPLEMENT_Q1_LIVE_STARTING")
    assert test_pos < clean_pos < marker_pos < live_pos


def test_launcher_qualifier_is_read_only_and_before_approval_consumption():
    text = _text()
    qualifier = text.index("DYNAMIC_COMPLEMENT_Q1_QUALIFIER_WAIT_START")
    marker = text.index('> "$USED_MARKER.tmp"')
    live = text.index("DYNAMIC_COMPLEMENT_Q1_LIVE_STARTING")
    assert qualifier < marker < live
    assert 'QUALIFY_WAIT_SECONDS="${QUALIFY_WAIT_SECONDS:-0}"' in text
    assert 'QUALIFY_POLL_SECONDS="${QUALIFY_POLL_SECONDS:-10}"' in text
    assert '"order_entry": False' in text
    assert '"mutation": False' in text
    assert "DYNAMIC_COMPLEMENT_Q1_NO_QUALIFYING_WINDOW order_entry=false approval_consumed=false" in text
    assert "DYNAMIC_COMPLEMENT_Q1_QUALIFYING_WINDOW_FOUND order_entry=false approval_consumed=false" in text


def test_launcher_retries_only_transient_qualifier_transport_failures():
    text = _text()
    assert "httpx.TransportError" in text
    assert "asyncio.TimeoutError" in text
    assert '"retryable_transport_error": type(exc).__name__' in text
    assert "raise SystemExit(4)" in text
    assert '[ "$QUALIFY_RC" -eq 4 ]' in text
    assert "DYNAMIC_COMPLEMENT_Q1_QUALIFIER_TRANSIENT_RETRY approval_consumed=false" in text
    assert '[ "$QUALIFY_RC" -eq 3 ] || { echo "qualifier failed unexpectedly rc=$QUALIFY_RC"' in text
