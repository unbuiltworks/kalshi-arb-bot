"""Crypto adapter for the shared allocator: probability bands and judging
the existing controller's orders (pure; no controller change)."""
import hashlib
import pathlib
from decimal import Decimal as D

import pytest

from src import l5_crypto_alloc_adapter as AD


@pytest.mark.parametrize("sym,slug", [
    ("BTC", "cpc-btc-updown-5m-2026-11-27-1200z"),
    ("ETH", "cpceth-eth-updown-15m-2026-11-27-1200z"),
    ("SOL", "cpcsol-sol-updown-15m-2026-11-27-1200z"),
    ("XRP", "cpcxrp-xrp-updown-15m-2026-11-27-1200z")])
def test_symbols(sym, slug):
    assert AD.symbol_of(slug) == sym
    p = AD.probability_band(reference=100.0, strike=100.0, tau_s=120,
                            symbol=sym)
    assert p is not None and p["lo"] < D("0.5") < p["hi"] or \
        p["p"] == D("0.5")


def test_band_moves_with_strike_distance_and_time():
    near = AD.probability_band(reference=74600, strike=74550, tau_s=60,
                               vol_per_sqrt_s=6.0)
    far = AD.probability_band(reference=74600, strike=74550, tau_s=600,
                              vol_per_sqrt_s=6.0)
    assert near["p"] > far["p"] > D("0.5")
    assert near["lo"] <= near["p"] <= near["hi"]


def test_stale_reference_or_decided_window_gives_no_probability():
    assert AD.probability_band(reference=1, strike=1, tau_s=60,
                               vol_per_sqrt_s=0.001, ref_age_s=5) is None
    assert AD.probability_band(reference=1, strike=1, tau_s=0,
                               vol_per_sqrt_s=0.001) is None
    assert AD.probability_band(reference="x", strike=1, tau_s=60) is None


def test_older_reference_widens_the_band():
    a = AD.probability_band(reference=100.1, strike=100, tau_s=100,
                            vol_per_sqrt_s=0.05, ref_age_s=0)
    b = AD.probability_band(reference=100.1, strike=100, tau_s=100,
                            vol_per_sqrt_s=0.05, ref_age_s=2.5)
    assert b["lo"] <= a["lo"] and b["hi"] >= a["hi"]


def test_model_band_from_reference_model_fields():
    rec = {"ok": True, "value": 0.2591, "distance_to_strike": -39.57,
           "sd": 48.47, "sigma_per_second": 6.17}
    p = AD.probability_from_model(rec, index_age_s=0.16)
    assert p["lo"] < D("0.2591") < p["hi"]
    assert AD.probability_from_model({"ok": False}) is None
    assert AD.probability_from_model(rec, index_age_s=10) is None


def _dec(*orders):
    return {"action": "QUOTE", "orders": list(orders)}


def _o(side, px, qty="1", op="PLACE"):
    return {"op": op, "side": side, "price": px, "qty": qty}


def test_filter_without_probability_is_identity():
    d = _dec(_o("UP", "0.40"), _o("DOWN", "0.55"))
    out = AD.filter_decision(d, inventory={}, prob=None)
    assert out["orders"] == d["orders"]


def test_filter_refuses_overpriced_additions_keeps_completions_and_cancels():
    prob = AD.DA.probability("0.30")       # UP 30%
    inv = {"UP": {"qty": "2", "cost": "0.80"}, "DOWN": {"qty": "0"}}
    d = _dec(_o("UP", "0.40"),             # adds to heavy UP above fair
             _o("DOWN", "0.75"),           # completion (lighter side)
             _o("UP", "0.10", op="CANCEL"))
    out = AD.filter_decision(d, inventory=inv, prob=prob, fill_loss="0")
    sides = [(o["op"], o["side"]) for o in out["orders"]]
    assert ("PLACE", "UP") not in sides
    assert ("PLACE", "DOWN") in sides and ("CANCEL", "UP") in sides


def test_filter_never_raises_size_or_price():
    prob = AD.DA.probability("0.80")
    d = _dec(_o("UP", "0.50", "3"))
    out = AD.filter_decision(d, inventory={}, prob=prob, fill_loss="0")
    for o in out["orders"]:
        assert D(str(o["qty"])) <= D(3) and D(str(o["price"])) == D("0.50")


def test_openings_pass_when_not_judged():
    prob = AD.DA.probability("0.30")
    d = _dec(_o("UP", "0.40"))
    out = AD.filter_decision(d, inventory={}, prob=prob,
                             alloc_cfg={"judge_openings": False})
    assert out["orders"] == d["orders"]


def test_allocator_file_identical_to_the_sports_copy_when_present():
    """The allocator is one module shared by both lanes: this branch carries
    the same file as the sports branch (sha256 recorded at copy time)."""
    src = pathlib.Path(AD.DA.__file__).read_bytes()
    assert hashlib.sha256(src).hexdigest() == AD.ALLOCATOR_SHA256
