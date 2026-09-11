"""The R5FT canary approval row: owner's words, and the rung it names.

An approval is the only thing that starts a money campaign, so the row is
tested like code. It must quote the owner rather than paraphrase him, must
name a rung that actually exists with the caps the row claims, and must
carry the two things this particular decision is contested on: that R5FT
is R5F honouring its own 2026-09-07 approval text, and that CLAUDE.md
separately locks near-$1 pair tolerance.
"""
from src import pmus_l5_continuous_session as cont
from src import pmus_l5_ramp_ladder as ladder

CANARY = "R5FT-CANARY-2026-09-11"


def _row():
    return next(a for a in cont.load_campaign_approvals()
                if a["campaign_id"] == CANARY)


def test_the_row_exists_and_names_the_canary_rung():
    r = _row()
    assert r["rung"] == "R5FT"
    assert r["approved_by"] == "owner"
    assert r["stop_rule"] == "rung_markets"


def test_it_quotes_the_owner_rather_than_paraphrasing_him():
    """'go 3' on its own is not an auditable authorisation -- the row has
    to carry what option 3 SAID, because that is what he agreed to."""
    src = _row()["source"]
    assert "'go 3'" in src
    assert "Go live now with a q=1 canary at a relaxed target" in src
    assert "Join the touch, hard cap <=1.00" in src
    # the constraints he set the same day and never revoked
    assert "Keep the daily stop at -$15" in src
    assert "do not arm without approval" in src


def test_the_shell_label_is_the_rung_the_ladder_actually_builds():
    """A row claiming caps the ladder does not implement would authorise
    something nobody has read."""
    assert _row()["shell"] == ladder.shell_version(ladder.rung("R5FT"))


def test_the_ceiling_is_stated_at_par_and_par_is_a_floor():
    r5ft, r5f = ladder.rung("R5FT"), ladder.rung("R5F")
    assert ladder.pair_cost_max(r5ft) == ladder.D("1.00")
    assert ladder.pair_cost_max(r5f) == ladder.D("0.98")   # R5F untouched
    assert str(r5ft["pair_cost_max"]) == "1.00"            # STATED, not inherited
    assert ladder.pair_cost_max({"pair_cost_max": "1.25"}) == ladder.D("1.00")


def test_the_row_records_that_r5f_has_never_run_as_approved():
    """The finding that motivates the rung, on the row rather than only in
    a commit message: R5F carries pair_cost_max None and inherits 0.98
    while its own approval says pricing is not behaviorally gated."""
    d = _row()["rung_derivation"]
    assert "not behaviorally gated" in d
    assert "DEFAULT_PAIR_COST_MAX" in d
    assert "never run as approved" in d


def test_the_row_states_the_unresolved_claude_md_lock():
    """Two readings -- a deliberate incentive-era lock versus an
    unintended default -- and the row must not pretend to have settled
    which. If this disappears, the canary reads as licensed policy."""
    d = _row()["rung_derivation"]
    assert "CLAUDE.md" in d
    assert "0.0134" in d          # our measured credit, not an assumed rebate
    assert "not settle" in d.lower() or "does not settle" in d.lower()


def test_the_measured_evidence_is_on_the_row_not_just_the_conclusion():
    d = _row()["rung_derivation"]
    for fact in ("2,382", "2,215", "0.74%", "47.6%", "0.9876", "37.2%"):
        assert fact in d, fact


def test_one_market_and_the_remaining_loss_budget_are_stated():
    r = _row()
    assert ladder.markets_per_campaign(ladder.rung("R5FT")) == 1
    assert "ONE MARKET" in r["stop_rule_reading"]
    assert "-$15.00" in r["stop_rule_reading"]
    assert ladder.daily_loss_stop(ladder.rung("R5FT")) == ladder.D("-15.00")
    assert "-$9.3736" in r["stop_rule_reading"]   # the day already spent
    assert "$3.00" in r["stop_rule_reading"]


def test_the_preflight_is_not_waived_by_the_row():
    """An approval may say what a campaign is; it may never say that the
    venue checks can be skipped."""
    joined = " ".join(_row()["preconditions"])
    assert "continuation_preflight" in joined
    assert "none of these is waived by this row" in joined


def test_it_is_not_a_promotion_of_r5f():
    n = _row()["not"]
    assert "not a promotion of R5F" in n
    assert "R5F, which stays off" in n
    assert "no automatic repeat" in n


def test_it_is_the_next_unconsumed_continuation_after_r5f():
    prev = ["R1-INITIAL", "R1-EVIDENCE-CONTINUATION-2026-09-06",
            "R2C-RUN-2026-09-06", "R2C2-RUN-2026-09-06", "R3L-RUN-2026-09-06",
            "R4-RUN-2026-09-07"]
    nxt = cont.approved_continuation("R5F-RUN-2026-09-07", prev)
    assert nxt["campaign_id"] == CANARY and nxt["rung"] == "R5FT"


def test_a_consumed_canary_is_never_re_run():
    """Consumption is by id, so a redeploy after the canary ran must find
    nothing -- no automatic second bounded session."""
    prev = ["R1-INITIAL", "R1-EVIDENCE-CONTINUATION-2026-09-06",
            "R2C-RUN-2026-09-06", "R2C2-RUN-2026-09-06", "R3L-RUN-2026-09-06",
            "R4-RUN-2026-09-07", "R5F-RUN-2026-09-07"]
    assert cont.approved_continuation(CANARY, prev) is None
