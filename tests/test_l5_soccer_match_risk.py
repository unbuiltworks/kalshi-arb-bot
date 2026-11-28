"""Soccer: the three outcome markets of one match are ONE risk
(pmus_sports_soccer_fair match arithmetic; engine PMUS_RN1_SOCCER wiring,
default off). Every scenario states its arithmetic."""
import json
import time
from decimal import Decimal as D

import pytest

from src import pmus_rn1_replica as RN1
from src import pmus_sports_sharp as SH
from src import pmus_sports_soccer_fair as SF
from src import pmus_sports_two_sided as M

MATCH = "atc-lal-bet-ray-2026-11-28-"
H, DR, A = MATCH + "bet", MATCH + "draw", MATCH + "ray"
OUTS = ("bet", "draw", "ray")


def h(yq="0", yc="0", nq="0", nc="0"):
    return {"pos": {"YES": yq, "NO": nq}, "cost": {"YES": yc, "NO": nc}}


# ------------------------------------------------------------- pure maths
def test_match_of():
    mo = SF.match_of(DR)
    assert mo["match"] == "lal-bet-ray-2026-11-28"
    assert mo["outcomes"] == OUTS and mo["siblings"] == (H, DR, A)
    assert SF.match_of("aec-nba-bos-nyk-2026-11-28") is None
    assert SF.match_of(MATCH + "rma") is None
    assert SF.match_of("atc-lal-bet-ray-2026-11-28-sh-bet") is None


def test_single_yes():
    # YES bet 2 @0.50 = $1.00: bet +2-1 = +1; draw -1; ray -1
    p = SF.outcome_pnl({"bet": h("2", "1.00")}, OUTS)
    assert p == {"bet": D(1), "draw": D(-1), "ray": D(-1)}
    assert SF.match_loss({"bet": h("2", "1.00")}, OUTS) == 1


def test_two_yes_do_not_offset_on_the_third_result():
    # YES bet 2 @0.50 + YES draw 2 @0.25 = $1.50
    # bet: 2-1.5=+0.5, draw: +0.5, ray: -1.5 -> worst -1.5 (both lose on ray)
    hold = {"bet": h("2", "1.00"), "draw": h("2", "0.50")}
    assert SF.outcome_pnl(hold, OUTS) == {"bet": D("0.5"), "draw": D("0.5"),
                                          "ray": D("-1.5")}
    assert SF.match_worst(hold, OUTS) == D("-1.5")


def test_yes_and_no_on_different_outcomes():
    # YES bet 1 @0.50 + NO draw 1 @0.70 = $1.20
    # bet: 1 + 1 - 1.2 = +0.8; draw: 0 + 0 - 1.2 = -1.2; ray: 0+1-1.2 = -0.2
    hold = {"bet": h("1", "0.50"), "draw": h(nq="1", nc="0.70")}
    assert SF.outcome_pnl(hold, OUTS) == {"bet": D("0.8"), "draw": D("-1.2"),
                                          "ray": D("-0.2")}


def test_pair_in_one_market_stays_locked():
    # YES bet 2 @0.50 + NO bet 2 @0.45 = $1.90: every result pays 2 -> +0.10
    hold = {"bet": h("2", "1.00", "2", "0.90")}
    assert set(SF.outcome_pnl(hold, OUTS).values()) == {D("0.10")}
    assert SF.match_loss(hold, OUTS) == 0


def test_dutch_below_one_is_locked():
    # YES 2 on each: 0.45+0.27+0.25 = 0.97 per set; 2 sets cost 1.94;
    # exactly one pays 2 whatever happens -> +0.06 on every result
    hold = {"bet": h("2", "0.90"), "draw": h("2", "0.54"),
            "ray": h("2", "0.50")}
    assert set(SF.outcome_pnl(hold, OUTS).values()) == {D("0.06")}
    assert SF.dutch_lock(hold, OUTS) == {"sets": D(2), "set_cost": D("0.97"),
                                         "locked_pnl": D("0.06")}
    # the per-market book (each unpaired side worthless) would say -1.94
    per = sum(min(D(x["pos"]["YES"]), D(x["pos"]["NO"])) -
              D(x["cost"]["YES"]) - D(x["cost"]["NO"]) for x in hold.values())
    assert per == D("-1.94")


def test_dutch_above_one_is_a_locked_loss():
    hold = {"bet": h("1", "0.50"), "draw": h("1", "0.30"),
            "ray": h("1", "0.25")}
    assert SF.match_worst(hold, OUTS) == D("-0.05")
    assert SF.dutch_lock(hold, OUTS)["locked_pnl"] == D("-0.05")


def test_resting_bids_fill_adversely_per_result():
    # hold YES bet 2 @0.50; resting YES draw 2 @0.25
    # bet: +1 + min(0, 2*(0-0.25)) = 0.5; draw: -1 + 0 = -1 (the fill wins)
    # ray: -1 - 0.5 = -1.5 -> worst -1.5; held-only worst -1 -> +0.5 inc
    hold = {"bet": h("2", "1.00")}
    rest = [("draw", "YES", "0.25", "2")]
    assert SF.outcome_pnl(hold, OUTS, rest) == {
        "bet": D("0.5"), "draw": D(-1), "ray": D("-1.5")}


def test_unknown_outcome_and_bad_figures_raise():
    with pytest.raises(ValueError):
        SF.outcome_pnl({"rma": h("1", "0.5")}, OUTS)
    with pytest.raises(ValueError):
        SF.outcome_pnl({"bet": h("-1", "0.5")}, OUTS)
    with pytest.raises(ValueError):
        SF.outcome_pnl({"bet": h("NaN", "0.5")}, OUTS)
    with pytest.raises(ValueError):
        SF.outcome_pnl({}, ("a", "a", "b"))


def test_three_way_consistency():
    assert SF.three_way_consistent(["0.50", "0.26", "0.25"]) == \
        (True, D("1.01"))
    assert SF.three_way_consistent(["0.55", "0.30", "0.25"])[0] is False
    assert SF.three_way_consistent(["0.50", None, "0.25"]) == (False, None)
    assert SF.three_way_consistent(["0.50", "0.26"]) == (False, None)
    assert SF.three_way_consistent(["0", "0.5", "0.5"])[0] is False


# ------------------------------------------------------------ engine stub
class _Book:
    def __init__(self):
        self.d = {"markets": {}}

    def m(self, slug):
        return self.d["markets"].setdefault(slug, {
            "pos": {"YES": "0", "NO": "0"}, "cost": {"YES": "0", "NO": "0"},
            "orders": {}, "done": False})


def _engine():
    e = object.__new__(M.Engine)
    e.b = _Book()
    e.since = None
    e.guard = None
    e._res_rebased = True
    return e


def _mk(e, slug, pos=("0", "0"), cost=("0", "0"), orders=()):
    mk = e.b.m(slug)
    mk["pos"] = {"YES": pos[0], "NO": pos[1]}
    mk["cost"] = {"YES": cost[0], "NO": cost[1]}
    for i, (sd, px, q) in enumerate(orders):
        mk["orders"]["o%d%s" % (i, slug[-3:])] = {
            "side": sd, "px": px, "qty": q, "filled": "0", "open": True,
            "stage": "rn1_open", "placed_at": 1.0}
    return mk


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(M, "RN1_SOCCER", True)
    monkeypatch.setattr(M, "RN1_SOCCER_LEAGUES", ("lal",))


def test_soccer_ok_needs_flag_and_allowlist(monkeypatch):
    e = _engine()
    assert not e.rn1_soccer_ok(H)                     # default off
    monkeypatch.setattr(M, "RN1_SOCCER", True)
    assert not e.rn1_soccer_ok(H)                     # empty allowlist
    monkeypatch.setattr(M, "RN1_SOCCER_LEAGUES", ("sea",))
    assert not e.rn1_soccer_ok(H)                     # league not listed
    monkeypatch.setattr(M, "RN1_SOCCER_LEAGUES", ("lal",))
    assert e.rn1_soccer_ok(H) and e.rn1_soccer_ok(DR)
    assert not e.rn1_soccer_ok("aec-nba-bos-nyk-2026-11-28")


def test_default_off_resting_worst_unchanged(monkeypatch):
    # two resting YES bids on two outcomes, summed per market as before
    e = _engine()
    _mk(e, H, orders=[("YES", "0.50", "2")])
    _mk(e, DR, orders=[("YES", "0.25", "2")])
    assert e.own_resting_worst() == D("1.50")
    monkeypatch.setattr(M, "RN1_SOCCER", True)       # allowlist still empty
    assert e.own_resting_worst() == D("1.50")
    assert e.rn1_soccer_book_adjust() == 0


def test_joint_resting_worst_dutch(on):
    # resting YES on all three (0.45+0.27+0.25 = 0.97 x 2): per market the
    # sum is 1.94; jointly, whatever happens, worst is: bet: 2*.55 lost?
    # result bet: bet fill +2*(1-.45)=+1.1 (not taken), draw -0.54, ray -0.5
    # -> -1.04; draw: -0.9 -0.5 = -1.4; ray: -0.9 -0.54 = -1.44 -> 1.44
    e = _engine()
    _mk(e, H, orders=[("YES", "0.45", "2")])
    _mk(e, DR, orders=[("YES", "0.27", "2")])
    _mk(e, A, orders=[("YES", "0.25", "2")])
    assert e.own_resting_worst() == D("1.44")


def test_joint_resting_worst_never_below_true(on):
    # hold YES bet 2 @0.50 (worst -1); resting YES draw 2 @0.25 -> worst -1.5
    e = _engine()
    _mk(e, H, pos=("2", "0"), cost=("1.00", "0"))
    _mk(e, DR, orders=[("YES", "0.25", "2")])
    assert e.own_resting_worst() == D("0.50")
    # with an extra hypothetical YES ray 2 @0.25: ray result now pays 2 ...
    # bet: +1 -0.5 -0.5 = 0; draw: -1 +0 -0.5 = -1.5; ray: -1 -0.5 +0 = -1.5
    assert e.own_resting_worst(extra=(A, "YES", D("0.25"), D("2"))) == \
        D("0.50")


def test_book_adjust_dutch_held(on):
    # held dutch YES 2 each @0.45/0.27/0.25: per-market book -1.94, exact
    # +0.06 -> adjustment +2.00 (the loss stop sees +0.06, not -1.94)
    e = _engine()
    for s_, c in ((H, "0.90"), (DR, "0.54"), (A, "0.50")):
        mk = _mk(e, s_, pos=("2", "0"), cost=(c, "0"))
        mk["orders"]["f" + s_[-3:]] = {"side": "YES", "px": "0.4",
                                       "qty": "2", "filled": "2",
                                       "open": False, "stage": "rn1_open",
                                       "placed_at": 1.0}
    assert e.rn1_soccer_book_adjust() == D("2.00")


def test_book_adjust_same_direction_no_relief(on):
    # YES bet + YES draw: exact worst -1.5 equals the per-market sum -1.5
    e = _engine()
    for s_, c in ((H, "1.00"), (DR, "0.50")):
        mk = _mk(e, s_, pos=("2", "0"), cost=(c, "0"))
        mk["orders"]["f"] = {"side": "YES", "px": "0.4", "qty": "2",
                             "filled": "2", "open": False,
                             "stage": "rn1_open", "placed_at": 1.0}
    assert e.rn1_soccer_book_adjust() == 0


def test_book_adjust_skips_unpiloted_or_settled(on):
    e = _engine()
    _mk(e, H, pos=("2", "0"), cost=("0.90", "0"))     # no pilot order
    _mk(e, DR, pos=("2", "0"), cost=("0.54", "0"))
    _mk(e, A, pos=("2", "0"), cost=("0.50", "0"))
    assert e.rn1_soccer_book_adjust() == 0
    for s_ in (H, DR, A):
        e.b.d["markets"][s_]["orders"]["f"] = {
            "side": "YES", "px": "0.4", "qty": "2", "filled": "2",
            "open": False, "stage": "rn1_open", "placed_at": 1.0}
    e.b.d["markets"][A]["settle_px"] = "1"
    assert e.rn1_soccer_book_adjust() == 0


def test_group_caps_use_existing_per_market_limits(on, monkeypatch):
    monkeypatch.setattr(RN1, "PER_MARKET_USD", D("8"))
    monkeypatch.setattr(RN1, "RESIDUAL_CAP_USD", D("3"))
    e = _engine()
    _mk(e, H, pos=("4", "0"), cost=("2.00", "0"))     # worst -2
    # YES draw 2 @0.40 more: ray result -2 -0.8 = -2.8 <= 3 -> ok
    assert e.rn1_soccer_group_why(DR, "YES", D("0.40"), D("2")) is None
    # YES draw 4 @0.40: ray -2 -1.6 = -3.6 > 3 -> refused (each market alone
    # would be 2.00 and 1.60 unpaired, both under 3)
    w = e.rn1_soccer_group_why(DR, "YES", D("0.40"), D("4"))
    assert w and w.startswith("match_worst_loss:3.6")
    # spend: committed spend of the match against PER_MARKET_USD (8)
    monkeypatch.setattr(RN1, "PER_MARKET_USD", D("3"))
    w = e.rn1_soccer_group_why(DR, "YES", D("0.40"), D("2"))
    assert w and w.startswith("match_spend:")


def test_group_refuses_settled_sibling(on):
    e = _engine()
    _mk(e, H)["settle_px"] = "0"
    assert e.rn1_soccer_group_why(DR, "YES", D("0.4"), D("1")) == \
        "soccer_group_unreadable"


# --------------------------------------------------- fair routing (engine)
def _mids(e, monkeypatch, mids, settled=True):
    monkeypatch.setattr(e, "intl_fair", lambda s: mids.get(s))
    monkeypatch.setattr(e, "intl_fair_band", lambda s: {
        "mid": mids.get(s), "lo": mids.get(s), "hi": mids.get(s),
        "settled": settled and mids.get(s) is not None})


def test_prob_intl_consistent(on, monkeypatch):
    e = _engine()
    _mids(e, monkeypatch, {H: D("0.50"), DR: D("0.26"), A: D("0.25")})
    p = e.rn1_directional_prob(DR)
    assert p and p["p"] == D("0.26") and p["source"] == "intl3"


def test_prob_intl_inconsistent_refuses_all_three(on, monkeypatch):
    e = _engine()
    _mids(e, monkeypatch, {H: D("0.55"), DR: D("0.30"), A: D("0.25")})
    e.b.d["sharp_shadow"] = {DR: {"kind": "soccer3", "fair_yes": "0.27",
                                  "t": time.time(), "odds_age_s": 1,
                                  "game_start": "2099-01-01T00:00:00Z"}}
    assert all(e.rn1_directional_prob(s_) is None for s_ in (H, DR, A))


def test_prob_sportsbook_pregame_only(on, monkeypatch):
    e = _engine()
    _mids(e, monkeypatch, {H: D("0.50"), DR: None, A: D("0.25")})
    st = {"kind": "soccer3", "fair_yes": "0.27", "t": time.time(),
          "odds_age_s": 5, "source": "pinnacle",
          "game_start": "2099-01-01T00:00:00Z"}
    e.b.d["sharp_shadow"] = {DR: dict(st)}
    p = e.rn1_directional_prob(DR)
    assert p and p["source"] == "sportsbook3:pinnacle"
    e.b.d["sharp_shadow"][DR]["game_start"] = "2020-01-01T00:00:00Z"
    assert e.rn1_directional_prob(DR) is None        # in play: refused
    e.b.d["sharp_shadow"][DR] = dict(st, t=time.time() - 10 ** 5)
    assert e.rn1_directional_prob(DR) is None        # stale


def test_prob_soccer3_ignored_without_flag(monkeypatch):
    e = _engine()
    monkeypatch.setattr(M, "RN1_DIR_SHARP", True)
    _mids(e, monkeypatch, {})
    e.b.d["sharp_shadow"] = {DR: {"kind": "soccer3", "fair_yes": "0.27",
                                  "t": time.time(), "odds_age_s": 1}}
    assert e.rn1_directional_prob(DR) is None
    assert e.rn1_has_independent_fair(DR) is False
    # a 2-way sharp entry still works exactly as before
    e.b.d["sharp_shadow"] = {"aec-x": {"fair_yes": "0.6", "t": time.time(),
                                       "odds_age_s": 1}}
    assert e.rn1_directional_prob("aec-x")["p"] == D("0.6")


# -------------------------------------------------------- cross hedge
def test_cross_hedge_third_leg_reduces_worst(on, monkeypatch):
    monkeypatch.setattr(M, "RN1_TF_HEDGE_GIVE_USD", D("0.01"))
    e = _engine()
    # held YES bet 2 @0.25 + YES ray 2 @0.25 = 1.00:
    # bet +1, draw -1, ray +1 -> worst -1; YES draw 2 @0.25 filled:
    # bet +0.5, draw +0.5, ray +0.5 -> worst +0.5 (a locked dutch at 0.75)
    _mk(e, H, pos=("2", "0"), cost=("0.50", "0"))
    _mk(e, A, pos=("2", "0"), cost=("0.50", "0"))
    assert e.rn1_soccer_hedge_ok(DR, "YES", D("0.25"), D("2"),
                                 D("0.25")) == "ok"
    assert e.rn1_soccer_hedge_ok(DR, "YES", D("0.27"), D("2"),
                                 D("0.25")) == "above_fair_plus_give"


def test_cross_hedge_single_other_outcome_does_not_reduce(on, monkeypatch):
    monkeypatch.setattr(M, "RN1_TF_HEDGE_GIVE_USD", D("0.01"))
    e = _engine()
    _mk(e, H, pos=("2", "0"), cost=("1.00", "0"))     # worst -1
    # + YES draw 2 @0.25: ray result -1.5 -> worse; not a hedge
    assert e.rn1_soccer_hedge_ok(DR, "YES", D("0.25"), D("2"),
                                 D("0.26")) == "does_not_reduce_worst"


def test_cross_hedge_off_without_give_or_flag(monkeypatch):
    e = _engine()
    assert e.rn1_soccer_hedge_ok(DR, "YES", D("0.25"), D("2"),
                                 D("0.25")) == "not_soccer"
    monkeypatch.setattr(M, "RN1_SOCCER", True)
    monkeypatch.setattr(M, "RN1_SOCCER_LEAGUES", ("lal",))
    monkeypatch.setattr(M, "RN1_TF_HEDGE_GIVE_USD", None)
    assert e.rn1_soccer_hedge_ok(DR, "YES", D("0.25"), D("2"),
                                 D("0.25")) == "no_hedge_give"


def test_trading_first_admits_cross_hedge_only(on, monkeypatch):
    from src import pmus_directional_alloc as DA
    monkeypatch.setattr(M, "RN1_TF_HEDGE_GIVE_USD", D("0.01"))
    monkeypatch.setattr(M, "RN1_TF_EDGE_USD", D("0.02"))
    e = _engine()
    _mk(e, H, pos=("2", "0"), cost=("0.50", "0"))
    _mk(e, A, pos=("2", "0"), cost=("0.50", "0"))
    mk = e.b.m(DR)
    prob = DA.probability(D("0.25"), D("0.25"), D("0.25"), source="intl3")
    assert e.rn1_trading_first_why(DR, mk, "YES", D("0.25"), "0.01", prob,
                                   qty=D("2")) is None
    assert mk["_tf_last"]["YES"]["cross_hedge"] == "ok"
    # not soccer-ok -> the ordinary edge refusal
    monkeypatch.setattr(M, "RN1_SOCCER", False)
    assert e.rn1_trading_first_why(DR, mk, "YES", D("0.25"), "0.01", prob,
                                   qty=D("2")) == "tf_negative_ev"


# -------------------------------------------------------- selection
def test_guard_tracks_siblings_only_with_flag(on, monkeypatch):
    e = _engine()
    _mk(e, H)
    assert set(e.guard_tracked()) == {H, DR, A}
    monkeypatch.setattr(M, "RN1_SOCCER", False)
    assert e.guard_tracked() == [H]


# -------------------------------------------------------- coverage
class _R:
    def __init__(self, body, headers):
        self._b, self.headers = json.dumps(body).encode(), headers

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_coverage_reports_soccer_keys():
    body = [{"key": "soccer_epl", "group": "Soccer"},
            {"key": "soccer_spain_la_liga", "group": "Soccer"},
            {"key": "soccer_brazil_campeonato", "group": "Soccer"},
            {"key": "basketball_nba", "group": "Basketball"}]
    c = SH.OddsClient(key="SECRETKEY", opener=lambda u, timeout=10: _R(
        body, {"x-requests-remaining": "10", "x-requests-used": "1"}))
    out = c.coverage()
    assert out["ok"]
    assert "epl=soccer_epl" in out["soccer_configured_offered"]
    assert "lal=soccer_spain_la_liga" in out["soccer_configured_offered"]
    assert "sea=soccer_italy_serie_a" in out["soccer_configured_missing"]
    assert out["soccer_offered_unmapped"] == ["soccer_brazil_campeonato"]
    assert "SECRETKEY" not in json.dumps(out)


def test_odds_fetch_lock_serialises_callers():
    c = SH.OddsClient(key="")
    assert c.fetch("soccer_epl") == [] and c.last_error == "NO_KEY"
    del c._fetch_lock                     # an old pickled client still works
    assert c.fetch("soccer_epl") == []


# ------------------------------------------- soccer sportsbook shadow path
def test_soccer_refresher_and_shadow_copy(monkeypatch):
    from tests import test_l5_soccer_3way_fair as F
    e = _engine()
    e._sharp_events = [{"slug": "lal-bet-ray-2026-11-28",
                        "markets": F.EVENT_MKS + [{"slug": "aec-x",
                                                   "sportsMarketType": "y"}]}]
    now = F.NOW
    calls = []

    class _Odds:
        def fetch(self, key):
            calls.append(key)
            return [F._event(F.PIN)]
    e._odds = _Odds()
    e._soccer_want = e.sharp_soccer_events(now)
    assert list(e._soccer_want) == ["lal-bet-ray-2026-11-28"]
    monkeypatch.setattr(M.time, "time", lambda: now)
    monkeypatch.setattr(M.time, "sleep",
                        lambda s: setattr(e, "stop", True))
    e.stop = False
    e._soccer_refresh_loop()                  # one pass (sleep stops it)
    assert calls == ["soccer_spain_la_liga"]
    e._soccer_thread = object()               # do not start a thread
    state = {"aec-other": {"fair_yes": "0.5", "t": now}}
    monkeypatch.setattr(M, "log", lambda *a, **k: None)
    assert e.sharp_soccer_shadow(state, now) == 3
    assert {k: v["fair_yes"] for k, v in state.items()
            if v.get("kind") == "soccer3"} == {H: "0.5", DR: "0.25",
                                               A: "0.25"}
    assert state["aec-other"] == {"fair_yes": "0.5", "t": now}
    # a market that stops pricing is removed (soccer3 entries only)
    e._soccer_fairs[H] = dict(e._soccer_fairs[H], fair=None, why="stale")
    e.sharp_soccer_shadow(state, now)
    assert H not in state and DR in state and "aec-other" in state


def test_soccer_events_outside_window_or_unmapped_league():
    from tests import test_l5_soccer_3way_fair as F
    e = _engine()
    e._sharp_events = [{"markets": F.EVENT_MKS}]
    assert e.sharp_soccer_events(F.NOW - 86400) == {}       # too early
    import copy
    ms = copy.deepcopy(F.EVENT_MKS)
    for m in ms:
        m["slug"] = m["slug"].replace("atc-lal-", "atc-xyz-")
    e._sharp_events = [{"markets": ms}]
    assert e.sharp_soccer_events(F.NOW) == {}


def test_replay_loader_and_sum_consistency(tmp_path):
    import importlib.util
    p = __import__("pathlib").Path(__file__).resolve().parents[1] / \
        "scripts" / "pmus_soccer_replay.py"
    spec = importlib.util.spec_from_file_location("soccer_replay", p)
    R = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(R)
    rows = []
    for t in range(3):
        for o, (b, a) in (("bet", (0.49, 0.51)), ("draw", (0.25, 0.27)),
                          ("ray", (0.24, 0.26))):
            rows.append({"slug": "lal-bet-ray-2026-11-28-" + o, "ti": t * 10,
                         "tu": t * 10, "ib": b, "ia": a, "ub": b, "ua": a,
                         "us": "MARKET_STATE_OPEN"})
    f = tmp_path / "x.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    fr = R.load([str(f)])
    assert set(fr) == {H, DR, A}
    c = R.sum_consistency(fr)["lal-bet-ray-2026-11-28"]
    # mids 0.50 + 0.26 + 0.25 = 1.01 -> within 0.04 in every bucket
    assert c["buckets_all_three"] == 3 and c["share_within_tol"] == 1.0
    assert c["median_abs_dev"] == 0.01
