from decimal import Decimal as D

from src import pmus_sports_open_gate as G


def mk(y, cy, n, cn, px):
    return {"pos": {"YES": str(y), "NO": str(n)},
            "cost": {"YES": str(cy), "NO": str(cn)}, "settle_px": px}


def test_outcome_splits_pair_margin_from_residual():
    o = G.market_outcome(mk(2, "0.92", 1, "0.52", "0"))
    # pair 1 at 0.46+0.52 = 0.98 -> +0.02; residual YES 0.46 -> 0
    assert o["margin"] == D("0.02") and o["residual"] == 1
    assert o["total"] == D("1") - D("1.44") and o["residual_pnl"] == D("-0.46")


def _book(n_pair, n_res, sport="cfb"):
    b = {}
    for i in range(n_pair):
        b[f"aec-{sport}-p{i}-x"] = mk(1, "0.46", 1, "0.50", "1")    # +0.04
    for i in range(n_res):
        b[f"aec-{sport}-r{i}-x"] = mk(1, "0.55", 0, "0", "0")       # -0.55
    return b


def test_todays_shape_is_negative_ev_and_rejected():
    # ~half the markets strand a leg that loses its price; pairs earn cents
    st = G.stats(_book(20, 18))
    d = G.allow_open("cfb", st)
    assert d["allow"] is False and d["why"] == "negative_ev"
    assert D(d["ev"]) < 0


def test_a_sport_that_rarely_strands_is_allowed():
    st = G.stats(_book(30, 1, sport="atp"))
    d = G.allow_open("atp", st)
    assert d["allow"] is True and D(d["ev"]) > 0


def test_small_sport_sample_is_shrunk_to_the_all_sport_figure():
    b = _book(20, 18)                                   # bad global evidence
    b.update(_book(3, 0, sport="nba"))                  # 3 lucky nba pairs
    d = G.allow_open("nba", G.stats(b))
    assert d["allow"] is False                          # not opened on luck


def test_insufficient_evidence_does_not_block():
    d = G.allow_open("cfb", G.stats(_book(2, 1)))
    assert d["allow"] is True and d["why"] == "insufficient_settled_evidence"


def test_unsettled_markets_are_ignored():
    b = _book(20, 18)
    b["aec-cfb-open-x"] = {"pos": {"YES": "1", "NO": "0"},
                           "cost": {"YES": "0.5", "NO": "0"}}
    assert G.stats(b)["*"]["n"] == 38
