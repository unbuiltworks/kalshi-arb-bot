from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from src.kalshi_nested_live_probe_guard import (
    LIVE_ENV,
    NestedProbePlan,
    ProbeLeg,
    build_v2_buy_payload,
    live_probe_authorized,
    outcome_cost_from_v2_payload,
    recompute_exact_locked_profit,
    validate_and_build_payloads,
)

D = Decimal
NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 8, 16, 19, 10, tzinfo=NY)
CLOSE = datetime(2026, 8, 16, 20, 0, tzinfo=NY)
EVENT = "KXSOLD-26AUG1620"


def plan(*, maker=None, hedge=None, close=CLOSE, target="0.03", fee_type="quadratic"):
    return NestedProbePlan(
        series="KXSOLD",
        event_ticker=EVENT,
        event_close=close,
        maker=maker or ProbeLeg(EVENT + "-T75.7499", "no", D("0.95")),
        hedge=hedge or ProbeLeg(EVENT + "-T75.2499", "yes", D("0.01")),
        fee_type=fee_type,
        fee_multiplier=D("1"),
        target_profit=D(target),
    )


def test_live_probe_requires_cli_and_environment(monkeypatch):
    monkeypatch.delenv(LIVE_ENV, raising=False)
    assert not live_probe_authorized(False)
    assert not live_probe_authorized(True)
    monkeypatch.setenv(LIVE_ENV, "YES")
    assert not live_probe_authorized(False)
    assert live_probe_authorized(True)


def test_buy_yes_maps_to_v2_bid_and_buy_no_maps_to_yes_ask():
    yes = build_v2_buy_payload(
        leg=ProbeLeg(EVENT + "-T75.2499", "yes", D("0.02")), maker=False
    )
    no = build_v2_buy_payload(
        leg=ProbeLeg(EVENT + "-T75.7499", "no", D("0.96")), maker=True
    )
    assert yes["side"] == "bid"
    assert yes["price"] == "0.0200"
    assert no["side"] == "ask"
    assert no["price"] == "0.0400"
    assert outcome_cost_from_v2_payload(yes) == ("yes", D("0.0200"))
    assert outcome_cost_from_v2_payload(no) == ("no", D("0.9600"))


def test_maker_is_post_only_gtc_and_hedge_is_fok():
    maker, hedge, locked = validate_and_build_payloads(plan(), now=NOW)
    assert maker["post_only"] is True
    assert maker["time_in_force"] == "good_till_canceled"
    assert hedge["post_only"] is False
    assert hedge["time_in_force"] == "fill_or_kill"
    assert maker["count"] == hedge["count"] == "1.00"
    assert locked == D("0.03")


def test_exact_payload_profit_recomputes_taker_fee_not_cached_claim():
    maker = build_v2_buy_payload(
        leg=ProbeLeg(EVENT + "-T75.7499", "no", D("0.95")), maker=True
    )
    hedge = build_v2_buy_payload(
        leg=ProbeLeg(EVENT + "-T75.2499", "yes", D("0.01")), maker=False
    )
    # 95c + 1c + 1c rounded taker fee = 97c, min payout $1 => 3c.
    assert recompute_exact_locked_profit(
        maker_payload=maker,
        hedge_payload=hedge,
        fee_multiplier=D("1"),
    ) == D("0.03")


def test_one_cent_shadow_math_is_not_silently_promoted_to_first_live_probe():
    one_cent = plan(
        maker=ProbeLeg(EVENT + "-T75.7499", "no", D("0.96")),
        hedge=ProbeLeg(EVENT + "-T75.2499", "yes", D("0.02")),
        target="0.01",
    )
    with pytest.raises(ValueError, match="at least 3 cents"):
        validate_and_build_payloads(one_cent, now=NOW)


def test_cross_event_pair_fails_closed():
    bad = plan(
        hedge=ProbeLeg("KXSOLD-26AUG1717-T75.2499", "yes", D("0.01")),
    )
    with pytest.raises(ValueError, match="same event"):
        validate_and_build_payloads(bad, now=NOW)


def test_greater_ladder_orientation_is_enforced():
    bad = plan(
        maker=ProbeLeg(EVENT + "-T75.7499", "yes", D("0.95")),
        hedge=ProbeLeg(EVENT + "-T75.2499", "no", D("0.01")),
    )
    with pytest.raises(ValueError, match="YES\(lower\) \+ NO\(higher\)"):
        validate_and_build_payloads(bad, now=NOW)


def test_event_more_than_six_hours_away_is_rejected():
    bad = plan(close=NOW + timedelta(hours=6, seconds=1))
    with pytest.raises(ValueError, match="6-hour"):
        validate_and_build_payloads(bad, now=NOW)


def test_unknown_or_maker_fee_bearing_fee_type_is_rejected():
    bad = plan(fee_type="quadratic_with_maker_fees")
    with pytest.raises(ValueError, match="fee type"):
        validate_and_build_payloads(bad, now=NOW)


def test_exact_payload_mutations_fail_closed():
    maker, hedge, _ = validate_and_build_payloads(plan(), now=NOW)
    bad_hedge = dict(hedge)
    bad_hedge["time_in_force"] = "good_till_canceled"
    with pytest.raises(ValueError, match="fill_or_kill"):
        recompute_exact_locked_profit(
            maker_payload=maker,
            hedge_payload=bad_hedge,
            fee_multiplier=D("1"),
        )


def test_series_is_allowlisted():
    bad = NestedProbePlan(
        series="KXBTCD",
        event_ticker="KXBTCD-26AUG1620",
        event_close=CLOSE,
        maker=ProbeLeg("KXBTCD-26AUG1620-T120000", "no", D("0.95")),
        hedge=ProbeLeg("KXBTCD-26AUG1620-T119000", "yes", D("0.01")),
        fee_type="quadratic",
        fee_multiplier=D("1"),
    )
    with pytest.raises(ValueError, match="restricted"):
        validate_and_build_payloads(bad, now=NOW)
