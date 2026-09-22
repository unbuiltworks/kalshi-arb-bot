"""The venue's OWN maker-rebate payout records, and what they may be used for.

THE MISTAKE THIS FILE GUARDS. A 2026-09-19 survey probed seventeen guessed
paths, got 404 on all the rebate-shaped ones, and this programme recorded
that "every prediction-market rebate/reward path 404s". It had tried
`/v1/prediction-markets/rebates` and missed
`/v1/prediction-markets/maker-rebate/...` by ONE PATH SEGMENT. A 404 on a
guessed path is evidence about the guess, never about the venue.

THE RULE THAT DOES NOT MOVE. A payout row is money only when its status is
PAID. An accrual of our own, and a row in any other status, both stay out of
economics no matter how well they agree with each other.
"""
import ast
import json

from decimal import Decimal as D

from src.gemini_execution import maker_rebate_api as API
from src.gemini_execution import rebate_program as RP


def _payout(**kw):
    row = {"id": "po_1", "status": "PAID", "paid_at": "2026-09-22T21:00:00Z",
           "created_at": "2026-09-22T21:00:00Z", "total_rebate_usd": "0.08",
           "total_fill_count": 17, "total_volume_usd": "6.67"}
    row.update(kw)
    return row


# ============ the paths come from the spec, not from a guess

def test_the_paths_are_the_published_ones():
    assert API.RATES_PATH == "/v1/prediction-markets/maker-rebate/rates"
    assert API.PAYOUTS_PATH == "/v1/prediction-markets/maker-rebate/payouts"
    assert API.SUMMARY_PATH == "/v1/prediction-markets/maker-rebate/summary/total"
    assert "prediction-markets-spec/rewards" in API.SPEC_SOURCE


def test_the_module_can_reach_no_mutation_path():
    """Enumerated literals only; nothing order-, cancel- or transfer-shaped."""
    src = ast.parse(open("src/gemini_execution/maker_rebate_api.py").read())
    literals = {n.value for n in ast.walk(src)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and n.value.startswith("/v1/")}
    assert literals == {API.RATES_PATH, API.PAYOUTS_PATH, API.SUMMARY_PATH}
    for bad in ("/order", "/cancel", "/withdraw", "/transfer", "newOrder"):
        assert not any(bad in p for p in literals), bad
    # and no path is built from an input
    calls = [n for n in ast.walk(src) if isinstance(n, ast.Call)]
    assert not [c for c in calls
                if isinstance(c.func, ast.Attribute)
                and c.func.attr in ("format", "join")
                and isinstance(c.func.value, ast.Constant)
                and str(c.func.value.value).startswith("/v1/")]


# ============ PAID is the only thing that is money

def test_only_a_PAID_row_is_credited():
    got = API.parse_payouts({"payouts": [
        _payout(id="a", status="PAID", total_rebate_usd="0.08"),
        _payout(id="b", status="PENDING", total_rebate_usd="0.05"),
        _payout(id="c", status="PROCESSING", total_rebate_usd="0.03"),
    ]})
    assert got["readable"] and got["n"] == 3
    assert got["n_credited"] == 1
    assert got["credited_total_usd"] == "0.08"
    assert got["uncredited_total_usd"] == "0.08", "0.05 + 0.03, reported apart"
    paid = [r for r in got["rows"] if r["credited"]]
    assert "CREDITED" in paid[0]["labels"]
    assert all("NOT CREDITED" in r["labels"]
               for r in got["rows"] if not r["credited"])


def test_an_unreadable_payout_read_is_never_an_empty_one():
    got = API.parse_payouts(None)
    assert got["readable"] is False
    assert got["n"] is None and got["rows"] is None
    assert "not the same claim" in got["why"]
    empty = API.parse_payouts({"payouts": []})
    assert empty["readable"] is True and empty["n"] == 0, (
        "the venue answering with nothing is a different fact")


# ============ three comparisons, not one

def test_a_reconciliation_checks_dollars_fills_AND_volume():
    ours = {"n_eligible": 17, "would_pay_usd": "0.08",
            "qualifying_volume_usd": "6.67", "accrued_usd": "0.0833259"}
    r = API.reconcile(_payout(), ours)
    assert r["matched"] is True and r["verdict"] == "RECONCILED"
    assert r["credited"] is True
    assert r["deltas"] == {"rebate_usd": "0", "fill_count": 0,
                           "volume_usd": "0"}


def test_the_right_dollars_with_the_wrong_fill_count_does_not_reconcile():
    """A dollar agreement reached on a different set of fills is a
    coincidence, and it must read as one."""
    ours = {"n_eligible": 17, "would_pay_usd": "0.08",
            "qualifying_volume_usd": "6.67"}
    r = API.reconcile(_payout(total_fill_count=31), ours)
    assert r["matched"] is False and r["verdict"] == "DISAGREES"
    assert r["deltas"]["rebate_usd"] == "0"
    assert r["deltas"]["fill_count"] == 14
    assert "the venue is authoritative" in r["why"]


def test_a_matching_but_PENDING_payout_is_still_not_money():
    ours = {"n_eligible": 17, "would_pay_usd": "0.08",
            "qualifying_volume_usd": "6.67"}
    r = API.reconcile(_payout(status="PENDING", paid_at=None), ours)
    assert r["matched"] is True, "the numbers agree"
    assert r["credited"] is False, "and it is still not credited"
    assert "only on status PAID" in r["labels"]


def test_no_payout_row_does_not_decide_between_unpaid_and_ineligible():
    r = API.reconcile(None, {"n_eligible": 17, "would_pay_usd": "0.08"})
    assert r["verdict"] == "NO_PAYOUT_ROW"
    assert r["matched"] is None
    assert "cannot tell them apart" in r["why"]


# ============ our own side of the comparison

def test_our_qualifying_volume_counts_only_eligible_fills():
    """An out-of-band fill earns nothing and is not qualifying volume."""
    d = RP.daily_accrual([{"contracts": "1", "price": "0.50"},
                          {"contracts": "1", "price": "0.19"}])
    assert d["n_eligible"] == 1 and d["n_outside_band"] == 1
    assert D(d["qualifying_volume_usd"]) == D("0.50"), (
        "the 0.19 fill's notional must not appear")


def test_the_EP016_accrual_is_stated_for_reconciliation():
    """The numbers the first payout covering 21 Sep ET will be checked
    against. They are an ACCRUAL and are not economics."""
    import glob
    fills = []
    for f in sorted(glob.glob("analysis/gemini/cohort_window_*.json")):
        w = (json.loads(open(f).read()).get("window") or {})
        for e in (w.get("order_events") or []):
            if str(e.get("X") or "").upper() in ("FILLED", "PARTIALLY_FILLED"):
                assert e.get("m") is True, "every EP016 fill is maker"
                fills.append({"contracts": e.get("Z") or e.get("q"),
                              "price": e.get("L") or e.get("p")})
    assert len(fills) == 18
    d = RP.daily_accrual(fills)
    assert d["n_eligible"] == 17 and d["n_outside_band"] == 1
    assert d["would_pay_usd"] == "0.08"
    assert D(d["qualifying_volume_usd"]) == D("6.67")
    assert "never added to trading P&L" in d["labels"]


def test_nothing_here_can_be_added_to_trading_pnl():
    src = open("src/gemini_execution/maker_rebate_api.py").read()
    assert "never merged into trading" in src
    assert "only when a payout row says it was PAID" in src
