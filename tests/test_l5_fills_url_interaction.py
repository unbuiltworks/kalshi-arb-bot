"""Setting L5_BTC5M_FILLS_URL also switches on the OLDER pre-settlement
check (`Runner.attest_other_strategy_cost`), which has never run in
production. These tests pin what it does to a two-sided 5-minute window
shaped like cpc-btc-updown-5m-2026-11-21-0620z: it must refuse, write
nothing, and leave the market for the post-settlement sibling form.
"""
from __future__ import annotations

import copy
import json
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_live_runner as R
from src import l5_venue_cost_attestation as VCA

from tests.test_l5_venue_cost_attestation import (
    Feed, _no_retry_spacing, run_until_idle,  # noqa: F401
)
from tests.test_l5_sibling_ledger_attestation import (
    M, URL, WINDOW, lane_doc, venue_fills,
)


def adopted(tmp_path, dn="0.10", up="0"):
    s = LX.InventoryStore(str(tmp_path / "inv.json"))
    for k in ("realized_unattributed", "realized_attributed",
              "realized_today"):
        s.state[k] = "0"
    rows = {}
    if D(up):
        rows["%s|UP" % M] = up
    if D(dn):
        rows["%s|DOWN" % M] = dn
    s.adopt_venue_position(M, rows, why="test")
    s.save()
    return s


class Venue:
    """The account service's own /v1/order reads of the lane's ids."""

    def __init__(self, rows=WINDOW, fail=None):
        self.rows = {"V%02d" % i: {
            "marketSlug": M, "cumQuantity": q,
            "avgPx": {"value": str(D(px) if side == "UP" else 1 - D(px))}}
            for i, (side, q, px) in enumerate(rows)}
        self.fail = fail
        self.asked = []

    def order(self, vid):
        self.asked.append(vid)
        if self.fail is not None:
            raise self.fail
        return self.rows[vid]

    def __getattr__(self, name):
        raise AssertionError("pre-settlement check touched transport.%s"
                             % name)


@pytest.fixture
def wire(monkeypatch):
    box = {"doc": lane_doc(), "raise": None, "emitted": []}
    monkeypatch.setattr(R, "emit", lambda k, d: box["emitted"].append((k, d)))
    monkeypatch.setenv(R.BTC5M_FILLS_URL_ENV, URL)

    class Resp:
        def __init__(self, b):
            self.b = b

        def read(self):
            return self.b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(url, timeout=0):
        assert url == URL
        if box["raise"] is not None:
            raise box["raise"]
        return Resp(json.dumps(box["doc"]).encode())
    import urllib.request as UR
    monkeypatch.setattr(UR, "urlopen", urlopen)
    return box


def pre(store, venue):
    class Shell:
        pass
    sh = Shell()
    sh.store = store
    return R.Runner.attest_other_strategy_cost(sh, M, venue)


def unchanged(store, tmp_path, before):
    assert store.state == before[0]
    assert (tmp_path / "inv.json").read_bytes() == before[1]


def snap(store, tmp_path):
    return copy.deepcopy(store.state), (tmp_path / "inv.json").read_bytes()


def test_the_two_sided_window_is_refused_and_nothing_is_written(
        tmp_path, wire):
    s = adopted(tmp_path)
    before = snap(s, tmp_path)
    got = pre(s, Venue())
    assert got["attested"] is False
    assert got["reason"] == "FILLS_DO_NOT_ACCOUNT_FOR_THE_POSITION"
    unchanged(s, tmp_path, before)
    assert wire["emitted"][-1][0] == "L5_OTHER_STRATEGY_COST"


@pytest.mark.parametrize("dn,up", [("0.12", "0"), ("0.61", "0"),
                                   ("0", "0.49"), ("0.17", "0")])
def test_no_net_or_single_side_snapshot_of_it_is_accepted(tmp_path, wire,
                                                          dn, up):
    s = adopted(tmp_path, dn=dn, up=up)
    before = snap(s, tmp_path)
    got = pre(s, Venue())
    assert got["attested"] is False
    assert got["reason"] == "FILLS_DO_NOT_ACCOUNT_FOR_THE_POSITION"
    unchanged(s, tmp_path, before)


def test_lane_unreachable_writes_nothing(tmp_path, wire):
    wire["raise"] = OSError("down")
    s = adopted(tmp_path)
    before = snap(s, tmp_path)
    got = pre(s, Venue())
    assert got == {"attested": False, "market": M,
                   "reason": "ATTEST_FAILED:OSError"}
    unchanged(s, tmp_path, before)


def test_a_venue_order_read_failing_writes_nothing(tmp_path, wire):
    s = adopted(tmp_path)
    before = snap(s, tmp_path)
    got = pre(s, Venue(fail=RuntimeError("429")))
    assert got["attested"] is False
    assert got["reason"] == "ATTEST_FAILED:RuntimeError"
    unchanged(s, tmp_path, before)


def test_a_feed_that_is_not_readable_writes_nothing(tmp_path, wire):
    wire["doc"] = lane_doc(readable=False)
    s = adopted(tmp_path)
    before = snap(s, tmp_path)
    assert pre(s, Venue())["reason"] == "ATTEST_FAILED:ValueError"
    unchanged(s, tmp_path, before)


def test_an_order_that_never_reached_the_venue_refuses(tmp_path, wire):
    d = lane_doc()
    d["orders"].append({"client_order_id": "b5m-none", "venue_order_id": None,
                        "market": M, "side": "UP", "price": "0.5",
                        "filled_qty": "0", "filled_cost": "0",
                        "state": "UNCERTAIN"})
    wire["doc"] = d
    s = adopted(tmp_path)
    before = snap(s, tmp_path)
    assert pre(s, Venue())["reason"] == "FILL_ID_MISSING_OR_REPEATED"
    unchanged(s, tmp_path, before)


def test_it_only_reads_the_venue_and_only_filled_orders(tmp_path, wire):
    v = Venue()
    pre(adopted(tmp_path), v)
    assert sorted(v.asked) == ["V%02d" % i for i in range(17)]


def test_a_market_not_held_at_the_bound_is_not_even_fetched(tmp_path, wire):
    s = LX.InventoryStore(str(tmp_path / "inv.json"))
    wire["raise"] = AssertionError("must not fetch")
    assert pre(s, Venue()) is None
    assert wire["emitted"] == []


def test_end_to_end_refuse_before_retire_at_the_bound_correct_after(
        tmp_path, wire, monkeypatch):
    """The whole path with both settings on, in production order."""
    monkeypatch.setenv(VCA.SIBLING_ENV, VCA.SIBLING_TOKEN)
    monkeypatch.setattr(
        VCA, "sibling_evidence",
        lambda market, _real=VCA.sibling_evidence: _real(
            market, fetch=lambda url: copy.deepcopy(wire["doc"])))
    s = adopted(tmp_path)
    assert pre(s, Venue())["attested"] is False
    out = s.retire_settled_market(
        M, yes_value=D("0"),
        resolution={"source": "VENUE_SETTLEMENT_PX", "market": M,
                    "yes_value": "0"})
    assert out["retired"] and D(out["settlement_pnl"]) == D("0")
    s.state["retired_markets"][M]["t"] = "2026-11-21T06:26:56+00:00"
    assert s.realized_today() == D("0")
    run_until_idle(s, Feed([venue_fills()]))
    assert s.realized_today() == D("0.0195")
    assert D(s.state["retired_markets"][M]["settlement_pnl"]) == D("0.0195")
    assert len(s.state["cost_basis_corrections"]) == 1


def test_when_it_does_accept_the_cost_is_the_real_one(tmp_path, wire):
    """The one shape it can accept: the ledger holds exactly what the
    lane's fills state, per side. Then the cost it writes is the fills'."""
    rows = [("DOWN", "0.06", "0.20"), ("DOWN", "0.04", "0.30")]
    wire["doc"] = lane_doc(rows)
    s = adopted(tmp_path, dn="0.10")
    got = pre(s, Venue(rows))
    assert got["attested"] is True
    assert s.leg_cost(M)["DOWN"] == D("0.06") * D("0.20") + D("0.04") * D("0.30")
