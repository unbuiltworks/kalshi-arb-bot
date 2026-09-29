"""The signature, and the shadow runner's inability to trade.

The signature exists so that "actions per minute" is the SAME arithmetic for
L5's 501-window history, R5's live journal and the shadow runner. These tests
pin the arithmetic and the no-POST boundary.
"""
from __future__ import annotations

import ast
import json
from decimal import Decimal as D

import pytest

from src import l5_persistence_signature as SIG
from src import l5_session_journal as JN
import scripts.l5_r7_shadow_runner as SR


def A(t, side, up, down, px=None, s=None):
    return {"t": t, "side": side, "up": up, "down": down, "px": px,
            "s_to_expiry": s}


# ------------------------------------------------------- mode classification

def test_extend_adds_to_the_heavy_side():
    assert SIG.classify("up", up=30, down=10) == SIG.EXTEND
    assert SIG.classify("down", up=10, down=30) == SIG.EXTEND


def test_rebalance_adds_to_the_light_side():
    assert SIG.classify("down", up=30, down=10) == SIG.REBALANCE
    assert SIG.classify("up", up=10, down=30) == SIG.REBALANCE


def test_a_balanced_book_is_a_flat_open_not_an_extend():
    assert SIG.classify("up", up=20, down=20) == SIG.FLAT_OPEN
    assert SIG.classify("up", up=0, down=0) == SIG.FLAT_OPEN


# ------------------------------------------------------- the R5 signature

def test_a_no_intent_tick_is_never_counted_as_an_action():
    """R5's defect WAS ticks that looked alive and emitted nothing. If those
    counted as actions the signature would hide exactly what it exists to
    show."""
    s = SIG.signature([A(0, "up", 0, 0)], no_intent_ticks=99)
    assert s["n_actions"] == 1
    assert s["n_ticks"] == 100
    assert s["no_intent_tick_frac"] == pytest.approx(0.99)


def test_r5s_shape_scores_as_near_total_idleness():
    """R5: 416 ticks after the last fill, 271 of them emitting nothing."""
    s = SIG.signature([A(i, "up", 0, 0) for i in range(145)],
                      no_intent_ticks=271, window_s=900.0)
    assert s["no_intent_tick_frac"] == pytest.approx(271 / 416, abs=1e-6)
    assert s["actions_per_min"] == pytest.approx(145 * 60.0 / 900.0)


def test_actions_per_min_uses_the_window_not_the_span():
    """A window with two actions ten seconds apart is not doing 12/min for
    fifteen minutes. window_s is the honest denominator."""
    acts = [A(0, "up", 0, 0), A(10, "up", 10, 0)]
    assert SIG.signature(acts, window_s=900.0)["actions_per_min"] == \
        pytest.approx(2 * 60.0 / 900.0)
    assert SIG.signature(acts)["actions_per_min"] == pytest.approx(12.0)


def test_streaks_transitions_and_inventory_series():
    acts = [A(0, "up", 0, 0), A(1, "up", 10, 0), A(2, "up", 20, 0),
            A(3, "down", 30, 0), A(4, "up", 30, 10)]
    s = SIG.signature(acts)
    assert s["max_streak"] == 3
    assert s["transitions"]["extend->extend"] == 1
    assert s["transitions"]["extend->rebalance"] == 1
    assert s["matched_series"] == [0, 0, 0, 0, 10]
    assert s["residual_series"] == [0, 10, 20, 30, 20]
    assert s["median_dt_prev"] == 1


def test_same_price_replenish_vs_reprice():
    acts = [A(0, "up", 0, 0, px=0.5), A(1, "up", 10, 0, px=0.5),
            A(2, "up", 20, 0, px=0.4)]
    assert SIG.signature(acts)["same_price_frac"] == pytest.approx(0.5)


def test_last_action_seconds_to_expiry_is_the_last_one():
    acts = [A(0, "up", 0, 0, s=800), A(1, "up", 10, 0, s=700)]
    s = SIG.signature(acts)
    assert s["last_action_s_to_expiry"] == 700
    assert s["first_action_s_to_expiry"] == 800


def test_an_empty_window_does_not_raise_and_reports_nothing_fake():
    s = SIG.signature([], no_intent_ticks=50)
    assert s["n_actions"] == 0 and s["no_intent_tick_frac"] == 1.0
    assert s["median_dt_prev"] is None and s["extend_frac"] is None


def test_from_native_journal_reads_r5_shaped_rows():
    rows = [{"kind": "native_tick", "t": 1.0, "intents": []},
            {"kind": "native_tick", "t": 2.0,
             "intents": [{"side": "up", "px": "0.5"}]},
            {"kind": "order_status", "t": 3.0},
            {"kind": "native_tick", "t": 4.0, "intents": []}]
    s = SIG.from_native_journal(rows)
    assert s["n_actions"] == 1 and s["n_ticks"] == 3
    assert s["no_intent_tick_frac"] == pytest.approx(2 / 3)


# ------------------------------------------------------- NO-POST boundary

def test_the_shadow_runner_names_no_order_path_symbol():
    src = open(SR.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for forbidden in ("submit", "submit_body", "cancel", "arm", "_post",
                      "claim", "admit", "PmusTransport", "NativeMoneyPath",
                      "sweep_all", "live_orders"):
        assert forbidden not in names, (
            "the shadow runner references %r" % forbidden)


def test_the_shadow_runner_imports_no_money_module():
    tree = ast.parse(open(SR.__file__, encoding="utf-8").read())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            mods.update("%s.%s" % (n.module, a.name) for a in n.names)
        elif isinstance(n, ast.Import):
            mods.update(a.name for a in n.names)
    for bad in ("pmus_native_money_path", "pmus_live_mandate",
                "l5_live_authorization", "l5_pmus_transport", "pmus_auth"):
        assert not any(bad in m for m in mods), "imports %s" % bad


def test_every_shadow_record_is_labelled_shadow(tmp_path):
    """No number this runner emits may be mistaken for realized economics."""
    class Feed:
        ladder = {"bids": [["0.60", "500"]], "offers": [["0.62", "400"]]}

        def __init__(self):
            self.n = 0

        def latest(self):
            self.n += 1
            return {"state": "MARKET_STATE_OPEN", "bid": "0.60", "ask": "0.62",
                    "t": "2026-09-29T21:00:%02dZ" % min(self.n, 59)}

    j = tmp_path / "s.jsonl"
    journal = JN.Journal(str(j))
    t = {"now": 0.0}
    out = SR.shadow_window(
        "cpc-btc-updown-15m-2026-09-29-2100z", 6.0, feed=Feed(),
        journal=journal, clock=lambda: t.__setitem__("now", t["now"] + 1.0)
        or t["now"], sleep=lambda *_a, **_k: None, clip=D(10))

    assert "SHADOW" in out["labels"]
    recs = [json.loads(l) for l in open(str(j)).read().splitlines() if l.strip()]
    assert recs, "nothing journalled"
    for r in recs:
        if r["kind"] in ("shadow_tick", "shadow_book_depth",
                         "shadow_window_done"):
            assert "SHADOW" in json.dumps(r), (
                "%s carries no SHADOW label" % r["kind"])


def test_the_depth_used_by_decisions_is_persisted(tmp_path):
    class Feed:
        ladder = {"bids": [["0.60", "500"], ["0.59", "250"]],
                  "offers": [["0.62", "400"]]}

        def __init__(self):
            self.n = 0

        def latest(self):
            self.n += 1
            return {"state": "MARKET_STATE_OPEN", "bid": "0.60", "ask": "0.62",
                    "t": "2026-09-29T21:00:%02dZ" % min(self.n, 59)}

    j = tmp_path / "s.jsonl"
    SR.shadow_window("cpc-btc-updown-15m-2026-09-29-2100z", 6.0, feed=Feed(),
                     journal=JN.Journal(str(j)),
                     clock=(lambda c={"n": 0.0}: (c.__setitem__("n", c["n"] + 1)
                                                  or c["n"])),
                     sleep=lambda *_a, **_k: None, clip=D(10))
    depth = [json.loads(l) for l in open(str(j)).read().splitlines()
             if l.strip() and json.loads(l)["kind"] == "shadow_book_depth"]
    assert depth, "no depth persisted"
    assert depth[0]["bids"] == [["0.60", "500"], ["0.59", "250"]]
