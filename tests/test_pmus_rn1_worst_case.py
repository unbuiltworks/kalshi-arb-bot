"""Completion orders cannot raise the worst case, simultaneous fills
included (owner 2026-11-25: "Verify that completion orders cannot increase
worst-case exposure, including simultaneous fills").

`RN1.committed` is the number every cap, the final gate and the loss stop
use for a market's worst case. These tests fill every combination of the
resting bids -- none, each alone, any subset together, and partial fills --
and check that (1) no outcome exceeds `committed`'s worst unpaired cost,
(2) a completion bid filling never increases unpaired cost, and (3) the
quotes the controller produces for the lighter side never size a
completion above what is unpaired.
"""
import itertools
from decimal import Decimal as D

import pytest

from src import pmus_rn1_replica as R


def _apply(pos, cost, fills):
    p = {k: D(str(v)) for k, v in pos.items()}
    c = {k: D(str(v)) for k, v in cost.items()}
    for side, px, q in fills:
        p[side] += D(str(q))
        c[side] += D(str(px)) * D(str(q))
    return p, c


CASES = [
    # held, resting bids (side, px, qty, is_completion)
    ({"YES": 4, "NO": 0}, {"YES": "2.60", "NO": "0"},
     [("NO", "0.29", 2, True), ("NO", "0.27", 2, False),
      ("NO", "0.25", 2, False), ("YES", "0.60", 2, False)]),
    ({"YES": 2, "NO": 0}, {"YES": "0.94", "NO": "0"},
     [("NO", "0.49", 2, True), ("NO", "0.47", 2, False),
      ("YES", "0.45", 2, False), ("YES", "0.43", 2, False)]),
    ({"YES": 0, "NO": 0}, {"YES": "0", "NO": "0"},
     [("YES", "0.47", 2, False), ("YES", "0.45", 2, False),
      ("NO", "0.49", 2, False), ("NO", "0.47", 2, False)]),
    ({"YES": 3, "NO": 1}, {"YES": "1.50", "NO": "0.45"},
     [("NO", "0.44", 2, True), ("YES", "0.48", 2, False)]),
]


@pytest.mark.parametrize("pos,cost,resting", CASES)
def test_no_combination_of_fills_exceeds_the_committed_worst_case(
        pos, cost, resting):
    bound = R.committed(pos, cost, resting)["unmatched"]
    fracs = (D(0), D("0.5"), D(1))                  # none, partial, full
    for combo in itertools.product(fracs, repeat=len(resting)):
        fills = [(s, px, D(q) * f) for (s, px, q, _c), f
                 in zip(resting, combo) if f > 0]
        p, c = _apply(pos, cost, fills)
        assert R.unmatched_usd(p, c) <= bound + D("0.0000001"), (
            combo, R.unmatched_usd(p, c), bound)


@pytest.mark.parametrize("pos,cost,resting", CASES)
def test_a_completion_fill_never_raises_unpaired_cost(pos, cost, resting):
    """Whatever else has filled, adding the completion's fill on top
    lowers or keeps the unpaired cost."""
    others = [r for r in resting if not r[3]]
    comps = [r for r in resting if r[3]]
    for comp in comps:
        for k in range(len(others) + 1):
            for sub in itertools.combinations(others, k):
                fills = [(s, px, q) for s, px, q, _c in sub
                         if s != comp[0]]     # the heavy side's own adds
                p0, c0 = _apply(pos, cost, fills)
                heavy = "YES" if p0["YES"] > p0["NO"] else "NO"
                if heavy == comp[0] or p0["YES"] == p0["NO"]:
                    continue                  # no longer a completion
                need = abs(p0["YES"] - p0["NO"])
                q = min(D(comp[2]), need)
                p1, c1 = _apply(p0, c0, [(comp[0], comp[1], q)])
                assert R.unmatched_usd(p1, c1) <= R.unmatched_usd(p0, c0)


@pytest.mark.parametrize("held", ["1", "2", "3", "4", "7"])
def test_a_quoted_completion_is_never_larger_than_what_is_unpaired(held):
    q = R.quotes(yes_bid="0.40", yes_ask="0.42", tick="0.01",
                 pos={"YES": held, "NO": "0"},
                 cost={"YES": str(D(held) * D("0.45")), "NO": "0"},
                 now=1000.0, last_fill_t={"YES": 0.0},
                 c=dict(qty="2", residual_cap_usd="20", per_market_usd="40"))
    no = q["NO"]
    assert no is not None and no["role"] == "complete"
    assert D(no["qty"]) <= D(held)
    assert D(no["px"]) + D("0.45") <= R.PAIR_CAP
