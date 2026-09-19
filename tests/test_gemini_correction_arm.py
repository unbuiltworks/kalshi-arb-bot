"""The corrected-timing arm: same tape, one field different, nothing shared.

WHAT THESE PIN. The comparison only means something if the two arms differ in
`quote_between_tte_s` and in NOTHING else, if neither can see the other's
fills, and if the arm cannot damage the collector it rides on. Each of those
is a property of running code here, not of a paragraph in a design note.
"""
from decimal import Decimal as D

import pytest

from src.gemini_execution import contract_cohort as CC
from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import shadow_soak as SS
from src.l5_surface import params as PR


SYM = "GEMI-BTC15M2609192030-UP"


def _book(yes_bid="0.40", yes_ask="0.42", no_bid="0.58", no_ask="0.60"):
    return {"readable": True, "identity_consistent": True,
            "yes": {"bid": yes_bid, "ask": yes_ask},
            "no": {"bid": no_bid, "ask": no_ask}}


def _arm(label, sp):
    return SS._Arm(label, sp, symbol=SYM, cohort_prefix="GEMI-BTC15M",
                   policy="FROZEN_L5_NATIVE")


# ------------------------------------------------------- the one difference

def test_THE_ARM_DIFFERS_FROM_THE_CONTROL_IN_EXACTLY_ONE_FIELD():
    corr = SS.correction_spec(CC.BTC15M)
    ctl = CP.probe_params()
    moved = [f for f in PR.FIELDS
             if (ctl.params[f].status, ctl.params[f].value)
             != (corr.params[f].status, corr.params[f].value)]
    assert moved == ["TTE_policy"], moved
    assert corr.get("TTE_policy") == {"quote_between_tte_s": [0, 900]}
    assert ctl.get("TTE_policy") == {"quote_between_tte_s": [0, 300]}
    # including nothing about size: an arm that also traded bigger would make
    # any difference in outcome unattributable
    for f in ("base_parent_qty", "secondary_rung_qty", "max_residual"):
        assert corr.get(f) == ctl.get(f), f


def test_THE_ARM_KEEPS_ITS_OWN_NAME_THROUGH_PROBE_SCOPING():
    """probe_params re-labels what it is given. An arm that lost its label
    would write decision records indistinguishable from the control's."""
    corr = SS.correction_spec(CC.BTC15M)
    assert corr.label == "L5_ADAPTED_GEMINI_BTC15M_V2"
    assert SS._corrects(corr.label) == "L5_ADAPTED_GEMINI_CRYPTO"
    assert SS._band_of(_arm(corr.label, corr)) == [0, 900]


def test_BTC5M_HAS_NO_ARM():
    assert SS.correction_spec(CC.BTC5M) is None
    assert "BTC5m" not in SS.CORRECTION_ARMS


# ------------------------------------------- the band is what actually binds

def test_THE_CONTROL_WITHHOLDS_ABOVE_TTE_300_AND_THE_ARM_DOES_NOT():
    """The whole correction, observable in one cycle at tte 800."""
    ctl = _arm("CONTROL", None)
    corr_sp = SS.correction_spec(CC.BTC15M)
    arm = _arm(corr_sp.label, corr_sp)
    flow = {"prints": []}
    a = SS._step_arm(ctl, symbol=SYM, tte=800.0, book=_book(), depth=None,
                     flow=flow, policy="FROZEN_L5_NATIVE")
    b = SS._step_arm(arm, symbol=SYM, tte=800.0, book=_book(), depth=None,
                     flow=flow, policy="FROZEN_L5_NATIVE")
    assert a["acted"] == [], a["acted"]
    assert "TTE_policy withholds" in str(a["turn"].get("surface") or
                                         a["turn"])
    assert b["acted"], "the corrected arm must quote where the control cannot"


def test_BELOW_TTE_300_THE_TWO_ARMS_AGREE():
    """Inside the frozen band the correction changes nothing, which is what
    "only timing" has to mean: the same state gives the same actions."""
    ctl = _arm("CONTROL", None)
    corr_sp = SS.correction_spec(CC.BTC15M)
    arm = _arm(corr_sp.label, corr_sp)
    flow = {"prints": []}
    a = SS._step_arm(ctl, symbol=SYM, tte=250.0, book=_book(), depth=None,
                     flow=flow, policy="FROZEN_L5_NATIVE")
    b = SS._step_arm(arm, symbol=SYM, tte=250.0, book=_book(), depth=None,
                     flow=flow, policy="FROZEN_L5_NATIVE")
    strip = lambda acts: [{k: v for k, v in x.items()} for x in acts]
    assert strip(a["acted"]) == strip(b["acted"])


# --------------------------------------------------------- nothing is shared

def test_ARMS_SHARE_THE_TAPE_AND_NOTHING_ELSE():
    """A fill in one arm must not appear in the other's inventory.

    If they shared a book or an inventory the second arm would be a function
    of the first's fills and the comparison would be measuring itself.
    """
    corr_sp = SS.correction_spec(CC.BTC15M)
    ctl = _arm("CONTROL", None)
    arm = _arm(corr_sp.label, corr_sp)
    assert ctl.inv is not arm.inv
    assert ctl.hyp is not arm.hyp
    assert ctl.guard is not arm.guard

    arm.inv.apply_fill("YES", D("0.01"), D("0.40"), source="test")
    assert arm.inv.residual() != 0
    assert ctl.inv.residual() == 0


def test_AN_ARM_THAT_RAISES_IS_RECORDED_AND_THE_OTHERS_STILL_STEP():
    """A research arm taking down a 24/7 collector is the one failure mode
    that would cost more than the question is worth.

    Driven, not asserted about: the first arm's hypothetical book is replaced
    with one that raises, and the second arm must still produce its record.
    """
    class _Boom:
        live = ()

        def consume(self, *a, **k):
            raise RuntimeError("arm exploded")

    corr_sp = SS.correction_spec(CC.BTC15M)
    bad = _arm("BROKEN", corr_sp)
    bad.hyp = _Boom()
    good = _arm(corr_sp.label, corr_sp)

    out = SS._step_arms([bad, good], symbol=SYM, tte=800.0, book=_book(),
                        depth=None, flow={"prints": []},
                        policy="FROZEN_L5_NATIVE")
    assert out["BROKEN"] == {"error": "RuntimeError: arm exploded"}
    assert bad.error == "RuntimeError: arm exploded"
    assert out[corr_sp.label]["decision"]["plan"]["place"], \
        "a healthy arm must still step when a sibling raises"
    assert "error" not in out[corr_sp.label]


def test_THE_ARM_HAS_NO_ORDER_PATH_AND_SENDS_NOTHING():
    """It is synchronous and touches only what it is handed, so there is no
    venue call to make. `place`/`cancel` appear as PLAN RECORD KEYS, which is
    a description of a rung that was never submitted -- the distinction this
    whole module exists to keep."""
    import ast
    import inspect
    import textwrap
    assert not inspect.iscoroutinefunction(SS._step_arm)
    # the CODE, with the prose stripped: a docstring saying "no session" must
    # not be what makes this pass
    fn = ast.parse(textwrap.dedent(inspect.getsource(SS._step_arm))).body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)):
        fn.body = fn.body[1:]
    code = ast.unparse(fn)
    for forbidden in ("await", "Driver", "session", ".place(", ".cancel(",
                      "submit", "httpx", "requests"):
        assert forbidden not in code, forbidden


# ------------------------------------------------------- the record it writes

def test_EVERY_ARM_RECORD_NAMES_WHAT_IT_CORRECTS_AND_ITS_BAND():
    corr_sp = SS.correction_spec(CC.BTC15M)
    out = SS._step_arms([_arm(corr_sp.label, corr_sp)], symbol=SYM,
                        tte=800.0, book=_book(), depth=None,
                        flow={"prints": []}, policy="FROZEN_L5_NATIVE")
    rec = out[corr_sp.label]
    assert rec["corrects"] == "L5_ADAPTED_GEMINI_CRYPTO"
    assert rec["quote_between_tte_s"] == [0, 900]
    # and the record carries its own inventory, not the control's
    assert "inventory" in rec and "hypothetical_fills" in rec
    import inspect
    assert "correction_arms_labels" in inspect.getsource(SS.one_window)


def test_THE_HEARTBEAT_REPORTS_ARMS_SEPARATELY_AND_NEVER_SUMS_THEM():
    hb = SS.CohortHeartbeat("BTC15m")
    hb.window_done({
        "symbol": SYM, "state": "COMPLETED", "finished_utc": "now",
        "inventory": {"matched_qty": "0", "directional_residual": "0"},
        "correction_arms": {
            "L5_ADAPTED_GEMINI_BTC15M_V2": {
                "corrects": "L5_ADAPTED_GEMINI_CRYPTO",
                "quote_between_tte_s": [0, 900],
                "hypothetical": {"first_fills": 3, "complete_fills": 2,
                                 "rungs_rested": 44},
                "inventory": {"matched_qty": "0.02",
                              "directional_residual": "0.01"},
                "error": None}},
    })
    a = hb.arms["L5_ADAPTED_GEMINI_BTC15M_V2"]
    assert a["first_fills"] == 3 and a["COMPLETE_fills"] == 2
    assert a["matched_pairs"] == 1 and a["residual"] == "0.01"
    assert a["band"] == [0, 900]
    # the control's own counters are untouched by the arm's
    assert hb.first_fills == 0 and hb.complete_fills == 0
    assert hb.matched_pairs == 0


def test_THE_ENTRY_BAND_LETS_THE_ARM_BE_SEEN_AT_ALL():
    """An arm quoting [0,900] on a lane that attaches at tte 280 would
    produce no evidence, and its silence would read as a finding."""
    lo, hi = CC.BTC15M.entry_band
    band = SS.correction_spec(CC.BTC15M).get(
        "TTE_policy")["quote_between_tte_s"]
    assert float(band[0]) <= lo <= hi <= float(band[1])
    # and well above the control's ceiling, so the two arms are actually
    # distinguishable for most of the window
    ctl_hi = CP.probe_params().get("TTE_policy")["quote_between_tte_s"][1]
    assert lo > float(ctl_hi)


# ----------------------------------------------- BTC5m must not have moved

def test_BTC5M_RUNS_THE_CONTROL_PATH_WITH_NO_ARM_BRANCH_AT_ALL():
    """The arm block is guarded by `if arms:`, and BTC5m has no arms.

    So the code BTC5m executes per cycle is the code it executed before,
    plus one integer written to the row. Nothing in its decision path is
    routed through _step_arm, which is why "byte-for-byte" is a statement
    about this file and not a hope.
    """
    import ast
    import inspect
    import textwrap
    src = textwrap.dedent(inspect.getsource(SS.one_window))
    tree = ast.parse(src)
    # the control's own step is NOT a call to _step_arm
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    names = [getattr(n.func, "id", getattr(n.func, "attr", ""))
             for n in calls]
    assert names.count("_step_arms") == 1, names.count("_step_arms")
    assert "_step_arm" not in names, "the control must not be routed via _step_arm"
    # and that single call sits under `if arms:`
    guarded = [n for n in ast.walk(tree)
               if isinstance(n, ast.If)
               and getattr(n.test, "id", None) == "arms"
               and "_step_arms" in ast.unparse(n)]
    assert len(guarded) == 1


def test_A_COHORT_WITHOUT_AN_ARM_WRITES_NO_ARM_KEYS():
    """A BTC5m row must not sprout correction_arms, empty or otherwise."""
    corr_sp = SS.correction_spec(CC.BTC5M)
    assert corr_sp is None
    out = SS._step_arms([], symbol=SYM, tte=250.0, book=_book(), depth=None,
                        flow={"prints": []}, policy="FROZEN_L5_NATIVE")
    assert out == {}


def test_THE_HEARTBEAT_OF_AN_ARMLESS_COHORT_REPORTS_ARMS_AS_NULL():
    hb = SS.CohortHeartbeat("BTC5m")
    hb.window_done({"symbol": "GEMI-BTC05M2609192115-UP", "state": "COMPLETED",
                    "inventory": {"matched_qty": "0",
                                  "directional_residual": "0"}})
    assert hb.arms == {}
    assert hb.windows == 1
