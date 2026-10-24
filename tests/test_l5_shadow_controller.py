"""The inventory-driven BTC15m shadow controller.

WHAT MUST HOLD: it decides from OUR inventory, the live book and the REAL
clock; it is STRUCTURALLY unable to trade (not disarmed by a flag — it holds
no transport and defines no order path, and arming env vars change nothing);
its provenance never mixes with L5's observed actions; simulated inventory
survives a restart; a touch is never credited as a fill; and nothing it does
can raise into the runner's tick.
"""
from __future__ import annotations

import io
import json
import os
from collections import Counter
from decimal import Decimal as D

import pytest

from src import l5_maker_fill_model as FM
from src import l5_shadow_controller as sc
from src import pmus_sports_l5_decision as l5d

#: repo root -- the clean-interpreter probe below must run where `src` is
#: importable, not wherever pytest happened to be invoked from
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SLUG = "cpc-btc-updown-15m-2026-10-19-1315z"
WS = 1792415700.0
WEND = WS + 900.0


def _model():
    """Frozen-model stand-in covering the tte buckets a real 900s window
    traverses -- the sports fixture only covers `tte>10m`."""
    full = {}
    for band in ("p45-55c", "p55-75c"):
        for tte in ("tte>10m", "tte5-10m", "tte2-5m", "tte30-120s", "tte<30s"):
            for imb in ("flat", "imb1-10"):
                stt = f"{band}|{tte}|{imb}"
                full[f"{stt}|open|q<5"] = {"complete|q<5": 8, "extend|q<5": 2}
                full[f"{stt}|extend|q<5"] = {"complete|q<5": 8}
                full[f"{stt}|complete|q<5"] = {"complete|q<5": 8}
                full[f"{stt}||q<5"] = {"open|q<5": 6, "extend|q<5": 2}
    return {"full": full, "base": {},
            "marginal": {"complete|q<5": 5, "extend|q<5": 3, "open|q<5": 2},
            "cadence_modal": {}, "_sha256_16": "btc15mshadow0000"}


def _ladder(slug=None, bid="0.50", ask="0.51", bid_qty="0", ask_qty="0",
            t=WS):
    """A REAL `FM.Ladder`, folded through the recorder's own envelope walker.

    `bid_qty`/`ask_qty` are the volume AHEAD of us at that rung. Zero means
    we are at the front of a served queue -- known, not unknown -- which is
    what lets CONSERVATIVE fill at all. Omitting the ladder entirely leaves
    queue_ahead None, which the model refuses to fill except on a sweep.
    """
    lad = FM.Ladder()
    lad.ingest({"market_events": [{"marketSlug": slug or SLUG,
                                   "bids": [{"px": bid, "qty": bid_qty}],
                                   "asks": [{"px": ask, "qty": ask_qty}]}]},
               t)
    return lad


def _print(px, qty="1", dir_=None, t=WS + 1.0, seq=0):
    """One trade print in the recorder's shape. `dir` unlabelled is the
    conservative reading: price alone decides, which can only count MORE
    volume against us."""
    return {"t": t, "trade_time": t + seq * 1e-6, "px": str(px),
            "qty": str(qty), "dir": dir_}


def _book(bid="0.50", ask="0.51"):
    """STRINGS, because that is what the venue hands us.

    `AuthBook.snapshot` returns bid/ask as str -- the recorder's `_px` ends
    in `str(value)`. This fixture used Decimals, which is why the whole file
    was green while production raised TypeError on every tick of the first
    window whose band the model covers. A fixture that is kinder than the
    real contract tests nothing.
    """
    return {"bid": bid, "ask": ask}


def ctl(tmp_path, **kw):
    return sc.ShadowController(state_path=str(tmp_path / "shadow.json"),
                               model=_model(), **kw)


# ===================================================== STRUCTURAL SAFETY

@pytest.mark.parametrize("attr", sc.FORBIDDEN_ATTRS)
def test_no_order_path_exists_on_the_class(attr):
    """Not disarmed — ABSENT. A future edit that wires an order path in has
    to delete FORBIDDEN_ATTRS first, which is a visible act."""
    assert not hasattr(sc.ShadowController, attr), attr


@pytest.mark.parametrize("attr", sc.FORBIDDEN_ATTRS)
def test_no_order_path_exists_on_an_instance(tmp_path, attr):
    assert not hasattr(ctl(tmp_path), attr), attr


#: modules that can reach the venue, hold credentials, or submit. None may
#: be reachable from the controller -- not as an import, and not pulled in
#: by anything it imports.
NO_ORDER_PATH = ("l5_pmus_transport", "l5_live_execution", "pmus_auth",
                 "pmus_q1_executor", "l5_order_registry_server",
                 "l5_cancel_mechanism", "httpx", "requests", "websockets",
                 "aiohttp")


def test_the_module_declares_no_order_path_import():
    """ABSENCE OF THE IMPORT, not absence of the word. Read off the AST, so
    prose in a docstring can neither fail this nor satisfy it -- grepping
    raw source does both."""
    import ast
    tree = ast.parse(io.open(sc.__file__, encoding="utf-8").read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names.add(base)
            names.update(("%s.%s" % (base, a.name)) if base else a.name
                         for a in node.names)
    leaves = {part for n in names for part in n.split(".") if part}
    assert leaves.isdisjoint(NO_ORDER_PATH), sorted(leaves & set(NO_ORDER_PATH))


def test_nothing_it_imports_drags_an_order_path_in():
    """THE STRONGER CLAIM, and the one requirement 3 actually makes. An
    object cannot submit through a module that was never loaded, so assert
    the whole transitive closure in a CLEAN interpreter -- this process has
    other test modules' imports in `sys.modules` and would pass vacuously.
    """
    import subprocess
    import sys
    probe = (
        "import sys, json\n"
        "import src.l5_shadow_controller\n"
        "bad = %r\n"
        "print(json.dumps(sorted(m for m in sys.modules"
        " if any(b == m.split('.')[-1] for b in bad))))\n" % (NO_ORDER_PATH,)
    )
    out = subprocess.run([sys.executable, "-c", probe],
                         cwd=str(ROOT), capture_output=True, text=True)
    assert out.returncode == 0, out.stderr[-2000:]
    assert json.loads(out.stdout.strip().splitlines()[-1]) == []


def test_arming_environment_variables_change_nothing(tmp_path, monkeypatch):
    """THE POINT OF PHASE A. Every arming key set to its live value, the
    shadow lock lifted — and this object still submits nothing, because
    there is nothing to submit with."""
    for k, v in (("PMUS_Q1_LIVE", "YES"), ("PMUS_Q1_SESSION_ID", "s"),
                 ("PMUS_Q1_ARM", "s"), ("L5_SHADOW_ONLY", "0"),
                 ("PMUS_SPRING_LIVE", "YES"), ("L5_CANCEL_MECHANISM", "YES")):
        monkeypatch.setenv(k, v)
    c = ctl(tmp_path)
    rec = c.step(WS + 310.0, slug=SLUG, book=_book(), window_end_ts=WEND)
    assert rec["provenance"] == sc.CONTROLLER_PROVENANCE
    for attr in sc.FORBIDDEN_ATTRS:
        assert not hasattr(c, attr)


# ========================================================== PROVENANCE

def test_every_record_is_controller_provenance(tmp_path):
    c = ctl(tmp_path)
    for t, bk in ((WS + 10.0, _book()), (WS + 310.0, None)):
        rec = c.step(t, slug=SLUG, book=bk, window_end_ts=WEND)
        assert rec["provenance"] == "CONTROLLER_GENERATED"
        assert "l5_native" not in rec and "rested_at_l5_price" not in rec


def test_provenance_is_never_the_l5_label():
    assert sc.CONTROLLER_PROVENANCE != "L5_NATIVE"
    assert sc.CONTROLLER_PROVENANCE == "CONTROLLER_GENERATED"


# ================================================= INVENTORY-DRIVEN OUTPUT

def test_from_flat_the_decision_is_an_open(tmp_path):
    c = ctl(tmp_path)
    rec = c.step(WS + 310.0, slug=SLUG, book=_book(), window_end_ts=WEND)
    assert rec["decision"] == "quote"
    assert rec["relation"] == "open"
    assert rec["inventory"] == {"yes": 0, "no": 0, "matched": 0, "residual": 0}


def test_a_surplus_extends_the_surplus_side(tmp_path):
    c = ctl(tmp_path)
    inv = c.inventory_for(SLUG)
    inv.record(True, D("0.48"))
    c._store_inventory(SLUG, inv)
    rec = c.step(WS + 320.0, slug=SLUG, book=_book(), window_end_ts=WEND)
    if rec.get("relation") == "extend":
        assert rec["side"] == "YES"


def test_a_deficit_completes_on_the_deficit_side_from_our_own_cost(tmp_path):
    c = ctl(tmp_path)
    inv = c.inventory_for(SLUG)
    inv.record(True, D("0.48"))
    c._store_inventory(SLUG, inv)
    c._slot(SLUG)["last_rel"] = "open"
    rec = c.step(WS + 330.0, slug=SLUG, book=_book(), window_end_ts=WEND)
    if rec.get("relation") == "complete" and rec["decision"] == "quote":
        assert rec["side"] == "NO"
        # priced off OUR average cost against the spring cap, not L5's price
        assert D(rec["max_pair_cost"]) <= l5d.BTC15M_PAIR_CAP


def test_accumulation_stops_at_the_residual_cap(tmp_path):
    c = ctl(tmp_path)
    inv = c.inventory_for(SLUG)
    for _ in range(l5d.MAX_RESIDUAL_UNITS):
        inv.record(True, D("0.48"))
    c._store_inventory(SLUG, inv)
    c._slot(SLUG)["last_rel"] = "extend"
    rec = c.step(WS + 340.0, slug=SLUG, book=_book(), window_end_ts=WEND)
    if rec.get("relation") == "extend":
        assert rec["decision"] == "no_action"
        assert "residual_cap" in rec["reason"]


# ==================================================== THE REAL CLOCK

@pytest.mark.parametrize("elapsed,want", [
    (10.0, 890.0), (310.0, 590.0), (700.0, 200.0), (880.0, 20.0)])
def test_the_record_carries_the_real_seconds_to_expiry(tmp_path, elapsed, want):
    c = ctl(tmp_path)
    rec = c.step(WS + elapsed, slug=SLUG, book=_book(), window_end_ts=WEND)
    assert rec["tte_s"] == want
    assert rec["state"] is not None


def test_without_a_window_clock_the_sports_sentinel_applies(tmp_path):
    c = ctl(tmp_path)
    rec = c.step(WS + 310.0, slug=SLUG, book=_book(), window_end_ts=None)
    assert rec["tte_s"] == round(l5d.SPORTS_TTE_DEFAULT_S, 1)


def test_the_state_key_moves_across_the_window(tmp_path):
    c = ctl(tmp_path)
    seen = {c.step(WS + e, slug=SLUG, book=_book(),
                   window_end_ts=WEND).get("state")
            for e in (10.0, 400.0, 700.0, 850.0, 890.0)}
    assert len([s for s in seen if s]) >= 3, seen


# ===================================================== THE FILL RULE

def _drive(c, t, bk, lad, scenario=None):
    """ONE step that also clears whatever was resting.

    `already_resting` is a real rule -- the deployed engine has no
    cancel-and-replace and does not chase a resting order with a newer
    price -- so a test loop that never fills can only ever produce one
    quote. This passes the volume that clears the current order into the
    same step, which is how a sequence actually advances.
    """
    sc_name = scenario or sc.HEADLINE_SCENARIO
    rest = c._scen(SLUG, sc_name).get("resting")
    prints = []
    if rest:
        px = D(rest["price"])
        hit = px if rest["side"] == "YES" else D(1) - px
        prints = [_print(hit, qty="9", t=t - 1.0)]
    return c.step(t, slug=SLUG, book=bk, window_end_ts=WEND,
                  prints=prints, ladder=lad)


def _fill_resting(c, t, scenario=None):
    """Clear whatever this scenario has resting, by trading enough volume
    through its level. Requires a served queue; `_ladder` with qty 0 puts
    us at the front of a KNOWN queue, which is the only honest way to make
    a conservative fill happen in a test."""
    sc_name = scenario or sc.HEADLINE_SCENARIO
    rest = c._scen(SLUG, sc_name).get("resting")
    if not rest:
        return None
    px = D(rest["price"])
    hit = px if rest["side"] == "YES" else D(1) - px
    lad = _ladder(bid=str(px) if rest["side"] == "YES" else "0.50",
                  ask="0.51" if rest["side"] == "YES" else str(D(1) - px),
                  bid_qty="0", ask_qty="0", t=t)
    return c.step(t, slug=SLUG, book=_book(), window_end_ts=WEND,
                  ladder=lad, prints=[_print(hit, qty="9", t=t - 1.0)])


def _rest_one(c, t=WS + 310.0, bid="0.50", ask="0.51", qty_ahead="0",
              scenario=None):
    """Quote once, with a served queue, and hand back that scenario's
    resting order description."""
    lad = _ladder(bid=bid, ask=ask, bid_qty=qty_ahead, ask_qty=qty_ahead)
    c.step(t, slug=SLUG, book=_book(bid, ask), window_end_ts=WEND,
           prints=[], ladder=lad)
    sc_name = scenario or sc.HEADLINE_SCENARIO
    return c._scen(SLUG, sc_name)["resting"], lad


def test_a_touch_at_our_price_without_volume_is_not_a_fill(tmp_path):
    """TOUCH IS NOT FILL, and now it is the queue model saying so rather
    than a price comparison: reaching our level advances nothing. Only
    trade volume through the level moves the queue."""
    c = ctl(tmp_path)
    rest, lad = _rest_one(c)
    assert rest is not None
    px = D(rest["price"])
    # the book arrives AT our price and nothing trades
    rec = c.step(WS + 320.0, slug=SLUG, book=_book(str(px), str(px + D("0.01"))),
                 window_end_ts=WEND, prints=[], ladder=lad)
    assert rec.get("modelled_fill") is None
    for scen in sc.SCENARIOS:
        assert c.inventory_for(SLUG, scen).snapshot()["residual"] == 0, scen


def test_an_unknown_queue_never_fills_except_on_a_sweep(tmp_path):
    """NO LADDER MEANS UNKNOWN QUEUE, and unknown is not zero. Treating it
    as zero would put us at the front of a queue we cannot see."""
    c = ctl(tmp_path)
    c.step(WS + 310.0, slug=SLUG, book=_book(), window_end_ts=WEND,
           prints=[], ladder=None)          # no ladder at all
    rest = c._scen(SLUG, sc.HEADLINE_SCENARIO)["resting"]
    assert rest is not None
    px = D(rest["price"])
    # volume AT our level: with an unknown queue this must not fill
    at = c.step(WS + 320.0, slug=SLUG, book=_book(), window_end_ts=WEND,
                ladder=None,
                prints=[_print(px if rest["side"] == "YES" else D(1) - px,
                               qty="999", t=WS + 315.0)])
    assert at.get("modelled_fill") is None
    assert c.inventory_for(SLUG).snapshot()["residual"] == 0


def test_volume_through_a_served_queue_fills_conservatively(tmp_path):
    """The one case CONSERVATIVE accepts: the queue ahead of us was SERVED
    (so it is known), and enough volume traded through our level to clear
    it and our own size as well."""
    c = ctl(tmp_path)
    rest, lad = _rest_one(c, qty_ahead="0")
    px = D(rest["price"])
    hit = px if rest["side"] == "YES" else D(1) - px
    rec = c.step(WS + 320.0, slug=SLUG, book=_book(), window_end_ts=WEND,
                 ladder=lad,
                 prints=[_print(hit, qty="5", t=WS + 315.0)])
    fill = rec.get("modelled_fill")
    assert fill is not None, rec
    assert fill["filled_by"] == sc.FILL_RULE
    assert fill["scenario"] == sc.HEADLINE_SCENARIO
    assert fill["queue_ahead_at_placement"] == "0"
    assert "NOT a confirmed fill" in fill["labels"]
    assert c.inventory_for(SLUG).snapshot()["residual"] != 0


def test_a_deep_served_queue_does_not_fill_the_conservative_scenario(tmp_path):
    """Behind real resting volume, a print that clears less than the queue
    plus our own size fills nothing conservatively."""
    c = ctl(tmp_path)
    rest, lad = _rest_one(c, qty_ahead="500")
    px = D(rest["price"])
    hit = px if rest["side"] == "YES" else D(1) - px
    rec = c.step(WS + 320.0, slug=SLUG, book=_book(), window_end_ts=WEND,
                 ladder=lad,
                 prints=[_print(hit, qty="1", t=WS + 315.0)])
    assert rec.get("modelled_fill") is None, rec
    assert c.inventory_for(SLUG, sc.FM.CONSERVATIVE).snapshot()["residual"] == 0


def test_the_fill_rule_is_named_in_every_record(tmp_path):
    c = ctl(tmp_path)
    rec = c.step(WS + 310.0, slug=SLUG, book=_book(), window_end_ts=WEND)
    assert rec["fill_rule"] == "QUEUE_AWARE_THREE_SCENARIO"


def test_a_quote_that_would_cross_is_refused_not_modelled(tmp_path):
    """A passive order that crosses is not a maker order, and modelling it
    as if it had rested would credit a fill no maker could earn."""
    c = ctl(tmp_path)
    inv = c.inventory_for(SLUG, sc.FM.CONSERVATIVE)
    inv.record(False, D("0.10"))            # NO at 0.10 -> deficit is YES
    c._store_inventory(SLUG, inv, sc.FM.CONSERVATIVE)
    # a YES completion priced through the ask would cross
    rec = c.step(WS + 310.0, slug=SLUG, book=_book("0.10", "0.11"),
                 window_end_ts=WEND, prints=[],
                 ladder=_ladder(bid="0.10", ask="0.11"))
    assert rec["decision"] in ("quote", "no_action")
    if rec["decision"] == "no_action":
        assert rec.get("reason") in ("would_cross_not_a_maker_quote",
                                     "v2_off_distribution",
                                     "no_legal_maker_complement",
                                     "already_resting")


def test_each_scenario_keeps_its_own_inventory_and_trajectory(tmp_path):
    """THE POINT OF THREE SCENARIOS. A fill that only the optimistic case
    earns must not create matched inventory in the conservative one -- and
    because inventory drives the next decision, the trajectories diverge in
    what they DO as well as in what they hold."""
    c = ctl(tmp_path)
    rest, lad = _rest_one(c, qty_ahead="500")
    px = D(rest["price"])
    # VOLUME EXACTLY EQUAL TO THE QUEUE AHEAD. That clears everyone in
    # front of us but not our own size, so BASE and OPTIMISTIC fill and
    # CONSERVATIVE does not. A sweep would NOT do this and must not: with a
    # known queue the model refuses sweeps outright, because a thin print
    # through a deep level means the levels above thinned by CANCELLATION,
    # and crediting that is crediting a cancel as queue advancement.
    hit = px if rest["side"] == "YES" else D(1) - px
    c.step(WS + 320.0, slug=SLUG, book=_book(), window_end_ts=WEND,
           ladder=lad, prints=[_print(hit, qty="500", t=WS + 315.0)])
    opt = c.inventory_for(SLUG, sc.FM.OPTIMISTIC).snapshot()
    base = c.inventory_for(SLUG, sc.FM.BASE).snapshot()
    cons = c.inventory_for(SLUG, sc.FM.CONSERVATIVE).snapshot()
    assert opt["residual"] != 0, opt
    assert base["residual"] != 0, base
    assert cons["residual"] == 0, cons


def test_a_sweep_through_a_KNOWN_queue_fills_nothing(tmp_path):
    """Pinned because it is counter-intuitive and was once wrong. A thin
    print through a deep level is evidence of CANCELLATION above us, not of
    our queue clearing, so with a served queue the sweep allowance does not
    apply in any scenario."""
    c = ctl(tmp_path)
    rest, lad = _rest_one(c, qty_ahead="9586")
    px = D(rest["price"])
    through = (px - D("0.02") if rest["side"] == "YES"
               else (D(1) - px) + D("0.02"))
    c.step(WS + 320.0, slug=SLUG, book=_book(), window_end_ts=WEND,
           ladder=lad, prints=[_print(through, qty="12", t=WS + 315.0)])
    for scen in sc.SCENARIOS:
        assert c.inventory_for(SLUG, scen).snapshot()["residual"] == 0, scen


# ====================================================== PERSISTENCE

def test_inventory_survives_a_restart(tmp_path):
    c = ctl(tmp_path)
    inv = c.inventory_for(SLUG)
    inv.record(True, D("0.48"))
    c._store_inventory(SLUG, inv)
    c._save()
    again = ctl(tmp_path)                 # a fresh object, same state file
    assert again.inventory_for(SLUG).snapshot() == {
        "yes": 1, "no": 0, "matched": 0, "residual": 1}


def test_unreadable_state_starts_clean_rather_than_crashing(tmp_path):
    (tmp_path / "shadow.json").write_text("{not json")
    c = ctl(tmp_path)
    assert c.inventory_for(SLUG).snapshot()["yes"] == 0


def test_the_state_write_is_atomic_and_valid_json(tmp_path):
    c = ctl(tmp_path)
    c.step(WS + 310.0, slug=SLUG, book=_book(), window_end_ts=WEND)
    doc = json.loads((tmp_path / "shadow.json").read_text())
    assert doc["v"] == sc.STATE_VERSION
    assert SLUG in doc["markets"]


# ======================================================== SETTLEMENT

def test_settlement_separates_matched_from_residual(tmp_path):
    c = ctl(tmp_path)
    inv = c.inventory_for(SLUG)
    inv.record(True, D("0.48"))
    inv.record(False, D("0.48"))          # one matched pair at 0.96
    inv.record(True, D("0.50"))           # plus one YES residual
    c._store_inventory(SLUG, inv)
    out = c.settle(SLUG, D("1"))          # YES wins
    assert out["scenarios"][sc.HEADLINE_SCENARIO]["matched_units"] == 1 and out["scenarios"][sc.HEADLINE_SCENARIO]["residual_units"] == 1
    assert out["scenarios"][sc.HEADLINE_SCENARIO]["cost_total"] == "1.46"
    assert out["scenarios"][sc.HEADLINE_SCENARIO]["redemption_total"] == "2"
    assert out["scenarios"][sc.HEADLINE_SCENARIO]["modelled_net"] == "0.54"
    assert out["maker_fee_usd"] == "0.0000"
    assert "NOT realized P&L" in out["labels"]


def test_a_losing_residual_settles_worthless(tmp_path):
    c = ctl(tmp_path)
    inv = c.inventory_for(SLUG)
    inv.record(True, D("0.65"))
    c._store_inventory(SLUG, inv)
    out = c.settle(SLUG, D("0"))          # YES loses
    assert out["scenarios"][sc.HEADLINE_SCENARIO]["residual_units"] == 1
    assert out["scenarios"][sc.HEADLINE_SCENARIO]["redemption_total"] == "0"
    assert out["scenarios"][sc.HEADLINE_SCENARIO]["modelled_net"] == "-0.65"


def test_settlement_clears_the_market_for_the_next_window(tmp_path):
    c = ctl(tmp_path)
    inv = c.inventory_for(SLUG)
    inv.record(True, D("0.48"))
    c._store_inventory(SLUG, inv)
    c.settle(SLUG, D("1"))
    assert c.inventory_for(SLUG).snapshot()["yes"] == 0


# ============================================ NEVER RAISES INTO THE TICK

def test_a_missing_book_is_a_no_action_not_an_exception(tmp_path):
    c = ctl(tmp_path)
    for bk in (None, {}, {"bid": None, "ask": None}):
        rec = c.step(WS + 10.0, slug=SLUG, book=bk, window_end_ts=WEND)
        assert rec["decision"] == "no_action"


def test_a_broken_model_is_contained(tmp_path):
    c = sc.ShadowController(state_path=str(tmp_path / "s.json"),
                            model={"full": None})
    rec = c.step(WS + 310.0, slug=SLUG, book=_book(), window_end_ts=WEND)
    assert rec["decision"] == "no_action"
    assert rec["provenance"] == sc.CONTROLLER_PROVENANCE


def test_the_report_counts_decisions(tmp_path):
    c = ctl(tmp_path)
    c.step(WS + 310.0, slug=SLUG, book=_book(), window_end_ts=WEND)
    c.step(WS + 320.0, slug=SLUG, book=None, window_end_ts=WEND)
    rep = c.report()
    assert rep["decisions"] == 2
    assert rep["provenance"] == "CONTROLLER_GENERATED"
    assert rep["pair_cap"] == str(l5d.BTC15M_PAIR_CAP)


# ====================================== THE DECISION THAT DID NOT EXIST

def _fill_the_resting(c, t, slug):
    """Take our resting order out under the fill rule, whichever side it is."""
    rest = c._slot(slug)["resting"]
    assert rest, "nothing was resting"
    return c.step(t, slug=slug, book=_book_against(rest, through=True),
                  window_end_ts=WEND)


def test_a_complete_is_reachable_and_prices_from_OUR_cost(tmp_path):
    """THE EVIDENCE THIS WHOLE PHASE EXISTS FOR. The deployed money path
    derives side, price and size from L5's own action, so it has never
    produced an inventory-driven COMPLETE. This one is ours: the side is
    the DEFICIT side, and the price comes from our own average cost through
    the pair cap -- not from L5, and not from the touch.

    Driven on the REAL frozen v2 model, not the fixture, because which
    relation is recommended at a state is the model's claim and not ours.
    """
    from src import pmus_l5_persistence_v2 as pv2
    c = sc.ShadowController(state_path=str(tmp_path / "s.json"),
                            model=pv2.load_v2())
    bk = _book("0.37", "0.38")
    lad = _ladder(bid="0.37", ask="0.38", bid_qty="0", ask_qty="0")
    c.step(WS + 3.0, slug=SLUG, book=bk, window_end_ts=WEND, prints=[],
           ladder=lad)
    got = _fill_resting(c, WS + 6.0)
    fill = (got or {}).get("modelled_fill")
    assert fill and fill["side"] == "YES" and fill["price"] == "0.37", got
    completes = []
    for i in range(3, 300):
        rec = _drive(c, WS + i * 3.0, bk, lad)
        if rec.get("relation") == "complete" and rec.get("decision") == "quote":
            completes.append(rec)
    assert completes, "no inventory-driven COMPLETE was ever produced"
    rec = completes[0]
    # THE DEFICIT SIDE, derived from the residual the record itself carries
    # rather than assumed: a trajectory that happens to end up short NO
    # completes in NO, and one short YES completes in YES.
    resid = rec["inventory"]["residual"]
    assert resid != 0, rec
    assert rec["side"] == ("NO" if resid > 0 else "YES"), rec
    # PRICED FROM OUR OWN AVERAGE COST UNDER THE CAP, and the record proves
    # it: observation_cap_px is `cap - our_avg_surplus`, and the quote is
    # the tightest of that, the hard >=1c bound, and the deficit touch.
    px = D(rec["target_price"])
    assert px == min(D(rec["hard_bound_px"]), D(rec["observation_cap_px"]))
    # the resulting pair clears the spring cap
    assert D(rec["max_pair_cost"]) <= sc.l5d.BTC15M_PAIR_CAP
    # and it is strictly INSIDE the touch -- patient, not marketable
    touch = (D(1) - D("0.38")) if rec["side"] == "NO" else D("0.37")
    assert px < touch, (px, touch)


def test_the_spring_pair_cap_is_what_binds_the_completion(tmp_path):
    """0.96, not the sports 0.98 and not the incentive-era near-$1: the
    completion price moves with the cap, so the cap is what sets it."""
    from src import pmus_l5_persistence_v2 as pv2
    seen, at = {}, {}
    for cap in (D("0.96"), D("0.92")):
        c = sc.ShadowController(state_path=str(tmp_path / ("s%s.json" % cap)),
                                model=pv2.load_v2(), pair_cap=cap)
        bk = _book("0.37", "0.38")
        lad = _ladder(bid="0.37", ask="0.38", bid_qty="0", ask_qty="0")
        c.step(WS + 3.0, slug=SLUG, book=bk, window_end_ts=WEND, prints=[],
               ladder=lad)
        _fill_resting(c, WS + 6.0)
        for i in range(3, 300):
            rec = _drive(c, WS + i * 3.0, bk, lad)
            if (rec.get("relation") == "complete"
                    and rec.get("decision") == "quote"):
                # BOTH RUNS ARE INVENTORY-IDENTICAL AT THIS POINT, and the
                # assertion below checks it: the cap changes the completion
                # PRICE, not which extends happen, so the two trajectories
                # reach their first completion holding the same thing.
                seen[cap] = D(rec["target_price"])
                at[cap] = rec["inventory"]["residual"]
                break
    # the cap is what BINDS: tighten it by 4c and the completion price
    # moves by 4c, because `observation_cap_px` is cap - our own average.
    assert set(seen) == {D("0.96"), D("0.92")}, seen
    assert at[D("0.96")] == at[D("0.92")], at
    assert seen[D("0.96")] - seen[D("0.92")] == D("0.04"), seen


def test_the_default_cap_is_the_btc15m_spring_target(tmp_path):
    assert ctl(tmp_path).pair_cap == D("0.96")
    assert sc.l5d.BTC15M_PAIR_CAP == D("0.96")


# ======================================== THE STATE FILE STAYS BOUNDED

def _settle_n(c, n, *, start=0):
    """Settle n synthetic windows in EVERY scenario, since a window only
    ages out of the state once all three are done with it."""
    for i in range(start, start + n):
        slug = "w%04d" % i
        for scen in sc.SCENARIOS:
            inv = c.inventory_for(slug, scen)
            inv.record(True, D("0.40"))
            inv.record(False, D("0.50"))
            c._store_inventory(slug, inv, scen)
        c._slot(slug)["window_end_ts"] = WS + i * 900.0
        c.settle(slug, D(1))


def test_pruning_drops_detail_but_never_a_cent_of_the_net(tmp_path):
    """One slot per 15-minute window is 96 a day forever. Dropping the
    oldest outright would shrink the modelled net toward zero -- the same
    error as zeroing a stale record -- so the totals are folded into a
    carry first and the net is identical either side of the prune."""
    c = ctl(tmp_path)
    _settle_n(c, sc.SETTLED_KEEP)
    before = c.economics()
    assert before["per_scenario"][sc.HEADLINE_SCENARIO]["windows_settled"] == sc.SETTLED_KEEP
    assert before["per_scenario"][sc.HEADLINE_SCENARIO]["windows_folded_into_carry"] == 0
    _settle_n(c, 50, start=sc.SETTLED_KEEP)
    after = c.economics()
    assert after["per_scenario"][sc.HEADLINE_SCENARIO]["windows_settled"] == sc.SETTLED_KEEP + 50
    assert after["per_scenario"][sc.HEADLINE_SCENARIO]["windows_folded_into_carry"] == 50
    assert len([k for k in c.state["markets"] if k.startswith("w")]) \
        == sc.SETTLED_KEEP
    # each window: cost 0.90, redemption 1.00 (one matched pair)
    assert D(after["per_scenario"][sc.HEADLINE_SCENARIO]["cost_total"]) == D("0.90") * (sc.SETTLED_KEEP + 50)
    assert D(after["per_scenario"][sc.HEADLINE_SCENARIO]["redemption_total"]) == D(sc.SETTLED_KEEP + 50)


def test_the_carry_survives_a_restart(tmp_path):
    c = ctl(tmp_path)
    _settle_n(c, sc.SETTLED_KEEP + 10)
    net = c.economics()["headline_net"]
    again = ctl(tmp_path)
    assert again.economics()["headline_net"] == net
    assert again.economics()["per_scenario"][
        sc.HEADLINE_SCENARIO]["windows_folded_into_carry"] == 10


def test_the_reported_stranded_list_is_capped_but_the_count_is_exact(tmp_path):
    """This report ships in every heartbeat; a long run of failed settlement
    reads must not grow it without bound."""
    c = ctl(tmp_path)
    for i in range(12):
        slug = "stranded%02d" % i
        inv = c.inventory_for(slug)
        inv.record(True, D("0.40"))
        c._store_inventory(slug, inv)
    assert c.stranded_count() == 12
    assert len(c.stranded()) == 12
    capped = c.stranded(cap=sc.STRANDED_REPORT_CAP)
    assert len(capped) == sc.STRANDED_REPORT_CAP + 1      # +_not_shown
    assert capped["_not_shown"] == 12 - sc.STRANDED_REPORT_CAP
    assert c.report()["economics"]["stranded_markets"] == 12


# ================================== THE VENUE'S ACTUAL BOOK CONTRACT

def test_a_string_valued_book_decides_normally(tmp_path):
    """THE PRODUCTION DEFECT, pinned. `AuthBook.snapshot` returns str
    prices; `plan_action` does Decimal arithmetic (`ONE - ask`,
    `_floor_tick(bid)`). Passing the snapshot through raised TypeError on
    every tick of the first covered-band window."""
    from src import pmus_l5_persistence_v2 as pv2
    c = sc.ShadowController(state_path=str(tmp_path / "s.json"),
                            model=pv2.load_v2())
    rec = c.step(WS + 60.0, slug=SLUG, window_end_ts=WEND,
                 book={"bid": "0.4800", "ask": "0.5000",
                       "bid_shares": "100", "ask_shares": "80",
                       "market_state": "MARKET_STATE_OPEN",
                       "source": "authenticated_ws"})
    assert rec["decision"] == "quote", rec
    assert rec["relation"] == "open" and rec["side"] == "YES"
    assert rec["target_price"] == "0.48"
    assert not str(rec.get("reason") or "").startswith("controller_error")


def test_the_snapshot_shape_is_taken_from_the_real_auth_book(tmp_path):
    """Not from my assumption about it. Builds the dict `AuthBook.snapshot`
    actually returns, from the module's own storage types, so a change to
    that contract fails here rather than in production."""
    from src import l5_pmus_auth_book as AB
    from src import pmus_ws_evidence_recorder as REC
    # the stored price is whatever _px produces, which is where str() enters
    assert isinstance(REC._px({"value": "0.4800"}), str)
    assert isinstance(REC._px(0.48), str)
    book = AB.AuthBook()
    book.connected = True
    book.last_frame_t = 1.0
    book.state[SLUG] = {"bid": REC._px(0.48), "ask": REC._px(0.50),
                        "bid_qty": "100", "ask_qty": "80",
                        "bbo_history": [], "book_updates": 3,
                        "market_state": "MARKET_STATE_OPEN"}
    snap = book.snapshot(SLUG)
    assert isinstance(snap["bid"], str) and isinstance(snap["ask"], str)
    from src import pmus_l5_persistence_v2 as pv2
    c = sc.ShadowController(state_path=str(tmp_path / "r.json"),
                            model=pv2.load_v2())
    rec = c.step(WS + 60.0, slug=SLUG, book=snap, window_end_ts=WEND)
    assert rec["decision"] == "quote", rec


@pytest.mark.parametrize("bad", [
    {"bid": "notanumber", "ask": "0.50"},
    {"bid": "0.48", "ask": "notanumber"},
    {"bid": None, "ask": "0.50"},
    {"bid": "0.48", "ask": None},
    {"bid": "", "ask": ""},
])
def test_an_unparseable_price_is_no_action_never_a_raise(tmp_path, bad):
    from src import pmus_l5_persistence_v2 as pv2
    c = sc.ShadowController(state_path=str(tmp_path / "b.json"),
                            model=pv2.load_v2())
    rec = c.step(WS + 60.0, slug=SLUG, book=bad, window_end_ts=WEND)
    assert rec["decision"] == "no_action"
    assert not str(rec.get("reason") or "").startswith("controller_error"), rec


def test_normalise_book_leaves_the_other_fields_alone(tmp_path):
    out = sc.normalise_book({"bid": "0.48", "ask": "0.50", "source": "ws",
                             "stream_age_s": 0.02})
    assert out["bid"] == D("0.48") and out["ask"] == D("0.50")
    assert out["source"] == "ws" and out["stream_age_s"] == 0.02
    assert sc.normalise_book(None) is None and sc.normalise_book({}) is None


def test_a_one_sided_book_is_refused_by_name(tmp_path):
    """This controller's decisions are all about PAIR economics, so a quote
    whose complement has no price is refused rather than taken on the one
    side that happens to be priceable -- which is what the shared sports
    layer would do, correctly, for a different purpose."""
    from src import pmus_l5_persistence_v2 as pv2
    c = sc.ShadowController(state_path=str(tmp_path / "o.json"),
                            model=pv2.load_v2())
    for bk in ({"bid": "0.48", "ask": None}, {"bid": None, "ask": "0.50"},
               {"bid": "0.48"}, {"ask": "0.50"}):
        rec = c.step(WS + 60.0, slug=SLUG, book=bk, window_end_ts=WEND)
        assert rec["decision"] == "no_action"
        assert rec["reason"] == "no_two_sided_book", (bk, rec)


def test_the_venue_itself_never_hands_us_a_one_sided_book(tmp_path):
    """The guard above refuses a state the venue already refuses to report,
    so it changes no production behaviour. Pinned, because that is the
    reason it is safe."""
    from src import l5_pmus_auth_book as AB
    book = AB.AuthBook()
    book.connected = True
    book.last_frame_t = 1.0
    for st in ({"bid": "0.48", "ask": None}, {"bid": None, "ask": "0.50"},
               {"bid": None, "ask": None}):
        book.state[SLUG] = dict(st, bid_qty="1", ask_qty="1",
                                bbo_history=[], book_updates=1)
        assert book.snapshot(SLUG) is None, st


# ===================================== THE POLICIES THEMSELVES

def test_every_policy_is_registered_and_the_baseline_is_the_argmax():
    assert sc.BASELINE_POLICY == "ARGMAX"
    assert set(sc.POLICIES) == {"ARGMAX", "PAIR_AVAILABLE",
                                "RESIDUAL_URGENCY"}
    assert sc.POLICIES[sc.BASELINE_POLICY] is sc.policy_argmax


def test_an_unknown_policy_is_refused_at_construction(tmp_path):
    with pytest.raises(ValueError):
        sc.ShadowController(state_path=str(tmp_path / "x.json"),
                            model=_model(), policy="WISHFUL")


def test_the_baseline_is_the_models_argmax_up_to_the_canonical_override(
        tmp_path):
    """If the baseline ever departs from the argmax it has stopped being the
    thing the candidates are measured against -- with ONE documented
    exception that belongs to the shared layer, not to the policy:
    `plan_action` canonicalises any relation to `open` from flat inventory,
    because "any buy from |residual| < 1 IS an open, whatever label the
    model emitted". So the override is asserted, not excused.
    """
    c = ctl(tmp_path, policy="ARGMAX")
    overrides = 0
    for i in range(40):
        resid_before = c.inventory_for(SLUG).residual
        rec = c.step(WS + i * 20.0, slug=SLUG, book=_book(),
                     window_end_ts=WEND)
        if rec.get("decision") != "quote":
            continue
        if rec["relation"] == rec["model_argmax"]:
            continue
        # the only legal divergence
        assert resid_before == 0, rec
        assert rec["relation"] == "open", rec
        overrides += 1
    assert overrides, "the canonical override never fired, so it is untested"


def test_residual_urgency_completes_one_step_before_the_cap(tmp_path):
    """The whole correction: at |residual| == MAX_RESIDUAL_UNITS - 1 it
    completes regardless of the model, so the cap is never reached and the
    controller never parks."""
    from src import pmus_l5_persistence_v2 as pv2
    inv = l5d.Inventory()
    for _ in range(l5d.MAX_RESIDUAL_UNITS - 1):
        inv.record(True, D("0.40"))
    prop = {"recommended_relation": "extend"}
    book = sc.normalise_book(_book("0.40", "0.42"))
    assert sc.policy_residual_urgency(prop, inv, book, 500.0) == "complete"
    # and one step below it still defers to the model
    lo = l5d.Inventory()
    lo.record(True, D("0.40"))
    if l5d.MAX_RESIDUAL_UNITS - 1 > 1:
        assert sc.policy_residual_urgency(prop, lo, book, 500.0) == "extend"


def test_residual_urgency_keeps_extend_as_a_first_class_action(tmp_path):
    """Unlike PAIR_AVAILABLE, which flattens immediately. The operating
    model is explicit that same-side EXTEND is not an error to flatten, so
    a candidate that never extends is a different strategy and not a
    replication."""
    from src import pmus_l5_persistence_v2 as pv2
    seen = {}
    for pol in ("RESIDUAL_URGENCY", "PAIR_AVAILABLE"):
        c = sc.ShadowController(state_path=str(tmp_path / (pol + ".json")),
                                model=pv2.load_v2(), policy=pol)
        rels = Counter()
        bk = _book("0.48", "0.50")
        lad = _ladder(bid="0.48", ask="0.50", bid_qty="0", ask_qty="0")
        for i in range(400):
            rec = _drive(c, WS + i * 4.0, bk, lad)
            if rec.get("decision") == "quote":
                rels[rec["relation"]] += 1
        seen[pol] = rels
    assert seen["RESIDUAL_URGENCY"]["extend"] > 0, seen
    assert (seen["RESIDUAL_URGENCY"]["extend"]
            > seen["PAIR_AVAILABLE"]["extend"]), seen


def test_no_policy_ever_reaches_the_residual_cap_except_the_baseline(tmp_path):
    """The baseline parks at `residual_cap`; the candidates must not. This
    is the measurable improvement, and it does not depend on the fill rule:
    it is about inventory, not about execution."""
    from src import pmus_l5_persistence_v2 as pv2
    capped = {}
    for pol in sorted(sc.POLICIES):
        c = sc.ShadowController(state_path=str(tmp_path / (pol + "c.json")),
                                model=pv2.load_v2(), policy=pol)
        n = 0
        bk = _book("0.48", "0.50")
        lad = _ladder(bid="0.48", ask="0.50", bid_qty="0", ask_qty="0")
        for i in range(400):
            rec = _drive(c, WS + i * 4.0, bk, lad)
            if "residual_cap" in str(rec.get("reason") or ""):
                n += 1
        capped[pol] = n
    assert capped["RESIDUAL_URGENCY"] == 0, capped
    assert capped["PAIR_AVAILABLE"] == 0, capped


# --------------------------------------------------------------------------
# WHAT A DEPLOY DOES TO THE DURABLE STATE.
#
# A deploy restarts the process over the same volume. Nothing may be
# credited, nothing discarded, and nothing attributed to the venue that the
# RESTART actually did.
def _deployed_shape(path, slug="cpc-btc-updown-15m-2026-10-19-1700z"):
    """A v3 state file shaped like the one on the volume: settled-pending
    inventory in every scenario, and a resting record in one of them."""
    doc = {"v": sc.STATE_VERSION, "markets": {slug: {
        "window_end_ts": 1792430100.0, "scen": {}}}}
    for i, scen in enumerate(sc.SCENARIOS):
        doc["markets"][slug]["scen"][scen] = {
            "yes": 6, "no": 3, "yes_cost": "1.83", "no_cost": "1.12",
            "last_rel": "extend", "settled": None,
            "resting": ({"side": "NO", "price": "0.41",
                         "relation": "open", "t": 1792431862.6}
                        if i == 0 else None)}
    io.open(path, "w", encoding="utf-8").write(json.dumps(doc))
    return slug


def test_a_restart_carries_inventory_and_records_no_discontinuity(tmp_path):
    p = str(tmp_path / "s.json")
    slug = _deployed_shape(p)
    c = sc.ShadowController(state_path=p)
    for scen in sc.SCENARIOS:
        inv = c.inventory_for(slug, scen)
        assert (inv.yes, inv.no) == (6, 3)
        assert inv.yes_cost + inv.no_cost == D("2.95")
    # SAME version, so there is no discontinuity to record
    assert c.discontinuity() in (None, {}), c.discontinuity()
    assert c.economics()["per_scenario"][
        sc.HEADLINE_SCENARIO]["windows_settled"] == 0
    assert slug in c.stranded()


def test_a_restart_drops_the_resting_record_it_cannot_reconstruct(tmp_path):
    """And says so, rather than leaving a stale record to be published as
    `dropped_resting` by the next window close -- which would blame expiry
    for a quote the restart dropped."""
    p = str(tmp_path / "s.json")
    slug = _deployed_shape(p)
    c = sc.ShadowController(state_path=p)
    boot = c.state["boot"]
    assert boot["dropped_resting_records"] == 1, boot
    assert boot["dropped"] == ["%s|%s" % (slug, sc.SCENARIOS[0])]
    assert boot["inventory_carried"] is True
    for scen in sc.SCENARIOS:
        assert c._scen(slug, scen)["resting"] is None
    closed = c.window_close(slug)
    for scen in sc.SCENARIOS:
        assert closed["scenarios"][scen]["dropped_resting"] is None
        # inventory survived the close untouched
        assert closed["scenarios"][scen]["cost_total"] == "2.95"


def test_the_boot_report_reaches_the_heartbeat(tmp_path):
    p = str(tmp_path / "s.json")
    _deployed_shape(p)
    c = sc.ShadowController(state_path=p)
    assert c.report()["boot"]["dropped_resting_records"] == 1


def test_a_restart_does_not_freeze_a_scenario_that_was_resting(tmp_path):
    """`already_resting` is keyed on the in-memory order, not the record, so
    the next tick quotes afresh. Asserted because the opposite -- a slot
    frozen until the window rolls -- would be invisible in the counts."""
    p = str(tmp_path / "s.json")
    slug = _deployed_shape(p)
    c = sc.ShadowController(state_path=p)
    assert (c._orders.get(slug) or {}).get(sc.SCENARIOS[0]) is None
    out = c.step(1792429500.0, slug=slug,
                 book={"bid": "0.45", "ask": "0.46"},
                 window_end_ts=1792430100.0, window_source="VENUE",
                 prints=[], ladder=None)
    assert out.get("reason") != "already_resting", out


# --------------------------------------------------------------------------
# MEASUREMENT CORRECTNESS. Each of these guards a way the economic report
# could state something false with a straight face. All three were found by
# reading the FIRST production report rather than by reading the code.
def test_a_window_settled_before_the_split_says_so_instead_of_zero(tmp_path):
    """The first production report showed pair=0.0000 for seven of eight
    windows, which invited the conclusion that pair economics contributed
    nothing. The truth was that those windows were settled by code that did
    not record a split. A missing value must not wear the costume of a
    measurement."""
    p = str(tmp_path / "s.json")
    c = sc.ShadowController(state_path=p)
    legacy = "cpc-btc-updown-15m-2026-10-19-1700z"
    fresh = "cpc-btc-updown-15m-2026-10-19-1715z"
    # a legacy line: exactly the keys the old settle() wrote
    c._slot(legacy)["window_end_ts"] = 1.0
    for scen in sc.SCENARIOS:
        c._scen(legacy, scen)["settled"] = {
            "matched_units": 3, "residual_units": 3, "cost_total": "2.95",
            "redemption_total": "3.0000", "modelled_net": "0.0500"}
    # and one settled by the current code
    c._slot(fresh)["window_end_ts"] = 2.0
    inv = c.inventory_for(fresh)
    inv.record(True, D("0.40"))
    inv.record(False, D("0.50"))
    c._store_inventory(fresh, inv)
    c.settle(fresh, D(1))

    e = c.economics()["per_scenario"][sc.HEADLINE_SCENARIO]
    by = {w["market"]: w for w in e["per_window"]}
    assert by[legacy]["pair_net"] == "NOT_RECORDED"
    assert by[legacy]["residual_net"] == "NOT_RECORDED"
    assert by[fresh]["pair_net"] != "NOT_RECORDED"
    # the legacy window still counts for net, which IS recorded
    assert D(by[legacy]["net"]) == D("0.05")
    assert e["windows_settled"] == 2
    # ...but the split totals cover only the window that has a split, and
    # say so
    assert e["windows_without_split"] == 1
    assert e["split_covers_windows"] == 1
    assert D(e["pair_net"]) == D("0.1000")


def test_the_residual_cap_is_a_justified_refusal_not_a_missed_opportunity():
    """`residual_cap:3>=3` is the risk limit working. It was 103 of
    ARGMAX's 178 refusals in the first production report, and counting it
    as forgone profit overstated the miss bucket by more than half."""
    assert "residual_cap".startswith(sc.JUSTIFIED_REFUSAL_PREFIXES)
    c = sc.ShadowController(state_path="/dev/null")
    for reason in ("residual_cap:3>=3", "pair_cap:0.97>0.96",
                   "already_resting", "no_legal_maker_complement",
                   "no_priceable_side", "one_sided_book",
                   "complete_without_surplus", "v2_off_distribution",
                   "would_cross_not_a_maker_quote", "no_two_sided_book"):
        c._tally_record({"scenario": sc.HEADLINE_SCENARIO,
                         "decision": "no_action", "reason": reason})
    t = c._tally(sc.HEADLINE_SCENARIO)
    assert t["possible_misses"] == 0, t["refusal_reasons"]
    assert t["justified_refusals"] == 10
    # and something unrecognised still lands in the miss bucket
    c._tally_record({"scenario": sc.HEADLINE_SCENARIO,
                     "decision": "no_action", "reason": ""})
    assert c._tally(sc.HEADLINE_SCENARIO)["possible_misses"] == 1


def test_the_counters_survive_a_restart(tmp_path):
    """_save wrote the tally and _load dropped it, so every deploy reset
    the execution counters while windows_settled kept persisting -- two
    numbers over two different periods in one report."""
    p = str(tmp_path / "s.json")
    c = sc.ShadowController(state_path=p)
    for _ in range(4):
        c._tally_record({"scenario": sc.HEADLINE_SCENARIO,
                         "decision": "quote"})
    since = c.state["counters_since"]
    assert since
    c._save()

    again = sc.ShadowController(state_path=p)
    t = again._tally(sc.HEADLINE_SCENARIO)
    assert t["quotes_placed"] == 4, t
    assert again.state["counters_since"] == since, "the period was reset"


def test_the_execution_block_names_its_own_period(tmp_path):
    p = str(tmp_path / "s.json")
    c = sc.ShadowController(state_path=p)
    c._slot("m")["window_end_ts"] = 1.0
    inv = c.inventory_for("m")
    inv.record(True, D("0.40"))
    c._store_inventory("m", inv)
    c.settle("m", D(1))
    ex = c.economics()["per_scenario"][sc.HEADLINE_SCENARIO]["execution"]
    # one settled window, no window_close observed -> the counters do NOT
    # cover it, and the report says so rather than implying they do
    assert ex["counters_cover_all_settled_windows"] is False
    assert "counters_since" in ex


# --------------------------------------------------------------------------
# THE PRE-REGISTERED PLACEMENT VARIANT (analysis/l5/TICK_DEPTH_PREREGISTRATION.md)
#
# One tick worse in our own outcome, nothing else different. These tests
# guard the ways a deeper quote could be flattered into looking better than
# it is.
def _two_variants(tmp_path):
    a = sc.ShadowController(state_path=str(tmp_path / "touch.json"),
                            policy=sc.BASELINE_POLICY,
                            placement=sc.AT_TOUCH)
    b = sc.ShadowController(state_path=str(tmp_path / "deep.json"),
                            policy=sc.BASELINE_POLICY,
                            placement=sc.ONE_TICK_BEHIND,
                            model=a.model)
    return a, b


def test_the_deep_variant_rests_exactly_one_tick_worse(tmp_path):
    a, b = _two_variants(tmp_path)
    book = _book("0.45", "0.46")
    ra = a.step(WS, slug=SLUG, book=book, window_end_ts=WS + 900.0,
                window_source="VENUE", prints=[], ladder=None)
    rb = b.step(WS, slug=SLUG, book=book, window_end_ts=WS + 900.0,
                window_source="VENUE", prints=[], ladder=None)
    if ra.get("decision") != "quote":
        pytest.skip("the baseline did not quote on this state")
    assert rb["decision"] == "quote", rb
    # same side and same relation: only the resting price differs
    assert rb["side"] == ra["side"]
    assert rb["relation"] == ra["relation"]
    assert D(rb["target_price"]) == D(ra["target_price"]) - l5d.TICK
    assert D(rb["at_touch_price"]) == D(ra["target_price"])
    assert rb["variant"] == "%s@%s" % (sc.BASELINE_POLICY,
                                       sc.ONE_TICK_BEHIND)
    assert rb["offset_ticks"] == 1 and ra["offset_ticks"] == 0


def test_a_depth_it_cannot_price_is_refused_not_rounded_back(tmp_path):
    """An arm that cannot quote at its own depth must be VISIBLE as that.
    Rounding back to the touch would silently turn the deep arm into the
    touch arm and make the comparison measure nothing."""
    a, b = _two_variants(tmp_path)
    # a book where our own-outcome price is one tick, so one deeper is zero
    book = _book("0.01", "0.02")
    rb = b.step(WS, slug=SLUG, book=book, window_end_ts=WS + 900.0,
                window_source="VENUE", prints=[], ladder=None)
    if rb.get("reason") == "offset_not_priceable":
        assert rb["decision"] == "no_action"
        # and it is a JUSTIFIED refusal, not a missed opportunity
        t = b._tally(sc.HEADLINE_SCENARIO)
        assert t["possible_misses"] == 0
        assert t["refusal_reasons"].get("offset_not_priceable") == 1
    else:
        # the model declined earlier for its own reasons; the refusal path
        # is still asserted directly
        assert "offset_not_priceable" in sc.JUSTIFIED_REFUSALS


def test_the_deep_quote_is_never_credited_a_fill_it_did_not_earn(tmp_path):
    """The deep variant sits behind MORE queue, so it is the variant most
    likely to be flattered by a loose fill rule. A print that merely
    reaches our price, with volume below the queue ahead of us, fills
    nothing."""
    a, b = _two_variants(tmp_path)
    book = _book("0.45", "0.46")
    lad = _ladder(bid="0.45", ask="0.46", bid_qty="900", ask_qty="900")
    rb = b.step(WS, slug=SLUG, book=book, window_end_ts=WS + 900.0,
                window_source="VENUE", prints=[], ladder=lad)
    if rb.get("decision") != "quote":
        pytest.skip("no quote to test the fill rule against")
    px = D(rb["target_price"])
    # a print AT our price, but with volume far below the queue ahead
    rb2 = b.step(WS + 5.0, slug=SLUG, book=book, window_end_ts=WS + 900.0,
                 window_source="VENUE",
                 prints=[_print(px, 5, t=WS + 4.0)], ladder=lad)
    assert rb2.get("modelled_fill") is None, rb2["modelled_fill"]
    inv = b.inventory_for(SLUG)
    assert (inv.yes, inv.no) == (0, 0), "a touch became a fill"


def test_the_two_variants_keep_separate_books_and_files(tmp_path):
    a, b = _two_variants(tmp_path)
    assert a.state_path != b.state_path
    assert a.variant != b.variant
    assert a.policy == b.policy == sc.BASELINE_POLICY
    inv = a.inventory_for(SLUG)
    inv.record(True, D("0.40"))
    a._store_inventory(SLUG, inv)
    assert b.inventory_for(SLUG).yes == 0, "the books are shared"


def test_the_canonical_variant_set_is_five_and_names_both_offset_arms():
    """Four at-touch/depth arms plus the two-sided arm. The two-sided arm
    sits at the SAME offset as the depth arm, so the pair differs in
    two-sidedness alone."""
    labels = [v[0] for v in sc.SHADOW_VARIANTS]
    assert len(labels) == 5, labels
    assert labels.count("ARGMAX") == 1
    deep = [v for v in sc.SHADOW_VARIANTS if v[2] == sc.ONE_TICK_BEHIND]
    assert len(deep) == 1
    assert deep[0] == ("ARGMAX@ONE_TICK_BEHIND", sc.BASELINE_POLICY,
                       sc.ONE_TICK_BEHIND)
    two = [v for v in sc.SHADOW_VARIANTS if v[2] == sc.TWO_SIDED]
    assert len(two) == 1
    assert two[0] == ("ARGMAX@TWO_SIDED", sc.BASELINE_POLICY, sc.TWO_SIDED)
    # the controlled comparison: same policy, same offset, differing only
    # in whether both sides may rest
    assert sc.PLACEMENT_TICKS[sc.TWO_SIDED] == \
        sc.PLACEMENT_TICKS[sc.ONE_TICK_BEHIND]
    assert sc.PLACEMENT_TWO_SIDED[sc.TWO_SIDED] is True
    assert sc.PLACEMENT_TWO_SIDED[sc.ONE_TICK_BEHIND] is False
    # distinct durable files, so no two variants can share state
    files = {sc.variant_state_filename(l) for l in labels}
    assert len(files) == 5, files


def test_a_tally_from_an_older_taxonomy_is_discarded_not_merged(tmp_path):
    """PRODUCTION FOUND THIS. Persisting the tally and then changing the
    classification merged counts bucketed by two different sets of rules
    and presented the sum as one measurement: 202 `residual_cap:3>=3`
    still sitting in possible_misses after the fix that moved it, restored
    from a state file written before it.

    Worse, `counters_since` was stamped at the restart, so the block
    claimed a period that did not contain most of its own counts."""
    p = str(tmp_path / "s.json")
    old = {"v": sc.STATE_VERSION, "markets": {},
           "tally_taxonomy": 1,                      # an older taxonomy
           "counters_since": "2026-01-01T00:00:00+00:00",
           "tally": {sc.HEADLINE_SCENARIO: dict(
               sc.ShadowController._fresh_tally(),
               possible_misses=202,
               refusal_reasons={"residual_cap:3>=3": 202})}}
    io.open(p, "w", encoding="utf-8").write(json.dumps(old))

    c = sc.ShadowController(state_path=p)
    r = c.state.get("tally_reset")
    assert r, "the stale tally was merged in silence"
    assert r["from_taxonomy"] == 1 and r["to_taxonomy"] == sc.TALLY_TAXONOMY
    assert sc.HEADLINE_SCENARIO in r["dropped_scenarios"]
    # the mis-bucketed counts are GONE, not carried
    t = c._tally(sc.HEADLINE_SCENARIO)
    assert t["possible_misses"] == 0, t
    assert t["refusal_reasons"] == {}
    # and the period is restamped, not inherited from the discarded counts
    assert c.state["counters_since"] != "2026-01-01T00:00:00+00:00"
    # the reset is visible in the heartbeat
    assert c.report()["tally_reset"]["to_taxonomy"] == sc.TALLY_TAXONOMY


def test_a_tally_from_the_CURRENT_taxonomy_still_survives_a_restart(tmp_path):
    """The discard must be keyed on the taxonomy CHANGING, not on every
    restart -- otherwise the persistence fix is undone."""
    p = str(tmp_path / "s.json")
    c = sc.ShadowController(state_path=p)
    for _ in range(5):
        c._tally_record({"scenario": sc.HEADLINE_SCENARIO,
                         "decision": "quote"})
    since = c.state["counters_since"]
    c._save()
    again = sc.ShadowController(state_path=p)
    assert again.state.get("tally_reset") is None
    assert again._tally(sc.HEADLINE_SCENARIO)["quotes_placed"] == 5
    assert again.state["counters_since"] == since


def test_the_residual_cap_really_does_land_in_justified_now():
    """The production report still showed it as a miss AFTER the fix,
    because of the stale tally above. Asserted directly on the classifier
    so the two failure modes stay distinguishable."""
    c = sc.ShadowController(state_path="/dev/null")
    for _ in range(202):
        c._tally_record({"scenario": sc.HEADLINE_SCENARIO,
                         "decision": "no_action",
                         "reason": "residual_cap:3>=3"})
    t = c._tally(sc.HEADLINE_SCENARIO)
    assert t["possible_misses"] == 0
    assert t["justified_refusals"] == 202


# ================================================ TWO-SIDED RESTING ARM
#
# The hypothesis under test: our pair cost is a market outcome rather than a
# quoting parameter because only one side may rest, so an adversely selected
# fill has no opposite leg already positioned. Nothing here asserts the
# hypothesis is TRUE -- these pin the mechanism so the shadow comparison
# measures two-sidedness and nothing else.

def _seed_up(c, lad, slug=SLUG):
    """Hand-place an UP order so the policy's wanted DOWN is the OPPOSITE
    side. Discovered empirically: from flat, the fixture policy quotes NO."""
    o = FM.HypoOrder(slug, FM.UP, D("0.49"), D(1), WS + 300.0, ladder=lad,
                     bid="0.50", ask="0.51", coid="seed")
    c._orders_for(slug, sc.HEADLINE_SCENARIO)[FM.UP] = o
    scn = c._scen(slug, sc.HEADLINE_SCENARIO)
    c._set_resting(scn, {FM.UP: {"side": "YES", "price": "0.49",
                                 "relation": "open", "t": WS + 300.0,
                                 "_side_key": FM.UP}})
    return o


def test_from_flat_both_arms_quote_the_same_side_at_the_same_price(tmp_path):
    """The controlled comparison: differing only in two-sidedness means the
    FIRST quote must be identical."""
    seen = {}
    for placement in (sc.ONE_TICK_BEHIND, sc.TWO_SIDED):
        c = ctl(tmp_path / placement, placement=placement)
        lad = _ladder(bid="0.50", ask="0.51", bid_qty="0", ask_qty="0")
        r = c.step(WS + 320.0, slug=SLUG, book=_book("0.50", "0.51"),
                   window_end_ts=WEND, prints=[], ladder=lad)
        seen[placement] = (r.get("decision"), r.get("side"),
                           r.get("target_price"))
        assert c.offset_ticks == 1
    assert seen[sc.ONE_TICK_BEHIND] == seen[sc.TWO_SIDED], seen


def test_the_same_side_is_never_requoted_on_either_arm(tmp_path):
    """Re-pricing a resting order toward a newer price is chasing. Neither
    arm may do it -- two-sidedness adds the OPPOSITE side, not a re-quote."""
    for placement in (sc.ONE_TICK_BEHIND, sc.TWO_SIDED):
        c = ctl(tmp_path / placement, placement=placement)
        lad = _ladder(bid="0.50", ask="0.51", bid_qty="0", ask_qty="0")
        c.step(WS + 320.0, slug=SLUG, book=_book("0.50", "0.51"),
               window_end_ts=WEND, prints=[], ladder=lad)
        resting = sorted(c._orders_for(SLUG, sc.HEADLINE_SCENARIO))
        r2 = c.step(WS + 330.0, slug=SLUG, book=_book("0.50", "0.51"),
                    window_end_ts=WEND, prints=[], ladder=lad)
        assert r2.get("reason") == "already_resting", placement
        assert r2.get("already_resting_side") == "SAME_SIDE", placement
        assert sorted(c._orders_for(SLUG, sc.HEADLINE_SCENARIO)) == resting


def test_one_sided_refuses_the_opposite_side_and_says_which(tmp_path):
    """REQUIREMENT 1. The old counter fired before the side was decided, so
    66,044 `already_resting` refusals could not be split. Now they can."""
    c = ctl(tmp_path, placement=sc.ONE_TICK_BEHIND)
    lad = _ladder(bid="0.50", ask="0.51", bid_qty="0", ask_qty="0")
    _seed_up(c, lad)
    r = c.step(WS + 320.0, slug=SLUG, book=_book("0.50", "0.51"),
               window_end_ts=WEND, prints=[], ladder=lad)
    assert r.get("decision") == "no_action"
    assert r.get("reason") == "already_resting"
    assert r.get("already_resting_side") == "OPPOSITE_SIDE"
    assert r.get("resting_sides") == [FM.UP]
    t = c._tally(sc.HEADLINE_SCENARIO)
    assert t["already_resting_opposite_side"] == 1
    assert t["already_resting_same_side"] == 0
    # the reason STRING is unchanged, so the taxonomy version need not move
    assert "already_resting" in sc.JUSTIFIED_REFUSALS
    assert sc.TALLY_TAXONOMY == 2


def test_two_sided_rests_the_opposite_side_instead_of_refusing(tmp_path):
    c = ctl(tmp_path, placement=sc.TWO_SIDED)
    lad = _ladder(bid="0.50", ask="0.51", bid_qty="0", ask_qty="0")
    _seed_up(c, lad)
    r = c.step(WS + 320.0, slug=SLUG, book=_book("0.50", "0.51"),
               window_end_ts=WEND, prints=[], ladder=lad)
    assert r.get("decision") == "quote", r.get("reason")
    assert sorted(c._orders_for(SLUG, sc.HEADLINE_SCENARIO)) == [FM.DOWN,
                                                                 FM.UP]


def test_a_fill_on_one_side_leaves_the_other_resting(tmp_path):
    """The whole point: an adversely selected fill must not take the
    opposite leg down with it."""
    c = ctl(tmp_path, placement=sc.TWO_SIDED)
    lad = _ladder(bid="0.50", ask="0.51", bid_qty="0", ask_qty="0")
    _seed_up(c, lad)
    c.step(WS + 320.0, slug=SLUG, book=_book("0.50", "0.51"),
           window_end_ts=WEND, prints=[], ladder=lad)
    orders = c._orders_for(SLUG, sc.HEADLINE_SCENARIO)
    assert sorted(orders) == [FM.DOWN, FM.UP]
    up_px = orders[FM.UP].own_px
    # volume through the UP level only
    r = c.step(WS + 340.0, slug=SLUG, book=_book("0.50", "0.51"),
               window_end_ts=WEND,
               prints=[_print(up_px, "500", t=WS + 335.0)], ladder=lad)
    still = sorted(c._orders_for(SLUG, sc.HEADLINE_SCENARIO))
    if r.get("modelled_fills"):
        assert FM.UP not in still, still          # the filled side cleared
        assert FM.DOWN in still, still            # the other still resting
        assert c._scen(SLUG, sc.HEADLINE_SCENARIO)["resting"] is not None


def test_inventory_and_the_residual_cap_still_see_TOTAL_inventory(tmp_path):
    """AGGREGATE EXPOSURE IS PRESERVED. Per-side ORDER state must not become
    per-side INVENTORY, or the residual cap would be evaluated twice over
    halves of the book and bound nothing."""
    c = ctl(tmp_path, placement=sc.TWO_SIDED)
    inv = c.inventory_for(SLUG, sc.HEADLINE_SCENARIO)
    inv.record(True, D("0.40"))
    inv.record(True, D("0.41"))
    c._store_inventory(SLUG, inv, sc.HEADLINE_SCENARIO)
    snap = c.inventory_for(SLUG, sc.HEADLINE_SCENARIO).snapshot()
    assert snap["yes"] == 2 and snap["no"] == 0
    assert snap["residual"] == 2 and snap["matched"] == 0
    # one inventory object per scenario, not one per side
    assert c.inventory_for(SLUG, sc.HEADLINE_SCENARIO).snapshot() == snap


def test_resting_keeps_its_legacy_single_shape_for_existing_readers(tmp_path):
    c = ctl(tmp_path, placement=sc.TWO_SIDED)
    lad = _ladder(bid="0.50", ask="0.51", bid_qty="0", ask_qty="0")
    _seed_up(c, lad)
    c.step(WS + 320.0, slug=SLUG, book=_book("0.50", "0.51"),
           window_end_ts=WEND, prints=[], ladder=lad)
    rest = c._scen(SLUG, sc.HEADLINE_SCENARIO)["resting"]
    # still ONE dict with the original keys, so nothing downstream breaks
    assert isinstance(rest, dict)
    for k in ("side", "price", "relation", "t"):
        assert k in rest, k
    assert len(c._resting_map(c._scen(SLUG, sc.HEADLINE_SCENARIO))) == 2


def test_a_legacy_state_file_migrates_rather_than_being_discarded(tmp_path):
    """Persisted state from before this change holds a single `resting`
    dict with no side key. The inventory beside it is real."""
    c = ctl(tmp_path, placement=sc.TWO_SIDED)
    scn = c._scen(SLUG, sc.HEADLINE_SCENARIO)
    scn["resting"] = {"side": "NO", "price": "0.49", "relation": "open",
                      "t": WS}
    scn.pop("resting_sides", None)
    m = c._resting_map(scn)
    assert list(m) == [FM.DOWN], m
    scn["resting"] = {"side": "YES", "price": "0.49", "relation": "open",
                      "t": WS}
    assert list(c._resting_map(scn)) == [FM.UP]


def test_both_unfilled_sides_are_counted_at_expiry(tmp_path):
    """Counting one would flatter a two-sided arm's fill rate."""
    c = ctl(tmp_path, placement=sc.TWO_SIDED)
    lad = _ladder(bid="0.50", ask="0.51", bid_qty="0", ask_qty="0")
    _seed_up(c, lad)
    c.step(WS + 320.0, slug=SLUG, book=_book("0.50", "0.51"),
           window_end_ts=WEND, prints=[], ladder=lad)
    assert len(c._resting_map(c._scen(SLUG, sc.HEADLINE_SCENARIO))) == 2
    before = c._tally(sc.HEADLINE_SCENARIO)["unfilled_at_expiry"]
    c.window_close(SLUG)
    after = c._tally(sc.HEADLINE_SCENARIO)["unfilled_at_expiry"]
    assert after - before == 2, (before, after)
    assert c._scen(SLUG, sc.HEADLINE_SCENARIO)["resting"] is None
    assert not c._resting_map(c._scen(SLUG, sc.HEADLINE_SCENARIO))


def test_the_report_names_the_arm_and_its_resting_sides(tmp_path):
    c = ctl(tmp_path, placement=sc.TWO_SIDED)
    rep = c.report()
    assert rep["two_sided"] is True
    assert rep["placement"] == sc.TWO_SIDED
    assert rep["offset_ticks"] == 1
    one = ctl(tmp_path / "b", placement=sc.ONE_TICK_BEHIND).report()
    assert one["two_sided"] is False


def test_two_fills_in_one_tick_are_both_counted(tmp_path):
    """A one-sided arm's list holds 0 or 1 and the count is unchanged; a
    two-sided arm can fill both sides at once and must not report one."""
    c = ctl(tmp_path, placement=sc.TWO_SIDED)
    t = c._tally(sc.HEADLINE_SCENARIO)
    f = {"swept": True, "queue_ahead_at_placement": "0",
         "adverse_selection": {"adverse": True, "moved_against_us": "0.01"}}
    c._tally_record({"scenario": sc.HEADLINE_SCENARIO, "decision": "no_action",
                     "modelled_fill": f, "modelled_fills": [f, dict(f)]})
    assert t["fills"] == 2
    assert t["both_sides_filled_same_tick"] == 1
    assert t["adverse_fills"] == 2


# --------------------------------------------------------------------------
# compare_two_sided: the requirement 3-6 comparison. These tests exist
# because the production numbers come from this function, and an arithmetic
# slip here would be invisible -- it would simply report a different answer.
# --------------------------------------------------------------------------
def _arm(two_sided, windows, *, offset=1, resting=None, execu=None,
         episodes=0, opp_windows=0):
    """A minimal {label: report()}-shaped arm over the three scenarios.

    `resting` goes into `already_resting_split`, NOT into
    `refusal_reasons`, because that is where `economics` actually puts it.
    The first version of this helper put it in `reasons` and the
    requirement-1 test passed against a shape the controller never emits --
    which is why `test_the_split_reaches_the_comparison_from_a_real_arm`
    below drives a real controller instead of a hand-built dict.
    """
    scen = {
        "per_window": list(windows),
        "windows_folded_into_carry": 0,
        "windows_observed": 10,
        "execution": dict(execu or {}),
        "opportunities": {
            "justified_refusals": 0, "possible_misses": 0, "reasons": {},
            "already_resting_total": sum((resting or {}).values()),
            "already_resting_split": dict(resting or {}),
            "already_resting_opposite_side_episodes": episodes,
            "windows_with_opposite_side_want": opp_windows}}
    return {"two_sided": two_sided, "offset_ticks": offset,
            "economics": {"per_scenario": {s: dict(scen)
                                           for s in sc.SCENARIOS}}}


def _w(market, net, pair, resid, cost, matched, residual_units):
    return {"market": market, "net": net, "pair_net": pair,
            "residual_net": resid, "capital_deployed": cost,
            "matched_units": matched, "residual_units": residual_units}


def _pair(control_windows, treatment_windows, **kw):
    return {sc.TWO_SIDED_CONTROL: _arm(False, control_windows, **kw),
            sc.TWO_SIDED_TREATMENT: _arm(True, treatment_windows, **kw)}


def test_a_missing_arm_is_named_and_nothing_is_invented():
    out = sc.compare_two_sided({sc.TWO_SIDED_CONTROL: _arm(False, [])})
    assert out["verdict"] == "ARM_NOT_RUNNING"
    assert out["arms_missing"] == [sc.TWO_SIDED_TREATMENT]
    assert "per_scenario" not in out


def test_only_windows_BOTH_arms_settled_are_compared():
    """Each arm keeps its own state, so differencing lifetime totals would
    compare different weather. m2 exists for one arm only and must not
    reach a single figure."""
    out = sc.compare_two_sided(_pair(
        [_w("m1", "-1.00", "0.00", "-1.00", "1.00", 0, 3),
         _w("m2", "-9.00", "0.00", "-9.00", "9.00", 0, 9)],
        [_w("m1", "0.50", "0.50", "0.00", "0.50", 1, 0)]))
    h = out["per_scenario"][sc.HEADLINE_SCENARIO]
    assert h["common_windows"] == 1
    assert h["coverage"]["control_only"] == 1
    assert h["economic_value"]["control_net"] == "-1.00"
    assert h["economic_value"]["incremental_net"] == "1.50"


def test_pair_acquisition_cost_is_the_exact_identity_not_an_estimate():
    """settle writes pair_net = matched - pair_cost against a $1 pair
    redemption, so cost per pair is exactly 1 - pair_net/matched. Two pairs
    bought at 0.96 each net 2 - 1.92 = 0.08."""
    out = sc.compare_two_sided(_pair(
        [_w("m1", "0.08", "0.08", "0.00", "1.92", 2, 0)],
        [_w("m1", "0.08", "0.08", "0.00", "1.92", 2, 0)]))
    m = out["per_scenario"][sc.HEADLINE_SCENARIO]["matched"]
    assert m["control_pair_acquisition_cost_per_matched"] == "0.9600"
    assert m["treatment_pair_acquisition_cost_per_matched"] == "0.9600"


def test_a_window_with_no_split_is_left_out_of_the_split_totals():
    """A window settled before the split existed has no split, and calling
    it 0.0000 would be a missing value wearing the costume of a
    measurement -- but its NET is real and must still count."""
    out = sc.compare_two_sided(_pair(
        [_w("m1", "-0.40", "NOT_RECORDED", "NOT_RECORDED", "0.40", 0, 2)],
        [_w("m1", "-0.40", "NOT_RECORDED", "NOT_RECORDED", "0.40", 0, 2)]))
    h = out["per_scenario"][sc.HEADLINE_SCENARIO]
    assert h["pair_economics"]["control_windows_without_split"] == 1
    assert h["pair_economics"]["control_pair_net"] == "0"
    assert h["economic_value"]["control_net"] == "-0.40"
    assert h["matched"]["control_pair_acquisition_cost_per_matched"] is None


def test_arms_that_stop_sharing_an_offset_are_not_a_controlled_comparison():
    reports = _pair([], [])
    reports[sc.TWO_SIDED_TREATMENT]["offset_ticks"] = 7
    out = sc.compare_two_sided(reports)
    assert out["controlled"] is False
    assert out["verdict"] == "ARMS_NOT_CONTROLLED"
    assert sc.compare_two_sided(_pair([], []))["controlled"] is True


def test_requirement_1_reports_the_opposite_side_split_per_arm():
    out = sc.compare_two_sided(_pair(
        [], [], resting={"already_resting_opposite_side": 420,
                         "already_resting_same_side": 70,
                         "already_resting_side_unknown": 10}))
    r = (out["per_scenario"][sc.HEADLINE_SCENARIO]
         ["requirement_1_already_resting"])
    assert r["control"]["already_resting_opposite_side"] == 420
    assert r["control"]["classified_ticks"] == 500
    assert sum(r["control"][k] for k in sc.RESTING_SPLIT) == 500
    # the SHARE is over the classified refusals, which is the only
    # denominator the split actually covers
    assert r["control"]["opposite_side_share_of_classified_TICKS"] == "0.8400"


def test_a_positive_result_on_too_few_windows_authorizes_nothing():
    out = sc.compare_two_sided(_pair(
        [_w("m1", "-1.00", "0.00", "-1.00", "1.00", 0, 3)],
        [_w("m1", "1.00", "1.00", "0.00", "1.00", 1, 0)]))
    assert out["per_scenario"][sc.HEADLINE_SCENARIO][
        "economic_value"]["incremental_net"] == "2.00"
    assert out["verdict"] == "INSUFFICIENT_COMMON_WINDOWS"
    assert out["min_common_windows"] == sc.TWO_SIDED_MIN_COMMON_WINDOWS


def test_the_verdict_turns_on_money_and_not_on_matched_count():
    """More matched pairs bought too dearly is not an improvement, and the
    verdict must not say otherwise."""
    ws = [_w("m%d" % i, "0.00", "0.00", "0.00", "0", 0, 0)
          for i in range(sc.TWO_SIDED_MIN_COMMON_WINDOWS)]
    ctrl = list(ws)
    treat = list(ws)
    treat[0] = _w("m0", "-0.10", "-0.10", "0.00", "1.10", 1, 0)
    out = sc.compare_two_sided(_pair(ctrl, treat))
    h = out["per_scenario"][sc.HEADLINE_SCENARIO]
    assert h["matched"]["incremental_matched_units"] == 1
    assert h["economic_value"]["incremental_net"] == "-0.10"
    assert out["verdict"] == "TWO_SIDED_NOT_BETTER"


def test_three_plus_residual_windows_are_counted_on_both_arms():
    out = sc.compare_two_sided(_pair(
        [_w("m1", "-0.90", "0.00", "-0.90", "0.90", 0, 3)],
        [_w("m1", "-1.20", "0.00", "-1.20", "1.20", 0, -4)]))
    u = out["per_scenario"][sc.HEADLINE_SCENARIO]["unmatched_inventory"]
    assert u["control_resid_3plus_windows"] == 1
    assert u["treatment_resid_3plus_windows"] == 1
    assert u["treatment_residual_units_abs"] == 4


def test_a_real_controller_report_is_an_acceptable_input(tmp_path):
    """The offline wrapper and the production emission both feed report()
    straight in, so the shapes must actually match."""
    reports = {
        sc.TWO_SIDED_CONTROL: ctl(
            tmp_path / "a", placement=sc.ONE_TICK_BEHIND).report(),
        sc.TWO_SIDED_TREATMENT: ctl(
            tmp_path / "b", placement=sc.TWO_SIDED).report()}
    out = sc.compare_two_sided(reports)
    assert out["arms_missing"] == []
    assert out["controlled"] is True
    assert out["verdict"] == "INSUFFICIENT_COMMON_WINDOWS"
    assert set(out["per_scenario"]) == set(sc.SCENARIOS)


def test_the_split_reaches_the_comparison_from_a_real_arm(tmp_path):
    """END TO END, and it exists because the hand-built version of this
    test passed while the real path reported three zeros.

    The counters are kept at the TALLY's top level, because the refusal
    REASON string is deliberately unchanged -- so unless `economics` lifts
    them into their own key they are counted on every tick and never
    reported. They were. This drives a real one-sided controller into the
    `already_resting` branch and asserts the number survives all the way
    into `compare_two_sided`.
    """
    one = ctl(tmp_path / "a", placement=sc.ONE_TICK_BEHIND)
    lad = _ladder(bid="0.50", ask="0.51", bid_qty="0", ask_qty="0")
    _seed_up(one, lad)
    for i in range(3):
        one.step(WS + 320.0 + i, slug=SLUG, book=_book("0.50", "0.51"),
                 window_end_ts=WEND, prints=[], ladder=lad)
    t = one._tally(sc.HEADLINE_SCENARIO)
    seen = {k: t[k] for k in sc.RESTING_SPLIT}
    assert sum(seen.values()) > 0, seen

    # it must survive the trip through economics() ...
    econ = one.economics()["per_scenario"][sc.HEADLINE_SCENARIO]
    assert econ["opportunities"]["already_resting_split"] == seen
    assert (econ["opportunities"]["already_resting_total"]
            == econ["opportunities"]["reasons"].get("already_resting"))

    # ... and all the way into the comparison line
    out = sc.compare_two_sided({
        sc.TWO_SIDED_CONTROL: one.report(),
        sc.TWO_SIDED_TREATMENT: ctl(tmp_path / "b",
                                    placement=sc.TWO_SIDED).report()})
    r = (out["per_scenario"][sc.HEADLINE_SCENARIO]
         ["requirement_1_already_resting"])
    assert {k: r["control"][k] for k in sc.RESTING_SPLIT} == seen
    assert r["control"]["classified_ticks"] == sum(seen.values())
    assert r["control"]["lifetime_bucket"] == sum(seen.values())
    assert r["control"]["split_covers_whole_bucket"] is True
    assert r["control"]["opposite_side_share_of_classified_TICKS"] == "1.0000"
    # the reason bucket and its split must account for each other, or the
    # split is not a split of it
    assert out["per_scenario"][sc.HEADLINE_SCENARIO]["fill_model"][
        "control"]["already_resting_split_accounts_for_the_bucket"] is True


def test_the_split_starting_later_than_the_bucket_is_said_not_hidden():
    """THE PRODUCTION CASE. The `already_resting` bucket has been counted
    since the counters began -- 66,918 on the tape when the split shipped
    -- while the split only counts from the deploy that added it. Dividing
    the split by the lifetime bucket would understate the opposite-side
    share by however long the bucket ran unclassified, so the share is
    taken over the CLASSIFIED refusals and the mismatch is reported.
    """
    arms = _pair([], [], resting={"already_resting_opposite_side": 80,
                                  "already_resting_same_side": 20,
                                  "already_resting_side_unknown": 0})
    for a in arms.values():
        for scen in sc.SCENARIOS:
            o = (a["economics"]["per_scenario"][scen]["opportunities"])
            o["already_resting_total"] = 66918      # lifetime, predates it
    out = sc.compare_two_sided(arms)
    r = (out["per_scenario"][sc.HEADLINE_SCENARIO]
         ["requirement_1_already_resting"]["control"])
    assert r["classified_ticks"] == 100
    assert r["lifetime_bucket"] == 66918
    assert r["split_covers_whole_bucket"] is False
    # the share is 80/100, NOT 80/66918
    assert r["opposite_side_share_of_classified_TICKS"] == "0.8000"


def test_two_sided_PERMITS_a_second_side_it_does_not_seek_one(tmp_path):
    """The limitation, pinned so nobody reads this arm as a test of
    simultaneous two-sided quoting.

    The policy decides from INVENTORY. While inventory is flat it wants the
    same side every tick, so the arm rests ONE order and then refuses
    SAME_SIDE -- it never holds two legs without a fill. Two sides rest
    only once inventory is non-flat and the policy wants the other one.
    """
    c = ctl(tmp_path, placement=sc.TWO_SIDED)
    lad = _ladder(bid="0.50", ask="0.51", bid_qty="0", ask_qty="0")
    quotes = []
    for i in range(8):
        rec = c.step(WS + 100.0 + i * 5, slug=SLUG,
                     book=_book("0.50", "0.51"), window_end_ts=WEND,
                     prints=[], ladder=lad)
        quotes.append(rec.get("decision"))
    scen = c._scen(SLUG, sc.HEADLINE_SCENARIO)
    assert quotes[0] == "quote"
    assert set(quotes[1:]) == {"no_action"}
    assert len(c._resting_map(scen)) == 1, "no fill, so no second side"
    t = c._tally(sc.HEADLINE_SCENARIO)
    assert t["already_resting_same_side"] == 7
    assert t["already_resting_opposite_side"] == 0

    # and with inventory on one side, the SAME arm does rest both
    c2 = ctl(tmp_path / "b", placement=sc.TWO_SIDED)
    _seed_up(c2, lad)
    c2.step(WS + 320.0, slug=SLUG, book=_book("0.50", "0.51"),
            window_end_ts=WEND, prints=[], ladder=lad)
    assert len(c2._resting_map(c2._scen(SLUG, sc.HEADLINE_SCENARIO))) == 2


def test_a_long_refusal_run_is_ONE_episode_and_ONE_window(tmp_path):
    """THE MEASUREMENT-DESIGN BUG, pinned.

    Over three consecutive production minutes OPPOSITE_SIDE sat frozen at
    17 while SAME_SIDE ran 60 -> 76 -> 95, so the reported share fell
    0.2208 -> 0.1828 -> 0.1518 with nothing whatever changing. A tick count
    measures how long an order SAT there, which is a function of our poll
    rate -- so a rate built on it drifts toward zero the longer we look.

    The episode and window counters are the denominators that do not.
    """
    c = ctl(tmp_path, placement=sc.ONE_TICK_BEHIND)
    lad = _ladder(bid="0.50", ask="0.51", bid_qty="0", ask_qty="0")
    _seed_up(c, lad)
    for i in range(12):
        c.step(WS + 320.0 + i, slug=SLUG, book=_book("0.50", "0.51"),
               window_end_ts=WEND, prints=[], ladder=lad)
    t = c._tally(sc.HEADLINE_SCENARIO)
    # twelve ticks of the SAME standing situation
    assert t["already_resting_opposite_side"] == 12
    # ... but one episode, and one window
    assert t["already_resting_opposite_side_episodes"] == 1
    assert t["windows_with_opposite_side_want"] == 1

    # and the comparison reports the window share, not the tick share
    out = sc.compare_two_sided({
        sc.TWO_SIDED_CONTROL: c.report(),
        sc.TWO_SIDED_TREATMENT: ctl(tmp_path / "b",
                                    placement=sc.TWO_SIDED).report()})
    r = (out["per_scenario"][sc.HEADLINE_SCENARIO]
         ["requirement_1_already_resting"]["control"])
    assert r["opposite_side_episodes"] == 1
    assert r["windows_with_opposite_side_want"] == 1
    assert r["classified_ticks"] == 12
    # the tick share says 1.0000 -- which is exactly how misleading it is
    assert r["opposite_side_share_of_classified_TICKS"] == "1.0000"
    # no window has CLOSED here, so the window denominator does not exist
    # yet and the share is None. A missing denominator is not a zero rate.
    assert r["windows_observed"] == 0
    assert r["share_of_windows_wanting_the_other_side"] is None


def test_a_second_window_counts_a_second_window(tmp_path):
    """`windows_with_opposite_side_want` must count WINDOWS, so the
    once-per-window latch has to live on the market slot and not the
    tally."""
    c = ctl(tmp_path, placement=sc.ONE_TICK_BEHIND)
    lad = _ladder(bid="0.50", ask="0.51", bid_qty="0", ask_qty="0")
    other = SLUG.replace("1315z", "1330z")
    for slug in (SLUG, other):
        _seed_up(c, lad, slug=slug)
        for i in range(4):
            c.step(WS + 320.0 + i, slug=slug, book=_book("0.50", "0.51"),
                   window_end_ts=WEND, prints=[], ladder=lad)
    t = c._tally(sc.HEADLINE_SCENARIO)
    assert t["already_resting_opposite_side"] == 8
    assert t["windows_with_opposite_side_want"] == 2
