"""Tests for the 24/7 non-trading completion-economics shadow.

Two classes of property matter: the arithmetic that decides good completion
from bad, and the guarantee that this process cannot trade.
"""

import ast
import json
from decimal import Decimal as D

import pytest

from src.pmus_completion_shadow import (PAR, classify, complement_touch,
                                        completion_economics, cycle, heartbeat,
                                        propose)


def book(bid="0.40", ask="0.42", slug="m", state="MARKET_STATE_OPEN"):
    return {"slug": slug, "state": state, "bid": D(bid), "ask": D(ask),
            "bid_depth": 5, "ask_depth": 5, "shares_traded": D("100")}


# ------------------------------ the complement side ------------------------------

def test_complement_of_a_no_surplus_is_the_yes_bid_not_one_minus_ask():
    """The bug this test exists for: on a 0.90/0.94 book a NO surplus at 0.06
    pairs with YES at the 0.90 bid for 0.96. Reading the complement as
    1-ask gives 0.06 and a 'pair' of 0.12, which is not a pair at all."""
    b = book("0.90", "0.94")
    assert complement_touch(b, "NO") == D("0.90")
    assert complement_touch(b, "YES") == D("0.06")


def test_pair_cost_uses_the_side_the_surplus_is_actually_on():
    b = book("0.90", "0.94")
    no_side = propose(b, D(5), D(5) * D("0.06"), D(0), D(0), surplus_side="NO")
    assert no_side["marginal_pair_cost"] == "0.96"
    assert no_side["complement_side"] == "YES"


def test_complement_refuses_a_book_with_no_quotes():
    assert complement_touch({"bid": None, "ask": D("0.5")}, "YES") is None


# ------------------------------ marginal vs weighted ------------------------------

def test_marginal_and_resulting_pair_cost_can_disagree():
    """The 2026-09-09 finding: each marginal pair can look acceptable while
    the inventory-weighted total lands above par. If these two ever collapse
    into one number the whole study is lost."""
    # 10 pairs already on the book at 0.90; now complete 5 more at 1.20
    e = completion_economics(surplus_qty=D(5), surplus_spend=D(5) * D("0.60"),
                             matched_qty=D(10), matched_spend=D(9),
                             complement_px=D("0.60"), fill_qty=D(5),
                             regime="l5_touch")
    assert e["marginal_pair_cost"] == "1.20"
    assert e["inventory_weighted_pair_cost_before"] == "0.9"
    assert D(e["resulting_pair_cost_if_filled"]) == D(15) / D(15)
    assert e["above_par_marginal"] is True
    # they diverge here, and the CLASSIFICATION must follow the finished
    # book: an above-par marginal that still lands the book at par is a
    # good completion. Goes red if classify() is pointed at the margin.
    assert e["above_par_resulting"] is False
    assert e["classification"].startswith("good_completion")


def test_resulting_above_par_is_classified_as_a_bad_completion():
    e = completion_economics(D(10), D(10) * D("0.70"), D(0), D(0),
                             complement_px=D("0.50"), fill_qty=D(10),
                             regime="l5_touch")
    assert e["marginal_pair_cost"] == "1.20"
    assert e["classification"].startswith("bad_completion")
    assert e["above_par_resulting"] is True


def test_resulting_at_or_below_par_is_a_good_completion():
    e = completion_economics(D(10), D(10) * D("0.40"), D(0), D(0),
                             complement_px=D("0.55"), fill_qty=D(10),
                             regime="l5_touch")
    assert e["classification"].startswith("good_completion")


def test_classification_is_decided_on_the_finished_book_not_the_margin():
    """Goes red if classify() is ever pointed at the marginal pair."""
    assert classify(None) == "no_pairs"
    assert classify(PAR).startswith("good_completion")
    assert classify(PAR + D("0.0001")).startswith("bad_completion")


def test_residual_before_and_after_are_both_recorded():
    e = completion_economics(D(8), D(4), D(0), D(0), D("0.5"), D(3), "l5_touch")
    assert (e["residual_before"], e["residual_after"]) == ("8", "5")


def test_fill_is_capped_by_the_residual_it_can_pair():
    e = completion_economics(D(2), D(1), D(0), D(0), D("0.5"), D(99), "l5_touch")
    assert e["fill_qty_modelled"] == "2" and e["residual_after"] == "0"


# ------------------------------ the pair_cost_max reading ------------------------------

def test_l5_touch_regime_is_recorded_as_ungated():
    """The answer to the owner's question, pinned. Under l5_touch the pricer
    returns pair_cost_max None and labels the pair 'reported not gated', so
    0.98 travels as an observation cap only."""
    e = completion_economics(D(5), D(5) * D("0.60"), D(0), D(0),
                             D("0.60"), D(5), "l5_touch")
    assert e["pair_cost_max_gates_this_regime"] is False
    assert e["pair_cost_max_permits"] is None
    assert e["observation_cap"] == "0.98"


def test_fee_safe_regime_is_gated_and_the_verdict_is_recorded():
    over = completion_economics(D(5), D(5) * D("0.60"), D(0), D(0),
                                D("0.60"), D(5), "fee_safe")
    under = completion_economics(D(5), D(5) * D("0.40"), D(0), D(0),
                                 D("0.50"), D(5), "fee_safe")
    assert over["pair_cost_max_gates_this_regime"] is True
    assert over["pair_cost_max_permits"] is False      # 1.20 > 0.98
    assert under["pair_cost_max_permits"] is True      # 0.90 <= 0.98


# ------------------------------ the loop ------------------------------

def test_cycle_seeds_the_cheaper_touch_and_remembers_its_side():
    inv = {}
    rows = cycle(["m"], inv, getter=lambda p: {"marketData": {
        "marketSlug": "m", "state": "MARKET_STATE_OPEN",
        "bestBid": {"value": "0.90"}, "bestAsk": {"value": "0.94"},
        "sharesTraded": "10", "bidDepth": 1, "askDepth": 1}})
    assert inv["m"]["side"] == "NO"          # 1-0.94 = 0.06 is cheaper than 0.90
    assert rows[0]["surplus_side"] == "NO"
    assert rows[0]["marginal_pair_cost"] == "0.96"


def test_a_market_that_fails_to_read_is_skipped_not_fatal():
    def getter(p):
        raise TimeoutError("boom")
    assert cycle(["bad"], {}, getter=getter) == []


def test_heartbeat_reports_the_non_trading_flags(tmp_path):
    hb = heartbeat(3, 12, 40, 0.0, out_dir=str(tmp_path))
    assert hb["places_orders"] is False and hb["read_only"] is True
    assert (tmp_path / "heartbeat.json").exists()


def test_heartbeat_survives_an_unwritable_state_dir():
    hb = heartbeat(1, 1, 1, 0.0, out_dir="/proc/nonexistent/nope")
    assert "heartbeat_write_failed" in hb


# ------------------------------ cannot trade, by AST ------------------------------

def _module_ast():
    import src.pmus_completion_shadow as m
    tree = ast.parse(open(m.__file__, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            b = node.body
            if b and isinstance(b[0], ast.Expr) and \
                    isinstance(b[0].value, ast.Constant) and \
                    isinstance(b[0].value.value, str):
                node.body = b[1:]
    return tree


def test_module_contains_no_write_verb():
    verbs = {"POST", "PUT", "DELETE", "PATCH"}
    found = {n.value for n in ast.walk(_module_ast())
             if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and n.value.upper() in verbs}
    assert not found, f"write verb in code: {found}"


def test_the_write_verb_check_catches_a_real_violation():
    tree = ast.parse('def go():\n    return req(method="POST")\n')
    found = {n.value for n in ast.walk(tree)
             if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and n.value.upper() in {"POST", "PUT", "DELETE", "PATCH"}}
    assert found == {"POST"}


def test_module_imports_no_executor_auth_or_session():
    banned = ("executor", "auth", "spring_l5_session", "micro_executor",
              "fill_ledger", "credential")
    mods = []
    for n in ast.walk(_module_ast()):
        if isinstance(n, ast.Import):
            mods += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            mods.append(n.module or "")
    bad = [m for m in mods if any(b in m.lower() for b in banned)]
    assert not bad, f"order/credential import: {bad}"


def test_the_dockerfile_copies_no_order_transport():
    """The container is the real boundary: if the executor is not in the
    image, the process cannot place an order however it is invoked."""
    txt = open("deploy/completion_shadow/Dockerfile", encoding="utf-8").read()
    copied = [l for l in txt.splitlines() if l.strip().startswith("COPY")]
    banned = ("executor", "auth", "spring_l5_session", "fill_ledger", "q1")
    bad = [l for l in copied if any(b in l.lower() for b in banned)]
    assert not bad, f"order transport copied into the image: {bad}"
    assert any("pmus_completion_shadow.py" in l for l in copied)


# ---- the multi-completion PATH: the failure mode the entry pair cannot show

from decimal import Decimal as _D

from src.pmus_completion_shadow import (advance_path, flow_delta,
                                        flow_summary, side_hint)


def _st(qty="10", spend="4.0", side="YES"):
    return {"qty": _D(qty), "spend": _D(spend), "matched": _D(0),
            "matched_spend": _D(0), "side": side, "flow": [], "prev": None,
            "tranches": 0}


def test_repeated_completion_at_rising_prices_degrades_the_weighted_average():
    """The 2026-09-09 mechanism, reproduced: every tranche can look sane
    while the finished book lands above par."""
    st = _st(qty="10", spend="4.0")             # surplus avg 0.40
    for px in ("0.55", "0.62", "0.70", "0.78"):  # the market runs away
        advance_path(st, _D(px), _D("2.5"))
    assert st["qty"] == 0 and st["tranches"] == 4
    weighted = st["matched_spend"] / st["matched"]
    assert weighted > _D("1.00"), weighted
    assert classify(weighted).startswith("bad_completion")


def test_a_stable_book_completes_below_par_over_the_same_path():
    st = _st(qty="10", spend="4.0")
    for _ in range(4):
        advance_path(st, _D("0.55"), _D("2.5"))
    assert st["matched_spend"] / st["matched"] == _D("0.95")


def test_a_tranche_never_exceeds_the_remaining_surplus():
    st = _st(qty="2", spend="1.0")
    advance_path(st, _D("0.5"), _D("99"))
    assert st["qty"] == 0 and st["matched"] == _D(2)
    advance_path(st, _D("0.5"), _D("5"))        # nothing left to pair
    assert st["tranches"] == 1


def test_surplus_average_is_preserved_as_the_surplus_is_drawn_down():
    """Goes red if drawing down the surplus silently re-prices what remains."""
    st = _st(qty="10", spend="4.0")
    advance_path(st, _D("0.55"), _D("4"))
    assert st["spend"] / st["qty"] == _D("0.40")


# ---- flow measured on the SAME poll as the completion

def _s(shares, t, last="0.50", bid="0.49", ask="0.51"):
    return {"shares_traded": _D(shares), "last_trade_px": _D(last),
            "bid": _D(bid), "ask": _D(ask), "t": t}


def test_flow_is_the_first_difference_not_the_lifetime_counter():
    assert flow_delta(_s("500000", 0.0), _s("500000", 60.0), 60.0)["shares"] == 0


def test_flow_gap_is_not_zero_flow():
    assert flow_delta(None, _s("1", 0.0), 0.0)["gap"] is True
    assert flow_delta(_s("500", 0.0), _s("100", 60.0), 60.0)["why"] == \
        "counter_went_backwards"


def test_flow_summary_excludes_gaps_from_the_rate():
    s = flow_summary([{"gap": True, "why": "x"},
                      flow_delta(_s("0", 0.0), _s("60", 60.0), 60.0)])
    assert s["intervals"] == 1 and s["gaps"] == 1
    assert s["shares_per_min"] == pytest.approx(60.0)


def test_two_sided_needs_both_a_bid_print_and_an_ask_print():
    one = [flow_delta(_s("0", 0.0, last="0.49"), _s("5", 60.0, last="0.49"), 60.0)]
    assert flow_summary(one)["two_sided"] is False
    both = one + [flow_delta(_s("5", 60.0, last="0.49"),
                             _s("9", 120.0, last="0.51"), 120.0)]
    assert flow_summary(both)["two_sided"] is True


def test_side_hint_refuses_a_crossed_book():
    assert side_hint({"last_trade_px": _D("0.5"), "bid": _D("0.6"),
                      "ask": _D("0.4")}) is None


def test_every_completion_row_carries_the_flow_of_the_same_market():
    """The join has to exist on the row, or the two variables can never be
    crossed at the same instant on the same market."""
    inv = {}
    n = {"i": 0}

    def getter(p):
        n["i"] += 1
        return {"marketData": {"marketSlug": "m", "state": "MARKET_STATE_OPEN",
                               "bestBid": {"value": "0.45"},
                               "bestAsk": {"value": "0.47"},
                               "lastTradePx": {"value": "0.45"},
                               "sharesTraded": str(n["i"] * 10),
                               "bidDepth": 1, "askDepth": 1}}
    cycle(["m"], inv, getter=getter, now=0.0)
    rows = cycle(["m"], inv, getter=getter, now=60.0)
    assert rows[0]["flow"]["intervals"] == 1
    assert rows[0]["flow"]["prints_seen"] == 1
    assert rows[0]["path_modelled"] is True


def test_a_market_keeps_being_watched_after_its_book_completes():
    """Measured 2026-09-09: every market emitted exactly 5 rows and went
    silent once the hypothetical surplus was paired, so no row ever reached
    more than 4 flow intervals and two-sidedness could never be classified.
    A 'flow does not separate' result from that window would have been an
    artifact of this loop. Goes red if observation ever stops at completion.
    """
    inv, n = {}, {"i": 0}

    def getter(p):
        n["i"] += 1
        return {"marketData": {"marketSlug": "m", "state": "MARKET_STATE_OPEN",
                               "bestBid": {"value": "0.45"},
                               "bestAsk": {"value": "0.47"},
                               "lastTradePx": {"value": "0.45"},
                               "sharesTraded": str(n["i"] * 10),
                               "bidDepth": 1, "askDepth": 1}}
    rows = []
    for i in range(20):
        rows += cycle(["m"], inv, getter=getter, now=float(i) * 30.0)
    assert len(rows) == 20, f"observation stopped after {len(rows)} rows"
    assert rows[-1]["sessions"] >= 1, "the book never re-seeded"
    assert rows[-1]["flow"]["intervals"] >= 15, rows[-1]["flow"]


def test_flow_keeps_accumulating_across_re_seeds():
    """Flow is a property of the MARKET, not of one hypothetical session, so
    it must survive the re-seed that starts a new one."""
    inv, n = {}, {"i": 0}
    last = {"px": "0.45"}

    def getter(p):
        n["i"] += 1
        last["px"] = "0.47" if n["i"] % 2 else "0.45"   # alternate the side
        return {"marketData": {"marketSlug": "m", "state": "MARKET_STATE_OPEN",
                               "bestBid": {"value": "0.45"},
                               "bestAsk": {"value": "0.47"},
                               "lastTradePx": {"value": last["px"]},
                               "sharesTraded": str(n["i"] * 10),
                               "bidDepth": 1, "askDepth": 1}}
    rows = []
    for i in range(20):
        rows += cycle(["m"], inv, getter=getter, now=float(i) * 30.0)
    f = rows[-1]["flow"]
    assert f["two_sided"] is True, f     # prints on both sides were seen
    assert f["prints_seen"] >= 15


def test_the_shadow_opens_and_closes_a_persistence_window(capsys):
    """OWNER GO: the 30-60s interval must actually be watched end to end, and
    a closed window must be emitted. Goes red if the wiring is dropped."""
    inv, n = {}, {"i": 0}

    def getter(p):
        n["i"] += 1
        return {"marketData": {"marketSlug": "m", "state": "MARKET_STATE_OPEN",
                               "bestBid": {"value": "0.45"},
                               "bestAsk": {"value": "0.47"},
                               "lastTradePx": {"value": "0.45"},
                               "sharesTraded": str(n["i"] * 10),
                               "bidDepth": 1, "askDepth": 1}}
    for i in range(7):
        cycle(["m"], inv, getter=getter, now=float(i) * 25.0)   # 0..150s
    out = capsys.readouterr().out
    assert "PMUS_PERSISTENCE_WINDOW" in out, "no window was ever closed"
    row = json.loads(out[out.index("PMUS_PERSISTENCE_WINDOW") + 24:]
                     .splitlines()[0])
    assert set(row["buckets"]) == {"a_0_30s", "b_30_60s", "c_60_120s"}
    assert row["closed"] is True
    # the interval we never operated in was actually observed
    assert row["buckets"]["b_30_60s"]["observations"] > 0
