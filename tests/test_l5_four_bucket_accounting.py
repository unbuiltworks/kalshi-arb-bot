"""Account-wide, verified L5, manual and unknown -- kept APART.

The failure this prevents: a $1/share upper-bound placeholder, or a pre-T
total whose L5 share nobody knows, being reported as the owner's verified
manual trading loss or as a verified L5 loss. Both are UNKNOWN, and a None
here never reads as zero.
"""
from decimal import Decimal as D

import pytest

from src import l5_live_execution as E


def store(tmp_path, **state):
    st = E.InventoryStore(str(tmp_path / "s.json"))
    st.state.update(state)
    return st


def baseline(t="2026-10-16T02:45:12+00:00", pre="-79.01"):
    return {"t": t, "cash_usd": "350.05",
            "pre_baseline_realized_total": pre,
            "pre_baseline_unknown_basis_keys": []}


def retired(t, pnl, attested):
    return {"t": t, "settlement_pnl": pnl,
            "l5_owner_attribution_verified": attested,
            "pnl_provenance": "ATTESTED" if attested
            else "UNKNOWN_COST_UPPER_BOUND"}


# -------------------------------------------------- the buckets stay apart

def test_with_no_baseline_the_L5_figure_is_UNKNOWN_not_zero(tmp_path):
    st = store(tmp_path, realized_attributed="-218.99")
    acc = st.four_bucket_accounting()
    assert acc["verified_l5_realized"] is None
    assert st.gate_input("verified_l5") is None       # and never D(0)


def test_an_unattested_post_baseline_settlement_lands_in_UNKNOWN(tmp_path):
    st = store(tmp_path,
               l5_baseline=baseline(),
               retired_markets={
                   "cpceth-eth-updown-15m-2026-10-16-0330z":
                       retired("2026-10-16T03:30:00+00:00", "-50.00", False)})
    acc = st.four_bucket_accounting()
    # NOT in the L5 bucket
    assert acc["verified_l5_realized"] == "0"
    # it IS in unknown, together with the pre-T total
    assert D(acc["unknown_unattributed_realized"]) == D("-129.01")
    rows = acc["unknown_detail"]["post_baseline_settlements_not_attested"]
    assert [r["market"] for r in rows] == [
        "cpceth-eth-updown-15m-2026-10-16-0330z"]
    assert rows[0]["provenance"] == "UNKNOWN_COST_UPPER_BOUND"


def test_an_attested_post_baseline_settlement_IS_the_L5_figure(tmp_path):
    st = store(tmp_path, l5_baseline=baseline(),
               retired_markets={"m": retired("2026-10-16T03:30:00+00:00",
                                             "-1.25", True)})
    acc = st.four_bucket_accounting()
    assert D(acc["verified_l5_realized"]) == D("-1.25")


def test_the_pre_baseline_total_is_UNKNOWN_not_manual(tmp_path):
    """Its L5 share is unknown by construction, so it cannot be called the
    owner's."""
    st = store(tmp_path, l5_baseline=baseline(pre="-79.01"))
    acc = st.four_bucket_accounting()
    assert D(acc["unknown_detail"]
             ["pre_baseline_total_l5_share_unknown"]) == D("-79.01")
    assert acc["manual_realized"] is None


# ---------------------------------------- manual is a residual, not a datum

def test_manual_is_None_while_anything_is_unattributable(tmp_path):
    st = store(tmp_path, realized_attributed="-218.99",
               l5_baseline=baseline(),
               retired_markets={"m": retired("2026-10-16T03:30:00+00:00",
                                             "-50.00", False)})
    acc = st.four_bucket_accounting()
    assert acc["manual_realized"] is None
    assert acc["manual_unavailable_reason"].startswith(
        "UNATTRIBUTED_RESIDUAL_NONZERO")
    assert st.gate_input("manual") is None


def test_manual_is_None_while_any_leg_is_priced_at_the_upper_bound(tmp_path):
    st = store(tmp_path, realized_attributed="-5",
               l5_baseline=baseline(pre="0"),
               leg_cost_basis_unknown=["m|UP"])
    acc = st.four_bucket_accounting()
    assert acc["manual_realized"] is None
    assert acc["unknown_detail"]["count"] == 1
    assert acc["unknown_detail"][
        "legs_priced_at_one_dollar_upper_bound"] == ["m|UP"]


def test_manual_becomes_a_residual_only_when_nothing_is_unknown(tmp_path):
    st = store(tmp_path, realized_attributed="-6.25",
               l5_baseline=baseline(pre="0"),
               retired_markets={"m": retired("2026-10-16T03:30:00+00:00",
                                             "-1.25", True)})
    acc = st.four_bucket_accounting()
    assert D(acc["verified_l5_realized"]) == D("-1.25")
    assert D(acc["manual_realized"]) == D("-5.00")
    assert D(acc["unknown_unattributed_realized"]) == D("0")
    assert acc["identity_holds"] is True


def test_the_identity_holds_when_every_bucket_is_known(tmp_path):
    st = store(tmp_path, realized_attributed="-6.25",
               l5_baseline=baseline(pre="0"),
               retired_markets={"m": retired("2026-10-16T03:30:00+00:00",
                                             "-1.25", True)})
    acc = st.four_bucket_accounting()
    assert D(acc["account_wide_realized"]) == (
        D(acc["verified_l5_realized"]) + D(acc["manual_realized"])
        + D(acc["unknown_unattributed_realized"]))


def test_the_identity_is_NOT_claimed_when_a_bucket_is_unknown(tmp_path):
    st = store(tmp_path, realized_attributed="-218.99",
               l5_baseline=baseline())
    assert st.four_bucket_accounting()["identity_holds"] is False


# -------------------------------------------------------- fail closed

def test_an_unreadable_settlement_row_makes_the_L5_figure_UNKNOWN(tmp_path):
    st = store(tmp_path, l5_baseline=baseline(),
               retired_markets={"m": {"t": "2026-10-16T03:30:00+00:00",
                                      "settlement_pnl": "not a number",
                                      "l5_owner_attribution_verified": True}})
    assert st.l5_realized_since_baseline() is None
    assert st.four_bucket_accounting()["verified_l5_realized"] is None


def test_an_unreadable_UNKNOWN_row_refuses_rather_than_dropping_it(tmp_path):
    st = store(tmp_path, l5_baseline=baseline(),
               retired_markets={"m": {"t": "2026-10-16T03:30:00+00:00",
                                      "settlement_pnl": "wat",
                                      "l5_owner_attribution_verified": False}})
    acc = st.four_bucket_accounting()
    assert acc["unknown_unattributed_realized"] is None
    assert acc["manual_unavailable_reason"] == "UNKNOWN_ROW_UNREADABLE"


@pytest.mark.parametrize("bucket", ["", "l5", "nonsense", "ACCOUNT_WIDE"])
def test_an_unnamed_bucket_is_refused(tmp_path, bucket):
    st = store(tmp_path, realized_attributed="-1")
    assert st.gate_input(bucket) is None


def test_every_named_bucket_is_reachable(tmp_path):
    st = store(tmp_path, realized_attributed="-6.25",
               l5_baseline=baseline(pre="0"),
               retired_markets={"m": retired("2026-10-16T03:30:00+00:00",
                                             "-1.25", True)})
    for b in E.InventoryStore.ACCOUNTING_BUCKETS:
        assert st.gate_input(b) is not None, b


def test_gate_input_never_returns_zero_for_an_unknown(tmp_path):
    """THE PROPERTY THAT MATTERS. A loss ceiling fed a zero it should have
    refused is not a ceiling."""
    st = store(tmp_path, realized_attributed="-218.99")
    for b in ("verified_l5", "manual"):
        v = st.gate_input(b)
        assert v is None, (b, v)


def test_gate_input_changes_no_policy_by_itself():
    """The POPULATION a loss ceiling measures is still account-wide.

    Updated with the daily-window fix, not deleted. Two things are asserted
    together, because the protection is the pair:

    1. Both `$20` CUMULATIVE gates still read the all-time account-wide
       total, so historical losses keep restraining and a fresh baseline is
       not a reset of cumulative losses.
    2. No gate reads `gate_input("verified_l5")`. The L5-scoped rescoping in
       L5_LOSS_GATE_RESCOPING_REVIEW.md -- which would leave manual trading
       with NO realized-loss ceiling -- has NOT been applied, and `gate_input`
       remains the unused mechanism it was landed as.

    The daily halt is the one site that changed, and it changed WINDOW, not
    population: `realized_current_utc_day()` is account-wide for one day.
    """
    import inspect
    src = inspect.getsource(E.InventoryStore.gate_input)
    assert "CHANGES NO POLICY" in src
    whole = inspect.getsource(E)
    # the cumulative ceilings are untouched: all-time, account-wide
    assert whole.count("cumulative_realized=self.store.realized_today()") == 2
    # the daily halt reads the day, and no longer the all-time total
    assert whole.count("realized_today=self.store.realized_today()") == 0
    assert whole.count("realized_today=day_realized") == 1
    # and the L5-scoped rescoping has NOT been slipped in at any gate: the
    # two loss-gate keywords only ever take a sanctioned right-hand side.
    import re
    fed = set(re.findall(r"(?:cumulative_realized|realized_today)=([^,\n]+)",
                         whole))
    assert fed == {"self.store.realized_today()", "day_realized"}, fed
    assert 'gate_input("verified_l5")' not in whole


# ------------------------------------- the daily halt measures the DAY only

#: 2026-10-17T12:00:00Z, inside the day the rows below are stamped with.
TODAY_TS = 1792238400.0
TODAY = "2026-10-17"


def test_the_day_figure_counts_today_and_excludes_history(tmp_path):
    """The $5 halt is DAILY. An all-time total made it a lifetime cap that
    could never reset, and made the separate $20 cumulative limit dead."""
    st = store(tmp_path, realized_attributed="-8.39480000",
               realized_unattributed="0",
               retired_markets={
                   "old-a": retired("2026-10-16T02:00:00+00:00", "-5.00", False),
                   "old-b": retired("2026-10-11T02:00:00+00:00", "-3.39", False),
                   "today-a": retired(TODAY + "T09:00:00+00:00", "-1.25", True),
                   "today-b": retired(TODAY + "T11:00:00+00:00", "0.75", True)})
    assert st.realized_current_utc_day(now=TODAY_TS) == D("-0.50")
    # the all-time total is unchanged and still what the cumulative gates read
    assert st.realized_today() == D("-8.39480000")


def test_the_day_figure_is_zero_when_nothing_settled_today(tmp_path):
    """The deployed shape: 53 retired markets, none of them from today."""
    st = store(tmp_path, realized_attributed="-8.39480000",
               realized_unattributed="0",
               retired_markets={
                   "old": retired("2026-10-16T12:30:00+00:00", "-8.39", False)})
    assert st.realized_current_utc_day(now=TODAY_TS) == D(0)


@pytest.mark.parametrize("state,why", [
    ({"realized_unattributed": "-12.00"}, "an undated residual has no day"),
    ({"realized_unattributed": "0",
      "retired_markets": {"m": {"settlement_pnl": "-1.00"}}}, "row has no t"),
    ({"realized_unattributed": "0",
      "retired_markets": {"m": {"t": TODAY + "T09:00:00+00:00",
                                "settlement_pnl": "not-a-number"}}},
     "unreadable settlement_pnl"),
    ({"realized_unattributed": "0",
      "retired_markets": {"m": "not-a-row"}}, "unreadable row"),
])
def test_the_day_figure_fails_closed_and_is_never_zero(tmp_path, state, why):
    """A ceiling fed an unknown figure is not a ceiling. None, never D(0) --
    and D(0) would be the one value that silently admits a live order."""
    st = store(tmp_path, **state)
    assert st.realized_current_utc_day(now=TODAY_TS) is None, why


def test_the_daily_halt_still_restrains_a_loss_incurred_today():
    """THE PROPERTY THAT MAKES THIS A FIX AND NOT A BYPASS. Narrowing the
    WINDOW must not stop the halt firing on a real loss incurred today, by
    whoever incurred it: the population is still account-wide, so an
    unattested (manual or unknown) settlement today still trips it."""
    lim = E.SafetyLimits(
        max_usd_per_market=D("100"), max_abs_residual=D("100"),
        max_total_exposure_usd=D("100"), max_concurrent_markets=5,
        daily_realized_loss_halt_usd=D("5"))
    common = dict(price=D("0.50"), qty=D("1"), market="m",
                  committed_by_market={}, residual_after=D("0"),
                  open_markets=[], outstanding_by_market={},
                  live_order_count=0)
    assert lim.refuse(realized_today=D("-5.00"),
                      **common) == "DAILY_REALIZED_LOSS_HALT"
    assert lim.refuse(realized_today=D("-4.99"),
                      **common) != "DAILY_REALIZED_LOSS_HALT"


def test_a_loss_today_halts_through_the_real_executor_path(tmp_path):
    """End to end, through the store the gate actually reads: an unattested
    settlement today for -6 trips the $5 daily halt."""
    st = E.InventoryStore(str(tmp_path / "s.json"))
    st.state["realized_unattributed"] = "0"
    today = E.dt.datetime.now(E.dt.timezone.utc).strftime("%Y-%m-%d")
    st.state["retired_markets"] = {
        "manual-today": retired(today + "T00:30:00+00:00", "-6.00", False)}
    assert st.realized_current_utc_day() == D("-6.00")


# ------------- the production forensics behind DAILY_REALIZED_LOSS_HALT (§63)

#: Real `L5_LIVE_SETTLED` rows from l5-pmus-live, (retirement UTC, market,
#: settlement_pnl) as the venue settled them. EVERY ONE is 2026-10-16; the
#: last settlement of any kind was 2026-10-16T21:05:01Z, where the emitted
#: `realized_today_after` was exactly -8.39480000 -- the figure the daily
#: halt was refusing on. A query for L5_LIVE_SETTLED on 2026-10-17 returns
#: NOTHING, so the true daily figure for that day is zero.
SETTLED_20261016 = [
    ("2026-10-16T01:15:49+00:00", "cpcsol-sol-updown-15m-2026-10-16-0100z", "-1.73"),
    ("2026-10-16T01:30:27+00:00", "cpc-btc-updown-15m-2026-10-16-0115z", "-1.09"),
    ("2026-10-16T02:03:09+00:00", "cpc-btc-updown-15m-2026-10-16-0145z", "-3.39"),
    ("2026-10-16T03:48:49+00:00", "cpceth-eth-updown-15m-2026-10-16-0330z", "-50.00"),
    ("2026-10-16T03:48:49+00:00", "cpcxrp-xrp-updown-15m-2026-10-16-0330z", "-37.9754"),
    ("2026-10-16T03:48:51+00:00", "cpcsol-sol-updown-15m-2026-10-16-0330z", "-50.00"),
    ("2026-10-16T03:51:15+00:00", "cpc-btc-updown-15m-2026-10-16-0330z", "-2.00"),
    ("2026-10-16T12:46:35+00:00", "cpc-btc-updown-15m-2026-10-16-1230z", "-5.00"),
    ("2026-10-16T21:05:01+00:00", "cpc-btc-updown-1d-2026-10-15-2100z", "0"),
]

#: 2026-10-17T19:00:00Z -- the day the halt was refusing on, one day after
#: every settlement above.
TS_17 = 1792263600.0
#: 2026-10-16T23:00:00Z -- still inside the day those settlements landed on.
TS_16 = 1792191600.0


def _production_store(tmp_path):
    st = E.InventoryStore(str(tmp_path / "s.json"))
    st.state["realized_attributed"] = "-8.39480000"
    st.state["realized_unattributed"] = "0"
    st.state["retired_markets"] = {
        m: {"t": t, "settlement_pnl": p,
            "l5_owner_attribution_verified": False,
            "pnl_provenance": "UNKNOWN_COST_UPPER_BOUND"}
        for t, m, p in SETTLED_20261016}
    return st


def test_the_halt_was_reading_a_prior_days_losses(tmp_path):
    """THE PRODUCTION CAUSE. Every settlement behind -8.39480000 landed on
    2026-10-16; the last was 21:05:01Z. On 2026-10-17 nothing settled at all,
    so the day's realized P&L is zero -- yet the all-time total was still
    being fed to a gate named `daily`, which is why it refused."""
    st = _production_store(tmp_path)
    assert st.realized_today() == D("-8.39480000")       # all-time, unchanged
    assert st.realized_current_utc_day(now=TS_17) == D(0)


def test_the_same_fix_WOULD_have_halted_on_the_day_it_happened(tmp_path):
    """THE PROPERTY THAT MAKES THIS A BOUNDARY FIX AND NOT A BYPASS.

    Measured on 2026-10-16 itself, the day figure is a large loss and the $5
    halt FIRES. Measured on 2026-10-17 it is zero and the halt passes. The
    gate did not get weaker; it got the right day.
    """
    st = _production_store(tmp_path)
    day16 = st.realized_current_utc_day(now=TS_16)
    assert day16 < D("-5")
    lim = E.SafetyLimits(
        max_usd_per_market=D("5"), max_abs_residual=D("10"),
        max_total_exposure_usd=D("5"), max_concurrent_markets=1,
        daily_realized_loss_halt_usd=D("5"))
    common = dict(price=D("0.55"), qty=D("1"), market="m",
                  committed_by_market={}, residual_after=D("0"),
                  open_markets=[], outstanding_by_market={},
                  live_order_count=0)
    assert lim.refuse(realized_today=day16,
                      **common) == "DAILY_REALIZED_LOSS_HALT"
    assert lim.refuse(realized_today=st.realized_current_utc_day(now=TS_17),
                      **common) != "DAILY_REALIZED_LOSS_HALT"


def test_a_settlement_retired_late_counts_on_the_day_it_was_retired(tmp_path):
    """The boundary is the RETIREMENT timestamp, not the window's own date.
    `cpc-btc-updown-1d-2026-10-15-2100z` is a 10-15 window that settled on
    10-16, and it belongs to 10-16 -- the day the cash actually moved."""
    st = _production_store(tmp_path)
    row = st.state["retired_markets"]["cpc-btc-updown-1d-2026-10-15-2100z"]
    assert row["t"].startswith("2026-10-16")        # window 10-15, settled 10-16
    assert st.realized_current_utc_day(now=TS_16) == sum(
        (D(p) for _t, _m, p in SETTLED_20261016), D(0))
