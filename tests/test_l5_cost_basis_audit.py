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
    """The $1/share placeholder vs the real fill price.

    THIS TEST ENCODED THE DEFECT. It asserted 1.27 x 0.12 = 0.1524 for a
    DOWN leg, costing it at the YES price. PMUS quotes one price per market
    and it is always the UP/YES price, so a DOWN fill at YES 0.12 cost US
    0.88 a share: 1.27 x 0.88 = 1.1176. The placeholder was 1.27, so the
    real overstatement is 0.1524 -- the two figures trade places.
    """
    s = FakeStore(["mBTC|DOWN"], {"mBTC|DOWN": "1.27"})
    t = FakeT([{"activities": [
        _fill("mBTC", "DOWN", "1.27", "0.12", "0.01",
              coid="owner-manual-1")], "eof": True}])
    out = A.audit(s, t)
    leg = out["legs"][0]
    assert leg["basis"]["established"] is True
    assert leg["basis"]["actual_cost_usd"] == "1.1176"
    assert leg["placeholder_cost_usd"] == "1.27"
    assert leg["owners"] == [A.OWNER_MANUAL]        # no coid of ours
    assert out["overstatement_usd"] == "0.1524"
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
    and the audit would silently never run.

    REWRITTEN FOR THE DEFERRED QUEUE, AND STRICTLY STRONGER. The audit no
    longer runs inside `roll` at all -- it walked 133 pages of the activity
    feed through a 1.5-requests-a-second limiter there, which is the 93-second
    window-roll stall of issue #195. `roll` now ENQUEUES it and the tick
    drains it after every trading stage.

    The ordering property survives and is checked on the enqueue, which is
    still between the transport and the executor. Two new properties are
    checked beside it, and either one failing would reintroduce the stall:
    `roll` must not call the audit, and the job must read `self.transport`
    when it runs rather than capture one at enqueue time.
    """
    import ast
    import inspect
    from src import l5_pmus_live_runner as RUN
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "src/l5_pmus_live_runner.py")).read()
    t = src.index("self.transport = T.PmusTransport(")
    a = src.index("self.enqueue_roll_diagnostics()")
    e = src.index("self.executor = LX.FaithfulExecutor(")
    assert t < a < e, "the audit must be queued after the transport is built"
    # gated: a normal boot must not spend the feed walk
    assert '"L5_COST_BASIS_AUDIT", ""' in src
    assert '.upper() == "YES"' in src

    # `roll` ITSELF MUST NOT WALK THE FEED. This is the regression guard for
    # the stall: any call to the audit or to the walk from inside `roll`
    # puts ~89 s of paced venue reads back on the window's opening.
    roll_src = inspect.getsource(RUN.Runner.roll)
    for forbidden in ("CBA.audit", "walk_activities", "DC.from_tape",
                      "TR.from_env", "establish_l5_baseline",
                      "apply_cost_basis_corrections"):
        assert forbidden not in roll_src, forbidden

    # AND THE JOB READS THE LIVE TRANSPORT. A walk may span a rollover, which
    # rebuilds `self.transport`, so capturing one at enqueue time would leave
    # the walk talking to a stale object.
    step = inspect.getsource(RUN.Runner._cost_basis_audit_step)
    assert "pw.transport = self.transport" in step
    assert "walk=pw.walk" in step
    # nothing is computed from a walk that is not finished
    tree = ast.parse(step.lstrip())
    guarded = [n for n in ast.walk(tree)
               if isinstance(n, ast.If) and "pw.done" in ast.unparse(n.test)]
    assert guarded, "the audit must be guarded on a completed walk"


def test_the_deferred_queue_runs_the_audit_once_per_process():
    """ITS OWN COMMENT SAID ONCE AND IT RAN NINETY-SIX TIMES A DAY.

    `roll` fires on every window roll, so the inline audit -- documented as
    reading the feed "ONCE" -- took 133 pages of it every fifteen minutes.
    The queue latches on the job name, so repeated enqueues are no-ops.
    """
    from src import l5_roll_diagnostics as RD
    rd = RD.DeferredDiagnostics()
    calls = []

    def job(_deadline):
        calls.append(1)
        return {"ok": True}

    assert rd.enqueue("cost_basis_audit", job) is True
    for _ in range(96):
        assert rd.enqueue("cost_basis_audit", job) is False
    assert rd.pending == ["cost_basis_audit"]
    out = rd.drain(t_in_window_s=600.0, entry_critical=False)
    assert out["outcome"] == RD.DONE
    assert calls == [1]
    assert rd.pending == []
    # and a later roll cannot put it back
    assert rd.enqueue("cost_basis_audit", job) is False
    assert rd.pending == []


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
    # ONE DELIBERATE EXCEPTION, and it is confined. The module gained a gated
    # applier, so it is no longer read-only as a whole -- the claim is now
    # narrower and is asserted rather than assumed: `correct_cost_basis` is
    # the only write verb it ever calls, and it is called from exactly one
    # function. Everything else here, `audit()` included, stays pure.
    assert "correct_cost_basis" in called
    writers = [fn.name for fn in ast.walk(tree)
               if isinstance(fn, ast.FunctionDef)
               and any(isinstance(c, ast.Call)
                       and isinstance(c.func, ast.Attribute)
                       and c.func.attr == "correct_cost_basis"
                       for c in ast.walk(fn))]
    assert writers == ["apply_cost_basis_corrections"], writers
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


# --------------------------------------------------------------------------
# Section 19: structured money. The venue reports price, cost, lastPx and
# commissionNotionalCollected as {currency, value} mappings -- confirmed by
# money_field_shapes() on 1,911 production fill rows. _d() unwraps them, and
# UNKNOWN still means None, never zero.


def test_structured_money_unwraps_to_the_value():
    assert A._d({"currency": "USD", "value": "0.53"}) == D("0.53")
    assert A._d({"currency": "usd", "value": 0}) == D("0")


def test_a_credited_rebate_keeps_its_negative_sign_through_the_unwrap():
    assert A._d({"currency": "USD", "value": "-0.01"}) == D("-0.01")


def test_an_unrecognized_currency_is_unknown_not_dollars():
    assert A._d({"currency": "EUR", "value": "1"}) is None
    assert A._d({"currency": "BTC", "value": "0.5"}) is None


def test_a_mapping_without_a_value_key_is_unknown_not_zero():
    for bad in ({"currency": "USD"}, {}, {"currency": "USD", "value": ""},
                {"currency": "USD", "value": None}, {"currency": "USD", "value": "nan"}):
        assert A._d(bad) is None, bad


def test_a_currencyless_mapping_still_reads_its_value():
    # The shape diagnostic saw currency on every row, but absence of the key is
    # not evidence of a foreign currency.
    assert A._d({"value": "2.5"}) == D("2.5")


def test_scalars_are_unchanged_by_the_structured_money_support():
    assert A._d("0.44") == D("0.44")
    assert A._d(3) == D("3")
    for bad in (None, "", "abc", "nan", "Infinity", [], ()):
        assert A._d(bad) is None, bad


def test_the_money_key_names_come_from_the_diagnostic_not_from_guessing():
    assert A.MONEY_VALUE_KEY == "value"
    assert A.MONEY_CURRENCY_KEY == "currency"
    assert "USD" in A.MONEY_CURRENCIES


def test_nested_mappings_do_not_recurse_into_a_fake_decimal():
    assert A._d({"currency": "USD", "value": {"currency": "USD", "value": "1"}}) == D("1")
    assert A._d({"currency": "USD", "value": {"nope": 1}}) is None


# --------------------------------------------------------------------------
# Section 20: the correction REQUEST. It prepares and never applies. The two
# unestablished legs keep cost UNKNOWN, ownership is carried verbatim, and the
# reasons no existing verb can apply it are stated rather than worked around.


def _leg(key, placeholder, *, established=True, cost="0.59", reason=None,
         owners=("UNKNOWN_OWNER",), l5=0, manual=0, unknown=1):
    basis = ({"established": True, "qty": "2.9000", "actual_cost_usd": cost,
              "fees_usd": "0.0100", "avg_price": "0.20"}
             if established else
             {"established": False, "reason": reason or "QUANTITY_MISMATCH",
              "venue_qty": "2.0000", "ledger_qty": "1.0000"})
    return {"key": key, "market": key.split("|")[0], "side": key.split("|")[1],
            "ledger_qty": "2.90", "qty_source": "retired_markets",
            "placeholder_cost_usd": placeholder, "owners": list(owners),
            "l5_owned_fills": l5, "owner_manual_fills": manual,
            "unknown_owner_fills": unknown, "basis": basis,
            "fills": [{"trade_id": "T-%s" % key}]}


def _req(*legs):
    return A.correction_request({"activity_walk": {"complete": True}, "legs": list(legs)})


def test_the_request_is_prepared_and_never_applied():
    r = _req(_leg("m|UP", "2.90"))
    assert r["prepared"] is True
    assert r["applied"] is False


def test_an_unestablished_leg_keeps_its_cost_unknown_not_zero():
    r = _req(_leg("m|UP", "1.00", established=False))
    row = r["legs"][0] if "legs" in r else None
    assert r["legs_to_correct"] == 0
    assert r["legs_preserved_unknown"] == 1
    # and it contributes nothing to either total
    assert A._d(r["cost_before_total_usd"]) == D("0")
    assert A._d(r["cost_after_total_usd"]) == D("0")


def test_the_unknown_leg_is_not_promoted_to_a_measurement():
    r = _req(_leg("m|UP", "1.00", established=False))
    assert r["realized_delta_usd"] == "0"


def test_totals_cover_only_the_established_legs():
    r = _req(_leg("a|UP", "2.90", cost="0.59"),
             _leg("b|DOWN", "1.00", cost="0.88"),
             _leg("c|UP", "5.00", established=False))
    assert r["legs_total"] == 3
    assert r["legs_to_correct"] == 2
    assert r["legs_preserved_unknown"] == 1
    assert A._d(r["cost_before_total_usd"]) == D("3.90")
    assert A._d(r["cost_after_total_usd"]) == D("1.47")
    assert A._d(r["realized_delta_usd"]) == D("2.43")


def test_the_delta_direction_is_named_as_toward_zero():
    r = _req(_leg("m|UP", "2.90"))
    assert r["realized_delta_direction"] == "TOWARD_ZERO"


def test_both_blocking_reasons_are_reported_not_worked_around():
    r = _req(_leg("m|UP", "2.90"))
    blob = " ".join(r["blocked_by"])
    assert "NO_COST_CORRECTION_VERB" in blob
    assert "CORRECTION_WOULD_RELAX_LOSS_HALT" in blob


def test_ownership_is_carried_verbatim_and_never_reclassified():
    r = _req(_leg("m|UP", "2.90", owners=("UNKNOWN_OWNER",), unknown=5))
    row = r["legs"][0]
    assert row["owners"] == ["UNKNOWN_OWNER"]
    assert row["unknown_owner_fills"] == 5
    assert row["l5_owned_fills"] == 0
    assert "verified_l5 stays 0" in r["ownership_note"]


def test_every_row_records_the_before_provenance_and_its_trade_ids():
    r = _req(_leg("m|UP", "2.90"))
    row = r["legs"][0]
    assert row["cost_before_provenance"] == "PLACEHOLDER_ONE_DOLLAR_PER_SHARE_UPPER_BOUND"
    assert row["cost_after_provenance"] == "MEASURED_FROM_VENUE_FILLS"
    assert row["fill_trade_ids"] == ["T-m|UP"]


def test_an_unreadable_measured_cost_takes_no_action():
    leg = _leg("m|UP", "2.90")
    leg["basis"]["actual_cost_usd"] = None
    r = _req(leg)
    assert r["legs_to_correct"] == 0
    assert r["legs"][0]["action"] == "NONE"
    assert r["legs"][0]["cost_after_usd"] is None


def test_the_request_carries_the_walk_so_a_partial_walk_is_visible():
    r = A.correction_request({"activity_walk": {"pages": 40, "ended": "max_pages",
                                                "complete": False}, "legs": []})
    assert r["activity_walk"]["complete"] is False


# --------------------------------------------------------------------------
# Section 21: the gated applier. INSPECT is the default and writes nothing;
# a batch id authorizes the write and every guard still runs in the verb.


class _RecordingStore:
    """A store that records calls and answers like the real verb's contract."""

    def __init__(self, *, corrected=True, reason=None, state=None):
        self.calls = []
        self._corrected = corrected
        self._reason = reason
        self.lines = {"realized_today": "-233.98540000"}
        # the plan reads its pre-state from the ROW, so the fake must carry
        # one. Single-sided, cost_total 2.90, as m1|UP retired.
        self.state = state if state is not None else {
            "retired_markets": {
                "m1": {"up_qty": "2.90", "dn_qty": "0",
                       "cost_total": "2.90", "yes_value": "0",
                       "settlement_pnl": "-2.90"},
                "m2": {"up_qty": "1.0000", "dn_qty": "0",
                       "cost_total": "1.0000", "yes_value": "0",
                       "settlement_pnl": "-1.0000"}}}

    def realized_lines(self):
        return dict(self.lines)

    def correct_cost_basis(self, market, side, **kw):
        self.calls.append({"market": market, "side": side, **kw})
        if self._corrected:
            return {"corrected": True, "delta": "2.31000000"}
        return {"corrected": False, "reason": self._reason}


def _manifest_for(payload=None):
    """The manifest the INSPECT run would have produced for _audit_payload."""
    return {"legs": 1, "keys": ["m1|UP"], "trade_ids": ["T1"],
            "realized_delta_usd": "2.31",
            "per_leg": {"m1|UP": {"cost_before_usd": "2.90",
                                  "cost_after_usd": "0.59000000",
                                  "trade_ids": ["T1"]}}}


def _audit_payload():
    return {"activity_walk": {"pages": 107, "ended": "eof", "complete": True},
            "legs": [
                {"key": "m1|UP", "market": "m1", "side": "UP",
                 "placeholder_cost_usd": "2.90",
                 "basis": {"established": True, "actual_cost_usd": "0.59000000",
                           "fees_usd": "0.0000"},
                 "fills": [{"trade_id": "T1", "qty": "2.9000",
                            "price": "0.2034482758620689655172413793",
                            "fee": "0.0000"}]},
                {"key": "m2|UP", "market": "m2", "side": "UP",
                 "placeholder_cost_usd": "1.0000",
                 "basis": {"established": False, "reason": "QUANTITY_MISMATCH"},
                 "fills": [{"trade_id": "T2", "qty": "1.0", "price": "0.28",
                            "fee": "0.0"}]}]}


def test_inspect_is_the_default_and_writes_nothing(monkeypatch):
    monkeypatch.delenv(A.APPLY_ENV, raising=False)
    assert A.apply_mode() == A.APPLY_INSPECT
    st = _RecordingStore()
    out = A.apply_cost_basis_corrections(st, _audit_payload())
    assert out["mode"] == "INSPECT"
    assert st.calls == []
    assert out["realized_before"] is None and out["realized_after"] is None


def test_an_unreadable_apply_value_reads_as_inspect(monkeypatch):
    for bad in ("", "   ", "inspect", "INSPECT"):
        monkeypatch.setenv(A.APPLY_ENV, bad)
        assert A.apply_mode() == A.APPLY_INSPECT, bad


def test_a_batch_id_authorizes_the_write(monkeypatch):
    monkeypatch.setenv(A.APPLY_ENV, "batch-7")
    assert A.apply_mode() == "batch-7"


def test_the_quantity_mismatched_leg_is_preserved_as_unknown():
    st = _RecordingStore()
    out = A.apply_cost_basis_corrections(st, _audit_payload(), batch_id="b1",
                                         manifest=_manifest_for())
    assert out["preserved_unknown"] == 1
    assert out["preserved_detail"][0]["cost"] == "UNKNOWN"
    assert out["preserved_detail"][0]["reason"] == "QUANTITY_MISMATCH"
    # and it was never even offered to the verb
    assert [c["market"] for c in st.calls] == ["m1"]


def test_the_evidence_passed_is_the_audits_own_fresh_walk():
    st = _RecordingStore()
    A.apply_cost_basis_corrections(st, _audit_payload(), batch_id="b1",
                                         manifest=_manifest_for())
    ev = st.calls[0]["evidence"]
    assert ev["activity_walk"]["complete"] is True
    assert ev["per_fill"][0]["trade_id"] == "T1"
    assert st.calls[0]["expect"] == {"cost_total": "2.90"}


def test_a_refusal_is_reported_and_not_retried():
    st = _RecordingStore(corrected=False, reason="COST_NOT_RECONCILED")
    out = A.apply_cost_basis_corrections(st, _audit_payload(), batch_id="b1",
                                         manifest=_manifest_for())
    assert out["applied"] == 0 and out["refused"] == 1
    assert out["refused_detail"][0]["reason"] == "COST_NOT_RECONCILED"
    assert len(st.calls) == 1, "no retry"


def test_an_incomplete_walk_is_carried_so_the_verb_can_refuse_it():
    st = _RecordingStore()
    payload = _audit_payload()
    payload["activity_walk"] = {"complete": False, "ended": "max_pages"}
    A.apply_cost_basis_corrections(st, payload, batch_id="b1",
                                     manifest=_manifest_for())
    assert st.calls[0]["evidence"]["activity_walk"]["complete"] is False


def test_an_unreadable_fill_aborts_the_batch_and_preserves_the_leg():
    """The leg drops out of the plan, so the batch no longer matches the
    manifest -- and a changed batch is abandoned, not applied smaller."""
    st = _RecordingStore()
    payload = _audit_payload()
    payload["legs"][0]["fills"][0]["price"] = None
    out = A.apply_cost_basis_corrections(st, payload, batch_id="b1",
                                         manifest=_manifest_for())
    assert st.calls == []
    assert out["mode"] == "ABORTED" and out["reason"] == "BATCH_MISMATCH"
    reasons = [r["reason"] for r in out["preserved_detail"]]
    assert "FILL_UNREADABLE" in reasons


def test_the_correction_id_carries_the_batch_so_a_rerun_is_idempotent():
    st = _RecordingStore()
    A.apply_cost_basis_corrections(st, _audit_payload(), batch_id="b1",
                                         manifest=_manifest_for())
    assert st.calls[0]["correction_id"] == "b1|m1|UP"


# --------------------------------------------------------------------------
# Section 22: the all-or-nothing batch precondition (OWNER 2026-10-16:
# "revalidate all 69 distinct trade IDs, all ten guards, and the expected
# $209.6638 aggregate adjustment. Abort on any mismatch.")


def _plan(keys=("m1|UP",), ids=("T1",), before="2.90", after="0.59"):
    return [{"key": k, "cost_before_usd": before, "cost_after_usd": after,
             "trade_ids": list(ids)} for k in keys]


def _man(**kw):
    m = {"legs": 1, "keys": ["m1|UP"], "trade_ids": ["T1"],
         "realized_delta_usd": "2.31",
         "per_leg": {"m1|UP": {"cost_before_usd": "2.90",
                               "cost_after_usd": "0.59",
                               "trade_ids": ["T1"]}}}
    m.update(kw)
    return m


def test_a_matching_batch_passes_all_three_equalities():
    out = A.check_batch(_plan(), _man())
    assert out["ok"] is True and out["problems"] == []
    assert out["delta"] == "2.31" and out["trade_ids"] == 1


def test_a_missing_leg_aborts_rather_than_applying_a_smaller_correction():
    out = A.check_batch([], _man())
    assert out["ok"] is False
    assert any(p.startswith("LEG_COUNT") for p in out["problems"])


def test_an_extra_leg_aborts():
    out = A.check_batch(_plan(keys=("m1|UP", "m2|UP")), _man())
    assert out["ok"] is False
    assert any(p.startswith("KEYS_DIFFER") for p in out["problems"])


def test_a_substituted_trade_id_aborts():
    out = A.check_batch(_plan(ids=("T9",)), _man())
    assert out["ok"] is False
    assert any(p.startswith("TRADE_IDS_DIFFER") for p in out["problems"])


def test_a_changed_aggregate_aborts_even_with_the_same_legs_and_ids():
    out = A.check_batch(_plan(after="0.60"), _man())
    assert out["ok"] is False
    assert any(p.startswith("DELTA_DIFFERS") for p in out["problems"])


def test_an_incomplete_or_unreadable_manifest_aborts():
    for bad in ({"legs": 1, "keys": [], "trade_ids": ["T1"],
                 "realized_delta_usd": "2.31"},
                {"legs": 1, "keys": ["m1|UP"], "trade_ids": [],
                 "realized_delta_usd": "2.31"},
                {"legs": 1, "keys": ["m1|UP"], "trade_ids": ["T1"],
                 "realized_delta_usd": "abc"}):
        assert A.check_batch(_plan(), bad)["ok"] is False, bad


def test_an_unreadable_leg_cost_aborts_the_batch():
    bad = _plan()
    bad[0]["cost_after_usd"] = None
    assert A.check_batch(bad, _man())["ok"] is False


# -- the applier refuses to write without a verified manifest --------------

def test_an_apply_with_no_manifest_is_aborted_not_attempted():
    st = _RecordingStore()
    out = A.apply_cost_basis_corrections(st, _audit_payload(), batch_id="b1")
    assert out["mode"] == "ABORTED"
    assert out["reason"] == "BATCH_MANIFEST_MISSING"
    assert st.calls == []
    assert out["realized_before"] == out["realized_after"]


def test_a_mismatched_manifest_writes_nothing_at_all():
    st = _RecordingStore()
    out = A.apply_cost_basis_corrections(
        st, _audit_payload(), batch_id="b1",
        manifest=_man(realized_delta_usd="999"))
    assert out["mode"] == "ABORTED" and out["reason"] == "BATCH_MISMATCH"
    assert st.calls == [], "not one leg may be written on a mismatch"
    assert out["realized_before"] == out["realized_after"]
    assert out["would_have_applied"][0]["key"] == "m1|UP"


def test_a_dry_run_reports_the_check_without_needing_to_pass_it():
    st = _RecordingStore()
    out = A.apply_cost_basis_corrections(
        st, _audit_payload(), manifest=_man(realized_delta_usd="999"))
    assert out["mode"] == "INSPECT"
    assert out["batch_check"]["ok"] is False
    assert st.calls == []


def test_the_manifest_file_reader_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv(A.BATCH_MANIFEST_ENV, raising=False)
    assert A.batch_manifest() is None
    missing = tmp_path / "nope.json"
    assert A.batch_manifest(str(missing)) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert A.batch_manifest(str(bad)) is None
    notdict = tmp_path / "list.json"
    notdict.write_text("[1,2]")
    assert A.batch_manifest(str(notdict)) is None
    good = tmp_path / "ok.json"
    good.write_text('{"legs": 1}')
    assert A.batch_manifest(str(good)) == {"legs": 1}


def test_the_shipped_manifest_is_inside_the_image_copy_set():
    """A manifest the container cannot read fails the apply closed, which is
    safe but useless -- and that is exactly what happened on deploy 95e713d0:
    the money-capable service pins RAILWAY_DOCKERFILE_PATH at
    deploy/l5_pmus_live/Dockerfile, which COPYs per file and named no config/
    path, so the apply aborted BATCH_MANIFEST_MISSING with nothing written.

    So this pins the manifest into EVERY Dockerfile that runs the live runner,
    not just the root one.
    """
    import pathlib
    import re
    here = pathlib.Path(__file__).resolve().parents[1]
    shipped = sorted((here / "config").glob("cost_basis_batch_manifest_*.json"))
    assert shipped, "no manifest in config/"
    rel = ["config/" + s.name for s in shipped]

    images = [f for f in [here / "Dockerfile",
                          *sorted((here / "deploy").rglob("Dockerfile"))]
              if f.is_file() and "l5_pmus_live_runner" in f.read_text()]
    assert images, "no image runs the live runner"
    assert any("l5_pmus_live" in str(f) for f in images), \
        "the money-capable image must be among those checked"

    for f in images:
        copied = [c.rstrip("/") for c in
                  re.findall(r"^COPY\s+(\S+)", f.read_text(), re.M)]
        assert any(c == "config" for c in copied) or all(
            r in copied for r in rel), (str(f), copied)

    m = A.batch_manifest(str(shipped[-1]))
    assert m is not None
    assert m["legs"] == len(m["keys"])
    assert len(m["trade_ids"]) == len(set(m["trade_ids"])), "ids must be distinct"
    assert A._d(m["realized_delta_usd"]) is not None


def test_a_second_run_of_the_same_batch_cannot_write_again():
    """OWNER: "Never apply the same batch twice." TWO independent mechanisms,
    and either alone is sufficient:

    1. the batch manifest no longer matches, because the corrected legs are
       no longer unknown-basis and drop out of the audit's leg list;
    2. per leg, the correction_id is already spent.
    """
    st = _RecordingStore()
    first = A.apply_cost_basis_corrections(
        st, _audit_payload(), batch_id="b1", manifest=_manifest_for())
    assert first["mode"] == "APPLIED" and first["applied"] == 1

    # (1) second boot: the corrected leg is established, so the audit no
    # longer lists it as unknown-basis and the batch shrinks.
    second = A.apply_cost_basis_corrections(
        st, {"activity_walk": {"complete": True}, "legs": []},
        batch_id="b1", manifest=_manifest_for())
    assert second["mode"] == "ABORTED"
    assert second["reason"] == "BATCH_MISMATCH"
    assert len(st.calls) == 1, "no second write"

    # (2) and even if the same leg were somehow offered again, the verb's own
    # idempotency refuses it on the spent correction_id.
    spent = _RecordingStore(corrected=False,
                            reason="COST_CORRECTION_ALREADY_APPLIED")
    out = A.apply_cost_basis_corrections(
        spent, _audit_payload(), batch_id="b1", manifest=_manifest_for())
    assert out["refused"] == 1
    assert out["refused_detail"][0]["reason"] == "COST_CORRECTION_ALREADY_APPLIED"


# -- the aggregate alone is not a precondition -----------------------------
# Found by the security review of this change: pinning only the aggregate
# delta plus the key and trade-id SETS is satisfied by two offsetting per-leg
# changes. The store's guards would still refuse them one at a time, but a
# precondition that passes a batch nobody inspected is not a precondition.


def test_offsetting_per_leg_changes_no_longer_satisfy_the_aggregate():
    man = {"legs": 2, "keys": ["a|UP", "b|UP"], "trade_ids": ["T1", "T2"],
           "realized_delta_usd": "4.62",
           "per_leg": {"a|UP": {"cost_before_usd": "2.90",
                                "cost_after_usd": "0.59",
                                "trade_ids": ["T1"]},
                       "b|UP": {"cost_before_usd": "2.90",
                                "cost_after_usd": "0.59",
                                "trade_ids": ["T2"]}}}
    honest = [{"key": "a|UP", "cost_before_usd": "2.90",
               "cost_after_usd": "0.59", "trade_ids": ["T1"]},
              {"key": "b|UP", "cost_before_usd": "2.90",
               "cost_after_usd": "0.59", "trade_ids": ["T2"]}]
    assert A.check_batch(honest, man)["ok"] is True

    # same legs, same ids, same AGGREGATE -- different per-leg values
    offsetting = [{"key": "a|UP", "cost_before_usd": "2.90",
                   "cost_after_usd": "1.59", "trade_ids": ["T1"]},
                  {"key": "b|UP", "cost_before_usd": "2.90",
                   "cost_after_usd": "-0.41", "trade_ids": ["T2"]}]
    agg = sum(D(r["cost_before_usd"]) - D(r["cost_after_usd"])
              for r in offsetting)
    assert agg == D("4.62"), "the aggregate is unchanged, which is the point"
    out = A.check_batch(offsetting, man)
    assert out["ok"] is False
    assert any(p.startswith("PER_LEG_COST_AFTER_USD") for p in out["problems"])


def test_a_manifest_without_per_leg_detail_is_refused():
    man = _man()
    man.pop("per_leg")
    assert A.check_batch(_plan(), man)["ok"] is False
    assert "MANIFEST_PER_LEG_MISSING" in A.check_batch(_plan(), man)["problems"]


def test_a_leg_absent_from_per_leg_is_refused():
    man = _man(per_leg={"other|UP": {"cost_before_usd": "1",
                                     "cost_after_usd": "0",
                                     "trade_ids": []}})
    out = A.check_batch(_plan(), man)
    assert out["ok"] is False
    assert any(p.startswith("PER_LEG_UNKNOWN") for p in out["problems"])


def test_per_leg_trade_ids_must_match_that_leg_not_just_the_batch():
    man = _man(per_leg={"m1|UP": {"cost_before_usd": "2.90",
                                  "cost_after_usd": "0.59",
                                  "trade_ids": ["T9"]}})
    out = A.check_batch(_plan(), man)
    assert out["ok"] is False
    assert any(p.startswith("PER_LEG_TRADE_IDS") for p in out["problems"])


def test_the_shipped_manifest_carries_per_leg_detail_for_every_leg():
    import pathlib
    here = pathlib.Path(__file__).resolve().parents[1]
    m = A.batch_manifest(str(sorted(
        (here / "config").glob("cost_basis_batch_manifest_*.json"))[-1]))
    assert len(m["per_leg"]) == m["legs"]
    assert set(m["per_leg"]) == set(m["keys"])
    ids = sorted(t for v in m["per_leg"].values() for t in v["trade_ids"])
    assert ids == sorted(m["trade_ids"])


# == section 23: the YES-price convention, both sides ======================
# The defect this section pins cost 13 of the 44 legs corrected on
# 2026-10-16 a too-favourable realized figure, and understated the correct
# aggregate correction by 30.75140000. Found by reconstructing
# cpc-btc-updown-15m-2026-10-15-1945z|DOWN against the venue's own `cost`.

def _vfill(market, side, qty, price, fee, cost=None, tid="t1", coid=None):
    """A fill carrying the venue's fee-INCLUSIVE `cost`, as production does."""
    r = _fill(market, side, qty, price, fee, coid=coid, tid=tid)
    if cost is not None:
        r["cost"] = cost
    return r


def test_own_price_is_the_quote_for_UP_and_its_complement_for_DOWN():
    assert A.own_price(D("0.51"), "UP") == D("0.51")
    assert A.own_price(D("0.51"), "DOWN") == D("0.49")
    assert A.own_price(D("0.02"), "UP") == D("0.02")
    # the case that made the XRP-style legs look free: a DOWN leg at a
    # 2-cent YES quote costs 98 cents a share, not 2.
    assert A.own_price(D("0.02"), "DOWN") == D("0.98")


def test_an_UP_leg_costs_the_quoted_price():
    b = A.establish_basis(
        [{"qty": "50", "price": "0.02", "fee": "0.05", "cost": "1.05",
          "trade_id": "x"}], expect_qty=D("50"), side="UP")
    assert b["established"] is True
    assert b["actual_cost_usd"] == "1.00"          # fees are their own line
    assert b["fees_usd"] == "0.05"
    assert b["venue_cost_total_usd"] == "1.05"     # fee-inclusive
    assert b["crosscheck_worst_gap_usd"] == "0.00"


def test_a_DOWN_leg_costs_the_complement_and_reconciles_to_the_venue():
    """The production row for 1945z|DOWN: 1.0000 at YES 0.5100, venue 0.49."""
    b = A.establish_basis(
        [{"qty": "1.0000", "price": "0.5100", "fee": "0.0000",
          "cost": "0.49", "trade_id": "D47QRSGGYZRN"}],
        expect_qty=D("1.0000"), side="DOWN")
    assert b["established"] is True
    assert D(b["actual_cost_usd"]) == D("0.49")
    assert b["venue_cost_total_usd"] == "0.49"
    assert D(b["crosscheck_worst_gap_usd"]) == D(0)
    # and the old arithmetic, which this refuses to reproduce
    assert D(b["actual_cost_usd"]) != D("0.51")


def test_the_old_YES_price_arithmetic_is_not_reproduced_on_a_DOWN_leg():
    """cpcxrp-xrp-updown-15m-2026-10-16-0215z|DOWN, the worst of the 13."""
    b = A.establish_basis(
        [{"qty": "5.28", "price": "0.06", "fee": "0.02", "cost": "4.9832",
          "trade_id": "w"}], expect_qty=D("5.28"), side="DOWN")
    assert b["established"] is True
    assert b["actual_cost_usd"] == "4.9632"        # 5.28 x 0.94
    assert b["actual_cost_usd"] != "0.3168"        # 5.28 x 0.06, the defect
    # the correction against a 5.28 placeholder is 0.3168, not 4.9632: the
    # over-correction on this one leg was 4.6464.
    assert D("5.28") - D(b["actual_cost_usd"]) == D("0.3168")


def test_a_credited_rebate_is_a_negative_fee_and_REDUCES_the_venue_cost():
    """A rebate is additive economics, and it lands in `cost`, not in ours."""
    b = A.establish_basis(
        [{"qty": "1", "price": "0.40", "fee": "-0.01", "cost": "0.39",
          "trade_id": "r"}], expect_qty=D("1"), side="UP")
    assert b["established"] is True
    assert b["actual_cost_usd"] == "0.40"   # the cost itself is unchanged
    assert b["fees_usd"] == "-0.01"         # kept separate, sign preserved
    assert b["venue_cost_total_usd"] == "0.39"
    assert D(b["crosscheck_worst_gap_usd"]) == D(0)


def test_cent_rounding_is_tolerated_and_a_real_disagreement_is_not():
    """The venue rounds to the cent; it does not round by a dollar."""
    ok = A.establish_basis(
        [{"qty": "1", "price": "0.333", "fee": "0.00", "cost": "0.33",
          "trade_id": "a"}], expect_qty=D("1"), side="UP")
    assert ok["established"] is True
    assert D(ok["crosscheck_worst_gap_usd"]) <= A.CROSSCHECK_TOLERANCE

    bad = A.establish_basis(
        [{"qty": "1", "price": "0.33", "fee": "0.00", "cost": "0.67",
          "trade_id": "b"}], expect_qty=D("1"), side="UP")
    assert bad["established"] is False
    assert bad["reason"] == "COST_CROSSCHECK_FAILED"
    assert bad["worst_gap_usd"] == "0.34"
    assert "cheaper reading is NOT adopted" in bad["labels"]


def test_a_side_mixup_is_CAUGHT_by_the_crosscheck_not_silently_costed():
    """The whole point of carrying two readings: costing a DOWN fill at the
    YES price now CONTRADICTS the venue's own cost and refuses."""
    out = A.establish_basis(
        [{"qty": "5.28", "price": "0.06", "fee": "0.02", "cost": "4.9832",
          "trade_id": "w"}], expect_qty=D("5.28"), side="UP")
    assert out["established"] is False
    assert out["reason"] == "COST_CROSSCHECK_FAILED"


def test_an_absent_venue_cost_is_one_fewer_reading_not_a_failure():
    b = A.establish_basis(
        [{"qty": "1", "price": "0.40", "fee": "0.00", "trade_id": "n"}],
        expect_qty=D("1"), side="DOWN")
    assert b["established"] is True
    assert b["actual_cost_usd"] == "0.60"
    assert b["venue_cost_fills_absent"] == 1
    assert b["venue_cost_fills_checked"] == 0
    assert b["crosscheck_worst_gap_usd"] is None


def test_a_bad_side_establishes_nothing():
    for s in (None, "", "YES", "BUY", "up ", "ORDER_SIDE_BUY"):
        out = A.establish_basis([{"qty": "1", "price": "0.4", "fee": "0"}],
                                expect_qty=D("1"), side=s)
        if s == "up ":                      # stripped and upper-cased
            assert out["established"] is True
        else:
            assert out["established"] is False and out["reason"] == "BAD_SIDE"


def test_the_quantity_check_still_precedes_everything():
    out = A.establish_basis(
        [{"qty": "1", "price": "0.4", "fee": "0", "cost": "9.99"}],
        expect_qty=D("2"), side="UP")
    assert out["reason"] == "QUANTITY_MISMATCH"


# == section 24: the plan's pre-state comes from the ROW ====================

def test_the_expectation_is_the_rows_own_cost_total_not_the_placeholder():
    """The 2026-10-16 refusals were caused by asserting the $1/share figure."""
    st = _RecordingStore(state={"retired_markets": {
        "m1": {"up_qty": "50.0000", "dn_qty": "0",
               "cost_total": "37.975400"}}})
    out = A.apply_cost_basis_corrections(st, {
        "activity_walk": {"complete": True},
        "legs": [{"key": "m1|UP", "market": "m1", "side": "UP",
                  "placeholder_cost_usd": "50.0000",
                  "basis": {"established": True,
                            "actual_cost_usd": "1.00000000",
                            "fees_usd": "0.0500"},
                  "fills": [{"trade_id": "T1", "qty": "50.0000",
                             "price": "0.0200", "fee": "0.0500"}]}]})
    leg = out["applied_detail"][0]
    assert leg["cost_before_usd"] == "37.975400"     # the ROW, not 50.0000
    assert leg["cost_after_usd"] == "1.00000000"
    assert out["realized_delta_usd"] == "36.97540000"


def test_a_two_sided_market_plans_the_LEGS_cost_not_the_PAIRS():
    """cpc-btc-updown-15m-2026-10-15-1945z: up_qty 1 / up_cost 0.4600 AND
    dn_qty 1.0000 / dn_cost 1.0000, cost_total 1.4600 for the PAIR.

    TWO FIGURES. `expect` asserts the market-level cost_total, because that
    is the field the row carries. The leg's pre-state is its OWN dn_cost.
    Using the total as the pre-state would book a 0.97 correction where the
    truth is 0.51 -- the other leg's real cost, double-counted.
    """
    st = _RecordingStore(state={"retired_markets": {
        "m1": {"up_qty": "1", "dn_qty": "1.0000", "up_cost": "0.4600",
               "dn_cost": "1.0000", "cost_total": "1.4600"}}})
    out = A.apply_cost_basis_corrections(st, {
        "activity_walk": {"complete": True},
        "legs": [{"key": "m1|DOWN", "market": "m1", "side": "DOWN",
                  "placeholder_cost_usd": "1.0000",
                  "basis": {"established": True,
                            "actual_cost_usd": "0.49000000",
                            "fees_usd": "0.0000"},
                  "fills": [{"trade_id": "T1", "qty": "1.0000",
                             "price": "0.4900", "fee": "0.0000"}]}]})
    leg = out["applied_detail"][0]
    assert leg["cost_before_usd"] == "1.0000"        # the LEG's own cost
    assert leg["expect_cost_total"] == "1.4600"      # the MARKET's total
    assert leg["two_sided"] is True
    assert leg["restate"] is False
    assert out["realized_delta_usd"] == "0.51000000"


def test_a_two_sided_row_with_unreadable_side_costs_stays_unknown():
    st = _RecordingStore(state={"retired_markets": {
        "m1": {"up_qty": "1", "dn_qty": "1.0000", "cost_total": "1.4600"}}})
    out = A.apply_cost_basis_corrections(st, {
        "activity_walk": {"complete": True},
        "legs": [{"key": "m1|DOWN", "market": "m1", "side": "DOWN",
                  "placeholder_cost_usd": "1.0000",
                  "basis": {"established": True,
                            "actual_cost_usd": "0.49000000",
                            "fees_usd": "0.0000"},
                  "fills": [{"trade_id": "T1", "qty": "1.0000",
                             "price": "0.4900", "fee": "0.0000"}]}]})
    assert out["applied_detail"] == []
    assert out["preserved_detail"][0]["reason"] == \
        "MARKET_TWO_SIDED_COST_UNREADABLE"
    assert out["preserved_detail"][0]["cost"] == "UNKNOWN"


def test_an_already_corrected_leg_is_planned_as_a_RESTATEMENT():
    """OWNER 2026-10-16: the verb must reconcile previously corrected legs in
    either direction, so an already-corrected leg now ENTERS the batch with
    restate=True instead of being skipped. The cost may therefore RISE,
    which is what the 13 YES-priced DOWN legs need."""
    st = _RecordingStore(state={"retired_markets": {
        "m1": {"up_qty": "2.90", "dn_qty": "0", "cost_total": "0.100000",
               "cost_superseded": {"cost_total": "2.90"}}}})
    out = A.apply_cost_basis_corrections(st, _audit_payload())
    leg = [x for x in out["applied_detail"] if x["key"] == "m1|UP"][0]
    assert leg["restate"] is True
    assert leg["cost_before_usd"] == "0.100000"
    assert leg["cost_after_usd"] == "0.59000000"     # RAISED
    assert out["realized_delta_usd"] == "-0.49000000"


def test_a_first_correction_that_would_RAISE_a_cost_stays_out():
    st = _RecordingStore(state={"retired_markets": {
        "m1": {"up_qty": "2.90", "dn_qty": "0", "cost_total": "0.100000"}}})
    out = A.apply_cost_basis_corrections(st, _audit_payload())
    assert out["applied_detail"] == []
    d = [x for x in out["preserved_detail"] if x["key"] == "m1|UP"][0]
    assert d["reason"] == "WOULD_RAISE_A_FIRST_COST"
    assert st.calls == []


def test_a_leg_with_nothing_to_reduce_is_not_put_in_the_batch():
    """cpc-btc-updown-15m-2026-10-14-2230z: the row already holds the
    measured 0.59, so there is no correction to make."""
    st = _RecordingStore(state={"retired_markets": {
        "m1": {"up_qty": "2.90", "dn_qty": "0", "cost_total": "0.590000"}}})
    out = A.apply_cost_basis_corrections(st, _audit_payload())
    assert out["applied_detail"] == []
    d = [x for x in out["preserved_detail"] if x["key"] == "m1|UP"][0]
    assert d["reason"] == "NOTHING_TO_CORRECT"
    assert st.calls == []


def test_an_unretired_or_unreadable_row_establishes_no_expectation():
    for state, reason in (
            ({"retired_markets": {}}, "MARKET_NOT_RETIRED"),
            ({"retired_markets": {"m1": {"up_qty": "2.9", "dn_qty": "0",
                                         "cost_total": None}}},
             "COST_TOTAL_UNREADABLE"),
            ({"retired_markets": {"m1": {"up_qty": "2.9", "dn_qty": "zzz",
                                         "cost_total": "2.90"}}},
             "OTHER_SIDE_QTY_UNREADABLE")):
        st = _RecordingStore(state=state)
        out = A.apply_cost_basis_corrections(st, _audit_payload())
        assert out["applied_detail"] == []
        assert any(d["reason"] == reason for d in out["preserved_detail"]), (
            reason, out["preserved_detail"])
        assert st.calls == []
