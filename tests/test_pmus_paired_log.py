"""The paired log must not be able to flatter the shadow model.

Checkpoint 25's +6.1167/window is a number produced under a maker-fill MODEL.
These tests pin the one property that makes the live measurement worth running:
the live side of a row can only come from the venue.
"""
import json
import os

import pytest

from src.pmus_paired_log import (FILL_AUTHORITIES, PairedLog, PromotionRefused)


def mk(path=None, **kw):
    log = PairedLog(market="BTC15M-X", window="0230z", path=path)
    row = log.intend(action=kw.pop("action", "OPEN"), side=kw.pop("side", "up"),
                     shadow_price=kw.pop("shadow_price", "0.48"),
                     shadow_qty=kw.pop("shadow_qty", "50"),
                     shadow_filled=kw.pop("shadow_filled", True),
                     shadow_floor=kw.pop("shadow_floor", "1.00"),
                     shadow_locked=kw.pop("shadow_locked", False))
    return log, row


# ------------------------------------------------- NO PROMOTION OF SHADOW FILLS

def test_a_row_starts_unknown_even_when_the_shadow_model_filled_it():
    _, row = mk(shadow_filled=True)
    assert row.shadow_filled is True
    assert row.live_state == "UNKNOWN"
    assert row.live_filled is False


def test_live_filled_is_derived_from_the_live_state_only():
    _, row = mk(shadow_filled=True)
    row.submitted({"x": 1})
    assert row.live_filled is False
    row.acked({"filled_qty": 0})
    assert row.live_filled is False


def test_an_ack_claiming_a_fill_still_does_not_resolve_the_row():
    # probe 04 filled 2ms AFTER an ACK that said filled_qty 0; the inverse is
    # just as possible, so a claimed fill is recorded and still not believed.
    _, row = mk()
    row.acked({"filled_qty": 50, "status": "filled"})
    assert row.live_state == "ACKED"
    assert row.live_filled is False
    assert row.resolved is False
    assert row.ack["filled_qty"] == 50   # recorded verbatim


@pytest.mark.parametrize("bogus", ["SHADOW", "SHADOW_FILL", "ACK", "MODEL",
                                   "QUEUE_AWARE", "", "venue_fill", None])
def test_only_a_venue_authority_may_produce_a_live_fill(bogus):
    _, row = mk()
    with pytest.raises(PromotionRefused):
        row.venue_fill(price="0.48", qty="50", source=bogus)
    assert row.live_state == "UNKNOWN"


@pytest.mark.parametrize("src", FILL_AUTHORITIES)
def test_the_two_permitted_authorities_work(src):
    _, row = mk()
    row.venue_fill(price="0.49", qty="50", source=src)
    assert row.live_filled is True
    assert row.fill_source == src


def test_a_zero_or_negative_quantity_is_not_a_fill():
    for q in ("0", "-1"):
        _, row = mk()
        with pytest.raises(PromotionRefused):
            row.venue_fill(price="0.48", qty=q, source="VENUE_FILL")


# --------------------------------------------------------------- the verdicts

def test_shadow_only_is_the_finding_the_experiment_exists_to_catch():
    log, row = mk(shadow_filled=True)
    row.submitted({}); row.acked({}); row.nonfill("never_reached_top_of_queue")
    p = row.parity()
    assert p["verdict"] == "SHADOW_ONLY"
    assert p["nonfill_reason"] == "never_reached_top_of_queue"
    assert log.summary()["shadow_fill_confirmation_rate"] == "0"


def test_both_filled_reports_signed_price_and_quantity_deltas():
    _, row = mk(shadow_price="0.48", shadow_qty="50")
    row.venue_fill(price="0.50", qty="30", source="VENUE_FILL")
    p = row.parity()
    assert p["verdict"] == "BOTH_FILLED"
    assert p["price_delta"] == "0.02"     # we paid 2c MORE than modelled
    assert p["qty_delta"] == "-20"        # for 20 FEWER units


def test_live_only_is_reported_not_silently_dropped():
    _, row = mk(shadow_filled=False)
    row.venue_fill(price="0.48", qty="10", source="VENUE_FILL")
    assert row.parity()["verdict"] == "LIVE_ONLY"


def test_neither_filled_is_distinct_from_unresolved():
    log, row = mk(shadow_filled=False)
    assert row.parity()["verdict"] == "UNRESOLVED"
    row.nonfill("cancelled_at_close")
    assert row.parity()["verdict"] == "NEITHER_FILLED"
    assert log.summary()["complete"] is True


def test_an_unresolved_row_blocks_completeness():
    log, row = mk()
    row.submitted({}); row.acked({})
    assert log.unresolved() == [1]
    assert log.summary()["complete"] is False


def test_the_confirmation_rate_is_withheld_when_the_model_counted_no_fills():
    log, row = mk(shadow_filled=False)
    row.nonfill("no_fill")
    # not 1.0, not 0.0 -- there is nothing to confirm, so the number is absent
    assert log.summary()["shadow_fill_confirmation_rate"] is None


# --------------------------------------------- throughput, not submitted size

def test_throughput_counts_reconciled_fills_not_submitted_quantity():
    log = PairedLog(market="m", window="w")
    a = log.intend(action="OPEN", side="up", shadow_price="0.48",
                   shadow_qty="50", shadow_filled=True)
    b = log.intend(action="EXTEND", side="up", shadow_price="0.47",
                   shadow_qty="50", shadow_filled=True)
    a.submitted({}); a.venue_fill(price="0.48", qty="12", source="VENUE_FILL")
    b.submitted({}); b.nonfill("no_fill")
    s = log.summary()
    assert s["submitted_qty"] == "100"
    assert s["reconciled_filled_qty"] == "12"
    assert s["shadow_fills_counted"] == 2
    assert s["shadow_fills_confirmed_live"] == 1


# ------------------------------------------------------- the full chain, once

def test_the_row_carries_every_stage_the_owner_asked_for():
    log, row = mk(shadow_floor="1.00")
    row.submitted({"slug": "m", "side": "up"})
    row.acked({"order_id": "o1"})
    row.venue_fill(price="0.49", qty="50", source="VENUE_FILL",
                   inventory={"up": "50", "down": "0"}, floor="-24.5", locked=False)
    row.completed(side="down", ask="0.47", permitted_by="taker_completion_ok")
    row.reconciled(venue_position="50", agrees="True")
    row.settled(authoritative_cash_pnl="5.25", resolution="UP")
    d = row.to_dict()
    assert d["shadow"]["action"] == "OPEN"
    assert d["shadow"]["fill_treatment"] == "queue_aware"
    for k in ("submission", "ack", "price", "qty", "inventory", "floor",
              "locked", "completion", "cancel", "reconciliation", "settlement",
              "authoritative_cash_pnl"):
        assert k in d["live"]
    assert d["live"]["authoritative_cash_pnl"] == "5.25"
    assert log.summary()["authoritative_cash_pnl"] == "5.25"


def test_cash_pnl_is_absent_until_settlement():
    log, row = mk()
    row.venue_fill(price="0.49", qty="50", source="VENUE_FILL")
    assert log.summary()["authoritative_cash_pnl"] is None


def test_a_mandate_refusal_resolves_the_row_without_a_fill():
    log, row = mk()
    row.refused({"admitted": False, "reason": "spend_cap"})
    assert row.live_state == "REFUSED"
    assert row.live_filled is False
    assert row.resolved is True


# ------------------------------------------------------------- durability

def test_every_live_transition_is_on_disk_before_the_next_one(tmp_path):
    p = str(tmp_path / "paired.json")
    log, row = mk(path=p)
    for step, expect in ((lambda: row.submitted({"a": 1}), "SUBMITTED"),
                         (lambda: row.acked({"b": 2}), "ACKED"),
                         (lambda: row.venue_fill(price="0.49", qty="50",
                                                 source="VENUE_FILL"), "FILLED")):
        step()
        assert json.load(open(p))["rows"][0]["live"]["state"] == expect
    row.settled(authoritative_cash_pnl="5.25")
    assert json.load(open(p))["summary"]["authoritative_cash_pnl"] == "5.25"


def test_the_file_is_replaced_atomically_and_leaves_no_temp(tmp_path):
    p = str(tmp_path / "paired.json")
    log, row = mk(path=p)
    row.nonfill("x")
    assert os.path.exists(p) and not os.path.exists(p + ".tmp")


def test_the_frozen_shadow_treatment_is_recorded_on_every_row():
    log = PairedLog(market="m", window="w")
    r = log.intend(action="OPEN", side="up", shadow_price="0.4",
                   shadow_qty="1", shadow_filled=True)
    assert r.shadow_treatment == "queue_aware"
    assert log.summary()["shadow_fill_treatment"] == "queue_aware"
