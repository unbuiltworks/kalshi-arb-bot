"""What the decision engine selects across the whole recorded dataset.

Drives the REAL `l5_unmatched_disposition.decide` over all 15 recorded
windows, at every sample where a residual could exist, with the held basis
taken from the venue-recorded cases where known and from a swept range
otherwise. Reports the distribution of selected actions and of the
counterfactual gap.

NOT A P&L AND NOT A FILL. Every figure is a controller verdict or an
arithmetic pair cost; the maker actions' upsides are conditional on fills
this engine does not predict, and the crossing actions are never selected.
"""
import sys
from collections import Counter
from decimal import Decimal as D

sys.path.insert(0, ".")
from src import l5_unmatched_disposition as UD        # noqa: E402
from analysis.l5.issue195.queue_depletion_report import (  # noqa: E402
    IN_SAMPLE, OUT_OF_SAMPLE, _load, _touches)

TICK = D("0.01")
BASES = ("0.30", "0.40", "0.49", "0.60")


def run(group, label, taker):
    sel = Counter()
    reasons = Counter()
    gap_sum, gap_n = D(0), 0
    for path, shape in group:
        for slug, frames in sorted(_load(path, shape).items()):
            if len(frames) < 10:
                continue
            ws = frames[0]["t"] - 30.0
            for i, f in enumerate(frames):
                tt = _touches(f)
                if tt is None:
                    continue
                b, a = tt
                for basis in BASES:
                    # held DOWN at `basis`, our UP quote resting two ticks
                    # behind the UP touch at the moment of entry
                    rest = b - 2 * TICK
                    if rest <= 0:
                        continue
                    r = UD.decide(
                        market=slug, held_side="DOWN", held_qty=D("1"),
                        held_cost_usd=D(basis),
                        resting_own_px=rest, resting_qty=D("1"),
                        bid=b, ask=a, tick=TICK, min_qty=D("1"),
                        now_s=f["t"], window_start_s=ws,
                        window_end_s=ws + 900.0, book_age_s=0.05,
                        residual_budget_usd=D("1.00"),
                        taker_enabled=taker)
                    sel[r["selected"]] += 1
                    reasons[r["selected_reason"]] += 1
                    cf = r.get("counterfactual") or {}
                    g = cf.get("worst_case_improvement_usd")
                    if g is not None:
                        gap_sum += D(g)
                        gap_n += 1
    n = sum(sel.values())
    print("=" * 72)
    print("%s   taker_route=%s   %d decisions" % (label, taker, n))
    for k, v in sel.most_common():
        print("  selected %-16s %6d  (%.1f%%)" % (k, v, 100.0 * v / n))
    print("  reasons: %s" % dict(reasons.most_common(4)))
    if gap_n:
        print("  mean counterfactual worst-case gap: $%s over %d"
              % ((gap_sum / gap_n).quantize(D("0.0001")), gap_n))


if __name__ == "__main__":
    for grp, lab in ((IN_SAMPLE, "IN SAMPLE"),
                     (OUT_OF_SAMPLE, "OUT OF SAMPLE")):
        run(grp, lab, taker=False)
    print()
    print("With the owner's crossing route OFF, no crossing action is ever")
    print("selected. The gap is what declining it would have cost.")
    run(IN_SAMPLE, "IN SAMPLE, route hypothetically ON", taker=True)
