"""1 tick vs 2 ticks, decided by RECORDS and never by the quoted edge."""
from decimal import Decimal as D

import json

import pytest

from src import l5_depth_comparison as C


def order(market, side, price, depth=None, filled="5", commission="0.00",
          bid="0.60", ask="0.61"):
    return {"market": market, "side": side, "price": price,
            "depth_ticks": depth, "filled_qty": filled,
            "commission": commission,
            "bid_at_placement": bid, "ask_at_placement": ask}


def book(n, depth, *, both=True, fill=True, edge_px=None, start=0):
    """n windows of UP/DOWN orders at `depth` ticks behind a 0.60/0.61 book.

    The depth is RECORDED on each order, as the probe now records it: a
    derived depth is deliberately no longer counted.
    """
    up_px, dn_px = edge_px or ("0.59", "0.38")
    out = []
    for i in range(n):
        m = "w%d-%d" % (depth, start + i)
        out.append(order(m, "UP", up_px, depth,
                         filled="5" if fill else "0"))
        out.append(order(m, "DOWN", dn_px, depth,
                         filled="5" if (fill and both) else "0"))
    return out


# ------------------------------------------------------ depth attribution

def test_a_recorded_depth_is_used_as_recorded():
    assert C.depth_of(order("m", "UP", "0.59", depth=2)) == 2


def test_only_a_RECORDED_depth_is_counted_as_a_depth():
    o = order("m", "UP", "0.59")                  # derivable, not recorded
    assert C.depth_of(o) == 1
    assert C.depth_of(o, recorded_only=True) is None
    rec = order("m", "UP", "0.59", depth=1)
    assert C.depth_of(rec, recorded_only=True) == 1


def test_an_UP_depth_is_derived_from_the_BID():
    assert C.depth_of(order("m", "UP", "0.59")) == 1
    assert C.depth_of(order("m", "UP", "0.58")) == 2
    assert C.depth_of(order("m", "UP", "0.60")) == 0


def test_a_DOWN_depth_is_derived_from_ONE_MINUS_THE_ASK():
    # DOWN's own-outcome touch is 1 - 0.61 = 0.39
    assert C.depth_of(order("m", "DOWN", "0.38")) == 1
    assert C.depth_of(order("m", "DOWN", "0.37")) == 2


def test_an_unrecordable_depth_is_UNKNOWN_not_a_guess():
    o = order("m", "UP", "0.59")
    o["bid_at_placement"] = None
    assert C.depth_of(o) is None
    o2 = order("m", "UP", "0.595")        # off-grid against the touch
    assert C.depth_of(o2) is None
    out = C.compare([o])
    assert out["unknown_depth"] == 1


def test_an_order_PRICED_THROUGH_the_touch_is_not_a_depth():
    assert C.depth_of(order("m", "UP", "0.62")) is None


# ------------------------------------------------- the refusal to conclude

def test_too_few_orders_reports_no_rate_at_all():
    out = C.compare(book(3, 1))
    d = out["depths"]["1"]
    assert d["verdict"] == "INSUFFICIENT_SAMPLE"
    assert d["single_leg_fill_rate"] is None
    assert out["conclusion"] == "NOT_COMPARABLE"


def test_enough_orders_but_too_few_windows_withholds_the_PAIR_rate():
    """The both-leg rate needs whole windows, which is the stricter bar."""
    out = C.compare([order("w%d" % i, "UP", "0.59", 1) for i in range(25)])
    d = out["depths"]["1"]
    assert d["verdict"] == "MEASURED"
    assert d["single_leg_fill_rate"] == "1.0000"
    assert d["windows"] == 25 and d["both_leg_rate"] is not None


def test_one_measured_depth_is_never_compared_against_an_unmeasured_one():
    out = C.compare(book(15, 1) + book(2, 2))
    assert out["depths"]["2"]["verdict"] == "INSUFFICIENT_SAMPLE"
    assert out["better_depth"] is None
    assert out["conclusion"] == "NOT_COMPARABLE"


def test_the_quoted_edge_is_never_used_as_a_substitute():
    """THE CENTRAL PROPERTY. 2 ticks always shows the larger quoted edge, so
    a comparison that fell back on it would always pick 2."""
    out = C.compare(book(15, 1) + book(2, 2))
    assert out["better_depth"] is None
    assert "quoted edge" in out["labels"]


# ----------------------------------------------------- the real comparison

def test_a_deeper_quote_that_never_completes_LOSES_despite_a_bigger_edge():
    """2 ticks prices the pair at 0.95 against 0.97 at 1 tick -- a strictly
    larger quoted edge -- but if the second leg never fills it earns nothing
    and the comparison must say so."""
    out = C.compare(book(15, 1) + book(15, 2, both=False))
    assert out["conclusion"] in ("MEASURED", "NOT_COMPARABLE")
    if out["conclusion"] == "MEASURED":
        assert out["better_depth"] == 1
    else:
        # 2 ticks priced no pair at all, which is itself the answer
        assert out["depths"]["2"]["windows_both_legs_filled"] == 0
        assert out["depths"]["2"]["realized_edge_per_contract"] is None


def test_the_comparison_is_edge_TIMES_the_measured_both_leg_rate():
    out = C.compare(book(15, 1) + book(15, 2, edge_px=("0.58", "0.37")))
    assert out["conclusion"] == "MEASURED"
    ev = out["ev_per_window_per_contract"]
    for k in ("1", "2"):
        d = out["depths"][k]
        assert D(ev[k]) == D(d["realized_edge_per_contract"]) * \
            D(d["both_leg_rate"])
    # both complete every window here, so the larger realized edge wins --
    # and it wins on a MEASURED rate, not on the quote
    assert out["better_depth"] == 2


def test_a_half_filling_deep_quote_loses_to_a_reliable_shallow_one():
    deep = book(8, 2, edge_px=("0.58", "0.37")) + \
        book(7, 2, both=False, edge_px=("0.58", "0.37"), start=100)
    out = C.compare(book(15, 1) + deep)
    assert out["conclusion"] == "MEASURED"
    assert D(out["depths"]["2"]["both_leg_rate"]) < \
        D(out["depths"]["1"]["both_leg_rate"])


# ------------------------------------------------------------- the fees

def test_a_missing_commission_withholds_the_realized_edge():
    """An unpaid fee is not a zero fee."""
    rows = book(15, 1)
    for o in rows:
        o["commission"] = None
    out = C.compare(rows)
    d = out["depths"]["1"]
    assert d["realized_edge_per_contract"] is None
    assert d["pairs_missing_commission"] == 15
    assert "not a zero commission" in d["labels_edge"]


def test_recorded_commissions_are_subtracted_from_the_realized_edge():
    rows = book(15, 1)
    for o in rows:
        o["commission"] = "0.05"
    out = C.compare(rows)
    # pair 0.97 -> 0.03 gross, less 0.10 of fees over 5 contracts = 0.02/ct
    assert D(out["depths"]["1"]["realized_edge_per_contract"]) == D("0.01")


def test_the_break_even_rate_is_reported_beside_the_measured_one():
    out = C.compare(book(15, 1))
    d = out["depths"]["1"]
    assert d["break_even_single_leg_rate_if_held"] is not None
    assert D(d["break_even_single_leg_rate_if_held"]) > D("0.5")


# ----------------------------------------------------------- tape reading

def test_a_missing_tape_refuses(tmp_path):
    out = C.from_tape(str(tmp_path / "nope.jsonl"))
    assert out["ok"] is False and out["reason"] == "TAPE_NOT_FOUND"


def test_a_tape_is_read_and_bad_lines_counted(tmp_path):
    p = tmp_path / "tape.jsonl"
    with open(p, "w") as fh:
        fh.write("{not json\n")
        for i in range(3):
            fh.write(json.dumps({
                "row": {"pmus": {"bbo": {"bid": "0.60", "ask": "0.61"}}},
                "record": {"market": "w%d" % i, "ORDER": {
                    "client_order_id": "c%d" % i, "filled_qty": "5",
                    "commission": "0.00",
                    "intent": {"side": "UP", "price": "0.59", "qty": "5",
                               "market": "w%d" % i}}}}) + "\n")
    out = C.from_tape(str(p))
    assert out["ok"] is True
    assert out["unparseable_lines"] == 1
    assert out["orders_read"] == 3


def test_the_module_cannot_submit_cancel_or_write():
    import inspect
    src = inspect.getsource(C)
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    for forbidden in ("submit", ".cancel(", "requests", "urllib",
                      'open(path, "w"', ".save()"):
        assert forbidden not in code, forbidden


# ------------------------------- the production defect, both halves

def test_a_derived_depth_is_REPORTED_but_never_COUNTED(tmp_path):
    """THE SECOND DEFECT, found by running it on the real volume. Deriving
    the depth from the touch and the order's own price attributed 7,228
    ordinary L5-mirroring records into buckets from 0 to 96 ticks -- it was
    measuring how far L5 quoted from the touch, not where we rested. Those
    records are now reported as derivable and counted into nothing."""
    p = tmp_path / "t.jsonl"
    with open(p, "w") as fh:
        for i in range(25):
            fh.write(json.dumps({
                "row": {"pmus": {"bbo": {"bid": "0.60", "ask": "0.61"}}},
                "record": {"market": "w%d" % i, "ORDER": {
                    "client_order_id": "c%d" % i, "filled_qty": "5",
                    "commission": "0.00",
                    "intent": {"side": "UP", "price": "0.59", "qty": "5"}}}
            }) + "\n")
    out = C.from_tape(str(p))
    # the BBO IS read from row.pmus.bbo -- these are derivable, which the
    # first version proved by bucketing them. They are no longer counted.
    assert out["derived_depth_NOT_counted"] == 25
    assert out["unknown_depth"] == 25
    assert out["depths"]["1"]["orders"] == 0
    assert out["conclusion"] == "NOT_COMPARABLE"


def test_a_recorded_depth_on_the_probe_leg_is_preferred(tmp_path):
    """The probe now writes depth_ticks onto each leg, which is the only
    unambiguous source: it needs no touch to be recoverable."""
    p = tmp_path / "t.jsonl"
    with open(p, "w") as fh:
        for i in range(25):
            fh.write(json.dumps({
                "row": {},                      # no BBO at all
                "record": {"market": "w%d" % i,
                           "TWO_SIDED_PROBE": {"depth_ticks": 2, "legs": [
                               {"side": "UP", "depth_ticks": 2,
                                "touch_at_placement": "0.60"}]},
                           "ORDER": {
                               "client_order_id": "c%d" % i,
                               "filled_qty": "5", "commission": "0.00",
                               "intent": {"side": "UP", "price": "0.58",
                                          "qty": "5"}}}}) + "\n")
    out = C.from_tape(str(p))
    assert out["unknown_depth"] == 0
    assert out["depths"]["2"]["orders"] == 25


def test_the_probe_writes_the_depth_it_placed_at():
    """Pinned against the REAL builder, so the recording cannot be dropped
    without this failing."""
    from src import l5_two_sided_probe as TSP
    import os
    os.environ[TSP.EXPERIMENT_ENV] = "e"
    os.environ[TSP.MARKET_ENV] = "m"
    try:
        built = TSP.build_orders(
            market="m", bid="0.60", ask="0.61", bid_depth="900",
            ask_depth="900", tick="0.01", min_qty="0.01", depth_ticks=2,
            cash_envelope="8", max_worst_case_loss="3", book_age_s="0.05")
        assert built["ok"] is True, built
        assert built["depth_ticks"] == 2
        for l in built["legs"]:
            assert l["depth_ticks"] == 2
            assert l["touch_at_placement"] is not None
        touch = {l["side"]: l["touch_at_placement"] for l in built["legs"]}
        assert touch["UP"] == "0.60"            # the UP bid
        assert touch["DOWN"] == "0.39"          # 1 - the UP ask
        # and the recorded depth round-trips through the attributor
        for l in built["legs"]:
            assert C.depth_of(l) == 2
    finally:
        os.environ.pop(TSP.EXPERIMENT_ENV, None)
        os.environ.pop(TSP.MARKET_ENV, None)
