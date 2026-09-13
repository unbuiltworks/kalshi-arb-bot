"""Regressions for the PMUS live-universe funnel audit.

The audit exists to answer "where do the 61 live events disappear". Its whole
value is that the buckets are exhaustive and the gates are production's, so
those are what these tests pin.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from decimal import Decimal as D
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "analysis/pmus/universe/audit_live_funnel.py"
spec = importlib.util.spec_from_file_location("audit_live_funnel", SRC)
A = importlib.util.module_from_spec(spec)
sys.modules["audit_live_funnel"] = A
spec.loader.exec_module(A)


def _m(**kw):
    base = {"slug": "aec-x-1", "status": "MARKET_STATUS_OPEN",
            "bestBidQuote": {"value": "0.50"}, "bestAskQuote": {"value": "0.52"},
            "orderPriceMinTickSize": 0.01, "minimumTradeQty": 1.0,
            "sportsMarketType": "moneyline", "feeCoefficient": 0.06,
            "endDate": "2026-09-27T17:00:00Z"}
    base.update(kw)
    return base


EV = {"slug": "e1", "live": True, "category": "sports", "period": "Q2",
      "eventState": {"type": "football"}}


# --- read-only ------------------------------------------------------------

def test_audit_cannot_reach_an_order_or_deploy_path():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for bad in ("place_order", "submit_order", "cancel_order", "redeploy",
                "create_deployment", "set_variables", "restart_service"):
        assert bad not in names, f"order/deploy verb in a read-only audit: {bad}"


def test_audit_never_reads_polymarket_com():
    """THE architecture rule: .com structural availability and PMUS executable
    availability must never be combined in one report."""
    txt = SRC.read_text(encoding="utf-8")
    assert "gamma-api" not in txt
    assert A.GATEWAY == "https://gateway.polymarket.us"


def test_every_row_carries_the_four_mandatory_provenance_fields():
    row = A.classify(_m(), EV, seats={"aec-x-1"})
    for k in ("VENUE", "EXECUTABLE_BY_CURRENT_ENGINE", "SOURCE",
              "SETTLEMENT_RULESET"):
        assert k in row, f"every market report must carry {k}"
    assert row["VENUE"] == "polymarket_us"


# --- the gates are production's, not re-guessed ---------------------------

def test_gates_match_the_modules_that_own_them():
    sess = (Path(__file__).resolve().parents[1]
            / "src/pmus_spring_l5_session.py").read_text(encoding="utf-8")
    entry = (Path(__file__).resolve().parents[1]
             / "src/pmus_spring_l5_probe_entry.py").read_text(encoding="utf-8")
    assert 'SPRING_BAND_LO, SPRING_BAND_HI = D("0.25"), D("0.75")' in sess
    assert "SPRING_MIN_TRADES_1H = 10" in sess
    assert 'SPRING_PAIR_TARGET = D("0.98")' in sess
    assert 'MIN_TOUCH_DEPTH = D("1")' in entry
    assert (A.BAND_LO, A.BAND_HI) == (D("0.25"), D("0.75"))
    assert A.MIN_TRADES_1H == 10
    assert A.PAIR_TARGET == D("0.98")
    assert A.MIN_TOUCH_DEPTH == D("1")


# --- exhaustive bucketing: no silent filters ------------------------------

def test_buckets_are_exhaustive_and_sum_to_the_market_count():
    evs = [{**EV, "markets": [
        _m(slug="a"), _m(slug="b", status="MARKET_STATUS_RESOLVED"),
        _m(slug="c", bestAskQuote=None), _m(slug="d", bestBidQuote={"value": "0.90"}),
        _m(slug="e", status="WEIRD")]}]
    rows = A.audit(evs, seats={"a"})
    assert len(rows) == 5
    assert sum(A.funnel(rows, 1)["buckets"].values()) == 5
    assert all(r["bucket"] in A.BUCKETS for r in rows)


def test_every_non_eligible_row_names_a_reason():
    evs = [{**EV, "markets": [_m(slug="a"), _m(slug="c", bestAskQuote=None)]}]
    for r in A.audit(evs, seats={"a"}):
        if r["bucket"] != "ELIGIBLE_NOW":
            assert r["FINAL_REJECTION_REASON"], "a dropped market must say why"


# --- the seat bucket, which is the point ----------------------------------

def test_a_qualified_market_without_a_seat_is_invisible_not_refused():
    """Admission needs trades_1h / two_sided_trades / off_distribution /
    bid_touch_qty, all recorder-seat-only. Without a seat the market never
    reaches a gate, so it never appears in a refusal counter -- which is how a
    coverage problem disguises itself as an economics problem."""
    row = A.classify(_m(slug="unseated"), EV, seats=set())
    assert row["bucket"] == "NO_RECORDER_SEAT"
    assert row["EXECUTABLE_BY_CURRENT_ENGINE"] is False
    assert "INVISIBLE" in row["FINAL_REJECTION_REASON"]


def test_a_seat_does_not_rescue_a_one_sided_or_out_of_band_market():
    """Order matters: blaming the seat for a book problem would overstate what
    more coverage can buy."""
    assert A.classify(_m(slug="s", bestAskQuote=None), EV,
                      seats={"s"})["bucket"] == "ONE_SIDED"
    assert A.classify(_m(slug="s", bestBidQuote={"value": "0.90"}), EV,
                      seats={"s"})["bucket"] == "PRICE_BAND"


def test_band_edges_are_inclusive_exactly_as_the_engine_has_them():
    for p, want in (("0.25", "ELIGIBLE_NOW"), ("0.75", "ELIGIBLE_NOW"),
                    ("0.24", "PRICE_BAND"), ("0.76", "PRICE_BAND")):
        r = A.classify(_m(slug="s", bestBidQuote={"value": p}), EV, seats={"s"})
        assert r["bucket"] == want, f"bid {p} -> {r['bucket']}, wanted {want}"


def test_resolved_and_closed_are_settlement_not_spread():
    for s in ("MARKET_STATUS_RESOLVED", "MARKET_STATUS_CLOSED"):
        assert A.classify(_m(status=s), EV, seats={"aec-x-1"})["bucket"] == "SETTLEMENT"


def test_degenerate_book_is_one_sided():
    for bid, ask in (("0", "0.50"), ("0.50", "1"), ("0.50", "0")):
        r = A.classify(_m(slug="s", bestBidQuote={"value": bid},
                          bestAskQuote={"value": ask}), EV, seats={"s"})
        assert r["bucket"] == "ONE_SIDED", f"{bid}/{ask} -> {r['bucket']}"


def test_held_market_is_duplicate_not_eligible():
    r = A.classify(_m(slug="h"), EV, seats={"h"}, held={"h"})
    assert r["bucket"] == "DUPLICATE_HELD"


# --- seat set comes from production's own selection row -------------------

def test_seats_are_read_from_the_probes_own_selection_row(tmp_path):
    p = tmp_path / "sel.json"
    p.write_text('{"refused":[{"slug":"a"},{"slug":"b"}],"admitted":[{"slug":"c"}]}')
    assert A.seats_from_selection(str(p)) == {"a", "b", "c"}
