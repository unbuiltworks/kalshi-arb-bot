"""The bridge must never count a credit twice and never count one early
(OWNER 5588925913)."""
from __future__ import annotations

import json

from src import l5_realized_bridge as rb


def _market(qty="4.11", cost="7.9878", venue="-3.8778", side="NO"):
    return {"economics": {
        "realized_confirmed": True, "paying_side": side,
        "realized_usd_venue": venue,
        "derived": {"bought_no": qty, "bought_yes": "0", "cost_basis_usd": cost},
    }}


def test_the_venue_number_is_pure_settlement_cashflow():
    """LIVE, and the reason credits may be ADDED to it: 4.11 x $1.00 -
    $7.9878 = -$3.8778, exactly the venue's realizedPnl on
    aec-setkamecz-tomvoj-ticjir. No rebate term, no fee term."""
    r = rb.reproduce_realized(_market())
    assert r["reproduces"] is True
    assert r["difference_usd"] == "0.0000"


def test_a_venue_number_carrying_a_rebate_is_caught():
    """If the venue ever folds the fill-time rebate into realizedPnl, the
    bridge would silently double-count it. It must refuse instead."""
    m = _market(venue="-3.8478")            # $0.03 better than cashflow
    assert rb.reproduce_realized(m)["reproduces"] is False
    dc = rb.assert_no_double_count({"m1": m})
    assert dc["safe_to_add_credits"] is False
    out = rb.bridge(settlement_usd="-12.5082", maker_rebates_usd="2.13",
                    rewards={"credited_usd": "0"}, fees_usd="0",
                    non_trading={}, double_count=dc)
    assert out["actual_realized_net_usd"] is None
    assert "double-count" in out["refused"]


def test_skipped_and_pending_rewards_are_not_income():
    """The live vocabulary. $0.93 was quoted as earnings; not one row of it
    was paid."""
    rows = [{"reward": 0.31, "status": "SKIPPED"},
            {"reward": 0.07, "status": "SKIPPED"},
            {"reward": 0.10, "status": "PENDING"}]
    r = rb.rewards_by_status(rows)
    assert r["credited_usd"] == "0"
    assert r["accrued_but_not_credited_usd"] == "0.48"
    assert r["by_status"]["SKIPPED"]["n"] == 2


def test_an_unknown_status_is_an_accrual_not_income():
    """A status we have never seen is not a licence to book the money."""
    r = rb.rewards_by_status([{"reward": 5, "status": "SOMETHING_NEW"},
                              {"reward": 2}])
    assert r["credited_usd"] == "0"
    assert r["accrued_but_not_credited_usd"] == "7"
    assert "STATUS_ABSENT" in r["by_status"]


def test_a_paid_reward_does_count():
    r = rb.rewards_by_status([{"reward": "1.25", "status": "PAID"}])
    assert r["credited_usd"] == "1.25"


def test_the_goodwill_credit_is_quarantined_from_performance():
    """LIVE: a $10.00 sep5_outage goodwill remediation sits in the balance.
    Counting it would make the strategy look $10 better than it earned --
    the same error as double-counting a rebate, pointing the other way."""
    rows = [{"type": "ACTIVITY_TYPE_TRANSFER", "accountBalanceChange": {
        "amount": {"value": "10", "currency": "USD"},
        "createTime": "2026-09-06T18:06:46Z",
        "description": '{"kind":"remediation","event":"sep5_outage",'
                       '"action":"goodwill"}'}}]
    r = rb.split_non_trading_credits(rows)
    assert r["non_trading_credit_usd"] == "10"
    assert r["rows"][0]["kind"] == "remediation"


def test_the_bridge_adds_only_confirmed_money(tmp_path):
    p = tmp_path / "seq.json"
    p.write_text(json.dumps({"per_market": {"m1": _market()}}))
    rep = rb.build(str(p), settlement_usd="-12.5082",
                   maker_rebates_usd="2.1300", fees_usd="0.0000",
                   reward_rows=[{"reward": 0.93, "status": "SKIPPED"}],
                   credit_rows=[])
    b = rep["bridge"]
    assert b["C_confirmed_liquidity_rewards_usd"] == "0"
    assert b["actual_realized_net_usd"] == "-10.3782"
    assert b["memo_not_performance"][
        "rewards_accrued_not_credited_usd"] == "0.93"


def test_it_reads_and_never_acts():
    import inspect
    src = inspect.getsource(rb)
    body = src.split('"""', 2)[-1]
    for forbidden in ("place_limit_buy", "session_runner", "run_campaign(",
                      "Transport(", "requests.", "os.environ["):
        assert forbidden not in body, forbidden


def test_the_probe_image_carries_the_module_the_audit_imports():
    """A missing COPY in the probe Dockerfile is a lane-down crash on
    import, not a degraded report -- this repo learned that today. The
    audit's import is guarded AND the file ships."""
    docker = open("deploy/pmus_spring_l5/Dockerfile").read()
    assert "COPY src/l5_realized_bridge.py" in docker
    import inspect
    from src import pmus_incentive_audit as ia
    src = inspect.getsource(ia.earnings)
    i = src.index("l5_realized_bridge")
    assert "try:" in src[max(0, i - 300):i]
    assert "except Exception" in src[i:i + 400]
