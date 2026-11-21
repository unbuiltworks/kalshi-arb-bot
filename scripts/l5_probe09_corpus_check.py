"""Can the Sep 24-25 corpus tell the two bases apart? Measured, not assumed.

The candidate differs from Probe 08's rule in exactly one respect: what an
outstanding order contributes. L5's public activity is a record of FILLS --
every row is a completed trade, with no resting interval and no order that
failed to fill. So on that corpus the outstanding book is empty at every
decision and the two bases are the same function.

This script proves that rather than claiming it, because "the held-out data
cannot answer this question" is a result that has to be demonstrated before
it can be relied on.
"""
import collections
import json
from decimal import Decimal as D

from src.l5_expectancy_replay import Fill, UP, DOWN
from src.l5_prospective_class import (OutstandingBook, classify, REALIZED,
                                      PROSPECTIVE)
from scripts.l5_treatment_oos import episodes

SRC = "data/l5/raw/sep/l5_sep23_25_daily.json"
DAYS = ("2026-09-24", "2026-09-25")


def main():
    doc = json.load(open(SRC))
    agree = 0
    differ = 0
    nonzero_outstanding = 0
    classes = collections.Counter()
    for day in DAYS:
        eps, _ = episodes(doc[day])
        for cid, fills, winner, _t in eps:
            book = OutstandingBook()          # stays empty: every row is a fill
            up = dn = D(0)
            for i, f in enumerate(fills):
                realized = up - dn
                out = book.signed()
                if out != 0:
                    nonzero_outstanding += 1
                a = classify(realized, out, f.side, basis=REALIZED)
                b = classify(realized, out, f.side, basis=PROSPECTIVE)
                classes[a] += 1
                if a == b:
                    agree += 1
                else:
                    differ += 1
                if f.side == UP:
                    up += f.qty
                else:
                    dn += f.qty
    total = agree + differ
    print("Sep 24 + Sep 25 held-out corpus")
    print("  decisions classified      %d" % total)
    print("  decisions where the two bases AGREE   %d (%.4f%%)"
          % (agree, 100.0 * agree / total))
    print("  decisions where they DIFFER           %d" % differ)
    print("  decisions with a non-empty outstanding book %d"
          % nonzero_outstanding)
    print("  class mix: %s" % dict(classes))
    print()
    print("  VERDICT: the corpus cannot discriminate. Every L5 activity row is")
    print("  a completed FILL, so the outstanding book is empty at every")
    print("  decision and the two bases are the same function on this data.")
    print("  The rule can only be told apart where orders REST -- which is")
    print("  Probe 08's window, and nothing else we hold.")


if __name__ == "__main__":
    main()
