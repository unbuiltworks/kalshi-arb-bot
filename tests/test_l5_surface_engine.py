"""The surface engine: it must refuse before it guesses, and never promote.

The replay cases are the chain-proven parents from the frozen worked case
btc15m_1789665300, because a surface hypothesis that cannot reproduce a parent
we have the receipt for is not worth running forward.
"""
import ast
import io
import json
from decimal import Decimal as D

import pytest

from src.l5_intent.action import DesiredInventoryAction
from src.l5_surface import engine as E
from src.l5_surface import evidence as EV
from src.l5_surface import params as PR


def _p(name, value, status=PR.INFERRED, evidence=None):
    return PR.Param(name, status, value=value, evidence=evidence)


def _test_params(kind="FIDELITY", **over):
    """A parameter set for TESTING GEOMETRY ONLY.

    Every field is INFERRED, never OBSERVED: these numbers exercise the
    arithmetic and are not claims about L5. A test set that claimed to be
    observed would be the exact mislabelling params.py exists to stop.
    """
    base = {
        "rung_count": _p("rung_count", 3),
        "tick_offsets": _p("tick_offsets", [1, 2, 3]),
        "base_parent_qty": _p("base_parent_qty", "100"),
        "secondary_rung_qty": _p("secondary_rung_qty", "25"),
        "both_sides_policy": _p("both_sides_policy",
                                {"outcomes": ["Up", "Down"]}),
        "inventory_price_skew": _p("inventory_price_skew",
                                   {"widening_ticks": 0, "reducing_ticks": 0}),
        "inventory_size_skew": _p("inventory_size_skew",
                                  {"widening_multiplier": 1,
                                   "reducing_multiplier": 1}),
        "refresh_policy": _p("refresh_policy", {"mode": "ON_STATE_CHANGE"}),
        "replenishment_policy": _p("replenishment_policy",
                                   {"mode": "REPLACE_AFTER_FILL"}),
        "minimum_rest_time": _p("minimum_rest_time", 30.0),
        "withdrawal_policy": _p("withdrawal_policy", {"mode": "NEVER"}),
        "TTE_policy": _p("TTE_policy", {"quote_between_tte_s": [0, 900]}),
        "max_residual": _p("max_residual", "3"),
    }
    base.update({k: v for k, v in over.items()})
    return PR.SurfaceParams(label="TEST_GEOMETRY_ONLY", kind=kind,
                            params=base, notes="test set; INFERRED only")


def _state(**over):
    s = {"cohort": "BTC15m", "market_key": "btc-updown-15m-1789665300",
         "state_id": "st1", "t_utc": 1789665400.0, "tte_s": 600.0,
         "outcomes": {"Up": {"bid": 0.50, "ask": 0.51, "tick": 0.01},
                      "Down": {"bid": 0.49, "ask": 0.50, "tick": 0.01}},
         "inventory": {}}
    s.update(over)
    return s


# ----------------------------------------------------- refuse before guessing

def test_the_shipped_fidelity_set_cannot_produce_a_surface():
    r = E.desired_surface(_state(), PR.FIDELITY_V0)
    assert r["emitted"] is False
    assert set(r["blocked_by"]) == set(E.LOAD_BEARING)
    assert "no default is substituted" in r["reason"]


def test_an_unresolved_parameter_has_no_value_to_read():
    with pytest.raises(PR.Unresolved):
        PR.FIDELITY_V0.get("base_parent_qty")


def test_an_unresolved_parameter_cannot_secretly_carry_one():
    with pytest.raises(ValueError) as e:
        PR.Param("x", PR.UNRESOLVED, value=100)
    assert "mislabelled default" in str(e.value)


def test_a_parameter_claiming_to_be_observed_must_cite_something():
    with pytest.raises(ValueError) as e:
        PR.Param("x", PR.OBSERVED_PROSPECTIVE, value=100)
    assert "cites nothing" in str(e.value)


def test_a_fidelity_set_cannot_carry_our_own_risk_choices():
    with pytest.raises(ValueError) as e:
        _test_params(max_residual=PR.Param("max_residual", PR.OURS_ADAPTED,
                                           value="3"))
    assert "cannot carry OUR choices" in str(e.value)


def test_the_shipped_sets_claim_no_l5_evidence_yet():
    """Nothing has been measured prospectively, so nothing may say it was."""
    for s in (PR.FIDELITY_V0, PR.ADAPTED_V0):
        assert s.l5_evidence_fields() == []


def test_adapted_resolves_only_the_fields_that_are_genuinely_ours():
    """Four, and each for a stated reason. Two are risk bounds; two are
    fields NO study of public data can ever reach -- a level shift with
    inventory is not visible in a study of levels, and a cancellation is not
    in any feed we have. Supplying them is a deployment choice, so they are
    OURS_ADAPTED and can never be cited as evidence about L5."""
    ours = {"max_residual", "minimum_rest_time", "inventory_price_skew",
            "withdrawal_policy", "base_parent_qty", "inventory_size_skew",
            "replenishment_policy"}
    assert set(PR.ADAPTED_V0.resolved()) == ours
    for f in ours:
        p = PR.ADAPTED_V0.params[f]
        assert p.status == PR.OURS_ADAPTED
        assert p.evidence is None, "ours is never evidence about him"
    assert PR.ADAPTED_V0.l5_evidence_fields() == []


def test_a_prior_is_recorded_without_becoming_a_value():
    p = PR.FIDELITY_V0.params["base_parent_qty"]
    assert p.status == PR.UNRESOLVED and p.value is None
    assert "1,142" in p.prior_evidence and "81.1% were not 100" in p.prior_evidence


def test_the_spec_sha_covers_evidence_not_just_numbers():
    a = _test_params()
    b = _test_params(base_parent_qty=_p("base_parent_qty", "100",
                                        evidence=None))
    c = _test_params(base_parent_qty=PR.Param(
        "base_parent_qty", PR.INFERRED, value="100",
        evidence="a different justification"))
    assert a.spec_sha256() == b.spec_sha256()
    assert a.spec_sha256() != c.spec_sha256()


# ------------------------------------------------------------ the geometry

def test_the_ladder_is_built_behind_the_ask_on_both_outcomes():
    r = E.desired_surface(_state(), _test_params())
    assert r["emitted"] is True
    s = r["surface"]
    up = [x for x in s.rungs if x.outcome == "Up"]
    dn = [x for x in s.rungs if x.outcome == "Down"]
    assert [x.max_acceptable_price for x in up] == ["0.49", "0.48", "0.47"]
    assert [x.max_acceptable_price for x in dn] == ["0.48", "0.47", "0.46"]
    assert [x.desired_qty for x in up] == ["100", "25", "25"]
    assert [x.role for x in up] == ["BASE", "SECONDARY", "SECONDARY"]
    assert all(x.side == "BUY" for x in s.rungs)
    assert all(x.attribution == "INFERRED_SURFACE" for x in s.rungs)


def test_a_one_sided_policy_quotes_one_outcome_only():
    sp = _test_params(both_sides_policy=_p("both_sides_policy",
                                           {"outcomes": ["Up"]}))
    s = E.desired_surface(_state(), sp)["surface"]
    assert {x.outcome for x in s.rungs} == {"Up"}


def test_the_tte_policy_can_withhold_the_whole_surface():
    sp = _test_params(TTE_policy=_p("TTE_policy",
                                    {"quote_between_tte_s": [60, 900]}))
    r = E.desired_surface(_state(tte_s=30.0), sp)
    assert r["emitted"] is False and r["blocked_by"] == []
    assert "TTE_policy withholds" in r["reason"]


def test_the_price_skew_shifts_only_the_widening_side():
    sp = _test_params(inventory_price_skew=_p(
        "inventory_price_skew", {"widening_ticks": 2, "reducing_ticks": 0}))
    s = E.desired_surface(_state(inventory={"Up": 50, "Down": 0}), sp)["surface"]
    up = [x for x in s.rungs if x.outcome == "Up"]
    dn = [x for x in s.rungs if x.outcome == "Down"]
    assert [x.max_acceptable_price for x in up] == ["0.47", "0.46", "0.45"]
    assert [x.max_acceptable_price for x in dn] == ["0.48", "0.47", "0.46"]


def test_the_size_skew_scales_only_the_widening_side():
    sp = _test_params(inventory_size_skew=_p(
        "inventory_size_skew", {"widening_multiplier": "0.5",
                                "reducing_multiplier": 1}))
    s = E.desired_surface(_state(inventory={"Up": 0, "Down": 50}), sp)["surface"]
    dn = [x for x in s.rungs if x.outcome == "Down"]
    up = [x for x in s.rungs if x.outcome == "Up"]
    assert dn[0].desired_qty == "50.0"
    assert up[0].desired_qty == "100"


def test_max_residual_suppresses_the_widening_side_and_keeps_the_other():
    s = E.desired_surface(_state(inventory={"Up": 10, "Down": 0}),
                          _test_params())["surface"]
    up = [x for x in s.rungs if x.outcome == "Up"]
    dn = [x for x in s.rungs if x.outcome == "Down"]
    assert all(x.suppressed_by and "max_residual" in x.suppressed_by
               for x in up)
    assert all(x.suppressed_by is None for x in dn)
    assert s.overlay["suppressed_rungs"] == len(up)
    assert len(s.live_rungs()) == len(dn)


def test_the_surface_id_changes_with_the_spec_and_with_the_rungs():
    a = E.desired_surface(_state(), _test_params())["surface"]
    b = E.desired_surface(_state(), _test_params(
        base_parent_qty=_p("base_parent_qty", "80")))["surface"]
    assert a.surface_id() != b.surface_id()


# --------------------------------------------- projection onto the intent spine

def test_each_live_rung_becomes_one_intent_and_suppressed_rungs_do_not():
    s = E.desired_surface(_state(inventory={"Up": 10, "Down": 0}),
                          _test_params())["surface"]
    ints = E.to_intents(s, model_version="surface-test",
                        inventory={"Up": 10, "Down": 0})
    assert len(ints) == len(s.live_rungs()) == 3
    assert all(isinstance(i, DesiredInventoryAction) for i in ints)
    assert {i.desired_outcome for i in ints} == {"Down"}


def test_the_action_label_is_derived_from_inventory_not_chosen():
    sp = _test_params(max_residual=_p("max_residual", "9999"))
    flat = E.to_intents(E.desired_surface(_state(), sp)["surface"],
                        model_version="v", inventory={})
    assert {i.action for i in flat} == {"OPEN"}
    long_up = E.to_intents(
        E.desired_surface(_state(inventory={"Up": 50, "Down": 0}),
                          sp)["surface"],
        model_version="v", inventory={"Up": 50, "Down": 0})
    by = {(i.desired_outcome, i.action) for i in long_up}
    assert ("Up", "EXTEND") in by and ("Down", "COMPLETE") in by


def test_every_intent_carries_the_surface_and_spec_identity():
    s = E.desired_surface(_state(), _test_params())["surface"]
    for i in E.to_intents(s, model_version="v", inventory={}):
        assert i.extra["surface_id"] == s.surface_id()
        assert i.extra["spec_sha256"] == s.spec_sha256
        assert i.extra["attribution"] == "INFERRED_SURFACE"
        assert 0 < float(i.max_acceptable_price) < 1


def test_the_engine_has_no_money_moving_capability():
    tree = ast.parse(io.open("src/l5_surface/engine.py").read())
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for banned in ("post", "submit", "place_order", "create_order", "signer",
                   "private_key", "api_secret", "environ", "getenv"):
        assert banned not in {x.lower() for x in names}, banned


# ------------------------------------- replay: the chain-proven parent cases

def _prints(*rows):
    return [{"t": t, "price": str(p), "size": str(q), "side": "SELL"}
            for t, p, q in rows]


def _rung(price, qty, outcome="Down", idx=0):
    return EV.RungEvidence(surface_id="s", spec_sha256="x",
                           market_key="btc-updown-15m-1789665300",
                           outcome=outcome, price=str(price),
                           desired_qty=str(qty), rung_index=idx)


def test_replay_the_30_10_40_fills_that_collapsed_to_one_parent_of_80():
    """Frozen worked case: three public fills of 30/10/40 at 7c are ONE
    chain-proven parent, orderHash 0x03bb5ae0c1f1b2da, of exactly 80.00.

    So 80 shares of flow at 7c is ONE counterparty's order being filled. A
    100-share rung of ours behind an empty queue is NOT covered by it, and the
    ladder must stop at TRADE_FLOW rather than call 80 a fill of 100."""
    flow = _prints((100.0, "0.07", "30"), (100.5, "0.07", "10"),
                   (101.0, "0.07", "40"))
    hundred = EV.walk(_rung("0.07", "100"), restable=True, touch_reached=True,
                      prints=flow, queue_ahead="0", placed_at=90.0,
                      observed_until=200.0, coverage_complete=True)
    assert hundred.stage == EV.TRADE_FLOW
    assert hundred.is_fill() is False
    assert hundred.filled_qty() == D("0")
    assert hundred.detail[EV.TRADE_FLOW]["volume_at_price"] == "80"

    # an 80-share rung IS covered by exactly that flow
    eighty = EV.walk(_rung("0.07", "80"), restable=True, touch_reached=True,
                     prints=flow, queue_ahead="0", placed_at=90.0,
                     observed_until=200.0, coverage_complete=True)
    assert eighty.stage == EV.EVIDENCED_FILL and eighty.is_fill()
    assert eighty.filled_qty() == D("80")


def test_replay_a_queue_ahead_of_us_absorbs_the_same_eighty():
    """Same flow, but 80 already resting ahead. Nothing reaches us."""
    flow = _prints((100.0, "0.07", "30"), (100.5, "0.07", "10"),
                   (101.0, "0.07", "40"))
    e = EV.walk(_rung("0.07", "80"), restable=True, touch_reached=True,
                prints=flow, queue_ahead="80", placed_at=90.0,
                observed_until=200.0, coverage_complete=True)
    assert e.stage == EV.EVIDENCED_FILL or e.stage == EV.TRADE_FLOW
    assert e.is_fill() is False, "queue priority we did not have"
    assert e.filled_qty() == D("0")


def test_replay_the_exact_100_lot_single_execution():
    """Frozen worked case: 100 Down @8c is one parent of exactly 100.00 in a
    single execution -- and 18.9% of 1,142 parents were exactly 100.00."""
    e = EV.walk(_rung("0.08", "100"), restable=True, touch_reached=True,
                prints=_prints((120.0, "0.08", "100")), queue_ahead="0",
                placed_at=100.0, observed_until=200.0, coverage_complete=True)
    assert e.is_fill() and e.filled_qty() == D("100")
    assert e.detail[EV.EVIDENCED_FILL]["criteria"]["VOLUME_AHEAD_CLEARED"]


def test_replay_the_92_51_execution_is_a_lower_bound_not_a_parent_size():
    """92.51 @7c executed in one go and is NOT round. It is consistent with a
    partly-filled 100-lot, so executed_qty bounds the order from BELOW. A
    100-share rung must therefore NOT be called filled by it."""
    flow = _prints((130.0, "0.07", "92.51"))
    e100 = EV.walk(_rung("0.07", "100"), restable=True, touch_reached=True,
                   prints=flow, queue_ahead="0", placed_at=100.0,
                   observed_until=200.0, coverage_complete=True)
    assert e100.is_fill() is False
    assert e100.stage == EV.TRADE_FLOW
    e92 = EV.walk(_rung("0.07", "92.51"), restable=True, touch_reached=True,
                  prints=flow, queue_ahead="0", placed_at=100.0,
                  observed_until=200.0, coverage_complete=True)
    assert e92.is_fill() and e92.filled_qty() == D("92.51")


def test_replay_the_adjacent_tick_pair_geometry_reproduces():
    """Both simultaneous-parent instances in the worked case were SAME-OUTCOME
    and ONE TICK apart, pairing a 100-lot with a small lot: Up 0.90/100.00 with
    Up 0.89/33.38, and Down 0.08/100.00 with Down 0.09/2.00.

    A 2-rung one-tick ladder with base 100 and secondary 33.38 reproduces the
    first exactly. The parameters that do so are INFERRED here and are NOT
    thereby established -- n=2 instances is not a measured offset set.
    """
    sp = _test_params(
        rung_count=_p("rung_count", 2),
        tick_offsets=_p("tick_offsets", [1, 2]),
        base_parent_qty=_p("base_parent_qty", "100"),
        secondary_rung_qty=_p("secondary_rung_qty", "33.38"),
        both_sides_policy=_p("both_sides_policy", {"outcomes": ["Up"]}),
        max_residual=_p("max_residual", "9999"))
    st = _state(outcomes={"Up": {"bid": 0.90, "ask": 0.91, "tick": 0.01}})
    s = E.desired_surface(st, sp)["surface"]
    assert [(r.max_acceptable_price, r.desired_qty) for r in s.rungs] == [
        ("0.89", "100"), ("0.88", "33.38")]
    assert s.spec_label == "TEST_GEOMETRY_ONLY"


# ------------------------------------------------------ the ladder's one rule

def test_no_stage_below_evidenced_fill_ever_counts_as_one():
    evs = []
    for stage in (EV.DESIRED_SURFACE, EV.RESTABLE, EV.BOOK_REACHED,
                  EV.TRADE_FLOW):
        e = _rung("0.50", "100")
        for s in EV.STAGES[1:EV.STAGES.index(stage) + 1]:
            e.advance(s)
        assert e.stage == stage
        assert e.is_fill() is False and e.filled_qty() == D("0")
        evs.append(e)
    inv = EV.inventory_from(evs)
    assert inv["inventory"] == {} and inv["rungs_counted"] == 0
    assert inv["residual"] == "0" and inv["matched"] == "0"


def test_inventory_counts_evidenced_fills_only():
    a = _rung("0.50", "100", outcome="Up")
    for s in EV.STAGES[1:EV.STAGES.index(EV.EVIDENCED_FILL) + 1]:
        a.advance(s, modelled_fill_qty="100")
    b = _rung("0.49", "100", outcome="Down")
    for s in EV.STAGES[1:EV.STAGES.index(EV.TRADE_FLOW) + 1]:
        b.advance(s)
    inv = EV.inventory_from([a, b])
    assert inv["inventory"] == {"Up": "100"}
    assert inv["residual"] == "100" and inv["matched"] == "0"
    assert inv["rungs_counted"] == 1 and inv["rungs_considered"] == 2
    assert "SHADOW inventory" in inv["labels"]


def test_fill_detail_attached_to_a_weak_stage_is_caught():
    e = _rung("0.50", "100")
    e.advance(EV.RESTABLE)
    e.detail[EV.EVIDENCED_FILL] = {"modelled_fill_qty": "100"}
    with pytest.raises(EV.Promotion):
        EV.inventory_from([e])


def test_restability_not_established_stops_at_the_first_rung():
    e = EV.walk(_rung("0.50", "100"), restable=None, touch_reached=True,
                prints=_prints((1.0, "0.50", "999")), queue_ahead="0",
                placed_at=0.0, observed_until=10.0)
    assert e.stage == EV.DESIRED_SURFACE
    assert "not established" in e.detail[EV.RESTABLE]["why"]


def test_an_unreadable_book_is_not_a_reached_touch():
    e = EV.walk(_rung("0.50", "100"), restable=True, touch_reached=None,
                prints=_prints((1.0, "0.50", "999")), queue_ahead="0",
                placed_at=0.0, observed_until=10.0)
    assert e.stage == EV.RESTABLE
    assert "not readable" in e.detail[EV.BOOK_REACHED]["why"]


def test_settlement_and_realization_are_refused_for_a_shadow():
    s = EV.settlement_placeholder("m")
    assert s[EV.SETTLED]["satisfied"] is False
    assert s[EV.REALIZED]["satisfied"] is False
    assert "venue-confirmed cash" in s[EV.REALIZED]["why"]


def test_recycling_requires_fills_on_both_sides():
    a = _rung("0.50", "100", outcome="Up")
    for s in EV.STAGES[1:EV.STAGES.index(EV.EVIDENCED_FILL) + 1]:
        a.advance(s, modelled_fill_qty="100", t=100.0)
    a.detail[EV.EVIDENCED_FILL]["t"] = 100.0
    b = _rung("0.49", "100", outcome="Down")
    for s in EV.STAGES[1:EV.STAGES.index(EV.TRADE_FLOW) + 1]:
        b.advance(s)
    r = EV.recycling([a, b], [5, 30])
    assert r["n_fills"] == 1
    assert r["n_opposite_within_30s"] == 0


def test_the_walk_resumes_rather_than_restarting_each_tick():
    """In a live loop every rung is re-walked on every tick. A walk that
    restarted from DESIRED_SURFACE would raise on the second tick; one that
    reset the stage would throw away evidence already established."""
    e = _rung("0.50", "100")
    kw = dict(restable=True, queue_ahead="0", placed_at=0.0)

    # tick 1: the touch has not reached us and nothing has traded
    EV.walk(e, touch_reached=False, prints=[], observed_until=10.0,
            coverage_complete=True, **kw)
    assert e.stage == EV.RESTABLE

    # tick 2: the touch arrives. It must ADVANCE, not start over
    EV.walk(e, touch_reached=True, prints=[], observed_until=20.0,
            coverage_complete=True, **kw)
    assert e.stage == EV.BOOK_REACHED
    assert e.reached == [EV.DESIRED_SURFACE, EV.RESTABLE, EV.BOOK_REACHED]

    # tick 3: volume trades through and covers us
    EV.walk(e, touch_reached=True,
            prints=[{"t": 15.0, "price": "0.49", "size": "150"}],
            observed_until=30.0, coverage_complete=True, **kw)
    assert e.stage == EV.EVIDENCED_FILL and e.is_fill()
    assert e.filled_qty() == D("100")

    # tick 4: re-walking a filled rung changes nothing and raises nothing
    EV.walk(e, touch_reached=True,
            prints=[{"t": 16.0, "price": "0.49", "size": "150"}],
            observed_until=40.0, coverage_complete=True, **kw)
    assert e.stage == EV.EVIDENCED_FILL and e.filled_qty() == D("100")


def test_a_gate_that_failed_earlier_can_still_pass_later():
    """Evidence accumulates. A rung whose book was unreadable on one tick is
    not condemned to stay there."""
    e = _rung("0.50", "100")
    EV.walk(e, restable=None, touch_reached=True, prints=[], queue_ahead="0",
            placed_at=0.0, observed_until=10.0)
    assert e.stage == EV.DESIRED_SURFACE
    EV.walk(e, restable=True, touch_reached=True, prints=[], queue_ahead="0",
            placed_at=0.0, observed_until=20.0)
    assert e.stage == EV.BOOK_REACHED



def test_rung_zero_joins_the_bid_and_is_therefore_passive():
    """The scorer measures ticks_from_touch against the BID for a resting BUY,
    so an offset of 0 means "at the best bid". Reading that as ask-relative
    would put rung 0 ON THE ASK -- a crossing taker order, in the module whose
    whole purpose is a passive surface."""
    sp = _test_params(rung_count=_p("rung_count", 1),
                      tick_offsets=_p("tick_offsets", [0]),
                      max_residual=_p("max_residual", "9999"))
    s = E.desired_surface(_state(), sp)["surface"]
    up = [x for x in s.rungs if x.outcome == "Up"][0]
    dn = [x for x in s.rungs if x.outcome == "Down"][0]
    assert up.max_acceptable_price == "0.50"   # the bid, not the 0.51 ask
    assert dn.max_acceptable_price == "0.49"
    assert up.ticks_behind_bid == 0
    # and it really is passive at the venue
    from src.l5_surface import shadow as S
    book = {"bids": [{"price": "0.50", "size": "10"}],
            "asks": [{"price": "0.51", "size": "12"}]}
    chk = S.restable(float(up.max_acceptable_price), 100,
                     {"tick": "0.01", "order_min_size": 5}, book)
    assert chk["restable"] is True


def test_an_outcome_with_no_bid_is_skipped_rather_than_priced_off_the_ask():
    sp = _test_params(max_residual=_p("max_residual", "9999"))
    st = _state(outcomes={"Up": {"bid": None, "ask": 0.51, "tick": 0.01},
                          "Down": {"bid": 0.49, "ask": 0.50, "tick": 0.01}})
    s = E.desired_surface(st, sp)["surface"]
    assert {x.outcome for x in s.rungs} == {"Down"}
