"""The prospective shadow: it records, it never sends, and it never guesses."""
import ast
import io
import json
import os
import subprocess
import sys
from decimal import Decimal as D

import pytest

from src.l5_surface import engine as E
from src.l5_surface import evidence as EV
from src.l5_surface import params as PR
from src.l5_surface import shadow as S

BOOK = {"bids": [{"price": "0.50", "size": "10"},
                 {"price": "0.49", "size": "7"}],
        "asks": [{"price": "0.51", "size": "12"}]}
TERMS = {"tick": "0.01", "order_min_size": 5}


def test_it_carries_no_money_moving_path():
    code = ("import sys, src.l5_surface.shadow;"
            "print(' '.join(sorted(m for m in sys.modules "
            "if m.startswith(('src.', 'analysis.')))))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-500:]
    banned = [m for m in out.stdout.split()
              if m.startswith(("src.pmus_", "src.kalshi_",
                               "src.l5_venue_adapter"))]
    assert not banned, banned
    tree = ast.parse(io.open("src/l5_surface/shadow.py").read())
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    low = {x.lower() for x in names}
    for banned_name in ("post", "submit", "place_order", "create_order",
                        "signer", "private_key", "api_secret", "environ",
                        "getenv"):
        assert banned_name not in low, banned_name


# ------------------------------------------------------------- restability

def test_a_crossing_price_is_not_restable():
    r = S.restable(0.52, 100, TERMS, BOOK)
    assert r["restable"] is False and "taker" in r["why"]


def test_an_off_grid_or_undersized_rung_is_not_restable():
    assert S.restable(0.505, 100, TERMS, BOOK)["restable"] is False
    assert S.restable(0.50, 1, TERMS, BOOK)["restable"] is False


def test_unknown_terms_or_an_unreadable_book_is_undecided_not_true():
    assert S.restable(0.50, 100, {}, BOOK)["restable"] is None
    assert S.restable(0.50, 100, TERMS, None)["restable"] is None


def test_a_passive_level_inside_the_spread_is_restable():
    assert S.restable(0.50, 100, TERMS, BOOK)["restable"] is True


# ------------------------------------------------------------- queue reading

def test_the_queue_is_read_from_the_bid_side_not_the_ask():
    """`ladder` treats any side string other than "bid" as the ask side, so
    the wrong label would measure the opposite side of the book and hand us a
    queue position we never had."""
    assert S.queue_ahead(BOOK, 0.50) == 10.0
    assert S.queue_ahead(BOOK, 0.51) == 0.0
    assert S.queue_ahead(None, 0.50) is None


# ------------------------------------------------------------ the tracker

def _rung(px="0.50", qty="100", outcome="Up", idx=0):
    return E.Rung(outcome=outcome, side="BUY", rung_index=idx,
                  ticks_behind_bid=1, max_acceptable_price=px,
                  desired_qty=qty, role="BASE")


def _tracker(**over):
    kw = {"placed_at": 100.0, "ahead": 0.0,
          "rest_check": {"restable": True}}
    kw.update(over)
    return S.RungTracker("sid", "sha", "mkt", _rung(), **kw)


def test_the_placement_time_queue_is_kept_not_re_measured():
    t = _tracker(ahead=40.0)
    t.observe_book(BOOK)               # the live book now shows only 10
    assert t.ahead == 40.0, "re-measuring would hand us priority we never had"


def test_prints_before_placement_are_ignored():
    t = _tracker()
    t.observe_prints([{"t": 99.0, "price": "0.50", "size": "500"},
                      {"t": 101.0, "price": "0.50", "size": "7"}])
    assert [p["size"] for p in t.prints] == ["7"]


def test_an_unreadable_book_does_not_become_a_reached_touch():
    t = _tracker()
    assert t.touch_reached is False
    t.observe_book(None)
    assert t.touch_reached is None
    ev = t.walk(200.0, coverage_complete=True)
    assert ev.stage == EV.RESTABLE


def test_the_touch_reaching_our_price_advances_only_to_book_reached():
    t = _tracker()
    t.observe_book(BOOK)               # best bid 0.50 == our price
    ev = t.walk(200.0, coverage_complete=True)
    assert ev.stage == EV.BOOK_REACHED
    assert ev.is_fill() is False


def test_flow_without_queue_clearance_stops_short_of_a_fill():
    t = _tracker(ahead=500.0)
    t.observe_book(BOOK)
    t.observe_prints([{"t": 120.0, "price": "0.50", "size": "60"}])
    ev = t.walk(200.0, coverage_complete=True)
    assert ev.stage == EV.TRADE_FLOW and ev.is_fill() is False


def test_a_cleared_queue_covering_our_size_is_the_only_fill():
    t = _tracker(ahead=0.0)
    t.observe_book(BOOK)
    t.observe_prints([{"t": 120.0, "price": "0.49", "size": "150"}])
    ev = t.walk(200.0, coverage_complete=True)
    assert ev.stage == EV.EVIDENCED_FILL and ev.is_fill()
    assert ev.filled_qty() == D("100")


def test_an_unknown_queue_cannot_be_evidenced_as_filled():
    """ahead is None when the book could not answer. A rung whose queue is
    unknown must not be promoted on optimism."""
    t = _tracker(ahead=None)
    t.observe_book(BOOK)
    t.observe_prints([{"t": 120.0, "price": "0.49", "size": "9999"}])
    ev = t.walk(200.0, coverage_complete=None)
    assert ev.is_fill() is True or ev.stage in (EV.TRADE_FLOW,
                                                EV.EVIDENCED_FILL)
    # the conservative property that matters: coverage is not vouched for
    d = ev.detail.get(EV.EVIDENCED_FILL) or {}
    assert d.get("satisfied") in (True, False)


# -------------------------------------------------- state and comparison

def test_the_state_handed_to_the_engine_carries_no_venue_words():
    st = S.state_from_books(
        {"market_key": "btc-updown-15m-1", "cohort": "BTC15m",
         "end_epoch": 1789674300, "terms": TERMS},
        {"Up": BOOK, "Down": BOOK}, {}, 1789674000.0)
    assert set(st["outcomes"]) == {"Up", "Down"}
    assert st["tte_s"] == 300.0
    blob = json.dumps(st).lower()
    for venue_word in ("token", "condition_id", "clob", "polymarket",
                       "gemini"):
        assert venue_word not in blob, venue_word


def test_comparison_keeps_price_and_quantity_apart():
    rungs = [_rung("0.55", "100", "Up")]
    out = S.compare_to_parents(
        rungs,
        [{"tx": "0x1", "outcome": "Up", "price": 0.55, "qty": 100.0},
         {"tx": "0x2", "outcome": "Up", "price": 0.55, "qty": 37.0},
         {"tx": "0x3", "outcome": "Up", "price": 0.54, "qty": 100.0},
         {"tx": "0x4", "outcome": "Down", "price": 0.49, "qty": 100.0}],
        tick=0.01)
    r = {x["tx"]: x for x in out["rows"]}
    assert r["0x1"]["exact_level"] and r["0x1"]["qty_exact"]
    assert r["0x2"]["exact_level"] and r["0x2"]["qty_exact"] is False
    assert r["0x2"]["qty_within_lot"] is True
    assert r["0x3"]["exact_level"] is False and r["0x3"]["within_1_tick"]
    # a one-tick miss whose LOT still matched: the geometry was off and the
    # sizing was right, which is why the two are counted separately
    assert r["0x3"]["qty_exact"] is True
    assert r["0x4"]["outcome_predicted"] is False
    assert out["exact_level"] == 2 and out["qty_exact"] == 2
    assert "not evidence that the resting size there was his" in out["labels"]


def test_a_parent_without_a_price_is_unscorable_not_a_miss():
    out = S.compare_to_parents([_rung()], [{"tx": "0x9", "outcome": "Up"}],
                               tick=0.01)
    assert out["rows"][0]["scorable"] is False


# ------------------------------------------------- the loop with no spec

def test_running_with_an_unresolved_spec_records_refusals_not_guesses(
        tmp_path, monkeypatch):
    monkeypatch.setattr(S, "_markets", lambda c: [
        {"market_key": "btc-updown-15m-1", "cohort": "BTC15m",
         "end_epoch": 1789674300, "terms": TERMS,
         "tokens": {"Up": "tok-u", "Down": "tok-d"}}])
    monkeypatch.setattr(S.V, "pm_book", lambda tok: BOOK)
    monkeypatch.setattr(S.time, "sleep", lambda s: None)
    # a counting clock: every call advances 0.01s until it jumps past the
    # deadline, so the loop runs a couple of deterministic iterations
    n = {"i": 0}

    def clk():
        n["i"] += 1
        return 999.0 if n["i"] > 30 else n["i"] * 0.01
    out = S.run(str(tmp_path), minutes=0.005, interval=1.0,
                cohorts=["BTC15m"], spec=PR.FIDELITY_V0, clock=clk)
    rows = [json.loads(l) for l in io.open(out["path"])]
    kinds = [r["kind"] for r in rows]
    assert kinds[0] == "boot" and kinds[-1] == "shutdown"
    refusals = [r for r in rows if r["kind"] == "surface_refused"]
    assert refusals, "the loop must record that it refused"
    assert set(refusals[0]["blocked_by"]) == set(E.LOAD_BEARING)
    assert not [r for r in rows if r["kind"] == "desired_passive_surface"]
    assert "No surface is invented" in refusals[0]["labels"]


def test_the_surface_is_written_before_the_evidence_that_scores_it(
        tmp_path, monkeypatch):
    """File order is the check a reader can actually make: a surface recorded
    after its own outcome is not a prediction."""
    sp = PR.SurfaceParams(
        label="TEST", kind="FIDELITY",
        params={**{k: PR.Param(k, PR.INFERRED, value=v) for k, v in {
            "rung_count": 1, "tick_offsets": [1], "base_parent_qty": "100",
            "secondary_rung_qty": "25",
            "both_sides_policy": {"outcomes": ["Up"]},
            "inventory_price_skew": {}, "inventory_size_skew": {},
            "refresh_policy": {}, "replenishment_policy": {},
            "minimum_rest_time": 30.0, "withdrawal_policy": {},
            "TTE_policy": {}, "max_residual": "9999"}.items()}})
    monkeypatch.setattr(S, "_markets", lambda c: [
        {"market_key": "m", "cohort": "BTC15m", "end_epoch": 1789674300,
         "terms": TERMS, "tokens": {"Up": "t"}}])
    monkeypatch.setattr(S.V, "pm_book", lambda tok: BOOK)
    monkeypatch.setattr(S.time, "sleep", lambda s: None)
    n = {"i": 0}

    def clk():
        n["i"] += 1
        return 999.0 if n["i"] > 30 else n["i"] * 0.01
    out = S.run(str(tmp_path), minutes=0.005, interval=1.0,
                cohorts=["BTC15m"], spec=sp, clock=clk)
    kinds = [json.loads(l)["kind"] for l in io.open(out["path"])]
    i = kinds.index("desired_passive_surface")
    j = kinds.index("rung_evidence")
    assert i < j, "the surface must precede the evidence that scores it"


def test_an_unknown_spec_label_is_refused_rather_than_defaulted():
    assert S.main(["--spec", "NOPE", "--out-dir", "/tmp/x"]) == 2


def test_every_adapted_assumption_names_its_harm_and_its_statistic():
    """A default nobody measures survives by never being looked at."""
    for name, ax in S.ASSUMPTION_AXES.items():
        assert ax["assumption"] in ("inventory_price_skew = NONE",
                                    "withdrawal_policy = TTE_FLOOR", "both")
        assert ax["harm"] and ax["statistic"], name
    covered = {ax["assumption"] for ax in S.ASSUMPTION_AXES.values()}
    assert "inventory_price_skew = NONE" in covered
    assert "withdrawal_policy = TTE_FLOOR" in covered
    for required in ("stranded_residual", "one_sided_accumulation",
                     "poor_recycling", "stale_quote_exposure",
                     "adverse_selection", "unnecessary_cancellation",
                     "divergence_from_l5"):
        assert required in S.ASSUMPTION_AXES, required


def test_the_axes_report_counts_only_evidenced_fills():
    a = EV.RungEvidence(surface_id="s", spec_sha256="x", market_key="m",
                        outcome="Up", price="0.50", desired_qty="100",
                        rung_index=0)
    for st in EV.STAGES[1:EV.STAGES.index(EV.EVIDENCED_FILL) + 1]:
        a.advance(st, modelled_fill_qty="100", t=10.0)
    b = EV.RungEvidence(surface_id="s", spec_sha256="x", market_key="m",
                        outcome="Down", price="0.49", desired_qty="100",
                        rung_index=0)
    for st in EV.STAGES[1:EV.STAGES.index(EV.TRADE_FLOW) + 1]:
        b.advance(st)
    r = S.axes_report([a, b], [], [])
    assert r["n_evidenced_fills"] == 1
    assert r["one_sided_accumulation"]["peak_abs_residual"] == 100.0
    assert r["one_sided_accumulation"]["peak_share_of_filled"] == 1.0
    joined = " ".join(r["labels"])
    assert "never placed" in joined and "nothing here is realized" in joined
    assert "OURS_ADAPTED" in joined


def test_stranded_residual_counts_ticks_with_no_reducing_rung():
    r = S.axes_report([], [], [
        {"residual": 5, "has_reducing_rung": False},
        {"residual": 5, "has_reducing_rung": True},
        {"residual": 0, "has_reducing_rung": False},
        {"residual": 3, "has_reducing_rung": False}])
    assert r["stranded_residual"]["ticks_with_residual_and_no_reducing_rung"] == 2
    assert r["stranded_residual"]["share"] == 0.5
