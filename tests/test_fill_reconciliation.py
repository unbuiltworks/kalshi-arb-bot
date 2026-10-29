"""The complete authoritative fill record, and the gate it enforces.

COHORT-2 finished with 56 fills that happened and 54 that were
reconstructable. `_drain_events` caps WebSocket frames per cycle, so
`order_events` is a SAMPLE. A live experiment must never end that way, and
the fix was not more WebSocket: the venue's own filled-order rows and trade
record were already being captured and nothing was reading them for
economics.

UNKNOWN STAYS UNKNOWN. `aggressor` is the venue's verdict; `maker-or-cancel`
is what we REQUESTED. A fill without an aggressor is UNKNOWN and is never
counted as maker, exactly as an absent fee is never counted as zero.
"""
import glob
import json

import pytest

from src.gemini_execution import fill_reconciliation as FR

C2 = {"GEMI-BTC15M2609221200-UP", "GEMI-BTC15M2609221215-UP",
      "GEMI-BTC15M2609221230-UP", "GEMI-BTC15M2609221245-UP",
      "GEMI-BTC15M2609221300-UP", "GEMI-BTC15M2609221315-UP",
      "GEMI-BTC15M2609221330-UP", "GEMI-BTC15M2609221345-UP",
      "GEMI-BTC15M2609221400-UP", "GEMI-BTC15M2609221415-UP",
      "GEMI-BTC15M2609221430-UP"}


def _win(n_fills=2, spend="0.90", yes="1", no="1", trades=None, orders=None,
         ws=None):
    orders = orders if orders is not None else [
        {"orderId": 1, "outcome": "yes", "price": "0.50",
         "timeInForce": "maker-or-cancel", "clientOrderId": "a"},
        {"orderId": 2, "outcome": "no", "price": "0.40",
         "timeInForce": "maker-or-cancel", "clientOrderId": "b"}]
    trades = trades if trades is not None else [
        {"tid": 11, "order_id": 1, "price": "0.50", "amount": "1",
         "aggressor": False, "fee_amount": "0", "fee_currency": "USD"},
        {"tid": 12, "order_id": 2, "price": "0.40", "amount": "1",
         "aggressor": False, "fee_amount": "0", "fee_currency": "USD"}]
    return {"window": {"symbol": "S", "inventory": {
                "n_fills": n_fills, "total_spend_usd": spend,
                "yes_qty": yes, "no_qty": no},
                "order_events": ws if ws is not None else []},
            "after": {"orders_seen": orders, "trades": trades}}


# ============ the happy path is all seven proofs

def test_a_complete_window_passes_every_proof():
    r = FR.reconcile_window(_win())
    assert r["verdict"] == FR.COMPLETE
    assert r["failed_proofs"] == []
    assert set(r["proofs"]) == {
        "count_complete", "no_double_count", "every_fill_priced_and_legged",
        "spend_reconciles", "qty_reconciles", "maker_resolved_for_every_fill",
        "fees_authoritative_for_every_fill"}
    assert all(r["proofs"].values())
    assert r["spend"]["authoritative_usd"] == "0.9"
    assert r["maker"]["all_maker"] is True


# ============ each proof actually fails when it should

def test_a_count_mismatch_is_INCOMPLETE():
    r = FR.reconcile_window(_win(n_fills=3))
    assert r["verdict"] == FR.INCOMPLETE
    assert "count_complete" in r["failed_proofs"]


def test_a_duplicate_tid_is_caught_as_double_counting():
    tr = [{"tid": 11, "order_id": 1, "price": "0.50", "amount": "1",
           "aggressor": False, "fee_amount": "0"},
          {"tid": 11, "order_id": 2, "price": "0.40", "amount": "1",
           "aggressor": False, "fee_amount": "0"}]
    r = FR.reconcile_window(_win(trades=tr))
    assert "no_double_count" in r["failed_proofs"]


def test_a_spend_that_does_not_reconcile_is_INCOMPLETE():
    r = FR.reconcile_window(_win(spend="9.99"))
    assert "spend_reconciles" in r["failed_proofs"]
    assert r["spend"]["agrees"] is False
    assert r["spend"]["authoritative_usd"] == "0.9"


def test_a_quantity_that_does_not_reconcile_is_INCOMPLETE():
    r = FR.reconcile_window(_win(yes="3"))
    assert "qty_reconciles" in r["failed_proofs"]


def test_an_unresolved_leg_is_INCOMPLETE():
    orders = [{"orderId": 1, "price": "0.50"},          # no outcome field
              {"orderId": 2, "outcome": "no", "price": "0.40"}]
    r = FR.reconcile_window(_win(orders=orders))
    assert "every_fill_priced_and_legged" in r["failed_proofs"]


# ============ UNKNOWN stays UNKNOWN

def test_a_fill_without_an_aggressor_is_UNKNOWN_never_maker():
    tr = [{"tid": 11, "order_id": 1, "price": "0.50", "amount": "1",
           "fee_amount": "0"},                            # no aggressor
          {"tid": 12, "order_id": 2, "price": "0.40", "amount": "1",
           "aggressor": False, "fee_amount": "0"}]
    r = FR.reconcile_window(_win(trades=tr))
    assert r["maker"]["unknown"] == 1 and r["maker"]["maker"] == 1
    assert r["maker"]["all_maker"] is False
    assert "maker_resolved_for_every_fill" in r["failed_proofs"]
    assert r["maker"]["unknown_tids"] == [11]


def test_post_only_being_REQUESTED_does_not_make_a_fill_maker():
    """`maker-or-cancel` is our request; `aggressor` is the venue's answer."""
    tr = [{"tid": 11, "order_id": 1, "price": "0.50", "amount": "1",
           "aggressor": True, "fee_amount": "0"},
          {"tid": 12, "order_id": 2, "price": "0.40", "amount": "1",
           "aggressor": False, "fee_amount": "0"}]
    r = FR.reconcile_window(_win(trades=tr))
    assert r["time_in_force"] == ["maker-or-cancel"], "we asked for post-only"
    assert r["maker"]["taker"] == 1 and r["maker"]["taker_tids"] == [11]
    assert r["maker"]["all_maker"] is False


def test_an_absent_fee_is_UNKNOWN_never_zero():
    tr = [{"tid": 11, "order_id": 1, "price": "0.50", "amount": "1",
           "aggressor": False},                           # no fee_amount
          {"tid": 12, "order_id": 2, "price": "0.40", "amount": "1",
           "aggressor": False, "fee_amount": "0.02"}]
    r = FR.reconcile_window(_win(trades=tr))
    assert r["fees"]["n_missing"] == 1 and r["fees"]["missing_tids"] == [11]
    assert r["fees"]["total_usd"] == "0.02", "the absent one is not summed as 0"
    assert "fees_authoritative_for_every_fill" in r["failed_proofs"]


# ============ unreadable is not empty

def test_an_unreadable_authoritative_read_is_not_an_empty_one():
    for after in (None, {}, {"orders_seen": None, "trades": []},
                  {"orders_seen": [], "trades": None}):
        r = FR.reconcile_window({"window": {"inventory": {}}, "after": after})
        assert r["verdict"] == FR.UNREADABLE, after
        assert r["fills"] is None


# ============ the WS stream is a latency signal, never the record

def test_the_WS_sample_can_be_short_and_the_window_still_COMPLETE():
    """This is the COHORT-2 case exactly: the capped stream missed a fill and
    the authoritative record did not."""
    ws = [{"X": "FILLED", "O": "YES", "L": "0.50", "Z": "1", "m": True}]
    r = FR.reconcile_window(_win(ws=ws))
    assert r["ws_n_fills"] == 1 and r["n_authoritative_fills"] == 2
    assert r["ws_missing"] == 1
    assert r["verdict"] == FR.COMPLETE, (
        "a short WS sample must not block a complete authoritative record")
    assert "CAPPED sample" in r["ws_note"]


# ============ the real COHORT-2 evidence

def _c2():
    reps = []
    for f in sorted(glob.glob("analysis/gemini/cohort_window_*.json")):
        d = json.loads(open(f).read())
        if (d.get("window") or {}).get("symbol") in C2:
            reps.append(d)
    return reps


def test_COHORT2_reconciles_COMPLETE_on_every_window():
    reps = _c2()
    if not reps:
        pytest.skip("COHORT-2 artifacts are not on this branch")
    assert len(reps) == 11
    out = FR.reconcile_all(reps)
    assert out["verdict"] == FR.COMPLETE
    assert out["n_complete"] == 11
    assert [p["failed_proofs"] for p in out["windows"]] == [[]] * 11


def test_COHORT2_the_two_fills_the_WS_stream_lost_are_recovered():
    reps = _c2()
    if not reps:
        pytest.skip("COHORT-2 artifacts are not on this branch")
    out = FR.reconcile_all(reps)
    assert out["fills"]["controller"] == 56
    assert out["fills"]["authoritative"] == 56, "the record is complete"
    assert out["fills"]["ws_recorded"] == 54
    assert out["fills"]["ws_missing"] == 2
    # and the two that the capped stream lost are NOT unverifiable after all
    assert out["maker"]["maker"] == 56
    assert out["maker"]["taker"] == 0 and out["maker"]["unknown"] == 0
    assert out["gross_spend_usd"] == "19.96"


def test_COHORT2_fees_are_zero_on_a_SECOND_independent_venue_surface():
    """The WS `n` field said 0.0000; `after.trades.fee_amount` is a different
    endpoint and says the same. Two surfaces, one answer."""
    reps = _c2()
    if not reps:
        pytest.skip("COHORT-2 artifacts are not on this branch")
    out = FR.reconcile_all(reps)
    assert out["fees_usd"] == "0"
    assert all((p["fees"]["n_missing"] == 0) for p in out["windows"])


def test_the_module_is_offline_and_read_only():
    import ast
    src = ast.parse(open("src/gemini_execution/fill_reconciliation.py").read())
    imported = {a.name.split(".")[0]
                for n in ast.walk(src) if isinstance(n, ast.Import)
                for a in n.names}
    imported |= {(n.module or "").split(".")[0]
                 for n in ast.walk(src) if isinstance(n, ast.ImportFrom)}
    assert imported <= {"decimal", "fractions", "typing", "__future__"}, imported
