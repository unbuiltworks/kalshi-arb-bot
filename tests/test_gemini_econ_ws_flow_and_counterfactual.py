"""WS flow provenance, the spread census, and both paths at every veto."""
from decimal import Decimal as D

from src.gemini_execution.econ import counterfactual as CF
from src.gemini_execution.econ import spread_census as SC
from src.gemini_execution.econ import ws_flow as WF


# ------------------------------------------------------------- ws provenance

def test_the_side_translation_is_the_aggressor_not_the_resting_side():
    """Gemini names the RESTING side that was hit. Reading it as the aggressor
    inverts every alternation and imbalance feature downstream."""
    assert WF.aggressor_side({"resting_side": "bid"}) == "sell"
    assert WF.aggressor_side({"resting_side": "ask"}) == "buy"


def test_an_unknown_side_is_none_and_never_defaulted():
    """A defaulted direction silently biases alternation; an excluded one is
    merely honest."""
    assert WF.aggressor_side({"resting_side": ""}) is None
    assert WF.aggressor_side({}) is None
    rows = WF.normalize([{"resting_side": "?", "size": "1", "price": "0.5"}])
    assert rows[0]["side"] is None


class _Book:
    def __init__(self, trades=None, vouch=True, ready_since=0.0):
        self._t = trades or []
        self._vouch = vouch
        self.ready = True
        self.ready_since = ready_since

    def prints_since(self, t0):
        return (list(self._t) if self._vouch else None)


class _Feed:
    def __init__(self, book=None):
        self._books = {"S": book} if book else {}
        self.watched = []

    def watch(self, s):
        self.watched.append(s)
        self._books.setdefault(s, _Book())

    def drop(self, s):
        self._books.pop(s, None)


def test_unvouched_coverage_returns_no_prints_and_a_reason():
    src = WF.WsFlowSource(feed=_Feed(_Book(vouch=False)))
    got = src.flow("S", window_s=120, now=1000.0)
    assert got["vouched"] is False
    assert got["prints"] is None
    assert "were not watching" in got["why"]


def test_unvouched_never_falls_back_to_the_polled_tape():
    """The module exists because the polled tape may not be decided on.
    A fallback would reintroduce it invisibly."""
    src = WF.WsFlowSource(feed=_Feed(_Book(vouch=False)))
    got = src.flow("S", window_s=120, now=1000.0)
    assert got["prints"] is None          # not [], and not a polled list
    assert "public_flow" not in str(got)
    assert any("never falls back" in x for x in got["labels"])


def test_a_symbol_with_no_socket_is_unvouched_rather_than_empty():
    src = WF.WsFlowSource(feed=_Feed())
    got = src.flow("NOPE", window_s=120, now=1.0)
    assert got["vouched"] is False and got["prints"] is None


def test_vouched_flow_normalizes_sides_and_counts_directionals():
    b = _Book([{"resting_side": "bid", "size": "2", "price": "0.6", "t": 1},
               {"resting_side": "ask", "size": "1", "price": "0.61", "t": 2},
               {"resting_side": "", "size": "1", "price": "0.6", "t": 3}])
    src = WF.WsFlowSource(feed=_Feed(b))
    got = src.flow("S", window_s=120, now=200.0)
    assert got["vouched"] is True
    assert [r["side"] for r in got["prints"]] == ["sell", "buy", None]
    assert got["n_prints"] == 3 and got["n_directional"] == 2


# --------------------------------------------------------------- the census

def test_the_census_conditions_rather_than_pools():
    a = SC.observe(cohort="BTC5m", tte_s=250, prints_per_min=0.0,
                   book={"yes": {"bid": "0.60", "ask": "0.90"}}, t=1.0)
    b = SC.observe(cohort="BTC5m", tte_s=40, prints_per_min=20.0,
                   book={"yes": {"bid": "0.05", "ask": "0.07"}}, t=1.0)
    assert SC.cell_of(a) != SC.cell_of(b)
    assert a["spread"] == "0.30" and b["spread"] == "0.02"


def test_the_census_gates_nothing():
    """`max_spread` is discussed in its docstring -- that is the whole reason
    the module exists -- so assert on the parsed module, not on the prose."""
    import ast
    tree = ast.parse(open(SC.__file__).read())
    fns = {n.name for n in ast.walk(tree)
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert not {f for f in fns
                if any(w in f.lower()
                       for w in ("admit", "gate", "refuse", "allow"))}
    # and it exposes no threshold constant to be gated on
    consts = {t.id for n in ast.walk(tree)
              if isinstance(n, ast.Assign)
              for t in n.targets if isinstance(t, ast.Name)}
    assert "MAX_SPREAD" not in consts and "SPREAD_LIMIT" not in consts


def test_a_cell_below_the_floor_is_marked_insufficient():
    c = SC.SpreadCensus(min_cell=5)
    for i in range(4):
        c.record(SC.observe(cohort="BTC5m", tte_s=250, prints_per_min=0.0,
                            book={"yes": {"bid": "0.60", "ask": "0.65"}},
                            t=float(i)))
    assert c.report()["cells_sufficient"] == 0
    c.record(SC.observe(cohort="BTC5m", tte_s=250, prints_per_min=0.0,
                        book={"yes": {"bid": "0.60", "ask": "0.65"}}, t=5.0))
    assert c.report()["cells_sufficient"] == 1


def test_a_markout_is_never_scored_before_its_horizon_elapses():
    c = SC.SpreadCensus()
    c.record(SC.observe(cohort="BTC5m", tte_s=250, prints_per_min=0.0,
                        book={"yes": {"bid": "0.60", "ask": "0.65"}}, t=0.0))
    c.advance(now=1.0, bid="0.70")          # too soon for any horizon
    cell = list(c.report()["cells"].values())[0]
    assert cell["signed_markout_ticks_by_horizon_s"] == {}
    c.advance(now=40.0, bid="0.64")
    cell = list(c.report()["cells"].values())[0]
    # the bid ROSE 4 ticks -- favourable to a maker long the YES leg
    assert cell["signed_markout_ticks_by_horizon_s"]["8"] == 4.0
    assert cell["abs_touch_move_ticks_by_horizon_s"]["30"] == 4.0
    assert cell["adverse_share_by_horizon_s"]["8"] == 0.0


def test_the_census_survives_a_restart(tmp_path):
    c = SC.SpreadCensus()
    c.record(SC.observe(cohort="BTC5m", tte_s=250, prints_per_min=1.0,
                        book={"yes": {"bid": "0.60", "ask": "0.65"}}, t=0.0))
    p = str(tmp_path / "sp.json")
    c.save(p)
    assert SC.SpreadCensus.load(p).observations == 1


# ------------------------------------------------------- both paths, scored

def _fork(**over):
    kw = dict(symbol="S", cohort="BTC5m", t=0.0, tte_s=200,
              held_leg="YES", complement_leg="NO", held_qty="0.01",
              held_avg_cost="0.63", complement_price="0.50", ceiling="0.35")
    kw.update(over)
    return CF.CounterfactualLedger(), kw


def test_completing_at_113_is_better_when_the_held_leg_then_falls():
    """THE WHOLE POINT. A 1.13 pair is locally negative, and completing it can
    still be the better decision because it retires a residual that goes on to
    lose more than the pair cost."""
    L, kw = _fork()
    f = L.open(**kw)
    r = f.resolve(book={"yes": {"bid": "0.40", "ask": "0.45"}}, t=60.0)
    assert float(r["allow_minus_veto"]) > 0
    assert L.report()["windows_where_ALLOW_would_have_been_better"] == 1


def test_vetoing_is_better_when_settlement_pays_the_held_leg():
    L, kw = _fork()
    f = L.open(**kw)
    r = f.resolve(book={}, t=60.0, settlement="YES")
    assert float(r["allow_minus_veto"]) < 0
    assert r["resolution_basis"] == "SETTLED"
    assert r["stage"] == "settled"


def test_a_residual_is_marked_at_the_bid_not_the_mid():
    L, kw = _fork()
    f = L.open(**kw)
    m = f.mark(book={"yes": {"bid": "0.50", "ask": "0.70"}}, t=1.0)
    assert m["mark_bid"] == "0.50"
    # 0.01 * (0.50 - 0.63)
    assert D(m[CF.VETO]["residual_mark"]) == D("0.01") * (D("0.50") - D("0.63"))


def test_the_retired_residual_is_reported_explicitly():
    """The brief asks for this term by name: did completing reduce a worse
    directional residual?"""
    L, kw = _fork()
    f = L.open(**kw)
    m = f.mark(book={"yes": {"bid": "0.60", "ask": "0.65"}}, t=1.0)
    assert m["residual_retired_by_completing"] == "0.01"
    assert m[CF.ALLOW]["residual_qty"] == "0"
    assert m[CF.VETO]["residual_qty"] == "0.01"


def test_the_allow_path_recycling_is_null_rather_than_estimated():
    """Forking the inventory is exact; forking the TRAJECTORY is not, and an
    estimate here would be invention."""
    L, kw = _fork()
    f = L.open(**kw)
    m = f.mark(book={"yes": {"bid": "0.60", "ask": "0.65"}}, t=1.0)
    assert m["allow_path_recycling"] is None
    assert "NOT OBSERVABLE" in m["allow_path_recycling_why"]
    assert "untouched control" in m["allow_path_recycling_why"]


def test_subsequent_fills_on_the_veto_path_are_observed():
    L, kw = _fork()
    L.open(**kw)
    L.note_fill()
    L.note_fill()
    assert L.forks[0].veto_path_fills_after == 2


def test_an_unreadable_book_marks_veto_as_unknown_not_zero():
    L, kw = _fork()
    f = L.open(**kw)
    m = f.mark(book={}, t=1.0)
    assert m[CF.VETO]["total_inventory_economics"] is None
    assert m["allow_minus_veto"] is None


def test_the_ledger_counts_which_side_won_and_labels_what_that_means():
    L, kw = _fork()
    L.open(**kw).resolve(book={"yes": {"bid": "0.40", "ask": "0.45"}}, t=1.0)
    L.open(**kw).resolve(book={}, t=1.0, settlement="YES")
    rep = L.report()
    assert rep["forks_resolved"] == 2
    assert rep["windows_where_ALLOW_would_have_been_better"] == 1
    assert rep["windows_where_VETO_was_better"] == 1
    assert any("expose the overlay" in x for x in rep["labels"])


def test_nothing_in_the_counterfactual_is_labelled_realized():
    L, kw = _fork()
    f = L.open(**kw)
    m = f.mark(book={"yes": {"bid": "0.60", "ask": "0.65"}}, t=1.0)
    assert m["stage"] == "shadow"
    assert any("nothing here is submitted, filled, settled, credited or "
               "realized" in x for x in m["labels"])
    assert all("REALIZED" not in str(v) for v in m.values()
               if isinstance(v, str))


# ------------------------------------------- the three layers, kept apart

def _layered():
    L = CF.CounterfactualLedger()
    f = L.open(symbol="S", cohort="BTC5m", t=0.0, tte_s=200,
               held_leg="YES", complement_leg="NO", held_qty="0.01",
               held_avg_cost="0.63", complement_price="0.50", ceiling="0.35",
               book={"yes": {"bid": "0.62", "ask": "0.66"}},
               features={"tte_s": 200, "spread": "0.04"})
    return L, f


def test_layer_1_is_valued_on_the_book_that_existed_at_the_decision():
    L, f = _layered()
    assert f.immediate["kind"] == "IMMEDIATE_INVENTORY_VALUE"
    assert f.immediate["mark_bid"] == "0.62"     # the fork-instant bid
    assert f.immediate["stage"] == "shadow"


def test_layer_2_scores_each_horizon_exactly_once():
    L, f = _layered()
    f.advance(book={"yes": {"bid": "0.40", "ask": "0.45"}}, t=65.0)
    assert sorted(f.mtm) == ["30", "60"]
    before = dict(f.mtm["30"])
    f.advance(book={"yes": {"bid": "0.10", "ask": "0.15"}}, t=66.0)
    assert f.mtm["30"] == before                 # not re-scored


def test_a_bid_marked_residual_is_mtm_and_never_realized():
    """The owner's rule, pinned: bid-marked residual is counterfactual_mtm."""
    L, f = _layered()
    f.advance(book={"yes": {"bid": "0.40", "ask": "0.45"}}, t=65.0)
    row = f.mtm["60"]
    assert row["layer"] == CF.MTM
    assert row["kind"] == "COUNTERFACTUAL_MTM"
    assert row["stage"] == "shadow"
    assert "Never realized P&L" in row["note"]


def test_layer_3_is_the_only_one_that_can_be_settled_and_even_then_not_realized():
    L, f = _layered()
    r = f.resolve(book={}, t=300.0, settlement="YES")
    assert r["layer"] == CF.SETTLEMENT
    assert r["kind"] == "SETTLEMENT_VALUE"
    assert r["stage"] == "settled"
    assert r["realized"] is None
    assert "venue-confirmed cash" in r["realized_note"]


def test_an_unsettled_resolution_stays_mtm_not_settlement():
    L, f = _layered()
    r = f.resolve(book={"yes": {"bid": "0.55", "ask": "0.60"}}, t=300.0)
    assert r["layer"] == CF.MTM
    assert r["resolution_basis"] == "MARKED_AT_BID"
    assert r["stage"] == "shadow"


def test_the_three_layers_travel_together_on_resolution():
    L, f = _layered()
    f.advance(book={"yes": {"bid": "0.40", "ask": "0.45"}}, t=65.0)
    r = f.resolve(book={}, t=300.0, settlement="YES")
    assert r["immediate_inventory_value"]["kind"] == "IMMEDIATE_INVENTORY_VALUE"
    assert "60" in r["counterfactual_mtm_by_horizon_s"]
    assert r["features_at_fork"]["spread"] == "0.04"


def test_the_layers_can_disagree_and_that_is_the_point():
    """Layer 1 said the overlay helped; layer 2 said it cost us. Collapsing
    them into one number would hide exactly the question being asked."""
    L, f = _layered()
    f.advance(book={"yes": {"bid": "0.40", "ask": "0.45"}}, t=65.0)
    l1 = float(f.immediate["allow_minus_veto"])
    l2 = float(f.mtm["60"]["allow_minus_veto"])
    assert l1 < 0 < l2


def test_the_sign_convention_is_stated_on_every_layer():
    L, f = _layered()
    f.advance(book={"yes": {"bid": "0.40", "ask": "0.45"}}, t=65.0)
    r = f.resolve(book={}, t=300.0, settlement="YES")
    for row in (f.immediate, f.mtm["60"], r):
        assert "risk overlay COST us" in row["sign_note"]


def test_every_candidate_feature_is_captured_at_the_fork():
    L, f = _layered()
    assert set(f.features) == set(CF.FORK_FEATURES)
    assert L.report()["features_captured"] == list(CF.FORK_FEATURES)


def test_the_ledger_reports_allow_minus_veto_per_horizon():
    L, f = _layered()
    f.advance(book={"yes": {"bid": "0.40", "ask": "0.45"}}, t=130.0)
    rep = L.report()
    assert rep["mtm_horizons_s"] == list(CF.MTM_HORIZONS_S)
    assert rep["allow_minus_veto_by_horizon_s"]["60"] is not None
    assert rep["allow_minus_veto_by_horizon_s"]["300"] is None   # not yet
