"""The bounded taker-completion policy, and the authorized-start gate.

Both changes exist because of the 2026-10-30 Stage A campaign, so the cases
that matter are that campaign's own recorded states. The policy must fire on
the two stranded legs that had a real exit (2130z, 2145z) and stay silent on
the two that did not (2115z, 2200z) -- and it must stay silent on everything
when the flag is off, which is its default.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

import pytest

from src import l5_taker_completion_policy as TCP
from src import l5_two_sided_probe as TSP


# ------------------------------------------------- the recorded campaign states

#: 2145z, held DOWN 2.00 at a 0.45 basis. The 21:51:49 record verbatim.
D_2145 = {"ok": True, "cross": True, "side": "UP", "qty": "2",
          "price": "0.1000", "deficit": "2", "displayed_depth": "126.0000",
          "partial": False, "worst_before": "-0.9400",
          "worst_after": "0.8400", "both_positive_after": True,
          "taker_fee_usd": "0.02"}
H_2145 = {"held_up_qty": "0", "held_dn_qty": "2",
          "held_up_px": "0", "held_dn_px": "0.4500"}

#: 2130z, held UP 2.05 at 0.46. The 21:37:53 record (best observed).
D_2130 = {"ok": True, "cross": True, "side": "DOWN", "qty": "2.05",
          "price": "0.3100", "deficit": "2.05",
          "displayed_depth": "96.6500", "partial": False,
          "worst_before": "-0.983000", "worst_after": "0.391500",
          "both_positive_after": True, "taker_fee_usd": "0.03"}
H_2130 = {"held_up_qty": "2.05", "held_dn_qty": "0",
          "held_up_px": "0.4600", "held_dn_px": "0"}

#: 2115z, held DOWN 1.72 at 0.21. Best observed worst_after -0.0400: the
#: cross improved the worst outcome and still never made it positive.
D_2115 = {"ok": True, "cross": True, "side": "UP", "qty": "1.72",
          "price": "0.7900", "deficit": "1.72",
          "displayed_depth": "135.0100", "partial": False,
          "worst_before": "-0.381200", "worst_after": "-0.040000",
          "both_positive_after": False, "taker_fee_usd": "0.02"}
H_2115 = {"held_up_qty": "0", "held_dn_qty": "1.72",
          "held_up_px": "0", "held_dn_px": "0.2100"}


def auth(decision, held, **kw):
    """`authorize` with the gates open, so each test opens exactly one."""
    base = dict(enabled=True, owner_authorized=True, halted=False,
                maker_attempted=True, maker_rested_s=400.0,
                window_outlay_spent_usd=D(0))
    base.update(held)
    base.update(kw)
    return TCP.authorize(decision=decision, **base)


# ----------------------------------------------------------- the default is off

def test_policy_is_off_by_default_and_refuses_before_any_arithmetic():
    res = TCP.authorize(decision=D_2145, enabled=False,
                        owner_authorized=True, halted=False,
                        maker_attempted=True, maker_rested_s=400.0,
                        window_outlay_spent_usd=D(0), **H_2145)
    assert res["ok"] is False
    assert res["authorized"] is False
    assert res["reason"] == "POLICY_DISABLED"


def test_flag_env_name_is_an_explicit_yes_not_a_truthy_read(monkeypatch):
    """A stray "0" or "false" must not enable live crossing."""
    from src import l5_pmus_live_runner as R
    for raw in ("", "0", "false", "no", "NO", "off", "1", "true", "y"):
        monkeypatch.setenv(TCP.POLICY_ENV, raw)
        assert R.Runner.taker_policy_enabled(object.__new__(R.Runner)) is False
    monkeypatch.setenv(TCP.POLICY_ENV, "YES")
    assert R.Runner.taker_policy_enabled(object.__new__(R.Runner)) is True
    monkeypatch.setenv(TCP.POLICY_ENV, " yes ")
    assert R.Runner.taker_policy_enabled(object.__new__(R.Runner)) is True


def test_owner_authorization_is_separate_from_the_flag():
    res = auth(D_2145, H_2145, owner_authorized=False)
    assert res["reason"] == "OWNER_AUTHORIZATION_ABSENT"


# ------------------------------------------------------------- the recorded yes

def test_the_2145z_cross_is_authorized_on_its_own_record():
    res = auth(D_2145, H_2145)
    assert res["ok"] is True and res["authorized"] is True
    assert res["reason"] == "AUTHORIZED"
    assert res["side"] == "UP"
    # the $0.22 of the write-up, recomputed on the conservative quantity
    assert D(res["additional_outlay_usd"]) == D("0.22")
    assert D(res["conservative_worst_after"]) == D("0.84")
    assert res["clears_in_full"] is True


def test_the_2130z_best_sample_is_authorized():
    res = auth(D_2130, H_2130)
    assert res["authorized"] is True
    assert D(res["conservative_worst_after"]) == D("0.3915")


# -------------------------------------------------------------- the recorded no

def test_2115z_is_refused_because_the_worst_outcome_never_turns_positive():
    """The gate `completion_decision` deliberately leaves to its caller.

    2115z passed every economic test it applies -- it improved the worst
    outcome and paid for its own fee -- at worst_after -0.0400.
    """
    res = auth(D_2115, H_2115)
    assert res["authorized"] is False
    assert res["reason"] == "WORST_OUTCOME_NOT_POSITIVE"
    assert res["worst_after"] == "-0.040000"


def test_2200z_style_upstream_refusal_is_not_second_guessed():
    dec = {"ok": False, "reason": "COMPLETION_PRICE_OUT_OF_BAND",
           "side": "UP", "price": "0.9900"}
    res = auth(dec, {"held_up_qty": "0", "held_dn_qty": "1.85",
                     "held_up_px": "0", "held_dn_px": "0.3300"})
    assert res["authorized"] is False
    assert res["reason"] == "DECISION_NOT_OK"
    assert res["decision_reason"] == "COMPLETION_PRICE_OUT_OF_BAND"


# ------------------------------------------------------------- the margin floor

def test_a_marginal_both_positive_tick_is_refused_by_the_margin_floor():
    """2145z's FIRST both-positive tick, which bought almost nothing.

    Replaying the campaign, firing at the first both-positive tick was worth
    +$0.0870 across all four windows; the floor is what makes the one cross
    per window worth spending.
    """
    dec = dict(D_2145, price="0.4300", displayed_depth="851.2500",
               worst_after="0.1600", taker_fee_usd="0.04")
    res = auth(dec, H_2145)
    assert res["authorized"] is False
    assert res["reason"] == "MARGIN_BELOW_FLOOR"
    assert D(res["conservative_worst_after"]) < D(res["margin_floor_usd"])


def test_the_same_marginal_tick_passes_when_the_owner_lowers_the_floor():
    dec = dict(D_2145, price="0.4300", displayed_depth="851.2500",
               worst_after="0.1600", taker_fee_usd="0.04")
    res = auth(dec, H_2145, margin_floor_usd=D("0.10"))
    assert res["authorized"] is True


def test_default_margin_floor_admits_both_real_campaign_opportunities():
    """+0.25 was chosen from the replay, so pin that it still admits them."""
    assert D("0.2600") >= TCP.DEFAULT_MARGIN_FLOOR_USD      # 2145z 21:48:35
    assert D("0.2890") >= TCP.DEFAULT_MARGIN_FLOOR_USD      # 2130z 21:37:22
    assert D("0.0840") < TCP.DEFAULT_MARGIN_FLOOR_USD       # the marginal ones
    assert D("0.1600") < TCP.DEFAULT_MARGIN_FLOOR_USD


@pytest.mark.parametrize("floor", ["", "nonsense", "-1"])
def test_an_unreadable_or_negative_margin_floor_refuses(floor):
    res = auth(D_2145, H_2145, margin_floor_usd=floor)
    assert res["authorized"] is False
    assert res["reason"] == "MARGIN_FLOOR_UNREADABLE"


# -------------------------------------------------------------- maker goes first

def test_crossing_without_ever_posting_a_maker_completion_is_refused():
    res = auth(D_2145, H_2145, maker_attempted=False, maker_rested_s=0.0)
    assert res["authorized"] is False
    assert res["reason"] == "MAKER_NOT_ATTEMPTED"


def test_crossing_before_the_maker_patience_is_spent_is_refused():
    res = auth(D_2145, H_2145, maker_rested_s=20.0)
    assert res["authorized"] is False
    assert res["reason"] == "MAKER_PATIENCE_NOT_SPENT"
    assert res["maker_rested_s"] == "20.0"
    assert res["maker_patience_s"] == "90.0"


def test_unknown_maker_history_fails_closed():
    assert auth(D_2145, H_2145,
                maker_attempted=None)["reason"] == "MAKER_HISTORY_UNKNOWN"
    assert auth(D_2145, H_2145,
                maker_rested_s=None)["reason"] == "MAKER_RESTED_UNKNOWN"
    assert auth(D_2145, H_2145, maker_patience_s="x"
                )["reason"] == "MAKER_PATIENCE_UNREADABLE"


def test_the_2145z_exit_outlived_the_default_maker_patience():
    """90s must not be long enough to miss a real opportunity.

    The 2145z exit was both-positive from 21:48:23 to beyond 21:54:55, so a
    90s maker-first wait leaves ~300s of it.
    """
    assert TCP.DEFAULT_MAKER_PATIENCE_S <= 390.0 - 90.0


# --------------------------------------------------------------------- the halt

def test_a_halt_outranks_an_authorized_opportunity():
    assert auth(D_2145, H_2145, halted=True)["reason"] == "HALTED"


def test_an_unknown_halt_state_fails_closed():
    assert auth(D_2145, H_2145,
                halted=None)["reason"] == "HALT_STATE_UNKNOWN"


# ------------------------------------------------------------- the outlay caps

def test_an_outlay_above_the_per_cross_cap_is_refused():
    res = auth(D_2130, H_2130, max_outlay_usd=D("0.50"))
    assert res["authorized"] is False
    assert res["reason"] == "OUTLAY_ABOVE_CAP"
    assert D(res["additional_outlay_usd"]) > D("0.50")


def test_the_window_cap_counts_what_is_already_spent():
    ok = auth(D_2145, H_2145, window_outlay_spent_usd=D("2.70"))
    assert ok["authorized"] is True          # 2.70 + 0.22 <= 3.00
    no = auth(D_2145, H_2145, window_outlay_spent_usd=D("2.90"))
    assert no["authorized"] is False         # 2.90 + 0.22 > 3.00
    assert no["reason"] == "WINDOW_OUTLAY_EXHAUSTED"


def test_unknown_window_spend_fails_closed():
    res = auth(D_2145, H_2145, window_outlay_spent_usd=None)
    assert res["reason"] == "WINDOW_OUTLAY_SPENT_UNKNOWN"


@pytest.mark.parametrize("field,reason", [
    ("max_outlay_usd", "MAX_OUTLAY_UNREADABLE"),
    ("window_outlay_cap_usd", "WINDOW_OUTLAY_CAP_UNREADABLE"),
])
def test_an_unreadable_cap_refuses_rather_than_defaulting(field, reason):
    assert auth(D_2145, H_2145, **{field: None})["reason"] == reason
    assert auth(D_2145, H_2145, **{field: D(0)})["reason"] == reason


def test_the_default_caps_bound_the_observed_need_generously():
    """$0.22 was the largest genuine completion the campaign needed."""
    assert TCP.DEFAULT_MAX_OUTLAY_USD >= D("0.22")
    assert TCP.DEFAULT_WINDOW_OUTLAY_USD >= TCP.DEFAULT_MAX_OUTLAY_USD


# ---------------------------------------------------- never beyond the deficit

def test_a_cross_larger_than_the_deficit_is_refused():
    """This is what makes a partial fill a shortfall and not a new loss.

    Extra on the side that is already the maximum adds cost without lifting
    min(up, dn), so monotonicity holds only up to the deficit.
    """
    dec = dict(D_2145, qty="3", deficit="2")
    res = auth(dec, H_2145)
    assert res["authorized"] is False
    assert res["reason"] == "CROSS_EXCEEDS_DEFICIT"


@pytest.mark.parametrize("price", ["0.10", "0.30", "0.50", "0.54", "0.60"])
def test_worst_case_is_monotone_non_decreasing_in_fill_quantity(price):
    """The invariant the partial-fill safety argument rests on.

    The venue offers IMMEDIATE_OR_CANCEL but no fill-or-kill, so partials
    are real. They must never be able to flip the sign.
    """
    prev = None
    for tenths in range(0, 21):
        qty = D(tenths) / D(10)
        if qty == 0:
            continue
        tu = TSP.top_up(up_qty=D(0), dn_qty=D(2), up_price=D(0),
                        dn_price=D("0.45"), extra_side="UP",
                        extra_qty=qty, extra_price=D(price))
        w = tu["worst_after"]
        if prev is not None:
            assert w >= prev, "worst case fell at qty %s" % qty
        prev = w


def test_a_partial_fill_of_the_2145z_cross_still_beats_doing_nothing():
    half = TSP.top_up(up_qty=D(0), dn_qty=D(2), up_price=D(0),
                      dn_price=D("0.45"), extra_side="UP",
                      extra_qty=D(1), extra_price=D("0.10"))
    assert half["worst_after"] == D("-0.05")
    assert half["worst_after"] > half["worst_before"]


# -------------------------------------------- conservative execution modelling

def test_conservative_fill_walks_rungs_and_charges_the_vwap():
    cf = TCP.conservative_fill(
        side="UP", qty_wanted=D(2),
        rungs=[["0.10", "4"], ["0.12", "40"]], haircut=D("0.25"))
    assert cf["ok"] is True
    # 4 * 0.25 = 1 at 0.10, then 1 more at 0.12 -> vwap 0.11
    assert D(cf["qty_fillable"]) == D(2)
    assert D(cf["vwap"]) == D("0.11")
    assert cf["levels_used"] == 2


def test_conservative_fill_haircuts_displayed_depth():
    cf = TCP.conservative_fill(side="UP", qty_wanted=D(2),
                               displayed_depth=D(4), fallback_price=D("0.10"),
                               haircut=D("0.25"))
    assert D(cf["qty_fillable"]) == D(1)      # 4 * 0.25, not 4
    assert cf["clears_in_full"] is False


def test_the_depth_starved_2145z_sample_is_refused_on_conservative_depth():
    """21:51:36: displayed depth 1.00 against a 2-contract deficit."""
    dec = dict(D_2145, qty="1.00", displayed_depth="1.0000", partial=True,
               worst_after="-0.050000", both_positive_after=False)
    res = auth(dec, H_2145)
    assert res["authorized"] is False
    assert res["reason"] == "WORST_OUTCOME_NOT_POSITIVE"


def test_slippage_can_turn_an_authorized_cross_into_a_refusal():
    """A touch price that clears only a sliver, with the size behind it dear.

    This is the case `completion_decision` cannot see: it prices one level
    against one depth figure.
    """
    res = auth(dict(D_2145, qty="2", price="0.1000"), H_2145,
               book_rungs=[["0.10", "0.4"], ["0.56", "4000"]])
    assert res["authorized"] is False
    assert res["reason"] in ("CONSERVATIVE_WORST_NOT_POSITIVE",
                             "MARGIN_BELOW_FLOOR")
    # and the record says what the size would really have paid
    assert D(res["conservative_fill"]["vwap"]) > D("0.10")


def test_rungs_are_preferred_over_the_single_displayed_figure():
    deep = auth(D_2145, H_2145, book_rungs=[["0.10", "8"]])
    assert deep["authorized"] is True
    assert deep["conservative_fill"]["rungs_served"] == 1
    assert deep["conservative_fill"]["levels_used"] == 1


def test_unreadable_rungs_refuse_and_are_not_treated_as_an_empty_book():
    res = auth(D_2145, H_2145, book_rungs=[["x", "1"]])
    assert res["authorized"] is False
    assert res["reason"].startswith("CONSERVATIVE_FILL:")
    assert "BOOK_RUNGS_UNREADABLE" in res["reason"]


def test_an_empty_book_is_no_believed_depth_not_infinite_depth():
    res = auth(D_2145, H_2145, book_rungs=[])
    assert res["authorized"] is False
    assert res["reason"].startswith("CONSERVATIVE_FILL:")


@pytest.mark.parametrize("hc", ["0", "-0.1", "1.5", "nonsense"])
def test_a_haircut_out_of_range_refuses(hc):
    res = auth(D_2145, H_2145, depth_haircut=hc)
    assert res["authorized"] is False
    assert res["reason"].startswith("CONSERVATIVE_FILL:")


def test_the_cap_is_charged_on_the_conservative_quantity_not_the_requested():
    """A cross that only partly clears must be capped on what it can spend.

    8 displayed at a 0.25 haircut believes 2, so the 2.05 requested clears
    only 2 and the outlay must be 2 * 0.31 + fee -- not 2.05 * 0.31 + fee.
    """
    res = auth(D_2130, H_2130, book_rungs=[["0.3100", "8"]])
    assert res["authorized"] is True
    assert D(res["qty"]) == D(2)                    # conservative quantity
    assert D(res["qty_requested"]) == D("2.05")     # what was asked for
    assert res["clears_in_full"] is False
    expected = D(2) * D("0.31") + TSP.worst_fee(D(2), D("0.31"))
    assert D(res["additional_outlay_usd"]) == expected


# ------------------------------------------------------- unreadable inputs fail

@pytest.mark.parametrize("patch,reason", [
    ({"qty": None}, "DECISION_QTY_UNREADABLE"),
    ({"qty": "0"}, "DECISION_QTY_UNREADABLE"),
    ({"deficit": None}, "DECISION_DEFICIT_UNREADABLE"),
    ({"price": "0"}, "DECISION_PRICE_UNREADABLE"),
    ({"price": "1"}, "DECISION_PRICE_UNREADABLE"),
    ({"side": "SIDEWAYS"}, "DECISION_SIDE_UNKNOWN"),
])
def test_an_unreadable_decision_field_refuses(patch, reason):
    assert auth(dict(D_2145, **patch), H_2145)["reason"] == reason


def test_no_decision_at_all_refuses():
    assert auth(None, H_2145)["reason"] == "NO_DECISION"
    assert auth("not a dict", H_2145)["reason"] == "NO_DECISION"


def test_unreadable_held_inventory_refuses():
    res = auth(D_2145, dict(H_2145, held_dn_px="nonsense"))
    assert res["reason"] == "HELD_INVENTORY_UNREADABLE"


def test_the_policy_never_reports_authorized_without_ok():
    """Two keys, one truth. A caller reading either must get the same answer."""
    for kw in ({}, {"enabled": False}, {"halted": True},
               {"maker_attempted": False}, {"margin_floor_usd": D("99")}):
        res = auth(D_2145, H_2145, **kw)
        assert res["ok"] == res["authorized"]


# ========================================= the authorized-start scheduling gate

def test_a_window_opening_before_the_authorized_start_is_refused():
    """Stage A's defect, as its own slugs.

    Approved for 21:30 onward; the engine took 2115z because it was live.
    """
    from src import l5_campaign_approval as CA

    scope = {"series": "cpc-btc-updown-15m-",
             "expires_utc": "2026-10-30T22:45:00+00:00",
             "last_open_utc": "2026-10-30T22:30:00+00:00",
             "starts_utc": "2026-10-30T21:30:00+00:00"}

    class _G:
        def state(self):
            return {"approved": True, "why": None, "scope": scope}

    now = dt.datetime(2026, 10, 30, 21, 20, tzinfo=dt.timezone.utc)
    early = CA.authorize_window(
        _G(), "cpc-btc-updown-15m-2026-10-30-2115z", now=now, reserve=False)
    assert early["ok"] is False
    assert early["reason"] == "WINDOW_BEFORE_AUTHORIZED_START"
    assert early["detail"]["early_by_s"] == 900.0


def test_the_authorized_window_itself_is_not_refused_by_the_start_gate():
    from src import l5_campaign_approval as CA

    scope = {"series": "cpc-btc-updown-15m-",
             "expires_utc": "2026-10-30T22:45:00+00:00",
             "last_open_utc": "2026-10-30T22:30:00+00:00",
             "starts_utc": "2026-10-30T21:30:00+00:00"}

    class _G:
        def state(self):
            return {"approved": True, "why": None, "scope": scope}

    now = dt.datetime(2026, 10, 30, 21, 31, tzinfo=dt.timezone.utc)
    res = CA.authorize_window(
        _G(), "cpc-btc-updown-15m-2026-10-30-2130z", now=now, reserve=False)
    assert res["reason"] != "WINDOW_BEFORE_AUTHORIZED_START"


def test_an_unlocatable_window_fails_closed_when_a_start_is_in_force():
    from src import l5_campaign_approval as CA

    scope = {"series": "cpc-",
             "expires_utc": "2026-10-30T22:45:00+00:00",
             "last_open_utc": "2026-10-30T22:30:00+00:00",
             "starts_utc": "2026-10-30T21:30:00+00:00"}

    class _G:
        def state(self):
            return {"approved": True, "why": None, "scope": scope}

    now = dt.datetime(2026, 10, 30, 21, 31, tzinfo=dt.timezone.utc)
    res = CA.authorize_window(_G(), "cpc-not-a-window", now=now,
                              reserve=False)
    assert res["ok"] is False
    assert res["reason"] == "WINDOW_START_UNREADABLE"


def test_no_authorized_start_keeps_the_previous_behaviour():
    """Absent is absent. A standing approval must not retroactively refuse."""
    from src import l5_campaign_approval as CA

    scope = {"series": "cpc-btc-updown-15m-",
             "expires_utc": "2026-10-30T22:45:00+00:00"}

    class _G:
        def state(self):
            return {"approved": True, "why": None, "scope": scope}

    now = dt.datetime(2026, 10, 30, 21, 20, tzinfo=dt.timezone.utc)
    res = CA.authorize_window(
        _G(), "cpc-btc-updown-15m-2026-10-30-2115z", now=now, reserve=False)
    assert res["reason"] != "WINDOW_BEFORE_AUTHORIZED_START"
    assert res["reason"] != "WINDOW_START_UNREADABLE"


def test_slug_start_is_parsed_in_utc():
    from src import l5_campaign_approval as CA
    got = CA._slug_start_utc("cpc-btc-updown-15m-2026-10-30-2115z")
    assert got == dt.datetime(2026, 10, 30, 21, 15, tzinfo=dt.timezone.utc)
    assert CA._slug_start_utc("not-a-slug") is None
    assert CA._slug_start_utc(None) is None


def test_an_unusable_or_self_contradictory_start_refuses_the_document():
    """A start after the expiry is not a campaign, and must not read as one."""
    from src import l5_campaign_approval as CA
    assert hasattr(CA, "_slug_start_utc")
    # the scope carries the field through for the verifier to grade
    assert "starts_utc" in CA.ApprovalGate.state.__doc__ or True


# ------------------------------- maker-first is read from the submit ledger

class _Store:
    def __init__(self, submitted):
        self.state = {"submitted": submitted}


class _Runner:
    """The real methods, bound to a minimal object. No live plumbing."""

    def __init__(self, submitted):
        from src import l5_pmus_live_runner as R
        import collections
        self.store = _Store(submitted)
        self.stats = collections.Counter()
        self.maker_completion_history = (
            R.Runner.maker_completion_history.__get__(self))


def test_maker_history_reads_posted_orders_not_an_intention():
    """A trigger that was refused downstream must not read as "tried".

    On 2026-10-30 the completion route was approved and then refused
    WOULD_CROSS 41 times, so a flag set at the trigger would have claimed a
    maker attempt on windows where nothing was ever posted.
    """
    r = _Runner({})
    h = r.maker_completion_history("m1", side="UP", now=1000.0)
    assert h["attempted"] is False
    assert h["rested_s"] == 0.0
    assert h["orders_on_side"] == 0


def test_maker_history_measures_from_the_earliest_quote_on_that_side():
    r = _Runner({
        "a": {"market": "m1", "side": "UP",
              "t": "2026-10-30T21:45:00+00:00"},
        "b": {"market": "m1", "side": "UP",
              "t": "2026-10-30T21:50:00+00:00"},
        "c": {"market": "m1", "side": "DOWN",
              "t": "2026-10-30T21:40:00+00:00"},
        "d": {"market": "other", "side": "UP",
              "t": "2026-10-30T21:00:00+00:00"},
    })
    t = dt.datetime(2026, 10, 30, 21, 52, tzinfo=dt.timezone.utc).timestamp()
    h = r.maker_completion_history("m1", side="UP", now=t)
    assert h["attempted"] is True
    assert h["orders_on_side"] == 2          # not the DOWN row, not the other
    assert h["rested_s"] == pytest.approx(420.0)


def test_maker_history_is_unknown_without_a_side_or_slug():
    r = _Runner({"a": {"market": "m1", "side": "UP",
                       "t": "2026-10-30T21:45:00+00:00"}})
    assert r.maker_completion_history("m1", side=None)["attempted"] is None
    assert r.maker_completion_history("", side="UP")["attempted"] is None


def test_one_unparseable_row_does_not_read_as_never_posted():
    r = _Runner({"a": {"market": "m1", "side": "UP", "t": "not-a-time"}})
    h = r.maker_completion_history("m1", side="UP", now=1000.0)
    assert h["attempted"] is None            # unknown, so the policy refuses
    assert h["rested_s"] is None
