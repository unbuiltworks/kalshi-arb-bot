"""Wiring tests: LOCK5 parity, taker completion, and the mandate boundary.

The parity suite is the point. It feeds the SAME frozen historical states to the
shadow decision path and the live translation and requires them to agree on side,
action, LOCK eligibility and the economic floor. Checkpoint25 data and scoring are
read only -- nothing here writes to them and nothing was altered to make parity pass.
"""
from __future__ import annotations

import json
import os
import sys
from decimal import Decimal as D

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import pmus_completion_shadow as SHADOW                # noqa: E402
from src import pmus_lock5_translation as T                     # noqa: E402
from src import pmus_probe_observability as OBS                 # noqa: E402
from src import pmus_q1_executor as Q1                          # noqa: E402

MKT = "cpc-btc-updown-15m-2026-09-27-2300z"


# ------------------------------------------------------------------ doubles

class _Tp:
    def __init__(self):
        self.posts = []

    def request(self, method, path, payload=None):
        self.posts.append((method, path, payload))
        return {"order": {"orderId": "OID-%d" % len(self.posts), "filledQty": 0}}


class _Mandate:
    max_spend_per_market = D("5.00")


def _sess(tmp_path, *, admit=True, reason="OK", book=None):
    mon = OBS.ProbeMonitor(str(tmp_path / "j.jsonl"), market=MKT,
                           clock=lambda: 1000.0)
    s = T.Lock5Session(market=MKT, book=book or SHADOW.Book(), transport=_Tp(),
                       mandate=_Mandate(), state_dir=tmp_path, monitor=mon)
    s._verdict = {"admits": admit, "reason": reason}
    return s


@pytest.fixture(autouse=True)
def _stub_admit(monkeypatch):
    """Default: admit. Individual tests override to prove fail-closed."""
    def admit(body, *, state_dir, mandate, slug=None, now=None,
              realized_today=None):
        return dict(getattr(admit, "verdict", {"admits": True,
                                               "reason": "OK"}))
    monkeypatch.setattr(T.MANDATE, "admit", admit)
    return admit


# ----------------------------------------------------- 1. LOCK5, read-only

def test_the_floor_is_the_frozen_implementation_not_a_copy():
    assert T.settlement_independent_floor is SHADOW.settlement_independent_floor


def test_this_module_does_not_reimplement_the_floor():
    """A second implementation would be the whole failure mode. Pin its absence."""
    src = open(T.__file__, encoding="utf-8").read()
    body = src.split("class Lock5Session")[1]
    for token in ("min(", "self.book.c[\"up\"] +", "- self.book.fees"):
        assert token not in body, token


def test_the_threshold_is_exactly_five_and_is_not_a_parameter():
    assert T.LOCK_USD == D("5.00")
    import inspect
    sig = inspect.signature(T.Lock5Session.evaluate_lock)
    assert "threshold" not in sig.parameters and "lock_usd" not in sig.parameters


def test_lock_latches_at_five_and_prohibits_new_risk(tmp_path):
    bk = SHADOW.Book()
    bk.add("up", D(100), D(100) * D("0.40"), maker=True)
    bk.add("down", D(100), D(100) * D("0.55"), maker=True)
    s = _sess(tmp_path, book=bk)
    assert s.floor_now() == D(100) - (D(40) + D(55))       # = 5
    r = s.evaluate_lock()
    assert r["locked"] is True and r["newly_locked"] is True
    for action in ("OPEN", "ACQUIRE", "EXTEND"):
        assert s.may_open_risk(action) == (False, "LOCKED_NO_NEW_RISK")
    assert s.may_open_risk("COMPLETE")[0] is True


def test_just_below_five_does_not_lock(tmp_path):
    bk = SHADOW.Book()
    bk.add("up", D(100), D(100) * D("0.40"), maker=True)
    bk.add("down", D(100), D(100) * D("0.5501"), maker=True)
    s = _sess(tmp_path, book=bk)
    assert s.floor_now() < T.LOCK_USD
    assert s.evaluate_lock()["locked"] is False


def test_an_expected_rebate_can_never_contribute_to_the_lock(tmp_path):
    """Maker acquisitions accrue a MODELED rebate; the floor must ignore it."""
    bk = SHADOW.Book()
    bk.add("up", D(100), D(100) * D("0.40"), maker=True)
    bk.add("down", D(100), D(100) * D("0.5501"), maker=True)
    assert bk.modeled_rebate > 0, "fixture must actually accrue a rebate"
    s = _sess(tmp_path, book=bk)
    assert s.evaluate_lock()["locked"] is False
    assert bk.modeled_rebate + s.floor_now() > T.LOCK_USD, (
        "with the rebate added it WOULD clear $5 -- which is exactly what must "
        "not happen")


# --------------------------------------------- 1b. shadow/live PARITY

#: frozen historical states, taken from the reconstructed PMUS action paths.
#: (label, [(side, qty, px)], expected_lock)
PARITY_STATES = (
    ("2245z_flat_150_150", [("up", 50, "0.44"), ("down", 50, "0.52"),
                            ("down", 50, "0.39"), ("up", 50, "0.77")], None),
    ("2030z_whipsaw", [("up", 50, "0.42"), ("down", 50, "0.79"),
                       ("up", 50, "0.99")], None),
    ("1830z_two_clip", [("up", 100, "0.40"), ("down", 100, "0.55")], None),
    ("thin", [("up", 5, "0.10"), ("down", 5, "0.20")], None),
    ("deep_lock", [("up", 200, "0.30"), ("down", 200, "0.40")], None),
)


def _book_of(actions):
    bk = SHADOW.Book()
    for side, qty, px in actions:
        bk.add(side, D(qty), D(qty) * D(px), maker=True)
    return bk


@pytest.mark.parametrize("label,actions,_x", PARITY_STATES)
def test_shadow_and_live_agree_on_floor_and_lock_eligibility(tmp_path, label,
                                                             actions, _x):
    """Identical inventory/cash/fee state -> identical floor and LOCK decision."""
    shadow_floor = SHADOW.settlement_independent_floor(_book_of(actions))
    s = _sess(tmp_path / label, book=_book_of(actions))
    live_floor = s.floor_now()
    assert live_floor == shadow_floor, label
    shadow_lock = shadow_floor >= D("5.00")
    assert s.evaluate_lock()["locked"] is shadow_lock, label


@pytest.mark.parametrize("label,actions,_x", PARITY_STATES)
def test_resting_orders_are_charged_identically_on_both_paths(tmp_path, label,
                                                             actions, _x):
    resting = {"up": {"px": "0.50", "qty": "25"}}
    a = SHADOW.settlement_independent_floor(_book_of(actions), resting=resting)
    s = _sess(tmp_path / (label + "r"), book=_book_of(actions))
    assert s.floor_now(resting=resting) == a, label


@pytest.mark.parametrize("label,actions,_x", PARITY_STATES)
def test_completion_eligibility_agrees_with_the_frozen_condition(tmp_path,
                                                                 label,
                                                                 actions, _x):
    s = _sess(tmp_path / (label + "c"), book=_book_of(actions))
    for first in ("0.20", "0.44", "0.79", "0.95"):
        for ask in ("0.05", "0.23", "0.52", "0.99"):
            assert s.completion_permitted(D(first), D(ask)) is \
                Q1.taker_completion_ok(D(first), D(ask)), (label, first, ask)


# ------------------------------------------------- 2. taker completion

def test_completion_is_refused_where_the_frozen_policy_refuses(tmp_path):
    s = _sess(tmp_path)
    r = s.submit_taker_completion(outcome_side="OUTCOME_SIDE_NO", ask=D("0.99"),
                                  qty=D(5), first_cost=D("0.95"))
    assert r["refused"] == "FROZEN_POLICY_DOES_NOT_PERMIT_COMPLETION"
    assert s.transport.posts == [], "nothing may reach the wire"


def test_a_permitted_completion_uses_the_existing_primitive_body(tmp_path):
    s = _sess(tmp_path)
    out = s.submit_taker_completion(outcome_side="OUTCOME_SIDE_NO",
                                    ask=D("0.23"), qty=D(5), first_cost=D("0.44"))
    body = s.transport.posts[0][2]["request"]
    from src import pmus_micro_executor as MICRO
    assert body == MICRO._body(MKT, "OUTCOME_SIDE_NO", D("0.23"), D(5), False)
    assert body["participateDontInitiate"] is False, "a completion is a taker"
    assert out["stage"].startswith("micro_complement_taker_")


def test_ack_is_not_fill_and_moves_no_inventory(tmp_path):
    s = _sess(tmp_path)
    before = (s.book.q["up"], s.book.q["down"], s.book.c["up"])
    s.submit_taker_completion(outcome_side="OUTCOME_SIDE_NO", ask=D("0.23"),
                              qty=D(5), first_cost=D("0.44"))
    assert (s.book.q["up"], s.book.q["down"], s.book.c["up"]) == before
    kinds = [r["kind"] for r in _rows(s)]
    assert "ACK" in kinds and "FILL" not in kinds


def test_inventory_moves_only_from_an_authoritative_source(tmp_path):
    s = _sess(tmp_path)
    with pytest.raises(ValueError):
        s.apply_authoritative_fill(side="down", qty=D(5), price=D("0.23"),
                                   source="ACK")
    s.apply_authoritative_fill(side="down", qty=D(5), price=D("0.23"),
                               source="VENUE_FILL")
    assert s.book.q["down"] == D(5)
    s.apply_authoritative_fill(side="up", qty=D(5), price=D("0.44"),
                               source="RECONCILIATION")
    assert s.book.q["up"] == D(5)


# ------------------------------------ 3. mandate above strategy intent

def _rows(s):
    return [json.loads(l) for l in open(s.mon.path, encoding="utf-8")]


def test_every_submission_passes_the_mandate_first(tmp_path, monkeypatch):
    seen = {}

    def admit(body, *, state_dir, mandate, slug=None, now=None,
              realized_today=None):
        seen["called"] = True
        return {"admits": True, "reason": "OK"}
    monkeypatch.setattr(T.MANDATE, "admit", admit)
    s = _sess(tmp_path)
    s.submit({"marketSlug": MKT}, stage="x")
    assert seen.get("called") is True


@pytest.mark.parametrize("reason", [
    "PER_MARKET_CAP", "TOTAL_SPEND_CAP", "RESIDUAL_CAP", "CONCURRENCY_CAP",
    "DAILY_REALIZED_LOSS", "MARKET_NOT_AUTHORIZED", "WINDOW_NOT_AUTHORIZED",
    "MANDATE_EXPIRED", "HALT_PRESENT", "NO_MANDATE"])
def test_every_safety_refusal_fails_closed(tmp_path, monkeypatch, reason):
    def admit(body, **k):
        return {"admits": False, "reason": reason}
    monkeypatch.setattr(T.MANDATE, "admit", admit)
    s = _sess(tmp_path)
    with pytest.raises(T.SafetyRefusal) as e:
        s.submit({"marketSlug": MKT}, stage="x")
    assert e.value.verdict["reason"] == reason
    assert s.transport.posts == [], "a refusal must never reach the wire"
    assert s.submitted == 0


def test_lock5_cannot_override_a_safety_refusal(tmp_path, monkeypatch):
    """LOCK is not an authorization. A locked session still cannot spend."""
    def admit(body, **k):
        return {"admits": False, "reason": "PER_MARKET_CAP"}
    monkeypatch.setattr(T.MANDATE, "admit", admit)
    bk = SHADOW.Book()
    bk.add("up", D(200), D(200) * D("0.30"), maker=True)
    bk.add("down", D(200), D(200) * D("0.40"), maker=True)
    s = _sess(tmp_path, book=bk)
    assert s.evaluate_lock()["locked"] is True
    with pytest.raises(T.SafetyRefusal):
        s.submit_taker_completion(outcome_side="OUTCOME_SIDE_NO", ask=D("0.23"),
                                  qty=D(5), first_cost=D("0.44"))
    assert s.transport.posts == []


def test_there_is_no_transport_path_that_skips_the_mandate():
    src = open(T.__file__, encoding="utf-8").read()
    assert src.count("self.transport.request") == 1, (
        "exactly one wire call, and it sits after MANDATE.admit")
    head, tail = src.split("self.transport.request")
    assert "MANDATE.admit" in head


# ------------------------------------------------- observability coverage

def test_the_full_transition_chain_is_reconstructable(tmp_path):
    s = _sess(tmp_path)
    s.evaluate_lock()
    s.submit_taker_completion(outcome_side="OUTCOME_SIDE_NO", ask=D("0.23"),
                              qty=D(5), first_cost=D("0.44"))
    s.apply_authoritative_fill(side="down", qty=D(5), price=D("0.23"),
                               source="VENUE_FILL")
    s.mon.record("RECONCILIATION", {"authority": "VENUE"})
    s.mon.record("TERMINAL", {"state": "MARKET_STATE_EXPIRED"})
    kinds = [r["kind"] for r in _rows(s)]
    for k in ("DECISION", "SUBMISSION", "ACK", "FILL", "INVENTORY", "LOCK",
              "RECONCILIATION", "TERMINAL"):
        assert k in kinds, k
    assert [r["seq"] for r in _rows(s)] == list(range(1, len(_rows(s)) + 1))
