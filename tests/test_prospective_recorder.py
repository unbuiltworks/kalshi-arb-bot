"""Regressions for the prospective L5 execution recorder.

Two of these pin mistakes that were made and caught during the build, because
both produced a recorder that looked healthy while recording nothing usable:

  * the first poll emitted L5's whole 500-fill backlog as "observations", every
    one with a null pre-fill book BY CONSTRUCTION -- we were not watching when
    they happened;
  * habitat tokens were absent from token_order, so the pruner sorted them to
    -1 and evicted them FIRST -- deleting exactly the coverage that makes a
    pre-fill book possible.
"""
import sys, os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import l5_prospective_recorder as R


# ------------------------------------------------------------- book primitives
BIDS = [{"price": "0.40", "size": "10"}, {"price": "0.41", "size": "5"},
        {"price": "0.39", "size": "7"}]
ASKS = [{"price": "0.44", "size": "3"}, {"price": "0.43", "size": "9"}]


def test_ladder_is_sorted_ascending_and_numeric():
    assert R.ladder(BIDS) == [(0.39, 7.0), (0.40, 10.0), (0.41, 5.0)]


def test_a_malformed_level_is_dropped_not_guessed():
    assert R.ladder([{"price": "x", "size": "1"}, {"size": "1"}]) == []


def test_touch_of_an_empty_side_is_none_not_zero():
    assert R.touch([], []) == (None, None, None, None)
    b, bs, a, asz = R.touch(R.ladder(BIDS), R.ladder(ASKS))
    assert (b, bs, a, asz) == (0.41, 5.0, 0.43, 9.0)


def test_depth_ahead_counts_better_prices_and_our_own_level():
    """Price priority: everything bid higher, plus what is already queued at
    the same price. An upper bound, since arrival order is invisible."""
    assert R.depth_ahead(R.ladder(BIDS), 0.40) == 15.0
    assert R.depth_ahead(R.ladder(BIDS), 0.41) == 5.0
    assert R.depth_ahead(R.ladder(BIDS), 0.42) == 0.0


def test_depth_at_is_the_level_only():
    assert R.depth_at(R.ladder(BIDS), 0.40) == 10.0
    assert R.depth_at(R.ladder(BIDS), 0.42) == 0.0


# ------------------------------------------------------------------- the ring
def test_pre_fill_snapshot_is_strictly_before_the_fill():
    """A snapshot taken AT the fill already contains the fill's own effect."""
    ring = R.BookRing()
    for t in (10.0, 20.0, 30.0):
        ring.add(t, {"_t": t})
    assert ring.before(20.0)["_t"] == 10.0
    assert ring.after(20.0)["_t"] == 30.0
    assert ring.before(5.0) is None


def test_the_ring_is_bounded():
    ring = R.BookRing(cap=3)
    for t in range(10):
        ring.add(float(t), {"_t": float(t)})
    assert len(ring.t) == 3 and ring.t[0] == 7.0


# --------------------------------------------------------- sequential state
def test_first_fill_is_open_then_extend_and_opposite():
    st = R.MarketState()
    assert st.classify(0) == "OPEN"
    st.apply(0, 1.0, "BUY", 100)
    assert st.classify(0) == "EXTEND" and st.classify(1) == "OPPOSITE"


def test_state_snapshot_is_taken_before_the_fill_is_applied():
    st = R.MarketState()
    st.apply(0, 5.0, "BUY", 100)
    snap = st.snapshot(0, 110)
    assert snap["inv_0_before"] == 5.0 and snap["run_before"] == 1
    assert snap["secs_since_prev_fill"] == 10
    st.apply(0, 5.0, "BUY", 110)
    assert st.inv[0] == 10.0


def test_run_resets_on_opposite_and_records_the_gap():
    st = R.MarketState()
    st.apply(0, 1.0, "BUY", 100)
    st.apply(0, 1.0, "BUY", 101)
    assert st.run == 2
    st.apply(1, 1.0, "BUY", 105)
    assert st.run == 1 and st.last_opposite_t == 105


# ------------------------------------------------------------- the event join
FILL = {"conditionId": "C", "asset": "T", "slug": "s", "outcome": "Up",
        "outcomeIndex": 0, "price": 0.40, "size": 2.0, "side": "BUY",
        "timestamp": 1000, "transactionHash": "0xabc"}


def _pre(t=990.0):
    return {"_t": t, "bids": BIDS, "asks": ASKS}


def test_event_reports_the_primary_ratio_and_its_inputs():
    prints = [{"t": 995.0, "price": 0.39, "size": 4.0, "outcomeIndex": 0},
              {"t": 996.0, "price": 0.95, "size": 99.0, "outcomeIndex": 1}]
    ev = R.build_event(FILL, is_taker=False, state=R.MarketState(),
                       pre=_pre(), post={"_t": 1005.0, "bids": [], "asks": []},
                       prints=prints, settle_epoch=1600.0)
    assert ev["depth_ahead_of_fill_price"] == 15.0
    # only the same-outcome print at or below 0.40 is executable against him
    assert ev["arriving_volume_since_pre_snapshot"] == 4.0
    assert ev["arriving_volume_whole_market"] == 103.0
    assert ev["arriving_flow_over_depth_ahead"] == pytest.approx(4.0 / 15.0)
    assert ev["secs_to_settlement"] == 600.0


def test_flow_from_the_other_outcome_is_not_counted_as_reaching_him():
    prints = [{"t": 995.0, "price": 0.10, "size": 50.0, "outcomeIndex": 1}]
    ev = R.build_event(FILL, is_taker=False, state=R.MarketState(), pre=_pre(),
                       post=None, prints=prints, settle_epoch=None)
    assert ev["arriving_volume_since_pre_snapshot"] == 0.0


def test_flow_priced_above_his_bid_could_not_have_filled_him():
    prints = [{"t": 995.0, "price": 0.41, "size": 50.0, "outcomeIndex": 0}]
    ev = R.build_event(FILL, is_taker=False, state=R.MarketState(), pre=_pre(),
                       post=None, prints=prints, settle_epoch=None)
    assert ev["arriving_volume_since_pre_snapshot"] == 0.0


def test_prints_before_the_pre_snapshot_are_outside_the_window():
    prints = [{"t": 980.0, "price": 0.39, "size": 4.0, "outcomeIndex": 0}]
    ev = R.build_event(FILL, is_taker=False, state=R.MarketState(), pre=_pre(990.0),
                       post=None, prints=prints, settle_epoch=None)
    assert ev["arriving_volume_since_pre_snapshot"] == 0.0


def test_a_missing_book_yields_null_with_a_reason_never_a_zero():
    ev = R.build_event(FILL, is_taker=None, state=R.MarketState(), pre=None,
                       post=None, prints=[], settle_epoch=None)
    assert ev["pre_fill_book"] is None
    assert ev["pre_fill_book_reason"]
    assert ev["depth_ahead_of_fill_price"] is None
    assert ev["arriving_flow_over_depth_ahead"] is None
    assert ev["arriving_flow_over_depth_ahead_reason"]
    assert ev["maker_taker_available"] is False


def test_zero_depth_ahead_is_undefined_not_infinite():
    ev = R.build_event(dict(FILL, price=0.42), is_taker=False,
                       state=R.MarketState(), pre=_pre(), post=None,
                       prints=[], settle_epoch=None)
    assert ev["depth_ahead_of_fill_price"] == 0.0
    assert ev["arriving_flow_over_depth_ahead"] is None
    assert "undefined" in ev["arriving_flow_over_depth_ahead_reason"]


def test_rest_position_relative_to_the_touch_is_recorded():
    ev = R.build_event(FILL, is_taker=False, state=R.MarketState(), pre=_pre(),
                       post=None, prints=[], settle_epoch=None)
    assert ev["touch_bid"] == 0.41
    assert ev["rested_at_touch"] is False
    assert ev["ticks_behind_touch"] == 1


def test_every_joined_source_carries_provenance_and_timing_uncertainty():
    ev = R.build_event(FILL, is_taker=False, state=R.MarketState(), pre=_pre(),
                       post={"_t": 1005.0, "bids": [], "asks": []},
                       prints=[], settle_epoch=None)
    for k in ("fill", "maker_taker", "pre_fill_book", "post_fill_book", "prints"):
        p = ev["provenance"][k]
        assert p["source"] and p["resolution_s"] is not None
    assert ev["provenance"]["fill"]["resolution_s"] == R.TRADE_TS_RESOLUTION_S
    assert ev["provenance"]["pre_fill_book"]["age_s"] == 10.0
    assert ev["timestamp_resolution_s"] == 1.0


# ---------------------------------------------------------------- discovery
def test_active_habitat_is_derived_from_the_slug_epoch_not_searched():
    """Gamma's date ordering returns markets 11+ hours out, so the live window
    cannot be found by querying; the slug carries its own window start."""
    slugs = R.active_habitat_slugs(now=1789956123.0)
    assert "btc-updown-15m-1789956000" in slugs
    assert "btc-updown-5m-1789956000" in slugs
    assert "btc-updown-15m-1789955100" in slugs          # previous window too
    assert len(slugs) == len(R.UPDOWN_COINS) * len(R.UPDOWN_PERIODS) * 2


def test_habitat_tokens_are_never_pruned():
    class Fake:
        tokens = {"h1": {"habitat": True}, "h2": {"habitat": True},
                  "x1": {}, "x2": {}, "x3": {}}
        books = {k: None for k in tokens}
        token_order = ["x1", "x2", "x3"]
    f = Fake()
    R.prune_tokens(f, keep=3)
    assert "h1" in f.tokens and "h2" in f.tokens
    assert len(f.tokens) == 3


def test_the_recorder_holds_no_order_path():
    """Checked against EXECUTABLE CODE, not prose.

    A raw substring scan fails twice over here: POST_FILL_GRACE_S contains
    "POST", and the module docstring's own disclaimer -- "no credential, no
    order transport, no signer" -- contains "signer". So identifiers and
    string literals are read out of the AST instead, and the request surface
    is checked structurally.
    """
    import ast
    tree = ast.parse(open("src/l5_prospective_recorder.py", encoding="utf-8").read())

    # 1. every urllib Request built here is a plain GET: no body, no method
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if fn == "Request":
                kw = {k.arg for k in node.keywords}
                assert "data" not in kw and "method" not in kw, ast.dump(node)[:160]

    # 2. no credential or order-verb identifier is referenced anywhere
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    # "post" is NOT on this list: it is the post-fill book parameter. Three
    # separate false positives here (POST_FILL_GRACE_S, the docstring's own
    # "no signer" disclaimer, and this) are why the check reads structure.
    for bad in ("api_key", "apiKey", "secret", "private_key", "sign",
                "place_order", "submit_order", "cancel_order", "signer"):
        assert bad not in names, bad

    # 3. no string literal names a mutating HTTP verb or an order endpoint
    lits = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
            and isinstance(n.value, str)}
    for lit in lits:
        assert lit.upper() not in ("POST", "PUT", "DELETE", "PATCH"), lit
        assert "/orders" not in lit, lit
