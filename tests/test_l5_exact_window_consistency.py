"""Exact-window gate regression. This pure helper must be wired before arming."""
import pytest
from src.l5_probe_preflight import exact_window_consistency

TARGET = "cpc-btc-updown-15m-2026-11-09-1200z"


def hb():
    return {"market": TARGET,
            "campaign": {"permits_this_window": True},
            "authorization_preflight": {"ok": True, "evaluated_market": TARGET}}


def check(heartbeat=None, plan=None, market=TARGET):
    return exact_window_consistency(heartbeat=hb() if heartbeat is None else heartbeat,
                                    plan={"market": TARGET} if plan is None else plan,
                                    market=market)


def test_matching_window_passes():
    assert check()["ok"] is True


@pytest.mark.parametrize("field,value,expected", [
    ("market", "cpc-btc-updown-15m-2026-11-09-1145z", "executor_active_market_matches"),
    ("market", None, "executor_active_market_matches"),
])
def test_stale_or_missing_active_market_fails(field, value, expected):
    h = hb()
    h[field] = value
    assert expected in check(heartbeat=h)["failed"]


def test_plan_for_different_window_fails():
    assert "plan_market_matches_executor" in check(plan={"market": "other"})["failed"]


def test_document_only_authorization_is_not_coverage():
    h = hb()
    h["authorization_preflight"]["evaluated_market"] = None
    assert "authorization_evaluated_same_market" in check(heartbeat=h)["failed"]


def test_other_campaign_window_fails():
    h = hb()
    h["campaign"]["permits_this_window"] = False
    assert "campaign_permits_exact_window" in check(heartbeat=h)["failed"]


def test_authorization_failure_fails():
    h = hb()
    h["authorization_preflight"]["ok"] = False
    assert "authorization_verdict_passes" in check(heartbeat=h)["failed"]


def test_missing_target_fails():
    assert check(market="")["ok"] is False


def test_book_age_policy_rejects_over_two_seconds():
    from src.l5_probe_preflight import preflight
    base = {"auth_book": {"connected": True, "stream_age_s": 2.01,
                          "gap_suspected": False}}
    report = preflight(heartbeat=base, plan=None, markets=[TARGET])
    row = next(x for x in report["checks"]
               if x["check"] == "authenticated_book_connected_and_fresh")
    assert row["ok"] is False


def test_book_age_policy_allows_two_seconds():
    from src.l5_probe_preflight import preflight
    base = {"auth_book": {"connected": True, "stream_age_s": 2.0,
                          "gap_suspected": False}}
    report = preflight(heartbeat=base, plan=None, markets=[TARGET])
    row = next(x for x in report["checks"]
               if x["check"] == "authenticated_book_connected_and_fresh")
    assert row["ok"] is True
