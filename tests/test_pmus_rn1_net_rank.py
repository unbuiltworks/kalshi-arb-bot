"""Net-profit ranking (RN1_NET_RANK): reward minus expected fill cost per
hour, at the best bid and one tick behind, per market."""
import time
from decimal import Decimal as D

from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_continuous_controller import (  # noqa: F401
    SLUG, eng, resting)
from tests.test_pmus_rn1_ladder import live  # noqa: F401


class FakeRewards:
    """reward per hour = rate[yes_px] (by the YES bid's price)."""

    def __init__(self, by_px):
        self.by_px = by_px
        self.calls = []

    def quote_estimate(self, book, per, y, n, q, tick):
        self.calls.append((round(y, 4), round(n, 4), q))
        return {"est_usd_per_hour": self.by_px[round(y, 2)], "share": 0.1,
                "max_spread_ok": None}


def _set_rates(monkeypatch, touch_rate, behind_rate, touch_loss,
               behind_loss, sizes=(D("10"),)):
    monkeypatch.setattr(M, "RN1_NET_SIZES", tuple(sizes))
    monkeypatch.setattr(M, "RN1_FILL_RATE", {0: {"": touch_rate},
                                             1: {"": behind_rate}})
    monkeypatch.setattr(M, "RN1_FILL_LOSS", {0: {"": touch_loss},
                                             1: {"": behind_loss}})


def test_off_by_default():
    assert M.RN1_NET_RANK is False


def test_competition_rates_parse(monkeypatch):
    monkeypatch.setenv("PMUS_X", "utr:0.5, atpdb:0.1,bad:x")
    got = M._comp_rates("PMUS_X", "0.2")
    assert got == {"": 0.2, "utr": 0.5, "atpdb": 0.1}
    tab = {0: got, 1: {"": 0.9}}
    assert M.rn1_fill_param(tab, 0, "aec-utr-a-b-2026-11-25") == 0.5
    assert M.rn1_fill_param(tab, 0, "aec-wta-a-b-2026-11-25") == 0.2
    assert M.rn1_fill_param(tab, 1, "aec-utr-a-b-2026-11-25") == 0.9


def test_prefers_one_tick_behind_when_fills_at_the_best_bid_cost_more(
        eng, monkeypatch):
    """10 a side. Best bid: reward 1.00/h, fills 0.5 x 10 x 2 sides x 5c
    = 0.50/h -> +0.50. Behind: reward 0.40/h, fills 0.1 x 10 x 2 x 3c =
    0.06/h -> +0.34. Best bid wins. With best-bid fills at 1.0/h it loses
    (1.00 - 1.00 = 0) and behind is chosen."""
    e, _v = eng
    fake = FakeRewards({0.40: 1.00, 0.39: 0.40})
    _set_rates(monkeypatch, 0.5, 0.1, 0.05, 0.03)
    got = e._net_score(SLUG, {}, {}, D("0.40"), D("0.45"), D("0.01"),
                       D("10"), fake)
    assert got["depth"] == 0
    assert abs(got["net_usd_per_hour"] - 0.50) < 1e-9
    assert abs(got["placements"][1]["net_usd_per_hour"] - 0.34) < 1e-9
    _set_rates(monkeypatch, 1.0, 0.1, 0.05, 0.03)
    got = e._net_score(SLUG, {}, {}, D("0.40"), D("0.45"), D("0.01"),
                       D("10"), fake)
    assert got["depth"] == 1
    assert e._net_choice[SLUG]["depth"] == 1


def test_a_placement_respects_the_pair_cost_limit(eng, monkeypatch):
    """One-tick book 0.48/0.49: at the best bid YES 0.48 + NO 0.51 = 0.99;
    the dearer side (NO) is lowered to 0.49 so the pair is 0.97."""
    e, _v = eng
    fake = FakeRewards({0.48: 0.5, 0.47: 0.2})
    _set_rates(monkeypatch, 0.0, 0.0, 0.0, 0.0)
    e._net_score(SLUG, {}, {}, D("0.48"), D("0.49"), D("0.01"), D("10"),
                 fake)
    y0, n0, _q = fake.calls[0]
    assert (y0, n0) == (0.48, 0.49) and y0 + n0 <= float(R.PAIR_CAP)


def test_ranking_keeps_only_positive_net_markets(eng, monkeypatch):
    e, _v = eng
    monkeypatch.setattr(M, "RN1_NET_RANK", True)
    nets = {"a": 0.5, "b": -0.1, "c": 0.9}
    monkeypatch.setattr(e, "_reward_share_score", lambda s, per: {
        "per_dollar_hour": nets[s] / 10, "net_usd_per_hour": nets[s]})
    monkeypatch.setattr(M, "RANK_SHARE_NEW_PER_ROUND", 10)
    scored = [(0, 0, "a"), (0, 0, "b"), (0, 0, "c"), (0, 0, "d")]
    rw = {s: {"pool_rate_usd_s": 0.01} for s in "abc"}     # d: no programme
    out = e.rank_by_reward_share(scored, rw)
    assert [r[2] for r in out] == ["c", "a"]


def test_join_follows_the_better_placement(live, monkeypatch):
    e, v = live
    v.bid, v.ask = D("0.46"), D("0.49")
    monkeypatch.setattr(M, "RN1_JOIN_TOUCH", True)
    monkeypatch.setattr(M, "RN1_NET_RANK", True)
    e._net_choice = {SLUG: {"depth": 1, "net_usd_per_hour": 0.2}}
    why = e.rn1_join_touch(SLUG, e.b.m(SLUG), {"bid": v.bid, "ask": v.ask},
                           D("0.01"), D("0"), time.time())
    assert why == "net_prefers_one_tick_behind"
    e._net_choice = {SLUG: {"depth": 0, "net_usd_per_hour": 0.2}}
    why = e.rn1_join_touch(SLUG, e.b.m(SLUG), {"bid": v.bid, "ask": v.ask},
                           D("0.01"), D("0"), time.time())
    assert why is None


def test_a_fill_clears_the_estimate_so_it_is_priced_again(eng):
    e, _v = eng
    mk = e.b.m(SLUG)
    mk["qualify_check"] = {"t": time.time(), "ok": True}
    e._net_choice = {SLUG: {"depth": 0}}
    e._share_cache = {SLUG: (time.monotonic(), {"per_dollar_hour": 1})}
    e.rn1_net_forget(SLUG)
    assert "qualify_check" not in mk
    assert SLUG not in e._net_choice and SLUG not in e._share_cache


def test_holding_inventory_with_no_positive_net_stops_replenishing(
        live, monkeypatch):
    """2 YES held: with the fresh estimate not positive only the
    completing NO bid rests; with it positive the YES side quotes too."""
    e, v = live
    monkeypatch.setattr(M, "RN1_NET_RANK", True)
    mk = e.b.m(SLUG)
    mk["pos"]["YES"], mk["cost"]["YES"] = D("2"), D("0.90")
    mk.setdefault("last_fill_t", {})["YES"] = time.time() - 3600
    monkeypatch.setattr(e, "rn1_quote_qualifies", lambda s, m: False)
    e.step_market(SLUG)
    rs = resting(v)
    assert rs and all(s_ == "NO" for s_, _p, _q in rs)
    stages = {o["stage"] for o in mk["orders"].values() if o.get("open")}
    assert stages == {"two_sided_complete"}
    monkeypatch.setattr(e, "rn1_quote_qualifies", lambda s, m: True)
    e.step_market(SLUG)
    assert any(s_ == "YES" for s_, _p, _q in resting(v))


def test_without_net_rank_inventory_markets_quote_as_before(
        live, monkeypatch):
    e, v = live
    mk = e.b.m(SLUG)
    mk["pos"]["YES"], mk["cost"]["YES"] = D("2"), D("0.90")
    mk.setdefault("last_fill_t", {})["YES"] = time.time() - 3600
    monkeypatch.setattr(e, "rn1_quote_qualifies", lambda s, m: False)
    e.step_market(SLUG)
    assert any(s_ == "YES" for s_, _p, _q in resting(v))


def test_a_block_stops_the_openings_behind_a_completion(live, monkeypatch):
    """Any block (here the campaign loss stop) leaves only the completing
    bid: the cheaper levels behind it are openings and must not rest."""
    e, v = live
    mk = e.b.m(SLUG)
    mk["pos"]["YES"], mk["cost"]["YES"] = D("2"), D("0.90")
    mk.setdefault("last_fill_t", {})["YES"] = time.time() - 3600
    monkeypatch.setattr(M, "RN1_PULL_THREATENED_ONLY", False)
    monkeypatch.setattr(M, "VOL_PAUSE_USD", D("0.001"))
    mk["vol_pause_until"] = time.time() + 600
    e.step_market(SLUG)
    stages = {o["stage"] for o in mk["orders"].values() if o.get("open")}
    assert stages == {"two_sided_complete"}


class SaturatingRewards:
    """A thin book: reward share saturates, so reward per hour is
    0.30 x min(qty, 2) / 2 at the best bid, half that behind."""

    def quote_estimate(self, book, per, y, n, q, tick):
        base = 0.30 if round(y, 2) == 0.40 else 0.15
        return {"est_usd_per_hour": base * min(q, 2.0) / 2.0, "share": 0.1,
                "max_spread_ok": None}


def test_the_smallest_qualifying_size_wins_in_a_thin_book(eng, monkeypatch):
    """Fill cost grows with size (1.5 fills per contract-hour, 3.5c a
    contract, both sides) while the reward stops at 2 contracts: 2 a side
    nets 0.30 - 0.21 = 0.09/h at the best bid, the most of 1/2/5/10."""
    e, _v = eng
    _set_rates(monkeypatch, 1.5, 0.5, 0.035, 0.035,
               sizes=(D("1"), D("2"), D("5"), D("10")))
    got = e._net_score(SLUG, {}, {}, D("0.40"), D("0.45"), D("0.01"),
                       D("10"), SaturatingRewards())
    assert got["qty"] == "2" and got["depth"] == 0
    assert abs(got["net_usd_per_hour"] - 0.09) < 1e-9
    assert e._net_choice[SLUG]["qty"] == D("2")


def test_the_chosen_size_caps_the_quoted_clip(live, monkeypatch):
    e, v = live
    monkeypatch.setattr(M, "RN1_NET_RANK", True)
    monkeypatch.setattr(M, "RN1_LIVE_QTY", D("10"))
    monkeypatch.setattr(e, "rn1_quote_qualifies", lambda s, m: True)
    e._net_choice = {SLUG: {"depth": 1, "qty": D("1"),
                            "net_usd_per_hour": 0.1}}
    e.step_market(SLUG)
    assert resting(v) and all(q == "1" for _s, _p, q in resting(v))
