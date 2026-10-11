"""The flat-account exit must refuse anything that is not attested flat.

`adopt_flat_account` deletes positions, leg_cost, fill_count, committed and
live_orders and posts no P&L. Before this guard it validated nothing: the
anchor was recorded verbatim and otherwise trusted, and its only production
caller passes a hardcoded constant from ACCOUNTING_REBASES. The more
destructive of the two sanctioned exits was the less guarded one --
`repair_orphaned_market_key` has always refused without stated evidence.

The last test here is the one that matters operationally: it runs the repair
the 1345z phantom actually needs, against the REAL 12:50:04Z venue anchor,
and checks that it closes the book without inventing a cent.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LE
from src.l5_live_execution import InventoryStore

#: the venue's own answers, 2026-10-11T12:50:04Z, from the GET-only
#: pmus-readonly-recovery service. analysis/l5/evidence/attest_1250z/.
REAL_FLAT_ANCHOR = {
    "as_of_utc": "2026-10-11T12:50:04Z",
    "open_orders": [],
    "positions": {},
    "reservations": {"balanceReservation": 0, "depositReservation": 0,
                     "bonusHold": 0, "marginRequirement": 0,
                     "unsettledFunds": 0, "openOrders": 0},
    "balances": {"currentBalance": 243.09255, "buyingPower": 243.09255},
    "order": {"id": "D0VC6VJDMZ7J", "cumQuantity": 0, "avgPx": "0.0000",
              "state": "ORDER_STATE_CANCELED",
              "outcomeSide": "OUTCOME_SIDE_YES", "quantity": 0.13},
    "source": "pmus-readonly-recovery ba60b394, GET-only transport",
}

PHANTOM_MARKET = "cpc-btc-updown-15m-2026-10-10-1345z"


def _store(tmp_path):
    return InventoryStore(str(tmp_path / "inv.json"))


def test_the_real_anchor_is_accepted():
    assert InventoryStore.flat_anchor_refusal(REAL_FLAT_ANCHOR) is None


@pytest.mark.parametrize("mutate,expect", [
    ({"open_orders": [{"id": "X"}]}, "not flat"),
    ({"open_orders": 2}, "not flat"),          # the COUNT spelling
    ({"positions": {"m|UP": "1"}}, "not flat"),
    ({"as_of_utc": ""}, "as_of_utc/utc"),
    ({"reservations": None, "balances": None}, "attests no reservation"),
])
def test_a_non_flat_or_undated_anchor_is_refused(mutate, expect):
    bad = dict(REAL_FLAT_ANCHOR, **mutate)
    why = InventoryStore.flat_anchor_refusal(bad)
    assert why and expect in why


def test_the_production_anchor_shape_is_accepted():
    """THE REGRESSION THAT MATTERS.

    A first version of this guard demanded `as_of_utc` and a `reservations`
    mapping, and so REFUSED the anchor this codebase actually ships in
    ACCOUNTING_REBASES -- which writes `utc`, `unsettled_funds`, and
    `open_orders` as the integer 0. It broke three existing tests, and they
    were right: the job is to validate flatness, not a field spelling.
    """
    from src.l5_pmus_live_runner import ACCOUNTING_REBASES
    shipped = [s["flat_account"] for s in ACCOUNTING_REBASES
               if s.get("flat_account")]
    assert shipped, "no shipped anchor to check against"
    for anchor in shipped:
        assert InventoryStore.flat_anchor_refusal(anchor) is None, anchor


def test_a_nonzero_reservation_refuses_in_any_spelling_or_pool():
    """Substance, not spelling: either name, at top level or nested."""
    for pool, name in (("top", "unsettled_funds"),
                       ("top", "unsettledFunds"),
                       ("reservations", "marginRequirement"),
                       ("balances", "openOrders")):
        a = dict(REAL_FLAT_ANCHOR)
        if pool == "top":
            a[name] = "0.01"
        else:
            a[pool] = dict(a.get(pool) or {}, **{name: "0.01"})
        why = InventoryStore.flat_anchor_refusal(a)
        assert why and "not flat" in why, (pool, name)


def test_a_nonzero_or_unreadable_reservation_refuses():
    for bad in ("0.01", "nonsense", None):
        res = dict(REAL_FLAT_ANCHOR["reservations"], unsettledFunds=bad)
        why = InventoryStore.flat_anchor_refusal(
            dict(REAL_FLAT_ANCHOR, reservations=res))
        assert why, bad


def test_the_wipe_refuses_rather_than_deleting_live_exposure(tmp_path):
    """The failure this guard exists to prevent, end to end."""
    s = _store(tmp_path)
    s.apply_fill(PHANTOM_MARKET, "UP", D("2"), D("0.40"), basis=LE.OWN)
    before = dict(s.state["positions"])
    assert before, "the fixture must actually hold exposure"
    with pytest.raises(ValueError, match="refusing to adopt a flat account"):
        s.adopt_flat_account(anchor={"as_of_utc": "2026-10-11T12:50:04Z",
                                     "open_orders": [{"id": "still-live"}],
                                     "positions": {},
                                     "reservations": dict(
                                         REAL_FLAT_ANCHOR["reservations"])},
                             reason="should never run")
    assert s.state["positions"] == before, "a refused call must change nothing"


def test_a_blank_reason_refuses(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(ValueError, match="stated reason"):
        s.adopt_flat_account(anchor=REAL_FLAT_ANCHOR, reason="   ")


def test_the_1345z_repair_closes_the_book_and_posts_nothing(tmp_path):
    """The operation that is actually waiting on owner authorization.

    The phantom is a cost-less quantity on the DOWN side of a market whose
    order filled nothing: positions carries it, leg_cost does not, and
    fill_count is zero. The repair must retire it, post NO P&L, and record
    the anchor verbatim so the decision stays auditable.
    """
    s = _store(tmp_path)
    s.state["positions"]["%s|DOWN" % PHANTOM_MARKET] = "0.1300"
    s.save()
    realized_before = str(s.state.get("realized_attributed") or 0)

    out = s.adopt_flat_account(
        anchor=REAL_FLAT_ANCHOR,
        reason="phantom 0.1300 DOWN in 1345z: venue says cumQuantity 0, no "
               "position, account flat; no P&L to post")

    assert out["posted_pnl"] == "0"
    assert PHANTOM_MARKET in out["closed"]
    assert s.state["positions"] == {}
    assert s.state.get("leg_cost", {}) == {}
    assert s.state.get("fill_count", {}) == {}
    assert s.state.get("committed", {}) == {}
    assert s.state.get("live_orders", {}) == {}
    # NOTHING was invented in either direction
    assert str(s.state.get("realized_attributed") or 0) == realized_before
    retired = s.state["retired_markets"][PHANTOM_MARKET]
    assert retired["settlement_pnl"] == "0"
    assert retired["closed_against"] == "AUTHORITATIVE_FLAT_ACCOUNT"
    # the decision stays auditable: the venue's own read, verbatim
    adoption = s.state["flat_adoptions"][-1]
    assert adoption["anchor"] == REAL_FLAT_ANCHOR
    assert adoption["anchor"]["order"]["cumQuantity"] == 0
    assert "pmus-readonly-recovery" in adoption["anchor"]["source"]
    # and it survives a reload, because this mutates durable state
    again = InventoryStore(str(tmp_path / "inv.json"))
    assert again.state["positions"] == {}
    assert again.state["retired_markets"][PHANTOM_MARKET][
        "settlement_pnl"] == "0"
