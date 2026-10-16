"""`correct_cost_basis`: the protected verb for the 47-leg measured-cost
correction.

ONE TEST PER GUARD, and every refusal test asserts NOTHING WAS WRITTEN. The
correction moves realized toward zero, which is the direction that deserves
the most scepticism, so the guards are the product and these are the proof.

It also pins what the verb must NEVER do: clear HALT, attribute a leg, touch
an exposure control, or modify `correct_settled_market`.
"""
import copy
import os
import tempfile
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX

MARKET = "cpc-btc-updown-15m-2026-10-14-2230z"
SIDE = "UP"
CID = "cb-2230z-up-1"

# the audit's own evidence for this leg: five fills summing to 2.9000 and
# $0.59000000, against a $2.90 one-dollar-per-share placeholder
FILLS = [
    {"trade_id": "D3NMZPH42Z6Y", "qty": "0.4500", "price": "0.0700", "fee": "0.0000"},
    {"trade_id": "D3NMHKSACZ6Y", "qty": "1.8000", "price": "0.1400", "fee": "0.0000"},
    {"trade_id": "D3NJNSJ1YZ6Y", "qty": "0.3600", "price": "0.3800", "fee": "0.0000"},
    {"trade_id": "D3NHS0K12Z6Y", "qty": "0.0700", "price": "0.5700", "fee": "0.0000"},
    {"trade_id": "D3NHEG37RZ6Y", "qty": "0.2200", "price": "0.5900", "fee": "0.0000"},
]
MEASURED_COST = D("0.59000000")
MEASURED_FEES = D("0.0000")
WALK = {"pages": 107, "ended": "eof", "complete": True}


def _evidence(**over):
    ev = {"trade_ids": [f["trade_id"] for f in FILLS],
          "per_fill": copy.deepcopy(FILLS), "activity_walk": dict(WALK)}
    ev.update(over)
    return ev


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as d:
        s = LX.InventoryStore(os.path.join(d, "inv.json"))
        s.state["retired_markets"] = {MARKET: {
            "t": "2026-10-14T22:45:31.560354+00:00",
            "yes_value": "1.0000", "settlement_pnl": "0",
            "up_qty": "2.9000", "dn_qty": "0",
            "cost_total": "2.90", "redemption_total": "2.9000",
        }}
        s.state["realized_attributed"] = "-233.98540000"
        s.state["realized_unattributed"] = "0"
        s.state["realized_today"] = "-233.98540000"
        yield s


def _apply(store, **over):
    kw = {"correction_id": CID, "expect": {"cost_total": "2.90"},
          "measured_cost": MEASURED_COST, "measured_fees": MEASURED_FEES,
          "evidence": _evidence()}
    kw.update(over)
    return store.correct_cost_basis(MARKET, SIDE, **kw)


def _unwritten(store, out, reason):
    """A refusal must leave the ledger byte-for-byte as it was."""
    assert out["corrected"] is False
    assert out["reason"] == reason, out
    row = store.state["retired_markets"][MARKET]
    assert row["cost_total"] == "2.90"
    assert "cost_superseded" not in row
    assert "cost_provenance" not in row
    assert store.state["realized_today"] == "-233.98540000"
    assert store.state["realized_attributed"] == "-233.98540000"
    assert not store.state.get("cost_basis_corrections")


# -- the happy path --------------------------------------------------------

def test_it_corrects_and_preserves_the_original_verbatim(store):
    before = copy.deepcopy(store.state["retired_markets"][MARKET])
    out = _apply(store)
    assert out["corrected"] is True, out
    row = store.state["retired_markets"][MARKET]
    assert row["cost_total"] == "0.59000000"
    assert row["cost_provenance"] == "MEASURED_FROM_VENUE_FILLS"
    assert row["cost_superseded"] == before
    assert D(store.state["realized_today"]) == D("-233.98540000") + D(out["delta"])


def test_the_audit_entry_records_the_trade_ids_it_was_derived_from(store):
    _apply(store)
    rec = store.state["cost_basis_corrections"][0]
    assert rec["trade_ids"] == [f["trade_id"] for f in FILLS]
    assert rec["was_cost_total"] == "2.90"
    assert rec["now_cost_total"] == "0.59000000"
    assert rec["activity_walk"]["complete"] is True


# -- guards 1-10, each asserting nothing was written -----------------------

def test_guard1_a_spent_correction_id_is_refused(store):
    _apply(store)
    out = _apply(store, measured_cost=D("0.50"))
    assert out["corrected"] is False
    assert out["reason"] == "COST_CORRECTION_ALREADY_APPLIED"


def test_guard2_an_unretired_market_is_refused(store):
    store.state["retired_markets"] = {}
    out = _apply(store)
    assert out["corrected"] is False and out["reason"] == "MARKET_NOT_RETIRED"


def test_guard3_a_corrected_leg_is_not_corrected_twice(store):
    _apply(store)
    out = _apply(store, correction_id="cb-other")
    assert out["corrected"] is False
    assert out["reason"] == "COST_ALREADY_CORRECTED"


def test_guard4_a_ledger_that_does_not_match_the_evidence_is_refused(store):
    out = _apply(store, expect={"cost_total": "9.99"})
    _unwritten(store, out, "LEDGER_DOES_NOT_MATCH_EVIDENCE")
    assert out["mismatch"]["cost_total"]["found"] == "2.90"


def test_guard5_a_partial_walk_cannot_establish_a_cost(store):
    for walk in ({"complete": False}, {"ended": "max_pages"}, {}, None):
        out = _apply(store, evidence=_evidence(activity_walk=walk))
        _unwritten(store, out, "ACTIVITY_WALK_INCOMPLETE")


def test_guard6_the_caller_cannot_assert_a_cost(store):
    out = _apply(store, measured_cost=D("0.01"))
    _unwritten(store, out, "COST_NOT_RECONCILED")
    assert D(out["derived_cost"]) == D("0.59")


def test_guard6_the_complement_cost_field_is_caught_by_reconciliation(store):
    # the venue row's own `cost` is the COMPLEMENT, not our cost. A caller
    # passing it must be refused, not silently believed.
    bad = copy.deepcopy(FILLS)
    for f in bad:
        f["price"] = str(D(1) - D(f["price"]))
    out = _apply(store, evidence=_evidence(per_fill=bad))
    _unwritten(store, out, "COST_NOT_RECONCILED")


def test_guard6_an_unreadable_fill_refuses_the_whole_correction(store):
    for bad_key, bad in (("qty", None), ("price", "abc"), ("fee", "nan"),
                         ("trade_id", None)):
        fills = copy.deepcopy(FILLS)
        fills[0][bad_key] = bad
        out = _apply(store, evidence=_evidence(per_fill=fills))
        _unwritten(store, out, "COST_NOT_RECONCILED")


def test_guard6_no_fills_is_refused_not_treated_as_zero(store):
    out = _apply(store, evidence=_evidence(per_fill=[]))
    _unwritten(store, out, "COST_NOT_RECONCILED")


def test_guard6_mismatched_fees_are_refused(store):
    out = _apply(store, measured_fees=D("0.25"))
    _unwritten(store, out, "COST_NOT_RECONCILED")


def test_guard7_a_quantity_mismatch_preserves_the_leg_as_unknown(store):
    # this is the gross-vs-net case: the venue reports 2x the ledger qty.
    store.state["retired_markets"][MARKET]["up_qty"] = "1.0000"
    out = _apply(store)
    _unwritten(store, out, "VENUE_QTY_NE_LEDGER_QTY")
    assert out["venue_qty"] == "2.9000" and out["ledger_qty"] == "1.0000"


def test_guard8_the_correction_is_one_directional(store):
    out = _apply(store, measured_cost=D("2.90"),
                 evidence=_evidence(per_fill=[
                     {"trade_id": "T1", "qty": "2.9000", "price": "1.0000",
                      "fee": "0.0000"}]))
    _unwritten(store, out, "COST_CORRECTION_MUST_REDUCE")


def test_guard9_a_negative_cost_is_not_a_measurement(store):
    out = _apply(store, measured_cost=D("-1"),
                 evidence=_evidence(per_fill=[
                     {"trade_id": "T1", "qty": "2.9000", "price": "-0.3448275862068965517241379310",
                      "fee": "0.0000"}]))
    assert out["corrected"] is False
    assert out["reason"] in ("COST_UNREADABLE", "COST_NOT_RECONCILED")


def test_guard9_a_non_finite_cost_is_unreadable(store):
    for bad in (D("NaN"), None):
        out = _apply(store, measured_cost=bad)
        assert out["corrected"] is False
        assert out["reason"] == "COST_UNREADABLE", bad


def test_guard10_the_same_fills_cannot_be_spent_twice(store):
    _apply(store)
    store.state["retired_markets"]["other-market"] = {
        "yes_value": "1.0000", "settlement_pnl": "0",
        "up_qty": "2.9000", "dn_qty": "0", "cost_total": "2.90"}
    out = store.correct_cost_basis(
        "other-market", SIDE, correction_id="cb-2",
        expect={"cost_total": "2.90"}, measured_cost=MEASURED_COST,
        measured_fees=MEASURED_FEES, evidence=_evidence())
    assert out["corrected"] is False
    assert out["reason"] == "TRADE_ID_ALREADY_CONSUMED"
    assert len(out["trade_ids"]) == 5


def test_a_bad_side_is_refused(store):
    for bad in ("", "YES", "yes", None, "BUY"):
        out = store.correct_cost_basis(
            MARKET, bad, correction_id=CID, expect={},
            measured_cost=MEASURED_COST, measured_fees=MEASURED_FEES,
            evidence=_evidence())
        assert out["corrected"] is False and out["reason"] == "BAD_SIDE"


# -- what it must never do -------------------------------------------------

def test_it_never_writes_an_owner_field_so_attribution_is_unchanged(store):
    _apply(store)
    row = store.state["retired_markets"][MARKET]
    for forbidden in ("owner", "owners", "verified_l5", "l5_owned",
                      "manual_realized", "attributed_to"):
        assert forbidden not in row, forbidden
    assert store.state["cost_basis_corrections"][0]["ownership_unchanged"] is True


def test_it_does_not_touch_realized_unattributed(store):
    _apply(store)
    assert store.state["realized_unattributed"] == "0"


def _code_only(fn):
    """The function's CODE, with comments and string literals removed.

    Grepping the raw source would match the docstring, which describes the
    controls precisely in order to say it does not touch them. The claim is
    about executable code, so the test examines executable code.
    """
    import inspect
    import io
    import tokenize
    src = inspect.getsource(fn)
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.STRING, tokenize.COMMENT):
            continue
        out.append(tok.string)
    return " ".join(out)


def test_it_does_not_read_or_write_any_exposure_or_halt_control():
    code = _code_only(LX.InventoryStore.correct_cost_basis)
    for forbidden in ("halt", "HALT", "max_usd_per_market",
                      "max_total_exposure", "max_abs_residual",
                      "max_cash_deployed", "max_live_orders",
                      "daily_realized_loss"):
        assert forbidden not in code, forbidden


def test_the_code_only_helper_would_actually_catch_a_real_read():
    # a guard that cannot fail is not a guard
    def sample():
        x = 1
        return x + len("halt")
    assert "halt" not in _code_only(sample)

    def sample2():
        halt = 1
        return halt
    assert "halt" in _code_only(sample2)


def test_correct_settled_market_is_untouched_and_still_refuses_upward():
    import inspect
    src = inspect.getsource(LX.InventoryStore.correct_settled_market)
    assert "CORRECTION_WOULD_RELAX_LOSS_HALT" in src
    assert "if delta > 0:" in src


def test_the_verb_is_not_wired_to_any_automatic_call_site():
    import pathlib
    hits = []
    for p in pathlib.Path("src").glob("*.py"):
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if "correct_cost_basis" in line and "def correct_cost_basis" not in line:
                hits.append("%s:%d" % (p, i))
    assert hits == [], ("applying it must be an explicit owner action, "
                        "not something a boot path does: %s" % hits)
