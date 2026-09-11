"""OWNER 5561465636: the first L5-SIZED rung (R3L) -- per-action size,
per-market cap, residual cap, daily stop and the hardened sized-rung
preflight all follow the ACTIVE rung; R1 / R2C are unchanged."""
from __future__ import annotations

import json
from decimal import Decimal as D

from src import pmus_incentive_audit as ia
from src import pmus_l5_continuous_session as cont
from src import pmus_l5_ramp_ladder as ladder
from src import pmus_micro_executor as micro
from src import pmus_spring_l5_session as base


def test_r3l_is_the_owner_rung_with_the_derived_numbers():
    r = ladder.rung("R3L")
    assert r["per_market_cap_usd"] == "7.50" and r["residual_units"] == 6
    assert r["markets_per_campaign"] == 3 and r["campaigns_per_day"] == 1
    # OWNER DECISION 5564173746: concurrency 1 at size
    assert r["concurrency"] == 1 and r["max_actions_per_session"] == 6
    assert r["target_notional_usd"] == "2.50" and r["max_leg_notional_usd"] == "3.25"
    assert r["daily_loss_stop_usd"] == "-15.00" and r["promote"] is None
    assert ladder.shell_version(r) == (
        "R3L/cap7.50/residual6/conc1/maker-only/fee-safe0.98/rest30s/action2.50")
    wc = ladder.worst_case(r)
    assert wc["per_market_usd"] == "7.50" and wc["per_campaign_usd"] == "22.50"
    assert wc["daily_loss_stop_usd"] == "-15.00"
    assert wc["per_day_bounded_by_daily_stop_usd"] == "22.50"   # stop + one in-flight market
    assert wc["max_concurrent_open_exposure_usd"] == "7.50"
    assert wc["per_action_target_usd"] == "2.50" and wc["per_leg_max_usd"] == "3.25"
    # R5FT appended 2026-09-11 (the at-the-touch canary), so the window moves.
    # Asserted as a TAIL rather than loosened to a membership check: the order
    # is the promotion order, and a rung silently inserted mid-ladder would
    # change what "the next rung" means.
    assert [x["id"] for x in ladder.ladder()][-5:] == [
        "R2C2", "R3L", "R4", "R5F", "R5FT"]


def test_r1_and_r2c_labels_stops_and_sizes_are_unchanged():
    assert ladder.shell_version(ladder.rung("R1")) == base.SAFETY_SHELL_VERSION
    assert ladder.shell_version(ladder.rung("R2C")) == (
        "R2C/cap0.15/residual6/conc1/maker-only/fee-safe0.98/rest30s")
    for rid in ("R1", "R2C"):
        r = ladder.rung(rid)
        assert ladder.daily_loss_stop(r) == D("-1.00")
        assert ladder.target_notional(r) == D("0.01")
        assert ladder.max_leg_notional(r) == D("0.02")
        assert ladder.worst_case(r)["daily_loss_stop_usd"] == "-1.00"
    assert ladder.worst_case(ladder.rung("R2C"))["per_day_bounded_by_daily_stop_usd"] == "0.90"


def test_active_rung_drives_the_per_action_size_and_the_daily_stop(monkeypatch):
    monkeypatch.delenv(micro.TARGET_NOTIONAL_ENV, raising=False)
    monkeypatch.delenv(micro.MAX_LEG_NOTIONAL_ENV, raising=False)

    class T:
        def request(self, method, path, body=None, base=None):
            return {"market": {"minimumTradeQty": 0.01, "orderPriceMinTickSize": 0.01}}
    ladder.set_active(None)
    r1 = micro.market_micro_spec(T(), "m", D("0.45"))
    assert r1["qty"] == D("0.02") and r1["target_notional"] == D("0.01")
    assert base.daily_loss_stop() == D("-1.00")
    ladder.set_active("R3L")
    r3 = micro.market_micro_spec(T(), "m", D("0.45"))
    assert r3["qty"] == D("5.55") and r3["max_leg_notional"] == D("3.25")
    assert r3["entry_notional"] == D("2.4975")
    assert r3["worst_complement_notional"] == D("3.0525")
    assert micro.market_micro_spec(T(), "m", D("0.50"))["qty"] == D("5.00")
    assert micro.market_micro_spec(T(), "m", D("0.65"))["qty"] == D("3.84")
    assert micro.market_micro_spec(T(), "m", D("0.35"))["qty"] == D("5.00")
    assert base.daily_loss_stop() == D("-15.00")
    assert base.session_notional_cap() == D("7.50") and base.max_residual_units() == 6
    assert base.safety_shell_version().endswith("/action2.50")
    # the rebate at the rung's size is NOT rounded away at any band price
    for px in ("0.35", "0.45", "0.50", "0.65"):
        assert ia.rebate_at(r3["qty"], D(px))["credited_after_rounding_usd"] in ("0.01", "0.02")
    assert ia.rebate_at(D("0.02"), D("0.45"))["credited_after_rounding_usd"] == "0.00"


def _t(power="110.60", fail_balances=False):
    class T:
        def request(self, method, path, body=None, base=None):
            if path == "/v1/account/balances":
                if fail_balances:
                    raise RuntimeError("HTTP 503")
                return {"balances": [{"buyingPower": power}]}
            if path.startswith("/v1/portfolio/activities"):
                return {"activities": [], "eof": True}
            if path.startswith("/v1/portfolio/orders"):
                return {"orders": []}
            if path.startswith("/v1/portfolio/positions"):
                return {"positions": []}
            return {}
    return T()


def test_sized_rung_preflight_fails_closed_on_power_identity_and_reconcile(monkeypatch):
    monkeypatch.setattr(cont, "settle_held", lambda t, s: {
        "status": "REALIZED", "settled": True, "settled_by": "POSITION_RESOLUTION",
        "realized_confirmed": True, "realized_usd": "0.01",
        "venue_qty_total": "0.04", "ledger_qty_total": "0.04"})
    monkeypatch.setattr(cont, "sweep_orphan_orders",
                        lambda t: {"seen": 0, "cancelled": [], "foreign": [], "errors": []})
    monkeypatch.setattr(cont, "held_market", lambda t: None)
    monkeypatch.setattr(cont, "unsettled_slugs", lambda: [])
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    monkeypatch.delenv("RAILWAY_DEPLOYMENT_ID", raising=False)
    r3l = ladder.rung("R3L")
    # unsized rung (R2C): the new checks are recorded, never blocking
    pre = cont.continuation_preflight(_t(), {"m"}, {"m"}, rung=ladder.rung("R2C"))
    assert pre["ok"] and pre["checks"]["deployment_identity"]["blocking"] is False
    assert pre["checks"]["buying_power"]["required_per_campaign_worst_case_usd"] == "0.90"
    # sized rung without the platform identity -> blocked
    pre = cont.continuation_preflight(_t(), {"m"}, {"m"}, rung=r3l)
    assert not pre["ok"] and pre["failed"] == ["deployment_identity"]
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abc123")
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "dep-1")
    pre = cont.continuation_preflight(_t(), {"m"}, {"m"}, rung=r3l)
    assert pre["ok"] and pre["checks"]["buying_power"]["buying_power_usd"] == "110.60"
    assert pre["checks"]["buying_power"]["required_per_campaign_worst_case_usd"] == "22.50"
    assert pre["checks"]["deployment_identity"]["commit"] == "abc123"
    assert pre["checks"]["rung"]["worst_case"]["per_campaign_usd"] == "22.50"
    # insufficient buying power / unreadable balances -> blocked
    assert cont.continuation_preflight(_t(power="20"), {"m"}, {"m"}, rung=r3l)["failed"] == ["buying_power"]
    assert cont.continuation_preflight(_t(fail_balances=True), {"m"}, {"m"}, rung=r3l)["failed"] == ["buying_power"]
    # ledger != venue quantity -> blocked for the sized rung only
    monkeypatch.setattr(cont, "settle_held", lambda t, s: {
        "status": "REALIZED", "settled": True, "settled_by": "POSITION_RESOLUTION",
        "realized_confirmed": True, "realized_usd": "0.01",
        "venue_qty_total": "0.04", "ledger_qty_total": "0.06"})
    assert cont.continuation_preflight(_t(), {"m"}, {"m"}, rung=r3l)["failed"] == ["ledger_reconciled"]
    assert cont.continuation_preflight(_t(), {"m"}, {"m"}, rung=ladder.rung("R2C"))["ok"]
    # cancel path: an orphan-sweep error blocks the sized rung
    monkeypatch.setattr(cont, "settle_held", lambda t, s: {
        "status": "REALIZED", "settled": True, "settled_by": "POSITION_RESOLUTION",
        "realized_confirmed": True, "realized_usd": "0.01",
        "venue_qty_total": "0.04", "ledger_qty_total": "0.04"})
    monkeypatch.setattr(cont, "sweep_orphan_orders",
                        lambda t: {"seen": 0, "cancelled": [], "foreign": [], "errors": ["HTTP 500"]})
    pre = cont.continuation_preflight(_t(), {"m"}, {"m"}, rung=r3l)
    assert set(pre["failed"]) == {"open_orders", "cancel_path"}


def test_r3l_approval_is_queued_behind_r2c_and_consumed_in_order():
    approvals = cont.load_campaign_approvals()
    ids = [a["campaign_id"] for a in approvals]
    # ORDER, not POSITION. This asserted a tail window, so every approval
    # the owner added afterwards broke a test about R3L's place in the
    # queue -- twice now. What the rung cares about is that R3L is queued
    # behind R2C2 and ahead of R4, which is true wherever the tail ends.
    assert [i for i in ids if i in
            ("R2C-RUN-2026-09-06", "R2C2-RUN-2026-09-06", "R3L-RUN-2026-09-06",
             "R4-RUN-2026-09-07", "R5F-RUN-2026-09-07")] == [
        "R2C-RUN-2026-09-06", "R2C2-RUN-2026-09-06", "R3L-RUN-2026-09-06",
        "R4-RUN-2026-09-07", "R5F-RUN-2026-09-07"]
    r3l = next(a for a in approvals if a["campaign_id"] == "R3L-RUN-2026-09-06")
    assert r3l["rung"] == "R3L" and r3l["stop_rule"] == "rung_markets"
    assert r3l["shell"] == ladder.shell_version(ladder.rung("R3L"))
    assert "5561465636" in r3l["source"]
    # R1 running: R2C is next; R2C running: R3L is next; R3L done: nothing
    assert cont.approved_continuation("R1-EVIDENCE-CONTINUATION-2026-09-06",
                                      ["R1-INITIAL"])["campaign_id"] == "R2C-RUN-2026-09-06"
    # R2C2 (the concurrency rung) is consumed first, then the sized rung
    assert cont.approved_continuation("R2C-RUN-2026-09-06",
                                      ["R1-INITIAL", "R1-EVIDENCE-CONTINUATION-2026-09-06"]
                                      )["campaign_id"] == "R2C2-RUN-2026-09-06"
    assert cont.approved_continuation("R2C2-RUN-2026-09-06",
                                      ["R1-INITIAL", "R1-EVIDENCE-CONTINUATION-2026-09-06",
                                       "R2C-RUN-2026-09-06"]
                                      )["campaign_id"] == "R3L-RUN-2026-09-06"
    # OWNER 5567269295: R4 is queued behind R3L and consumed only once R3L
    # is the completed campaign; after R4 there is nothing (no auto R5)
    assert cont.approved_continuation("R3L-RUN-2026-09-06",
                                      ["R1-INITIAL", "R1-EVIDENCE-CONTINUATION-2026-09-06",
                                       "R2C-RUN-2026-09-06",
                                       "R2C2-RUN-2026-09-06"])["campaign_id"] == "R4-RUN-2026-09-07"
    # OWNER 5574762432: R5F is queued behind R4 (consumed live 2026-09-07
    # 19:21:39Z); after R5F there is nothing (no automatic promotion)
    assert cont.approved_continuation("R4-RUN-2026-09-07",
                                      ["R1-INITIAL", "R1-EVIDENCE-CONTINUATION-2026-09-06",
                                       "R2C-RUN-2026-09-06", "R2C2-RUN-2026-09-06",
                                       "R3L-RUN-2026-09-06"])["campaign_id"] == "R5F-RUN-2026-09-07"
    # OWNER 2026-09-11 ("go 3"): the R5FT canary is queued behind R5F. It
    # is the LAST row, so after it there is still nothing automatic.
    after_r5f = ["R1-INITIAL", "R1-EVIDENCE-CONTINUATION-2026-09-06",
                 "R2C-RUN-2026-09-06", "R2C2-RUN-2026-09-06",
                 "R3L-RUN-2026-09-06", "R4-RUN-2026-09-07"]
    assert cont.approved_continuation(
        "R5F-RUN-2026-09-07", after_r5f)["campaign_id"] == "R5FT-CANARY-2026-09-11"
    assert cont.approved_continuation(
        "R5FT-CANARY-2026-09-11", after_r5f + ["R5F-RUN-2026-09-07"]) is None
    json.dumps(approvals)   # serialisable, as the campaign logs it
