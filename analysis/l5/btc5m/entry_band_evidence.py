"""The entry band, on both sources, with the validation each allows.

A. Bonereaper's real BTC5m buys, split by DATE: 2026-03-25 against
   2026-08-30/31 -- five months apart, so the two halves share no regime.
   Return per share at settlement for fills inside and outside the band,
   three ways: share-weighted within a window then averaged over windows;
   every fill counted once; and the dollar total. No fee or rebate.
B. The existing controller on the 187 PMUS windows with the band switched
   on (method and caveats: entry_filter_study.py PART B), first and second
   half. Portfolio settled net per window.

A is real fills on another venue; B is our venue under an assumed fill
rule. Neither is an executable profit on PMUS.
"""
import collections
import json
import sys
from decimal import Decimal as D

sys.path.insert(0, ".")
from analysis.l5.btc5m import entry_filter_study as E    # noqa: E402
from analysis.l5.btc5m import entry_research as R        # noqa: E402
from analysis.l5.btc5m.one_sided_value import load       # noqa: E402

BANDS = [("0.55-0.85", 0.55, 0.85), ("0.65-0.85", 0.65, 0.85),
         ("0.55-0.70", 0.55, 0.70), ("0.15-0.45 (the underdog)", 0.15, 0.45),
         ("every price", 0.0, 1.01)]


def part_a(P):
    wins = collections.defaultdict(list)
    for line in open(R.BR):
        f = json.loads(line)
        if f.get("asset") != "BTC" or "-5m-" not in f.get("market_slug", ""):
            continue
        if f.get("winning_outcome") in (None, ""):
            continue
        wins[f["market_slug"]].append(f)
    groups = {"2026-03-25": [w for w, r in wins.items()
                             if r[0]["datetime_utc"] < "2026-06"],
              "2026-08-30/31": [w for w, r in wins.items()
                                if r[0]["datetime_utc"] >= "2026-06"]}
    P("== A. Bonereaper's real BTC5m buys, by date ==")
    for name, lo, hi in BANDS:
        P("-- bought at %s" % name)
        for g, ws in groups.items():
            bw, flat, usd, cost = {}, [], 0.0, 0.0
            for w in ws:
                rows = [(f["shares"],
                         (1.0 if f["outcome"] == f["winning_outcome"]
                          else 0.0) - f["price"], f["price"])
                        for f in wins[w] if lo <= f["price"] <= hi]
                if rows:
                    bw[w] = [(a, b) for a, b, _ in rows]
                    flat += [b for _, b, _ in rows]
                    usd += sum(a * b for a, b, _ in rows)
                    cost += sum(a * c for a, _, c in rows)
            P("   %-14s %s" % (g, R.cell(bw)))
            if flat:
                P("   %-14s per fill %+.4f (n=%d)   dollars %+.2f on %.0f "
                  "(%+.2f%%)" % ("", sum(flat) / len(flat), len(flat), usd,
                                 cost, 100 * usd / cost if cost else 0))


CONFIGS = [
    ("live configuration (both sides)", {}),
    ("band 0.55-0.85", {"straddle_entry_bid_min": D("0.55"),
                        "straddle_entry_bid_max": D("0.85")}),
    ("band 0.65-0.85", {"straddle_entry_bid_min": D("0.65"),
                        "straddle_entry_bid_max": D("0.85")}),
    ("band 0.55-0.70", {"straddle_entry_bid_min": D("0.55"),
                        "straddle_entry_bid_max": D("0.70")}),
    ("band 0.55-0.85, no new entry after 240 s",
     {"straddle_entry_bid_min": D("0.55"),
      "straddle_entry_bid_max": D("0.85"), "no_new_entry_after_s": 240.0}),
    ("band 0.55-0.85, no new entry after 150 s",
     {"straddle_entry_bid_min": D("0.55"),
      "straddle_entry_bid_max": D("0.85"), "no_new_entry_after_s": 150.0}),
    ("band 0.15-0.45 (the underdog only)",
     {"straddle_entry_bid_min": D("0.15"),
      "straddle_entry_bid_max": D("0.45")}),
]


def main():
    P = print
    part_a(P)
    P("\n== B. the existing controller on 187 PMUS windows ==")
    E.CONFIGS = CONFIGS
    data = load()
    E.part_b(data, list(data), P)
    return 0


if __name__ == "__main__":
    sys.exit(main())
