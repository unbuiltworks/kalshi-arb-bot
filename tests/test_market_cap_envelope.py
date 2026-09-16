"""ONE authoritative per-market envelope, and the acceptance probe is in it.

OWNER 2026-09-16 P0. `prove_complement_when_flat` placed a real, money-moving
BUY that `spring_cumulative_notional` did not count -- the stage allow-list was
written for R5F, where $0.01 against a $7.50 envelope was noise. At R1's $0.05
the same order is 20% of the envelope, so "max $0.05 per market" was not true:
worst case was $0.06.

The fix is accounting and risk-bound only. The probe still runs, still joins
the bid, is still post-only at the venue minimum, and is still not an L5
decision. What changed is that its dollars are inside the one counter, tested
BEFORE submission, and reconciled from the venue's own fill.

These six tests are the owner's acceptance list, one per numbered item.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal as D

import pytest

#: This whole file is ABOUT the concrete per-market envelope, so the suite's
#: permissive stand-in mandate must never be what decides its assertions.
pytestmark = pytest.mark.real_mandate_env

from src import pmus_spring_l5_session as base


SLUG = "aec-atp-kenmiy-phisek-2026-09-15"


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """A real durable ledger on disk -- the counter reads a file, so a stub
    that cannot disagree with the writer would prove nothing."""
    monkeypatch.setenv("PMUS_Q1_STATE_DIR", str(tmp_path))
    path = tmp_path / "ledger.jsonl"

    def append(row):
        with open(path, "a", encoding="utf-8") as sink:
            sink.write(json.dumps(row, separators=(",", ":"),
                                  default=str) + "\n")
    monkeypatch.setattr(base.q1, "ledger_append", append)
    return path


def _submit(ledger, stage, oid, price, qty):
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "submitted", "slug": SLUG,
                             "order_id": oid, "stage": stage,
                             "price": str(price), "qty": str(qty)}) + "\n")


def _terminal(ledger, oid, price, filled):
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "order_terminal", "slug": SLUG,
                             "order_id": oid, "price": str(price),
                             "filled_qty": str(filled)}) + "\n")


# --------------------------------------------------------------------------
# the membership rule itself

def test_every_money_moving_buy_stage_is_in_the_counter():
    """RESTATED 2026-09-16, the second correction this file has needed.

    This test used to assert that `spring_complete_*` returns False, with the
    comment "a COMPLETE reduces the position it completes; it is bounded by the
    <=1.00 pair invariant, not by the surplus-spend envelope". I wrote that
    while building the P0 envelope correction, and it is the bug in test form:
    the exemption it pinned is what let aec-itfme-conbra-andpet spend $19.3295
    against a counter that read $7.4476.

    Both halves of the reasoning were wrong. A COMPLETE is a BUY of the
    complement -- cash leaves the account. And `<= 1.00` bounds the PRICE PER
    PAIR, never the DOLLAR VOLUME: 20.44 pairs at a pair cost of 0.9457 honour
    that invariant at every step and cost nineteen dollars.

    The test's NAME was already right -- "every money-moving BUY stage". Only
    the answer was wrong, so the answer is what changed.
    """
    for stage in ("spring_open_1", "spring_extend_2", "micro_first_leg",
                  base.FLAT_PROBE_STAGE,
                  "spring_complete_1", "spring_complete_4_retry1",
                  "a_stage_nobody_has_invented_yet", "", None):
        assert base.counts_against_market_cap(stage) is True, stage


# --------------------------------------------------------------------------
# 1. acceptance order + later session fills can never exceed $0.05

def test_1_acceptance_plus_session_fills_can_never_exceed_the_cap(ledger):
    cap = base.session_notional_cap()
    assert cap == D("0.05")

    # the acceptance probe fills at the venue minimum
    _submit(ledger, base.FLAT_PROBE_STAGE, "ACC", "0.39", "0.01")
    _terminal(ledger, "ACC", "0.39", "0.01")
    spent = base.spring_cumulative_notional(SLUG)
    assert spent == D("0.0039")

    # session legs spend the rest, and the counter carries the probe
    _submit(ledger, "spring_open_1", "O1", "0.43", "0.10")
    _terminal(ledger, "O1", "0.43", "0.10")
    spent = base.spring_cumulative_notional(SLUG)
    assert spent == D("0.0039") + D("0.043") == D("0.0469")

    # the next leg that would breach is REFUSED before submission
    assert base.cap_admits(SLUG, D("0.01"))["admits"] is False
    # and one that exactly reaches the cap is admitted -- <=, not <
    assert base.cap_admits(SLUG, cap - spent)["admits"] is True
    assert base.cap_admits(SLUG, cap - spent + D("0.0001"))["admits"] is False


def test_1b_the_probe_refuses_to_submit_when_the_cap_is_already_spent(
        ledger, monkeypatch):
    """Not just arithmetic: the probe must not place the order."""
    _submit(ledger, "spring_open_1", "O1", "0.50", "0.10")
    _terminal(ledger, "O1", "0.50", "0.10")           # $0.05, the whole cap
    assert base.spring_cumulative_notional(SLUG) == D("0.05")

    placed = []
    monkeypatch.setattr(base.q1, "outcome_bbo",
                        lambda t, s, side: {"bid": D("0.39"), "ask": D("0.42")})
    monkeypatch.setattr(base.micro, "market_micro_spec",
                        lambda t, s, e: {"minimum_trade_qty": D("0.01"),
                                         "tick": D("0.01")})
    monkeypatch.setattr(base.micro, "place_limit_buy",
                        lambda *a, **k: placed.append(a) or "OID")

    out = base.prove_complement_when_flat(None, SLUG, clock=lambda: 0.0,
                                          sleep=lambda _s: None,
                                          wall=lambda: 100.0)
    assert placed == [], "the probe submitted despite an exhausted envelope"
    assert "market_cap_exhausted" in out["skipped"]
    assert base.spring_cumulative_notional(SLUG) == D("0.05")


# --------------------------------------------------------------------------
# 2. a partial acceptance fill reduces remaining budget correctly

def test_2_a_partial_acceptance_fill_returns_the_remainder_to_the_budget(
        ledger):
    _submit(ledger, base.FLAT_PROBE_STAGE, "ACC", "0.40", "0.10")
    # before the venue answers, the whole submission counts -- fail closed
    assert base.spring_cumulative_notional(SLUG) == D("0.04")
    assert base.cap_admits(SLUG, D("0.02"))["admits"] is False

    _terminal(ledger, "ACC", "0.40", "0.025")          # partial
    assert base.spring_cumulative_notional(SLUG) == D("0.01")
    g = base.cap_admits(SLUG, D("0.02"))
    assert g["admits"] is True
    # compare the VALUES: Decimal keeps its exponent, so "0.01000" and "0.010"
    # are the same number and pinning the string would be pinning formatting
    assert D(g["already_spent"]) == D("0.01")
    assert D(g["remaining_before"]) == D("0.04")
    assert D(g["remaining_after"]) == D("0.02")


def test_2b_a_zero_fill_acceptance_returns_the_whole_budget(ledger):
    _submit(ledger, base.FLAT_PROBE_STAGE, "ACC", "0.39", "0.01")
    _terminal(ledger, "ACC", "0.39", "0")
    assert base.spring_cumulative_notional(SLUG) == D("0")
    assert base.cap_admits(SLUG, D("0.05"))["admits"] is True


# --------------------------------------------------------------------------
# 3. a fully filled acceptance order stays in the final market accounting

def test_3_a_filled_acceptance_order_is_visible_to_the_quantity_reconciler(
        ledger, monkeypatch):
    """ledger_qty_total() is what the venue-vs-ledger check and the settlement
    repair assert on, and it counts `filled` rows. The old bespoke
    `order_filled` kind made a real position invisible to both."""
    # NOT monkeypatched: the counter reads pmus_q1_executor.state_dir() and the
    # auditor reads pmus_q1_executor_v4.state_dir(). Different modules, both
    # driven by PMUS_Q1_STATE_DIR -- patching one to the other would hide a
    # divergence between them, which is the kind of thing this test exists for.
    from src import pmus_settlement_auditor as auditor
    assert auditor.q1.state_dir() == base.q1.base.state_dir()

    monkeypatch.setattr(base.q1, "outcome_bbo",
                        lambda t, s, side: {"bid": D("0.39"), "ask": D("0.42")})
    monkeypatch.setattr(base.micro, "market_micro_spec",
                        lambda t, s, e: {"minimum_trade_qty": D("0.01"),
                                         "tick": D("0.01")})
    monkeypatch.setattr(base.micro, "place_limit_buy",
                        lambda *a, **k: "ACCEPT1")
    monkeypatch.setattr(base.q1, "_order_status", lambda t, o: {"state": "OPEN"})
    monkeypatch.setattr(base.micro, "authoritative_fill",
                        lambda o, q_: {"qty": D("0.01"), "price": D("0.39")})
    monkeypatch.setattr(base, "complement_proof_path",
                        lambda: ledger.parent / "proof.json")

    proof = base.prove_complement_when_flat(None, SLUG, clock=lambda: 0.0,
                                            sleep=lambda _s: None,
                                            wall=lambda: 100.0)
    assert proof["filled_qty"] == "0.01"

    rows = [json.loads(x) for x in ledger.read_text().splitlines()]
    kinds = {r["kind"] for r in rows}
    assert {"submitted", "order_terminal", "filled"} <= kinds
    assert "order_filled" not in kinds

    assert auditor.ledger_qty_total(SLUG) == "0.01"
    # and the same fill is inside the envelope, exactly once
    assert base.spring_cumulative_notional(SLUG) == D("0.0039")
    assert D(proof["cap_after"]) == D("0.0039")


# --------------------------------------------------------------------------
# 4. restart cannot reset the cumulative counter

def test_4_the_counter_survives_a_restart_because_it_is_read_from_disk(
        ledger, monkeypatch):
    _submit(ledger, base.FLAT_PROBE_STAGE, "ACC", "0.39", "0.01")
    _terminal(ledger, "ACC", "0.39", "0.01")
    _submit(ledger, "spring_open_1", "O1", "0.43", "0.09")
    _terminal(ledger, "O1", "0.43", "0.09")
    before = base.spring_cumulative_notional(SLUG)
    assert before == D("0.0426")

    # a restart is a fresh import with fresh process state; the only thing
    # that persists is the volume, which is what the counter reads
    import importlib
    reloaded = importlib.reload(base)
    try:
        assert reloaded.spring_cumulative_notional(SLUG) == before
        assert reloaded.cap_admits(SLUG, D("0.01"))["admits"] is False
    finally:
        importlib.reload(base)


def test_4b_an_unreconciled_submission_survives_restart_at_full_notional(
        ledger):
    """A crash between submit and terminal must not return the budget."""
    _submit(ledger, base.FLAT_PROBE_STAGE, "ACC", "0.50", "0.09")
    assert base.spring_cumulative_notional(SLUG) == D("0.045")
    assert base.cap_admits(SLUG, D("0.01"))["admits"] is False


# --------------------------------------------------------------------------
# 5. settlement / reconciliation includes the acceptance position

def test_5_the_acceptance_submission_is_reconcilable_like_any_other(ledger,
                                                                    monkeypatch):
    """A crashed probe leaves a submission with no terminal row, which counts
    at FULL notional forever. reconcile_submissions must be able to close it,
    or one crash silently retires the market."""
    _submit(ledger, base.FLAT_PROBE_STAGE, "ACC", "0.50", "0.09")
    assert base.spring_cumulative_notional(SLUG) == D("0.045")

    monkeypatch.setattr(base.q1, "_order_status",
                        lambda t, o: {"state": "ORDER_STATE_CANCELED",
                                      "cumQuantity": "0"})
    monkeypatch.setattr(base.micro, "authoritative_fill",
                        lambda o, q_, accept_partial=False: {"qty": D("0"),
                                                             "price": D("0.50")})
    out = base.reconcile_submissions(None, SLUG)
    assert out["checked"] == 1 and out["reconciled"] == 1
    assert base.spring_cumulative_notional(SLUG) == D("0")


def test_5b_the_acceptance_fill_never_enters_the_l5_behavioural_inventory(
        ledger):
    """The boundary, stated as a test so it cannot drift by accident. The
    probe's DOLLARS are inside the risk envelope; its POSITION is deliberately
    not an L5 decision, so held_inventory_from_ledger keys on `spring_` and
    feeding the probe into the state lookup would change what the model does.
    That would be a strategy change, which this correction is not."""
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "filled", "slug": SLUG,
                             "order_id": "ACC",
                             "stage": base.FLAT_PROBE_STAGE,
                             "side": "OUTCOME_SIDE_YES", "price": "0.39",
                             "qty": "0.01", "l5_action": False}) + "\n")
    inv = base.held_inventory_from_ledger(SLUG)
    assert inv.up == 0 and inv.dn == 0
    # but the dollars ARE counted
    _submit(ledger, base.FLAT_PROBE_STAGE, "ACC", "0.39", "0.01")
    _terminal(ledger, "ACC", "0.39", "0.01")
    assert base.spring_cumulative_notional(SLUG) == D("0.0039")


# --------------------------------------------------------------------------
# 6. the one-market stop is unchanged

def test_6_one_market_per_campaign_is_untouched_by_this_correction():
    from src import pmus_l5_ramp_ladder as ladder
    r1 = ladder.rung("R1")
    assert r1["per_market_cap_usd"] == "0.05"
    assert r1["concurrency"] == 1
    # the campaign's market allowance is the image ENV, not anything here
    src = open(base.__file__, encoding="utf-8").read()
    assert "PMUS_SPRING_RUNG_MARKETS" not in src
    for name in ("counts_against_market_cap", "cap_admits"):
        body = src.split(f"def {name}")[1].split("\ndef ")[0]
        for forbidden in ("markets_allowed", "concurrency", "rung_markets"):
            assert forbidden not in body, (name, forbidden)


def test_6b_the_correction_adds_no_order_verb_and_no_new_size(ledger):
    """It is an accounting change. It must not be able to place, size or
    price anything."""
    src = open(base.__file__, encoding="utf-8").read()
    for name in ("counts_against_market_cap", "cap_admits"):
        body = src.split(f"def {name}")[1].split("\ndef ")[0]
        # DOCSTRINGS ARE PROSE, NOT AN ORDER PATH. The scan is for executable
        # verbs; `counts_against_market_cap` now explains in its docstring why
        # every *submitted* order counts, and that sentence is not a call.
        code = re.sub(r'"""(?:.|\n)*?"""', "", body)
        for verb in ("place_limit_buy", "place_order", "submit", "cancel",
                     "post_only", "tick", "qty ="):
            assert verb not in code, (name, verb)
