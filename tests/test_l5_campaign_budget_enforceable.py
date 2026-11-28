"""The $5 CAMPAIGN budget binds independently of historical attribution.

This is a DIFFERENT mechanism from the $5 daily halt
(`test_l5_daily_loss_halt_enforceable`). The daily halt reads
`realized_current_utc_day()`; the campaign budget reads a CAMPAIGN-SCOPED
additional loss measured from an owner-gated baseline.

THE DEFECT THIS GATE ALREADY CARRIES A FIX FOR, dated 2026-11-04 in its own
source: the used term was `max(0, -cumulative_realized)`, the account's
LIFETIME realized loss. Against a $5 budget and a standing account loss of
$10.62 the budget was spent before a single order existed -- every window
refused `CAMPAIGN_WORST_CASE_EXHAUSTED:11.3369>=5` and an authorized
four-window campaign submitted nothing. So the gate now measures only loss
this campaign caused.

FOUR FAIL-CLOSED REFUSALS, none of which can be satisfied by missing data:
  CAMPAIGN_IDENTITY_UNAVAILABLE   - no establishable approval identity
  CAMPAIGN_BASELINE_UNAVAILABLE   - a budget configured with no basis
  CAMPAIGN_BASELINE_UNKNOWN       - the baseline cannot answer
  CAMPAIGN_BUDGET_ERROR:<type>    - the budget raised
"""
import inspect
import io

from src import l5_live_validation_gates as VG


def _gates_src() -> str:
    return io.open("src/l5_live_validation_gates.py", encoding="utf-8").read()


def _refuse_src() -> str:
    for name in ("refuse", "refuse_amount", "refuse_arming"):
        fn = getattr(VG.ValidationGates, name, None)
        if fn is not None:
            src = inspect.getsource(fn)
            if "campaign_budget" in src:
                return src
    raise AssertionError("no ValidationGates method consults campaign_budget")


# ------------------------------------------- the four fail-closed refusals

def test_a_missing_campaign_identity_refuses_rather_than_skipping():
    src = _refuse_src()
    assert "if not self.campaign_id:" in src
    assert 'return "CAMPAIGN_IDENTITY_UNAVAILABLE"' in src
    assert "A MISSING CAMPAIGN ID IS NOT PERMISSION TO SKIP THE BUDGET" in src


def test_an_unestablished_baseline_refuses_and_never_falls_back():
    src = _refuse_src()
    assert "if self.campaign_baseline is None:" in src
    assert 'return "CAMPAIGN_BASELINE_UNAVAILABLE"' in src
    # the comment wraps, so match a fragment inside one line
    assert "silent fall back to the lifetime figure" in src


def test_a_baseline_that_cannot_answer_refuses():
    src = _refuse_src()
    assert 'CAMPAIGN_BASELINE_UNKNOWN' in src
    assert 'if not _cs.get("ok"):' in src


def test_a_budget_that_raises_refuses():
    src = _refuse_src()
    assert 'return "CAMPAIGN_BUDGET_ERROR:%s" % type(exc).__name__' in src
    assert "A budget that cannot answer is a refusal, never a pass" in src


# --------------------------- the measured quantity is campaign-scoped only

def test_the_used_term_is_campaign_scoped_not_lifetime():
    src = _refuse_src()
    assert "additional_loss_usd(" in src
    assert "campaign_used = _d(_cs.get(\"additional_loss_usd\"))" in src
    assert "CAMPAIGN-SCOPED, NOT LIFETIME" in src
    # the lifetime figure is still PASSED, as the baseline's input...
    assert "account_wide_realized=cumulative_realized" in src
    # ...but it is not what the budget is charged
    i = src.index("campaign_used = ")
    j = src.index("refuse_amount(", i)
    assert "cumulative_realized" not in src[i:j]


def test_the_zero_point_is_owner_gated_and_immovable_by_the_executor():
    src = _refuse_src()
    assert "keyed on the approval" in src and "nonce" in src
    assert "this executor cannot write" in src
    assert "idempotent so a restart or re-arm cannot move it" in src


def test_a_gain_creates_no_room():
    src = _refuse_src()
    assert "A GAIN CREATES NO ROOM" in src
    assert "max(0, base - now)" in src


def test_every_lifetime_and_daily_control_still_reads_the_full_figure():
    """Only the campaign's zero point moves; nothing else is relaxed."""
    src = _refuse_src()
    assert "NOTHING IS WRITTEN DOWN" in src
    assert "every independent account-wide" in src
    assert "lifetime and daily control still reads it unchanged" in src


# --------------------------------- the reporting field no gate is allowed to read

def test_incremental_room_is_reporting_only():
    """`incremental_room_usd` IS budget minus lifetime loss -- and reads 0
    today. If a gate read it, no campaign could ever arm. Nothing does.

    It lives in l5_campaign_budget's telemetry, not in the gates module.
    """
    bud = io.open("src/l5_campaign_budget.py", encoding="utf-8").read()
    i = bud.index("incremental_room_basis")
    win = bud[i:i + 300]
    # the literal is split across source lines, so match the fragments
    assert "Reporting only; " in win
    assert "no gate reads it." in win
    assert "budget_usd - account-wide realized LOSS" in win
    # absent from the refusal path...
    assert "incremental_room_usd" not in _refuse_src()
    # ...and its one mention in the gates module is inside the DOCSTRING of
    # as_dict, a reporting method -- never in code.
    g = _gates_src()
    assert g.count("incremental_room_usd") == 1
    doc = inspect.getdoc(VG.ValidationGates.as_dict) or ""
    assert "incremental_room_usd" in doc
