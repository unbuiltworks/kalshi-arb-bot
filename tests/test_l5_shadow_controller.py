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
from decimal import Decimal as D

import pytest

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


def _book(bid="0.50", ask="0.51"):
    return {"bid": D(bid), "ask": D(ask)}


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

def _book_against(rest, *, through):
    """A book that trades THROUGH our resting order, or arrives exactly AT
    it, in whichever side's own price it rests.

    The side is not ours to assume: an open from flat inventory takes the
    cheaper maker join, which on a 0.50/0.51 book is NO at `1 - ask`. Our
    YES buy rests as a bid at px and is taken out by an ask strictly BELOW
    px; our NO buy at NO-price px rests as a YES ask at `1 - px` and is
    taken out by a bid strictly ABOVE that. A test that hard-codes the YES
    branch passes the other side's case for the wrong reason.
    """
    px = D(rest["price"])
    if rest["side"] == "YES":
        ask = px - D("0.01") if through else px
        return {"bid": ask - D("0.01"), "ask": ask}
    bid = (D(1) - px) + (D("0.01") if through else D(0))
    return {"bid": bid, "ask": bid + D("0.01")}


def test_a_touch_at_our_price_is_not_a_fill(tmp_path):
    c = ctl(tmp_path)
    c.step(WS + 310.0, slug=SLUG, book=_book(), window_end_ts=WEND)
    rest = c._slot(SLUG)["resting"]
    assert rest is not None
    # the touch arrives exactly AT us: executability, not execution
    rec = c.step(WS + 320.0, slug=SLUG, book=_book_against(rest, through=False),
                 window_end_ts=WEND)
    assert rec.get("modelled_fill") is None
    assert c.inventory_for(SLUG).snapshot() == {"yes": 0, "no": 0,
                                                "matched": 0, "residual": 0}


def test_a_print_strictly_through_us_advances_inventory(tmp_path):
    c = ctl(tmp_path)
    c.step(WS + 310.0, slug=SLUG, book=_book(), window_end_ts=WEND)
    rest = c._slot(SLUG)["resting"]
    rec = c.step(WS + 320.0, slug=SLUG, book=_book_against(rest, through=True),
                 window_end_ts=WEND)
    fill = rec.get("modelled_fill")
    assert fill is not None
    assert fill["filled_by"] == sc.FILL_RULE
    assert fill["side"] == rest["side"]
    assert fill["price"] == rest["price"]
    assert "UPPER BOUND" in fill["labels"]
    snap = c.inventory_for(SLUG).snapshot()
    assert snap["yes" if rest["side"] == "YES" else "no"] == 1


@pytest.mark.parametrize("bid,ask,side", [("0.50", "0.51", "NO"),
                                          ("0.48", "0.50", "YES")])
def test_the_fill_rule_holds_on_both_resting_sides(tmp_path, bid, ask, side):
    """Both branches of the rule exercised on a book that actually produces
    that side, so neither is covered only by accident."""
    c = ctl(tmp_path)
    c.step(WS + 310.0, slug=SLUG, book=_book(bid, ask), window_end_ts=WEND)
    rest = c._slot(SLUG)["resting"]
    assert rest["side"] == side, rest
    at = c.step(WS + 320.0, slug=SLUG, book=_book_against(rest, through=False),
                window_end_ts=WEND)
    assert at.get("modelled_fill") is None
    through = c.step(WS + 330.0, slug=SLUG,
                     book=_book_against(c._slot(SLUG)["resting"] or rest,
                                        through=True),
                     window_end_ts=WEND)
    assert (through.get("modelled_fill") or {}).get("filled_by") == sc.FILL_RULE


def test_the_fill_rule_is_named_in_every_record(tmp_path):
    c = ctl(tmp_path)
    rec = c.step(WS + 310.0, slug=SLUG, book=_book(), window_end_ts=WEND)
    assert rec["fill_rule"] == "TOUCH_THROUGH_OPTIMISTIC"


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
    assert out["matched_units"] == 1 and out["residual_units"] == 1
    assert out["cost_total"] == "1.46"
    assert out["redemption_total"] == "2"
    assert out["modelled_net"] == "0.54"
    assert out["maker_fee_usd"] == "0.0000"
    assert "NOT realized P&L" in out["labels"]


def test_a_losing_residual_settles_worthless(tmp_path):
    c = ctl(tmp_path)
    inv = c.inventory_for(SLUG)
    inv.record(True, D("0.65"))
    c._store_inventory(SLUG, inv)
    out = c.settle(SLUG, D("0"))          # YES loses
    assert out["residual_units"] == 1
    assert out["redemption_total"] == "0"
    assert out["modelled_net"] == "-0.65"


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
    c.step(WS + 3.0, slug=SLUG, book=bk, window_end_ts=WEND)
    fill = _fill_the_resting(c, WS + 6.0, SLUG)["modelled_fill"]
    assert fill and fill["side"] == "YES" and fill["price"] == "0.37"
    completes = []
    for i in range(3, 300):
        rec = c.step(WS + i * 3.0, slug=SLUG, book=bk, window_end_ts=WEND)
        if rec.get("relation") == "complete" and rec.get("decision") == "quote":
            completes.append(rec)
    assert completes, "no inventory-driven COMPLETE was ever produced"
    rec = completes[0]
    # the DEFICIT side, from our residual -- we hold YES, so we buy NO
    assert rec["side"] == "NO"
    # priced from OUR average cost under the pair cap, NOT at the touch.
    # the NO touch here is 1 - 0.38 = 0.62; a pair at the touch would cost
    # 0.99, which the 0.96 spring cap refuses.
    assert D(rec["target_price"]) == D("0.59")
    assert D("0.37") + D(rec["target_price"]) <= sc.l5d.BTC15M_PAIR_CAP
    assert D(rec["target_price"]) < D(1) - D("0.38")


def test_the_spring_pair_cap_is_what_binds_the_completion(tmp_path):
    """0.96, not the sports 0.98 and not the incentive-era near-$1: the
    completion price moves with the cap, so the cap is what sets it."""
    from src import pmus_l5_persistence_v2 as pv2
    seen = {}
    for cap in (D("0.96"), D("0.92")):
        c = sc.ShadowController(state_path=str(tmp_path / ("s%s.json" % cap)),
                                model=pv2.load_v2(), pair_cap=cap)
        bk = _book("0.37", "0.38")
        c.step(WS + 3.0, slug=SLUG, book=bk, window_end_ts=WEND)
        _fill_the_resting(c, WS + 6.0, SLUG)
        for i in range(3, 300):
            rec = c.step(WS + i * 3.0, slug=SLUG, book=bk, window_end_ts=WEND)
            if rec.get("relation") == "complete" and rec.get("decision") == "quote":
                seen[cap] = D(rec["target_price"])
                break
    assert seen[D("0.96")] == D("0.59")
    assert seen[D("0.92")] == D("0.55")


def test_the_default_cap_is_the_btc15m_spring_target(tmp_path):
    assert ctl(tmp_path).pair_cap == D("0.96")
    assert sc.l5d.BTC15M_PAIR_CAP == D("0.96")


# ======================================== THE STATE FILE STAYS BOUNDED

def _settle_n(c, n, *, start=0):
    for i in range(start, start + n):
        slug = "w%04d" % i
        inv = c.inventory_for(slug)
        inv.record(True, D("0.40"))
        inv.record(False, D("0.50"))
        c._store_inventory(slug, inv)
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
    assert before["windows_settled"] == sc.SETTLED_KEEP
    assert before["windows_folded_into_carry"] == 0
    _settle_n(c, 50, start=sc.SETTLED_KEEP)
    after = c.economics()
    assert after["windows_settled"] == sc.SETTLED_KEEP + 50
    assert after["windows_folded_into_carry"] == 50
    assert len([k for k in c.state["markets"] if k.startswith("w")]) \
        == sc.SETTLED_KEEP
    # each window: cost 0.90, redemption 1.00 (one matched pair)
    assert D(after["cost_total"]) == D("0.90") * (sc.SETTLED_KEEP + 50)
    assert D(after["redemption_total"]) == D(sc.SETTLED_KEEP + 50)


def test_the_carry_survives_a_restart(tmp_path):
    c = ctl(tmp_path)
    _settle_n(c, sc.SETTLED_KEEP + 10)
    net = c.economics()["modelled_net"]
    again = ctl(tmp_path)
    assert again.economics()["modelled_net"] == net
    assert again.economics()["windows_folded_into_carry"] == 10


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
