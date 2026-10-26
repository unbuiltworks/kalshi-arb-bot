"""NEW_OPEN vs COMPLEMENT must not depend on `legs_claimed`."""
import json
import glob
import sys
from decimal import Decimal as D

sys.path.insert(0, "src")
import l5_decision_role as R


def test_holding_down_buying_up_is_a_complement():
    assert R.decision_role(residual="-1.77", side="UP") == R.COMPLEMENT


def test_holding_down_buying_down_opens_more():
    assert R.decision_role(residual="-1.77", side="DOWN") == R.NEW_OPEN


def test_holding_up_buying_down_is_a_complement():
    assert R.decision_role(residual="2.18", side="DOWN") == R.COMPLEMENT


def test_holding_up_buying_up_opens_more():
    assert R.decision_role(residual="2.18", side="UP") == R.NEW_OPEN


def test_flat_is_its_own_role_not_new_open():
    # a post-pair opening is taken while FLAT, and must stay
    # distinguishable from one taken against live inventory
    assert R.decision_role(residual="0", side="UP") == R.FLAT_OPEN
    assert R.decision_role(residual="0", side="DOWN") == R.FLAT_OPEN


def test_yes_no_are_accepted_as_up_down():
    assert R.decision_role(residual="-1", side="yes") == R.COMPLEMENT
    assert R.decision_role(residual="-1", side="NO") == R.NEW_OPEN


def test_unreadable_inputs_are_unknown_never_a_guess():
    for bad in (None, "", "sideways", 3, True):
        assert R.decision_role(residual="-1", side=bad) == R.UNKNOWN
    for bad in (None, "", "nan", "inf", "abc", True):
        assert R.decision_role(residual=bad, side="UP") == R.UNKNOWN


def test_role_of_decision_never_raises():
    class Hostile:
        def get(self, _k, _d=None):
            raise RuntimeError("boom")
    assert R.role_of_decision(Hostile()) == R.UNKNOWN
    assert R.role_of_decision(None) == R.UNKNOWN
    assert R.role_of_decision(42) == R.UNKNOWN
    assert R.role_of_decision({"residual": "-1", "side": "UP"}) == R.COMPLEMENT


def test_every_role_is_declared():
    for v in (R.NEW_OPEN, R.COMPLEMENT, R.FLAT_OPEN, R.UNKNOWN):
        assert v in R.ROLES


def test_the_preserved_complement_refusals_are_not_identifiable_by_legs_claimed():
    """The defect this module exists for, asserted against real tapes.

    Every COMPLEMENT_PAIR_ABOVE_CAP row is a complement attempt by the
    refusal's own name, yet none of them can be recognised as one from
    `legs_claimed`.
    """
    tapes = glob.glob(
        "analysis/l5/residual_exit/preserved/btc15m_*.ndjson")
    assert tapes, "preserved tapes are the evidence for this test"
    total = 0
    identifiable = 0
    for tp in tapes:
        for ln in open(tp):
            d = json.loads(ln)
            if d.get("label") != "L5_LIVE_DECISION":
                continue
            b = json.loads(d["body"], strict=False)
            if "COMPLEMENT_PAIR_ABOVE_CAP" not in str(b.get("key") or ""):
                continue
            total += 1
            claimed = (b.get("two_sided") or {}).get("legs_claimed") or {}
            if any(bool(v) for v in claimed.values()):
                identifiable += 1
    assert total > 0, "expected complement refusals in the tapes"
    assert identifiable == 0, (
        "legs_claimed identified %d of %d complement attempts; if this now "
        "passes, legs_claimed was fixed and this module may be redundant"
        % (identifiable, total))


def test_the_runner_emits_decision_role_on_every_decision_row():
    """Wired at the emit, not after translation.

    The point of the field is to be present on the EARLY refusals, so this
    pins it to the log site rather than to `inventory_class`'s note, which
    runs only once a decision has been translated.
    """
    src = open("src/l5_pmus_live_runner.py").read()
    assert "import l5_decision_role as DROLE" in src
    # the emitted line carries it
    emit_at = src.index('emit("L5_LIVE_DECISION"')
    emit_block = src[emit_at:emit_at + 2600]
    assert '"decision_role": _role' in emit_block, \
        "the emitted L5_LIVE_DECISION line must carry decision_role"
    # and so does the durable tape doc
    assert '"decision_role": _role}' in src

    # it must be computed fail-open: a raising store cannot break the log
    role_at = src.index("_role = DROLE.decision_role")
    guard = src[role_at - 400:role_at + 400]
    assert "except BaseException" in guard
    assert "DROLE.UNKNOWN" in guard


def test_decision_role_is_not_consulted_by_any_gate():
    """Reporting only. If a gate ever reads it, this test is the alarm."""
    import ast
    src = open("src/l5_pmus_live_runner.py").read()
    tree = ast.parse(src)
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = None
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            if f.value.id == "DROLE":
                name = f.attr
        if name:
            calls.append(name)
    # exactly one call site, and it is the reporting one
    assert calls == ["decision_role"], calls
    # the value is never compared against a role constant anywhere, which is
    # what branching on it would look like
    for bad in ("_role ==", "_role !=", "_role in ", "if _role"):
        assert bad not in src, "decision_role appears to drive control flow: %r" % bad
