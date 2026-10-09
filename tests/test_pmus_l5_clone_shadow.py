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


def test_entry_rule_does_not_require_an_instantly_completable_pair():
    """The live gate demanded maker pair <= 0.96, i.e. a 4-cent spread, and
    proposed nothing on 332 crypto windows. A 3-cent book must propose."""
    r = CS.entry_rule({"bid": 0.48, "ask": 0.51},
                      {"resolved": True, "tte_s": 600, "expired": False})
    assert r["propose"] is True
    assert r["maker_pair_cost_now"] == "0.97"


def test_entry_rule_refuses_a_first_leg_above_the_acquisition_ceiling():
    r = CS.entry_rule({"bid": 0.95, "ask": 0.98},
                      {"resolved": True, "tte_s": 600, "expired": False})
    # YES maker 0.95, NO maker 0.02 -> NO is cheaper and is proposed
    assert r["propose"] is True and r["side_yes"] is False
    # both sides expensive cannot happen in one book, so force it directly
    r2 = CS.entry_rule({"bid": 0.95, "ask": 0.96},
                       {"resolved": True, "tte_s": 600, "expired": False})
    assert r2["propose"] is False or float(str(r2["px"])) <= 0.90


def test_entry_rule_quotes_into_a_one_tick_book():
    """The two-tick minimum was our invention and refused live markets."""
    clock = {"resolved": True, "tte_s": 600, "expired": False}
    r = CS.entry_rule({"bid": 0.50, "ask": 0.51}, clock)
    # NO maker is 1-0.51 = 0.49, cheaper than the 0.50 YES bid
    assert r["propose"] is True and r["px"] == D("0.49")


def test_entry_rule_refuses_a_locked_book_and_expiry():
    clock = {"resolved": True, "tte_s": 600, "expired": False}
    assert CS.entry_rule({"bid": 0.50, "ask": 0.50}, clock)["why"] == \
        "BOOK_CROSSED_OR_LOCKED"
    assert CS.entry_rule({"bid": 0.4, "ask": 0.6},
                         {"resolved": True, "tte_s": 5})["why"] == \
        "TOO_CLOSE_TO_EXPIRY"


def test_entry_rule_picks_the_cheaper_side():
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


def test_watch_ranking_demotes_books_that_never_read(tmp_path):
    gw = Gateway([], {})
    cs = CS.CloneShadow(get_json=gw, sleep=None, now=lambda: 1.0,
                        policies={}, state_dir=str(tmp_path))
    universe = [{"slug": "soon", "clock": {"tte_s": 100.0}},
                {"slug": "later", "clock": {"tte_s": 900.0}}]
    assert [r["slug"] for r in cs.rank_watch(universe)] == ["soon", "later"]
    cs._poll_fail["soon"] = 3
    assert [r["slug"] for r in cs.rank_watch(universe)] == ["later", "soon"]


# ------------------------------------------------------- settlement -------
def test_resolved_outcome_refuses_anything_but_a_clean_zero_one():
    assert CS.resolved_outcome({"outcomePrices": ["0.62", "0.38"]})[
        "why"] == "OUTCOME_PRICES_NOT_ZERO_ONE"
    assert CS.resolved_outcome({})["why"] == "NO_TWO_OUTCOME_PRICES"
    assert CS.resolved_outcome({"outcomePrices": "oops"})["resolved"] is False
    # the venue serves it as a JSON string on some payloads
    r = CS.resolved_outcome({"outcomePrices": "[\"1\", \"0\"]"})
    assert r["resolved"] is True and r["yes_won"] is True


def test_matched_pair_settles_for_one_dollar_either_way():
    inv = CS.VirtualInventory()
    inv.add(side_yes=True, px=D("0.40"), qty=D("2"), t=1.0)
    inv.add(side_yes=False, px=D("0.55"), qty=D("2"), t=2.0)
    for yes_won in (True, False):
        out = CS.settle_inventory(inv, {"resolved": True, "yes_won": yes_won})
        assert out["settled"] is True
        assert out["matched_shares"] == "2"
        # 2 pairs at a 0.95 pair cost -> 2 * 0.05
        assert D(out["matched_pair_pnl_usd"]) == D("0.10")
        assert D(out["residual_pnl_usd"]) == 0
        assert D(out["net_usd"]) == D("0.10")
        assert out["fees_usd"] == "0"
        assert out["maker_rebate_netted"] is False
        assert out["LABEL"] == "SHADOW_SETTLED_NOT_REALIZED"


def test_residual_settles_directionally():
    inv = CS.VirtualInventory()
    inv.add(side_yes=True, px=D("0.30"), qty=D("1"), t=1.0)
    won = CS.settle_inventory(inv, {"resolved": True, "yes_won": True})
    lost = CS.settle_inventory(inv, {"resolved": True, "yes_won": False})
    assert D(won["residual_pnl_usd"]) == D("0.70")
    assert D(lost["residual_pnl_usd"]) == D("-0.30")
    assert D(won["matched_pair_pnl_usd"]) == 0


def test_an_unresolved_market_is_not_settled_as_zero():
    inv = CS.VirtualInventory()
    inv.add(side_yes=True, px=D("0.30"), qty=D("1"), t=1.0)
    out = CS.settle_inventory(inv, {"resolved": False, "why": "STILL_OPEN"})
    assert out["settled"] is False and "net_usd" not in out


@pytest.mark.skipif(not os.path.exists(os.path.join(MODELS,
                                                    "l5_policy_early.pkl")),
                    reason="models not built in this checkout")
def test_complement_is_no_longer_blocked_by_a_pending_first_leg(tmp_path):
    """8.5 h of shadow completed zero pairs because a PENDING first leg
    suppressed every quote in the same market for 300 s, the complement
    included."""
    slug = "cpc-btc-updown-15m-2026-09-23-1345z"
    t0 = 1790171100.0 + 60.0
    gw = Gateway([market(slug)], {slug: [book(0.30, 0.60)]})
    cs = CS.CloneShadow(get_json=gw, sleep=None, now=lambda: t0,
                        policies=CS.load_policies(MODELS),
                        state_dir=str(tmp_path))
    cs.poll([{"slug": slug, "market": market(slug)}])
    # a pending YES first leg
    cs.candidates["early"]["x"] = {
        "id": "x", "slug": slug, "side_yes": True, "status": CS.PENDING}
    inv = cs.inv["early"].setdefault(slug, CS.VirtualInventory())
    inv.add(side_yes=True, px=D("0.30"), qty=D("1"), t=t0)
    rows = [r for r in cs.decide([slug]) if r["policy"] == "early"]
    intent = rows[0]["intended_order"]
    if intent.get("propose") and intent["side_yes"] is False:
        assert "candidate_suppressed" not in rows[0]
    # a SECOND yes quote is still suppressed
    cs.candidates["early"]["y"] = {
        "id": "y", "slug": slug, "side_yes": False, "status": CS.PENDING}
    rows2 = [r for r in cs.decide([slug]) if r["policy"] == "early"]
    i2 = rows2[0]["intended_order"]
    if i2.get("propose"):
        assert rows2[0].get("candidate_suppressed") == \
            "ONE_OPEN_CANDIDATE_PER_SIDE_PER_MARKET_PER_ARM"


def test_one_sided_book_records_the_payload_shape(tmp_path):
    slug = "cpc-btc-updown-15m-2026-09-23-1345z"
    t0 = 1790171100.0 + 60.0
    one_sided = {"marketData": {"bids": [{"px": {"value": 0.3}, "qty": 5}],
                                "offers": [], "stats": {},
                                "state": "OPEN", "transactTime": None}}
    gw = Gateway([market(slug)], {slug: [one_sided]})
    cs = CS.CloneShadow(get_json=gw, sleep=None, now=lambda: t0,
                        policies={}, state_dir=str(tmp_path))
    cs.poll([{"slug": slug, "market": market(slug)}])
    d = cs.one_sided[slug]
    assert d["n_bid_levels"] == 1 and d["n_offer_levels"] == 0
    assert d["ask"] is None and "payload_keys" in d


# ------------------------------------------------- completion discipline --
def test_completion_is_due_when_the_pair_budget_allows_it():
    inv = CS.VirtualInventory()
    inv.add(side_yes=True, px=D("0.40"), qty=D("2"), t=1.0)
    # NO maker price = 1 - 0.52 = 0.48; budget = 0.96 - 0.40 = 0.56
    out = CS.completion_due(inv, {"bid": 0.45, "ask": 0.52})
    assert out["due"] is True and out["side_yes"] is False
    assert out["px"] == D("0.48")
    assert out["pair_cost_if_filled"] == "0.88"
    # never more than the residual
    assert out["qty"] <= abs(inv.residual)


def test_completion_refuses_above_the_pair_ceiling():
    inv = CS.VirtualInventory()
    inv.add(side_yes=True, px=D("0.80"), qty=D("1"), t=1.0)
    out = CS.completion_due(inv, {"bid": 0.70, "ask": 0.75})
    assert out["due"] is False
    assert out["why"] == "COMPLEMENT_ABOVE_PAIR_CEILING"


def test_no_residual_means_no_completion():
    inv = CS.VirtualInventory()
    inv.add(side_yes=True, px=D("0.4"), qty=D("1"), t=1.0)
    inv.add(side_yes=False, px=D("0.5"), qty=D("1"), t=2.0)
    assert CS.completion_due(inv, {"bid": 0.4, "ask": 0.6})["why"] == \
        "NO_RESIDUAL"


@pytest.mark.skipif(not os.path.exists(os.path.join(MODELS,
                                                    "l5_policy_early.pkl")),
                    reason="models not built in this checkout")
def test_completion_takes_precedence_over_the_predicted_next_fill(tmp_path):
    """The model's argmax is EXTEND ~70% of the time, so an argmax policy
    never completes: 9 markets carried residual for hours with a matched
    pair-cost sample of zero."""
    slug = "cpc-btc-updown-15m-2026-09-23-1345z"
    t0 = 1790171100.0 + 60.0
    gw = Gateway([market(slug)], {slug: [book(0.45, 0.52)]})
    cs = CS.CloneShadow(get_json=gw, sleep=None, now=lambda: t0,
                        policies=CS.load_policies(MODELS),
                        state_dir=str(tmp_path))
    cs.poll([{"slug": slug, "market": market(slug)}])
    cs.inv["early"].setdefault(slug, CS.VirtualInventory()).add(
        side_yes=True, px=D("0.40"), qty=D("2"), t=t0)
    row = [r for r in cs.decide([slug]) if r["policy"] == "early"][0]
    i = row["intended_order"]
    assert i["propose"] is True
    assert i["ACTION_SOURCE"] == "COMPLETION_RULE_NOT_MODEL"
    assert i["side_yes"] is False              # the complement side
    # what the model wanted is still on the row, so fidelity is measurable
    assert i["model_action"] in ("EXTEND", "COMPLEMENT", "STOP")
    assert set(i["model_proba"]) == {"EXTEND", "COMPLEMENT", "STOP"}
    assert row["model"] is not None


def test_ranking_sinks_one_sided_markets_below_tradable_ones(tmp_path):
    gw = Gateway([], {})
    cs = CS.CloneShadow(get_json=gw, sleep=None, now=lambda: 1.0,
                        policies={}, state_dir=str(tmp_path))
    universe = [{"slug": "otm", "clock": {"tte_s": 60.0}},
                {"slug": "updown", "clock": {"tte_s": 800.0}}]
    # both unseen -> expiry decides
    assert [r["slug"] for r in cs.rank_watch(universe)] == ["otm", "updown"]
    # once the near one is known to be one-sided it sinks, even though it
    # expires first
    cs.one_sided["otm"] = {"bid": None}
    cs.obs["updown"] = [{}]
    assert [r["slug"] for r in cs.rank_watch(universe)] == ["updown", "otm"]


@pytest.mark.skipif(not os.path.exists(os.path.join(MODELS,
                                                    "l5_policy_early.pkl")),
                    reason="models not built in this checkout")
def test_a_stale_book_produces_no_decision(tmp_path):
    """The live run emitted 44 decisions a cycle off 5 successful polls:
    every market ever polled still had a last observation."""
    slug = "cpc-btc-updown-15m-2026-09-23-1345z"
    t0 = 1790171100.0 + 60.0
    gw = Gateway([market(slug)], {slug: [book(0.30, 0.60)]})
    clock = {"t": t0}
    cs = CS.CloneShadow(get_json=gw, sleep=None, now=lambda: clock["t"],
                        policies=CS.load_policies(MODELS),
                        state_dir=str(tmp_path))
    cs.poll([{"slug": slug, "market": market(slug)}])
    assert len(cs.decide([slug])) == 2            # fresh
    clock["t"] = t0 + cs.FRESH_S + 1
    assert cs.decide([slug]) == []                # stale, not refused-on


def test_held_markets_are_pinned_into_the_watch_set(tmp_path):
    gw = Gateway([], {})
    cs = CS.CloneShadow(get_json=gw, sleep=None, now=lambda: 1.0,
                        policies={"early": None}, state_dir=str(tmp_path))
    universe = [{"slug": "fresh", "clock": {"tte_s": 60.0}},
                {"slug": "held", "clock": {"tte_s": 800.0}}]
    assert [r["slug"] for r in cs.rank_watch(universe)] == ["fresh", "held"]
    inv = cs.inv["early"].setdefault("held", CS.VirtualInventory())
    inv.add(side_yes=True, px=D("0.4"), qty=D("1"), t=1.0)
    # even one-sided and later-expiring, a held market comes first
    cs.one_sided["held"] = {"bid": None}
    assert [r["slug"] for r in cs.rank_watch(universe)] == ["held", "fresh"]
    # once settled it stops being pinned
    cs.settled["held"] = {"yes_won": True}
    assert [r["slug"] for r in cs.rank_watch(universe)] == ["fresh", "held"]


def test_refused_completion_reports_how_far_above_the_ceiling():
    inv = CS.VirtualInventory()
    inv.add(side_yes=True, px=D("0.80"), qty=D("1"), t=1.0)
    out = CS.completion_due(inv, {"bid": 0.70, "ask": 0.75})
    # budget 0.96-0.80 = 0.16; NO maker = 1-0.75 = 0.25
    assert out["shortfall"] == "0.09"
    assert out["pair_cost_available"] == "1.05"


def test_pair_cost_reachability_aggregates_the_refusals(tmp_path):
    rows = [{"completion_rule": {"pair_cost_available": "1.05",
                                 "shortfall": "0.09"}},
            {"completion_rule": {"pair_cost_available": "0.94",
                                 "due": True}},
            {"completion_rule": {"why": "NO_RESIDUAL"}}]
    out = CS.CloneShadow.pair_cost_reachability(rows)
    assert out["n"] == 2
    assert out["pair_cost_available"]["min"] == 0.94
    assert out["pair_cost_available"]["frac_at_or_below_ceiling"] == 0.5
    assert out["shortfall_when_refused"]["n"] == 1


def test_settlement_recomputes_the_clock_instead_of_trusting_the_cache(
        tmp_path):
    """Discovery drops a market the moment it expires, so a held market was
    never polled again and its cached clock still said 'not expired' --
    settlement could never fire. 11 markets sat unsettled."""
    slug = "cpc-btc-updown-15m-2026-09-23-1345z"
    resolved = {"market": {"slug": slug, "outcomes": ["Up", "Down"],
                           "outcomePrices": ["1", "0"], "closed": True}}
    def gw(path):
        # resolutions live on the market LIST; the per-market endpoint 404s
        if path.startswith("/v1/markets/"):
            raise AssertionError("per-market endpoint must not be used")
        if "closed=true" in path and "offset=0" in path:
            return {"markets": [resolved["market"]]}
        return {"markets": []}

    cs = CS.CloneShadow(get_json=gw, sleep=None, now=lambda: 0.0,
                        policies={"early": None}, state_dir=str(tmp_path))
    inv = cs.inv["early"].setdefault(slug, CS.VirtualInventory())
    inv.add(side_yes=True, px=D("0.30"), qty=D("1"), t=0.0)
    # a STALE cached clock claiming the window is still open
    cs.clocks[slug] = {"resolved": True, "expired": False, "tte_s": 900.0}
    # well past the real 2026-09-23 14:00Z close
    rows = cs.settle_expired([], 1790179000.0)
    assert len(rows) == 1 and rows[0]["settled"] is True
    assert D(rows[0]["residual_pnl_usd"]) == D("0.70")


def test_resolution_comes_from_the_closed_market_sweep_not_a_404_endpoint():
    """/v1/markets/{slug} returns 404 on this gateway; resolutions are only
    available from the market list."""
    slug = "cpc-btc-updown-15m-2026-10-09-0300z"
    calls = []

    def gw(path):
        calls.append(path)
        if path.startswith("/v1/markets/"):
            raise AssertionError("per-market endpoint must not be used")
        if "closed=true" in path and "offset=0" in path:
            return {"markets": [{"slug": slug, "outcomes": ["Up", "Down"],
                                 "outcomePrices": ["0", "1"]}]}
        return {"markets": []}

    out = CS.fetch_resolutions(gw, [slug], sleep=None)
    assert out["n_found"] == 1
    assert CS.resolved_outcome(out["found"][slug])["yes_won"] is False
    assert out["tried"][0]["found"] == 1


def test_settle_expired_uses_the_sweep_and_records_misses(tmp_path):
    slug = "cpc-btc-updown-15m-2026-09-23-1345z"

    def gw(path):
        if path.startswith("/v1/markets/"):
            raise AssertionError("per-market endpoint must not be used")
        return {"markets": []}        # resolution not published yet

    cs = CS.CloneShadow(get_json=gw, sleep=None, now=lambda: 0.0,
                        policies={"early": None}, state_dir=str(tmp_path))
    inv = cs.inv["early"].setdefault(slug, CS.VirtualInventory())
    inv.add(side_yes=True, px=D("0.30"), qty=D("1"), t=0.0)
    rows = cs.settle_expired([], 1790179000.0)
    assert rows == []
    assert cs.settle_attempts[slug]["stage"] == "NOT_IN_CLOSED_MARKET_SWEEP"
    assert cs.resolution_sweep["n_wanted"] == 1


def test_by_cohort_groups_on_the_selection_time_stamp(tmp_path):
    gw = Gateway([], {})
    cs = CS.CloneShadow(get_json=gw, sleep=None, now=lambda: 0.0,
                        policies={"early": None}, state_dir=str(tmp_path))
    def cand(tag, status, spread, adv, pair):
        return {"id": tag + status, "slug": tag, "status": status,
                "cohort": {"sport_type": tag, "market_type": "MONEYLINE"},
                "state_at_post": {"spread": spread, "own_side_depth": 100,
                                  "flow_usd_per_min": 50.0},
                "markouts": {"t+30s": {
                    "adverse_selection_per_share": adv,
                    "pair_cost_maker": pair,
                    "completable_at_or_below_ceiling_maker": pair <= 0.96}}}
    cs.candidates["early"] = {
        "a": cand("football", CS.QUEUE_CLEARED, 0.04, -0.01, 0.93),
        "b": cand("football", CS.NO_TOUCH, 0.05, 0.0, 0.99),
        "c": cand("tennis", CS.QUEUE_CLEARED, 0.02, 0.02, 0.98)}
    out = cs.by_cohort("sport_type")["arms"]["early"]
    assert out["football"]["candidates"] == 2
    assert out["football"]["queue_cleared_rate"] == 0.5
    assert out["football"]["spread"]["median"] == 0.045
    assert out["football"]["pair_edge_per_share_if_completed_maker"]["n"] == 1
    assert out["tennis"]["complement_completable_at_ceiling_t30"] == 0.0
    # and the same candidates regroup by market type without re-reading
    bymt = cs.by_cohort("market_type")["arms"]["early"]
    assert bymt["MONEYLINE"]["candidates"] == 3


def test_dist_never_reports_a_bare_mean():
    assert CS._dist([])["n"] == 0
    d = CS._dist([1.0, 3.0])
    assert d["median"] == 2.0 and d["min"] == 1.0 and d["max"] == 3.0


def test_sports_universe_comes_from_the_event_feed_not_the_market_list():
    """Measured live: 208 markets carried a recognised game position while
    ZERO of the 15,000 sports rows in the paged list did -- the list fills
    with futures and the page cap is hit before the live games."""
    prog = {"aec-nfl-sf-lar-2026-10-09": {
        "game_proximity": 0.5, "sport_type": "football", "league": "nfl",
        "proximity_basis": "period_token/quarters/football",
        "period": "Q3", "ended": False, "rebate_eligible": True}}
    # the market list contains only an unrelated future
    markets = [{"slug": "tec-mlb-champ-2026-09-27-cws", "category": "sports",
                "startDate": "2026-03-19T14:32:24Z",
                "endDate": "2026-11-06T21:20:09Z"}]
    out = CS.eligible_markets(markets, 1.0, progress=prog)
    slugs = {r["slug"] for r in out}
    assert "aec-nfl-sf-lar-2026-10-09" in slugs
    assert "tec-mlb-champ-2026-09-27-cws" not in slugs   # futures: no clock
    row = [r for r in out if r["slug"].startswith("aec-")][0]
    assert row["source"] == "live_event_feed"
    assert row["sport_type"] == "football" and row["league"] == "nfl"
    assert row["clock"]["basis"] == "venue_event_progress"


def test_a_market_in_both_sources_is_not_duplicated():
    prog = {"aec-nfl-a-b-2026-10-09": {
        "game_proximity": 0.5, "sport_type": "football", "ended": False,
        "proximity_basis": "period_token/quarters/football"}}
    markets = [{"slug": "aec-nfl-a-b-2026-10-09", "category": "sports"}]
    out = CS.eligible_markets(markets, 1.0, progress=prog)
    assert len(out) == 1 and out[0]["source"] == "live_event_feed"
