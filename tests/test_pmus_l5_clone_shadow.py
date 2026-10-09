"""Offline proof of the clone shadow: a scripted gateway, no network.

The assertions that matter are the refusals -- out-of-domain states, unseen
queues, touch-without-trade -- because the service's value depends on it
never overstating a fill.
"""
import json
import os
from decimal import Decimal as D

import pytest

from src import pmus_l5_clone_shadow as CS

MODELS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "analysis", "l5", "clone", "models")


def book(bid, ask, bid_qty=100, ask_qty=100, traded=0.0, last=None):
    return {"marketData": {
        "bids": [{"px": {"value": bid}, "qty": bid_qty}],
        "offers": [{"px": {"value": ask}, "qty": ask_qty}],
        "stats": {"sharesTraded": traded, "notionalTraded": traded * 50,
                  "lastTradePx": last},
        "state": "OPEN", "transactTime": None}}


def market(slug, start="2026-09-23T13:45:00Z", end="2026-09-23T14:00:00Z"):
    return {"slug": slug, "category": "crypto", "startTime": start,
            "endDate": end}


class Gateway:
    """Scripted gateway. `books[slug]` may be a list consumed per poll."""

    def __init__(self, markets, books):
        self.markets = markets
        self.books = {k: list(v) for k, v in books.items()}
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        if path.startswith("/v1/markets?"):
            if "offset=0" in path:
                return {"markets": self.markets}
            return {"markets": []}
        slug = path.split("/v1/markets/")[1].split("/book")[0]
        seq = self.books.get(slug)
        if not seq:
            raise KeyError(slug)
        return seq[0] if len(seq) == 1 else seq.pop(0)


# ---------------------------------------------------------------- units ----
def test_queue_ahead_counts_better_and_same_price_for_both_sides():
    obs = {"book_levels": {"bids": [{"px": 0.60, "qty": 10},
                                    {"px": 0.59, "qty": 5}],
                           "offers": [{"px": 0.62, "qty": 7},
                                      {"px": 0.63, "qty": 3}]}}
    # YES buy at 0.59: 10 better (0.60) + 5 at our level
    q = CS.queue_ahead(obs, True, D("0.59"))
    assert q["better_px_shares"] == 10.0 and q["same_px_shares"] == 5.0
    # NO buy at 0.38 == YES 0.62: nothing better (0.62 is the best offer),
    # 7 at our level
    q = CS.queue_ahead(obs, False, D("0.38"))
    assert q["better_px_shares"] == 0.0 and q["same_px_shares"] == 7.0


def test_unknown_ladder_is_never_an_empty_queue():
    q = CS.queue_ahead({"book_levels": {"bids": [], "offers": []}}, True,
                       D("0.5"))
    assert q["known"] is False


def test_own_side_frame_conversion():
    q = CS.own_side_quote({"bid": 0.60, "ask": 0.62}, False)
    assert q["maker_px"] == D("1") - D("0.62")
    assert q["taker_px"] == D("1") - D("0.60")


def test_features_refuse_out_of_domain_before_any_virtual_fill():
    inv = CS.VirtualInventory()
    f = CS.features(inv, {"resolved": True, "t_frac": 0.1, "tte_s": 800,
                          "duration_s": 900}, 1.0)
    assert f["in_domain"] is False and "NO_VIRTUAL_FILL_YET" in f["why"]


def test_features_refuse_when_clock_unresolved():
    inv = CS.VirtualInventory()
    inv.add(side_yes=True, px=D("0.5"), qty=D("1"), t=1.0)
    f = CS.features(inv, {"resolved": False, "basis": "UNRESOLVED"}, 2.0)
    assert f["in_domain"] is False and "CLOCK_UNRESOLVED" in f["why"]


def test_feature_vector_matches_training_definition():
    inv = CS.VirtualInventory()
    inv.add(side_yes=True, px=D("0.40"), qty=D("2"), t=100.0)
    inv.add(side_yes=False, px=D("0.55"), qty=D("1"), t=130.0)
    f = CS.features(inv, {"resolved": True, "t_frac": 0.5, "tte_s": 450.0,
                          "duration_s": 900.0}, 130.0)
    n = f["named"]
    # last side is NO: same=1, opp=2 -> imbalance (1-2)/3
    assert n["imbalance"] == pytest.approx(-1 / 3)
    assert n["matched_frac"] == pytest.approx(2 / 3)
    assert n["residual_shares"] == 1.0
    assert n["pair_cost_sofar"] == pytest.approx(0.95)
    assert n["price"] == 0.55 and n["price_is_favourite"] == 1.0
    assert n["n_fills"] == 2 and n["gap_prev_s"] == 30.0
    assert n["dur_5m"] == 0.0 and n["opp_has_any"] == 1.0


def test_entry_rule_refuses_tight_spread_and_expiry():
    clock = {"resolved": True, "tte_s": 600, "expired": False}
    assert CS.entry_rule({"bid": 0.50, "ask": 0.51}, clock)["why"] == \
        "SPREAD_UNDER_TWO_TICKS"
    assert CS.entry_rule({"bid": 0.4, "ask": 0.6},
                         {"resolved": True, "tte_s": 5})["why"] == \
        "TOO_CLOSE_TO_EXPIRY"


def test_entry_rule_picks_the_cheaper_side_under_the_ceiling():
    r = CS.entry_rule({"bid": 0.30, "ask": 0.60},
                      {"resolved": True, "tte_s": 600, "expired": False})
    assert r["propose"] is True
    # YES maker 0.30 vs NO maker 1-0.60=0.40 -> YES is cheaper
    assert r["side_yes"] is True and r["px"] == D("0.30")


def test_complement_above_pair_ceiling_is_refused():
    inv = CS.VirtualInventory()
    inv.add(side_yes=True, px=D("0.80"), qty=D("1"), t=1.0)
    out = CS.intended_order("early", {"EXTEND": 0.1, "COMPLEMENT": 0.8,
                                      "STOP": 0.1}, inv,
                            {"bid": 0.70, "ask": 0.75},
                            {"resolved": True, "tte_s": 500})
    assert out["propose"] is False
    assert out["why"] == "COMPLEMENT_ABOVE_PAIR_CEILING"


def test_stop_never_proposes_an_order():
    inv = CS.VirtualInventory()
    inv.add(side_yes=True, px=D("0.4"), qty=D("1"), t=1.0)
    out = CS.intended_order("late", {"EXTEND": 0.2, "COMPLEMENT": 0.2,
                                     "STOP": 0.6}, inv,
                            {"bid": 0.4, "ask": 0.5},
                            {"resolved": True, "tte_s": 500})
    assert out["propose"] is False and out["timing"] == "WAIT"


# ----------------------------------------------------------- feasibility ---
def _cand(px="0.40", side_yes=True, ahead=5.0, t_post=1000.0):
    return {"id": "x", "policy": "early", "slug": "s", "side_yes": side_yes,
            "px": px, "qty": "1", "t_post": t_post, "status": CS.PENDING,
            "queue_ahead_at_post": {"known": True, "total_ahead": ahead},
            "state_at_post": {"mid": "0.45"}, "markouts": {}}


def obs_row(t, bid, ask, traded, last=None):
    # `t` must be ISO: pmus_status_report.infer_trades timestamps trades by
    # parsing it, and a non-ISO value silently drops every print.
    return {"_poll_t": t, "t": CS._utc(t), "bid": bid, "ask": ask,
            "shares_traded_cum": traded, "last_trade_px": last,
            "book_levels": {"bids": [{"px": bid, "qty": 10}],
                            "offers": [{"px": ask, "qty": 10}]}}


def test_touch_alone_is_not_a_fill():
    c = _cand()
    obs = [obs_row(1000, 0.39, 0.41, 0), obs_row(1100, 0.38, 0.40, 0)]
    CS.advance_candidate(c, obs, 2000.0)      # past FOLLOW_S
    assert c["status"] == CS.TOUCHED_NO_TRADE
    assert c["touched"] is True


def test_volume_inside_the_queue_is_queue_unproven():
    c = _cand(ahead=50.0)
    obs = [obs_row(1000, 0.39, 0.41, 0),
           obs_row(1100, 0.38, 0.40, 10, last=0.38)]
    CS.advance_candidate(c, obs, 2000.0)
    assert c["status"] == CS.QUEUE_UNPROVEN
    assert c["traded_through"] > 0 and c["excess_over_queue"] < 0


def test_volume_past_the_queue_clears_only_the_excess():
    c = _cand(ahead=5.0)
    obs = [obs_row(1000, 0.39, 0.41, 0),
           obs_row(1100, 0.38, 0.40, 5.5, last=0.38)]
    CS.advance_candidate(c, obs, 1200.0)
    assert c["status"] == CS.QUEUE_CLEARED
    assert c["virtual_fill_qty"] == pytest.approx(0.5)


def test_unknown_queue_never_clears():
    c = _cand()
    c["queue_ahead_at_post"] = {"known": False}
    obs = [obs_row(1000, 0.39, 0.41, 0),
           obs_row(1100, 0.38, 0.40, 999, last=0.38)]
    CS.advance_candidate(c, obs, 2000.0)
    assert c["status"] == CS.QUEUE_UNPROVEN
    assert "QUEUE_UNKNOWN_AT_POST" in c["why"]


def test_markouts_carry_actual_lag_and_signed_adverse_selection():
    c = _cand()
    obs = [obs_row(1000, 0.44, 0.46, 0),
           obs_row(1013, 0.40, 0.42, 0),       # mid 0.41, fell 0.04
           obs_row(1035, 0.50, 0.52, 0),
           obs_row(1065, 0.44, 0.46, 0)]
    CS.advance_candidate(c, obs, 1400.0)
    m10 = c["markouts"]["t+10s"]
    assert m10["actual_lag_s"] == 13.0
    # YES buyer, mid fell 0.45 -> 0.41: adverse, so negative
    assert m10["adverse_selection_per_share"] == pytest.approx(-0.04, abs=1e-6)
    m30 = c["markouts"]["t+30s"]
    # mid rose to 0.51: favourable for a YES buyer, so positive
    assert m30["adverse_selection_per_share"] == pytest.approx(0.06, abs=1e-6)
    assert m30["complement_available"] is True
    assert m30["maker_rebate_netted"] is False


# ---------------------------------------------------------------- service --
@pytest.mark.skipif(not os.path.exists(os.path.join(MODELS,
                                                    "l5_policy_early.pkl")),
                    reason="models not built in this checkout")
def test_end_to_end_both_arms_decide_and_persist(tmp_path):
    slug = "cpc-btc-updown-15m-2026-09-23-1345z"
    t0 = 1790171100.0 + 60.0   # one minute into the 13:45Z window
    gw = Gateway([market(slug)], {slug: [book(0.30, 0.60)]})
    clock = {"n": 0}

    def now():
        clock["n"] += 1
        return t0 + clock["n"] * 20.0

    cs = CS.CloneShadow(get_json=gw, sleep=None, now=now,
                        policies=CS.load_policies(MODELS),
                        state_dir=str(tmp_path))
    assert set(cs.policies) == {"early", "late"}
    universe = CS.eligible_markets(CS.discover(gw, sleep=None), now())
    assert universe and universe[0]["slug"] == slug
    cs.poll(universe)
    rows = cs.decide([slug])
    # one row per arm, both out of domain, both using the entry rule
    assert len(rows) == 2
    assert {r["policy"] for r in rows} == {"early", "late"}
    for r in rows:
        assert r["model_domain"]["in_domain"] is False
        assert r["intended_order"]["propose"] is True
        assert r["intended_order"]["TRANSLATION_IS_OURS"] is True
        assert r["model"] is None
    assert cs.append_rows("decisions", rows) is True
    assert cs.save() is True
    # a restart reads the same ledgers back
    cs2 = CS.CloneShadow(get_json=gw, sleep=None, now=now,
                         policies=cs.policies, state_dir=str(tmp_path))
    assert sum(len(v) for v in cs2.candidates.values()) == 2


@pytest.mark.skipif(not os.path.exists(os.path.join(MODELS,
                                                    "l5_policy_early.pkl")),
                    reason="models not built in this checkout")
def test_model_drives_the_action_once_inventory_exists(tmp_path):
    slug = "cpc-btc-updown-15m-2026-09-23-1345z"
    t0 = 1790171100.0 + 60.0
    gw = Gateway([market(slug)], {slug: [book(0.30, 0.60)]})
    cs = CS.CloneShadow(get_json=gw, sleep=None, now=lambda: t0,
                        policies=CS.load_policies(MODELS),
                        state_dir=str(tmp_path))
    cs.poll([{"slug": slug, "market": market(slug)}])
    cs.inv["early"].setdefault(slug, CS.VirtualInventory()).add(
        side_yes=True, px=D("0.30"), qty=D("1"), t=t0)
    rows = [r for r in cs.decide([slug]) if r["policy"] == "early"]
    r = rows[0]
    assert r["model_domain"]["in_domain"] is True
    assert set(r["model"]["proba"]) == {"EXTEND", "COMPLEMENT", "STOP"}
    assert abs(sum(r["model"]["proba"].values()) - 1.0) < 0.01
    assert r["model"]["PREDICTS"].startswith("L5's next observed action")
    assert r["intended_order"]["model_action"] in ("EXTEND", "COMPLEMENT",
                                                   "STOP")


def test_no_credentials_and_no_order_path_in_the_module():
    src = open("src/pmus_l5_clone_shadow.py").read()
    # the real contract: no credential read, no executor, no mutating verb
    for banned in ("PMUS_API_SECRET", "PMUS_API_KEY", "pmus_auth",
                   "micro_executor", "q1_executor", "submit_order",
                   "urlopen(req, data", "method=\"POST\"", "method='POST'"):
        assert banned not in src, banned
    assert "ReadOnlyTransport" not in src   # not even a read transport
    import inspect
    from src import pmus_l5_clone_shadow as mod
    for name, obj in vars(mod).items():
        if inspect.ismodule(obj):
            assert "executor" not in obj.__name__, obj.__name__


def test_discovery_walks_each_category_then_the_unfiltered_slate():
    """The first live cycle served exactly 15,000 rows -- the 30x500 cap --
    so anything past it, including the short-dated crypto windows, was
    never seen. Discovery must reach past a single capped walk."""
    pages = {}

    def gw(path):
        pages[path] = pages.get(path, 0) + 1
        if "categories=crypto" in path and "offset=0" in path:
            return {"markets": [{"slug": "cpc-btc-updown-15m-2026-10-09-0215z"}]}
        if "categories=" not in path and "offset=0" in path:
            return {"markets": [{"slug": "aec-nfl-a-b-2026-10-09"}]}
        return {"markets": []}

    rows = CS.discover(gw, sleep=None, categories=["crypto", "sports"])
    slugs = {r["slug"] for r in rows}
    assert "cpc-btc-updown-15m-2026-10-09-0215z" in slugs
    assert "aec-nfl-a-b-2026-10-09" in slugs
    # the category walk tags the row so ranking/reporting still has one
    crypto = [r for r in rows if r["slug"].startswith("cpc-")][0]
    assert crypto["category"] == "crypto"


def test_discovery_deduplicates_across_walks():
    def gw(path):
        if "offset=0" in path:
            return {"markets": [{"slug": "dup", "category": "crypto"}]}
        return {"markets": []}
    rows = CS.discover(gw, sleep=None, categories=["crypto", "sports"])
    assert [r["slug"] for r in rows] == ["dup"]
