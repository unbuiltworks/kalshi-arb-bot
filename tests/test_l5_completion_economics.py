"""P1: a completion of HELD inventory is judged on its own economics.

THE DEFECT THESE PIN, from the four settled windows of 2026-10-28.

`complement_order` refuses a completion on three OPENING tests: the 0.96
pair-cost cap, the fee floor, and both-outcomes-positive. Those answer "is
this pair worth STARTING?". Once a leg has filled, the question is "does
completing beat holding the residual to settlement?", and the two have
different answers at exactly the prices that matter.

MEASURED, with the owner's $3.00 residual budget:

  window  worst if held   improves  within_budget  pair_cap  min(all)
  1245z        -0.7264        0.95           0.95      0.62      0.62
  1215z        -0.3835        0.95           0.95      0.74      0.74
  1300z        -0.6674        0.95           0.95      0.57      0.57

`pair_cap` binds in every case while `improves` permits the whole grid. So
the opening cap, and not the economics, is what refuses a dear completion.

AND THE SECOND DEFECT, which is what actually cost the fills. Completions
are quoted `depth_ticks` behind the touch -- 2 on the deployed service --
so they rest where the price must come BACK to reach them:

  1215z 12:23:50  touch own 0.4900, quoted 0.4700, rested 56s, cancelled
  1245z 12:52:25  touch own 0.6500, quoted 0.6300, rested 21s, cancelled
                  queue telemetry: requires_price_to_reach_us true

At the 1215z touch the pair is 0.8593, inside 0.96, so the ceiling was
never the obstacle there -- the two-tick offset was. At the 1245z touch the
pair is 0.9700 and the ceiling IS the obstacle. Both defects are real and
they bind in different windows.

WHAT THIS DELIBERATELY DOES NOT CLAIM. `improves` is a WORST-CASE test, not
an expected-value one: completing gives up the outcome where the residual
wins. That is the intended trade under the operating model's rule that
residual directional exposure is not assumed to have predictive skill, and
a matched pair redeems exactly $1.00. It is a risk reduction, not a
discovered edge, and no fill is assumed anywhere here.
"""
from decimal import Decimal as D

import pytest

from src import l5_two_sided_probe as TS

from tests.test_l5_two_sided_wiring import (_book, _row, gates,  # noqa: F401
                                            mk, probe)

BUDGET = "3.00"

#: the three live windows, as the venue settled them
W1215 = dict(held_side="UP", held_qty="4.21", held_cost_usd="1.5547",
             deficient_qty="1.92", deficient_cost_usd="0.7488",
             complete_qty="2.29", residual_budget_usd=BUDGET)
W1245 = dict(held_side="UP", held_qty="2.27", held_cost_usd="0.7264",
             deficient_qty="0", deficient_cost_usd="0",
             complete_qty="2.27", residual_budget_usd=BUDGET)
W1300 = dict(held_side="UP", held_qty="4.42", held_cost_usd="1.5642",
             deficient_qty="2.36", deficient_cost_usd="1.4632",
             complete_qty="2.06", residual_budget_usd=BUDGET)


# ------------------------------------------- the economics, measured

@pytest.mark.parametrize("case,worst_held", [
    (W1215, "-0.3835"), (W1245, "-0.7264"), (W1300, "-0.6674")])
def test_the_pair_cap_binds_where_the_economics_do_not(case, worst_held):
    r = TS.max_completion_price(**case)
    assert r["ok"] is True
    assert r["worst_outcome_if_held_usd"] == worst_held
    c = r["ceilings"]
    assert c["improves"] == "0.95"
    assert c["within_budget"] == "0.95"
    assert D(c["pair_cap"]) < D(c["improves"])
    assert r["binding_ceiling"] == "pair_cap"


def test_the_1245z_touch_improves_on_stranding_but_breaches_the_opening_cap():
    """own 0.65 against a 0.32 basis is a 0.97 pair -- refused by the
    opening cap -- yet it turns a -$0.7264 strand into a near-flat book."""
    r = TS.max_completion_price(**W1245)
    assert D("0.65") <= D(r["ceilings"]["improves"])
    assert D("0.65") > D(r["ceilings"]["pair_cap"])
    assert D("0.32") + D("0.65") > TS.max_pair_cost()


def test_the_completion_ceiling_is_the_in_force_minimum():
    """`improves` and `within_budget` are the ceilings a RISK-REDUCING
    completion answers to. The opening cap is not one of them."""
    for case in (W1215, W1245, W1300):
        got = TS.completion_ceiling(**case)
        r = TS.max_completion_price(**case)
        want = min(D(r["ceilings"]["improves"]),
                   D(r["ceilings"]["within_budget"]))
        assert got == want


def test_a_tight_residual_budget_binds_instead():
    """The owner's budget is a real ceiling, not decoration."""
    tight = dict(W1245, residual_budget_usd="0.10")
    assert TS.completion_ceiling(**tight) < TS.completion_ceiling(**W1245)


def test_an_unreadable_budget_yields_no_ceiling():
    assert TS.completion_ceiling(**dict(W1245,
                                        residual_budget_usd=None)) is None


def test_the_ceiling_responds_to_the_REALISED_basis_in_both_directions():
    """1300z realised a 0.9739 matched pair because later UP fills at own
    0.45 lifted the weighted basis off the 0.27 first fill. The ceiling is
    therefore computed from the cash ACTUALLY paid, and the two ceilings
    move in OPPOSITE directions as that basis dearens:

      held_cost   worst if held   improves   pair_cap
       1.1934          -0.2966        0.95       0.75
       1.5642          -0.6674        0.95       0.57
       1.9000          -1.0032        0.95       0.40

    `pair_cap` tightens, which is the opening discipline reacting to a
    dearer leg. `improves` does NOT tighten, and that is correct rather than
    a loophole: a dearer first leg makes STRANDING worse, so a dearer
    completion is the rational response to it. Paying more to avoid losing
    more is the trade, and `within_budget` is what bounds how far it goes.
    """
    W13 = dict(held_side="UP", held_qty="4.42", deficient_qty="2.36",
               deficient_cost_usd="1.4632", complete_qty="2.06",
               residual_budget_usd=BUDGET)
    rows = [TS.max_completion_price(**dict(W13, held_cost_usd=hc))
            for hc in ("1.1934", "1.5642", "1.9000")]
    worst = [D(r["worst_outcome_if_held_usd"]) for r in rows]
    caps = [D(r["ceilings"]["pair_cap"]) for r in rows]
    assert worst[0] > worst[1] > worst[2]        # stranding gets worse
    assert caps[0] > caps[1] > caps[2]           # the OPENING cap tightens
    # the basis is the cash actually paid, sunk across both legs
    assert rows[1]["avg_cost_px"] == "0.6849"    # (1.5642 + 1.4632) / 4.42


def test_a_pair_above_the_opening_cap_is_reported_not_silent():
    """P1 does NOT make the 1300z overshoot disappear -- that pair was
    lifted by an OPENING buy, and openings keep answering to the 0.96 cap.
    What it must not do is let a completion breach the cap QUIETLY, so the
    payload states the verdict and the opening test separately."""
    r = _cmp(W1245, "0.3200", "0.3300")
    assert r["ok"] is True, r
    assert r["judged_as"] == "COMPLETION"
    assert r["clears_opening_pair_cap"] is False
    assert D(r["pair_cost"]) > TS.max_pair_cost()


# ------------------------------------- the rescue, and its limits
#
# SCOPE, STATED HONESTLY. This change is STRICTLY ADDITIVE: it converts a
# refused completion into a permitted one and never moves a price or
# re-judges a quote the opening discipline already accepted.
#
# The consequence is that it is INERT on all four windows of 2026-10-28,
# and the tests below say so. At the deployed two-tick offset every one of
# those completions CLEARED the 0.96 cap -- 1215z at own 0.47 for a 0.839
# pair, 1245z at own 0.63 for a 0.95 pair -- and was placed. They did not
# strand because the cap refused them; they stranded because a quote two
# ticks behind the touch rested where the price had to come back to reach
# it, and it never did.
#
# Moving completions to the touch is therefore the change that would have
# saved those windows, and it is deliberately NOT bundled here: quoting the
# touch makes the ordinary second leg of a round dearer, pushes routine
# pairs past 0.96, and -- measured -- refused the second leg outright where
# the ceiling sat below the touch, which stopped accumulation dead in eight
# existing wiring tests. That is a policy decision about the opening
# discipline, not a bug fix, and it belongs to its own proposal.


def _cmp(case, bid, ask, **over):
    """`complement_order` with the completion context supplied."""
    kw = dict(side="DOWN", qty=case["complete_qty"],
              cost_px=str(D(case["held_cost_usd"]) / D(case["held_qty"])),
              bid=bid, ask=ask, tick="0.01", min_qty="0.01", depth_ticks=2,
              book_age_s="0.05", market="m", held_side="UP",
              bid_shares="5000", ask_shares="5000",
              held_qty=case["held_qty"], held_cost_usd=case["held_cost_usd"],
              deficient_qty=case["deficient_qty"],
              deficient_cost_usd=case["deficient_cost_usd"],
              residual_budget_usd=case["residual_budget_usd"])
    kw.update(over)
    return TS.complement_order(**kw)


def test_the_rescue_fires_where_the_opening_cap_refuses():
    """Book bid 0.3200 / ask 0.3300 puts the two-tick DOWN quote at own
    0.6500 against a 0.32 basis: a 0.97 pair the opening cap refuses. As a
    completion it is permitted, because it beats stranding inside budget."""
    r = _cmp(W1245, "0.3200", "0.3300")
    assert r["ok"] is True, r
    assert r["judged_as"] == "COMPLETION"
    assert r["leg"]["price"] == "0.6500"
    assert D(r["pair_cost"]) > TS.max_pair_cost()
    assert r["clears_opening_pair_cap"] is False
    assert r["completion_ceiling"] == "0.95"


def test_the_rescue_does_not_move_the_price():
    """It permits the quote the engine would already have made. It does not
    reprice toward the touch -- that is the separate, unbundled change."""
    r = _cmp(W1245, "0.3200", "0.3300")
    bare = TS.complement_order(
        side="DOWN", qty="2.27", cost_px="0.32", bid="0.3200", ask="0.3300",
        tick="0.01", min_qty="0.01", depth_ticks=2, book_age_s="0.05",
        market="m", held_side="UP", bid_shares="5000", ask_shares="5000")
    assert bare["ok"] is False                       # refused today
    assert r["leg"]["price"] == "0.6500"             # same price, permitted
    assert r["leg"]["touch_at_placement"] == "0.6700"


def test_it_is_INERT_on_the_four_settled_windows():
    """The live completions all cleared the cap at the deployed offset, so
    this change would not have altered any of them. Pinned so the claim
    cannot quietly drift into an overstatement."""
    r1215 = _cmp(W1215, "0.4900", "0.5100")
    r1245 = _cmp(W1245, "0.3400", "0.3500")
    for r, px in ((r1215, "0.4700"), (r1245, "0.6300")):
        assert r["ok"] is True, r
        assert r["judged_as"] == "OPENING"
        assert r["leg"]["price"] == px
        assert r["clears_opening_pair_cap"] is True


def test_a_rescue_the_ceiling_also_refuses_stays_refused():
    """Adverse selection: the residual is carried, not chased. The refusal
    keeps the opening reason and states that no rescue was available."""
    # book 0.20/0.21 puts the two-tick quote at own 0.77 for a 1.09 pair.
    # With the budget at $3.00 the ceiling is 0.95 and the rescue fires; at
    # zero it is 0.66 and the residual is carried instead.
    loose = _cmp(W1245, "0.2000", "0.2100")
    assert loose["ok"] is True and loose["judged_as"] == "COMPLETION"
    tight = dict(W1245, residual_budget_usd="0")
    r = _cmp(tight, "0.2000", "0.2100")
    assert r["ok"] is False
    assert r["reason"] == "COMPLEMENT_PAIR_ABOVE_CAP"
    assert r["completion_rescue_available"] is False
    assert r["completion_ceiling"] == "0.66"


def test_without_a_budget_there_is_no_rescue():
    r = _cmp(dict(W1245, residual_budget_usd=None), "0.3200", "0.3300")
    assert r["ok"] is False
    assert r["completion_rescue_available"] is False
    assert r["completion_ceiling"] is None


def test_the_rescued_completion_never_crosses_and_stays_post_only():
    r = _cmp(W1245, "0.3200", "0.3300")
    assert r["ok"] is True
    px = D(r["leg"]["price"])
    assert TS.leg_crosses("DOWN", px, bid=D("0.3200"), ask=D("0.3300")) \
        is False
    assert r["leg"]["mode"] == TS.MODE
    assert r["leg"]["post_only"] is True


def test_the_rescued_quantity_never_exceeds_the_unmatched_remainder():
    r = _cmp(W1245, "0.3200", "0.3300")
    assert D(r["leg"]["qty"]) <= D("2.27")


# ------------------------------- what must NOT change

def test_without_completion_context_the_old_cap_still_refuses():
    """The fallback is today's behaviour exactly: no context, no new rule.

    Probed at the TOUCH (depth_ticks 0), because that is where the 1245z
    pair reaches 0.97. At the deployed depth_ticks of 2 the quote is own
    0.63 for a 0.95 pair, which the opening cap allows -- and the venue
    record confirms that order was placed and simply never filled.
    """
    r = TS.complement_order(
        side="DOWN", qty="2.27", cost_px="0.32", bid="0.3400", ask="0.3500",
        tick="0.01", min_qty="0.01", depth_ticks=0, book_age_s="0.05",
        market="m", held_side="UP", bid_shares="5000", ask_shares="5000")
    assert r["ok"] is False
    assert r["reason"] == "COMPLEMENT_PAIR_ABOVE_CAP"


def test_without_context_the_deployed_two_tick_quote_was_permitted():
    """Pins what actually happened live: 1245z's completion was NOT refused
    by the cap at depth_ticks 2. It was permitted at own 0.63 and the price
    never came back to it. So the cap and the offset are separate defects."""
    r = TS.complement_order(
        side="DOWN", qty="2.27", cost_px="0.32", bid="0.3400", ask="0.3500",
        tick="0.01", min_qty="0.01", depth_ticks=2, book_age_s="0.05",
        market="m", held_side="UP", bid_shares="5000", ask_shares="5000")
    assert r["ok"] is True, r
    assert r["leg"]["price"] == "0.6300"


def test_the_OPENING_pair_ceiling_is_untouched():
    assert TS.max_pair_cost() == D("0.96")


def test_an_opening_plan_is_still_gated_at_the_opening_cap(probe, tmp_path):
    """End to end: a FLAT market still may not open a pair above 0.96."""
    H, E = probe
    ex, t = mk(H, E, tmp_path)
    # bid 0.60/ask 0.61 plans UP 0.58 + DOWN 0.37 = 0.95, which is allowed
    rec = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert t.sent, rec.get("refused_reason")
    assert D(rec["TWO_SIDED_PROBE"]["pair_cost"]) <= D("0.96")


def test_a_held_residual_completes_end_to_end_and_does_not_reverse(
        probe, tmp_path):
    """Through the real executor: the completion is placed, the venue fills
    it whole, and the residual lands on zero without crossing it."""
    H, E = probe
    ex, t = mk(H, E, tmp_path, t=H.FakeTransport(fill_all=True))
    ex.store.apply_fill("m", "UP", D("2.29"), D("0.3693"), basis=E.OWN)
    rec = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert t.sent, rec.get("refused_reason")
    assert t.sent[-1]["side"] == "DOWN"
    assert ex.store.residual("m") >= D("0")
    assert ex.store.residual("m") <= D("2.29")


def test_a_no_fill_completion_leaves_the_residual_exactly_as_it_was(
        probe, tmp_path):
    """A permitted completion is an OFFER. When nothing fills, the residual
    is unchanged and no exposure was added."""
    H, E = probe
    ex, t = mk(H, E, tmp_path, t=H.FakeTransport(fill_qty="0"))
    ex.store.apply_fill("m", "UP", D("2.29"), D("0.3693"), basis=E.OWN)
    before = ex.store.residual("m")
    ex.handle(_row(H), _book(), market="m", window="21:00")
    assert ex.store.residual("m") == before == D("2.29")
