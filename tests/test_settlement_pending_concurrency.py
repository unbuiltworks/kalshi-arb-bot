"""OWNER 2026-09-17 P0 -- FIX SETTLED-PENDING FULLY-MATCHED CONCURRENCY DEADLOCK.

THE LIVE FAILURE, reproduced here rather than described. On 2026-09-17 the
stochastic-selector cohort ended CAMPAIGN_STOP:HALT after ONE market. Market A
(`aec-itfme-artkuk-egople-2026-09-17`) went OPEN -> EXTEND -> COMPLETE to
YES 0.34 / NO 0.34, residual exactly 0, every order terminal, venue traded
quantity 0.68 equal to the ledger's. The scheduler released it -- correctly,
there was no work left. The mandate did not, because it saw fills. The campaign
selected market B, admission refused it `max_concurrent_markets_exhausted`, the
session HALTed, and the campaign ended 24 minutes before A actually resolved.

THE INVARIANT UNDER TEST separates economic/risk exposure from settlement/
accounting obligation. A market with no live orders, venue reconciled to
ledger, YES and NO fully matched, residual exactly zero and no other
directional exposure may leave the CONCURRENCY set while remaining in a
separate pending-settlement set. It keeps the durable ledger, keeps counting
toward max_total_markets, keeps its settlement watch, and stays un-realized.

EVERY OTHER STATE FAILS CLOSED, and most of this file is the proof of that:
residual != 0, unmatched inventory, an unresolved order, a venue/ledger
disagreement, an unreadable ledger, missing terminal evidence and unknown
exposure each keep the slot and must still refuse B.

NO LIMIT MOVES. max_markets stays 1 in every test below.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

from src import pmus_live_mandate as lm
from src import pmus_risk_set_audit as rsa

A = "aec-itfme-artkuk-egople-2026-09-17"
B = "aec-itfme-maxreh-alewei-2026-09-17"
AUTH = "OWNER-2026-09-17-CP1-STOCHASTIC-SELECTOR-COHORT"


def _mandate(**kw):
    """The LIVE envelope, unchanged. max_markets is 1 and stays 1."""
    base = dict(
        authorization_id=AUTH, authorized_at="2026-09-17T11:45:17Z",
        service="l5-r1-probe", strategy="pmus-l5-continuous-v1",
        strategy_version="CP1", max_markets=1, max_total_markets=10,
        max_spend_per_market=D("5.00"), max_total_spend=D("15.00"),
        max_residual=D("3.00"), max_daily_realized_loss=D("6.00"),
        expiry="2099-01-01T00:00:00Z")
    base.update(kw)
    return lm.Mandate(**base)


def _row(d, row):
    with (d / "ledger.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _leg(d, slug, oid, side, qty, px, *, terminal=True, filled=True):
    """One money-moving leg, written the way the live engine writes it:
    commit -> link -> order_terminal -> filled."""
    cid = f"{AUTH}:{slug}:{oid}"
    _row(d, {"kind": lm.COMMIT_KIND, "authorization_id": AUTH,
             "commit_id": cid, "slug": slug, "cash_at_risk": str(px * qty),
             "quantity": str(qty)})
    _row(d, {"kind": lm.LINK_KIND, "authorization_id": AUTH,
             "commit_id": cid, "order_id": oid})
    if terminal:
        _row(d, {"kind": "order_terminal", "order_id": oid, "slug": slug,
                 "filled_qty": str(qty if filled else 0)})
    if filled:
        _row(d, {"kind": "filled", "slug": slug, "qty": str(qty),
                 "side": side, "stage": "spring"})


def _market_a_matched(d, *, up=D("0.34"), dn=D("0.34"), attest=True,
                      venue_agrees=True, may_assert_flat=True,
                      attest_up=None, attest_dn=None):
    """A, exactly as the live run left it: OPEN NO, EXTEND NO, COMPLETE YES,
    COMPLETE YES -- 4 fills, 0.34 each side, residual 0, all terminal."""
    _leg(d, A, "OPEN1", "OUTCOME_SIDE_NO", dn / 2, D("0.41"))
    _leg(d, A, "EXTEND2", "OUTCOME_SIDE_NO", dn / 2, D("0.41"))
    _leg(d, A, "COMPLETE3", "OUTCOME_SIDE_YES", up / 2, D("0.57"))
    _leg(d, A, "COMPLETE4", "OUTCOME_SIDE_YES", up / 2, D("0.45"))
    if attest:
        _row(d, {"kind": lm.FLAT_ATTEST_KIND, "slug": A,
                 "authorization_id": AUTH,
                 "up": str(up if attest_up is None else attest_up),
                 "dn": str(dn if attest_dn is None else attest_dn),
                 "may_assert_flat": may_assert_flat,
                 "venue_agrees": venue_agrees,
                 "venue_qty": "0.6800", "ledger_qty": "0.68"})


def _admit_b(d, m=None):
    """The admission that refused in production."""
    return lm.admit({"marketSlug": B, "quantity": "0.17", "price": "0.34",
                     "outcomeSide": "OUTCOME_SIDE_NO",
                     "action": "ORDER_ACTION_BUY"},
                    state_dir=d, mandate=m or _mandate(), slug=B)


# --------------------------------------------------------------------------
# the reproduction

def test_the_live_failure_no_longer_happens(tmp_path):
    """Every clause of the owner's expected outcome, on one ledger."""
    _market_a_matched(tmp_path)
    used = lm.consumption(tmp_path, AUTH)

    assert used["readable"] is True
    # A remains PENDING_SETTLEMENT and is named as such
    assert used["markets_pending_settlement"] == [A]
    assert used["pending_settlement_basis"][A] == "matched_flat_pending_settlement"
    # A still counts as a market touched -- max_total_markets is NOT relaxed
    assert A in used["markets_touched"]
    assert used["n_markets_touched"] == 1
    # A does not consume risk-bearing concurrency
    assert used["markets_open"] == []
    assert used["n_markets_open"] == 0
    # residual is exactly zero and stays visible
    assert D(used["residual_usd"][A]) == D("0")
    # B passes concurrency admission
    v = _admit_b(tmp_path)
    assert v["admits"] is True, v
    assert v["reason"] == "admitted"


def test_no_max_concurrent_markets_exhausted_refusal(tmp_path):
    _market_a_matched(tmp_path)
    assert _admit_b(tmp_path)["reason"] != "max_concurrent_markets_exhausted"


def test_no_held_absent_while_risk_bearing_alarm(tmp_path):
    """A leaves the scheduler's held set; the alarm must not fire for it."""
    _market_a_matched(tmp_path)
    pending = lm.consumption(tmp_path, AUTH)["markets_pending_settlement"]
    alarms = rsa.detect(prev_held=[A], held=[], unsettled=[A],
                        settlement_pending=pending)
    assert alarms == []


def test_a_is_still_carried_in_the_risk_set_and_named_pending(tmp_path):
    """Released from the SLOT, not from the books: still risk-bearing, still
    charged, and now separately legible as a settlement obligation."""
    _market_a_matched(tmp_path)
    pending = lm.consumption(tmp_path, AUTH)["markets_pending_settlement"]
    snap = rsa.snapshot([A], [], settlement_pending=pending)
    assert snap["risk_bearing"] == [A]
    assert snap["settlement_pending"] == [A]
    assert snap["n_settlement_pending"] == 1
    assert snap["charged_not_scheduled"] == [A]


def test_a_is_not_settled_and_not_realized(tmp_path):
    """Releasing the slot must not look like settlement anywhere."""
    _market_a_matched(tmp_path)
    used = lm.consumption(tmp_path, AUTH)
    rows = (tmp_path / "ledger.jsonl").read_text()
    assert '"kind": "settlement"' not in rows and '"kind":"settlement"' not in rows
    assert '"realized"' not in rows
    assert A in used["markets_touched"]          # still on the books


# --------------------------------------------------------------------------
# mutations: each one must put A back in the concurrency set AND refuse B

@pytest.mark.parametrize("mutate,why", [
    ("residual", "residual_not_zero"),
    ("unmatched", "unmatched_inventory"),
    ("live_order", "unresolved_order"),
    ("no_terminal", "missing_terminal_evidence"),
    ("no_attestation", "no_reconciliation_proof"),
    ("venue_disagrees", "venue_ledger_mismatch"),
    ("not_flat", "may_assert_flat_false"),
    ("stale_attestation", "inventory_moved_since_attestation"),
])
def test_each_unsafe_state_keeps_the_slot_and_refuses_b(tmp_path, mutate, why):
    if mutate == "residual" or mutate == "unmatched":
        # 0.34 YES against 0.51 NO: directional exposure, not a matched pair
        _market_a_matched(tmp_path, up=D("0.34"), dn=D("0.34"))
        _leg(tmp_path, A, "EXTRA", "OUTCOME_SIDE_NO", D("0.17"), D("0.41"))
    elif mutate == "live_order":
        _market_a_matched(tmp_path)
        _leg(tmp_path, A, "LIVE9", "OUTCOME_SIDE_YES", D("0.17"), D("0.50"),
             terminal=False, filled=False)
    elif mutate == "no_terminal":
        _market_a_matched(tmp_path)
        cid = f"{AUTH}:{A}:NOTERM"
        _row(tmp_path, {"kind": lm.COMMIT_KIND, "authorization_id": AUTH,
                        "commit_id": cid, "slug": A,
                        "cash_at_risk": "0.05", "quantity": "0.10"})
    elif mutate == "no_attestation":
        _market_a_matched(tmp_path, attest=False)
    elif mutate == "venue_disagrees":
        _market_a_matched(tmp_path, venue_agrees=False)
    elif mutate == "not_flat":
        _market_a_matched(tmp_path, may_assert_flat=False)
    elif mutate == "stale_attestation":
        # attested at a state the market has since left
        _market_a_matched(tmp_path, attest_up=D("0.17"), attest_dn=D("0.17"))

    used = lm.consumption(tmp_path, AUTH)
    assert used["markets_open"] == [A], f"{why}: A must keep the slot"
    assert used["markets_pending_settlement"] == []
    v = _admit_b(tmp_path)
    assert v["admits"] is False, f"{why}: B must be refused"
    assert v["reason"] == "max_concurrent_markets_exhausted"


def test_an_unreadable_ledger_refuses_b_and_releases_nothing(tmp_path):
    """Unknown exposure state is not a safe state."""
    _market_a_matched(tmp_path)
    with (tmp_path / "ledger.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("{ this is not json\n")
    used = lm.consumption(tmp_path, AUTH)
    assert used["readable"] is False
    v = _admit_b(tmp_path)
    assert v["admits"] is False
    assert v["reason"] == "consumption_unreadable"


def test_a_fill_after_the_attestation_invalidates_it(tmp_path):
    """The attestation is evidence about a moment, never a standing permit."""
    _market_a_matched(tmp_path)
    assert lm.consumption(tmp_path, AUTH)["markets_pending_settlement"] == [A]
    _row(tmp_path, {"kind": "filled", "slug": A, "qty": "0.05",
                    "side": "OUTCOME_SIDE_YES", "stage": "spring"})
    used = lm.consumption(tmp_path, AUTH)
    assert used["markets_pending_settlement"] == []
    assert used["markets_open"] == [A]


def test_a_matched_pair_of_fills_after_the_attestation_also_invalidates_it(
        tmp_path):
    """Still matched, but not the state anybody reconciled against."""
    _market_a_matched(tmp_path)
    _row(tmp_path, {"kind": "filled", "slug": A, "qty": "0.05",
                    "side": "OUTCOME_SIDE_YES", "stage": "spring"})
    _row(tmp_path, {"kind": "filled", "slug": A, "qty": "0.05",
                    "side": "OUTCOME_SIDE_NO", "stage": "spring"})
    used = lm.consumption(tmp_path, AUTH)
    assert D(used["residual_usd"][A]) == D("0")      # matched again...
    assert used["markets_open"] == [A]               # ...but not attested


def test_zero_quantity_attestation_releases_nothing(tmp_path):
    """A market with no position is handled by the zero-fill rule, not by
    claiming 0 == 0 is a matched pair."""
    _row(tmp_path, {"kind": lm.FLAT_ATTEST_KIND, "slug": A,
                    "authorization_id": AUTH, "up": "0", "dn": "0",
                    "may_assert_flat": True, "venue_agrees": True})
    assert lm.consumption(tmp_path, AUTH)["markets_pending_settlement"] == []


# --------------------------------------------------------------------------
# the writer refuses to attest anything it has not proven

@pytest.mark.parametrize("recon,up,dn", [
    ({"may_assert_flat": True, "venue_vs_ledger": {"agrees": False}}, "0.34", "0.34"),
    ({"may_assert_flat": True, "venue_vs_ledger": {"agrees": None}}, "0.34", "0.34"),
    ({"may_assert_flat": False, "venue_vs_ledger": {"agrees": True}}, "0.34", "0.34"),
    ({"may_assert_flat": True, "venue_vs_ledger": {"agrees": True}}, "0.34", "0.17"),
    ({"may_assert_flat": True, "venue_vs_ledger": {"agrees": True}}, "0", "0"),
])
def test_attest_writes_nothing_unless_every_clause_holds(tmp_path, recon, up, dn):
    assert lm.attest_matched_flat(tmp_path, slug=A, up=up, dn=dn,
                                  recon=recon) is None
    assert not (tmp_path / "ledger.jsonl").exists()


def test_attest_writes_the_proof_when_it_does_hold(tmp_path):
    row = lm.attest_matched_flat(
        tmp_path, slug=A, up="0.34", dn="0.34", authorization_id=AUTH,
        recon={"may_assert_flat": True,
               "venue_vs_ledger": {"agrees": True, "venue_qty": "0.6800",
                                   "ledger_qty": "0.68"}})
    assert row is not None and row["kind"] == lm.FLAT_ATTEST_KIND
    assert row["venue_agrees"] is True and row["may_assert_flat"] is True
    assert (tmp_path / "ledger.jsonl").exists()


# --------------------------------------------------------------------------
# the limits themselves did not move

def test_no_risk_limit_was_relaxed_by_this_repair(tmp_path):
    m = _mandate()
    assert m.max_markets == 1
    assert m.max_total_markets == 10
    assert m.max_spend_per_market == D("5.00")
    assert m.max_total_spend == D("15.00")
    assert m.max_residual == D("3.00")
    assert m.max_daily_realized_loss == D("6.00")


def test_pending_settlement_still_consumes_max_total_markets(tmp_path):
    """Ten matched-and-pending markets exhaust the authorization exactly as
    ten settled ones would. Releasing the SLOT must never become unlimited
    market scanning."""
    m = _mandate(max_total_markets=1)
    _market_a_matched(tmp_path)
    used = lm.consumption(tmp_path, AUTH)
    assert used["n_markets_open"] == 0            # slot is free...
    v = _admit_b(tmp_path, m)
    assert v["admits"] is False                   # ...the authorization is not
    assert v["reason"] == "max_total_markets_exhausted"


def test_detect_without_the_new_argument_is_unchanged(tmp_path):
    """Suppression requires positive proof. A caller that passes nothing gets
    exactly the old alarm."""
    assert rsa.detect(prev_held=[A], held=[], unsettled=[A]) != []
    assert rsa.detect(prev_held=[A], held=[], unsettled=[A])[0]["alarm"] \
        == "held_absent_while_risk_bearing"
