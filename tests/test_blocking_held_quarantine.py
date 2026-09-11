"""The re-arm gate must know what quarantine is, or quarantine cannot help.

THE DEFECT. Residual quarantine frees the runtime acquisition slot, and by
design a quarantined market stays in `held` right through to settlement. But
boot_assertions asked for `held_markets(t) == []`, so on every restart a
quarantined residual refused to arm anyway. The engine stops, and every maker
fill and every credited rebate stops with it. Quarantine was written for the
exact county-cricket fixture that halted us on 2026-09-11 and could never
have helped, because nothing taught the BOOT gate about it.

MEASURED, 16 settled R5F markets: the 12 that paired to zero returned
+6.3639; the 4 that stranded a residual returned -9.2384, none positive. A
residual is expensive on its own. A residual that also halts acquisition
costs the entire remaining schedule.

What must stay true: nothing here forgives a dollar. Quarantine changes WHEN
we may acquire, never HOW MUCH we may lose.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import pmus_l5_continuous_session as C
from src import pmus_residual_quarantine as rq

SLUG = "aec-county-not-ham-2026-09-08"


class _Inv:
    def __init__(self, up="16.67", dn="28.34"):
        self.up, self.dn = D(up), D(dn)

    def avg_up(self):
        return D("0.7121")

    def avg_dn(self):
        return D("0.1885")


@pytest.fixture
def live(monkeypatch):
    """The 02:43Z state: one held market, zero open orders, quantities
    reconciling exactly."""
    monkeypatch.setattr(C, "held_markets", lambda t: [SLUG])
    monkeypatch.setattr(C, "_fresh_open_orders",
                        lambda t: {"ours": 0, "foreign": 0})
    monkeypatch.setattr(C, "unsettled_slugs", lambda: [SLUG])
    monkeypatch.setattr(C.base, "held_inventory_from_ledger",
                        lambda s: _Inv())
    monkeypatch.setattr(C.sa, "traded_qty_with_evidence",
                        lambda s, t=None: {"qty": "45.0100"})
    return monkeypatch


# ------------------------------------------------- OFF by default, unchanged
def test_with_quarantine_off_the_gate_is_exactly_what_it_was(live, monkeypatch):
    monkeypatch.delenv(rq.QUARANTINE_ENV, raising=False)
    assert rq.quarantine_enabled() is False
    assert C.blocking_held(object()) == [SLUG], "unchanged when OFF"


def test_no_held_markets_is_still_flat_either_way(monkeypatch):
    monkeypatch.setattr(C, "held_markets", lambda t: [])
    for on in ("", "1"):
        monkeypatch.setenv(rq.QUARANTINE_ENV, on)
        assert C.blocking_held(object()) == []


# ------------------------------------------------------- ON: dormant is fine
def test_a_dormant_venue_confirmed_residual_stops_blocking(live, monkeypatch):
    monkeypatch.setenv(rq.QUARANTINE_ENV, "1")
    assert C.blocking_held(object()) == [], (
        "a quarantinable residual must not halt the engine")


def test_an_open_order_still_blocks(live, monkeypatch):
    """Quarantine requires zero resting orders. A working market is not
    dormant and must keep the slot."""
    monkeypatch.setenv(rq.QUARANTINE_ENV, "1")
    monkeypatch.setattr(C, "_fresh_open_orders",
                        lambda t: {"ours": 1, "foreign": 0})
    assert C.blocking_held(object()) == [SLUG]


def test_an_unknown_order_count_blocks(live, monkeypatch):
    """`ours: None` is not `ours: 0`. Unknown must never read as dormant."""
    monkeypatch.setenv(rq.QUARANTINE_ENV, "1")
    monkeypatch.setattr(C, "_fresh_open_orders",
                        lambda t: {"ours": None, "foreign": 0})
    assert C.blocking_held(object()) == [SLUG]


def test_a_residual_the_venue_does_not_confirm_still_blocks(live, monkeypatch):
    """This is the 774.62 shape. An unreconciled position is not a position
    we may quarantine and walk away from."""
    monkeypatch.setenv(rq.QUARANTINE_ENV, "1")
    monkeypatch.setattr(C.sa, "traded_qty_with_evidence",
                        lambda s, t=None: {"qty": "774.6200"})
    assert C.blocking_held(object()) == [SLUG]


def test_an_unaskable_venue_blocks(live, monkeypatch):
    monkeypatch.setenv(rq.QUARANTINE_ENV, "1")
    monkeypatch.setattr(C.sa, "traded_qty_with_evidence",
                        lambda s, t=None: {"qty": None})
    assert C.blocking_held(object()) == [SLUG]


def test_an_exception_anywhere_blocks(live, monkeypatch):
    monkeypatch.setenv(rq.QUARANTINE_ENV, "1")

    def boom(*a, **kw):
        raise RuntimeError("venue down")

    monkeypatch.setattr(C.sa, "traded_qty_with_evidence", boom)
    assert C.blocking_held(object()) == [SLUG], "could not establish -> blocks"


def test_a_market_no_longer_settlement_monitored_blocks(live, monkeypatch):
    """Quarantine's promise is that the market stays monitored. If it is not
    in unsettled_slugs, that promise is already broken."""
    monkeypatch.setenv(rq.QUARANTINE_ENV, "1")
    monkeypatch.setattr(C, "unsettled_slugs", lambda: [])
    assert C.blocking_held(object()) == [SLUG]


def test_a_flat_held_market_blocks_rather_than_being_quarantined(
        live, monkeypatch):
    """Zero inventory is FLAT, not quarantined. Quarantining it would hide a
    scheduler bug -- quarantine's own eligibility says so."""
    monkeypatch.setenv(rq.QUARANTINE_ENV, "1")
    monkeypatch.setattr(C.base, "held_inventory_from_ledger",
                        lambda s: _Inv(up="0", dn="0"))
    monkeypatch.setattr(C.sa, "traded_qty_with_evidence",
                        lambda s, t=None: {"qty": "0"})
    assert C.blocking_held(object()) == [SLUG]


def test_only_the_blocking_markets_are_named(live, monkeypatch):
    """Two held markets, one dormant and one working: the report names the
    one that actually blocks, not both."""
    monkeypatch.setenv(rq.QUARANTINE_ENV, "1")
    monkeypatch.setattr(C, "held_markets", lambda t: [SLUG, "other-mkt"])
    monkeypatch.setattr(C, "unsettled_slugs", lambda: [SLUG])
    assert C.blocking_held(object()) == ["other-mkt"]


# ------------------------------------------- the dollars are NOT forgiven
def test_quarantine_changes_when_we_acquire_never_how_much_we_can_lose():
    """combined_loss_above_stop is a separate assertion over ALL held
    markets, and blocking_held does not touch it."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(C.blocking_held))
    for node in ast.walk(tree):          # strip docstrings: prose is not code
        body = getattr(node, "body", None)
        if (isinstance(body, list) and body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body.pop(0)
    src = ast.unparse(ast.fix_missing_locations(tree))
    for banned in ("combined_loss", "daily_loss_stop", "cap", "budget"):
        assert banned not in src, banned
    gate = inspect.getsource(C.boot_assertions)
    assert "combined_loss_above_stop" in gate
    assert "blocking_held" in gate


def test_the_boot_gate_still_lists_flat_held_state_by_name(live, monkeypatch):
    """The check keeps its name so an operator reading a refusal sees the
    same assertion they have always seen."""
    import inspect
    assert '"flat_held_state"' in inspect.getsource(C.boot_assertions)
