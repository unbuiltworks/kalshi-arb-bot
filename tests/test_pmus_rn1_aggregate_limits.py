"""Aggregate limits across several markets: some only CARRY inventory
(an unpaired leg, at most a completion bid), others ACCUMULATE at the touch
(PMUS_RN1_TOUCH_EV on). Run with PMUS_RN1_SLOTS_COUNT_INVENTORY on (1, the
default) and off (0, the proposed activation setting).

Owner 2026-11-27: with inventory-only markets no longer holding a quoting
slot, every dollar they carry must still count in the per-market,
campaign spend/unpaired, at-risk (fills + resting + in-flight), account
and loss limits; completions are never refused; nothing forces a
completion. Includes simultaneous fills across active and inventory-only
markets, uncertain submissions and a restart (reservations rebased to
uncertain). No limit or default changes here: the caps are the tests'.
"""
import datetime as dt
import time
from decimal import Decimal as D

import pytest

from src import l5_account_risk as AR
from src import pmus_pilot_pnl as PP
from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_two_sided import FakeVenue

INV1, INV2 = "aec-atp-i1-x-2026-11-27", "aec-utr-i2-x-2026-11-27"
ACT1, ACT2, ACT3 = ("aec-atp-a1-x-2026-11-27", "aec-atpdb-a2-x-2026-11-27",
                    "aec-setkameua-a3-x-2026-11-27")
LIMIT = D("6.00")                       # account drawdown limit (the test's)


def _doc(used, mx=str(LIMIT)):
    now = dt.datetime.now(dt.timezone.utc)
    return {"as_of_utc": now.isoformat(),
            "account_risk": {
                "readable": True, "utc_day": now.strftime("%Y-%m-%d"),
                "utc_day_realized_usd": "0", "daily_halt_usd": "50",
                "lifetime_realized_usd": "0",
                "cumulative_loss_ceiling_usd": "75", "retired_markets": [],
                "portfolio_drawdown": {"state": "ON", "drawdown_usd": used,
                                       "max_drawdown_usd": mx}}}


@pytest.fixture(params=[True, False], ids=["slots_count_inv_1",
                                           "slots_count_inv_0"])
def z(request, tmp_path, monkeypatch):
    # explicit: these tests hold whatever the import-time flags are
    for name in ("RN1_TOUCH_EV", "RN1_REWARD_CALIBRATION", "RN1_MARGINAL_EV",
                 "RN1_MARGINAL_EV_NO_FAIR", "RN1_FIT_ALL_LEVELS",
                 "RN1_NET_RANK", "RN1_JOIN_TOUCH", "RN1_LIVE_FIRST",
                 "RN1_DURATION_RANK", "RN1_SELECT_NO_CHURN"):
        monkeypatch.setattr(M, name, False)
    monkeypatch.setattr(M, "RN1_REWARD_CAL_TABLE", "")
    monkeypatch.setattr(M, "RN1_JOIN_MAX_RANGE",
                        {"": M.RN1_JOIN_MAX_RANGE_USD})
    monkeypatch.setattr(M, "RN1_SLOTS_COUNT_INVENTORY", request.param)
    monkeypatch.setattr(M, "RN1_TOUCH_EV", True)
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setenv(M.ENABLED_ENV, "YES")
    monkeypatch.delenv(M.SUBMISSION_LOCK_ENV, raising=False)
    for mod, name, val in (
            (R, "QTY", D("1")), (R, "PAIR_CAP", D("0.97")),
            (R, "PER_MARKET_USD", D("3")), (R, "RESIDUAL_CAP_USD", D("1.5")),
            (R, "TOTAL_SPEND_USD", D("8")),
            (R, "TOTAL_UNMATCHED_USD", D("4")),
            (M, "RN1_PREGAME_QTY", None), (M, "RN1_LIVE_QTY", None),
            (M, "TOTAL_CAP", D("50")), (M, "MIN_TOUCH_QTY", D("0")),
            (M, "ACCOUNT_RISK_ON", True), (M, "ACCOUNT_RISK_TTL_S", 10.0),
            (M, "ACCOUNT_RISK_RESTING_IN_LEDGER", False),
            (M, "PILOT_LOSS_USD", D("5")), (M, "MAX_MARKETS", 2)):
        monkeypatch.setattr(mod, name, val)
    monkeypatch.setenv(AR.RISK_URL_ENV, "http://risk/l5/live_orders.json")
    monkeypatch.setenv(AR.ACCOUNT_CAP_ENV, "40")
    monkeypatch.setenv(AR.ACCOUNT_LANES_ENV, "2")
    box = {"doc": _doc("0.50")}
    monkeypatch.setattr(AR, "fetch_json", lambda url, timeout=3.0: box["doc"])
    v = FakeVenue()
    v.states = {}
    v.bid, v.ask = D("0.48"), D("0.50")
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    e.since = None
    _meter(e)
    # two markets carrying inventory only: an unpaired YES leg each
    for s_, px in ((INV1, "0.60"), (INV2, "0.55")):
        mk = e.b.m(s_)
        mk["pos"]["YES"], mk["cost"]["YES"] = D("1"), D(px)
        mk["orders"]["seed-" + s_] = {"side": "YES", "px": px, "qty": "1",
                                      "filled": "1", "open": False,
                                      "stage": "rn1_open",
                                      "placed_at": time.time()}
    return e, v, box, request.param


def _meter(e, net="0", resid="0"):
    e._pilot = {"realized_net_usd": net, "open_residual_worst_usd": resid,
                "unconfirmed": False}
    e._pilot_at = time.monotonic()


def sent(v):
    return [(o["slug"], o["side"], str(o["px"])) for o in v.orders.values()]


def fill(e, slug, side, px, qty="1"):
    mk = e.b.d["markets"][slug]
    for o in mk["orders"].values():
        if o["open"] and o["side"] == side and D(o["px"]) == D(px):
            o["filled"], o["open"] = o["qty"], False
            mk["pos"][side] = D(str(mk["pos"][side])) + D(qty)
            mk["cost"][side] = D(str(mk["cost"][side])) + D(px) * D(qty)
            return
    raise AssertionError("no such resting bid")


def touch(e, slug, side="YES"):
    """An opening at the touch (YES 0.48 / NO 0.50), as touch EV places."""
    px = D("0.48") if side == "YES" else D("0.50")
    e.place(slug, side, px, D("1"), "rn1_open")


def exposure_ok(e):
    x = e.rn1_campaign_exposure()
    return x["spend"] <= R.TOTAL_SPEND_USD + D("0.000001") and \
        x["unmatched"] <= R.TOTAL_UNMATCHED_USD + D("0.000001")


# ------------------------------------------------- slots vs accounting
def test_inventory_only_markets_never_leave_the_accounting(z):
    e, v, box, counts = z
    claim = e.slot_claimants()
    assert (INV1 in claim and INV2 in claim) is counts
    x = e.rn1_campaign_exposure()
    # 1 x 0.60 + 1 x 0.55 unpaired, each with its completion reserve
    assert x["unmatched"] == D("1.15")
    assert x["spend"] == D("1.15") + (D("0.97") - D("0.60")) + \
        (D("0.97") - D("0.55"))
    assert PP.book_worst_case(e.b.d["markets"], None) == D("-1.15")
    assert e.b.total_unmatched() == D("1.15")


def test_per_market_limits_hold_for_an_inventory_only_market(z, capsys):
    e, v, *_ = z
    e.place(INV1, "YES", D("0.48"), D("1"), "rn1_extend")   # 1.08 unpaired
    assert len(sent(v)) == 1
    e.place(INV1, "YES", D("0.47"), D("1"), "rn1_extend")   # 1.55 > 1.5
    assert len(sent(v)) == 1
    assert "per_market_unpaired" in capsys.readouterr().out


def test_campaign_caps_hold_while_active_markets_accumulate_at_the_touch(
        z, capsys):
    """Inventory-only markets take 1.15 of the 4.00 campaign unpaired
    room; touch openings across three active markets stop at the cap."""
    e, v, box, _ = z
    box["doc"] = _doc("0")
    for _round in range(4):
        for s_ in (ACT1, ACT2, ACT3):
            touch(e, s_)
            assert exposure_ok(e)
    n = len(sent(v))
    assert 0 < n < 12
    out = capsys.readouterr().out
    assert "campaign_unpaired" in out or "per_market" in out or \
        "campaign_spend" in out or "ACCOUNT_RISK" in out
    assert exposure_ok(e)


def test_simultaneous_fills_across_active_and_inventory_markets(z):
    """Everything resting fills at once in several markets (active ones at
    the touch, and an extension on an inventory-only market). Nothing more
    is accepted past any cap; a completion still goes."""
    e, v, box, _ = z
    box["doc"] = _doc("0")
    touch(e, ACT1)
    touch(e, ACT2)
    e.place(INV2, "YES", D("0.47"), D("1"), "rn1_extend")
    for slug, side, px in sent(v):
        fill(e, slug, side, px)
    assert exposure_ok(e)
    before = len(sent(v))
    for _ in range(6):
        for s_ in (ACT1, ACT2, ACT3, INV1, INV2):
            e.place(s_, "YES", D("0.47"), D("1"), "rn1_extend")
            assert exposure_ok(e)
    assert e.b.total_unmatched() <= R.TOTAL_UNMATCHED_USD
    for s_ in e.b.d["markets"]:
        assert R.unmatched_usd(e.b.m(s_)["pos"], e.b.m(s_)["cost"]) <= \
            R.RESIDUAL_CAP_USD + D("0.000001")
    # completions are never refused by the exposure caps
    n = len(sent(v))
    e.place(INV1, "NO", D("0.37"), D("1"), "two_sided_complete")
    assert len(sent(v)) == n + 1
    assert len(sent(v)) >= before


def test_in_flight_and_uncertain_reservations_count_everywhere(z):
    e, v, box, _ = z
    box["doc"] = _doc("0")
    real = v.place

    def lost(slug, side, px, qty, stage, post_only=True):
        if slug == ACT1:
            raise TimeoutError("no answer")
        return real(slug, side, px, qty, stage, post_only)
    v.place = lost
    with pytest.raises(TimeoutError):
        touch(e, ACT1)
    v.place = real
    (r,) = e.reserved_orders()
    assert r["state"] == "uncertain" and r["slug"] == ACT1
    x = e.rn1_campaign_exposure(exclude=ACT2)
    assert x["unmatched"] == D("1.15") + D("0.48")
    assert e.own_resting_worst() == D("0.48")
    assert e.own_exposure_usd() >= D("0.48")


def test_a_restart_rebases_reservations_and_every_limit_still_holds(
        z, tmp_path, monkeypatch):
    e, v, box, counts = z
    box["doc"] = _doc("0")
    touch(e, ACT1)
    v.place = lambda *a_, **k: (_ for _ in ()).throw(TimeoutError("lost"))
    with pytest.raises(TimeoutError):
        touch(e, ACT2)
    e.b.save()
    v2 = FakeVenue()
    v2.states = {}
    v2.bid, v2.ask = D("0.48"), D("0.50")
    e2 = M.Engine(v2, M.Book(tmp_path / "s.json"))
    e2.since = None
    _meter(e2)
    res = e2.reserved_orders()
    assert [r["state"] for r in res] == ["uncertain"]     # rebased
    x = e2.rn1_campaign_exposure()
    assert x["unmatched"] == D("1.15") + D("0.48") + D("0.48")
    # fill the inventory up to the campaign cap: nothing passes it
    for _ in range(8):
        for s_ in (ACT1, ACT3, INV1, INV2):
            e2.place(s_, "YES", D("0.47"), D("1"), "rn1_extend")
            assert exposure_ok(e2)
    # a completion in an inventory-only market still goes
    n = len(v2.orders)
    e2.place(INV2, "NO", D("0.40"), D("1"), "two_sided_complete")
    assert len(v2.orders) == n + 1


def test_the_account_limit_holds_across_all_markets(z):
    e, v, box, _ = z
    box["doc"] = _doc("4.60")                    # 1.40 of room
    e.account_risk()
    for s_ in (ACT1, ACT2, ACT3, INV1, INV2):
        e.place(s_, "YES", D("0.48"), D("1"), "rn1_extend"
                if s_.startswith("aec-atp-i") or s_.startswith("aec-utr-i")
                else "rn1_open")
    used = D(box["doc"]["account_risk"]["portfolio_drawdown"]
             ["drawdown_usd"])
    assert used + e.own_resting_worst() <= LIMIT
    assert len(sent(v)) == 2


@pytest.mark.parametrize("stop", ["latched", "at_risk_book", "meter_stale",
                                  "daily"])
def test_loss_stops_refuse_openings_everywhere_but_not_completions(
        z, monkeypatch, stop, capsys):
    e, v, box, _ = z
    box["doc"] = _doc("0")
    if stop == "latched":
        e.b.d["pilot_loss_stop"] = {"t": "x", "net": "-5"}
    elif stop == "at_risk_book":
        # inventory-only legs alone take the book's worst case to the stop
        for s_ in (INV1, INV2):
            mk = e.b.m(s_)
            mk["pos"]["YES"], mk["cost"]["YES"] = D("4"), D("2.60")
    elif stop == "meter_stale":
        e._pilot_at = time.monotonic() - M.PILOT_METER_MAX_AGE_S - 1
    else:
        monkeypatch.setattr(M.Engine, "daily_stopped", lambda self: True)
    for s_ in (ACT1, ACT2, INV1, INV2):
        e.place(s_, "YES", D("0.30"), D("1"), "rn1_open")
    assert sent(v) == []
    out = capsys.readouterr().out
    assert ("campaign_loss_stop" in out) or ("daily_loss_stop" in out)
    e.place(INV1, "NO", D("0.30"), D("1"), "two_sided_complete")
    assert sent(v) == [(INV1, "NO", "0.30")]


def test_no_forced_completion_in_an_inventory_only_market(z):
    """No acceptable completion price: the controller rests nothing above
    the pair cap and sends nothing as a taker."""
    e, v, box, _ = z
    box["doc"] = _doc("0")
    v.bid, v.ask = D("0.55"), D("0.56")       # NO touch 0.44: 0.60+0.44>0.97
    e.step_market(INV1)
    for o in v.orders.values():
        if o["slug"] == INV1 and o["side"] == "NO" and o["open"]:
            assert D(str(o["px"])) + D("0.60") <= R.PAIR_CAP
    assert not getattr(v, "taker", [])


def test_controller_at_the_touch_respects_campaign_caps(z, monkeypatch):
    """step_market with touch EV choosing the touch in every active
    market, inventory-only markets holding inventory: every bid that rests
    fits the campaign caps."""
    from src import pmus_sports_rewards as RW
    e, v, box, _ = z
    box["doc"] = _doc("0")
    monkeypatch.setattr(R, "EDGE_GATE", False)
    monkeypatch.setattr(M, "MAX_MARKETS", 5)
    monkeypatch.setattr(M, "RN1_FILL_RATE", {0: {"": 1.0}, 1: {"": 1.0}})
    monkeypatch.setattr(M, "RN1_FILL_LOSS", {0: {"": 0.01}, 1: {"": 0.01}})
    from tests.test_pmus_rn1_reward_aware import Guard
    e.guard = Guard(None)
    monkeypatch.setattr(e, "rn1_reward", lambda s: {
        "df": 0.3, "targetSize": 10, "pool_rate_usd_s": 0.01,
        "programId": "P", "period": "live", "maxSpread": None})
    monkeypatch.setattr(e, "_reward_get",
                        lambda: (lambda p: {"marketData": {"x": 1}}))
    monkeypatch.setattr(RW, "marginal_ladder_reward", lambda *a: {
        "incremental_reward_usd_per_hour":
            5.0 if abs(a[8] - 0.48) < 1e-9 else 0.1})
    for s_ in (ACT1, ACT2, ACT3):
        e.b.m(s_)
    for _ in range(3):
        for s_ in (ACT1, ACT2, ACT3, INV1, INV2):
            e.step_market(s_)
            assert exposure_ok(e)
    yes_touch = [o for o in v.orders.values() if o["open"] and
                 o["side"] == "YES" and D(str(o["px"])) == D("0.48")]
    assert yes_touch                      # the touch was joined somewhere
    assert exposure_ok(e)
