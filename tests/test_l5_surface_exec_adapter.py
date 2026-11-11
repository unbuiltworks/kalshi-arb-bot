"""The surface execution adapter: it proposes, and it refuses by default."""
import io
import json
import os
import subprocess
import sys
from decimal import Decimal as D

import pytest

from src import pmus_q1_executor as X
from src.l5_surface import engine as E
from src.l5_surface_exec import adapter as A

SLUG = "btc-updown-15m-1789674300"
BBO = {"bid": 0.50, "ask": 0.51}


def _rung(outcome="Up", px="0.50", qty="100", idx=0):
    return E.Rung(outcome=outcome, side="BUY", rung_index=idx,
                  ticks_behind_bid=0, max_acceptable_price=px,
                  desired_qty=qty, role="BASE")


def _limits(**over):
    kw = dict(max_order_usd=D("0.60"), max_market_usd=D("5.00"),
              max_abs_residual=D("3.00"), max_total_usd=D("15.00"),
              max_concurrent_markets=1, daily_loss_stop_usd=D("6.00"))
    kw.update(over)
    return A.Limits(**kw)


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv(X.STATE_ENV, str(tmp_path))
    A.Kill.clear()
    yield
    A.Kill.clear()


# ------------------------------------ the shadow's closure must stay intact

def test_the_shadow_still_imports_no_order_path():
    """This adapter imports the order path ON PURPOSE. The shadow must not,
    and adding the adapter must not have changed that."""
    code = ("import sys, src.l5_surface.shadow;"
            "print(' '.join(sorted(m for m in sys.modules "
            "if m.startswith('src.'))))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-400:]
    mods = out.stdout.split()
    assert "src.pmus_q1_executor" not in mods
    assert "src.l5_surface_exec.adapter" not in mods


def test_the_adapter_carries_no_r1_controller_logic():
    """Code tokens, not prose: the docstring PROMISES there is no stochastic
    draw here, and a substring scan would fail on the sentence making the
    promise."""
    import ast
    tree = ast.parse(io.open("src/l5_surface_exec/adapter.py").read())
    docs = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                          ast.ClassDef)):
            d = ast.get_docstring(n, clean=False)
            if d:
                docs.add(d)
    toks = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            toks.add(n.id)
        elif isinstance(n, ast.Attribute):
            toks.add(n.attr)
        elif isinstance(n, (ast.FunctionDef, ast.ClassDef)):
            toks.add(n.name)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            if n.value not in docs:
                toks.add(n.value)
    blob = " ".join(t.lower() for t in toks)
    for banned in ("action_selection", "stochastic", "run_campaign",
                   "choose_action", "sample_action"):
        assert banned not in blob, banned
    # and it must not import the controller entry point
    assert "run_campaign" not in {n.attr for n in ast.walk(tree)
                                  if isinstance(n, ast.Attribute)}


# -------------------------------------------- 1/2 authenticated and passive

def test_an_unreadable_bbo_is_a_refusal_not_a_pass():
    def boom(t, slug):
        raise RuntimeError("gateway 502")
    r = A.bbo_check(None, SLUG, "Up", D("0.50"), fresh=boom)
    assert r["ok"] is False and r["refusal"] == "BBO_UNREADABLE"


def test_a_stale_bbo_is_refused():
    ticks = iter([100.0, 100.0 + A.MAX_BBO_AGE_S + 1])
    r = A.bbo_check(None, SLUG, "Up", D("0.50"),
                    fresh=lambda t, s: BBO, clock=lambda: next(ticks))
    assert r["ok"] is False and r["refusal"] == "BBO_STALE"


def test_a_one_sided_bbo_is_refused():
    r = A.bbo_check(None, SLUG, "Up", D("0.50"),
                    fresh=lambda t, s: {"bid": 0.5, "ask": None})
    assert r["ok"] is False and r["refusal"] == "BBO_NOT_TWO_SIDED"


def test_a_crossing_price_is_refused_on_both_outcomes():
    up = A.bbo_check(None, SLUG, "Up", D("0.51"), fresh=lambda t, s: BBO)
    assert up["ok"] is False and up["refusal"] == "WOULD_CROSS"
    # BUY NO at p crosses when p >= 1 - YES bid = 0.50
    dn = A.bbo_check(None, SLUG, "Down", D("0.50"), fresh=lambda t, s: BBO)
    assert dn["ok"] is False and dn["refusal"] == "WOULD_CROSS"


def test_a_passive_price_passes_and_logs_what_was_seen():
    r = A.bbo_check(None, SLUG, "Up", D("0.50"), fresh=lambda t, s: BBO)
    assert r["ok"] is True and r["post_only"] is True
    assert r["post_only_field"] == "participateDontInitiate"
    assert r["outcome_side"] == "OUTCOME_SIDE_YES"
    assert r["desired_price"] == "0.50" and r["wire_price"] == "0.50"
    assert r["bbo_age_s"] is not None and "authenticated" in r["source"]


def test_a_no_side_order_is_wired_as_one_minus_the_price():
    """The venue takes the LONG-side price. Sending the NO price itself would
    place a different order at a different level."""
    assert A.wire_price("Down", D("0.49")) == D("0.51")
    assert A.wire_price("Up", D("0.49")) == D("0.49")
    r = A.bbo_check(None, SLUG, "Down", D("0.40"), fresh=lambda t, s: BBO)
    assert r["wire_price"] == "0.60" and r["ok"] is True


# ------------------------------------------------ 6 full durable inventory

OLD_SESSION = [
    {"kind": "fill", "slug": SLUG, "side": "OUTCOME_SIDE_YES",
     "qty": "0.40", "price": "0.49"},
    {"kind": "fill", "slug": SLUG, "side": "OUTCOME_SIDE_NO",
     "qty": "0.59", "price": "0.41"},
]


def test_inventory_includes_previous_sessions():
    inv = A.durable_inventory(SLUG, ledger=OLD_SESSION)
    assert inv["up"] == "0.40" and inv["dn"] == "0.59"
    assert inv["residual"] == "-0.19"
    assert inv["fill_rows"] == 2


def test_simulated_rows_never_enter_the_real_position():
    led = OLD_SESSION + [{"kind": "SIM:fill", "slug": SLUG, "simulated": True,
                          "side": "OUTCOME_SIDE_YES", "qty": "999",
                          "price": "0.5"}]
    inv = A.durable_inventory(SLUG, ledger=led)
    assert inv["up"] == "0.40"
    assert inv["simulated_rows_excluded"] == 1


def test_the_adversarial_case_old_residual_plus_a_new_quote(monkeypatch):
    """OWNER-specified: a residual carried in from a previous session, plus a
    new proposed quote, must be judged on the POST-ORDER state. Judging only
    the current state permits an order that breaches the moment it fills."""
    limits = _limits(max_abs_residual=D("1.00"))
    inv = A.durable_inventory(SLUG, ledger=OLD_SESSION)
    assert abs(D(inv["residual"])) == D("0.19")        # currently inside
    ps = A.proposed_state(inv, "Up", D("1"), D("0.50"))
    assert D(ps["residual"]) == D("0.81")              # still inside
    v = A.risk_verdict(ps, limits, total_usd=D("0"), concurrent=1,
                       realized_today=D("0"))
    assert v["ok"] is True

    # now make the same order breach only AFTER it fills
    tight = _limits(max_abs_residual=D("0.50"))
    v2 = A.risk_verdict(ps, tight, total_usd=D("0"), concurrent=1,
                        realized_today=D("0"))
    assert v2["ok"] is False
    assert any("residual" in f for f in v2["fails"])
    # and the CURRENT state alone would have passed, which is the trap
    cur = {"residual": inv["residual"], "spend_usd": inv["spend_usd"],
           "order_usd": "0"}
    assert A.risk_verdict(cur, tight, total_usd=D("0"), concurrent=1,
                          realized_today=D("0"))["ok"] is True


def test_every_bound_is_reported_not_just_the_first():
    ps = A.proposed_state({"up": "0", "dn": "0", "spend_usd": "99"},
                          "Up", D("100"), D("0.9"))
    v = A.risk_verdict(ps, _limits(), total_usd=D("99"), concurrent=9,
                       realized_today=D("-99"))
    assert v["ok"] is False and len(v["fails"]) >= 5


def test_the_daily_loss_stop_blocks_at_the_boundary():
    ps = A.proposed_state({"up": "0", "dn": "0", "spend_usd": "0"}, "Up",
                          D("1"), D("0.5"))
    v = A.risk_verdict(ps, _limits(), total_usd=D("0"), concurrent=1,
                       realized_today=D("-6.00"))
    assert v["ok"] is False and any("daily realized" in f for f in v["fails"])


# ---------------------------------------------------- 7 provenance / recon

def test_a_rung_id_is_stable_across_ticks_and_unique_across_rungs():
    a = A.rung_id("sha", "surf", 0, SLUG, "Up", "0.50")
    assert a == A.rung_id("sha", "surf", 0, SLUG, "Up", "0.50")
    for change in (("sha2", "surf", 0, SLUG, "Up", "0.50"),
                   ("sha", "surf2", 0, SLUG, "Up", "0.50"),
                   ("sha", "surf", 1, SLUG, "Up", "0.50"),
                   ("sha", "surf", 0, "other", "Up", "0.50"),
                   ("sha", "surf", 0, SLUG, "Down", "0.50"),
                   ("sha", "surf", 0, SLUG, "Up", "0.49")):
        assert A.rung_id(*change) != a


def test_duplicate_or_unknown_mappings_fail_closed():
    r = A.Reconciler()
    r.bind("rung_a", "o1")
    r.bind("rung_a", "o1")                   # idempotent
    with pytest.raises(A.ReconciliationError):
        r.bind("rung_a", "o2")
    with pytest.raises(A.ReconciliationError):
        r.bind("rung_b", "o1")
    with pytest.raises(A.ReconciliationError):
        r.rung_for("never_seen")
    assert r.healthy() is True


def test_the_provenance_chain_is_complete():
    for link in ("spec_sha256", "surface_id", "rung_index",
                 "authenticated_bbo", "order_intent", "order_id",
                 "authoritative_fill", "durable_inventory", "settlement"):
        assert link in A.PROVENANCE_CHAIN


# --------------------------------------------------- 8 kill / cancel / deadman

def test_a_kill_latches_before_it_cancels_and_blocks_submission():
    called = {}

    def boom(t, slug):
        called["cancelled"] = True
        raise RuntimeError("venue unreachable")
    out = A.kill(None, SLUG, "operator", cancel_all=boom)
    assert out["killed"] and out["cancel_error"]
    assert called["cancelled"]
    # the kill survives the cancel failing: submissions are already blocked
    assert A.Kill.engaged()["reason"] == "operator"


def test_a_kill_makes_a_proposal_refuse():
    A.Kill.engage("test")
    p = A.propose(None, rung=_rung(), slug=SLUG, spec_sha256="sha",
                  surface_id="surf", limits=_limits(), total_usd=D("0"),
                  concurrent=1, ledger=[], realized=D("0"),
                  bbo_fn=lambda t, s: BBO)
    assert any(x.startswith("KILL_ENGAGED") for x in p.refusals)


def test_an_unreadable_status_leaves_the_order_outstanding():
    """cancel_exact returns True when a status merely lacks FILLED, which
    reads an unreadable status as a cancellation. Here it does not."""
    def bad_status(t, oid, **kw):
        raise RuntimeError("504")
    out = A.cancel_and_confirm(None, "o1", SLUG, cancel=lambda *a, **k: True,
                               status=bad_status)
    assert out["terminal"] is False and out["outstanding"] is True
    assert out["state"] == "STATUS_UNREADABLE"


def test_a_failed_cancel_call_leaves_the_order_outstanding():
    def boom(t, oid, slug, **kw):
        raise RuntimeError("network")
    out = A.cancel_and_confirm(None, "o1", SLUG, cancel=boom)
    assert out["outstanding"] is True and out["state"] == "CANCEL_CALL_FAILED"


def test_a_venue_terminal_state_is_accepted_as_terminal():
    out = A.cancel_and_confirm(None, "o1", SLUG, cancel=lambda *a, **k: True,
                               status=lambda t, oid, **kw: {"status":
                                                            "ORDER_CANCELLED"})
    assert out["terminal"] is True and out["outstanding"] is False


# ------------------------------------------------------------- 9 accounting

def test_simulated_writes_are_namespaced_and_flagged(tmp_path):
    A.record({"kind": "submitted", "slug": SLUG}, simulated=True)
    rows = [json.loads(l) for l in (tmp_path / "ledger.jsonl").open()]
    assert rows[-1]["kind"] == "SIM:submitted"
    assert rows[-1]["simulated"] is True


def test_a_simulated_row_can_never_become_realized_pnl(tmp_path):
    A.record({"kind": "submitted", "slug": SLUG}, simulated=True)
    A.record({"kind": "fill", "slug": SLUG, "qty": "1", "price": "0.5",
              "side": "OUTCOME_SIDE_YES"}, simulated=True)
    assert X.realized_today() == D("0")
    inv = A.durable_inventory(SLUG)
    assert inv["up"] == "0" or inv["up"] == "0.0" or inv["fill_rows"] == 0


def test_this_module_refuses_to_write_a_realized_row_at_all():
    for kind in ("realized", "SIM:realized"):
        with pytest.raises(ValueError):
            A.record({"kind": kind, "realized_usd": "1"}, simulated=False)


def test_the_lifecycle_has_no_shortcuts():
    assert A.LIFECYCLE == ("PROPOSED", "SUBMITTED", "FILLED", "SETTLED",
                           "REALIZED")


# ------------------------------------------------------- the nine-gate itself

def _gate(**over):
    kw = dict(reconciler=A.Reconciler(), mandate="OWNER-TEST", deadman_ok=True,
              limits=_limits(), total_usd=D("0"), concurrent=1, ledger=[],
              realized=D("0"), bbo_fn=lambda t, s: BBO)
    kw.update(over)
    p = A.Proposal(rung_id="r", spec_sha256="sha", surface_id="surf",
                   rung_index=0, slug=SLUG, outcome="Up",
                   outcome_side="OUTCOME_SIDE_YES", desired_price="0.50",
                   wire_price="0.50", desired_qty="100", submit_qty=1)
    return A.gate(None, p, **kw), p


def test_the_gate_is_closed_by_default_in_this_environment():
    g, _ = _gate()
    assert g["all_pass"] is False
    for c in ("armed", "has_credentials", "gate_asserted"):
        assert c in g["failed"], c


def test_every_precondition_can_fail_independently(monkeypatch):
    monkeypatch.setattr(X, "armed", lambda: True)
    monkeypatch.setattr(X, "has_credentials", lambda: True)
    monkeypatch.setattr(X, "gate_asserted", lambda slug: True)
    g, _ = _gate()
    assert g["failed"] == [], g["why"]

    assert "mandate_present" in _gate(mandate=None)[0]["failed"]
    assert "deadman_healthy" in _gate(deadman_ok=False)[0]["failed"]
    bad = _gate(limits=_limits(max_order_usd=D("0.01")))[0]
    assert "inventory_risk_pass" in bad["failed"]
    cross = _gate(bbo_fn=lambda t, s: {"bid": 0.50, "ask": 0.50})[0]
    assert "non_crossing_post_only" in cross["failed"]
    dead = _gate(bbo_fn=lambda t, s: {"bid": None, "ask": None})[0]
    assert "fresh_authenticated_bbo" in dead["failed"]
    r = A.Reconciler()
    r.by_rung["x"] = "o1"                      # deliberately inconsistent
    assert "reconciliation_healthy" in _gate(reconciler=r)[0]["failed"]


def test_submit_raises_rather_than_submitting_when_the_gate_is_closed():
    placed = {}

    def never(*a, **k):
        placed["yes"] = True
        return "SHOULD_NOT_HAPPEN"
    import src.pmus_q1_executor as XX
    orig = XX.place_limit_buy
    XX.place_limit_buy = never
    try:
        _, p = _gate()
        with pytest.raises(A.GateClosed):
            A.submit(None, p, reconciler=A.Reconciler(), mandate="M",
                     deadman_ok=True, limits=_limits(), total_usd=D("0"),
                     concurrent=1, ledger=[], realized=D("0"),
                     bbo_fn=lambda t, s: BBO)
    finally:
        XX.place_limit_buy = orig
    assert "yes" not in placed, "an order was placed through a closed gate"


def test_the_gate_re_reads_the_book_rather_than_trusting_the_proposal():
    """A proposal built seconds ago is not evidence about now."""
    reads = {"n": 0}

    def counting(t, s):
        reads["n"] += 1
        return BBO
    A.propose(None, rung=_rung(), slug=SLUG, spec_sha256="sha",
              surface_id="surf", limits=_limits(), total_usd=D("0"),
              concurrent=1, ledger=[], realized=D("0"), bbo_fn=counting)
    before = reads["n"]
    _gate(bbo_fn=counting)
    assert reads["n"] > before, "the gate reused a cached book"
