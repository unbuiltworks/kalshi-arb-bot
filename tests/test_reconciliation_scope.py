"""OWNER 2026-09-17 P0 -- reconciliation scope, terminal rejection, read errors.

THE DEFECT. `_reconcile_fills` compared the venue's CUMULATIVE traded quantity
for a market against `inv.up + inv.dn` -- the SESSION's inventory. On a resumed
session those coincide because the session rebuilt itself from the ledger. On a
FRESH session they do not. Live 2026-09-17T12:59:31Z reported "venue 0.9900 vs
ledger 0.19, delta 0.8000, agrees false" on a market where nothing was wrong:
the 0.80 was three prior sessions' fills, in the durable ledger the whole time.
`recovered_qty` was 0 on every session of that cohort -- the reconciler has
never recovered a share.

WHY IT MATTERS MORE THAN A COSMETIC MISREADING. A false disagreement pins
`may_assert_flat` False, which is the gate that blocks settlement-pending
concurrency release. And the failure direction is not always benign: had the
account been UNMATCHED when a fresh session started, the session-local residual
would have UNDERSTATED true account exposure. That is the case this file exists
to make impossible, and it is the owner's adversarial case:

    session A leaves an unmatched residual
      -> fresh session B starts, takes one small fill
      -> the system must still see the COMPLETE A+B residual,
         and must not assert flat or release concurrency from B's local view.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

from src import pmus_fill_ledger as fl
from src import pmus_live_mandate as lm

SLUG = "aec-itfme-maxreh-alewei-2026-09-17"
AUTH = "OWNER-2026-09-17-CP1-SETTLEMENT-PENDING-COHORT"


# --------------------------------------------------------------------------
# the comparison itself

def test_venue_vs_ledger_agrees_when_both_sides_are_whole_market():
    """The live numbers: venue 0.99 against the DURABLE 0.99."""
    r = fl.venue_vs_ledger("0.9900", D("0.99"))
    assert r["agrees"] is True
    assert fl.may_assert_flat(r) is True


def test_venue_vs_ledger_disagrees_against_a_session_local_figure():
    """The same venue number against session-local 0.19 -- the false alarm
    this repair removes. Pinned so the regression is unmistakable if the
    comparison ever reverts to session scope."""
    r = fl.venue_vs_ledger("0.9900", D("0.19"))
    assert r["agrees"] is False
    assert r["delta"] == "0.8000"
    assert fl.may_assert_flat(r) is False


def test_a_real_disagreement_still_fails_closed():
    """Narrowing the false positive must not blunt the true one."""
    assert fl.venue_vs_ledger("1.5000", D("0.99"))["agrees"] is False
    assert fl.may_assert_flat(fl.venue_vs_ledger(None, D("0.99"))) is False


# --------------------------------------------------------------------------
# THE ADVERSARIAL CASE, on the mandate's own ledger reading

def _row(d, row):
    with (d / "ledger.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _fill(d, qty, side):
    cid = f"{AUTH}:{SLUG}:{qty}:{side}"
    _row(d, {"kind": lm.COMMIT_KIND, "authorization_id": AUTH,
             "commit_id": cid, "slug": SLUG, "cash_at_risk": "0.05",
             "quantity": str(qty)})
    _row(d, {"kind": lm.LINK_KIND, "authorization_id": AUTH,
             "commit_id": cid, "order_id": "O" + cid[-8:]})
    _row(d, {"kind": "order_terminal", "order_id": "O" + cid[-8:],
             "slug": SLUG, "filled_qty": str(qty)})
    _row(d, {"kind": "filled", "slug": SLUG, "qty": str(qty), "side": side})


def test_session_a_residual_plus_fresh_session_b_fill_is_seen_whole(tmp_path):
    """A leaves 0.40 unmatched NO. B is FRESH and takes 0.14 more NO.

    The account residual is 0.54. B's local view would be 0.14. Every safety
    reading must be 0.54."""
    _fill(tmp_path, D("0.40"), "OUTCOME_SIDE_NO")          # session A
    used_a = lm.consumption(tmp_path, AUTH)
    assert D(used_a["residual_usd"][SLUG]) == D("0.40")

    _fill(tmp_path, D("0.14"), "OUTCOME_SIDE_NO")          # fresh session B
    used = lm.consumption(tmp_path, AUTH)

    assert D(used["residual_usd"][SLUG]) == D("0.54"), \
        "the durable ledger must show A+B, never B alone"
    assert D(used["filled_qty"][SLUG]) == D("0.54")


def test_fresh_session_b_cannot_release_concurrency_from_its_local_view(tmp_path):
    """B's own 0.14 is not matched and is not the account's position. The
    market must keep its acquisition slot and must not be settlement-pending."""
    _fill(tmp_path, D("0.40"), "OUTCOME_SIDE_NO")
    _fill(tmp_path, D("0.14"), "OUTCOME_SIDE_NO")
    used = lm.consumption(tmp_path, AUTH)
    assert used["markets_open"] == [SLUG]
    assert used["markets_pending_settlement"] == []
    assert used["n_markets_open"] == 1


def test_an_attestation_built_from_the_session_local_view_is_refused(tmp_path):
    """The attack this closes: B attests 'matched flat' from its own 0.14.

    Even with a perfect reconciliation proof attached, the mandate re-derives
    the matched state from the durable rows and refuses, because 0.40 NO plus
    0.14 NO is 0.54 unmatched -- not a matched pair."""
    _fill(tmp_path, D("0.40"), "OUTCOME_SIDE_NO")
    _fill(tmp_path, D("0.14"), "OUTCOME_SIDE_NO")
    _row(tmp_path, {"kind": lm.FLAT_ATTEST_KIND, "slug": SLUG,
                    "authorization_id": AUTH,
                    "up": "0.14", "dn": "0.14",          # B's local fiction
                    "may_assert_flat": True, "venue_agrees": True})
    used = lm.consumption(tmp_path, AUTH)
    assert used["markets_pending_settlement"] == []
    assert used["markets_open"] == [SLUG]


def test_a_genuinely_matched_whole_market_position_still_releases(tmp_path):
    """The repair must not make release impossible -- only honest."""
    _fill(tmp_path, D("0.40"), "OUTCOME_SIDE_NO")
    _fill(tmp_path, D("0.40"), "OUTCOME_SIDE_YES")
    _row(tmp_path, {"kind": lm.FLAT_ATTEST_KIND, "slug": SLUG,
                    "authorization_id": AUTH, "up": "0.40", "dn": "0.40",
                    "may_assert_flat": True, "venue_agrees": True})
    used = lm.consumption(tmp_path, AUTH)
    assert used["markets_pending_settlement"] == [SLUG]
    assert used["markets_open"] == []


# --------------------------------------------------------------------------
# terminal rejection closes immediately, from the venue's own record

#: the venue's own record, exactly as the raise site now attaches it
REJECTED_REC = {"state": "ORDER_STATE_REJECTED", "cumQuantity": "0",
                "id": "CJ12V9NJ6VNN", "price": "0.72", "quantity": "0.14"}


def test_a_venue_rejection_record_closes_the_order_without_any_re_read():
    """THE LIVE DEFECT, inverted. 11 of 13 rejections on 2026-09-17 stayed
    open because the close path re-GOT the order and the venue refuses to
    serve a rejected one. Given the record the exception now carries, the
    order closes with no read at all."""
    led = fl.FillLedger()
    led.track("CJ12V9NJ6VNN")
    assert led.open_orders() == ["CJ12V9NJ6VNN"]
    assert led.close_on_venue_rejection("CJ12V9NJ6VNN", REJECTED_REC) is True
    assert led.open_orders() == [], "a rejected zero-fill order is terminal"


def test_the_close_guard_is_unchanged_for_a_rejection_reporting_a_fill():
    """Narrowing how we OBTAIN the record must not widen what it may close.
    A rejection claiming quantity we never booked is a contradiction, and the
    order stays open."""
    led = fl.FillLedger()
    led.track("CJ12V9NJ6VNN")
    lying = dict(REJECTED_REC, cumQuantity="0.14")
    assert led.close_on_venue_rejection("CJ12V9NJ6VNN", lying) is False
    assert led.open_orders() == ["CJ12V9NJ6VNN"]


def test_no_record_closes_nothing():
    led = fl.FillLedger()
    led.track("CJ12V9NJ6VNN")
    assert led.close_on_venue_rejection("CJ12V9NJ6VNN", None) is False
    assert led.open_orders() == ["CJ12V9NJ6VNN"]


def test_the_exception_carries_the_venue_record():
    """The raise site must attach it, or the catch site has nothing to use."""
    import inspect
    from src import pmus_q1_executor_v4 as v4
    src = inspect.getsource(v4)
    assert "_rej.order = dict(order)" in src
    assert "_rej.order_id = order_id" in src
    assert "raise _rej" in src


def test_terminality_is_never_inferred_from_an_unreadable_read():
    """An order we could not read is OPEN, with the reason retained."""
    led = fl.FillLedger()
    oids = ["CJ12V9NJ6VNN"]

    def boom(_oid):
        exc = RuntimeError("HTTP 400 order not servable once rejected")
        exc.code = 400
        raise exc

    out = led.reconcile(boom, order_ids=oids)
    assert out["unreadable"] == oids
    assert out["closed"] == []
    assert "CJ12V9NJ6VNN" in out["unreadable_why"]
    w = out["unreadable_why"]["CJ12V9NJ6VNN"]
    assert w.startswith("RuntimeError:400:"), w
    assert "not servable" in w


def test_unreadable_why_is_present_even_without_an_http_code():
    led = fl.FillLedger()

    def boom(_oid):
        raise ValueError("malformed body")

    out = led.reconcile(boom, order_ids=["X1"])
    assert out["unreadable_why"]["X1"].startswith("ValueError:-:")


def test_reconcile_still_reports_the_same_shape(tmp_path):
    """unreadable_why is additive: every existing key survives."""
    led = fl.FillLedger()
    out = led.reconcile(lambda _o: {"state": "ORDER_STATE_NEW"}, order_ids=[])
    for k in ("recovered_fills", "recovered_qty", "closed", "unreadable",
              "still_open", "contradictions", "unreadable_why"):
        assert k in out, k
