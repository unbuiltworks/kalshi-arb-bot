"""GEMINI_COMPLETION_MODEL_B: recycling, not fill count.

An arm may change a COMPLETE price, a quote's persistence, whether an EXTEND
is acted on, or whether a COMPLETE-only quote holds late. It may not change
what the frozen policy decides, may never cross, and nothing here may be
labelled realized.
"""
from decimal import Decimal as D

import pytest

from src.gemini_execution import completion as CM


# ================================================== the matrix is the matrix

def test_the_matrix_is_four_prices_by_five_persistences():
    arms = CM.matrix_arms()
    assert len(arms) == 20
    assert {a.price_policy for a in arms} == set(CM.PRICE_POLICIES)
    assert {a.sticky_s for a in arms} == set(CM.STICKY_S)
    assert CM.Arm().name == "NATIVE+REPRICE"
    assert CM.Arm(price_policy=CM.BEST_MAKER, sticky_s=30).name == \
        "BEST_PERMISSIBLE_MAKER+STICKY_30"


def test_an_arms_name_carries_every_thing_that_varies():
    a = CM.Arm(price_policy=CM.IMPROVE_2, sticky_s=15,
               preserve_prior_complement_s=45, extend_veto=True,
               late_complete_phase=True)
    assert a.name == ("IMPROVE_2+STICKY_15+PRESERVE_45+EXTEND_VETO"
                      "+LATE_60_30")


# ============================================================ NEVER A CROSS

def test_NO_PRICE_POLICY_MAY_EVER_REACH_THE_ASK():
    """The one property that must hold in every cell of the matrix."""
    book = {"yes": {"bid": "0.40", "ask": "0.43"}}
    for pol in CM.PRICE_POLICIES:
        r = CM.maker_price(book, "YES", "0.40", pol)
        assert D(r["price"]) < D("0.43"), (pol, r)


def test_improve_two_is_refused_when_only_one_tick_fits():
    book = {"yes": {"bid": "0.40", "ask": "0.42"}}
    one = CM.maker_price(book, "YES", "0.40", CM.IMPROVE_1)
    two = CM.maker_price(book, "YES", "0.40", CM.IMPROVE_2)
    assert one == {"price": "0.41", "applied": True, "policy": CM.IMPROVE_1,
                   "ask": "0.42", "improved_by": "0.01"}
    assert two["applied"] is False
    assert two["price"] == "0.40"
    assert "crossing is not authorized" in two["why"]


def test_best_permissible_maker_is_one_tick_under_the_ask():
    book = {"yes": {"bid": "0.30", "ask": "0.70"}}
    r = CM.maker_price(book, "YES", "0.30", CM.BEST_MAKER)
    assert r["price"] == "0.69" and r["applied"] is True


def test_a_policy_that_would_not_improve_leaves_the_native_price():
    book = {"yes": {"bid": "0.40", "ask": "0.41"}}
    r = CM.maker_price(book, "YES", "0.40", CM.BEST_MAKER)
    assert r["price"] == "0.40" and r["applied"] is False
    assert "no better than the native" in r["why"]


def test_without_an_ask_maker_only_cannot_be_proven_so_native_stands():
    r = CM.maker_price({"yes": {"bid": "0.40"}}, "YES", "0.40", CM.IMPROVE_1)
    assert r["price"] == "0.40" and r["applied"] is False
    assert "cannot be proven" in r["why"]


def test_native_needs_no_ask_and_is_always_applied():
    r = CM.maker_price({}, "YES", "0.40", CM.NATIVE)
    assert r == {"price": "0.40", "applied": True, "policy": CM.NATIVE}


# ======================================================= the side convention

def test_extend_and_complete_are_reached_by_opposite_aggressors():
    for held in ("YES", "NO"):
        assert (CM.reaching_aggressor(held)
                != CM.reaching_aggressor(CM.complement_leg(held)))


def test_a_NO_rung_is_reached_by_a_buyer_at_or_above_its_mirror():
    p = {"type": "buy", "price": "0.54", "amount": "3"}
    assert CM.reaches("NO", "0.46", p) is True      # needs >= 0.54
    assert CM.reaches("NO", "0.45", p) is False     # needs >= 0.55
    assert CM.reaches("NO", "0.46", dict(p, type="sell")) is False


# ============================================================ the dollar view

class _Inv:
    """Just enough of the frozen Inventory to exercise the dollar view."""
    def __init__(self, yq, yc, nq, nc):
        self._y, self._yc, self._n, self._nc = (D(yq), D(yc), D(nq), D(nc))

    def matched(self):
        return min(self._y, self._n)

    def residual(self):
        return self._y - self._n

    def avg_yes(self):
        return self._yc / self._y if self._y > 0 else None

    def avg_no(self):
        return self._nc / self._n if self._n > 0 else None

    def effective_matched_pair_cost(self):
        a, b = self.avg_yes(), self.avg_no()
        return None if a is None or b is None else a + b

    def spend(self):
        return self._yc + self._nc


def test_matched_and_residual_dollars_are_separate_and_never_summed():
    inv = _Inv("0.02", "0.008", "0.01", "0.005")     # avg .40 / .50
    d = CM.dollars(inv)
    assert d["matched_units"] == "0.01"
    assert D(d["matched_usd"]) == D("0.01") * D("0.9")
    assert D(d["residual_usd"]) == D("0.01") * D("0.40")
    assert D(d["gross_filled_usd"]) == D("0.013")
    assert "NEVER summed" in d["labels"]


def test_a_flat_book_has_no_matched_and_no_residual_dollars():
    d = CM.dollars(_Inv("0", "0", "0", "0"))
    assert d["matched_usd"] == "0" and d["residual_usd"] == "0"
    assert d["matched_dollar_share"] is None


def test_MATCHED_OVER_GROSS_IS_GAMEABLE_AND_THE_RANK_KNOWS_IT():
    """An arm raises matched/gross simply by OVERPAYING for the same pair.
    The ranking must not reward that, so it ranks the par-constrained ratio
    and shows the raw one beside it."""
    cheap = {"arm": "CHEAP", "matched_over_gross_usd": "0.10",
             "matched_over_gross_usd_at_or_below_par": "0.10",
             "residual_over_gross_usd": "0.90"}
    dear = {"arm": "DEAR", "matched_over_gross_usd": "0.16",
            "matched_over_gross_usd_at_or_below_par": "0.00",
            "residual_over_gross_usd": "0.84"}
    assert [r["arm"] for r in CM.rank([dear, cheap])] == ["CHEAP", "DEAR"]


def test_a_rank_tie_goes_to_the_lower_residual():
    a = {"arm": "A", "matched_over_gross_usd_at_or_below_par": "0.10",
         "residual_over_gross_usd": "0.90"}
    b = {"arm": "B", "matched_over_gross_usd_at_or_below_par": "0.10",
         "residual_over_gross_usd": "0.50"}
    assert [r["arm"] for r in CM.rank([a, b])] == ["B", "A"]


# ================================================= the veto's own comparison

def test_EACH_VETO_ARM_IS_COMPARED_WITH_ITS_OWN_TWIN():
    """Comparing every veto arm against one shared baseline would credit the
    veto with whatever the price policy did."""
    alls = {
        "NATIVE+REPRICE": {"arm": "NATIVE+REPRICE", "matched_usd": "0",
                           "residual_usd": "0.13"},
        "IMPROVE_1+REPRICE": {"arm": "IMPROVE_1+REPRICE",
                              "matched_usd": "0.008",
                              "residual_usd": "0.129"},
    }
    veto = {"IMPROVE_1+REPRICE+EXTEND_VETO": {
        "matched_usd": "0.008", "residual_usd": "0.096",
        "extend_fills_vetoed": 7}}
    p = CM.pareto(veto, alls)
    row = p["points"][0]
    assert row["twin"] == "IMPROVE_1+REPRICE"
    assert row["matched_retained_share"] == "1.0000"
    assert D(row["residual_avoided_usd"]) == D("0.033")
    assert row["dominates_twin"] is True


def test_a_veto_that_destroys_matched_inventory_does_not_dominate():
    alls = {"X": {"arm": "X", "matched_usd": "0.02", "residual_usd": "0.10"}}
    veto = {"X+EXTEND_VETO": {"matched_usd": "0.00",
                              "residual_usd": "0.05"}}
    row = CM.pareto(veto, alls)["points"][0]
    assert row["dominates_twin"] is False
    assert D(row["matched_usd_given_up"]) == D("0.02")


def test_a_veto_arm_with_no_twin_says_so_rather_than_inventing_one():
    row = CM.pareto({"Q+EXTEND_VETO": {}}, {})["points"][0]
    assert row["comparable"] is False


# ================================================== the aged-queue model

def test_a_preserved_quote_has_its_queue_eaten_a_requote_does_not():
    q = CM._Quote(leg="YES", price=D("0.4"), placed_ms=D("0"),
                  ahead_at_place=D("100"), preserved=True)
    assert q.ahead_now() == D("100")
    q.consumed += D("40")
    assert q.ahead_now() == D("60")
    q.consumed += D("500")
    assert q.ahead_now() == D("0")          # never negative


def test_an_unreadable_queue_stays_unknown_and_is_not_called_zero():
    q = CM._Quote(leg="NO", price=D("0.4"), placed_ms=D("0"),
                  ahead_at_place=None)
    assert q.ahead_now() is None
    assert CM._size_ahead({"readable": True, "bids": {"levels": []}},
                          "NO", "0.4") is None


def test_size_ahead_counts_every_rung_at_or_better_than_ours():
    depth = {"readable": True, "bids": {"levels": [
        {"price": "0.45", "qty": "10"}, {"price": "0.40", "qty": "7"},
        {"price": "0.35", "qty": "99"}]}}
    assert CM._size_ahead(depth, "YES", "0.40") == D("17")


def test_a_fill_only_happens_once_the_queue_is_cleared():
    hits = [{"amount": "50"}]
    assert CM._fill_from(hits, D("100"), D("0.01")) == D("0")
    assert CM._fill_from(hits, D("49.995"), D("0.01")) == D("0.005")
    assert CM._fill_from(hits, D("0"), D("0.01")) == D("0.01")
    assert CM._fill_from([], None, D("0.01")) == D("0")


# ============================================================ the replay

def _cycle(ms, tte, ybid="0.40", yask="0.50", nbid="0.45", nask="0.55"):
    return {"utc_ms": ms, "utc": "u", "tte_s": tte,
            "book": {"readable": True, "identity_consistent": True,
                     "tick": "0.01", "t_utc": ms / 1000.0,
                     "yes": {"bid": ybid, "ask": yask},
                     "no": {"bid": nbid, "ask": nask}},
            "depth": {"readable": False}}


@pytest.fixture()
def _terms(monkeypatch):
    from src.gemini_execution import metadata as MD
    monkeypatch.setattr(MD, "terms_for", lambda s: {"priceIncrement": "0.01"})


def _cycles(n=8, start=1000, step=8000, tte0=200):
    return [_cycle(start + i * step, tte0 - i * 8) for i in range(n)]


def test_EVERY_ARM_TAKES_THE_SAME_ACTIONS_IN_THE_SAME_ORDER(_terms):
    """Model A is frozen. An arm moves prices and persistence, never the
    decision."""
    cyc = _cycles()
    shapes = set()
    for arm in list(CM.matrix_arms())[:8] + [
            CM.Arm(preserve_prior_complement_s=30),
            CM.Arm(late_complete_phase=True)]:
        t = CM.trace(symbol="S", arm=arm, seed_leg="YES", seed_qty="0.01",
                     seed_price="0.40", seed_ms=0, cycles=cyc, prints=[],
                     resolution={}, expiry_ms=10 ** 9)
        shapes.add(str([[(a["kind"], a["leg"]) for a in s["actions"]]
                        for s in t["steps"]]))
    assert len(shapes) == 1


def test_THE_EXTEND_VETO_RECORDS_WHAT_MODEL_A_WANTED(_terms):
    """The frozen policy still asks. Model B declines to act, and what it
    declined is on the record."""
    cyc = _cycles()
    prints = [{"utc_ms": 1500, "type": "sell", "price": "0.10",
               "amount": "900"}]
    plain = CM.trace(symbol="S", arm=CM.Arm(), seed_leg="YES",
                     seed_qty="0.01", seed_price="0.40", seed_ms=0,
                     cycles=cyc, prints=prints, resolution={},
                     expiry_ms=10 ** 9)
    veto = CM.trace(symbol="S", arm=CM.Arm(extend_veto=True), seed_leg="YES",
                    seed_qty="0.01", seed_price="0.40", seed_ms=0,
                    cycles=cyc, prints=prints, resolution={},
                    expiry_ms=10 ** 9)
    assert plain["extend_theoretical_fills"] >= 1
    assert veto["extend_theoretical_fills"] == 0
    assert veto["extend_fills_vetoed"] >= 1
    assert D(veto["final"]["gross_filled_usd"]) < \
        D(plain["final"]["gross_filled_usd"])


def test_the_prior_resting_complement_is_found_from_the_cycle_before(_terms):
    """On flat inventory the frozen policy quotes BOTH legs, so there is
    already a complement live when the first leg fills. That is the whole
    premise of the preserve experiment."""
    cyc = _cycles()
    p = CM.prior_resting_complement("S", cyc, 9000, "YES")
    assert p["existed"] is True
    assert p["leg"] == "NO"
    assert p["price"] is not None


def test_with_no_earlier_cycle_the_prior_complement_is_absent_not_guessed():
    p = CM.prior_resting_complement("S", [], 1000, "YES")
    assert p["existed"] is False
    assert "no cycle recorded before the fill" in p["why"]


def test_a_residual_mark_no_cycle_reached_is_marked_unobserved(_terms):
    t = CM.trace(symbol="S", arm=CM.Arm(), seed_leg="YES", seed_qty="0.01",
                 seed_price="0.40", seed_ms=0, cycles=[_cycle(1000, 240)],
                 prints=[], resolution={}, expiry_ms=10 ** 9)
    for k in ("tte_60", "tte_30", "tte_0"):
        assert t["residual_at"][k]["observed"] is False
        assert "no cycle reached this mark" in t["residual_at"][k]["note"]


def test_an_unreadable_seed_refuses_rather_than_guessing(_terms):
    t = CM.trace(symbol="S", arm=CM.Arm(), seed_leg="YES", seed_qty=None,
                 seed_price="0.4", seed_ms=0, cycles=[], prints=[],
                 resolution={}, expiry_ms=1000)
    assert t["computable"] is False


# ========================================================== the late phase

def test_the_late_phase_is_skipped_when_there_is_nothing_to_recycle(_terms):
    t = CM.trace(symbol="S", arm=CM.Arm(late_complete_phase=True),
                 seed_leg="YES", seed_qty="0.01", seed_price="0.40",
                 seed_ms=0, cycles=[], prints=[], resolution={},
                 expiry_ms=None)
    assert t["late_phase"]["ran"] is False


def test_the_late_phase_only_looks_between_TTE_60_AND_30(_terms):
    """A print at TTE 100 or at TTE 10 is outside the phase and must not be
    counted by it."""
    exp = 10 ** 9
    cyc = [_cycle(exp - 300000, 300)]
    prints = [
        {"utc_ms": exp - 100000, "type": "buy", "price": "0.90",
         "amount": "500"},                                   # TTE 100: out
        {"utc_ms": exp - 45000, "type": "buy", "price": "0.90",
         "amount": "500"},                                   # TTE 45: IN
        {"utc_ms": exp - 10000, "type": "buy", "price": "0.90",
         "amount": "500"},                                   # TTE 10: out
    ]
    t = CM.trace(symbol="S", arm=CM.Arm(late_complete_phase=True),
                 seed_leg="YES", seed_qty="0.01", seed_price="0.40",
                 seed_ms=exp - 400000, cycles=cyc, prints=prints,
                 resolution={}, expiry_ms=exp)
    lp = t["late_phase"]
    assert lp["ran"] is True
    assert lp["prints_in_band"] == 1
    assert "UPPER BOUND" in lp["queue_caveat"]


def test_the_late_phase_reports_how_stale_its_price_is(_terms):
    exp = 10 ** 9
    t = CM.trace(symbol="S", arm=CM.Arm(late_complete_phase=True),
                 seed_leg="YES", seed_qty="0.01", seed_price="0.40",
                 seed_ms=exp - 400000, cycles=[_cycle(exp - 300000, 300)],
                 prints=[], resolution={}, expiry_ms=exp)
    assert t["late_phase"]["price_staleness_s_at_tte_60"] == 240.0


def test_the_late_phase_is_not_a_new_bound():
    """The frozen policy is ALREADY COMPLETE_ONLY below 60 and WITHDRAWN
    below 30. This phase adds observation, not permission."""
    from src.gemini_execution import continuous_probe as CP
    assert CM.LATE_PHASE_HI_S == CP.NO_NEW_OPEN_EXTEND_TTE_S
    assert CM.LATE_PHASE_LO_S == CP.WITHDRAW_ALL_TTE_S


# ============================================================= hazard rows

def test_a_hazard_row_carries_every_feature_asked_for():
    inv = _Inv("0.02", "0.008", "0.01", "0.005")
    r = CM.hazard_row(
        symbol="S", now_ms=100000, tte_s=120.0, inv=inv, desired_leg="NO",
        desired_price="0.45",
        book={"yes": {"bid": "0.40", "ask": "0.50"},
              "no": {"bid": "0.45", "ask": "0.55"}},
        depth={"readable": False},
        prints=[{"utc_ms": 95000, "type": "buy", "price": "0.6",
                 "amount": "10"}],
        rest_s=12.0)
    for k in ("tte_s", "held_leg", "held_qty", "held_avg_price",
              "desired_complete_price", "complete_distance_from_touch",
              "queue_ahead", "spread", "price_velocity_per_min",
              "seconds_since_opposite_flow", "rest_duration_s",
              "opposite_prints_15s", "opposite_prints_120s",
              "aggressor_imbalance_60s", "eventual_complement_reachable"):
        assert k in r, k
    assert r["held_leg"] == "YES"
    assert r["at_touch"] is True
    assert r["spread"] == "0.10"
    assert r["queue_readable"] is False


def test_a_hazard_rows_outcome_columns_start_unknown():
    r = CM.hazard_row(symbol="S", now_ms=1, tte_s=1, inv=_Inv("1", "1", "0",
                                                              "0"),
                      desired_leg="NO", desired_price="0.4", book={},
                      depth=None, prints=[], rest_s=None)
    assert r["eventual_complement_reachable"] is None
    assert r["seconds_until_fill"] is None


# ========================================================== accounting hygiene

def test_nothing_in_this_module_claims_a_realized_number():
    import ast
    import inspect
    banned = ("realized", "realised", "profit", "pnl")
    denials = ("never", "not ", "nothing was", "no order existed", "cannot",
               "is a matched loss")
    for node in ast.walk(ast.parse(inspect.getsource(CM))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            low = node.value.lower()
            if any(d in low for d in denials):
                continue
            for w in banned:
                assert w not in low, node.value


def test_the_late_phase_never_reports_a_NEGATIVE_staleness(_terms):
    """A cycle recorded INSIDE the band would price the quote on information
    a quote placed at TTE 60 could not have had. Caught when the first run
    printed a staleness of -25.1s."""
    exp = 10 ** 9
    cyc = [_cycle(exp - 45000, 45)]          # the only book is INSIDE the band
    t = CM.trace(symbol="S", arm=CM.Arm(late_complete_phase=True),
                 seed_leg="YES", seed_qty="0.01", seed_price="0.40",
                 seed_ms=exp - 400000, cycles=cyc, prints=[], resolution={},
                 expiry_ms=exp)
    lp = t["late_phase"]
    assert lp["price_staleness_s_at_tte_60"] >= 0.0
    assert lp["priced_from_inside_the_band"] is True
    assert "optimistic assumption" in lp["pricing_caveat"]


def test_a_book_before_the_band_is_preferred_over_one_inside_it(_terms):
    exp = 10 ** 9
    cyc = [_cycle(exp - 120000, 120), _cycle(exp - 45000, 45)]
    lp = CM.trace(symbol="S", arm=CM.Arm(late_complete_phase=True),
                  seed_leg="YES", seed_qty="0.01", seed_price="0.40",
                  seed_ms=exp - 400000, cycles=cyc, prints=[], resolution={},
                  expiry_ms=exp)["late_phase"]
    assert lp["priced_from_inside_the_band"] is False
    assert lp["priced_from_tte_s"] == 120
    assert lp["price_staleness_s_at_tte_60"] == 60.0
