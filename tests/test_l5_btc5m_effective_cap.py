"""The controller plans against the ceiling that actually binds, and keeps
room for the completing order.

The defect: the executor handed the controller its default Params
(`max_exposure_usd` $5.00, fixed `hedge_reserve_usd` $1.50) while the
account cap on the service is $0.45. The controller then kept opening the
favorite until `_place` refused on the real cap, and the opposite-side
order that completes the pair found no room left.

Deterministic and offline. Mechanism only: nothing here is evidence about
fills on a real venue or about economics.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
from src import l5_btc5m_execution as X

import test_l5_btc5m_execution as H
from test_l5_btc5m_execution import armed, clean_env, rig  # noqa: F401  (fixtures)

CAP = D("0.45")


def inv(up=("0", "0"), down=("0", "0")):
    return {"UP": {"qty": up[0], "cost": up[1]},
            "DOWN": {"qty": down[0], "cost": down[1]}}


def order(oid, side, price, qty, age=1.0):
    return {"id": oid, "side": side, "price": str(price), "qty": str(qty),
            "age_s": age}


def book(bid, ask):
    return {"bid": str(bid), "ask": str(ask), "age_s": 0.1}


def P(**kw):
    kw.setdefault("max_exposure_usd", CAP)
    kw.setdefault("reserve_mode", "COMPLETION")
    return C.Params(**kw)


def decide(bk, *, inventory=None, resting=(), p_up="0.75", params=None, t=60.0):
    return C.decide(book=bk, t_in_window_s=t,
                    inventory=inventory or inv(), resting=list(resting),
                    params=params or P(), p_up=p_up, p_source="TEST")


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(C.CONTROLLER_ENV, "ON")


# ---------------------------------------------------- completion_reserve

def _r(inventory, resting=(), **add):
    i = C._read_inventory(inventory)
    r = C._read_resting(list(resting))
    return C.completion_reserve(i, r, P(), **add)


def test_flat_book_reserves_nothing():
    assert _r(inv())["usd"] == 0


def test_reserve_prices_every_uncovered_share_at_the_target_pair():
    # 0.17 UP at 0.58: an ordinary completion pays at most 0.98 - 0.58
    got = _r(inv(up=("0.17", "0.0986")))
    assert got["long_side"] == "UP" and got["uncovered_qty"] == D("0.17")
    assert got["price_bound"] == D("0.40")
    assert got["usd"] == D("0.17") * D("0.40")


def test_resting_completion_is_counted_once_not_reserved_again():
    held = inv(up=("0.17", "0.0986"))
    hedge = [order("h", "DOWN", "0.38", "0.17")]
    assert _r(held, hedge)["usd"] == 0           # covered by the resting hedge
    part = [order("h", "DOWN", "0.38", "0.10")]
    assert _r(held, part)["uncovered_qty"] == D("0.07")


def test_resting_entry_and_new_entry_both_count_as_filled():
    held = inv(up=("0.17", "0.0986"))
    rest = [order("e", "UP", "0.55", "0.18")]
    got = _r(held, rest, add_side="UP", add_price=D("0.53"), add_qty=D("0.18"))
    assert got["uncovered_qty"] == D("0.53")


def test_partial_fill_reserve_matches_filled_plus_remainder():
    # 0.10 of 0.17 filled, 0.07 still resting: same reserve as 0.17 held
    part = _r(inv(up=("0.10", "0.058")), [order("e", "UP", "0.58", "0.07")])
    whole = _r(inv(up=("0.17", "0.0986")))
    assert part["usd"] == whole["usd"]


def test_fee_allowance_raises_the_reserve():
    i = C._read_inventory(inv(up=("0.17", "0.0986")))
    lo = C.completion_reserve(i, [], P())["usd"]
    hi = C.completion_reserve(i, [], P(completion_fee_per_share=D("0.01")))["usd"]
    assert hi - lo == D("0.0017")


# -------------------------------------------------------------- decide

def test_fixed_mode_under_a_045_cap_can_never_enter(on):
    # the old rule with the real cap: 0.45 - 1.50 < 0, nothing ever opens
    d = decide(book("0.60", "0.62"), params=C.Params(max_exposure_usd=CAP))
    assert d["action"] == C.HOLD_OR_SKIP and d["why"] == "NO_EXPOSURE_ROOM"


def test_completion_mode_opens_when_the_pair_still_fits(on):
    d = decide(book("0.60", "0.62"))
    assert d["action"] == C.OPEN_FAVORITE
    e = d["evaluation"]["entry"]
    assert e["cap_usd"] == CAP
    assert e["completion_reserve_usd"] == D("0.17") * D("0.40")


def test_entries_stop_while_the_completion_still_fits(on):
    # held 0.35 UP at 0.58 (0.203); the price dipped so the hedge pair is
    # 0.58 + 0.45 = 1.03 (blocked). One more 0.19 @0.52 entry would need
    # 0.203 + 0.0988 + 0.54 * (0.98 - 0.5589) = 0.53 > 0.45
    held = inv(up=("0.35", "0.203"))
    d = decide(book("0.54", "0.55"), inventory=held, p_up="0.70")
    assert d["evaluation"]["hedge"]["why"] == "PAIR_ABOVE_CEILING"
    assert d["evaluation"]["entry"]["why"] == "NO_EXPOSURE_ROOM"
    assert d["action"] == C.HOLD_OR_SKIP
    # FIXED mode at the $5.00 default would have added it
    old = decide(book("0.54", "0.55"), inventory=held, p_up="0.70",
                 params=C.Params())
    assert old["action"] == C.ADD_FAVORITE


def test_cap_left_after_entries_always_buys_the_full_completion(on):
    """Whatever entries were allowed, completion at the target pair fits."""
    held = inv()
    resting = []
    bk = book("0.60", "0.62")
    for _ in range(8):
        d = decide(bk, inventory=held, resting=resting)
        if d["action"] not in (C.OPEN_FAVORITE, C.ADD_FAVORITE):
            break
        (o,) = [x for x in d["orders"] if x["op"] == "PLACE"]
        q = D(held["UP"]["qty"]) + o["qty"]
        c = D(held["UP"]["cost"]) + o["qty"] * o["price"]
        held = inv(up=(str(q), str(c)))
    q, c = D(held["UP"]["qty"]), D(held["UP"]["cost"])
    need = q * (D("0.98") - c / q)
    assert q > 0 and c + need <= CAP


def test_hedge_is_chosen_for_the_whole_unmatched_leg_near_the_cap(on):
    # the most UP the reserve lets accumulate at 0.58, then the hedge
    held = inv(up=("0.51", "0.2958"))      # 0.2958 + 0.51 * 0.42 = 0.51 > cap?
    r = C.completion_reserve(C._read_inventory(held), [], P())
    assert D("0.2958") + r["usd"] > CAP    # not reachable: entries stop first
    held = inv(up=("0.34", "0.1972"))      # two 0.17 @0.58 fills
    r = C.completion_reserve(C._read_inventory(held), [], P())
    assert D("0.1972") + r["usd"] <= CAP
    d = decide(book("0.62", "0.64"), inventory=held, p_up="0.75")
    assert d["action"] == C.HEDGE_UNDERDOG
    (o,) = [x for x in d["orders"] if x["op"] == "PLACE"]
    assert o["side"] == "DOWN" and o["qty"] == D("0.34")
    assert D("0.1972") + o["price"] * o["qty"] <= CAP


def test_unknown_reserve_mode_fails_closed(on):
    d = decide(book("0.60", "0.62"), params=P(reserve_mode="BOTH"),
               resting=[order("x", "UP", "0.58", "0.17")])
    assert d["action"] == C.HOLD_OR_SKIP and d["why"] == "PARAMS_UNREADABLE"
    assert [o["id"] for o in d["orders"] if o["op"] == "CANCEL"] == ["x"]


def test_default_params_unchanged():
    assert C.Params().reserve_mode == "FIXED"
    assert C.Params().completion_fee_per_share == 0


# ------------------------------------------------------- the executor

def test_effective_params_is_the_smallest_ceiling(monkeypatch, tmp_path):
    ex = X.Btc5mExecutor(None, str(tmp_path / "l.json"))
    monkeypatch.delenv(X.ACCOUNT_CAP_ENV, raising=False)
    assert ex.effective_params().max_exposure_usd == D("5.00")
    monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "0.45")
    p = ex.effective_params()
    assert p.max_exposure_usd == D("0.45") and p.reserve_mode == "COMPLETION"
    assert ex.effective_params(D("0.30")).max_exposure_usd == D("0.30")
    assert ex.effective_params("garbage").max_exposure_usd == D("0.45")
    monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "not-a-number")
    assert ex.effective_params().max_exposure_usd == D("5.00")   # _place still refuses
    assert ex.params.max_exposure_usd == D("5.00")               # never mutated


def _dip_then_recover(rig, monkeypatch, old):
    """Open at 0.58, the price dips (pair above target: no hedge), entries
    keep coming and fill, then the price recovers and the hedge is due."""
    monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "0.45")
    if old:
        monkeypatch.setattr(X.Btc5mExecutor, "effective_params",
                            lambda self, ceiling_usd=None: self.params)
    v = rig.venue
    rig.step()                                     # entry at 0.58
    for oid in list(v.by_oid):
        v.fill(oid)
    for _ in range(6):                              # the dip
        rig.tick(2)
        rep = rig.step(bid="0.55", ask="0.57", p_up="0.70")
        for p in rep["placed"]:
            v.fill(p["venue_order_id"])
    rig.tick(2)
    rig.step(bid="0.55", ask="0.57", p_up="0.70")   # book any last fills
    rig.tick(2)
    rep = rig.step(bid="0.62", ask="0.64", p_up="0.75")   # recovery
    inv_ = rig.ex.inventory(H.MARKET)
    hedge = [p for p in rep["placed"] if p["side"] == "DOWN"]
    refused = [r["reason"] for r in rep["refused"]]
    return inv_, hedge, refused, rep


def test_old_wiring_starves_the_completion(armed, rig, monkeypatch):
    inv_, hedge, refused, rep = _dip_then_recover(rig, monkeypatch, old=True)
    up = inv_["UP"]["qty"]
    hedged = sum((D(h["qty"]) for h in hedge), D(0))
    assert up > 0 and hedged < up                  # the defect, reproduced


def test_effective_cap_completes_the_whole_leg(armed, rig, monkeypatch):
    inv_, hedge, refused, rep = _dip_then_recover(rig, monkeypatch, old=False)
    up = inv_["UP"]["qty"]
    assert up > 0 and inv_["DOWN"]["qty"] == 0
    (h,) = hedge
    assert D(h["qty"]) == up                       # the whole unmatched leg
    assert "ACCOUNT_EXPOSURE_CAP" not in refused
    total = inv_["UP"]["cost"] + D(h["qty"]) * D(h["price"])
    assert total <= CAP
    pair = inv_["UP"]["cost"] / up + D(h["price"])
    assert pair <= D("0.98")


def test_cancelled_entry_frees_its_reserve(armed, rig, monkeypatch):
    monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "0.45")
    rep = rig.step()
    (p,) = rep["placed"]
    rig.tick(25)                                   # past the 20 s TTL
    rep = rig.step()
    assert p["venue_order_id"] in [c.get("venue_order_id") for c in rep["cancelled"]]
    assert rig.ex.inventory(H.MARKET)["UP"]["qty"] == 0
    # the cancelled order's room is reused in the same pass, and once it is
    # terminal it is no longer counted: exactly one entry's exposure remains
    assert len(rep["placed"]) == 1
    exp = rep["exposure"]["btc5m"]
    assert exp["resting_notional_usd"] == D(rep["placed"][0]["price"]) * D(rep["placed"][0]["qty"])
