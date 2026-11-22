"""Owner 2026-11-22: reward-aware inventory accumulation. Rewarded markets
are selected first (evidence: +$0.63 on 74 rewarded markets, -$25.71 on 167
unrewarded); an add to an already unpaired side must clear a marginal-edge
test against the international fair value that grows with concentration
and with the market's jump risk and shrinks with a verified reward."""
from decimal import Decimal as D

import pytest

from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_two_sided import FakeVenue

T = D("0.01")


def q(pos, cost, bid, ask, **c):
    return R.quotes(yes_bid=bid, yes_ask=ask, tick=T, pos=pos, cost=cost,
                    c=c or None)


# ------------------------------------------------ marginal test (pure)
def test_without_the_switch_quotes_are_exactly_as_before():
    held = ({"YES": 0, "NO": 2}, {"YES": 0, "NO": "1.32"})
    a = q(*held, "0.30", "0.31")
    assert a["NO"]["role"] == "extend" and a["NO"]["px"] == D("0.68")
    assert "extend_ev" not in a


def test_the_genoa_add_is_refused_on_a_soccer_market():
    """22 Nov: NO 2 @0.66 unpaired; book 0.30/0.31, intl mid 0.285. The add
    at 0.68 had 3.5c of edge; soccer needs 1 + 1 + 3 = 5 ticks."""
    held = ({"YES": 0, "NO": 2}, {"YES": 0, "NO": "1.32"})
    a = q(*held, "0.30", "0.31", extend_ev=True, fair_yes="0.285",
          extend_extra_ticks=3)
    assert a["NO"] is None and a["why_NO"] == "extend_marginal_edge"
    ev = a["extend_ev"]["NO"]
    assert D(ev["edge"]) == D("0.035") and D(ev["need"]) == D("0.05")
    assert ev["unpaired_clips"] == 1
    assert a["YES"]["role"] == "complete"          # the completion still rests


def test_the_same_add_passes_where_jump_risk_is_low():
    held = ({"YES": 0, "NO": 2}, {"YES": 0, "NO": "1.32"})
    a = q(*held, "0.30", "0.31", extend_ev=True, fair_yes="0.285",
          extend_extra_ticks=0)                    # e.g. basketball: 2 ticks
    assert a["NO"]["role"] == "extend" and a["NO"]["px"] == D("0.68")


def test_required_edge_grows_with_concentration():
    """The same 2.5c of edge: enough for the first add, not for the second."""
    one = q({"YES": 0, "NO": 2}, {"YES": 0, "NO": "1.32"}, "0.30", "0.31",
            extend_ev=True, fair_yes="0.295", extend_extra_ticks=0)
    assert one["NO"] is not None                   # 1 clip: needs 2 ticks
    two = q({"YES": 0, "NO": 4}, {"YES": 0, "NO": "2.64"}, "0.30", "0.31",
            extend_ev=True, fair_yes="0.295", extend_extra_ticks=0,
            residual_cap_usd="9")
    assert two["NO"] is None and two["why_NO"] == "extend_marginal_edge"
    assert two["extend_ev"]["NO"]["unpaired_clips"] == 2
    assert D(two["extend_ev"]["NO"]["need"]) == D("0.03")
    more = q({"YES": 0, "NO": 4}, {"YES": 0, "NO": "2.64"}, "0.30", "0.31",
             extend_ev=True, fair_yes="0.27", extend_extra_ticks=0,
             residual_cap_usd="9")
    assert more["NO"] is not None                  # 5c of edge clears it


def test_a_verified_reward_lowers_the_bar_by_its_credit():
    held = ({"YES": 0, "NO": 2}, {"YES": 0, "NO": "1.32"})
    no = q(*held, "0.30", "0.31", extend_ev=True, fair_yes="0.29",
           extend_extra_ticks=1)                   # needs 3 ticks, has 3
    assert no["NO"] is not None
    hard = q(*held, "0.30", "0.31", extend_ev=True, fair_yes="0.30",
             extend_extra_ticks=1)                 # 2 ticks of edge: refused
    assert hard["why_NO"] == "extend_marginal_edge"
    credit = q(*held, "0.30", "0.31", extend_ev=True, fair_yes="0.30",
               extend_extra_ticks=0)               # reward credit -1 tick
    assert credit["NO"] is not None


def test_no_fair_value_means_no_add_and_never_a_deeper_bid():
    held = ({"YES": 0, "NO": 2}, {"YES": 0, "NO": "1.32"})
    a = q(*held, "0.30", "0.31", extend_ev=True, fair_yes=None)
    assert a["NO"] is None and a["why_NO"] == "extend_no_fair"
    b = q(*held, "0.30", "0.31", extend_ev=True, fair_yes="0.30",
          extend_extra_ticks=3)
    assert b["NO"] is None                          # refused, not re-priced


def test_openings_replenishment_and_completions_are_untouched():
    flat = q({"YES": 0, "NO": 0}, {"YES": 0, "NO": 0}, "0.48", "0.50",
             extend_ev=True, fair_yes=None)
    assert flat["YES"]["role"] == "open" and flat["NO"]["role"] == "open"
    paired = q({"YES": 2, "NO": 2}, {"YES": "0.94", "NO": "0.98"}, "0.48",
               "0.50", extend_ev=True, fair_yes=None)
    assert paired["YES"] is not None and paired["NO"] is not None
    held = ({"YES": 0, "NO": 2}, {"YES": 0, "NO": "1.32"})
    a = q(*held, "0.30", "0.31", extend_ev=True, fair_yes=None)
    assert a["YES"]["role"] == "complete"           # no fair needed


# ---------------------------------------------------------------- engine
class Guard:
    require = True
    max_age = 20.0

    def __init__(self, mid):
        self.mid, self.stats = mid, {"blocked": 0, "ok": 0, "no_intl": 0}
        g = self

        class Intl:
            def fresh(self, tok, age):
                if g.mid is None or not tok:
                    return None
                m = D(str(g.mid))
                return {"bid": m - D("0.005"), "ask": m + D("0.005"), "t": 0}
        self.intl = Intl()

    def token(self, slug):
        return "TOK"

    def prefetch(self, slugs):
        pass

    def track(self, slugs):
        pass

    def verdict(self, slug, prices, tick):
        return {"ok": True, "why": "intl_confirms"}


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setattr(M, "TOTAL_CAP", D("20"))
    monkeypatch.setenv(M.ENABLED_ENV, "YES")
    monkeypatch.delenv(M.SUBMISSION_LOCK_ENV, raising=False)
    v = FakeVenue()
    v.states = {}
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    return e, v


def test_engine_refuses_the_genoa_add_and_logs_why(eng, capsys):
    e, v = eng
    slug = "atc-sea-sas-gen-2026-11-22-gen"
    v.bid, v.ask = D("0.30"), D("0.31")
    v.states = {slug: {"type": "soccer", "period": "1H", "elapsed": "20'"}}
    e.guard = Guard(0.285)
    mk = e.b.m(slug)
    mk["pos"]["NO"], mk["cost"]["NO"] = D("2"), D("1.32")
    assert e.rn1_extend_cfg(slug) == {"extend_ev": True,
                                      "fair_yes": D("0.285"),
                                      "extend_extra_ticks": 3}
    e.step_market(slug)
    assert [(o["side"]) for o in v.orders.values() if o["open"]] == ["YES"]
    out = capsys.readouterr().out
    assert "extend_marginal_edge" in out and '"need":"0.05"' in out


def test_engine_jump_ticks_by_event_type_and_reward_credit(eng, monkeypatch):
    e, v = eng
    e.guard = Guard(0.5)
    for typ, extra in (("soccer", 3), ("hockey", 2), ("football", 1),
                       ("tennis", 1), ("basketball", 0), ("", 0)):
        v.states = {"aec-x": {"type": typ}}
        assert e.rn1_extend_cfg("aec-x")["extend_extra_ticks"] == extra
    v.states = {"aec-x": {"type": "soccer"}}
    monkeypatch.setattr(e, "rn1_reward", lambda s: {"pool": 30})
    assert e.rn1_extend_cfg("aec-x")["extend_extra_ticks"] == 2
    e.guard = Guard(None)
    assert e.rn1_extend_cfg("aec-x")["fair_yes"] is None    # -> no add
    e.guard = None
    assert e.rn1_extend_cfg("aec-x") == {}        # no fair source configured
    e.guard = Guard(0.5)
    monkeypatch.setattr(R, "EXTEND_EV", False)
    assert e.rn1_extend_cfg("aec-x") == {}


def targets(*slugs):
    return [{"slug": s, "market_meta": {"bestBidQuote": "0.48",
                                        "bestAskQuote": "0.50"}}
            for s in slugs]


def test_rewarded_markets_are_selected_first_richest_pool_first(
        eng, monkeypatch, capsys):
    e, v = eng
    monkeypatch.setattr(M, "MAX_MARKETS", 2)
    v.bid, v.ask = D("0.48"), D("0.50")
    v.targets = targets("aec-nba-a-b-2026-11-22", "aec-nfl-c-d-2026-11-22",
                        "aec-cfb-e-f-2026-11-22", "aec-wta-g-h-2026-11-22")
    monkeypatch.setattr(e, "reward_programs", lambda slugs: {
        "aec-cfb-e-f-2026-11-22": {"pool_rate_usd_s": 0.002,
                                   "period": "live"},
        "aec-wta-g-h-2026-11-22": {"pool_rate_usd_s": 0.010,
                                   "period": "live"}})
    e.select(v.account())
    assert list(e.b.d["markets"]) == ["aec-wta-g-h-2026-11-22",
                                      "aec-cfb-e-f-2026-11-22"]
    assert "RN1_REWARD_RANK" in capsys.readouterr().out


def test_reward_only_selects_nothing_without_a_programme(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "RN1_REWARD_ONLY", True)
    v.bid, v.ask = D("0.48"), D("0.50")
    v.targets = targets("aec-nba-a-b-2026-11-22", "aec-nfl-c-d-2026-11-22")
    monkeypatch.setattr(e, "reward_programs", lambda slugs: {})
    e.select(v.account())
    assert e.b.d["markets"] == {}
    monkeypatch.setattr(e, "reward_programs", lambda slugs: {
        "aec-nfl-c-d-2026-11-22": {"pool_rate_usd_s": 0.001,
                                   "period": "live"}})
    e.select(v.account())
    assert list(e.b.d["markets"]) == ["aec-nfl-c-d-2026-11-22"]


def test_ranking_off_or_a_failed_programme_read_keeps_the_old_order(
        eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "MAX_MARKETS", 1)
    v.bid, v.ask = D("0.48"), D("0.50")
    v.targets = targets("aec-nba-a-b-2026-11-22", "aec-wta-g-h-2026-11-22")

    def boom(slugs):
        raise RuntimeError("incentives down")
    monkeypatch.setattr(e, "reward_programs", boom)
    e.select(v.account())
    assert list(e.b.d["markets"]) == ["aec-nba-a-b-2026-11-22"]
    e.b.d["markets"].clear()
    monkeypatch.setattr(M, "RN1_REWARD_RANK", False)
    monkeypatch.setattr(e, "reward_programs", lambda slugs: {
        "aec-wta-g-h-2026-11-22": {"pool_rate_usd_s": 1, "period": "live"}})
    e.select(v.account())
    assert list(e.b.d["markets"]) == ["aec-nba-a-b-2026-11-22"]


def test_boot_reports_the_reward_aware_settings():
    c = M.safeguards_cfg()
    assert c["rn1_reward_rank"] is True and c["rn1_reward_only"] is False
    assert c["rn1_extend_ev"]["jump_ticks"]["soccer"] == 3
    assert c["rn1_extend_ev"]["on"] is True
