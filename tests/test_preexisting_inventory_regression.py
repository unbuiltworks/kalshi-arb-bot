"""run() must refuse inherited inventory on the SELECTED market.

DEFECT_PREEXISTING_INVENTORY_CASE_001. The guard asked
`store.position(market, "up"/"down")`, but the sole writer of positions
(`InventoryStore.apply_fill`) canonicalises through `canonical_side` and only
ever creates "<market>|UP" / "<market>|DOWN". The guard read two keys that
cannot exist, so it concluded "flat" over a store holding inventory and could
never fire.

WHY THE EXISTING TESTS MISSED IT: they seeded `state["positions"]` by hand,
choosing the key spelling themselves, so they encoded the same wrong assumption
the guard did. These tests seed through the REAL writer and then drive the REAL
`run()`, so the spelling is whatever production actually writes.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

import scripts.l5_pmus_live_oneshot as EP
from src import l5_live_execution as EX
from src import pmus_live_mandate as MANDATE

from tests.test_run_money_path_execution import (        # noqa: F401
    MANDATE_RAW, SEED, SLUG, Feed, Venue, ticking)


@pytest.fixture()
def armed(tmp_path, monkeypatch):
    monkeypatch.setenv(MANDATE.MANDATE_ENV, json.dumps(MANDATE_RAW))
    monkeypatch.setenv("PMUS_API_KEY_ID", "key-aaaaaaaaaaaa")
    monkeypatch.setenv("PMUS_API_SECRET_KEY", SEED)
    monkeypatch.setenv("PMUS_Q1_LIVE", "YES")
    monkeypatch.setenv("PMUS_Q1_SESSION_ID", "preexist-session")
    monkeypatch.setenv("PMUS_Q1_ARM", "preexist-session")
    monkeypatch.setenv(EP.AUTH.MARKET_ENV, SLUG)
    monkeypatch.setenv("L5_OBSERVE_ONLY", "")
    monkeypatch.setenv("L5_RECOVER_ONLY", "")
    return tmp_path


def _seed_through_the_real_writer(path, market, side, qty="1", px="0.40"):
    """The ONLY way inventory legitimately enters a store."""
    st = EX.InventoryStore(str(path))
    st.apply_fill(market, side, D(qty), D(px), basis="OWN")
    return st


def _run(tmp_path, venue, state):
    return EP.run(clock=ticking(EP.window_start(SLUG) + EP.PREFLIGHT_AT_S),
                  sleep=lambda *_a, **_k: None,
                  transport_factory=lambda slug: venue,
                  state_path=str(state),
                  journal_path=str(tmp_path / "journal.jsonl"),
                  ledger_path=str(tmp_path / "ledger.jsonl"),
                  book_feed=Feed())


@pytest.mark.parametrize("venue_side", ["OUTCOME_SIDE_YES", "OUTCOME_SIDE_NO"])
def test_run_refuses_inherited_inventory_on_the_selected_market(armed, venue_side):
    """THE REGRESSION. Before the fix this returned a normal run and submitted."""
    state = armed / "inv.json"
    _seed_through_the_real_writer(state, SLUG, venue_side)

    venue = Venue()
    rep = _run(armed, venue, state)

    assert rep["refused"] == "PREEXISTING_INVENTORY", rep.get("refused")
    assert rep["armed"] is False
    assert rep["submitted"] == 0
    assert venue.posts == [], "an order went out over inherited inventory"


def test_the_refusal_names_the_inherited_quantity_in_canonical_terms(armed):
    state = armed / "inv.json"
    _seed_through_the_real_writer(state, SLUG, "OUTCOME_SIDE_YES", qty="2")

    rep = _run(armed, Venue(), state)

    assert rep["refused"] == "PREEXISTING_INVENTORY"
    detail = rep["detail"]
    assert any(D(str(v)) != 0 for v in detail.values()), detail


def test_a_matched_pair_is_still_inherited_inventory(armed):
    """residual() would report 0 here. Both legs must be checked, not the net."""
    state = armed / "inv.json"
    st = _seed_through_the_real_writer(state, SLUG, "OUTCOME_SIDE_YES")
    st.apply_fill(SLUG, "OUTCOME_SIDE_NO", D("1"), D("0.55"), basis="OWN")
    assert st.residual(SLUG) == 0, "the pair nets to zero by construction"

    rep = _run(armed, Venue(), state)

    assert rep["refused"] == "PREEXISTING_INVENTORY", (
        "a matched pair nets to zero residual and is still inventory whose "
        "cash basis this session did not observe")


def test_a_fresh_store_is_not_refused(armed):
    """The negative control: the guard must not refuse an empty store."""
    rep = _run(armed, Venue(), armed / "fresh.json")
    assert rep.get("refused") != "PREEXISTING_INVENTORY", rep.get("refused")


def test_inventory_on_a_DIFFERENT_market_does_not_block_this_one(armed):
    """The quarantine property Rung A depends on: the guard is per-market.

    The legacy sports residual lives on another slug, so it must not refuse a
    BTC15m probe. This is the code-level statement of that.
    """
    state = armed / "inv.json"
    _seed_through_the_real_writer(state, "aec-county-not-ham-2026-09-08",
                                  "OUTCOME_SIDE_YES", qty="45")

    rep = _run(armed, Venue(), state)

    assert rep.get("refused") != "PREEXISTING_INVENTORY", (
        "inventory on an unrelated market blocked the selected one")


def test_the_guard_asks_for_the_key_the_writer_actually_creates():
    """Structural: the two must agree on spelling, forever.

    Stated against the store itself rather than the source text, so a future
    rewrite of either side cannot reintroduce the mismatch.
    """
    import tempfile, os
    with tempfile.TemporaryDirectory() as d:
        st = EX.InventoryStore(os.path.join(d, "s.json"))
        st.apply_fill(SLUG, "OUTCOME_SIDE_YES", D("1"), D("0.4"), basis="OWN")
        written = list(st.state["positions"])
        assert written == ["%s|UP" % SLUG], written
        asked = [EX.canonical_side(sd, doing="test") for sd in ("up", "down")]
        assert asked == ["UP", "DOWN"], asked
        assert any(st.position(SLUG, a) != 0 for a in asked), (
            "the canonical read must see what the writer wrote")
        assert all(st.position(SLUG, sd) == 0 for sd in ("up", "down")), (
            "the lowercase read is the defect; it must still read empty, "
            "which is why the call site had to change rather than the store")
