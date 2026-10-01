"""The ONE authorized Case-1 live calibration: its envelope, in tests.

Every number the owner authorized is asserted here, so a later edit that widens
risk fails the suite rather than reaching the venue.
"""
import json
import pathlib
from decimal import Decimal as D

import pytest

from src import pmus_case1_probe as K


# ------------------------------------------------------------ the envelope

def test_the_authorized_numbers_are_exactly_these():
    assert K.QTY == 1
    assert K.MAX_ENTRY_PX == D("0.25")
    assert K.MAX_LOSS == D("0.25")
    assert K.PRICE_TICK == D("0.01")
    assert K.ENTRY_OFFSET_TICKS == 1
    assert K.worst_case_loss(K.MAX_ENTRY_PX) == K.MAX_LOSS


def test_worst_case_loss_at_the_cap_never_exceeds_the_authorized_loss():
    for cents in range(1, 26):
        px = D(cents) / D(100)
        assert K.worst_case_loss(px) <= K.MAX_LOSS


# ------------------------------------------------------------------ arming

def test_arming_needs_all_four_and_live_is_the_literal_yes(monkeypatch):
    sid = "sess-case1-abc"
    for env in (K.LIVE_ENV, K.SESSION_ENV, K.ARM_ENV, K.CASE1_ARM_ENV):
        monkeypatch.delenv(env, raising=False)
    assert not K.armed(sid)
    monkeypatch.setenv(K.LIVE_ENV, "YES")
    monkeypatch.setenv(K.SESSION_ENV, sid)
    monkeypatch.setenv(K.ARM_ENV, sid)
    assert not K.armed(sid)                      # the fourth is still missing
    monkeypatch.setenv(K.CASE1_ARM_ENV, sid)
    assert K.armed(sid)
    #: LIVE is never the session id
    monkeypatch.setenv(K.LIVE_ENV, sid)
    assert not K.armed(sid)
    monkeypatch.setenv(K.LIVE_ENV, "YES")
    assert K.armed(sid)
    #: and a mismatched session id on any one of them refuses
    for env in (K.SESSION_ENV, K.ARM_ENV, K.CASE1_ARM_ENV):
        monkeypatch.setenv(env, "other")
        assert not K.armed(sid)
        monkeypatch.setenv(env, sid)
    assert not K.armed("")                       # an empty session never arms


def test_the_arming_message_states_the_real_contract():
    msg = K.arming_contract()
    for part in (K.LIVE_ENV, K.SESSION_ENV, K.ARM_ENV, K.CASE1_ARM_ENV):
        assert part in msg
    assert "'YES'" in msg
    #: it must not claim all four equal the session id -- the earlier probe's
    #: message did, and would have had an operator refuse forever.
    assert "all four" not in msg


# ------------------------------------------------------- own-terms geometry

def test_own_terms_invert_exactly_once():
    bid, ask = "0.60", "0.62"
    assert K.own_bid("up", bid, ask) == D("0.60")
    assert K.own_ask("up", bid, ask) == D("0.62")
    #: the DOWN leg's own bid is 1 - the YES ask, and its own ask 1 - YES bid
    assert K.own_bid("down", bid, ask) == D("0.38")
    assert K.own_ask("down", bid, ask) == D("0.40")
    #: a missing side is None, never a guessed number
    assert K.own_bid("up", None, ask) is None
    assert K.own_ask("down", None, ask) is None


# -------------------------------------------------------------- price rule

def test_it_picks_the_cheaper_side_one_tick_below_its_own_touch():
    #: YES 0.78/0.80 -> DOWN own bid = 1-0.80 = 0.20, one tick below = 0.19
    side, px = K.choose_entry({"bid": "0.78", "ask": "0.80"})
    assert side == "down"
    assert px == D("0.19")
    assert K.worst_case_loss(px) <= K.MAX_LOSS


def test_it_refuses_above_the_cap_rather_than_widening_or_drifting_out():
    #: a balanced book: the cheap side's touch is 0.49, one tick below 0.48,
    #: well over the 0.25 cap. It must REFUSE, not price at 0.25.
    with pytest.raises(K.Case1Refuse) as e:
        K.choose_entry({"bid": "0.50", "ask": "0.51"})
    assert "ABOVE_CAP" in str(e.value)


def test_the_cap_boundary_is_inclusive_and_one_tick_over_refuses():
    #: DOWN own bid 0.26 -> entry 0.25, exactly the cap: allowed
    side, px = K.choose_entry({"bid": "0.73", "ask": "0.74"})
    assert (side, px) == ("down", D("0.25"))
    #: DOWN own bid 0.27 -> entry 0.26, one tick over: refused
    with pytest.raises(K.Case1Refuse) as e:
        K.choose_entry({"bid": "0.72", "ask": "0.73"})
    assert "ABOVE_CAP" in str(e.value)


def test_it_refuses_an_off_grid_touch_instead_of_rounding():
    #: the DOWN leg is the cheap one here and its own touch is 1 - 0.805 =
    #: 0.195, which is off the 0.01 grid. Refuse rather than round to 0.19/0.20.
    with pytest.raises(K.Case1Refuse) as e:
        K.choose_entry({"bid": "0.78", "ask": "0.805"})
    assert "OFF_GRID" in str(e.value)


def test_it_refuses_a_one_sided_book():
    for bbo in ({"bid": None, "ask": "0.80"}, {"bid": "0.78", "ask": None},
                {}):
        with pytest.raises(K.Case1Refuse):
            K.choose_entry(bbo)


def test_it_refuses_a_non_positive_price():
    #: DOWN own bid = 1 - 0.99 = 0.01, one tick below = 0.00
    with pytest.raises(K.Case1Refuse) as e:
        K.choose_entry({"bid": "0.10", "ask": "0.99"})
    assert "NON_POSITIVE_PRICE" in str(e.value)


def test_the_chosen_price_is_always_strictly_non_marketable():
    """The whole design depends on resting, so this is checked exhaustively."""
    seen = 0
    for b in range(1, 99):
        for a in range(b + 1, 100):
            bbo = {"bid": D(b) / D(100), "ask": D(a) / D(100)}
            try:
                side, px = K.choose_entry(bbo)
            except K.Case1Refuse:
                continue
            seen += 1
            oa = K.own_ask(side, bbo["bid"], bbo["ask"])
            assert px < oa, (bbo, side, px, oa)
            assert px <= K.MAX_ENTRY_PX
            assert K.on_grid(px)
    assert seen > 50, f"only {seen} admissible books; the sweep proved little"


# ------------------------------------------------------ reach and crossing

def test_reach_state_distinguishes_touched_from_reached():
    #: our resting UP buy at 0.20, book 0.22/0.24: neither
    st = K.reach_state("up", D("0.20"), "0.22", "0.24")
    assert st == {"touched": False, "reached": False}
    #: the bid falls to our price: TOUCHED but nobody is offering to us
    st = K.reach_state("up", D("0.20"), "0.20", "0.24")
    assert st["touched"] and not st["reached"]
    #: the ask falls to our price: the market crossed us. THE Case-1 condition.
    st = K.reach_state("up", D("0.20"), "0.18", "0.20")
    assert st["touched"] and st["reached"]


def test_reach_state_is_in_own_terms_for_the_down_leg():
    #: resting DOWN buy at 0.20; DOWN own ask = 1 - YES bid
    #: YES 0.80/0.82 -> own ask 0.20 -> reached
    assert K.reach_state("down", D("0.20"), "0.80", "0.82")["reached"]
    #: YES 0.78/0.82 -> own ask 0.22 -> not reached
    assert not K.reach_state("down", D("0.20"), "0.78", "0.82")["reached"]


def test_only_correctly_directed_prints_could_have_consumed_us():
    assert K.CONSUMED_BY == {"up": "SELL", "down": "BUY"}
    trades = [{"direction": "SELL", "price": "0.20"},     # hits our up buy
              {"direction": "BUY", "price": "0.20"},      # wrong direction
              {"direction": "SELL", "price": "0.30"},     # above our price
              {"direction": "", "price": "0.20"},         # unknown: fills none
              {"price": "0.20"}]                          # no direction at all
    got = K.consuming_trades("up", D("0.20"), trades)
    assert len(got) == 1 and got[0]["direction"] == "SELL"
    #: for the down leg a BUY print at YES 0.80 is own-terms 0.20
    got = K.consuming_trades("down", D("0.20"),
                             [{"direction": "BUY", "price": "0.80"},
                              {"direction": "SELL", "price": "0.80"}])
    assert len(got) == 1 and got[0]["direction"] == "BUY"


def test_unknown_direction_never_counts_as_a_consuming_print():
    assert K.consuming_trades("up", D("0.50"),
                             [{"direction": "UNKNOWN", "price": "0.10"}]) == []
    assert K.consuming_trades("up", D("0.50"), None) == []


# -------------------------------------------------------- qualification

def test_all_four_preregistered_conditions_are_required():
    assert K.QUALIFYING_CONDITIONS == ("rest_proved", "reached",
                                       "private_terminal", "public_sequence")
    full = dict.fromkeys(K.QUALIFYING_CONDITIONS, True)
    assert K.qualifies(full)["qualifies"]
    for drop in K.QUALIFYING_CONDITIONS:
        part = dict(full, **{drop: False})
        q = K.qualifies(part)
        assert not q["qualifies"]
        assert q["missing"] == [drop]


# ------------------------------------------------------------- classify

def test_classification_labels():
    reach_no = {"touched": True, "reached": False}
    reach_yes = {"touched": True, "reached": True}
    ok = {"qualifies": True}
    bad = {"qualifies": False}
    assert K.classify(submitted=False, rest_proved=False, reach=reach_no,
                      filled_qty=D(0), qual=bad) == "REFUSED"
    assert K.classify(submitted=True, rest_proved=False, reach=reach_no,
                      filled_qty=D(0), qual=bad) == "STOPPED_REST_NOT_PROVED"
    #: the market never came to us. A VALID NULL, and we did not chase it.
    assert K.classify(submitted=True, rest_proved=True, reach=reach_no,
                      filled_qty=D(0), qual=bad) == "NULL_NOT_REACHED"
    assert K.classify(submitted=True, rest_proved=True, reach=reach_yes,
                      filled_qty=D(1), qual=ok) == "CASE1_OBSERVED_FILLED"
    #: crossed and NOT filled is a real Case-1 answer, not a failure
    assert K.classify(submitted=True, rest_proved=True, reach=reach_yes,
                      filled_qty=D(0), qual=ok) == "CASE1_OBSERVED_NOT_FILLED"
    assert K.classify(submitted=True, rest_proved=True, reach=reach_yes,
                      filled_qty=D(0), qual=bad) == "CASE1_INCONCLUSIVE"


def test_no_outcome_label_claims_realized_profit_or_validation():
    """The real property is about the LABELS the probe emits, not about whether
    the prose contains the word -- the prose says the opposite on purpose."""
    from scripts import pmus_case1_probe_run as RUN
    labels = set(RUN.TERMINAL_LABELS) | {
        K.classify(submitted=s, rest_proved=r, reach={"reached": x},
                   filled_qty=D(f), qual={"qualifies": q})
        for s in (True, False) for r in (True, False) for x in (True, False)
        for f in (0, 1) for q in (True, False)}
    for label in labels:
        up = label.upper()
        for banned in ("REALIZED", "PROFIT", "VALIDATED", "OOS", "SETTLED_NET"):
            assert banned not in up, label
    #: and the report itself carries an explicit non-profit label
    text = pathlib.Path("scripts/pmus_case1_probe_run.py").read_text()
    assert "not realized profit, not OOS" in text


# ------------------------------------------------- private field capture

def test_every_authorized_field_is_captured_or_marked_unavailable():
    assert set(K.PRIVATE_CAPTURE_FIELDS) == {
        "type", "tradeId", "aggressor", "transactTime", "lastShares",
        "lastPx", "commissionNotionalCollected", "cumQuantity",
        "leavesQuantity"}
    frames = [{"subscriptionType": "SUBSCRIPTION_TYPE_ORDER",
               "orderUpdate": {"order": {
                   "id": "OID-1", "type": "EXECUTION_TYPE_NEW",
                   "tradeId": "", "cumQuantity": "0",
                   "leavesQuantity": "1"}}},
              {"subscriptionType": "SUBSCRIPTION_TYPE_ORDER",
               "orderUpdate": {"order": {
                   "id": "OID-1", "type": "EXECUTION_TYPE_TRADE",
                   "tradeId": "T-9", "aggressor": "AGGRESSOR_SIDE_SELL",
                   "transactTime": "2026-10-01T15:00:00Z",
                   "lastShares": "1", "lastPx": {"value": "0.20"},
                   "cumQuantity": "1", "leavesQuantity": "0"}}},
              {"subscriptionType": "SUBSCRIPTION_TYPE_ORDER",
               "orderUpdate": {"order": {"id": "OTHER", "tradeId": "NOPE"}}}]
    got = K.extract_private(frames, "OID-1")
    assert got["frames_naming_our_order"] == 2
    assert got["fields"]["tradeId"] == ["", "T-9"]
    assert got["fields"]["aggressor"] == ["AGGRESSOR_SIDE_SELL"]
    assert got["fields"]["type"] == ["EXECUTION_TYPE_NEW",
                                     "EXECUTION_TYPE_TRADE"]
    #: commission never appeared, so it is UNAVAILABLE, never defaulted to 0
    assert got["fields"]["commissionNotionalCollected"] == "UNAVAILABLE"
    assert "commissionNotionalCollected" in got["fields_unavailable"]
    #: and the other order's frame contributed nothing
    assert "NOPE" not in json.dumps(got["fields"])


def test_extract_private_is_safe_with_no_order_id_and_no_frames():
    got = K.extract_private([], "")
    assert got["frames_naming_our_order"] == 0
    assert sorted(got["fields_unavailable"]) == sorted(
        K.PRIVATE_CAPTURE_FIELDS)


# ------------------------------- against the RECORDER'S REAL row shape, not
# ------------------------------- an invented dictionary

def test_consuming_trades_reads_the_recorders_actual_row_shape():
    """`apply_events` emits `px` and `dir`, not `price`/`direction`. Reading
    only the latter found zero consuming prints on every real tape, so this
    test builds the rows with the recorder itself rather than by hand."""
    from src import pmus_ws_evidence_recorder as R
    slug = "cpc-btc-updown-15m-2099-01-01-1200z"
    state = {}
    #: a two-sided book first, so direction is resolvable against it
    R.apply_events(state, {"marketSlug": slug,
                           "bids": [{"px": {"value": "0.20"}, "qty": "50"}],
                           "offers": [{"px": {"value": "0.22"}, "qty": "40"}]},
                   1.0)
    #: a venue-stated SELL print at 0.20, and a BUY print at 0.22
    R.apply_events(state, {"marketSlug": slug, "price": {"value": "0.20"},
                           "qty": "5", "tradeTime": "1",
                           "taker": {"side": "SELL"}}, 2.0)
    R.apply_events(state, {"marketSlug": slug, "price": {"value": "0.22"},
                           "qty": "7", "tradeTime": "2",
                           "taker": {"side": "BUY"}}, 3.0)
    rows = state[slug]["trades"]
    assert rows, "the recorder produced no trade rows; the fixture is wrong"
    assert "px" in rows[0] and "dir" in rows[0], rows[0]

    #: our resting UP buy at 0.20 can only be consumed by the SELL
    got = K.consuming_trades("up", D("0.20"), rows)
    assert len(got) == 1
    assert got[0]["dir"].upper() == "SELL" and got[0]["px"] == "0.20"
    #: and a resting UP buy at 0.19 is below both prints: nothing consumes it
    assert K.consuming_trades("up", D("0.19"), rows) == []


# =====================================================================
# THE SIX-LABEL VERDICT TAXONOMY
# =====================================================================

def test_the_six_verdicts_are_exactly_these_and_mutually_exclusive():
    assert K.VERDICTS == ("RESTING_NOT_REACHED", "REACHED_OR_CROSSED_UNFILLED",
                          "FILLED_AS_MAKER", "FILLED_AS_AGGRESSOR",
                          "AMBIGUOUS_EVENT_ORDER", "PROBE_FAILURE")
    assert len(set(K.VERDICTS)) == len(K.VERDICTS)


def _v(**kw):
    base = dict(safety_ok=True, rest_proved=True, reached_before_rest=False,
                reached_after_rest=False, filled_qty=0,
                role={"role": "UNRESOLVED"})
    base.update(kw)
    return K.verdict(**base)


def test_no_fill_and_no_opportunity_never_share_a_bucket():
    """The distinction Case 1 turns on."""
    assert _v(reached_after_rest=False) == "RESTING_NOT_REACHED"
    assert _v(reached_after_rest=True) == "REACHED_OR_CROSSED_UNFILLED"


def test_a_safety_or_lifecycle_deviation_is_never_an_evidence_claim():
    for kw in ({"safety_ok": False}, {"rest_proved": False}):
        assert _v(**kw) == "PROBE_FAILURE"
    #: and it outranks everything, including a fill
    assert _v(safety_ok=False, filled_qty=1,
              role={"role": "MAKER"}) == "PROBE_FAILURE"


def test_a_book_already_there_when_we_were_proved_resting_is_ambiguous():
    """If the book was already at/through our price at rest, the ordering claim
    is unestablished and no amount of later observation rescues it."""
    assert _v(reached_before_rest=True,
              reached_after_rest=True) == "AMBIGUOUS_EVENT_ORDER"
    assert _v(reached_before_rest=True, filled_qty=1,
              role={"role": "MAKER"}) == "AMBIGUOUS_EVENT_ORDER"


def test_fill_role_decides_between_maker_and_aggressor():
    assert _v(filled_qty=1, role={"role": "MAKER"}) == "FILLED_AS_MAKER"
    assert _v(filled_qty=1, role={"role": "AGGRESSOR"}) == "FILLED_AS_AGGRESSOR"
    #: an unresolved role is NEVER guessed into one of the two
    assert _v(filled_qty=1,
              role={"role": "UNRESOLVED"}) == "AMBIGUOUS_EVENT_ORDER"


# ------------------------------------------------- the two-signal role rule

def test_the_aggressor_orientation_rule_is_marked_unverified():
    assert K.AGGRESSOR_RULE_VERIFIED is False, \
        "no real fill has ever established this mapping for our account"


def test_one_signal_alone_is_never_enough():
    px = D("0.20")
    #: aggressor only
    r = K.fill_role({"aggressor": ["AGGRESSOR_SIDE_SELL"],
                     "commissionNotionalCollected": "UNAVAILABLE"}, px)
    assert r["role"] == "UNRESOLVED" and "one is never enough" in r["basis"]
    #: commission only
    r = K.fill_role({"aggressor": "UNAVAILABLE",
                     "commissionNotionalCollected": ["0"]}, px)
    assert r["role"] == "UNRESOLVED"


def test_two_agreeing_signals_resolve_maker():
    """Post-only filled by someone else's SELL, nothing charged to us."""
    r = K.fill_role({"aggressor": ["AGGRESSOR_SIDE_SELL"],
                     "commissionNotionalCollected": ["0"]}, D("0.20"))
    assert r["role"] == "MAKER"
    assert r["basis"] == "two independent signals agree"
    assert r["aggressor_rule_verified"] is False


def test_two_agreeing_signals_resolve_aggressor_against_the_fee_schedule():
    from src import pmus_fees as FEES
    px = D("0.20")
    charged = FEES.taker_fee(px, D(1), cents=True)
    assert charged > 0, "the fee model must charge a taker something"
    r = K.fill_role({"aggressor": ["AGGRESSOR_SIDE_BUY"],
                     "commissionNotionalCollected": [str(charged)]}, px)
    assert r["role"] == "AGGRESSOR"


def test_disagreeing_signals_are_reported_not_interpreted():
    r = K.fill_role({"aggressor": ["AGGRESSOR_SIDE_BUY"],
                     "commissionNotionalCollected": ["0"]}, D("0.20"))
    assert r["role"] == "UNRESOLVED"
    assert "DISAGREE" in r["basis"]


def test_a_commission_we_cannot_account_for_is_not_evidence():
    assert K.role_from_commission("0.99", D("0.20")) is None
    assert K.role_from_commission(None, D("0.20")) is None
    assert K.role_from_commission("UNAVAILABLE", D("0.20")) is None
    #: a credit to us is maker evidence
    assert K.role_from_commission("-0.0125", D("0.20")) == "MAKER"
    #: and the venue's nested {"value": ...} shape is read too
    assert K.role_from_commission({"value": "0"}, D("0.20")) == "MAKER"


def test_a_contradictory_or_empty_aggressor_string_yields_nothing():
    for bad in ("", None, "AGGRESSOR_SIDE_UNSPECIFIED", "BUY_SELL"):
        assert K.role_from_aggressor(bad) is None
    assert K.role_from_aggressor("AGGRESSOR_SIDE_SELL") == "MAKER"
    assert K.role_from_aggressor("AGGRESSOR_SIDE_BUY") == "AGGRESSOR"
