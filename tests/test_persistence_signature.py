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


# ------------------------------------------------- the continuous runner

def test_run_forever_shadows_successive_windows_and_never_reuses_one(tmp_path):
    """One window at a time, each visited once. A collector that re-entered
    the same slug would double-count its cadence."""
    from src import l5_session_journal as JN2

    seen = []

    class Feed:
        ladder = {"bids": [["0.60", "500"]], "offers": [["0.62", "400"]]}

        def __init__(self, slug):
            seen.append(slug)
            self.n = 0

        def latest(self):
            self.n += 1
            if self.n > 3:
                return None
            return {"state": "MARKET_STATE_OPEN", "bid": "0.60", "ask": "0.62",
                    "t": "x%d" % self.n}

    t = {"now": 1790000000.0}

    def clock():
        t["now"] += 120.0
        return t["now"]

    SR.run_forever(clock=clock, sleep=lambda *_a, **_k: None,
                   feed_factory=lambda slug, dl: Feed(slug),
                   journal=JN2.Journal(str(tmp_path / "c.jsonl")),
                   max_windows=3)
    assert len(seen) == 3, seen
    assert len(set(seen)) == 3, "the same window was shadowed twice: %s" % seen


def test_a_failing_window_does_not_stop_the_collector(tmp_path):
    """Read-only collectors must self-recover; a bounded money session must
    not. This is the collector."""
    from src import l5_session_journal as JN2

    calls = {"n": 0}

    def boom(slug, dl):
        calls["n"] += 1
        raise RuntimeError("ws refused")

    t = {"now": 1790000000.0}
    j = tmp_path / "c.jsonl"
    SR.run_forever(clock=lambda: t.__setitem__("now", t["now"] + 120.0)
                   or t["now"], sleep=lambda *_a, **_k: None,
                   feed_factory=boom, journal=JN2.Journal(str(j)),
                   max_windows=3)
    assert calls["n"] == 3, "the collector stopped after a failure"
    kinds = [json.loads(l)["kind"] for l in open(str(j)).read().splitlines()
             if l.strip()]
    assert kinds.count("shadow_window_failed") == 3


# ------------------------------------------------- schema vintages

def test_an_integer_submitted_count_does_not_raise_and_is_not_given_a_side():
    """R5's live journal carries `submitted` as an INT COUNT, not a list.

    An earlier version of from_native_journal iterated it and raised
    TypeError against the real file on the volume. A count says an action
    happened but not which side, so the side metrics must report None
    rather than a fabricated value.
    """
    rows = [{"kind": "native_tick", "t": 1.0, "submitted": 0},
            {"kind": "native_tick", "t": 2.0, "submitted": 2},
            {"kind": "native_tick", "t": 3.0, "submitted": 0}]
    s = SIG.from_native_journal(rows, label="R5")
    assert s["n_actions"] == 2, "the count was dropped"
    assert s["n_ticks"] == 4
    assert s["actions_without_side"] == 2
    for k in ("extend_frac", "rebalance_frac", "max_streak", "transitions",
              "same_price_frac"):
        assert s[k] is None, "%s was invented for sideless actions" % k
    assert "not reported" in s["side_metrics_unavailable"].replace(
        "NOT reported", "not reported")


def test_a_boolean_submitted_is_not_counted_as_one_action():
    """`submitted: True` is a flag, not a quantity."""
    rows = [{"kind": "native_tick", "t": 1.0, "submitted": True}]
    assert SIG.from_native_journal(rows)["n_actions"] == 0


def test_sided_and_sideless_actions_mix_without_corrupting_mode_fractions():
    """The mode denominator must be SIDED actions; including sideless ones
    would understate every fraction."""
    acts = [A(0, "up", 0, 0), A(1, None, 10, 0), A(2, "up", 10, 0)]
    s = SIG.signature(acts)
    assert s["n_actions"] == 3
    assert s["flat_open_frac"] == pytest.approx(0.5)
    assert s["extend_frac"] == pytest.approx(0.5)


def test_a_sideless_action_still_counts_toward_cadence():
    acts = [A(0, None, 0, 0), A(30, None, 0, 0)]
    s = SIG.signature(acts, window_s=900.0)
    assert s["n_actions"] == 2
    assert s["actions_per_min"] == pytest.approx(2 * 60.0 / 900.0)
    assert s["median_dt_prev"] == 30


# ------------------------------------------------- mandate-record reconstruction

def _adm(t, cid, qty="10", cash="4.60"):
    return {"kind": "mandate_admission", "admits": True, "t": t,
            "commit_id": cid, "quantity": qty, "cash_at_risk": cash}


def _cmt(cid, side, px):
    return {"kind": "mandate_commit", "commit_id": cid, "side": side,
            "price_wire": px}


def test_the_reconciliation_invariant_refuses_rather_than_estimating():
    """R5 submitted 13 orders. A cadence built from the wrong count is worse
    than no cadence, so a mismatch must produce a refusal, not statistics."""
    out = SIG.from_mandate_records([_adm(1.0, "c1")], [], expect_actions=13)
    assert out["RECONCILIATION_FAILED"] is True
    assert out["expected_actions"] == 13
    assert out["reconstructed_actions"] == 1
    assert "actions_per_min" not in out, "statistics leaked past the invariant"


def test_a_matching_count_produces_cadence_from_the_mandate():
    j = [_adm(100.0, "c1"), _adm(115.0, "c2")]
    l = [_cmt("c1", "OUTCOME_SIDE_YES", "0.46"),
         _cmt("c2", "OUTCOME_SIDE_NO", "0.50")]
    s = SIG.from_mandate_records(j, l, expect_actions=2, window_s=900.0)
    assert s["reconciled_actions"] == 2 and s["ledger_joined"] == 2
    assert s["actions_per_min"] == pytest.approx(2 * 60.0 / 900.0)
    assert s["median_dt_prev"] == 15.0
    assert s["median_qty"] == 10.0
    assert s["total_cash_at_risk"] == pytest.approx(9.2)


def test_declined_admissions_are_not_actions():
    j = [_adm(1.0, "c1"),
         {"kind": "mandate_admission", "admits": False, "t": 2.0,
          "commit_id": "x"}]
    assert SIG.from_mandate_records(j, [], expect_actions=1
                                    )["reconciled_actions"] == 1


def test_no_intent_fraction_is_not_reported_from_mandate_records():
    """The mandate records only ADMITTED orders. Absence of a declined tick
    is not evidence of idleness, and reporting 0.0 or 1.0 here would invite
    exactly that misreading."""
    s = SIG.from_mandate_records([_adm(1.0, "c1")], [], expect_actions=1)
    assert s["no_intent_tick_frac"] is None
    assert "idleness" in s["no_intent_note"]


def test_an_absent_ledger_leaves_side_metrics_unavailable_not_guessed():
    s = SIG.from_mandate_records([_adm(1.0, "c1"), _adm(2.0, "c2")], [],
                                 expect_actions=2)
    assert s["ledger_joined"] == 0
    for k in ("extend_frac", "rebalance_frac", "max_streak", "transitions"):
        assert s[k] is None
    assert s["actions_per_min"] is not None, "cadence should still be reported"


def test_venue_side_words_map_to_our_canonical_sides():
    j = [_adm(1.0, "c1"), _adm(2.0, "c2")]
    l = [_cmt("c1", "OUTCOME_SIDE_YES", "0.4"),
         _cmt("c2", "OUTCOME_SIDE_NO", "0.6")]
    s = SIG.from_mandate_records(j, l, expect_actions=2)
    assert s["ledger_joined"] == 2
    assert s["flat_open_frac"] == pytest.approx(1.0)
