"""The shadow two-sided evaluator: modelled, queue-disciplined, inert.

It must never submit, never touch the store, never move a limit, and never
call a touch a fill. Capital is deployed-and-committed.
"""
import inspect
import re
from decimal import Decimal as D

import pytest

from src import l5_maker_fill_model as FM
from src import l5_pmus_live_runner as R
from src import l5_two_sided_shadow as TS

SLUG = "cpc-btc-updown-15m-2026-10-15-0100z"


def snap(bid, ask, bsh="100", ash="100"):
    return {"bid": bid, "ask": ask, "bid_shares": bsh, "ask_shares": ash}


def prt(px, qty, dr, t=1.0):
    return {"t": t, "px": px, "qty": qty, "dir": dr, "trade_time": str(t)}


def cell(rep, th, env):
    return [c for c in rep["cells"]
            if c["threshold"] == th and c["envelope"] == env][0]


def ev(**kw):
    return TS.TwoSidedShadow(SLUG, window_start=0.0, **kw)


# ------------------------------------------------------------ inertness

def code_only(src):
    """Executable lines only. Prose about not submitting is not submitting."""
    out = []
    in_doc = False
    for line in src.splitlines():
        stripped = line.strip()
        if in_doc:
            if '"""' in stripped:
                in_doc = False
            continue
        if stripped.startswith('#'):
            continue
        if stripped.startswith('"""') or stripped.startswith('r"""'):
            if stripped.count('"""') == 1:
                in_doc = True
            continue
        out.append(line.split("#")[0])
    return "\n".join(out)


def test_the_module_cannot_submit_anything():
    """No executable line reaches a transport, a store or an order route.

    String LITERALS are allowed to say so -- the report's own label does --
    so they are stripped alongside comments and docstrings.
    """
    src = re.sub(r'"[^"\n]*"', '""', code_only(inspect.getsource(TS)))
    for forbidden in ("submit", "transport", "InventoryStore", ".store",
                      "arm(", "cancel("):
        assert forbidden not in src, forbidden


def test_the_runner_only_reads_the_book_and_the_prints():
    src = code_only(inspect.getsource(R.Runner.two_sided_advance))
    assert "self.book.snapshot" in src and "self.book.trades" in src
    assert "submit" not in src and "self.store" not in src


def test_the_live_envelope_is_always_reported_beside_the_test_ones():
    rep = ev().report(None)
    envs = {c["envelope"] for c in rep["cells"]}
    assert envs == {"100", "200", "300", "500", "1"}
    assert {c["threshold"] for c in rep["cells"]} == {"0.96", "0.97", "0.98", "0.99"}


# ------------------------------------------------------- queue discipline

def test_a_touch_is_not_a_fill():
    e = ev()
    e.step(snap("0.49", "0.50"), [], 1.0)          # quote goes out
    e.step(snap("0.49", "0.50"), [], 2.0)          # price sits there, no prints
    c = cell(e.report(None), "0.99", "100")
    assert c["flat_quotes"] == 1
    assert c["filled_shares_conservative"] == "0"


def test_volume_inside_the_queue_ahead_fills_nothing():
    e = ev()
    e.step(snap("0.49", "0.50"), [], 1.0)
    # 90 shares sold into a 100-share queue ahead of us
    e.step(snap("0.49", "0.50"), [prt("0.49", "90", "sell", 2.0)], 2.0)
    c = cell(e.report(None), "0.99", "100")
    assert c["filled_shares_conservative"] == "0"


def test_volume_beyond_the_queue_fills_the_excess_as_a_partial():
    e = ev()
    e.step(snap("0.49", "0.50", "100", "100"), [], 1.0)
    e.step(snap("0.49", "0.50", "100", "100"), [prt("0.49", "100.40", "sell", 2.0)], 2.0)
    c = cell(e.report(None), "0.99", "100")
    assert D(c["up"]) == D("0.40")               # 100.40 - 100 queue
    assert D(c["dn"]) == D(0)
    assert c["partial_legs"] >= 1


def test_the_wrong_direction_never_fills_a_leg():
    e = ev()
    e.step(snap("0.49", "0.50"), [], 1.0)
    # a BUY lifting the ask cannot fill our resting BID
    e.step(snap("0.49", "0.50"), [prt("0.49", "500", "buy", 2.0)], 2.0)
    c = cell(e.report(None), "0.99", "100")
    assert D(c["up"]) == D(0)


def test_an_unknown_queue_never_fills_conservatively():
    e = ev()
    e.step({"bid": "0.49", "ask": "0.50"}, [], 1.0)        # no displayed size
    e.step({"bid": "0.49", "ask": "0.50"}, [prt("0.49", "9999", "sell", 2.0)], 2.0)
    c = cell(e.report(None), "0.99", "100")
    assert D(c["up"]) == D(0)
    assert D(c["filled_shares_optimistic"]) > 0            # the band is reported


def test_both_legs_fill_when_both_sides_are_crossed():
    e = ev()
    e.step(snap("0.49", "0.50", "10", "10"), [], 1.0)
    e.step(snap("0.49", "0.50", "10", "10"),
           [prt("0.49", "500", "sell", 2.0), prt("0.50", "500", "buy", 2.1)], 2.0)
    c = cell(e.report(None), "0.99", "100")
    assert D(c["matched_shares"]) > 0
    assert D(c["matched_pnl_modelled"]) > 0            # pair cost 0.99 < 1.00


# ----------------------------------------------------------- the gate

def test_a_wide_pair_cost_is_refused_by_the_tight_thresholds():
    e = ev()
    e.step(snap("0.49", "0.50"), [], 1.0)             # spread 0.01 -> 0.99
    rep = e.report(None)
    assert cell(rep, "0.99", "100")["flat_quotes"] == 1
    for th in ("0.96", "0.97", "0.98"):
        assert cell(rep, th, "100")["flat_quotes"] == 0
        assert cell(rep, th, "100")["eligible_ticks"] == 0


def test_a_locked_book_is_not_quotable():
    e = ev()
    e.step(snap("0.50", "0.50"), [], 1.0)
    assert e.two_sided_ticks == 0
    assert cell(e.report(None), "0.99", "100")["flat_quotes"] == 0


def test_only_the_complement_is_quoted_while_inventory_is_one_sided():
    e = ev()
    e.step(snap("0.49", "0.50", "10", "10"), [], 1.0)
    e.step(snap("0.49", "0.50", "10", "10"), [prt("0.49", "500", "sell", 2.0)], 2.0)
    c0 = cell(e.report(None), "0.99", "100")
    assert D(c0["up"]) > 0 and D(c0["dn"]) == 0
    # the touch moves to where completion is still economic:
    # basis 0.49 + DOWN touch (1 - 0.56) = 0.93 <= 0.99
    e.step(snap("0.55", "0.56", "10", "10"), [], 3.0)
    c = cell(e.report(None), "0.99", "100")
    assert c["complement_quotes"] >= 1
    assert c["flat_quotes"] == 1                        # no second opening leg


def test_an_uneconomic_completion_is_refused_however_long_it_waits():
    """basis 0.49 + a DOWN touch of 0.59 is 1.08: a locked loss, refused."""
    e = ev()
    e.step(snap("0.49", "0.50", "10", "10"), [], 1.0)
    e.step(snap("0.49", "0.50", "10", "10"), [prt("0.49", "500", "sell", 2.0)], 2.0)
    for i in range(5):
        e.step(snap("0.40", "0.41", "10", "10"), [], 3.0 + i)
    c = cell(e.report(None), "0.99", "100")
    assert c["complement_quotes"] == 0
    assert D(c["residual_shares"]) > 0                  # carried, not forced


# --------------------------------------------------------------- capital

def test_capital_is_deployed_plus_committed_and_bounded_by_the_envelope():
    e = ev()
    e.step(snap("0.49", "0.50", "10000", "10000"), [], 1.0)
    for env in ("1", "100", "200", "300", "500"):
        c = cell(e.report(None), "0.99", env)
        assert D(c["peak_exposure"]) <= D(env)
        assert c["envelope_binding"] is True


def test_the_one_dollar_live_envelope_quotes_about_one_pair():
    e = ev()
    e.step(snap("0.49", "0.50", "10000", "10000"), [], 1.0)
    c = cell(e.report(None), "0.99", "1")
    assert D(c["quoted_shares"]) == D("2.02")        # 1.01 per side
    assert D(c["peak_exposure"]) <= D(1)


def test_a_clip_is_never_larger_than_the_displayed_size():
    e = ev()
    e.step(snap("0.49", "0.50", "3", "3"), [], 1.0)
    c = cell(e.report(None), "0.99", "500")
    assert D(c["quoted_shares"]) == D("6")          # 3 per side, not 505


# ------------------------------------------------------------ settlement

def test_residual_is_not_scored_until_the_outcome_is_known():
    e = ev()
    e.step(snap("0.49", "0.50", "10", "10"), [], 1.0)
    e.step(snap("0.49", "0.50", "10", "10"), [prt("0.49", "500", "sell", 2.0)], 2.0)
    unscored = cell(e.report(None), "0.99", "100")
    assert unscored["residual_pnl_modelled"] is None
    assert unscored["net_pnl_modelled"] is None
    scored = cell(e.report(True), "0.99", "100")
    assert scored["residual_pnl_modelled"] is not None


def test_a_matched_pair_redeems_exactly_one_dollar():
    e = ev()
    e.step(snap("0.49", "0.50", "10", "10"), [], 1.0)
    e.step(snap("0.49", "0.50", "10", "10"),
           [prt("0.49", "500", "sell", 2.0), prt("0.50", "500", "buy", 2.1)], 2.0)
    for up_won in (True, False):
        c = cell(e.report(up_won), "0.99", "100")
        m = D(c["matched_shares"])
        assert D(c["matched_pnl_modelled"]) == m * D(1) - D(c["matched_cost"])


def test_every_report_says_it_is_modelled():
    rep = ev().report(None)
    assert "MODELLED" in rep["cells"][0]["labels"]
    assert "not realized" in rep["cells"][0]["labels"]
    assert "frozen" in rep["discipline"]


# ------------------------------------------------------------- coverage

def test_prints_are_anchored_by_row_identity_not_by_index():
    e = ev()
    rows = [prt("0.49", "1", "sell", float(i)) for i in range(3)]
    e.step(snap("0.49", "0.50"), rows, 1.0)
    assert e.prints_consumed == 3
    e.step(snap("0.49", "0.50"), rows, 2.0)          # same rows again
    assert e.prints_consumed == 3                    # nothing double-counted


def test_a_rolled_buffer_is_counted_as_a_coverage_gap():
    e = ev()
    e.step(snap("0.49", "0.50"), [prt("0.49", "1", "sell", 1.0)], 1.0)
    e.step(snap("0.49", "0.50"), [prt("0.49", "1", "sell", 99.0)], 2.0)
    assert e.coverage_gaps == 1


# ============================================================ P0: scoring
# The 0130z defect: scoring hung off sweep_settlements, which only fires for
# markets the REAL ledger holds exposure in. In shadow it holds none, so a
# window with residual would never be scored. These pin the replacement.

import os
import tempfile

from decimal import Decimal as D


def window(up, dn, up_cost, dn_cost, slug="cpc-btc-updown-15m-2026-10-15-0200z"):
    """An unscored parked report with one cell carrying known inventory."""
    up, dn = D(str(up)), D(str(dn))
    uc, dc = D(str(up_cost)), D(str(dn_cost))
    m = min(up, dn)
    bu = (uc / up) if up else D(0)
    bd = (dc / dn) if dn else D(0)
    resid = up - dn
    rcost = (resid * bu) if resid > 0 else (-resid * bd)
    return {
        "LABEL": "L5_TWO_SIDED_SHADOW", "market": slug,
        "cells": [{
            "threshold": "0.99", "envelope": "100",
            "up": str(up), "dn": str(dn),
            "matched_shares": str(m),
            "matched_cost": str(m * (bu + bd)),
            "matched_pnl_modelled": str(m * D(1) - m * (bu + bd)),
            "residual_shares": str(resid), "residual_cost": str(rcost),
            "residual_pnl_modelled": None, "net_pnl_modelled": None,
        }],
    }


def only(rep):
    return rep["cells"][0]


def ledger():
    d = tempfile.mkdtemp()
    return TS.ShadowLedger(os.path.join(d, "two_sided_shadow.json"))


def test_a_losing_one_sided_fill_is_scored_as_a_loss():
    """10 UP at 0.59, DOWN settles. Stage A's shape exactly."""
    rep = TS.score_report(window(10, 0, "5.90", "0"), settle_up=False)
    c = only(rep)
    assert D(c["residual_pnl_modelled"]) == D("-5.90")
    assert D(c["net_pnl_modelled"]) == D("-5.90")
    assert c["state"] == TS.FINALIZED


def test_a_winning_one_sided_fill_is_scored_as_a_gain():
    rep = TS.score_report(window(10, 0, "5.90", "0"), settle_up=True)
    c = only(rep)
    assert D(c["residual_pnl_modelled"]) == D("4.10")      # 10 x (1 - 0.59)
    assert D(c["net_pnl_modelled"]) == D("4.10")


def test_a_one_sided_DOWN_fill_is_scored_from_its_own_side():
    rep = TS.score_report(window(0, 10, "0", "4.00"), settle_up=False)
    c = only(rep)
    assert D(c["residual_pnl_modelled"]) == D("6.00")      # DOWN won: 10 - 4.00
    rep2 = TS.score_report(window(0, 10, "0", "4.00"), settle_up=True)
    assert D(only(rep2)["residual_pnl_modelled"]) == D("-4.00")


def test_a_completed_matched_pair_settles_the_same_either_way():
    for up_won in (True, False):
        rep = TS.score_report(window(10, 10, "4.90", "4.90"), settle_up=up_won)
        c = only(rep)
        assert D(c["residual_pnl_modelled"]) == D(0)
        assert D(c["net_pnl_modelled"]) == D("0.20")       # 10 x (1 - 0.98)


def test_missing_settlement_leaves_the_window_pending_and_never_zero():
    led = ledger()
    rec = led.park(window(10, 0, "5.90", "0"), now=1000.0)
    assert rec["state"] == TS.PENDING
    assert only(rec)["net_pnl_modelled"] is None
    assert only(rec)["residual_pnl_modelled"] is None
    assert led.pending_slugs() == ["cpc-btc-updown-15m-2026-10-15-0200z"]
    # nothing was finalized, so nothing contributes to a total
    assert led.state["finalized"] == {}


def test_a_window_that_never_settles_becomes_ERROR_not_zero():
    led = ledger()
    led.park(window(10, 0, "5.90", "0"), now=1000.0)
    assert led.expire(1000.0 + TS.SETTLE_DEADLINE_S - 1) == []
    out = led.expire(1000.0 + TS.SETTLE_DEADLINE_S + 1)
    assert len(out) == 1
    assert out[0]["state"] == TS.ERROR
    assert out[0]["state_reason"] == "SETTLEMENT_NOT_OBSERVED"
    assert only(out[0])["net_pnl_modelled"] is None
    assert led.pending_slugs() == []


def test_scoring_is_idempotent_and_survives_a_restart():
    led = ledger()
    slug = "cpc-btc-updown-15m-2026-10-15-0200z"
    led.park(window(10, 0, "5.90", "0"), now=1000.0)
    first = led.finalize(slug, settle_up=False)
    assert D(only(first)["net_pnl_modelled"]) == D("-5.90")
    assert led.finalize(slug, settle_up=False) is None        # same process
    reopened = TS.ShadowLedger(led.path)                      # restart
    assert reopened.pending_slugs() == []
    assert reopened.finalize(slug, settle_up=False) is None   # across restart
    assert reopened.finalize(slug, settle_up=True) is None    # even flipped
    assert D(only(reopened.state["finalized"][slug])["net_pnl_modelled"]) == D("-5.90")


def test_re_parking_a_finalized_window_does_not_reopen_it():
    led = ledger()
    slug = "cpc-btc-updown-15m-2026-10-15-0200z"
    led.park(window(10, 0, "5.90", "0"), now=1000.0)
    led.finalize(slug, settle_up=False)
    again = led.park(window(99, 0, "50", "0"), now=2000.0)
    assert again["state"] == TS.FINALIZED
    assert led.pending_slugs() == []
    assert D(only(led.state["finalized"][slug])["net_pnl_modelled"]) == D("-5.90")


def test_a_corrupt_sidecar_starts_empty_rather_than_raising():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "two_sided_shadow.json")
    with open(path, "w") as fh:
        fh.write("{not json")
    led = TS.ShadowLedger(path)
    assert led.pending_slugs() == [] and led.state["finalized"] == {}


def test_the_pending_map_is_bounded_oldest_first():
    led = ledger()
    for i in range(led.max_pending + 3):
        led.park(window(1, 0, "0.5", "0", slug="m%02d" % i), now=1000.0 + i)
    assert len(led.pending_slugs()) == led.max_pending
    assert "m00" not in led.pending_slugs()
    assert "m%02d" % (led.max_pending + 2) in led.pending_slugs()


def test_the_runner_settles_without_any_live_exposure():
    """The defect in one line: scoring must not be reachable only through
    the real ledger's settlement sweep."""
    import inspect
    src = code_only(inspect.getsource(R.Runner.two_sided_settle))
    assert "transport" in src or "self.transport" in src
    assert "sweep_settlements" not in src
    sweep = code_only(inspect.getsource(R.Runner.sweep_settlements))
    assert "two_sided" not in sweep
