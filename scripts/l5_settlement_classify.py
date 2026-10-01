"""Classify each audited market from AUTHORITATIVE settlement, then rebuild
realized trading P&L.

CLASSES, decided only by what the venue states:
  RESOLVED_WIN       the outcome L5 held has winner == true
  RESOLVED_LOSS      the outcome L5 held has winner == false
  UNRESOLVED         no token carries a winner flag, or the market is not closed
  INVALID_AMBIGUOUS  both or neither token wins, L5's outcome is not among the
                     market's tokens, or the record contradicts itself

NOTHING IS INFERRED from price, last trade, redemption absence, slug text or
timing. A market with no authoritative statement is UNRESOLVED and is EXCLUDED
from realized P&L -- never counted as a loss.

REALIZED TRADING P&L, per market:
  resolved        redeem_cash - spend          (redeem_cash is the venue's)
  resolved loss   -spend                       (zero redemption, winner false)
  unresolved      EXCLUDED
Incentives are account-level and never enter a market figure.

    python3 scripts/l5_settlement_classify.py <pop.json> <resolutions.jsonl>
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from decimal import Decimal as D

ZERO = D("0")


def load_resolutions(path):
    out = {}
    with open(path) as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            out[r["condition_id"]] = r
    return out


def classify(rec, held_outcomes):
    """(class, winning_outcome, why). `held_outcomes` is what L5 bought."""
    if rec is None:
        return "UNRESOLVED", None, "no settlement record fetched"
    if not rec.get("payload"):
        return "UNRESOLVED", None, f"no payload (status {rec.get('status')})"
    p = rec["payload"]
    toks = p.get("tokens") or []
    if not toks:
        return "INVALID_AMBIGUOUS", None, "market record carries no tokens"
    winners = [t for t in toks if t.get("winner") is True]
    if not p.get("closed"):
        return "UNRESOLVED", None, "market not closed"
    if len(winners) == 0:
        return "UNRESOLVED", None, "closed but no token flagged winner"
    if len(winners) > 1:
        return "INVALID_AMBIGUOUS", None, f"{len(winners)} tokens flagged winner"
    win = str(winners[0].get("outcome"))
    labels = {str(t.get("outcome")) for t in toks}
    unknown = set(held_outcomes) - labels
    if unknown:
        return ("INVALID_AMBIGUOUS", win,
                f"L5 held outcome(s) {sorted(unknown)} not among {sorted(labels)}")
    #: L5 may hold both sides; he WINS if he held the winning outcome at all
    if win in held_outcomes:
        return "RESOLVED_WIN", win, "held the winning outcome"
    return "RESOLVED_LOSS", win, "held only losing outcome(s)"


def main(argv) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    pop = json.load(open(argv[1]))
    res = load_resolutions(argv[2])
    spend = {k: D(v) for k, v in pop["spend"].items()}
    redeem = {k: D(v) for k, v in pop["redeem"].items()}
    held = {k: {o: D(v) for o, v in d.items()}
            for k, d in pop["held_by_outcome"].items()}
    ids = pop["all_ids"]
    zero = set(pop["zero_redemption"])

    cls = {}
    for c in ids:
        k, win, why = classify(res.get(c), set(held.get(c, {})))
        cls[c] = {"class": k, "winner": win, "why": why}

    cov = Counter(v["class"] for v in cls.values())
    covered = sum(1 for c in ids if c in res)
    print("=" * 96)
    print("SETTLEMENT CLASSIFICATION")
    print("=" * 96)
    print(f"  conditionIds in population : {len(ids)}")
    print(f"  settlement records fetched : {covered} ({100.0*covered/len(ids):.1f}%)")
    for k, n in cov.most_common():
        print(f"    {k:<20} {n:6d}")

    zc = Counter(cls[c]["class"] for c in zero)
    zs = defaultdict(lambda: ZERO)
    for c in zero:
        zs[cls[c]["class"]] += spend[c]
    print(f"\n  ZERO-REDEMPTION SUBSET ({len(zero)} markets, "
          f"${sum(spend[c] for c in zero):,.2f} spend)")
    for k in ("RESOLVED_LOSS", "RESOLVED_WIN", "UNRESOLVED",
              "INVALID_AMBIGUOUS"):
        if zc[k]:
            print(f"    {k:<20} {zc[k]:5d} markets   ${zs[k]:>12,.2f}")

    #: realized P&L, resolved only
    realized = ZERO
    excluded = ZERO
    n_incl = n_excl = 0
    anomalies = []
    for c in ids:
        k = cls[c]["class"]
        if k in ("UNRESOLVED", "INVALID_AMBIGUOUS"):
            excluded += spend[c]
            n_excl += 1
            continue
        realized += redeem.get(c, ZERO) - spend[c]
        n_incl += 1
        if k == "RESOLVED_WIN" and redeem.get(c, ZERO) == 0:
            anomalies.append(c)

    print("\n" + "=" * 96)
    print("AUTHORITATIVE REALIZED TRADING P&L (resolved markets only)")
    print("=" * 96)
    print(f"  markets included            {n_incl}")
    print(f"  markets excluded            {n_excl}   (${excluded:,.2f} spend)")
    print(f"  REALIZED TRADING P&L        ${realized:,.2f}")
    print(f"  spend (resolved only)       ${sum(spend[c] for c in ids if cls[c]['class'].startswith('RESOLVED')):,.2f}")
    print(f"  redeem (resolved only)      ${sum(redeem.get(c,ZERO) for c in ids if cls[c]['class'].startswith('RESOLVED')):,.2f}")
    if anomalies:
        print(f"\n  ANOMALY: {len(anomalies)} markets resolved WIN but show no "
              f"redemption cash. Not silently treated as losses; listed.")
        for c in anomalies[:5]:
            print(f"    {c}  spend ${spend[c]}")
    json.dump({"classes": cls,
               "realized_trading_pnl": str(realized),
               "excluded_spend": str(excluded)},
              open("analysis/l5/economics/settlement_classes.json", "w"),
              indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
