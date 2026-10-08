"""THREE ATTEMPTS IN ONE WINDOW, AND THE TAPE THAT PROVES IT.

Two things are under test and neither is a new strategy.

1. THE ATTEMPT TAPE IS WRITTEN FROM THE PRODUCTION PATH. `record_attempt`
   and `reconstruct_chain` existed, were frozen into the spec's call graph,
   were covered by unit tests -- and nothing in production called either,
   so `attempts.jsonl` was always empty and the estimation chain the
   stopping rule depends on would have run over zero rows. These tests
   drive `run_cohort` and then read the file off disk.

2. THE ENVELOPE IS FOUR BOUNDS, NOT ONE. `3 x 0.13 x $0.60 = $0.2340`
   bounds the FIRST-LEG purchases. It is not the gross outlay, because
   each filled first leg is followed by a complement and the pair may cost
   $0.96 per share, so the cash that can leave is `3 x 0.13 x $0.96 =
   $0.3744`. Cumulative shares, gross notional, instantaneous outstanding
   exposure and directional residual are bounded separately, each counting
   the attempt about to be made, each read from venue-confirmed state or
   durable latch files.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D

import pytest

from analysis.l5.e1_cohort import attempt_rows as AR
from analysis.l5.e1_cohort import multitrade as MT
from analysis.l5.e1_cohort import recorder as RC
from analysis.l5.e1_cohort.spec import (FIRST_LEG_QTY, MAX_FIRST_LEG_PRICE,
                                        PAIR_COST_CEILING)

UP = "OUTCOME_SIDE_YES"
DOWN = "OUTCOME_SIDE_NO"
SLUG = "cpc-btc-updown-15m-2026-10-08-2200z"


# ===================== 1. THE EXPOSURE ARITHMETIC =======================

def test_first_leg_outlay_is_not_the_gross_outlay():
    env = MT.exposure_envelope(3)
    assert env["cum_first_leg_shares"] == "0.39"
    assert env["first_leg_purchases_usd"] == "0.2340"
    # THE CORRECTION. The complement is a second purchase.
    assert env["gross_outlay_after_complements_usd"] == "0.3744"
    assert env["complement_portion_usd"] == "0.1404"
    assert D(env["gross_outlay_after_complements_usd"]) > \
        D(env["first_leg_purchases_usd"])


def test_the_2200z_probe_is_the_arithmetics_own_witness():
    """One pair actually cost $0.1248 == 0.13 x $0.96."""
    assert MT.exposure_envelope(3)["per_pair_gross_usd"] == "0.1248"
    assert FIRST_LEG_QTY * PAIR_COST_CEILING == D("0.1248")


def test_three_attempt_envelope_is_derived_never_typed():
    e = MT.MultiTradeEnvelope.for_attempts(3)
    assert e.cum_first_leg_shares == 3 * FIRST_LEG_QTY
    assert e.gross_notional_usd == 3 * FIRST_LEG_QTY * PAIR_COST_CEILING
    assert e.residual_shares == FIRST_LEG_QTY
    assert e.outstanding_usd == (FIRST_LEG_QTY * MAX_FIRST_LEG_PRICE
                                 + FIRST_LEG_QTY
                                 * (PAIR_COST_CEILING - MT.TICK))


# ======================= 2. NARROWING ONLY ==============================

@pytest.mark.parametrize("key", list(MT.ENVELOPE_KEYS))
def test_a_missing_bound_is_a_refusal(monkeypatch, key):
    for k in MT.ENVELOPE_KEYS:
        monkeypatch.setenv(k, "0.1")
    monkeypatch.delenv(key, raising=False)
    with pytest.raises(MT.EnvelopeNotAuthorized):
        MT.MultiTradeEnvelope.from_env()


@pytest.mark.parametrize("bad", ["", "  ", "abc", "0", "-1", "NaN", "1e400"])
def test_a_malformed_bound_is_a_refusal(monkeypatch, bad):
    for k in MT.ENVELOPE_KEYS:
        monkeypatch.setenv(k, "0.1")
    monkeypatch.setenv(MT.SHARES_KEY, bad)
    with pytest.raises(MT.EnvelopeNotAuthorized):
        MT.MultiTradeEnvelope.from_env()


def test_a_bound_wider_than_the_frozen_ceiling_is_a_refusal(monkeypatch):
    for k in MT.ENVELOPE_KEYS:
        monkeypatch.setenv(k, "0.1")
    monkeypatch.setenv(MT.GROSS_KEY,
                       str(MT.FROZEN_GROSS_NOTIONAL_USD + D("0.01")))
    with pytest.raises(MT.EnvelopeNotAuthorized) as e:
        MT.MultiTradeEnvelope.from_env()
    assert "may only" in str(e.value).lower()


def test_the_three_attempt_values_are_accepted(monkeypatch):
    monkeypatch.setenv(MT.SHARES_KEY, "0.39")
    monkeypatch.setenv(MT.GROSS_KEY, "0.3744")
    monkeypatch.setenv(MT.OUTSTANDING_KEY, "0.2015")
    monkeypatch.setenv(MT.RESIDUAL_KEY, "0.13")
    e = MT.MultiTradeEnvelope.from_env()
    assert e.cum_first_leg_shares == D("0.39")
    assert e.gross_notional_usd == D("0.3744")
    assert e.outstanding_usd == D("0.2015")
    assert e.residual_shares == D("0.13")


# ================== 3. THE GATE READS REAL STATE ========================

class FakeLatch:
    """Durable slot count, as the real latch reports it."""

    def __init__(self, spent: int, available: bool = True):
        self._spent, self._available = spent, available

    def window_state(self, *, window, session_id=None):
        if not self._available:
            return {"available": False, "window": window,
                    "why": "LATCH_DIR_MISSING"}
        return {"available": True, "window": window,
                "attempts_spent": self._spent, "attempts_remaining": 20 - self._spent,
                "exhausted": False}


def view(*, filled="0", open_="0", residual=None, ok=True, reason=None,
         n_outstanding=0, rows=None, known=None, resolved=None):
    """A view shaped like `read_order_state`'s.

    `filled`/`open_` are the SCOPE-wide sums the real view publishes; the
    envelope deliberately ignores them and sums OUR rows instead, so the
    tests supply rows and the registry's ids.
    """
    rows = list(rows or [])
    if known is None:
        known = [str(r.get("id")) for r in rows]
    return {"ok": ok, "reason": reason, "filled_notional_usd": filled,
            "open_notional_usd": open_, "residual": residual,
            "n_outstanding": n_outstanding, "orders": rows,
            "known_ids": list(known),
            "resolved_history_ids": list(resolved or [])}


def owned_pair(n, *, fl_own="0.02", cp_own="0.94", cum="0.13"):
    """N completed pairs of OURS, as classified rows."""
    out = []
    for i in range(n):
        out.append(order("FL%d" % i, side=DOWN, cum=cum, own=fl_own))
        out.append(order("CP%d" % i, side=UP, cum=cum, own=cp_own))
    return out


def env3():
    return MT.MultiTradeEnvelope.for_attempts(3)


def test_an_unestablished_order_state_refuses_outright():
    g = env3().gate(order_state=view(ok=False, reason="HTTPError:429"),
                    window=3, latch_module=FakeLatch(0))
    assert g["may_attempt"] is False
    assert any("MT_ORDER_STATE_NOT_ESTABLISHED" in r for r in g["refusals"])


def test_an_unreadable_latch_is_a_refusal_not_a_zero():
    g = env3().gate(order_state=view(), window=3,
                    latch_module=FakeLatch(0, available=False))
    assert g["may_attempt"] is False
    assert any("LATCH_SCOPE_UNAVAILABLE" in r for r in g["refusals"])


def test_attempt_one_of_three_passes_every_bound():
    g = env3().gate(order_state=view(), window=3, first_leg_price="0.02",
                    latch_module=FakeLatch(0))
    assert g["may_attempt"] is True, g["refusals"]
    assert g["prospective_cum_first_leg_shares"] == "0.13"


def test_attempt_two_counts_the_FIRST_attempts_durable_slot():
    """THE REGRESSION. Attempt two must see attempt one's spent slot."""
    g = env3().gate(order_state=view(rows=owned_pair(1)), window=3,
                    first_leg_price="0.02", latch_module=FakeLatch(1))
    assert g["authorized_first_leg_shares"]["slots_spent"] == 1
    assert g["prospective_cum_first_leg_shares"] == "0.26"
    assert g["may_attempt"] is True, g["refusals"]
    # and the gross figure carries the first pair's real cost, summed
    # from OUR rows: 0.13 x 0.02 + 0.13 x 0.94 = 0.1248
    assert g["our_filled_notional_usd"] == "0.1248"
    assert g["prospective_gross_notional_usd"] == "0.2496"


def test_attempt_three_counts_both_earlier_slots():
    g = env3().gate(order_state=view(rows=owned_pair(2)), window=3,
                    first_leg_price="0.02", latch_module=FakeLatch(2))
    assert g["prospective_cum_first_leg_shares"] == "0.39"
    assert g["prospective_gross_notional_usd"] == "0.3744"
    assert g["may_attempt"] is True, g["refusals"]


def test_a_FOURTH_attempt_is_refused_on_shares_and_on_gross():
    g = env3().gate(order_state=view(rows=owned_pair(3)), window=3,
                    first_leg_price="0.02", latch_module=FakeLatch(3))
    assert g["may_attempt"] is False
    joined = " ".join(g["refusals"])
    assert "MT_CUM_FIRST_LEG_SHARES_0.52_WOULD_EXCEED_0.39" in joined
    assert "MT_GROSS_NOTIONAL_0.4992_WOULD_EXCEED_0.3744" in joined


def test_the_bound_is_never_crossed_not_even_by_one_attempt():
    """Three slots spent and no spend recorded still refuses on shares."""
    g = env3().gate(order_state=view(), window=3,
                    first_leg_price="0.02", latch_module=FakeLatch(3))
    assert g["may_attempt"] is False
    assert any("CUM_FIRST_LEG_SHARES" in r for r in g["refusals"])


def test_outstanding_exposure_is_bounded_at_one_instant():
    resting = [dict(order("CPX", side=UP, cum="0", own="0.95",
                          state="ORDER_STATE_NEW"),
                    outstanding=True, leaves_quantity="0.13",
                    terminal=False)]
    g = env3().gate(order_state=view(rows=resting), window=3,
                    first_leg_price="0.60", latch_module=FakeLatch(0))
    # 0.1235 already resting + 0.13 x 0.60 = 0.2015 -> exactly at the bound
    # 0.13 x 0.95 resting + 0.13 x 0.60 = 0.2015, exactly at the bound
    assert g["our_open_notional_usd"] == "0.1235"
    assert g["prospective_outstanding_usd"] == "0.2015"
    assert g["may_attempt"] is True, g["refusals"]
    dearer = [dict(order("CPX", side=UP, cum="0", own="0.96",
                         state="ORDER_STATE_NEW"),
                   outstanding=True, leaves_quantity="0.13",
                   terminal=False)]
    g2 = env3().gate(order_state=view(rows=dearer), window=3,
                     first_leg_price="0.60", latch_module=FakeLatch(0))
    assert g2["may_attempt"] is False
    assert any("MT_OUTSTANDING" in r for r in g2["refusals"])


def test_a_directional_residual_over_the_bound_refuses():
    g = env3().gate(
        order_state=view(residual={"qty": "0.26", "outcome_side": UP}),
        window=3, first_leg_price="0.02", latch_module=FakeLatch(1))
    assert g["may_attempt"] is False
    assert any("MT_RESIDUAL_SHARES_0.26_EXCEEDS_0.13" in r
               for r in g["refusals"])


def test_an_unpriceable_order_of_OURS_is_a_refusal_not_zero():
    bad = [dict(order("FLX", side=DOWN, cum="0", own="0.02",
                      state="ORDER_STATE_NEW"),
                outstanding=True, leaves_quantity="0.13", terminal=False)]
    bad[0]["own_side_price"] = None
    g = env3().gate(order_state=view(rows=bad), window=3,
                    latch_module=FakeLatch(0))
    assert g["may_attempt"] is False
    assert any("MT_OUR_EXPOSURE_UNPRICEABLE" in r for r in g["refusals"])


def test_another_desks_resting_order_is_NOT_charged_to_our_envelope():
    """The shared account. A foreign 16.21 shares in OUR market must not
    refuse our attempt: the scope-wide sum includes it by design, our
    envelope does not."""
    foreign = dict(order("OTHER-SAME-MARKET", side=DOWN, cum="0",
                         own="0.20", state="ORDER_STATE_NEW"),
                   outstanding=True, leaves_quantity="16.21",
                   terminal=False)
    st = view(rows=[foreign], known=[], open_="3.2420")
    g = env3().gate(order_state=st, window=3, first_leg_price="0.02",
                    latch_module=FakeLatch(0))
    assert g["our_open_notional_usd"] == "0"
    assert g["may_attempt"] is True, g["refusals"]


def test_rows_missing_entirely_is_a_refusal():
    st = view()
    st["orders"] = None
    g = env3().gate(order_state=st, window=3, latch_module=FakeLatch(0))
    assert g["may_attempt"] is False
    assert any("MT_OWN_EXPOSURE_NOT_ESTABLISHED" in r
               for r in g["refusals"])


def test_resolved_history_is_not_charged_again():
    rows = owned_pair(1)
    st = view(rows=rows, resolved=[r["id"] for r in rows])
    g = env3().gate(order_state=st, window=3, first_leg_price="0.02",
                    latch_module=FakeLatch(1))
    assert g["our_filled_notional_usd"] == "0"


def test_the_gate_counts_the_whole_pair_not_just_the_first_leg():
    g = env3().gate(order_state=view(), window=3, first_leg_price="0.02",
                    latch_module=FakeLatch(0))
    # the first leg would cost 0.13 x 0.02 = 0.0026, but the pair at the
    # ceiling is what is charged against the gross bound
    assert g["prospective_pair_cost_usd"] == "0.1248"


# =============== 4. THE TAPE, AND THE RECONSTRUCTOR =====================

WS = {"timing_authority": RC.TIMING_WEBSOCKET,
      "pinned_market_streaming": True,
      "rest_is_sufficient_timing_authority": False}


class Leg:
    pass


class FakeLifecycle:
    def __init__(self, legs, residuals=None, events=None):
        self.legs = legs
        self.residuals_opened = residuals or {}
        self.events = events or []
        self.deadman_result = None


def order(oid, *, side, cum, qty="0.13", own="0.02",
          state="ORDER_STATE_FILLED"):
    return {"id": oid, "slug": SLUG, "outcome_side": side, "state": state,
            "quantity": qty, "cum_quantity": cum, "leaves_quantity": "0",
            "own_side_price": own, "wire_price": own, "terminal": True,
            "partially_filled": False}


def three_attempt_state():
    """Three first legs, three complements, all filled: the live shape."""
    legs, orders, comp = {}, [], {}
    for i in range(3):
        fl, cp = "FL%d" % i, "CP%d" % i
        legs[fl] = {"window": 3, "slot": i, "price": "0.02",
                    "complemented": "0.13"}
        orders.append(order(fl, side=DOWN, cum="0.13", own="0.02"))
        orders.append(order(cp, side=UP, cum="0.13", own="0.94"))
        comp[fl] = cp
    st = view(filled="0.3744")
    st["orders"] = orders
    return legs, st, comp


def test_the_production_path_writes_one_attempt_row_per_attempt(tmp_path):
    rec = RC.Recorder(str(tmp_path))
    legs, st, comp = three_attempt_state()
    out = AR.emit_attempt_rows(rec, lifecycle=FakeLifecycle(legs),
                               order_state=st, market=SLUG, ws_state=WS,
                               complement_ids=comp,
                               windows_in_scope={3})
    assert out["errors"] == [], out["errors"]
    assert out["n_written"] == 3
    rows = rec.read(RC.ATTEMPTS)
    assert len(rows) == 3, "one row per attempt, never two"


def test_reconstruct_chain_reads_the_live_format_tape(tmp_path):
    rec = RC.Recorder(str(tmp_path))
    legs, st, comp = three_attempt_state()
    AR.emit_attempt_rows(rec, lifecycle=FakeLifecycle(legs), order_state=st,
                         market=SLUG, ws_state=WS, complement_ids=comp,
                         windows_in_scope={3})
    chain = RC.reconstruct_chain(rec)
    s1 = chain["step_1_first_leg_maker_fill_rate"]
    s2 = chain["step_2_conditional_maker_to_maker_completion_rate"]
    s4 = chain["step_4_pair_cost"]
    assert s1["placements"] == 3, "the live tape must carry three attempts"
    assert s1["fills"] == 3
    assert s1["rate"] == "1"
    assert s2["completions"] == 3
    assert s2["non_completions"] == 0
    # the pair cost is arithmetic over two confirmed fills: 0.02 + 0.94
    assert s4["n"] == 3
    assert s4["mean_usd_per_share"] == "0.96"
    assert s4["all_within_ceiling_096"] is True
    # the reconstructor's own completeness audit is satisfied: an
    # explicit "not observed" record is a record, unlike a missing field
    assert chain["step_6_completeness"]["missing"] == [] \
        if "step_6_completeness" in chain else True


def test_a_partial_fill_reconstructs_as_a_partial(tmp_path):
    rec = RC.Recorder(str(tmp_path))
    legs = {"FL0": {"window": 3, "slot": 0, "price": "0.02",
                    "complemented": "0.05"}}
    st = view(filled="0.0010")
    st["orders"] = [
        dict(order("FL0", side=DOWN, cum="0.05",
                   state="ORDER_STATE_PARTIALLY_FILLED"),
             partially_filled=True, leaves_quantity="0.08"),
        order("CP0", side=UP, cum="0.05", own="0.94")]
    AR.emit_attempt_rows(rec, lifecycle=FakeLifecycle(legs), order_state=st,
                         market=SLUG, ws_state=WS,
                         complement_ids={"FL0": "CP0"}, windows_in_scope={3})
    s1 = RC.reconstruct_chain(rec)["step_1_first_leg_maker_fill_rate"]
    assert s1["placements"] == 1
    assert s1["fills"] == 1
    assert s1["partial_fills"] == 1


def test_an_unfilled_first_leg_has_no_pair_cost(tmp_path):
    rec = RC.Recorder(str(tmp_path))
    legs = {"FL0": {"window": 3, "slot": 0, "price": "0.02",
                    "complemented": "0"}}
    st = view()
    st["orders"] = [order("FL0", side=DOWN, cum="0",
                          state="ORDER_STATE_CANCELED")]
    AR.emit_attempt_rows(rec, lifecycle=FakeLifecycle(legs), order_state=st,
                         market=SLUG, ws_state=WS, windows_in_scope={3})
    row = rec.read(RC.ATTEMPTS)[0]
    assert row["outcome"]["state"] == "NO_FILL"
    assert row["outcome"]["realized_pair_cost_usd_per_share"] is None


def test_a_filled_leg_with_an_unfilled_complement_is_RESIDUAL(tmp_path):
    rec = RC.Recorder(str(tmp_path))
    legs = {"FL0": {"window": 3, "slot": 0, "price": "0.02",
                    "complemented": "0.13"}}
    resid = {"FL0": {"filled_qty": "0.13", "duration_s": 42.0}}
    st = view(filled="0.0026")
    st["orders"] = [order("FL0", side=DOWN, cum="0.13"),
                    dict(order("CP0", side=UP, cum="0", own="0.94",
                               state="ORDER_STATE_CANCELED"))]
    AR.emit_attempt_rows(rec, lifecycle=FakeLifecycle(legs, residuals=resid),
                         order_state=st, market=SLUG, ws_state=WS,
                         complement_ids={"FL0": "CP0"}, windows_in_scope={3})
    row = rec.read(RC.ATTEMPTS)[0]
    assert row["outcome"]["state"] == "RESIDUAL"
    assert row["outcome"]["realized_pair_cost_usd_per_share"] is None
    assert row["outcome"]["residual_shares"] == "0.13"


def test_nothing_unobserved_is_fabricated(tmp_path):
    rec = RC.Recorder(str(tmp_path))
    legs, st, comp = three_attempt_state()
    AR.emit_attempt_rows(rec, lifecycle=FakeLifecycle(legs), order_state=st,
                         market=SLUG, ws_state=WS, complement_ids=comp,
                         windows_in_scope={3})
    row = rec.read(RC.ATTEMPTS)[0]
    assert row["queue_state"]["observed"] is False
    assert row["markouts"]["observed"] is False
    # and no number was invented in their place. `bool` is an `int` in
    # Python, so the flag itself is excluded rather than counted.
    assert not any(isinstance(v, (int, float)) and not isinstance(v, bool)
                   for v in row["queue_state"].values())
    assert not any(isinstance(v, (int, float)) and not isinstance(v, bool)
                   for v in row["markouts"].values())
    assert row["outcome"]["fees"]["disclosed"] is False
    assert row["REALIZED_CLAIM"] is False


def test_an_attempt_row_carries_inventory_before_and_after(tmp_path):
    rec = RC.Recorder(str(tmp_path))
    legs, st, comp = three_attempt_state()
    AR.emit_attempt_rows(rec, lifecycle=FakeLifecycle(legs), order_state=st,
                         market=SLUG, ws_state=WS, complement_ids=comp,
                         windows_in_scope={3})
    row = rec.read(RC.ATTEMPTS)[0]
    after = row["extra"]["inventory_after"]
    assert after["established"] is True
    assert after["source"] == "VENUE_AUTHORITATIVE"
    assert row["extra"]["written_from"] == "run_cohort production path"


def test_legs_from_an_earlier_window_are_not_re_reported(tmp_path):
    rec = RC.Recorder(str(tmp_path))
    legs, st, comp = three_attempt_state()
    legs["OLD"] = {"window": 1, "slot": 0, "price": "0.50",
                   "complemented": "0"}
    out = AR.emit_attempt_rows(rec, lifecycle=FakeLifecycle(legs),
                               order_state=st, market=SLUG, ws_state=WS,
                               complement_ids=comp, windows_in_scope={3})
    assert out["n_written"] == 3
    assert any(s.get("order_id") == "OLD" for s in out["skipped"])


def test_inventory_snapshot_never_reads_a_failed_view_as_zero():
    snap = AR.inventory_snapshot(view(ok=False, reason="HTTPError:429"))
    assert snap["established"] is False
    assert snap["NEVER_READ_AS_ZERO"] is True
    assert "n_outstanding" not in snap


def test_a_refusal_is_logged_with_state_inventory_and_reason():
    seen = []

    class Rec:
        def record_event(self, *, window, slot, event, detail):
            seen.append({"event": event, "detail": detail})

    AR.record_decision(Rec(), window=3, slot=-1, event="DECISION_REFUSED",
                       market=SLUG, reason=["E9_PRICE_ABOVE_CEILING:0.62"],
                       order_state=view(filled="0.1248"),
                       snapshot={"first_leg_side": DOWN,
                                 "first_leg_price": "0.62",
                                 "qty": "0.13"},
                       envelope={"max_cum_first_leg_shares": "0.39"})
    assert len(seen) == 1
    d = seen[0]["detail"]
    assert d["reason"] == ["E9_PRICE_ABOVE_CEILING:0.62"]
    assert d["inventory_before"]["filled_notional_usd"] == "0.1248"
    assert d["market_state_observed"]["first_leg_price"] == "0.62"
    assert d["bounds_consulted"]["max_cum_first_leg_shares"] == "0.39"


def test_recording_failure_never_changes_what_the_engine_does():
    class Boom:
        def record_event(self, **kw):
            raise RuntimeError("tape is full")

    # must not raise
    AR.record_decision(Boom(), window=3, slot=-1, event="DECISION_REFUSED",
                       market=SLUG, reason="x", order_state=view())


def test_a_refusal_never_reaches_the_attempt_tape(tmp_path):
    """A refusal is not a position in SUBMITTED..REALIZED, and the
    recorder enforces that -- so refusals go to events, by design."""
    rec = RC.Recorder(str(tmp_path))
    with pytest.raises(RC.RecorderRefused):
        rec.record_attempt(window=3, slot=0, market=SLUG, ws_state=WS,
                           first_leg={}, queue={}, timestamps={}, fill={},
                           complement=None, outcome={}, cancellations=None,
                           markouts={}, label="REFUSED")
