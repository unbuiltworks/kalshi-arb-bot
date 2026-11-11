"""Live-slate census: what is tradeable right now, asked of the venue.

Pinned here are the ways a census can mislead: dropping a market it
could not read (which reads as "not a candidate"), and quoting a spread
without saying whether a pair could ever clear the hard bound.
"""
from __future__ import annotations

from src import pmus_live_slate_census as cen


def _bbo(bid, ask, shares="1000"):
    return {"marketData": {"bestBid": {"value": bid},
                           "bestAsk": {"value": ask},
                           "sharesTraded": shares,
                           "state": "MARKET_STATE_OPEN"}}


def _book(bq="500", oq="400"):
    return {"marketData": {"bids": [{"qty": bq}], "offers": [{"qty": oq}]}}


def _get(table):
    def get(path):
        slug = path.split("/")[3]
        return _bbo(*table[slug]) if path.endswith("bbo") else _book()
    return get


def test_in_band_two_sided_market_is_a_candidate():
    rows = cen.census(["m1"], {"m1": {"event_slug": "e1", "league": "mlb"}},
                      _get({"m1": ("0.4500", "0.4550")}))
    assert rows[0]["in_band"] is True
    assert rows[0]["two_sided"] is True
    assert cen.candidates(rows) == rows


def test_out_of_band_market_is_not_a_candidate():
    rows = cen.census(["m1"], {}, _get({"m1": ("0.9700", "0.9750")}))
    assert rows[0]["in_band"] is False
    assert cen.candidates(rows) == []


def test_maker_pair_cost_is_one_minus_the_spread():
    """The pair a maker can build costs bid + (1 - ask). It is the single
    number deciding whether a completion can clear the >=1c bound, so the
    census states it rather than leaving it to be re-derived."""
    rows = cen.census(["m1"], {}, _get({"m1": ("0.4500", "0.4550")}))
    assert rows[0]["maker_pair_cost"] == 0.995
    assert rows[0]["pair_clears_hard_bound"] is False
    wide = cen.census(["m2"], {}, _get({"m2": ("0.4500", "0.4600")}))
    assert wide[0]["maker_pair_cost"] == 0.99
    assert wide[0]["pair_clears_hard_bound"] is True


def test_an_unreadable_market_is_reported_not_dropped():
    """A dropped row reads as 'not a candidate', which is a different
    claim from 'we could not look'."""
    def boom(path):
        raise RuntimeError("venue said no")
    rows = cen.census(["m1"], {}, boom)
    assert rows[0]["readable"] is False
    assert rows[0]["error"] == "RuntimeError"
    assert cen.candidates(rows) == []


def test_candidates_rank_by_touch_depth_not_by_slug():
    table = {"thin": ("0.4500", "0.4550"), "deep": ("0.4500", "0.4550")}
    def get(path):
        slug = path.split("/")[3]
        if path.endswith("bbo"):
            return _bbo(*table[slug])
        return _book(bq="9" if slug == "thin" else "90000")
    rows = cen.census(["thin", "deep"], {}, get)
    assert [r["slug"] for r in cen.candidates(rows)] == ["deep", "thin"]


def test_census_uses_the_execution_band_not_a_local_copy():
    from src import pmus_sports_live_collector as live
    assert (cen.BAND_LO, cen.BAND_HI) == (live.EXEC_BAND_LO,
                                          live.EXEC_BAND_HI)


def test_census_has_no_order_path():
    """Scanned as CODE, not prose -- the module says "GET requests only"
    in its docstring, which a text grep would flag as a network import."""
    import ast
    tree = ast.parse(open(cen.__file__, encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    for banned in ("requests", "urllib", "http", "socket"):
        assert banned not in imported, f"census must not reach out: {banned}"
    literals = {n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    for path in literals:
        assert "/v1/orders" not in path, path
        assert not path.startswith("POST"), path
