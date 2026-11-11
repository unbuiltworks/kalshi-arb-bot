"""The join that would let the PMUS lane reach EVIDENCED_FILL.

The rename is trivial. The coverage rules are not, because coverage is what
turns silence into an evidenced no-fill, and a false no-fill is most confident
exactly where it is most wrong -- in a busy market, where the recorder's ring
has wrapped.
"""
import pytest

from src.l5_shadow import maker as MK
from src.l5_surface import pmus_prints as P


def _tr(n=3, t0=10.0):
    return [{"t": t0 + i, "px": "0.4%d" % (7 - i), "qty": "10",
             "dir": "buy"} for i in range(n)]


# ------------------------------------------------------------ the rename

def test_recorder_rows_become_ladder_prints():
    got = P.to_prints([{"t": 1.0, "px": "0.47", "qty": "30", "dir": "buy"}])
    assert got == [{"t": 1.0, "price": "0.47", "size": "30", "dir": "buy"}]


def test_the_ladder_can_actually_consume_them():
    """The shape is only right if evidence_for_quote accepts it."""
    ps = P.to_prints([{"t": 10.0, "px": "0.46", "qty": "50"}])
    ev = MK.evidence_for_quote(quote_px="0.47", quote_qty="10", ahead="0",
                               placed_at=5.0, prints=ps, observed_until=20.0)
    assert ev["evidenced_fill"] is True
    assert ev["traded_through"] == "50"


def test_prints_are_returned_in_time_order():
    got = P.to_prints([{"t": 9.0, "px": "0.4", "qty": "1"},
                       {"t": 2.0, "px": "0.4", "qty": "1"}])
    assert [p["t"] for p in got] == [2.0, 9.0]


def test_an_unreadable_tape_stays_unreadable():
    """[] would assert nobody traded. That is the claim that manufactures
    no-fills out of a failed read."""
    assert P.to_prints(None) is None


def test_a_malformed_row_is_dropped_not_guessed():
    got = P.to_prints([{"t": 1.0, "px": "0.4", "qty": "1"},
                       {"t": 2.0, "px": None, "qty": "1"},
                       {"px": "0.4", "qty": "1"}, "junk"])
    assert len(got) == 1


# ---------------------------------------------------------- coverage rules

def test_a_watched_interval_can_be_vouched_for():
    c = P.coverage(_tr(), subscribed_at=1.0, placed_at=8.0,
                   observed_until=30.0)
    assert c["complete"] is True


def test_a_full_ring_cannot_vouch_for_anything():
    """The recorder keeps the last 500 trades. A busy market is where it wraps
    and where a false no-fill would be most confident."""
    c = P.coverage([{"t": 1.0}] * P.RING_CAPACITY, subscribed_at=1.0,
                   placed_at=8.0, observed_until=30.0)
    assert c["complete"] is None
    assert "ring is full" in c["why"]


def test_subscribing_after_the_quote_cannot_vouch_for_the_start():
    c = P.coverage(_tr(), subscribed_at=12.0, placed_at=8.0,
                   observed_until=30.0)
    assert c["complete"] is None
    assert "AFTER the quote was placed" in c["why"]


def test_an_unknown_subscription_start_is_not_assumed_early():
    c = P.coverage(_tr(), subscribed_at=None, placed_at=8.0,
                   observed_until=30.0)
    assert c["complete"] is None


def test_an_unreadable_tape_is_never_vouched_for():
    assert P.coverage(None, subscribed_at=1.0, placed_at=8.0,
                      observed_until=30.0)["complete"] is None


def test_coverage_is_never_true_by_accident():
    """Every rejection path returns None, never False: 'we did not watch' is
    not 'we watched and saw nothing'."""
    for kw in ({"trades": None, "subscribed_at": 1.0},
               {"trades": _tr(), "subscribed_at": None},
               {"trades": _tr(), "subscribed_at": 99.0},
               {"trades": [{"t": 1.0}] * P.RING_CAPACITY,
                "subscribed_at": 1.0}):
        c = P.coverage(kw["trades"], subscribed_at=kw["subscribed_at"],
                       placed_at=8.0, observed_until=30.0)
        assert c["complete"] is None and c["why"]


# ------------------------------------------------ flow is per price, not market

def test_flow_is_measured_through_our_price_not_across_the_market():
    """This is the figure sharesTraded structurally cannot give."""
    tr = [{"t": 10.0, "px": "0.47", "qty": "30"},
          {"t": 11.0, "px": "0.49", "qty": "15"},
          {"t": 12.0, "px": "0.40", "qty": "5"}]
    f = P.trade_flow_for("0.47", tr, side="BUY", placed_at=8.0)
    assert f["prints_after_placement"] == 3
    assert f["prints_through"] == 2          # 0.47 and 0.40, not 0.49
    assert f["traded_through"] == "35"


def test_a_sell_quote_is_crossed_from_above():
    tr = [{"t": 10.0, "px": "0.49", "qty": "7"},
          {"t": 11.0, "px": "0.40", "qty": "9"}]
    f = P.trade_flow_for("0.47", tr, side="SELL", placed_at=8.0)
    assert f["prints_through"] == 1 and f["traded_through"] == "7"


def test_prints_before_placement_never_count():
    """A trade that already happened cannot fill an order that did not exist."""
    tr = [{"t": 1.0, "px": "0.40", "qty": "999"}]
    f = P.trade_flow_for("0.47", tr, side="BUY", placed_at=8.0)
    assert f["traded_through"] == "0" and f["prints_after_placement"] == 0


def test_flow_is_not_reported_as_a_fill():
    f = P.trade_flow_for("0.47", [{"t": 10.0, "px": "0.4", "qty": "99"}],
                         side="BUY", placed_at=8.0)
    assert "evidenced_fill" not in f
    assert "the queue still has to clear" in f["labels"]


def test_an_unreadable_tape_reports_no_flow_figure_at_all():
    f = P.trade_flow_for("0.47", None, side="BUY", placed_at=8.0)
    assert f["readable"] is False and f["traded_through"] is None


# ------------------------------------------------------ holds nothing it should

def test_it_opens_no_socket_and_holds_no_credential():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(P))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module)
    assert not (mods & {"websockets", "httpx", "socket", "os"})
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "environ" not in names and "getenv" not in names
