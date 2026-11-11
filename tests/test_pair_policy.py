"""The extracted pair rule: arithmetic only, and no probability model in it."""
import ast
from fractions import Fraction as F

from src.gemini_execution import pair_policy as PP

YES, NO = PP.YES, PP.NO
ACQ, COMP = F(95, 100), F(120, 100)


def d(**kw):
    base = dict(yes_qty=0, no_qty=0, avg_yes=None, avg_no=None,
                offered_side=YES, price=F(40, 100), q_hat=F(50, 100),
                acq_max=ACQ, comp_max=COMP)
    base.update(kw)
    return PP.decide(**base)


# -------------------------------------------------------------- acquisition
def test_acquisition_compares_the_projected_pair_against_its_ceiling():
    assert d(price=F(40, 100), q_hat=F(50, 100))["action"] == PP.ACQUIRE
    assert d(price=F(40, 100), q_hat=F(56, 100))["action"] == PP.SKIP


def test_the_acquisition_ceiling_is_inclusive():
    r = d(price=F(45, 100), q_hat=F(50, 100))
    assert r["projected_pair"] == ACQ and r["action"] == PP.ACQUIRE


def test_an_unknown_complement_price_is_refused_not_assumed():
    """The failure mode this guards: inventing a complement nobody has paid
    for, which turns a cheap first leg into a directional bet."""
    r = d(q_hat=None, price=F(5, 100))
    assert r["action"] == PP.SKIP
    assert r["no_qhat"] is True
    assert r["projected_pair"] is None


def test_a_leg_below_the_floor_is_refused_whatever_the_projection_says():
    assert d(price=F(12, 100), q_hat=F(10, 100))["action"] == PP.ACQUIRE
    r = d(price=F(12, 100), q_hat=F(10, 100), min_leg=F(20, 100))
    assert r["action"] == PP.SKIP and r["below_min_leg"] is True


def test_the_floor_is_not_applied_to_completions():
    """Completing a position is not opening one; a cheap complement is
    exactly what a held leg wants."""
    r = d(yes_qty=2, no_qty=0, avg_yes=F(30, 100), offered_side=NO,
          price=F(5, 100), min_leg=F(20, 100))
    assert r["kind"] == PP.COMPLETE and r["action"] == PP.COMPLETE


# -------------------------------------------------------------- completion
def test_completion_compares_the_realized_pair_against_its_ceiling():
    r = d(yes_qty=1, no_qty=0, avg_yes=F(30, 100), offered_side=NO,
          price=F(85, 100))
    assert r["pair_cost"] == F(115, 100) and r["action"] == PP.COMPLETE
    r = d(yes_qty=1, no_qty=0, avg_yes=F(30, 100), offered_side=NO,
          price=F(95, 100))
    assert r["action"] == PP.SKIP


def test_the_held_side_decides_which_offer_reduces():
    assert d(yes_qty=2, no_qty=0, avg_yes=F(3, 10), offered_side=NO,
             price=F(5, 10))["kind"] == PP.COMPLETE
    assert d(yes_qty=2, no_qty=0, avg_yes=F(3, 10), offered_side=YES,
             price=F(5, 10))["kind"] == PP.ACQUIRE
    assert d(yes_qty=0, no_qty=2, avg_no=F(3, 10), offered_side=YES,
             price=F(5, 10))["kind"] == PP.COMPLETE


def test_a_balanced_book_has_nothing_to_complete():
    assert d(yes_qty=2, no_qty=2, avg_yes=F(3, 10), avg_no=F(3, 10),
             offered_side=NO, price=F(5, 10))["kind"] == PP.ACQUIRE


# -------------------------------------------------------------- the deadline
def test_the_late_relaxation_can_only_raise_the_ceiling():
    kw = dict(yes_qty=1, no_qty=0, avg_yes=F(30, 100), offered_side=NO,
              price=F(95, 100))
    assert d(**kw)["action"] == PP.SKIP
    assert d(late=True, comp_late=F(130, 100), **kw)["action"] == PP.COMPLETE
    # a LOWER late ceiling must not tighten the standing one
    r = d(late=True, comp_late=F(50, 100), yes_qty=1, no_qty=0,
          avg_yes=F(30, 100), offered_side=NO, price=F(85, 100))
    assert r["ceiling"] == COMP and r["action"] == PP.COMPLETE


def test_the_relaxation_does_nothing_before_the_deadline():
    r = d(late=False, comp_late=F(130, 100), yes_qty=1, no_qty=0,
          avg_yes=F(30, 100), offered_side=NO, price=F(95, 100))
    assert r["action"] == PP.SKIP


# -------------------------------------------------------------- the premise
def test_the_module_contains_no_probability_model_and_no_rebate():
    """P8-A's whole claim is that this rule needs neither. If it grows an
    import of ev_policy or a rebate term, the claim is no longer true."""
    path = "src/gemini_execution/pair_policy.py"
    tree = ast.parse(open(path).read())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add((n.module or "").split(".")[0])
    assert mods <= {"__future__", "fractions", "typing"}, mods
    # CODE ONLY: prose may discuss rebates and outcomes, code may not read
    # them. Comments and strings are stripped before the check.
    import io, tokenize
    code = "".join(
        tok.string.lower() for tok in
        tokenize.generate_tokens(io.StringIO(open(path).read()).readline)
        if tok.type not in (tokenize.COMMENT, tokenize.STRING))
    for bad in ("rebate", "reward", "p_win", "resolution", "settle"):
        assert bad not in code, bad
