"""Continuous inventory accumulation and replenishment, side by side.

Owner 2026-11-27: (1) after a fill on a side, an economically justified
replacement/extension bid on THAT side is re-posted after the refill
cooldown, independently of the other side; (2) same-side accumulation is
never conditioned on pair completion or on a pending completion bid --
only on marginal EV, the residual/unpaired caps, the concentration charge
and hard blocks; (3) each side's ladder is maintained independently: one
side blocked by its own condition does not pull or block the other side
unless a shared hard block applies (jump pause, reactive pull, lock, halt,
loss stop, account risk).
"""
import time
from decimal import Decimal as D

import pytest

from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_continuous_controller import (  # noqa: F401
    SLUG, eng, resting)


def _fill(v, side, px):
    for oid, o in v.orders.items():
        if o["open"] and o["side"] == side and str(o["px"]) == px:
            o["filled"], o["open"] = o["qty"], False
            return oid
    raise AssertionError("no resting %s bid at %s" % (side, px))


def _open(v, side):
    return {oid: (str(o["px"]), str(o["qty"])) for oid, o in v.orders.items()
            if o["open"] and o["side"] == side}


def _cool_down(e):
    lf = e.b.m(SLUG).setdefault("last_fill_t", {})
    for s_ in list(lf):
        lf[s_] = float(lf[s_]) - 11


@pytest.fixture(params=[(1, "default"), (3, "default"), (1, "enabled"),
                        (3, "enabled")],
                ids=["one_level", "ladder_3", "one_level_enabled",
                     "ladder_3_enabled"])
def live(request, eng, monkeypatch):
    """`enabled`: the proposed activation's economics (marginal EV with no
    fair value, fit-all-levels, touch EV, calibration 0.4) with a paying
    programme crediting every bid, so every bid is judged and rests."""
    from src import pmus_sports_rewards as RW
    levels, mode = request.param
    e, v = eng
    e.guard = None
    for name in ("RN1_TOUCH_EV", "RN1_REWARD_CALIBRATION", "RN1_MARGINAL_EV",
                 "RN1_MARGINAL_EV_NO_FAIR", "RN1_FIT_ALL_LEVELS",
                 "RN1_NET_RANK", "RN1_JOIN_TOUCH", "RN1_SIDE_INDEPENDENT"):
        if hasattr(M, name):
            monkeypatch.setattr(M, name, False)
    if mode == "enabled":
        for name in ("RN1_TOUCH_EV", "RN1_REWARD_CALIBRATION",
                     "RN1_MARGINAL_EV", "RN1_MARGINAL_EV_NO_FAIR",
                     "RN1_FIT_ALL_LEVELS"):
            monkeypatch.setattr(M, name, True)
        monkeypatch.setattr(M, "RN1_REWARD_CAL_TABLE", "*:0.4")
        # equal reward everywhere: one tick behind is then the best placement
        # (cheaper fills than the touch), the prices the assertions use
        monkeypatch.setattr(M, "RN1_TOUCH_EV_MAX_DEPTH", 1)
        monkeypatch.setattr(R, "EDGE_GATE", False)
        monkeypatch.setattr(e, "rn1_reward", lambda s: {
            "df": 0.3, "targetSize": 10, "pool_rate_usd_s": 0.01,
            "programId": "P", "maxSpread": None})
        monkeypatch.setattr(e, "rn1_incentive", lambda *a, **k: {
            "ticks": 0, "why": "qualifies"})
        monkeypatch.setattr(e, "_reward_get",
                            lambda: (lambda p: {"marketData": {"x": 1}}))
        monkeypatch.setattr(RW, "marginal_ladder_reward", lambda *a: {
            "incremental_reward_usd_per_hour": 1.0})
    monkeypatch.setattr(M, "RN1_LADDER_LEVELS", levels)
    monkeypatch.setattr(M, "RN1_LADDER_STEP_TICKS", 2)
    monkeypatch.setattr(M, "SLATE", "live")
    monkeypatch.setattr(M, "RN1_LIVE_QTY", D("2"))
    monkeypatch.setattr(R, "RESIDUAL_CAP_USD", D("3"))
    monkeypatch.setattr(R, "PER_MARKET_USD", D("8"))
    monkeypatch.setattr(R, "TOTAL_UNMATCHED_USD", D("6"))
    monkeypatch.setattr(R, "REFILL_COOLDOWN_S", 10.0)
    e.b.m(SLUG)
    return e, v, levels


def test_a_fill_replenishes_that_side_after_the_cooldown(live):
    e, v, levels = live
    e.step_market(SLUG)
    _fill(v, "YES", "0.47")
    e.step_market(SLUG)
    assert ("YES", "0.47", "2") not in resting(v)          # cooling down
    _cool_down(e)
    e.step_market(SLUG)
    assert ("YES", "0.47", "2") in resting(v)              # re-posted
    assert e.b.m(SLUG)["pos"]["YES"] in ("2", D("2"))


def test_the_other_side_is_not_touched_by_a_fill(live):
    """YES fills: NO's resting bids stay the same orders (same id, price
    and size) through the cooldown and the YES replenishment, unless the
    pair cap requires a NO price change (it does not here: 0.47 + 0.49)."""
    e, v, levels = live
    e.step_market(SLUG)
    no0 = _open(v, "NO")
    _fill(v, "YES", "0.47")
    e.step_market(SLUG)
    _cool_down(e)
    e.step_market(SLUG)
    no1 = _open(v, "NO")
    assert set(no0.items()) <= set(no1.items())


def test_same_side_extension_is_not_conditioned_on_a_completion(live):
    """Holding YES unpaired with a NO completion bid resting, YES keeps
    extending after the cooldown; the completion bid never has to fill."""
    e, v, levels = live
    e.step_market(SLUG)
    _fill(v, "YES", "0.47")
    e.step_market(SLUG)
    comp = [o for o in e.b.m(SLUG)["orders"].values()
            if o.get("open") and o["side"] == "NO"]
    assert comp                                            # completion rests
    _cool_down(e)
    e.step_market(SLUG)
    yes = [p for s, p, q in resting(v) if s == "YES"]
    assert "0.47" in yes
    roles = {o.get("stage") for o in e.b.m(SLUG)["orders"].values()
             if o.get("open") and o["side"] == "YES"}
    assert roles & {"rn1_extend", "rn1_open"}


def test_extension_goes_with_a_pending_completion_in_flight(live):
    """The NO completion is still on the wire (reserved, uncertain): YES
    extension is not held back by it."""
    e, v, levels = live
    e.step_market(SLUG)
    _fill(v, "YES", "0.47")
    e.step_market(SLUG)                               # fill seen, cooling
    e.reserve_order(SLUG, "NO", "0.48", "2", "two_sided_complete")
    _cool_down(e)
    e.step_market(SLUG)
    assert ("YES", "0.47", "2") in resting(v)


def test_one_side_at_its_own_cap_does_not_block_the_other(live,
                                                          monkeypatch):
    """YES holds enough unpaired inventory that one more YES clip passes
    the residual cap: YES rests nothing new, NO still rests."""
    e, v, levels = live
    mk = e.b.m(SLUG)
    mk["pos"]["YES"], mk["cost"]["YES"] = D("6"), D("2.70")
    e.step_market(SLUG)
    assert [r for r in resting(v) if r[0] == "YES"] == []
    assert [r for r in resting(v) if r[0] == "NO"]


def test_one_side_out_of_band_does_not_block_the_other(live, monkeypatch):
    """YES best bid below the replica's price band: only YES is refused."""
    e, v, levels = live
    v.bid, v.ask = D("0.18"), D("0.20")       # YES 0.17 < PX_MIN, NO 0.79
    e.step_market(SLUG)
    assert [r for r in resting(v) if r[0] == "YES"] == []
    assert [r for r in resting(v) if r[0] == "NO"]


def test_one_side_refused_by_its_depth_check_does_not_pull_the_other(
        live, monkeypatch):
    e, v, levels = live
    e.step_market(SLUG)
    no0 = _open(v, "NO")
    monkeypatch.setattr(e, "depth_block",
                        lambda slug, side: "thin" if side == "YES" else None)
    e.step_market(SLUG)
    assert [r for r in resting(v) if r[0] == "YES"] == []
    assert set(no0.items()) <= set(_open(v, "NO").items())


def test_a_shared_hard_block_still_pulls_both_sides(live):
    e, v, levels = live
    e.step_market(SLUG)
    e.b.m(SLUG)["vol_pause_until"] = time.time() + 60
    e.step_market(SLUG)
    assert resting(v) == []


# ----------------------------------------------- the coupling that was found
def _pending_cancel_setup(e, v):
    """YES and NO rest; the NO order is then closed by the venue (it must be
    re-placed) and the book moves so the YES bid is stale, but the venue
    does not confirm the YES cancel this pass."""
    e.step_market(SLUG)
    no_id = next(oid for oid, o in v.orders.items()
                 if o["open"] and o["side"] == "NO")
    v.orders[no_id]["open"] = False
    v.bid, v.ask = D("0.52"), D("0.54")

    def cancel(oid):                       # YES: accepted, not confirmed
        if v.orders[oid]["side"] == "NO":
            v.orders[oid]["open"] = False
        return True
    v.cancel = cancel


def test_off_a_pending_cancel_on_one_side_holds_the_other_a_pass(live):
    """src/pmus_sports_two_sided.py reconcile: 'a fill raced the cancel, or
    still pending' returned before ANY placement (unchanged default)."""
    e, v, levels = live
    _pending_cancel_setup(e, v)
    seen = set(v.orders)
    e.step_market(SLUG)
    assert [o for k, o in v.orders.items() if k not in seen] == []


def test_on_a_pending_cancel_holds_only_its_own_side(live, monkeypatch):
    e, v, levels = live
    monkeypatch.setattr(M, "RN1_SIDE_INDEPENDENT", True)
    _pending_cancel_setup(e, v)
    seen = set(v.orders)
    yes0 = sorted(p for s, p, q in resting(v) if s == "YES")
    e.step_market(SLUG)
    new = [(o["side"], str(o["px"])) for k, o in v.orders.items()
           if k not in seen]
    assert ("NO", "0.45") in new or ("NO", "0.43") in new
    assert all(sd == "NO" for sd, _p in new)       # YES waits for its cancel
    assert sorted(p for s, p, q in resting(v) if s == "YES") == yes0


def test_on_a_fill_that_raced_the_cancel_still_stops_the_pass(
        live, monkeypatch):
    e, v, levels = live
    monkeypatch.setattr(M, "RN1_SIDE_INDEPENDENT", True)
    e.step_market(SLUG)
    no_id = next(oid for oid, o in v.orders.items()
                 if o["open"] and o["side"] == "NO")
    v.orders[no_id]["open"] = False
    v.bid, v.ask = D("0.52"), D("0.54")

    def raced(oid):
        o = v.orders[oid]
        if o["side"] == "YES":
            o["filled"], o["open"] = o["qty"], False       # filled first
        return True
    v.cancel = raced
    seen = set(v.orders)
    e.step_market(SLUG)
    assert [o for k, o in v.orders.items() if k not in seen] == []
