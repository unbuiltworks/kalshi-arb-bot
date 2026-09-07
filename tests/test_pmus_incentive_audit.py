"""OWNER 5561142758 #2/#3: the read-only incentive audit reads the venue's
own records (earnings endpoint, activity credits, trade fee fields,
balances, published programs) and labels observed vs published vs
estimated; the campaign runs it at boot and every 6 h, contained."""
from __future__ import annotations

import json
from decimal import Decimal as D

from src import pmus_incentive_audit as ia
from src import pmus_l5_continuous_session as cont
from src import pmus_q1_executor as q1


class FakeT:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def request(self, method, path, body=None, base=q1.API):
        self.calls.append((method, path, base))
        key = (base, path)
        if key in self.routes:
            v = self.routes[key]
            if isinstance(v, Exception):
                raise v
            return v
        raise RuntimeError(f"HTTP 404 {path}")


TRADE = {"id": "tr1", "marketSlug": "m1", "price": {"value": "0.45"}, "qty": "0.02",
         "state": "TRADE_STATE_NEW", "isAggressor": False, "cost": {"value": "0.009"},
         "passiveExecution": {"orderId": "o1", "side": "OUTCOME_SIDE_YES",
                              "commissionNotional": {"value": "0.00"}},
         "aggressorExecution": {"orderId": "o2"}, "createTime": "2026-09-06T16:00:00Z"}


def _routes(earnings_rows=None, credit_rows=(), programs=None, earnings_err=False):
    acts = [{"type": "ACTIVITY_TYPE_TRADE", "time": "2026-09-06T16:00:00Z", "trade": TRADE},
            {"type": "ACTIVITY_TYPE_POSITION_RESOLUTION", "time": "2026-09-06T15:00:00Z",
             "positionResolution": {"marketSlug": "m0"}},
            {"type": "ACTIVITY_TYPE_ACCOUNT_DEPOSIT", "time": "2026-08-14T03:26:06Z",
             "accountDeposit": {"amount": {"value": "50"}}},
            *credit_rows]
    r = {(q1.API, "/v1/portfolio/activities"): {"activities": acts, "eof": True},
         (q1.API, "/v1/account/balances"): {"balances": [{"buyingPower": {"value": "49.5"},
                                                          "pendingRebates": {"value": "0.00"}}]},
         (q1.GATEWAY, "/v1/incentives"): {"programs": programs if programs is not None else [
             {"marketSlug": "aqc-tennis-x", "category": "", "instrumentState": "INSTRUMENT_STATE_OPEN",
              "timePeriods": [{"programId": "us_open_womens_20260901", "programType": "liquidityProgram",
                               "rewardPool": 2500, "status": "active", "discountFactor": 0.9,
                               "targetSize": 10000, "period": "live", "start": "2026-09-06T00:00:00Z"}]},
             {"marketSlug": "m1", "category": "",
              "timePeriods": [{"programId": "us_open_womens_20260901", "programType": "liquidityProgram",
                               "rewardPool": 2500, "status": "ended", "discountFactor": 0.9,
                               "targetSize": 10000, "period": "live", "start": "2026-09-05T00:00:00Z"}]},
             {"marketSlug": "cfb-1", "category": "",
              "timePeriods": [{"programId": "cfb_live", "programType": "liquidityProgram",
                               "rewardPool": 1200, "status": "active", "targetSize": 5000}]}]}}
    if earnings_err:
        r[(q1.API, "/v1/incentives/earnings")] = RuntimeError("HTTP 404 not found")
    else:
        r[(q1.API, "/v1/incentives/earnings")] = {"earnings": earnings_rows or []}
    return r


def _ledger(tmp_path, monkeypatch):
    monkeypatch.setenv(q1.STATE_ENV, str(tmp_path))
    rows = [{"t": "2026-09-06T16:00:00+00:00", "kind": "filled", "slug": "m1",
             "price": "0.45", "qty": "0.02", "side": "YES", "stage": "spring_open_1"},
            {"t": "2026-09-06T16:01:00+00:00", "kind": "filled", "slug": "m2",
             "price": "0.55", "qty": "0.02", "side": "NO", "stage": "spring_complete_2"}]
    (tmp_path / "ledger.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def test_rebate_arithmetic_matches_the_published_formula_and_cent_rounding():
    ours = ia.rebate_at(D("0.02"), D("0.45"))
    assert ours["raw_usd"] == "0.000062" and ours["credited_after_rounding_usd"] == "0.00"
    l5 = ia.rebate_at(D("10"), D("0.45"))
    assert l5["raw_usd"] == "0.030938" and l5["credited_after_rounding_usd"] == "0.03"
    assert ia.min_qty_for_nonzero_rebate(D("0.45")) == "1.62"


def test_audit_reads_every_venue_source_and_labels_observed_vs_published(monkeypatch, tmp_path):
    _ledger(tmp_path, monkeypatch)
    t = FakeT(_routes())
    out = ia.incentive_audit(t)
    assert out["our_filled_markets"] == ["m1", "m2"] and out["our_fills"] == 2
    e = out["earnings"]
    assert e["ok"] and e["endpoint"] == "/v1/incentives/earnings" and e["rows"] == 0
    a = out["activities"]
    assert a["types"] == {"ACTIVITY_TYPE_TRADE": 1, "ACTIVITY_TYPE_POSITION_RESOLUTION": 1,
                          "ACTIVITY_TYPE_ACCOUNT_DEPOSIT": 1}
    assert a["credit_rows_n"] == 0 and "_rows" not in a
    tf = out["trade_fields"]
    assert tf["trades"] == 1
    assert "passiveExecution.commissionNotional" in tf["fee_or_rebate_key_paths"]
    assert tf["sample_row"]["id"] == "tr1"
    assert out["balances"]["ok"] and any("pendingRebates" in p for p in out["balances"]["fee_or_rebate_key_paths"])
    p = out["programs"]
    assert p["ok"] and p["programs"] == 3 and p["markets_with_active_period"] == 2
    assert p["our_markets_in_programs"][0]["market"] == "m1"
    assert p["our_markets_in_programs"][0]["periods"][0]["programId"] == "us_open_womens_20260901"
    assert p["our_markets_in_programs"][0]["periods"][0]["status"] == "ended"
    prog = {b["program_id"]: b for b in p["by_program"]}
    assert prog["us_open_womens_20260901"]["markets"] == 2
    assert prog["us_open_womens_20260901"]["markets_active"] == 1
    assert prog["us_open_womens_20260901"]["target_size"] == 10000
    assert prog["cfb_live"]["reward_pool"] == 1200
    est = out["estimated_rebate_on_our_fills"]
    assert est["fills"] == 2 and est["rebate_after_per_fill_rounding_usd"] == "0.00"
    assert "ESTIMATED" in est["labels"]
    v = out["verdict"]
    assert v["observed_credits_earnings_endpoint"] == "0 rows, total None"
    assert v["observed_credit_rows_in_activities"] == 0
    assert v["trade_rows_carry_fee_or_rebate_fields"] is True
    assert v["published_programs_covering_our_markets"] == 1
    assert v["rebate_at_our_executed_size_after_rounding"] == "0.00"
    # read-only: only GETs, no order paths
    assert all(m == "GET" for m, _, _ in t.calls)


def test_observed_credits_are_counted_from_the_venue_rows(monkeypatch, tmp_path):
    _ledger(tmp_path, monkeypatch)
    rows = [{"programId": "us_open_womens_20260901", "amount": {"value": "1.25"},
             "period": "2026-09-05"},
            {"programId": "maker_rebate", "amount": "0.03"}]
    credit = [{"type": "ACTIVITY_TYPE_INCENTIVE_REWARD", "time": "2026-09-06T12:00:00Z",
               "reward": {"amount": {"value": "1.25"}}}]
    out = ia.incentive_audit(FakeT(_routes(earnings_rows=rows, credit_rows=credit)))
    assert out["earnings"]["rows"] == 2 and out["earnings"]["total_usd"] == "1.28"
    assert out["earnings"]["by_program"] == {"us_open_womens_20260901": 1, "maker_rebate": 1}
    assert out["activities"]["credit_rows_n"] == 1
    assert out["activities"]["credit_rows"][0]["type"] == "ACTIVITY_TYPE_INCENTIVE_REWARD"
    assert out["verdict"]["observed_credits_earnings_endpoint"] == "2 rows, total 1.28"


def test_unavailable_endpoints_are_reported_not_fatal(monkeypatch, tmp_path):
    _ledger(tmp_path, monkeypatch)
    out = ia.incentive_audit(FakeT(_routes(earnings_err=True, programs=[])))
    assert out["earnings"]["ok"] is False and "404" in out["earnings"]["error"]
    assert "unavailable" in out["verdict"]["observed_credits_earnings_endpoint"]
    assert out["programs"]["programs"] == 0 and out["verdict"]["published_programs_covering_our_markets"] == 0


def test_campaign_hook_is_contained_and_logs_once(monkeypatch):
    logged = []
    monkeypatch.setattr(cont.incentives, "incentive_audit", lambda t: {"verdict": {"x": 1}})
    out = cont.run_incentive_audit(object(), lambda kind, **kw: logged.append((kind, kw)))
    assert out == {"verdict": {"x": 1}} and logged[0][0] == "incentive_audit"
    monkeypatch.setattr(cont.incentives, "incentive_audit",
                        lambda t: (_ for _ in ()).throw(RuntimeError("venue down")))
    out = cont.run_incentive_audit(object(), lambda kind, **kw: logged.append((kind, kw)))
    assert out["ok"] is False and "venue down" in out["error"]
    assert cont.run_incentive_audit(None, lambda kind, **kw: logged.append((kind, kw))) is None
    assert len(logged) == 2


def test_credited_on_fills_reports_what_the_venue_actually_paid():
    """LIVE 2026-09-07 (R3L): fills now clear the 1.62-share rounding
    floor, so 'did the venue credit anything' is answerable. The sum is
    the venue's own signed number; nothing is called a rebate on the
    strength of the published formula alone."""
    def _tr(tid, qty, passive="0.0000", aggressor="0.0000"):
        return {"trade": {"id": tid, "marketSlug": "m1", "qtyDecimal": qty,
                          "passiveExecution": {"commissionNotionalCollected": {"value": passive}},
                          "aggressorExecution": {"commissionNotionalCollected": {"value": aggressor}}}}
    out = ia.credited_on_fills([_tr("a", "0.0200"), _tr("b", "5.1500"),
                                _tr("c", "4.3300", passive="-0.0200")])
    assert out["trades"] == 3
    # only the two sized fills clear the floor; the 0.02-share one does not
    assert out["trades_at_or_above_rebate_floor"] == 2
    assert out["rebate_floor_shares"] == "1.62"
    assert D(out["commission_notional_total_usd"]) == D("-0.02")
    assert len(out["trades_with_nonzero_commission"]) == 1
    assert out["trades_with_nonzero_commission"][0]["id"] == "c"
    assert out["trades_with_nonzero_commission"][0]["at_or_above_floor"] is True
    # an all-zero book reports zero rather than inferring the formula
    zero = ia.credited_on_fills([_tr("a", "5.0000"), _tr("b", "5.0000")])
    assert D(zero["commission_notional_total_usd"]) == 0
    assert zero["trades_with_nonzero_commission"] == []


def test_audit_verdict_carries_the_credited_number(monkeypatch, tmp_path):
    _ledger(tmp_path, monkeypatch)
    out = ia.incentive_audit(FakeT(_routes()))
    assert out["credited_on_fills"]["trades"] == 1
    assert D(out["verdict"]["venue_credited_on_our_fills_usd"]) == 0
    assert out["verdict"]["our_fills_at_or_above_rebate_floor"] == 0
