"""The lane x isAggressor fee cross-tab.

The classifier must come from the venue's own flag and NEVER from the fee,
lanes must never pool, and UNKNOWN must never be folded into MAKER.
"""
from decimal import Decimal as D

import src.l5_cost_basis_audit as A


def _row(slug, *, is_aggressor=None, fee="0", qty="1", px="0.50",
         both_objects=True, kind="trade"):
    """A trade row shaped like the production feed: type + one payload."""
    execobj = {"lastShares": qty, "lastPx": px,
               "commissionNotionalCollected": fee}
    body = {"marketSlug": slug, "qty": qty, "price": px}
    if is_aggressor is not None:
        body["isAggressor"] = is_aggressor
    if both_objects:
        body["aggressorExecution"] = dict(execobj)
        body["passiveExecution"] = dict(execobj)
    else:
        body["passiveExecution"] = dict(execobj)
    return {"type": kind, kind: body}


def _bucket(out, lane, cls):
    for b in out["buckets"]:
        if b["lane"] == lane and b["aggressor"] == cls:
            return b
    return None


# ------------------------------------------------ the classifier

def test_classification_comes_from_isAggressor_not_from_the_fee():
    """A ZERO-fee row flagged as the aggressor is TAKER, and a CHARGED row
    flagged passive is MAKER. The fee must not move the label."""
    out = A.fee_crosstab([
        _row("cpc-btc-updown-15m-2026-10-16-0100z", is_aggressor=True,
             fee="0"),
        _row("cpc-btc-updown-15m-2026-10-16-0115z", is_aggressor=False,
             fee="0.05"),
    ])
    assert _bucket(out, "btc15m", "TAKER")["fills"] == 1
    assert _bucket(out, "btc15m", "MAKER")["fills"] == 1
    assert _bucket(out, "btc15m", "MAKER")["charged_usd"] == "0.05"


def test_an_absent_flag_is_UNKNOWN_and_is_never_folded():
    out = A.fee_crosstab([
        _row("cpc-btc-updown-15m-2026-10-16-0100z", is_aggressor=None),
    ])
    assert _bucket(out, "btc15m", "UNKNOWN")["fills"] == 1
    assert _bucket(out, "btc15m", "MAKER") is None
    assert _bucket(out, "btc15m", "TAKER") is None


def test_a_single_execution_object_is_unambiguous():
    """Only passiveExecution present means we were the passive party."""
    out = A.fee_crosstab([
        _row("cpc-btc-updown-15m-2026-10-16-0100z", is_aggressor=None,
             both_objects=False),
    ])
    assert _bucket(out, "btc15m", "MAKER")["fills"] == 1


# ------------------------------------------------ lanes

def test_lanes_do_not_pool_and_sports_stays_separate():
    out = A.fee_crosstab([
        _row("cpc-btc-updown-15m-2026-10-16-0100z", is_aggressor=False,
             fee="0.03"),
        _row("tsc-cfb-lou-miss-2026-09-06-total-41pt5", is_aggressor=False,
             fee="-0.01"),
    ])
    btc = _bucket(out, "btc15m", "MAKER")
    spt = _bucket(out, "sports", "MAKER")
    assert btc["charged_usd"] == "0.03" and btc["rebated_usd"] == "0"
    assert spt["rebated_usd"] == "-0.01" and spt["charged_usd"] == "0"
    # and the btc lane's rate is NOT improved by the sports credit
    assert D(btc["effective_fee_rate_pct"]) > 0
    assert D(spt["effective_fee_rate_pct"]) < 0


def test_each_crypto_series_is_its_own_lane():
    out = A.fee_crosstab([
        _row("cpc-btc-updown-15m-x", is_aggressor=True),
        _row("cpceth-eth-updown-15m-x", is_aggressor=True),
        _row("cpcsol-sol-updown-15m-x", is_aggressor=True),
        _row("cpcxrp-xrp-updown-15m-x", is_aggressor=True),
        _row("cpc-btc-updown-1d-x", is_aggressor=True),
    ])
    for lane in ("btc15m", "eth15m", "sol15m", "xrp15m", "btc1d"):
        assert _bucket(out, lane, "TAKER")["fills"] == 1, lane


def test_an_unrecognised_slug_is_other_not_a_neighbour():
    out = A.fee_crosstab([_row("totally-new-series-2027", is_aggressor=True)])
    assert _bucket(out, "other", "TAKER")["fills"] == 1


# ------------------------------------------------ the money

def test_charges_and_rebates_are_reported_separately_and_netted():
    """A bucket that nets to zero from offsetting flows is not the same
    object as one that was never charged, so both are reported."""
    slug = "cpc-btc-updown-15m-2026-10-16-0100z"
    out = A.fee_crosstab([
        _row(slug, is_aggressor=False, fee="0.04"),
        _row(slug, is_aggressor=False, fee="-0.04"),
    ])
    b = _bucket(out, "btc15m", "MAKER")
    assert b["commission_usd"] == "0.00"
    assert b["charged_usd"] == "0.04" and b["rebated_usd"] == "-0.04"
    assert b["n_charged"] == 1 and b["n_rebated"] == 1 and b["n_zero"] == 0


def test_the_effective_rate_keeps_the_venues_sign():
    """Negative means the bucket was PAID; the sign is never flipped."""
    slug = "tsc-cfb-x"
    out = A.fee_crosstab([_row(slug, is_aggressor=False, fee="-0.01",
                               qty="2", px="0.50")])
    b = _bucket(out, "sports", "MAKER")
    assert b["notional_usd"] == "1.00"
    assert D(b["effective_fee_rate_pct"]) == D("-1.0000")


def test_quantity_and_notional_accumulate_from_our_execution():
    slug = "cpc-btc-updown-15m-2026-10-16-0100z"
    out = A.fee_crosstab([
        _row(slug, is_aggressor=True, qty="1.27", px="0.7700", fee="0.02"),
        _row(slug, is_aggressor=True, qty="2.00", px="0.5000", fee="0.03"),
    ])
    b = _bucket(out, "btc15m", "TAKER")
    assert b["qty"] == "3.27"
    assert D(b["notional_usd"]) == D("1.27") * D("0.77") + D("1.00")


def test_non_fill_rows_are_ignored():
    out = A.fee_crosstab([
        {"type": "position_resolution",
         "position_resolution": {"marketSlug": "cpc-btc-updown-15m-x"}},
    ])
    assert out["buckets"] == []


def test_the_labels_forbid_transferring_sports_economics():
    assert "must NOT be transferred to btc15m" in out_labels()


def out_labels():
    return A.fee_crosstab([])["labels"]
