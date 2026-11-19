"""SEQUENTIAL completion replay, and the blocker it exposed (#208 PRIORITY 1).

One leg fills, the book moves, the complement is priced against the NEW
touch. That is ROUTE B's situation and no simultaneous-entry result speaks
to it.

TWO FINDINGS ARE PINNED HERE BECAUSE BOTH ARE EASY TO LOSE:

  1. `BC.decide` CANNOT BE REPLAYED ON THESE RECORDINGS. It requires
     `stream_age_s <= TS.MAX_BOOK_AGE_S = 2.0` and every ladder recording in
     the repository samples at a median 6.4-6.9 s. Asked honestly it returns
     STALE_BOOK or PAST_TERMINAL and NOT ONCE an economic refusal, so a
     "controller" run measures the data's sampling rate and nothing about
     pair economics. The test keeps that reproducible instead of leaving it
     as a remark in a document.

  2. EVERY SEQUENTIAL PAIR IS DEARER THAN 0.96. A leg fails to fill
     precisely when the book moved away from it, so the complement is
     expensive by selection. That is adverse selection, measured, and it is
     why the rescue branch is the common case here rather than the exception.

Nothing here is a realized result, and nothing here is evidence about the
historical cancels, which faced their own inventory, prices and runway.
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal as D

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "analysis", "l5", "issue208"))
os.environ.setdefault("L5_BOOK_COMPLETION", "1")

from src import l5_two_sided_probe as TS          # noqa: E402
import queue_scenarios as QS                      # noqa: E402
import sequential_completion_replay as SQ         # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


# =====================================================================
# 1. THE BLOCKER, kept reproducible.
# =====================================================================

def test_the_recordings_are_too_coarse_for_the_freshness_gate():
    """2.0 s allowed; 6.4-6.9 s sampled. Three times the limit."""
    assert float(TS.MAX_BOOK_AGE_S) == 2.0
    gaps = []
    for path, shape in QS.OUT_OF_SAMPLE:
        full = os.path.join(ROOT, path)
        if not os.path.exists(full):
            continue
        for _slug, frames in QS.load(full, shape).items():
            gaps += [float(frames[i]["t"]) - float(frames[i - 1]["t"])
                     for i in range(1, len(frames))]
    assert gaps
    gaps.sort()
    median = gaps[len(gaps) // 2]
    assert median > float(TS.MAX_BOOK_AGE_S) * 3


def test_the_controller_gate_declines_for_data_reasons_not_economic_ones():
    """The whole point of recording this. If the controller arm ever starts
    refusing for an economic reason, the data got better and this test
    should be revisited -- not deleted."""
    rows = SQ.sequential(QS.OUT_OF_SAMPLE, entry_depth=2, complete_depth=0,
                         scenario=QS.SCENARIOS[0], completion_ttl_s=None,
                         gate="controller", root=ROOT)
    reasons = {r.get("decision_reason") for r in rows
               if r["state"] == "DECLINED_BY_CONTROLLER"}
    assert reasons, rows[:2]
    assert reasons <= {"STALE_BOOK", "PAST_TERMINAL", "BOOK_AGE_UNKNOWN"}
    assert not any(r["state"] == "PAIR_SEQUENTIAL" for r in rows)


# =====================================================================
# 2. THE ECONOMIC GATE IS A MODEL, AND ITS RULE IS EXACT.
# =====================================================================

def test_the_modelled_gate_completes_only_above_the_carry_alternative():
    """`pair < 1 - residual` = 1.0236. One tick either side of it."""
    limit = D(1) - QS.RESIDUAL_PER_CONTRACT
    assert limit == D("1.0236")
    frames = [{"t": 0.0, "bids": [["0.6000", "100"]],
               "asks": [["0.6100", "100"]]}] * 3
    # held DOWN at 0.40: complement UP at the touch 0.60 -> pair 1.00 < 1.0236
    k, own, why = SQ._econ_gate(QS.UP, D("0.40"), D(1), frames, 0, 0, None)
    assert own == D("0.6000") and why == "PAIR_BEATS_CARRY_MODELLED"
    # held DOWN at 0.45: pair 1.05, which does NOT beat carrying
    k, own, why = SQ._econ_gate(QS.UP, D("0.45"), D(1), frames, 0, 0, None)
    assert own is None
    assert why == "NO_COMPLEMENT_EVER_BEAT_CARRY"


def test_the_gate_waits_for_the_book_and_reports_how_long():
    """It asks once a frame, as the 5 s cadence does.

    MIND THE CONVENTION, which has now caught three test premises in this
    work. A complement's own price is `bid` for UP and `1 - ask` for DOWN,
    so a HIGH bid makes an UP complement DEAR and a LOW bid makes it cheap.
    To make the gate wait, the FIRST frame must have the high bid.
    """
    frames = [{"t": 0.0, "bids": [["0.9000", "100"]],      # pair 1.35, dear
               "asks": [["0.9100", "100"]]},
              {"t": 7.0, "bids": [["0.5500", "100"]],      # pair 1.00, ok
               "asks": [["0.5600", "100"]]},
              {"t": 14.0, "bids": [["0.5000", "100"]],
               "asks": [["0.5100", "100"]]}]
    k, own, why = SQ._econ_gate(QS.UP, D("0.45"), D(1), frames, 0, 0, None)
    assert k == 1 and own == D("0.5500")        # 0.45 + 0.55 = 1.00
    assert why == "PAIR_BEATS_CARRY_MODELLED"


def test_a_ttl_can_expire_before_a_worthwhile_complement_appears():
    """Dear first, cheap only after the TTL has run out.

    THREE FRAMES, NOT TWO. The gate never quotes into the LAST frame,
    because a quote placed there has no later frame in which it could be
    observed filling. A two-frame fixture therefore only ever sees frame 0
    and exits before the TTL is tested at all.
    """
    frames = [{"t": 0.0, "bids": [["0.9000", "100"]],      # pair 1.35, dear
               "asks": [["0.9100", "100"]]},
              {"t": 90.0, "bids": [["0.2000", "100"]],     # cheap, too late
               "asks": [["0.2100", "100"]]},
              {"t": 97.0, "bids": [["0.2000", "100"]],
               "asks": [["0.2100", "100"]]}]
    k, own, why = SQ._econ_gate(QS.UP, D("0.45"), D(1), frames, 0, 0, 60.0)
    assert own is None
    assert why == "TTL_BEFORE_A_WORTHWHILE_COMPLEMENT"
    # and with no TTL the same book completes, so the TTL is the cause
    k2, own2, _ = SQ._econ_gate(QS.UP, D("0.45"), D(1), frames, 0, 0, None)
    assert own2 == D("0.2000") and k2 == 1


# =====================================================================
# 3. REDUCE is certain, and usually the worst of the three.
# =====================================================================

def test_crossing_out_pays_the_spread_and_the_taker_fee():
    frame = {"t": 0.0, "bids": [["0.4000", "100"]],
             "asks": [["0.4100", "100"]]}
    out = SQ.reduce_outcome(QS.UP, D("0.4500"), D(1), frame)
    assert out["action"] == "REDUCE"
    assert D(out["receive_own"]) == D("0.4000")     # hits the bid
    assert D(out["fee"]) == SQ.taker_fee("0.4000", 1)
    assert D(out["pnl"]) == (D("0.4000") - D("0.4500")) - D(out["fee"])


def test_the_taker_fee_is_the_published_form():
    """theta * C * p * (1 - p), theta = 0.0695."""
    assert SQ.TAKER_THETA == D("0.0695")
    assert SQ.taker_fee("0.50", 1) == (D("0.0695") * D("0.5") * D("0.5")
                                       ).quantize(D("0.000001"))
    assert SQ.taker_fee("0.50", 2) == SQ.taker_fee("0.50", 1) * 2


# =====================================================================
# 4. THE REPLAY'S OWN RESULTS.
# =====================================================================

@pytest.fixture(scope="module")
def oos_econ():
    rows = []
    for cd in SQ.COMPLETE_DEPTHS:
        for ttl in SQ.TTLS:
            for scen in QS.SCENARIOS:
                rows += SQ.sequential(QS.OUT_OF_SAMPLE, entry_depth=2,
                                      complete_depth=cd, scenario=scen,
                                      completion_ttl_s=ttl, gate="econ",
                                      root=ROOT)
    return rows


def test_every_sequential_pair_is_dearer_than_the_opening_ceiling(oos_econ):
    """FINDING 2. Adverse selection, by construction: a leg fails to fill
    when the book moved away, so the complement is dear."""
    pairs = [r for r in oos_econ if r["state"] == "PAIR_SEQUENTIAL"]
    assert pairs
    assert all(D(r["pair_cost"]) > D("0.96") for r in pairs)


def test_a_pair_at_entry_is_never_credited_to_the_completion_logic(oos_econ):
    """Both legs filling at the quoted prices is the simultaneous outcome."""
    entry = [r for r in oos_econ if r["state"] == "PAIR_AT_ENTRY"]
    summed = QS and SQ.summarise(oos_econ)
    assert entry                                   # they do occur
    for s in summed:
        assert s["sequential"] == s["pairs"] + s["residual"]


def test_completing_beats_carrying_and_carrying_beats_crossing_out(oos_econ):
    """The ordering that matters, on the SAME inventory at the SAME frame."""
    by = {(s["complete_depth"], s["ttl"], s["scenario"]): s
          for s in SQ.summarise(oos_econ)}
    for cd in SQ.COMPLETE_DEPTHS:
        s = by[(cd, "hold", "pessimistic")]
        assert D(s["complete_pnl"]) > D(s["hold_pnl"]), s
        assert D(s["hold_pnl"]) > D(s["reduce_pnl"]), s


def test_every_sequential_cell_is_negative_so_this_is_damage_limitation(oos_econ):
    """THE CONCLUSION, and it must not be read as profit. Once one leg has
    filled alone the position is already a loss in expectation; completing
    recovers part of it. The edge lives at entry, not in the rescue."""
    for s in SQ.summarise(oos_econ):
        assert D(s["complete_per_leg"]) < 0, s


def test_resting_longer_never_hurts_and_saturates_by_two_minutes(oos_econ):
    by = {(s["complete_depth"], s["ttl"], s["scenario"]): s
          for s in SQ.summarise(oos_econ)}
    for cd in SQ.COMPLETE_DEPTHS:
        short = D(by[(cd, "60s", "pessimistic")]["complete_pnl"])
        longer = D(by[(cd, "120s", "pessimistic")]["complete_pnl"])
        forever = D(by[(cd, "hold", "pessimistic")]["complete_pnl"])
        assert short <= longer <= forever, cd
        # 120s is already as good as holding to the end, within a cent
        assert abs(forever - longer) < D("0.05"), cd
