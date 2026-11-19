"""The one gate that refuses when a protection is merely absent.

An absent protection is the dangerous kind: a failing check is loud,
a skipped one is invisible. Every test here pins that absence refuses.
"""
from decimal import Decimal as D

import pytest

from src import l5_live_safety as S


# --------------------------------------------------------- can it fire

def test_the_shipped_timings_cannot_fire_at_all():
    #: THE MEASURED DEFECT. window 300, cancel lead 30 -> stepping
    #: stops before t=270; late_window 30 -> fires from t=270.
    #: Disjoint. Zero steps at t>=270 across 20 recorded windows.
    out = S.strategy_can_fire(late_window_s=30, window_s=300,
                              cancel_lead_s=30, step_s=5)
    assert out["ok"] is False
    assert out["reason"] == "STRATEGY_WINDOW_NEVER_STEPPED"
    assert out["stepping_stops_before_s"] == "270"
    assert out["strategy_fires_from_s"] == "270"
    assert out["rest_s"] == "0"


def test_exactly_one_step_of_overlap_is_reachable_but_thin():
    #: fires from t=265, stepping stops before 270. Any half-open 5s
    #: interval contains exactly one point of a 5s lattice, so this
    #: ALWAYS evaluates once -- refusing it would be false. What it
    #: does not give is time to rest: `rest_s` is the number an
    #: operator should judge, and 5s is one step of queue presence.
    out = S.strategy_can_fire(late_window_s=35, window_s=300,
                              cancel_lead_s=30, step_s=5)
    assert out["ok"] is True
    assert out["rest_s"] == "5"


def test_a_window_narrower_than_one_step_may_be_missed_and_is_refused():
    #: a 3s overlap on a 5s lattice can contain no step at all,
    #: depending only on where the loop happened to start
    out = S.strategy_can_fire(late_window_s=33, window_s=300,
                              cancel_lead_s=30, step_s=5)
    assert out["ok"] is False
    assert out["reason"] == "STRATEGY_WINDOW_NARROWER_THAN_ONE_STEP"


def test_a_wide_enough_window_can_fire_and_reports_resting_time():
    out = S.strategy_can_fire(late_window_s=45, window_s=300,
                              cancel_lead_s=30, step_s=5)
    assert out["ok"] is True
    assert out["strategy_fires_from_s"] == "255"
    #: 15s of resting before the cancel sweep -- the number that
    #: decides whether a maker order can realistically be hit
    assert out["rest_s"] == "15"


@pytest.mark.parametrize("kw", [
    {"late_window_s": None, "window_s": 300, "cancel_lead_s": 30, "step_s": 5},
    {"late_window_s": 30, "window_s": None, "cancel_lead_s": 30, "step_s": 5},
    {"late_window_s": 30, "window_s": 300, "cancel_lead_s": None, "step_s": 5},
    {"late_window_s": 30, "window_s": 300, "cancel_lead_s": 30, "step_s": None},
])
def test_unreadable_timings_refuse_rather_than_assume(kw):
    assert S.strategy_can_fire(**kw)["reason"] == "WINDOW_TIMINGS_UNREADABLE"


def test_nonsense_timings_refuse():
    out = S.strategy_can_fire(late_window_s=30, window_s=0,
                              cancel_lead_s=30, step_s=5)
    assert out["reason"] == "WINDOW_TIMINGS_NONSENSE"


# ------------------------------------------------- does it actually bind

def test_an_envelope_missing_a_limit_does_not_bind():
    out = S.envelope_binds({"max_aggregate_usd": D("10"),
                            "max_per_market_usd": D("5"),
                            "maker_only": True})
    assert out["ok"] is False
    assert "max_daily_loss_usd" in out["missing"]


def test_a_missing_limit_is_never_read_as_the_ceiling():
    #: defaulting an absent figure to its compiled ceiling is the
    #: permissive answer, which is the whole thing this refuses
    out = S.envelope_binds({})
    assert out["ok"] is False
    assert set(out["missing"]) >= {"max_aggregate_usd", "max_per_market_usd",
                                   "max_daily_loss_usd", "maker_only"}


def test_maker_only_must_be_exactly_true():
    for v in (None, 0, 1, "YES", "true", False):
        out = S.envelope_binds({"max_aggregate_usd": D("10"),
                                "max_per_market_usd": D("5"),
                                "max_daily_loss_usd": D("5"),
                                "maker_only": v})
        assert "maker_only" in out["missing"], v


def test_a_limit_over_its_compiled_ceiling_is_refused():
    out = S.envelope_binds({"max_aggregate_usd": D("20"),
                            "max_per_market_usd": D("5"),
                            "max_daily_loss_usd": D("5"),
                            "maker_only": True})
    assert out["ok"] is False
    assert out["exceeds"] == ["max_aggregate_usd=20>10.00"]


def test_clamping_down_is_allowed():
    out = S.envelope_binds({"max_aggregate_usd": D("4"),
                            "max_per_market_usd": D("2"),
                            "max_daily_loss_usd": D("1"),
                            "maker_only": True})
    assert out["ok"] is True


def test_a_zero_limit_is_missing_not_binding():
    out = S.envelope_binds({"max_aggregate_usd": D("0"),
                            "max_per_market_usd": D("5"),
                            "max_daily_loss_usd": D("5"),
                            "maker_only": True})
    assert "max_aggregate_usd" in out["missing"]


# ------------------------------------------------------------ the gate

def _good(**over):
    kw = dict(
        env={"L5_PERSISTENT_ENVELOPE": "{...}",
             "L5_SHARED_RESERVATION": "/state/resv.json",
             "L5_ACCOUNT_LANES": "1",
             "L5_BTC5M_FOREIGN_EXPOSURE": "CHARGED"},
        state="ARMED",
        envelope_scope={"max_aggregate_usd": D("10"),
                        "max_per_market_usd": D("5"),
                        "max_daily_loss_usd": D("5"),
                        "maker_only": True},
        reservation_readable=True,
        halt={"ok": True},
        account_risk={"reachable": True, "age_s": D("4"),
                      "account_exposure_usd": D("1.50")},
        watchdog_managed_urls=[
            "http://pmus-spring-l5-probe.railway.internal:8090"],
        this_lane_url="http://l5-btc5m-live.railway.internal:8082",
        oracle_sees_lane=True,
        strategy_inputs={"min_edge_per_share": D("0.004"),
                         "clip_usd": D("2.00")},
        strategy_timing={"late_window_s": 45, "window_s": 300,
                         "cancel_lead_s": 30, "step_s": 5},
    )
    kw.update(over)
    return S.check(**kw)


def test_every_protection_present_passes():
    out = _good()
    assert out["ok"] is True, out["failures"]
    assert out["failures"] == []


def test_an_absent_envelope_refuses_and_says_what_stops_binding():
    out = _good(env={"L5_SHARED_RESERVATION": "/state/resv.json",
                     "L5_ACCOUNT_LANES": "1",
                     "L5_BTC5M_FOREIGN_EXPOSURE": "CHARGED"})
    assert out["ok"] is False
    assert "ENVELOPE_ABSENT" in out["failures"]
    why = out["detail"]["ENVELOPE_ABSENT"]["why"]
    assert "per-market" in why and "maker-only" in why


def test_an_empty_envelope_string_counts_as_absent():
    #: `_submit` reads `is not None and str(env_raw).strip()`, so a
    #: whitespace value skips the gate exactly as unset does
    out = _good(env={"L5_PERSISTENT_ENVELOPE": "   ",
                     "L5_SHARED_RESERVATION": "/state/resv.json",
                     "L5_ACCOUNT_LANES": "1",
                     "L5_BTC5M_FOREIGN_EXPOSURE": "CHARGED"})
    assert "ENVELOPE_ABSENT" in out["failures"]


def test_reported_foreign_exposure_refuses_an_account_wide_claim():
    out = _good(env={"L5_PERSISTENT_ENVELOPE": "{...}",
                     "L5_SHARED_RESERVATION": "/state/resv.json",
                     "L5_ACCOUNT_LANES": "1",
                     "L5_BTC5M_FOREIGN_EXPOSURE": "REPORTED"})
    assert out["ok"] is False
    assert "FOREIGN_EXPOSURE_NOT_CHARGED" in out["failures"]


def test_being_on_the_managed_list_is_the_failure_not_being_off_it():
    #: THE READING THAT LOOKS RIGHT AND IS BACKWARDS. The watchdog's own
    #: docstring: registering a lane "EXEMPTS from the overdue/age latch
    #: only; it never makes an order cancellable". A managed lane is one
    #: the watchdog is told to leave alone. During normal operation an
    #: order rests ~15s against a 300s threshold, so that latch fires
    #: exactly in the case worth covering -- a crashed lane leaving an
    #: order resting -- and an exemption removes it.
    out = _good(watchdog_managed_urls=[
        "http://l5-btc5m-live.railway.internal:8082"])
    assert out["ok"] is False
    assert "LANE_EXEMPT_FROM_WATCHDOG_AGE_LATCH" in out["failures"]


def test_an_unclaimable_order_is_not_covered_however_healthy_the_watchdog():
    #: the watchdog cancels nothing the oracle cannot claim: it reports
    #: not_ours NOT_IN_OWNERSHIP_REGISTRY, asserts the halt, and leaves
    #: the order resting
    out = _good(oracle_sees_lane=False)
    assert out["ok"] is False
    assert "LANE_NOT_IN_OWNERSHIP_REGISTRY" in out["failures"]


def test_a_stale_account_risk_document_refuses():
    out = _good(account_risk={"reachable": True, "age_s": D("31"),
                              "account_exposure_usd": D("1.50")})
    assert "ACCOUNT_RISK_STALE" in out["failures"]


def test_a_fresh_but_figureless_risk_document_refuses():
    out = _good(account_risk={"reachable": True, "age_s": D("2")})
    assert "ACCOUNT_RISK_HAS_NO_EXPOSURE_FIGURE" in out["failures"]


def test_an_unreachable_risk_endpoint_refuses():
    out = _good(account_risk={"reachable": False, "age_s": D("2"),
                              "account_exposure_usd": D("0")})
    assert "ACCOUNT_RISK_UNREACHABLE" in out["failures"]


@pytest.mark.parametrize("state", ["MONITOR", "HALTED", "STOPPED", "", None])
def test_only_armed_proceeds(state):
    out = _good(state=state)
    assert "STATE_NOT_ARMED" in out["failures"]


def test_an_asserted_halt_refuses():
    out = _good(halt={"ok": False, "reason": "HALT_ASSERTED"})
    assert "HALT_UNREADABLE_OR_ASSERTED" in out["failures"]


def test_an_unset_strategy_input_refuses_by_name():
    out = _good(strategy_inputs={"min_edge_per_share": None,
                                 "clip_usd": D("2.00")})
    assert "STRATEGY_INPUT_UNSET:min_edge_per_share" in out["failures"]


def test_a_zero_edge_margin_refuses():
    #: there is no safe default margin for an unmeasured edge
    out = _good(strategy_inputs={"min_edge_per_share": D("0"),
                                 "clip_usd": D("2.00")})
    assert "STRATEGY_INPUT_UNSET:min_edge_per_share" in out["failures"]


def test_the_shipped_timing_defect_is_caught_by_the_gate():
    out = _good(strategy_timing={"late_window_s": 30, "window_s": 300,
                                 "cancel_lead_s": 30, "step_s": 5})
    assert out["ok"] is False
    assert "STRATEGY_CANNOT_FIRE:STRATEGY_WINDOW_NEVER_STEPPED" in \
        out["failures"]


def test_every_failure_is_reported_not_just_the_first():
    #: an operator fixing these one boot at a time is arming late and
    #: guessing; one run must give the whole list
    out = S.check(
        env={}, state="MONITOR", envelope_scope=None,
        reservation_readable=False, halt=None, account_risk=None,
        watchdog_managed_urls=[], this_lane_url="",
        oracle_sees_lane=False,
        strategy_inputs={"min_edge_per_share": None},
        strategy_timing={"late_window_s": 30, "window_s": 300,
                         "cancel_lead_s": 30, "step_s": 5})
    assert out["ok"] is False
    assert len(out["failures"]) >= 9
    for name in ("ENVELOPE_ABSENT", "STATE_NOT_ARMED",
                 "SHARED_RESERVATION_PATH_UNSET",
                 "SHARED_RESERVATION_UNREADABLE",
                 "ACCOUNT_LANES_UNREADABLE",
                 "HALT_UNREADABLE_OR_ASSERTED", "ACCOUNT_RISK_UNREADABLE",
                 "THIS_LANE_URL_UNKNOWN", "LANE_NOT_IN_OWNERSHIP_REGISTRY"):
        assert name in out["failures"], name


def test_unreadable_inputs_refuse_rather_than_raise():
    for over in ({"envelope_scope": "nonsense"},
                 {"halt": "nonsense"},
                 {"account_risk": "nonsense"},
                 {"watchdog_managed_urls": 7},
                 {"strategy_inputs": "nonsense"},
                 {"strategy_timing": "nonsense"}):
        out = _good(**over)
        assert out["ok"] is False, over


def test_the_note_states_the_posture():
    assert "fail-closed" in _good()["note"]


# ---------------- authenticated depth must be the SAME BOOK as the quote

def _pub(bid="0.9600", ask="0.9850", age_s="0.2"):
    return {"bid": D(bid), "ask": D(ask), "age_s": D(age_s)}


def _auth(bid="0.9600", ask="0.9850", bid_shares="40", ask_shares="55",
          stream_age_s="0.3", market_state="OPEN", read=True, **kw):
    d = {"read": read, "bid": bid, "ask": ask, "bid_shares": bid_shares,
         "ask_shares": ask_shares, "stream_age_s": stream_age_s,
         "market_state": market_state}
    d.update(kw)
    return d


def test_agreeing_sources_yield_the_size():
    out = S.depth_for_pricing(public=_pub(), auth=_auth())
    assert out["ok"] is True, out
    assert out["bid_shares"] == "40"
    assert out["ask_shares"] == "55"


def test_the_complementary_token_is_named_not_reported_as_latency():
    #: THE WIRING ERROR THAT MATTERS MOST. A binary market's two tokens
    #: have complementary books: if the socket is serving the other one,
    #: `ask_shares` is the size resting on the OPPOSITE side and a 0.97
    #: bid would be sized against depth that is not there. Reporting
    #: this as a generic divergence sends someone hunting a latency bug
    #: for a wiring one.
    out = S.depth_for_pricing(
        public=_pub(bid="0.9600", ask="0.9850"),
        auth=_auth(bid="0.0150", ask="0.0400"))      # 1 - ask, 1 - bid
    assert out["ok"] is False
    assert out["reason"] == "TOKEN_COMPLEMENT_MISMATCH"
    assert out["bid_shares"] is None and out["ask_shares"] is None
    assert "opposite side" in out["why"]


def test_transposed_sides_are_named_too():
    out = S.depth_for_pricing(public=_pub(bid="0.9600", ask="0.9850"),
                              auth=_auth(bid="0.9850", ask="0.9600"))
    assert out["reason"] == "SIDES_TRANSPOSED"


def test_a_different_market_shows_up_as_divergence_and_sizes_nothing():
    #: the snapshot is keyed by slug so this should not happen, but a
    #: reader that trusts that silently inherits any change to it
    out = S.depth_for_pricing(public=_pub(bid="0.9600", ask="0.9850"),
                              auth=_auth(bid="0.4200", ask="0.4400"))
    assert out["reason"] == "QUOTE_DIVERGENCE"
    assert out["bid_shares"] is None


def test_one_tick_of_divergence_is_tolerated():
    #: the two are read microseconds apart over different transports
    out = S.depth_for_pricing(public=_pub(bid="0.9600", ask="0.9850"),
                              auth=_auth(bid="0.9700", ask="0.9750"))
    assert out["ok"] is True


def test_two_ticks_is_not():
    out = S.depth_for_pricing(public=_pub(bid="0.9600", ask="0.9850"),
                              auth=_auth(bid="0.9800", ask="0.9650"))
    assert out["ok"] is False


def test_a_stale_socket_sizes_nothing_however_well_it_agrees():
    #: a push socket sends nothing when nothing changes, so the age of
    #: the last CHANGE proves nothing; the age of the last FRAME does
    out = S.depth_for_pricing(public=_pub(), auth=_auth(stream_age_s="9.0"))
    assert out["reason"] == "AUTH_BOOK_STALE"
    assert out["bid_shares"] is None


def test_an_unmeasurable_stream_age_refuses():
    out = S.depth_for_pricing(public=_pub(), auth=_auth(stream_age_s=None))
    assert out["reason"] == "AUTH_BOOK_STALE"


def test_a_stale_public_quote_refuses():
    out = S.depth_for_pricing(public=_pub(age_s="9.0"), auth=_auth())
    assert out["reason"] == "PUBLIC_QUOTE_STALE"


def test_freshness_is_checked_before_agreement():
    #: two stale readings that happen to agree must not pass: the
    #: refusal has to name staleness, not silently accept it
    out = S.depth_for_pricing(public=_pub(age_s="30"),
                              auth=_auth(stream_age_s="30"))
    assert out["reason"] in ("AUTH_BOOK_STALE", "PUBLIC_QUOTE_STALE")


def test_an_unread_socket_sizes_nothing():
    out = S.depth_for_pricing(public=_pub(),
                              auth={"read": False, "reason": "NO_SNAPSHOT"})
    assert out["reason"] == "AUTH_BOOK_NOT_READ"
    assert out["detail"] == "NO_SNAPSHOT"


def test_a_one_sided_AUTH_book_still_sizes_nothing():
    assert S.depth_for_pricing(public=_pub(), auth=_auth(bid=None)
                               )["reason"] == "AUTH_BOOK_ONE_SIDED"


def test_a_one_sided_PUBLIC_quote_falls_back_to_the_socket():
    #: MEASURED 2026-11-16: all 19 qualifying signals were refused
    #: PUBLIC_QUOTE_ONE_SIDED while the authenticated book carried a
    #: real, uncrossed ask with 814-38,728 shares behind it. Refusing
    #: there was a decision about OUR FEED, not about the market.
    out = S.depth_for_pricing(
        public={"bid": D("0.99"), "ask": None, "age_s": D("0.2")},
        auth=_auth(bid="0.9900", ask="0.9900"))
    assert out["ok"] is True
    assert out["source"] == "AUTHENTICATED_ONLY"
    assert out["public_missing_side"] == "ask"
    #: the weaker claim is REPORTED, never silent
    assert out["checked"]["agreement"] == "NOT_CHECKED_PUBLIC_ONE_SIDED"


def test_the_fallback_still_refuses_a_crossed_socket():
    #: the 1915z phantom: ask 0.98 under bid 0.99, size frozen at 16.17
    out = S.depth_for_pricing(
        public={"bid": D("0.99"), "ask": None, "age_s": D("0.2")},
        auth=_auth(bid="0.9900", ask="0.9800", ask_shares="16.17"))
    assert out["ok"] is False
    assert out["reason"] == "AUTH_BOOK_CROSSED"
    assert out["bid_shares"] is None


def test_the_fallback_still_refuses_a_stale_socket():
    out = S.depth_for_pricing(
        public={"bid": D("0.99"), "ask": None, "age_s": D("0.2")},
        auth=_auth(stream_age_s="9.0"))
    assert out["reason"] == "AUTH_BOOK_STALE"


def test_the_fallback_still_refuses_an_untradable_market():
    out = S.depth_for_pricing(
        public={"bid": D("0.99"), "ask": None, "age_s": D("0.2")},
        auth=_auth(market_state="CLOSED"))
    assert out["reason"] == "MARKET_NOT_TRADABLE"


def test_a_market_that_is_not_tradable_sizes_nothing():
    out = S.depth_for_pricing(public=_pub(), auth=_auth(market_state="CLOSED"))
    assert out["reason"] == "MARKET_NOT_TRADABLE"


def test_absent_size_is_never_read_as_unlimited_size():
    out = S.depth_for_pricing(public=_pub(), auth=_auth(ask_shares=None))
    assert out["reason"] == "DEPTH_UNREADABLE"
    assert out["ask_shares"] is None


def test_garbage_refuses_rather_than_raising():
    for pub, auth in (("nonsense", _auth()), (_pub(), "nonsense"),
                      (None, None), (_pub(), {"read": True})):
        out = S.depth_for_pricing(public=pub, auth=auth)
        assert out["ok"] is False
        assert out["bid_shares"] is None


def test_strings_from_the_socket_are_handled():
    #: `book_depth()` stringifies every field it returns
    out = S.depth_for_pricing(
        public={"bid": "0.9600", "ask": "0.9850", "age_s": "0.2"},
        auth=_auth())
    assert out["ok"] is True
    assert out["ask_shares"] == "55"
