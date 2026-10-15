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
