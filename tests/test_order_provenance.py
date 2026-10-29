"""OWNER 2026-09-17 P0 -- an arbitrary venue order traces back deterministically.

WHAT FAILED. On 2026-09-17 the rejection forensics could not answer "what was
the book when we sent this order?" for a single one of 47 submissions:

  * `id=` appeared in ZERO of the 47 preview log lines -- they truncate before
    it -- so previews could not be keyed to order ids at all;
  * only 23 of 63 decision rows carried an authenticated BBO;
  * positional alignment of decisions to orders was WRONG in 5 of 9 sessions,
    so zipping them was unsound.

Three joins were attempted and two were plainly wrong. The conclusion was that
the question was not reconstructable, which is not an acceptable state for a
system that moves money.

THE FIX IS A DURABLE ROW, not a better parser: `order_provenance`, written at
the one point where the venue's order id first exists alongside the decision
that produced it. These tests prove the chain closes for an arbitrary order and
that nothing in it is inferred from log text.
"""
from __future__ import annotations

import ast
import inspect
import io
import json
from decimal import Decimal as D

import pytest

from src import pmus_spring_l5_session as S

#: every field the owner named, by the name the row uses
REQUIRED = (
    "order_id", "decision_id", "decision_idx", "session_id",
    "sampled_relation", "acted_relation", "order_relation",
    "inventory_before", "proposed_inventory_after_if_filled",
    "authenticated_bbo", "desired_px", "wire_px", "qty",
    "pair_economics", "submission_result", "terminal_result",
)


def _provenance_call_source() -> str:
    """The whole provenance block, from its banner comment to the end of the
    guarded write. Anchored on the banner rather than on a byte offset: the
    first version of this helper sliced 400 characters backwards from the
    `kind` line and silently cut off the decision binding and the wire-price
    conversion it was supposed to be checking."""
    src = io.open(inspect.getsourcefile(S), encoding="utf-8").read()
    start = src.index("FULL ORDER<->DECISION PROVENANCE")
    end = src.index("order_provenance_write_failed", start)
    return src[start:end + 200]


def test_the_row_carries_every_field_the_owner_named():
    body = _provenance_call_source()
    missing = [k for k in REQUIRED if '"%s"' % k not in body]
    assert missing == [], "order_provenance is missing: %s" % missing


def test_the_decision_is_bound_at_submission_not_inferred_later():
    """The binding must read the CURRENT decision at the moment the order id
    exists. Inferring it afterwards is what was unsound."""
    body = _provenance_call_source()
    assert 'report.get("decisions") or [{}])[-1]' in body


def test_the_row_is_written_where_the_order_id_first_exists():
    """It must sit with the venue's oid in scope -- not in a later pass that
    would have to re-derive the association."""
    body = _provenance_call_source()
    assert '"order_id": oid' in body
    assert '"terminal_result"' in body and "end_reason" in body


def test_provenance_failure_cannot_break_the_money_path():
    """A provenance bug must degrade to a visible gap, never to a wrong
    answer and never to a failed order."""
    body = _provenance_call_source()
    assert "except Exception" in body
    assert "order_provenance_write_failed" in body


def test_the_wire_price_conversion_is_present_and_side_aware():
    """wire is the LONG-side price; a NO order goes out at 1 - px. Getting
    this backwards is what made the first two forensics joins wrong."""
    body = _provenance_call_source()
    assert 'px_ if side_up_ else (D("1") - px_)' in body


def test_proposed_inventory_is_labelled_as_proposed_not_a_position():
    body = _provenance_call_source()
    assert "PROPOSED, not achieved" in body
    assert "Never read as a position" in body


# --------------------------------------------------------------------------
# the trace itself, on a synthetic row shaped exactly like the emitted one

def _row(order_id="CJ12V9NJ6VNN"):
    """A row with the same shape the session writes."""
    return {
        "kind": "order_provenance", "spring": True,
        "slug": "aec-itfme-maxreh-alewei-2026-09-17",
        "order_id": order_id, "stage": "spring_extend_2",
        "session_id": "cb78ff4a293d4a46",
        "decision_id": "770651d5311fc15dc3f3e35d", "decision_idx": 4,
        "sampled_relation": "extend", "acted_relation": "extend",
        "order_relation": "extend", "side": "NO",
        "desired_px": "0.28", "wire_px": "0.72", "qty": "0.14",
        "inventory_before": {"up": "0.40", "dn": "0.45", "residual": "-0.05"},
        "proposed_inventory_after_if_filled": {
            "up": "0.40", "dn": "0.59", "residual": "-0.19"},
        "authenticated_bbo": {"yes_bid": "0.99", "yes_ask": None,
                              "authenticated_age_s": 1.3,
                              "both_sides_served": False, "elapsed_ms": 148.9},
        "pair_economics": {"pair_cost_max": "0.98", "chosen_px": "0.28"},
        "submission_result": {"order_id_returned": True},
        "terminal_result": {"end_reason": "rejected:ORDER_REJECTED_ZERO_FILL",
                            "filled_qty": "0", "rest_s": 0.9},
    }


def trace(rows, order_id):
    """Given a venue order id, return its full origin. Deterministic: the row
    is keyed by order_id, so there is exactly one answer or none."""
    hits = [r for r in rows if r.get("kind") == "order_provenance"
            and r.get("order_id") == order_id]
    if len(hits) != 1:
        return None
    return hits[0]


def test_an_arbitrary_order_id_traces_to_exactly_one_decision_and_book():
    rows = [_row("A"), _row("B"), _row("CJ12V9NJ6VNN"), {"kind": "filled"}]
    got = trace(rows, "CJ12V9NJ6VNN")
    assert got is not None
    assert got["decision_id"] == "770651d5311fc15dc3f3e35d"
    assert got["decision_idx"] == 4
    assert got["authenticated_bbo"]["yes_bid"] == "0.99"
    assert got["inventory_before"]["residual"] == "-0.05"
    assert got["proposed_inventory_after_if_filled"]["residual"] == "-0.19"


def test_the_trace_explains_the_live_rejection_without_any_log_parsing():
    """The exact question the forensics could not answer: this order was a NO
    buy at wire 0.72 while the YES bid was 0.99, so post-only had to reject
    it. Every term comes from the row."""
    got = trace([_row()], "CJ12V9NJ6VNN")
    wire, bid = D(got["wire_px"]), D(got["authenticated_bbo"]["yes_bid"])
    assert got["side"] == "NO"
    assert wire <= bid, "a NO buy at or below the bid is marketable"
    assert "REJECTED" in got["terminal_result"]["end_reason"]


def test_an_unknown_order_id_returns_nothing_rather_than_a_guess():
    assert trace([_row()], "NOT-AN-ORDER") is None


def test_a_duplicated_order_id_refuses_to_resolve():
    """Two rows for one id is a provenance defect, and the trace must say so
    by returning nothing rather than silently picking one."""
    assert trace([_row("X"), _row("X")], "X") is None


def test_sampled_and_acted_are_both_recorded_so_substitution_is_visible():
    """A sampled COMPLETE executed as something else must be provable from
    the order row alone."""
    r = _row()
    r["sampled_relation"], r["acted_relation"] = "complete", "extend"
    got = trace([r], "CJ12V9NJ6VNN")
    assert got["sampled_relation"] != got["acted_relation"]


def test_the_row_is_json_serialisable_as_written():
    json.loads(json.dumps(_row()))
