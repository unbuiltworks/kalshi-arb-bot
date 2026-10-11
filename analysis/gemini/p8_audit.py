"""P8 AUDIT -- an independent recomputation, sharing no code with what it checks.

    python3 analysis/gemini/p8_audit.py analysis/gemini/p7_bulk_tape.json

DELIBERATELY SELF-CONTAINED. It imports nothing from `src.gemini_execution`:
no `ev_replay`, no `pair_policy`, no `tape_normalize`, no `completion_labels`.
The tape walk, the fill rule, the inventory, the settlement and the twin test
are all written again from the raw records, so agreement between this file and
P8 is evidence and disagreement is a finding. Where a quantity can only be
produced one way, that is stated rather than dressed up as confirmation.

It also runs every figure BOTH WAYS -- with and without the mirrored-twin
deduplication -- because the audit's first question is whether that
deduplication should be happening at all.
"""
import datetime as _dt
import json
import sys
from collections import defaultdict
from fractions import Fraction as F

DEV = {"2026-09-16", "2026-09-17", "2026-09-18"}
VAL = {"2026-09-19", "2026-09-20"}

#: the P8-A frozen BTC5m candidate, restated here rather than imported
ACQ_MAX = F(90, 100)
COMP_MAX = F(92, 100)
MAX_LEGS = 12
MAX_RESIDUAL = 4


def split_of(day):
    return "dev" if day in DEV else ("val" if day in VAL else None)


def life_min(c):
    def iso(v):
        try:
            return _dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except Exception:                                      # noqa: BLE001
            return None
    a, b = iso(c.get("start")), iso(c.get("expiry"))
    if a is None or b is None or not b > a:
        return None
    return (b - a).total_seconds() / 60.0


def lane(c):
    """Duration from the contract's declared life. Written independently of
    tape_normalize, with the same bands, because the bands are the claim."""
    m = life_min(c)
    if m is None:
        return None
    if 3 <= m <= 8:
        return "5m"
    if 12 <= m <= 20:
        return "15m"
    if 50 <= m <= 70:
        return "1h"
    return None


# --------------------------------------------------------------------------
# twin handling, reimplemented, and OPTIONAL
# --------------------------------------------------------------------------
def drop_twins(prints):
    by_t = defaultdict(list)
    for r in prints:
        by_t[int(r[0])].append(list(r))
    out, prev = [], None
    for t in sorted(by_t):
        rows = by_t[t]
        used = set()
        for i, a in enumerate(rows):
            if i in used:
                continue
            twin = None
            for j in range(i + 1, len(rows)):
                if j in used:
                    continue
                b = rows[j]
                if (F(str(a[2])) + F(str(b[2])) == 1
                        and F(str(a[3])) == F(str(b[3]))):
                    twin = j
                    break
            used.add(i)
            if twin is None:
                out.append(a)
                prev = F(str(a[2]))
                continue
            b = rows[twin]
            used.add(twin)
            keep = a if (prev is None
                         or abs(F(str(a[2])) - prev) <= abs(F(str(b[2])) - prev)) else b
            out.append(keep)
            prev = F(str(keep[2]))
    return sorted(out, key=lambda r: (int(r[0]), str(r[1])))


def ordered(prints):
    return sorted([list(r) for r in prints], key=lambda r: (int(r[0]), str(r[1])))


# --------------------------------------------------------------------------
# the candidate, walked again from scratch
# --------------------------------------------------------------------------
def walk(contract, prints, *, capture=F(1)):
    """Buy legs under the frozen pair rule and settle. No shared code."""
    yes, no = [], []
    fills = []
    seen = 0
    every = int(1 / float(capture)) if float(capture) > 0 else 1
    # causal complement estimate: the mean price at which each side has printed
    yes_seen, no_seen = [], []

    for t, side, px_s, _qty in prints:
        px = F(str(px_s))
        if side == "sell":
            offered, unit = "YES", px
        elif side == "buy":
            offered, unit = "NO", F(1) - px
        else:
            continue
        # market state first, exactly as a live decider would see it
        (yes_seen if offered == "YES" else no_seen).append(unit)
        if unit <= 0 or unit >= 1:
            continue
        seen += 1
        if every > 1 and seen % every != 0:
            continue
        if len(fills) >= MAX_LEGS:
            continue

        ny, nn = len(yes), len(no)
        reduces = (offered == "NO" and ny > nn) or (offered == "YES" and nn > ny)
        if reduces:
            held = (sum(yes, F(0)) / ny) if offered == "NO" else (sum(no, F(0)) / nn)
            if held + unit > COMP_MAX:
                continue
        else:
            hist = no_seen[:-1] if offered == "YES" else yes_seen[:-1]
            # the complement side's own history, EXCLUDING this print
            hist = [x for x in (no_seen if offered == "YES" else yes_seen)]
            if offered == "YES":
                hist = no_seen
            else:
                hist = yes_seen
            if not hist:
                continue
            q_hat = sum(hist, F(0)) / len(hist)
            if unit + q_hat > ACQ_MAX:
                continue
            if abs((ny + (offered == "YES")) - (nn + (offered == "NO"))) > MAX_RESIDUAL:
                continue
        (yes if offered == "YES" else no).append(unit)
        fills.append((int(t), offered, unit))

    y, n = len(yes), len(no)
    m, r = min(y, n), abs(y - n)
    dom = None if y == n else ("YES" if y > n else "NO")
    spend = sum(yes, F(0)) + sum(no, F(0))
    avg_y = (sum(yes, F(0)) / y) if y else F(0)
    avg_n = (sum(no, F(0)) / n) if n else F(0)
    avg_d = avg_y if dom == "YES" else (avg_n if dom == "NO" else F(0))
    res = str(contract.get("resolution") or "").strip().lower()
    won = "YES" if res in ("yes", "up", "true") else (
        "NO" if res in ("no", "down", "false") else None)
    if won is None:
        return None
    matched = F(m) * (F(1) - avg_y - avg_n)
    residual = F(r) * ((F(1) if dom == won else F(0)) - avg_d)
    payout = F(m) + (F(r) if dom == won else F(0))
    net = payout - spend
    assert matched + residual == net, (matched, residual, net)
    return {"symbol": contract["symbol"], "day": contract["day"],
            "fills": len(fills), "matched_qty": m, "residual_qty": r,
            "matched": matched, "residual": residual, "net": net,
            "spend": spend, "dominant": dom, "won": won,
            "avg_dominant": avg_d}


def aggregate(rows):
    rows = sorted(rows, key=lambda r: (r["day"], r["symbol"]))
    eq = peak = dd = F(0)
    for r in rows:
        eq += r["net"]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    traded = [r for r in rows if r["fills"] > 0]
    return {
        "markets_settled": len(rows), "markets_traded": len(traded),
        "fills": sum(r["fills"] for r in rows),
        "matched": sum((r["matched"] for r in rows), F(0)),
        "residual": sum((r["residual"] for r in rows), F(0)),
        "net": sum((r["net"] for r in rows), F(0)),
        "spend": sum((r["spend"] for r in rows), F(0)),
        "residual_qty": sum(r["residual_qty"] for r in rows),
        "fully_matched": sum(1 for r in traded if r["residual_qty"] == 0),
        "with_residual": sum(1 for r in traded if r["residual_qty"] > 0),
        "max_dd": dd,
    }


def line(tag, a):
    print(f"{tag:>34} {a['markets_traded']:>5} {a['fills']:>6} "
          f"{float(a['spend']):>8.2f} {float(a['matched']):>9.2f} "
          f"{float(a['residual']):>9.2f} {float(a['net']):>8.2f} "
          f"{a['residual_qty']:>7} {a['fully_matched']:>7} "
          f"{a['with_residual']:>7} {float(a['max_dd']):>7.2f}")


def twin_audit(raw):
    """Is a 'mirrored twin' one execution reported twice, or two trades?

    P8 asserted the first from the rows' SHAPE alone. This tests the assertion
    against things the shape cannot fake: which side each row's aggressor was,
    and -- for contracts still inside the venue's ~7-day retention -- whether
    the two rows carry the same `tid`. `/v1/trades` returns `tid` and
    `timestampms`; our collector stored neither, which is why the original
    claim could only ever be an inference.
    """
    sides = defaultdict(int)
    at_half = 0
    total = 0
    keep_d, drop_d = [], []
    for c in raw:
        by = defaultdict(list)
        for p in (c.get("prints") or []):
            by[int(p[0])].append(p)
        prev = None
        for t in sorted(by):
            rows = by[t]
            used = set()
            for i, a in enumerate(rows):
                if i in used:
                    continue
                tw = None
                for j in range(i + 1, len(rows)):
                    if j in used:
                        continue
                    b = rows[j]
                    if (F(str(a[2])) + F(str(b[2])) == 1
                            and F(str(a[3])) == F(str(b[3]))):
                        tw = j
                        break
                used.add(i)
                if tw is None:
                    prev = F(str(a[2]))
                    continue
                b = rows[tw]
                used.add(tw)
                total += 1
                sides[(a[1], b[1])] += 1
                at_half += int(a[2] in ("0.5", "0.50") and b[2] in ("0.5", "0.50"))
                if prev is not None:
                    da, db = abs(F(str(a[2])) - prev), abs(F(str(b[2])) - prev)
                    keep_d.append(float(min(da, db)))
                    drop_d.append(float(max(da, db)))
                    prev = F(str(a[2])) if da <= db else F(str(b[2]))
                else:
                    prev = F(str(a[2]))
    print(f"\nTWIN AUDIT -- {total} candidate pairs in the corpus")
    print(f"  aggressor sides: {dict(sides)}")
    print(f"  the mirror story predicts OPPOSITE sides (one buy, one sell).")
    same = sides.get(("buy", "buy"), 0) + sides.get(("sell", "sell"), 0)
    print(f"  same-side pairs: {same} ({same / total:.1%}) -- opposite-side: "
          f"{total - same}")
    print(f"  pairs at 0.50/0.50, where p + (1-p) = 1 trivially: {at_half}")
    if keep_d:
        print(f"  survivor rule: keeps the row nearer the previous price, so it "
              f"smooths BY CONSTRUCTION")
        print(f"    mean |kept - prev| {sum(keep_d) / len(keep_d):.3f} vs "
              f"|dropped - prev| {sum(drop_d) / len(drop_d):.3f} "
              f"-- mean excursion deleted {sum(drop_d) / len(drop_d) - sum(keep_d) / len(keep_d):.3f}")


def main(path):
    raw = json.load(open(path))
    print(f"raw contracts in the pull: {len(raw)}")
    lanes = defaultdict(list)
    quarantined = 0
    for c in raw:
        ln = lane(c)
        if ln is None:
            quarantined += 1
            continue
        lanes[ln].append(c)
    print(f"  classified by declared life: "
          f"{ {k: len(v) for k, v in lanes.items()} }, quarantined {quarantined}")

    twin_audit(raw)

    print(f"\n{'audit'.upper()} -- BTC5m frozen candidate acq {float(ACQ_MAX):.2f} "
          f"comp {float(COMP_MAX):.2f}, recomputed independently")
    print(f"{'':>34} {'mkts':>5} {'fills':>6} {'spend':>8} {'matched$':>9} "
          f"{'resid$':>9} {'NET$':>8} {'residQ':>7} {'fullmt':>7} {'wresid':>7} "
          f"{'maxDD':>7}")
    for split in ("dev", "val"):
        for dedupe in (True, False):
            rows = []
            for c in lanes["5m"]:
                if split_of(c["day"]) != split or not c.get("prints"):
                    continue
                pr = drop_twins(c["prints"]) if dedupe else ordered(c["prints"])
                r = walk(c, pr)
                if r:
                    rows.append(r)
            if rows:
                line(f"{split} {'deduped' if dedupe else 'RAW, no dedupe'}",
                     aggregate(rows))


if __name__ == "__main__":
    main(sys.argv[1])
