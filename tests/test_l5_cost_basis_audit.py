"""The READ-ONLY cost-basis audit for $1/share placeholder legs."""
from __future__ import annotations

import os
import sys
from decimal import Decimal as D

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import l5_cost_basis_audit as A       # noqa: E402


class FakeStore:
    def __init__(self, unknown, positions, prov=None):
        self.state = {"leg_cost_basis_unknown": list(unknown),
                      "positions": dict(positions),
                      "fill_provenance": dict(prov or {})}


class FakeT:
    """Cursor-paginated activities, read-only."""
    def __init__(self, pages, raise_at=None):
        self.pages, self.raise_at, self.calls = pages, raise_at, []

    def request(self, method, path, body=None, base=None):
        assert method == "GET", "the audit must only ever GET"
        self.calls.append(path)
        if self.raise_at is not None and len(self.calls) > self.raise_at:
            raise OSError("venue unreachable")
        i = len(self.calls) - 1
        return self.pages[i] if i < len(self.pages) else {"activities": [],
                                                          "eof": True}


def _fill(market, side, qty, price, fee, coid=None, tid="t1"):
    return {"kind": "filled", "marketSlug": market, "side": side,
            "quantity": qty, "price": price, "commission": fee,
            "clientOrderId": coid, "tradeId": tid}


# ------------------------------------------------------------------ the point
def test_a_manual_leg_basis_is_measured_and_the_overstatement_reported():
    """The $1/share placeholder vs the real fill price."""
    s = FakeStore(["mBTC|DOWN"], {"mBTC|DOWN": "1.27"})
    t = FakeT([{"activities": [
        _fill("mBTC", "DOWN", "1.27", "0.12", "0.01",
              coid="owner-manual-1")], "eof": True}])
    out = A.audit(s, t)
    leg = out["legs"][0]
    assert leg["basis"]["established"] is True
    assert leg["basis"]["actual_cost_usd"] == "0.1524"
    assert leg["placeholder_cost_usd"] == "1.27"
    assert leg["owners"] == [A.OWNER_MANUAL]        # no coid of ours
    assert out["overstatement_usd"] == "1.1176"
    assert out["writes"] == "NONE"


def test_l5_owned_fills_are_distinguished_from_manual_by_our_own_record():
    prov = {"mX": {"entries": [{"coid": "ours1", "l5_owner_verified": True}]}}
    s = FakeStore(["mX|UP"], {"mX|UP": "2"}, prov)
    t = FakeT([{"activities": [
        _fill("mX", "UP", "1", "0.44", "0.01", coid="ours1", tid="a"),
        _fill("mX", "UP", "1", "0.46", "0.01", coid="theirs", tid="b"),
    ], "eof": True}])
    out = A.audit(s, t)
    leg = out["legs"][0]
    assert leg["l5_owned_fills"] == 1
    assert leg["owner_manual_fills"] == 1
    assert leg["basis"]["established"] is True
    assert leg["basis"]["actual_cost_usd"] == "0.90"


def test_a_fill_with_no_client_order_id_is_unknown_owner_not_manual():
    s = FakeStore(["m|DOWN"], {"m|DOWN": "1"})
    t = FakeT([{"activities": [
        dict(_fill("m", "DOWN", "1", "0.10", "0.00"), clientOrderId=None)],
        "eof": True}])
    out = A.audit(s, t)
    assert out["legs"][0]["unknown_owner_fills"] == 1
    assert A.UNKNOWN_OWNER in out["legs"][0]["owners"]


# --------------------------------------------------------------- fails closed
@pytest.mark.parametrize("missing,reason", [
    ("price", "FILL_WITHOUT_PRICE"),
    ("commission", "FILL_WITHOUT_FEE"),
    ("quantity", "FILL_WITHOUT_QUANTITY"),
])
def test_a_missing_field_is_never_zero(missing, reason):
    s = FakeStore(["m|DOWN"], {"m|DOWN": "1"})
    row = _fill("m", "DOWN", "1", "0.10", "0.01")
    row[missing] = None
    row.pop("avgPx", None); row.pop("cumQuantity", None)
    row.pop("commissionNotionalTotalCollected", None)
    t = FakeT([{"activities": [row], "eof": True}])
    out = A.audit(s, t)
    assert out["legs"][0]["basis"]["established"] is False
    assert out["legs"][0]["basis"]["reason"] == reason
    assert out["establishable"] == 0
    assert out["overstatement_usd"] == "0"


def test_a_quantity_mismatch_refuses_rather_than_understating_cost():
    s = FakeStore(["m|DOWN"], {"m|DOWN": "5"})
    t = FakeT([{"activities": [_fill("m", "DOWN", "2", "0.10", "0.01")],
                "eof": True}])
    out = A.audit(s, t)
    b = out["legs"][0]["basis"]
    assert b["established"] is False and b["reason"] == "QUANTITY_MISMATCH"
    assert b["venue_qty"] == "2" and b["ledger_qty"] == "5"


def test_no_fills_found_keeps_the_placeholder():
    s = FakeStore(["m|DOWN"], {"m|DOWN": "1"})
    out = A.audit(s, FakeT([{"activities": [], "eof": True}]))
    assert out["legs"][0]["basis"]["reason"] == "NO_FILLS_FOUND"
    assert out["unestablished"] == 1


def test_an_incomplete_activity_walk_establishes_nothing():
    """A truncated feed may be missing fills, so no basis is trusted."""
    s = FakeStore(["m|DOWN"], {"m|DOWN": "1"})
    pages = [{"activities": [_fill("m", "DOWN", "1", "0.10", "0.01")],
              "nextCursor": "c%d" % i} for i in range(3)]
    out = A.audit(s, FakeT(pages), max_pages=2)
    assert out["activity_walk"]["complete"] is False
    assert out["activity_walk"]["ended"] == "max_pages"
    assert out["legs"][0]["basis"]["reason"].startswith(
        "ACTIVITY_WALK_INCOMPLETE")
    assert out["establishable"] == 0


def test_an_unreachable_venue_mid_walk_is_reported_not_treated_as_eof():
    s = FakeStore(["m|DOWN"], {"m|DOWN": "1"})
    pages = [{"activities": [], "nextCursor": "c1"}]
    out = A.audit(s, FakeT(pages, raise_at=1))
    assert out["activity_walk"]["ended"].startswith("error:")
    assert out["activity_walk"]["complete"] is False


# ------------------------------------------------------- non-fill rows ignored
def test_settlements_and_corrections_are_not_fills():
    s = FakeStore(["m|DOWN"], {"m|DOWN": "1"})
    t = FakeT([{"activities": [
        {"kind": "settlement", "marketSlug": "m", "side": "DOWN",
         "quantity": "1", "price": "1.00", "commission": "0"},
        {"kind": "reconciliation_adjustment", "marketSlug": "m",
         "side": "DOWN", "quantity": "1", "price": "0.50", "commission": "0"},
    ], "eof": True}])
    out = A.audit(s, t)
    assert out["legs"][0]["basis"]["reason"] == "NO_FILLS_FOUND"


def test_the_feed_is_walked_once_for_many_markets():
    """ACCOUNT-scoped: walking per market is what drew HTTP 429."""
    s = FakeStore(["a|DOWN", "b|DOWN", "c|UP"],
                  {"a|DOWN": "1", "b|DOWN": "1", "c|UP": "1"})
    t = FakeT([{"activities": [], "eof": True}])
    A.audit(s, t)
    assert len(t.calls) == 1, t.calls


def test_the_audit_writes_nothing_to_the_store():
    s = FakeStore(["m|DOWN"], {"m|DOWN": "1.27"})
    before = repr(s.state)
    A.audit(s, FakeT([{"activities": [
        _fill("m", "DOWN", "1.27", "0.12", "0.01")], "eof": True}]))
    assert repr(s.state) == before


# ------------------------------------------------- wiring into the executor
def test_the_runner_hook_is_env_gated_and_runs_after_the_transport_exists():
    """THE BUG THIS PINS. The hook was first placed in __init__ before
    self.transport was assigned, so it could only ever raise AttributeError
    and the audit would silently never run."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "src/l5_pmus_live_runner.py")).read()
    t = src.index("self.transport = T.PmusTransport(")
    a = src.index('os.getenv("L5_COST_BASIS_AUDIT"')
    e = src.index("self.executor = LX.FaithfulExecutor(")
    assert t < a < e, "the audit must run after the transport is built"
    # gated: a normal boot must not spend the feed walk
    assert '"L5_COST_BASIS_AUDIT", ""' in src
    assert '.upper() == "YES"' in src


def test_the_audit_module_calls_no_write_or_order_verb():
    """Mentions in prose are fine; CALLS are not. Checked on the parsed
    tree so a docstring explaining why it cannot correct a settlement does
    not read as it correcting one."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(A))
    called = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute):
                called.add(f.attr)
            elif isinstance(f, ast.Name):
                called.add(f.id)
    for forbidden in ("save", "submit", "cancel", "correct_settled_market",
                      "reconstruct_cost_basis", "persist", "put"):
        assert forbidden not in called, forbidden
    # the only venue verbs it resolves are the two READ verbs, and the
    # method it ever passes is GET
    src = inspect.getsource(A._get)
    assert '("_get", "request")' in src
    assert '"GET"' in src
    assert "POST" not in inspect.getsource(A)
    # and no assignment anywhere targets a store/state subscript
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            for tgt in n.targets:
                assert not isinstance(tgt, ast.Subscript), ast.dump(tgt)[:80]


# ------------------------------------------- the two defects the deploy found
class GetOnlyT(FakeT):
    """A transport shaped like the REAL PmusTransport: _get, no request."""
    def _get(self, path, base=None):
        return FakeT.request(self, "GET", path)
    request = property(lambda self: (_ for _ in ()).throw(
        AttributeError("PmusTransport has no public request")))


def test_the_executor_read_verb_is_used_not_a_nonexistent_request():
    """THE DEFECT THE DEPLOY FOUND. PmusTransport has no public `request` --
    that belongs to the private _SignedHTTP -- so the first deployed audit
    raised AttributeError on page one and established nothing while
    reporting itself ok."""
    s = FakeStore([], {})
    t = GetOnlyT([{"activities": [], "eof": True}])
    walk = A.walk_activities(t)
    assert walk["ended"] == "eof" and walk["complete"] is True
    assert t.calls == [A.ACTIVITIES_PATH]


def test_a_transport_with_neither_verb_is_reported_not_silently_empty():
    class Mute:
        pass
    walk = A.walk_activities(Mute())
    assert walk["ended"].startswith("error:AttributeError")
    assert walk["complete"] is False


def test_a_retired_leg_uses_the_settled_quantity_not_the_zeroed_position():
    """THE SECOND DEFECT. Settlement consumes the position, so positions[key]
    is 0 for a retired market and 28 of 29 legs reported a $0 placeholder."""
    s = FakeStore(["mBTC|UP"], {"mBTC|UP": "0"})
    s.state["retired_markets"] = {"mBTC": {
        "up_qty": "2.9000", "dn_qty": "0", "cost_total": "2.9000",
        "pnl_provenance": "UNKNOWN_COST_UPPER_BOUND"}}
    t = GetOnlyT([{"activities": [
        _fill("mBTC", "UP", "2.9000", "0.20", "0.02",
              coid="owner-manual-9")], "eof": True}])
    out = A.audit(s, t)
    leg = out["legs"][0]
    assert leg["qty_source"] == "retired_markets"
    assert leg["ledger_qty"] == "2.9000"
    assert leg["placeholder_cost_usd"] == "2.9000"
    assert leg["basis"]["established"] is True
    assert leg["basis"]["actual_cost_usd"] == "0.580000"
    assert out["overstatement_usd"] == "2.320000"


def test_a_live_leg_still_uses_positions():
    s = FakeStore(["mLive|DOWN"], {"mLive|DOWN": "1.27"})
    t = GetOnlyT([{"activities": [], "eof": True}])
    out = A.audit(s, t)
    assert out["legs"][0]["qty_source"] == "positions"
    assert out["legs"][0]["ledger_qty"] == "1.27"


def test_an_unreadable_leg_quantity_is_reported_not_guessed_as_zero():
    s = FakeStore(["mX|UP"], {})
    s.state["retired_markets"] = {"mX": {"up_qty": "nonsense"}}
    out = A.audit(s, GetOnlyT([{"activities": [], "eof": True}]))
    leg = out["legs"][0]
    assert leg["ledger_qty"] is None
    assert leg["qty_source"] == "retired_markets:UNREADABLE"
    assert leg["basis"]["reason"] == "LEG_QUANTITY_UNAVAILABLE"
    assert out["establishable"] == 0


# ------------------------------- why nothing matched, when nothing matched

def test_the_diagnostic_names_the_WRONG_PREDICATE_rather_than_guessing():
    """THE PRODUCTION CASE. All 46 legs reported NO_FILLS_FOUND over a
    COMPLETE walk of 2,126 rows. classify_fills needs market, fill-kind and
    side to hold TOGETHER, so counting them independently is what says which
    one is wrong."""
    rows = [{"kind": "trade", "marketId": "wrong-market", "side": "UP"}
            for _ in range(50)]
    sh = A.feed_shape(rows, markets=["right-market"], sides=["UP", "DOWN"])
    assert sh["rows"] == 50
    assert sh["rows_matching_fill_kind_only"] == 50     # kind is fine
    assert sh["rows_matching_side_only"] == 50          # side is fine
    assert sh["rows_matching_market_only"] == 0         # the market is NOT
    assert sh["rows_matching_all_three"] == 0


def test_an_unrecognised_fill_vocabulary_is_named_as_such():
    rows = [{"kind": "EXECUTION", "market": "m", "side": "UP"}] * 10
    sh = A.feed_shape(rows, markets=["m"], sides=["UP"])
    assert sh["rows_matching_market_only"] == 10
    assert sh["rows_matching_side_only"] == 10
    assert sh["rows_matching_fill_kind_only"] == 0       # FILL_KINDS misses it
    assert "execution" in sh["distinct_kinds"]
    assert sh["fill_kinds_recognised"] == sorted(A.FILL_KINDS)


def test_a_wrong_side_vocabulary_is_named_as_such():
    rows = [{"kind": "trade", "market": "m", "side": "BUY"}] * 7
    sh = A.feed_shape(rows, markets=["m"], sides=["UP", "DOWN"])
    assert sh["rows_matching_side_only"] == 0
    assert sh["rows_matching_market_only"] == 7


def test_an_absent_kind_is_reported_not_dropped():
    sh = A.feed_shape([{"market": "m", "side": "UP"}], markets=["m"],
                      sides=["UP"])
    assert sh["distinct_kinds"] == {"<absent>": 1}


def test_the_diagnostic_reports_field_NAMES_and_no_values():
    """It must be safe to log: no price, quantity, identifier or credential."""
    rows = [{"kind": "trade", "market": "m", "side": "UP", "price": "0.59",
             "quantity": "5", "clientOrderId": "l5f-secret",
             "apiKey": "SHOULD-NEVER-APPEAR"}]
    sh = A.feed_shape(rows, markets=["m"], sides=["UP"])
    import json as _json
    blob = _json.dumps(sh)
    for secret in ("0.59", "l5f-secret", "SHOULD-NEVER-APPEAR"):
        assert secret not in blob, secret
    # the field NAMES are present, which is the whole point
    assert "clientOrderId" in sh["most_common_field_name_sets"][0]["fields"]


def test_the_diagnostic_is_computed_only_when_nothing_was_established():
    """A healthy audit should not carry it."""
    import inspect
    src = inspect.getsource(A.audit)
    assert "if establishable == 0 and keys else None" in src


def test_the_placeholder_total_is_summed_for_EVERY_leg():
    """It reported "0" in production while 33 legs carried non-zero
    placeholders, because it was summed only over established legs. That is
    the one number saying how much of realized rests on an upper bound."""
    import inspect
    src = inspect.getsource(A.audit)
    i_all = src.index("placeholder_total += placeholder")
    i_est = src.index('if basis.get("established")')
    assert i_all < i_est, "the placeholder total must be summed before the gate"


# ----------------------------- the REAL feed shape, from the production feed

def prod_row(market="cpc-btc-updown-15m-2026-10-16-0330z", side="YES",
             price="0.41", qty="50", fee="0.00", coid=None):
    """A row shaped EXACTLY as the production feed sends it: two top-level
    fields, `type` and `trade`, with every money field one level down.
    feed_shape reported 1,911 of these and the parser matched none."""
    inner = {"marketSlug": market, "side": side, "price": price,
             "quantity": qty, "commission": fee, "tradeId": "t-1",
             "time": "2026-10-16T03:30:00Z"}
    if coid:
        inner["clientOrderId"] = coid
    return {"type": "activity_type_trade", "trade": inner}


def test_the_feeds_actual_kind_is_recognised():
    """activity_type_trade, not "trade". The old set matched none of the
    five kinds the feed actually sends."""
    assert "activity_type_trade" in A.FILL_KINDS
    for legacy in ("trade", "fill", "filled"):
        assert legacy in A.FILL_KINDS, legacy


def test_the_market_is_read_from_the_NESTED_payload():
    r = prod_row()
    assert A._row_market(r) == "cpc-btc-updown-15m-2026-10-16-0330z"


def test_the_side_is_read_from_the_NESTED_payload():
    assert A._row_side(prod_row(side="YES")) == "UP"
    assert A._row_side(prod_row(side="NO")) == "DOWN"


def test_a_flat_row_still_parses():
    """A feed that stops wrapping must not break this."""
    flat = {"type": "trade", "marketSlug": "m", "side": "YES",
            "price": "0.5", "quantity": "1"}
    assert A._row_market(flat) == "m"
    assert A._row_side(flat) == "UP"


def test_the_money_fields_come_from_the_payload_not_the_envelope():
    c = A.classify_fills([prod_row(price="0.41", qty="50", fee="0.07")],
                         market="cpc-btc-updown-15m-2026-10-16-0330z",
                         side="UP", our_coids=set())
    assert len(c["fills"]) == 1
    f = c["fills"][0]
    assert f["price"] == "0.41" and f["qty"] == "50" and f["fee"] == "0.07"
    assert f["trade_id"] == "t-1"
    assert f["t"] == "2026-10-16T03:30:00Z"


def test_ownership_still_comes_from_OUR_coids_only():
    """A nested clientOrderId we do not recognise is OWNER_MANUAL, and an
    absent one is UNKNOWN_OWNER. Neither is ever assumed to be ours."""
    ours = A.classify_fills([prod_row(coid="l5f-mine")],
                            market="cpc-btc-updown-15m-2026-10-16-0330z",
                            side="UP", our_coids={"l5f-mine"})
    assert ours["fills"][0]["owner"] == A.L5_OWNED
    theirs = A.classify_fills([prod_row(coid="someone-else")],
                              market="cpc-btc-updown-15m-2026-10-16-0330z",
                              side="UP", our_coids={"l5f-mine"})
    assert theirs["fills"][0]["owner"] == A.OWNER_MANUAL
    anon = A.classify_fills([prod_row()],
                            market="cpc-btc-updown-15m-2026-10-16-0330z",
                            side="UP", our_coids={"l5f-mine"})
    assert anon["fills"][0]["owner"] == A.UNKNOWN_OWNER


def test_a_position_resolution_is_NOT_counted_as_a_fill():
    """205 of these rows exist. A settlement is not an acquisition."""
    r = {"type": "activity_type_position_resolution",
         "positionResolution": {"marketSlug": "m", "side": "YES",
                                "quantity": "5"}}
    c = A.classify_fills([r], market="m", side="UP", our_coids=set())
    assert c["fills"] == []


def test_the_production_shape_now_matches_all_three_predicates():
    """The end-to-end regression for the defect: feed_shape counted zero on
    every predicate against these rows."""
    rows = [prod_row() for _ in range(5)]
    sh = A.feed_shape(rows, markets=["cpc-btc-updown-15m-2026-10-16-0330z"],
                      sides=["UP", "DOWN"])
    assert sh["rows_matching_market_only"] == 5
    assert sh["rows_matching_fill_kind_only"] == 5
    assert sh["rows_matching_side_only"] == 5
    assert sh["rows_matching_all_three"] == 5


# ------------------------- the two things that were being assumed

def test_a_zero_commission_is_NOT_counted_as_a_rebate():
    """The programme's standing rule, now enforced by the counter."""
    rows = [prod_row(fee="0.00") for _ in range(10)]
    ev = A.fee_and_notional_evidence(rows)
    assert ev["commission_signs"]["zero"] == 10
    assert ev["commission_signs"]["negative_a_credited_rebate"] == 0
    assert ev["rebates_ever_credited"] is False


def test_a_NEGATIVE_commission_is_a_credited_rebate():
    ev = A.fee_and_notional_evidence([prod_row(fee="-0.02")])
    assert ev["commission_signs"]["negative_a_credited_rebate"] == 1
    assert ev["rebates_ever_credited"] is True
    assert ev["commission_sum_usd"] == "-0.02"


def test_an_unreadable_commission_is_counted_not_dropped():
    ev = A.fee_and_notional_evidence([prod_row(fee=None)])
    assert ev["commission_signs"]["unreadable"] == 1
    assert ev["rebates_ever_credited"] is False


def test_the_smallest_executed_notional_is_reported():
    """The venue's minimum order VALUE is read nowhere in this codebase, so
    the smallest notional it has actually executed is the evidence."""
    rows = [prod_row(price="0.59", qty="5"), prod_row(price="0.41", qty="0.01")]
    ev = A.fee_and_notional_evidence(rows)
    assert ev["smallest_executed_quantity"] == "0.01"
    assert D(ev["smallest_executed_notional_usd"]) == D("0.0041")
    assert D(ev["largest_executed_notional_usd"]) == D("2.95")


def test_a_settlement_is_not_counted_in_the_fee_evidence():
    rows = [{"type": "activity_type_position_resolution",
             "positionResolution": {"quantity": "5", "price": "1"}}]
    ev = A.fee_and_notional_evidence(rows)
    assert ev["fill_rows"] == 0
    assert ev["smallest_executed_notional_usd"] is None


def test_the_evidence_reports_no_identifier():
    rows = [prod_row(coid="l5f-secret")]
    rows[0]["trade"]["tradeId"] = "SHOULD-NOT-APPEAR"
    import json as _json
    blob = _json.dumps(A.fee_and_notional_evidence(rows))
    for secret in ("l5f-secret", "SHOULD-NOT-APPEAR"):
        assert secret not in blob, secret


# ------------------- the nested execution mapping (the real feed shape)

def trade(isaggressor=True, agg_side="OUTCOME_SIDE_YES",
          pas_side="OUTCOME_SIDE_NO", agg_fee="0.07", pas_fee="-0.02",
          market="cpc-btc-updown-15m-2026-10-16-0330z", qty="50",
          price="0.41", cost="20.50", **extra):
    """A trade row shaped as the production feed sends it. Its payload has
    qty/qtyDecimal/cost/price/marketSlug and NO top-level side or
    commission -- those live in the two nested execution objects."""
    inner = {"marketSlug": market, "price": price, "qty": qty,
             "qtyDecimal": qty, "cost": cost, "id": "t-1",
             "aggressorExecution": {"side": agg_side, "commission": agg_fee},
             "passiveExecution": {"side": pas_side, "commission": pas_fee}}
    if isaggressor is not None:
        inner["isAggressor"] = isaggressor
    inner.update(extra)
    return {"type": "activity_type_trade", "trade": inner}


def test_OUR_side_comes_from_isAggressor_not_from_whichever_is_first():
    """A trade has TWO sides and we are one of them. Taking the first would
    book the COUNTERPARTY's side on half the rows -- a worse failure than
    reading nothing, because it looks like data."""
    assert A._row_side(trade(isaggressor=True)) == "UP"
    assert A._row_side(trade(isaggressor=False)) == "DOWN"


def test_OUR_commission_comes_from_OUR_execution():
    for flag, want in ((True, "0.07"), (False, "-0.02")):
        f = A.classify_fills(
            [trade(isaggressor=flag)],
            market="cpc-btc-updown-15m-2026-10-16-0330z",
            side=("UP" if flag else "DOWN"), our_coids=set())["fills"][0]
        assert f["fee"] == want, flag


def test_an_absent_isAggressor_leaves_the_side_UNKNOWN():
    """Do not infer missing values: which side was ours is unknown, so it
    stays unknown rather than becoming a coin flip."""
    assert A._row_side(trade(isaggressor=None)) is None


def test_a_single_execution_object_is_unambiguous():
    r = {"type": "activity_type_trade", "trade": {
        "marketSlug": "m", "price": "0.5", "qty": "1", "cost": "0.5",
        "passiveExecution": {"side": "OUTCOME_SIDE_NO", "commission": "0"}}}
    assert A._row_side(r) == "DOWN"


def test_the_top_level_probe_is_SHALLOW():
    """THE BUG THIS PINS. _first_present searching one level down walked into
    aggressorExecution and returned the aggressor's side before isAggressor
    was consulted, defeating the selection entirely."""
    body = {"aggressorExecution": {"side": "OUTCOME_SIDE_YES"}}
    assert A._first_present([body], A.SIDE_KEYS) is None          # shallow
    assert A._first_present([body], A.SIDE_KEYS, deep=True) == "OUTCOME_SIDE_YES"


def test_the_feeds_own_quantity_and_cost_field_names_are_used():
    f = A.classify_fills([trade(qty="50", cost="20.50", price="0.41")],
                         market="cpc-btc-updown-15m-2026-10-16-0330z",
                         side="UP", our_coids=set())["fills"][0]
    assert f["qty"] == "50"            # `qty`, not `quantity`
    assert f["cost"] == "20.50"        # the venue's AUTHORITATIVE cost
    assert f["price"] == "0.41"


def test_realized_pnl_and_cost_basis_are_carried_when_present():
    f = A.classify_fills(
        [trade(realizedPnl="-1.25", costBasis="3.40")],
        market="cpc-btc-updown-15m-2026-10-16-0330z", side="UP",
        our_coids=set())["fills"][0]
    assert f["realized_pnl"] == "-1.25"
    assert f["cost_basis"] == "3.40"


def test_the_side_vocabulary_matches_by_SUFFIX():
    """POSITION_RESOLUTION_SIDE_LONG and OUTCOME_SIDE_YES are both seen on
    the feed; a new prefix must not fall through to the wrong side."""
    for v, want in (("OUTCOME_SIDE_YES", "UP"), ("OUTCOME_SIDE_NO", "DOWN"),
                    ("POSITION_RESOLUTION_SIDE_LONG", "UP"),
                    ("POSITION_RESOLUTION_SIDE_SHORT", "DOWN"),
                    ("SOMETHING_ELSE", None)):
        r = {"type": "activity_type_trade",
             "trade": {"marketSlug": "m", "passiveExecution": {"side": v}}}
        assert A._row_side(r) == want, v


def test_the_inventory_censuses_BOTH_executions():
    """The key census is not an attribution, so it is not restricted by
    isAggressor."""
    inv = A.execution_field_inventory([trade(isaggressor=None)])
    assert inv["fill_rows_with_execution_objects"] == 1
    assert inv["our_side_selectable_via_isAggressor"] == 0
    vals = inv["side_like_values_inside_executions"]
    assert "side=OUTCOME_SIDE_YES" in vals and "side=OUTCOME_SIDE_NO" in vals


def test_selectability_is_counted_so_attributability_is_bounded():
    inv = A.execution_field_inventory(
        [trade(isaggressor=True), trade(isaggressor=False),
         trade(isaggressor=None)])
    assert inv["our_side_selectable_via_isAggressor"] == 2


def real_trade(isaggressor=True):
    """The production execution shape, from the key census: the execution's
    own keys are aggressor/commissionNotionalCollected/commissionSpreadPx/id/
    lastPx/lastShares/legPrices/order/... with NO side among them -- the side
    is inside the nested `order`."""
    def ex(side, fee):
        return {"commissionNotionalCollected": fee, "commissionSpreadPx": "0",
                "lastPx": "0.41", "lastShares": "50", "id": "e-1",
                "order": {"side": side}}
    return {"type": "activity_type_trade", "trade": {
        "marketSlug": "cpc-btc-updown-15m-2026-10-16-0330z",
        "price": "0.41", "qty": "50", "qtyDecimal": "50", "cost": "20.50",
        "isAggressor": isaggressor, "id": "t-1",
        "aggressorExecution": ex("OUTCOME_SIDE_YES", "0.07"),
        "passiveExecution": ex("OUTCOME_SIDE_NO", "-0.02")}}


def test_the_side_is_found_inside_the_executions_nested_order():
    assert A._row_side(real_trade(True)) == "UP"
    assert A._row_side(real_trade(False)) == "DOWN"


def test_the_deep_search_cannot_reach_the_COUNTERPARTYS_side():
    """A deep search is only safe because isAggressor narrowed the set to one
    object first. Both rows carry both sides; each resolves to its own."""
    assert A._row_side(real_trade(True)) != A._row_side(real_trade(False))


def test_the_feeds_real_commission_key_is_read():
    """commissionNotionalCollected, not commissionNotionalTotalCollected --
    the census reported {} for fees because of that one word."""
    assert A.FEE_KEYS[0] == "commissionNotionalCollected"
    for flag, want in ((True, "0.07"), (False, "-0.02")):
        f = A.classify_fills(
            [real_trade(flag)],
            market="cpc-btc-updown-15m-2026-10-16-0330z",
            side=("UP" if flag else "DOWN"), our_coids=set())["fills"][0]
        assert f["fee"] == want, flag


def test_OUR_executed_shares_and_price_come_from_OUR_execution():
    f = A.classify_fills(
        [real_trade(True)], market="cpc-btc-updown-15m-2026-10-16-0330z",
        side="UP", our_coids=set())["fills"][0]
    assert f["qty"] == "50"        # lastShares
    assert f["price"] == "0.41"    # lastPx
    assert f["cost"] == "20.50"    # the trade's authoritative cost


def test_the_census_searches_deep_enough_to_name_both():
    inv = A.execution_field_inventory([real_trade(True)])
    assert inv["side_like_values_inside_executions"] != {}
    assert "commissionNotionalCollected" in inv[
        "fee_like_keys_inside_executions"]


def test_all_three_predicates_now_match_a_real_trade_row():
    sh = A.feed_shape([real_trade(True)],
                      markets=["cpc-btc-updown-15m-2026-10-16-0330z"],
                      sides=["UP", "DOWN"])
    assert sh["rows_matching_all_three"] == 1


def census_trade(isaggressor=True, agg_outcome="OUTCOME_SIDE_YES",
                 pas_outcome="OUTCOME_SIDE_NO", agg_fee="0.07",
                 pas_fee="-0.02"):
    """The shape the production key census reported: the execution carries
    `outcomeSide` AND a nested order whose `side` is ORDER_SIDE_BUY/SELL."""
    def ex(outcome, fee):
        return {"commissionNotionalCollected": fee,
                "commissionNotionalTotalCollected": fee,
                "lastPx": "0.41", "lastShares": "50",
                "outcomeSide": outcome,
                "order": {"side": "ORDER_SIDE_BUY"}}
    return {"type": "activity_type_trade", "trade": {
        "marketSlug": "m", "price": "0.41", "qty": "50", "cost": "20.50",
        "isAggressor": isaggressor,
        "aggressorExecution": ex(agg_outcome, agg_fee),
        "passiveExecution": ex(pas_outcome, pas_fee)}}


def test_outcomeSide_is_preferred_over_the_orders_BUY_SELL_side():
    """THE PRECEDENCE DEFECT. The order's `side` is ORDER_SIDE_BUY/SELL --
    1,911 of each in production -- which is a DIRECTION, not an outcome.
    Probing `side` first returned None for every trade row."""
    assert A.SIDE_KEYS.index("outcomeSide") < A.SIDE_KEYS.index("side")
    assert A._row_side(census_trade(True)) == "UP"
    assert A._row_side(census_trade(False)) == "DOWN"


def test_a_BUY_SELL_direction_never_resolves_to_an_outcome():
    """Returning UP for ORDER_SIDE_BUY would book a side the row never
    stated."""
    for v in ("ORDER_SIDE_BUY", "ORDER_SIDE_SELL", "BUY", "SELL"):
        r = {"type": "activity_type_trade", "trade": {
            "marketSlug": "m", "passiveExecution": {"order": {"side": v}}}}
        assert A._row_side(r) is None, v


def test_the_venues_UNSPECIFIED_outcome_stays_UNKNOWN():
    """751 executions carry it. It is the venue declining to say."""
    r = {"type": "activity_type_trade", "trade": {
        "marketSlug": "m",
        "passiveExecution": {"outcomeSide": A.UNSPECIFIED_SIDE}}}
    assert A._row_side(r) is None


def test_the_fee_evidence_reads_OUR_execution_not_the_trade_body():
    """THE SECOND DEFECT. The trade body has no commission at all, so every
    one of the 1,911 rows reported `unreadable` and rebates_ever_credited
    read false for the wrong reason."""
    ev = A.fee_and_notional_evidence([census_trade(True), census_trade(False)])
    assert ev["commission_signs"]["unreadable"] == 0
    assert ev["commission_signs"]["negative_a_credited_rebate"] == 1
    assert ev["commission_signs"]["positive_a_charge"] == 1
    assert ev["rebates_ever_credited"] is True


def test_the_notional_evidence_uses_our_executed_shares_and_price():
    ev = A.fee_and_notional_evidence([census_trade(True)])
    assert ev["smallest_executed_quantity"] == "50"
    assert D(ev["smallest_executed_notional_usd"]) == D("20.50")


def test_all_three_predicates_match_the_census_shape():
    sh = A.feed_shape([census_trade(True)], markets=["m"],
                      sides=["UP", "DOWN"])
    assert sh["rows_matching_all_three"] == 1
