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
    """The account-wide gates still read account_wide until the protected
    rescoping is applied by a human. This is the mechanism, not the change."""
    import inspect
    src = inspect.getsource(E.InventoryStore.gate_input)
    assert "CHANGES NO POLICY" in src
    # and the three protected call sites still read realized_today
    whole = inspect.getsource(E)
    assert whole.count("cumulative_realized=self.store.realized_today()") == 2
    assert whole.count("realized_today=self.store.realized_today()") == 1
