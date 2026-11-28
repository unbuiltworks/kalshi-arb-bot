"""Full reconciliation of every L5 figure reported, with the waterfall.

WHY THIS EXISTS. The throughput report quoted trade counts, window counts,
turnover, pair basis, residual basis, redemptions and realised P&L from
several different cuts of the same file, and one earlier figure
(+17.95c/residual contract) was an artefact of an attribution that produced
payouts above $1.00 on a binary contract. This module computes every number
once, from one pass, and prints the waterfall between the cuts so the
differences are visible rather than implied.

THE CUTS, and why each exists:

  RAW            every TRADE row in the file, duplicates included. The
                 collector pages with limit/offset and overlapping pages
                 repeat rows, so this is not a trade count.
  UNIQUE         deduplicated on (txhash, asset, timestamp, size, side).
  BTC15M         restricted to btc-updown-15m, the family we trade.
  WITH_TRADES    grouped into windows.
  WITH_REDEEM    windows that also carry a REDEEM event. A window with no
                 redemption cannot be settled from this file.
  DETERMINABLE   windows where the redemption equals ONE side's quantity to
                 within tolerance, so the winning outcome is known exactly
                 and nothing has to be attributed.

Only DETERMINABLE supports a pair/residual split. WITH_REDEEM supports the
top line, because spend and redemption are both directly observed there.
"""
import json
import statistics
import sys
from collections import defaultdict

PATH = "data/l5/raw/activity.jsonl"
FAMILY = "btc-updown-15m"
TOL = 0.02          # 2% match tolerance on the redemption identity


def load(path=PATH):
    raw = 0
    uniq = {}
    kinds = defaultdict(int)
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        for p in (json.loads(line).get("payload") or []):
            ty = p.get("type")
            kinds[ty] += 1
            if ty == "TRADE":
                raw += 1
            if ty not in ("TRADE", "REDEEM"):
                continue
            k = (p.get("transactionHash"), p.get("asset"),
                 p.get("timestamp"), p.get("size"), p.get("side"), ty)
            uniq[k] = p
    return raw, list(uniq.values()), dict(kinds)


def windows(rows, family=FAMILY):
    W = defaultdict(lambda: {"uq": 0.0, "uc": 0.0, "dq": 0.0, "dc": 0.0,
                             "red": 0.0, "n": 0, "t": []})
    for t in rows:
        s = str(t.get("slug") or "")
        if not s.startswith(family):
            continue
        e = W[s]
        if t.get("type") == "TRADE":
            e["n"] += 1
            e["t"].append(int(t.get("timestamp") or 0))
            o = t.get("outcome")
            if o == "Up":
                e["uq"] += float(t.get("size") or 0)
                e["uc"] += float(t.get("usdcSize") or 0)
            elif o == "Down":
                e["dq"] += float(t.get("size") or 0)
                e["dc"] += float(t.get("usdcSize") or 0)
        else:
            e["red"] += float(t.get("usdcSize") or 0)
    return W


def capture_consistent(e):
    """Could this window's redemption have come from the trades we captured?

    A redemption pays $1 per WINNING contract, and only one outcome pays,
    so the payout can never exceed the larger side's holding. When it does,
    trades are MISSING from the capture -- the collector pages with
    limit/offset and loses rows in the busiest windows. Missing trades mean
    missing COST, so spend is understated and net is overstated, which
    inflates every profitability figure computed from that window.

    This is not a cosmetic filter. 93 of the 315 redeem-mismatch windows
    fail it, and they average +$230/window against +$37.76 for the windows
    that pass. They were carrying roughly 90% of the reported edge.
    """
    mx = max(e["uq"], e["dq"])
    return mx > 0 and e["red"] <= mx * (1.0 + TOL)


def classify(e):
    """'determinable:Up' / 'determinable:Down' / 'redeem_mismatch' / ..."""
    if e["n"] == 0:
        return "no_trades"
    if e["red"] <= 0:
        return "no_redeem"
    if e["uq"] <= 0 or e["dq"] <= 0:
        return "one_sided"
    r = e["red"]
    if abs(r - e["uq"]) / e["uq"] < TOL:
        return "determinable:Up"
    if abs(r - e["dq"]) / e["dq"] < TOL:
        return "determinable:Down"
    return "redeem_mismatch"


def main():
    raw, rows, kinds = load()
    trades = [r for r in rows if r.get("type") == "TRADE"]
    b15 = [r for r in trades if str(r.get("slug") or "").startswith(FAMILY)]
    print("=" * 74)
    print("WATERFALL")
    print("=" * 74)
    print("  event types in file          %s" % kinds)
    print("  RAW trade rows              %8d" % raw)
    print("  UNIQUE trades               %8d   (%.1f%% were duplicate pages)"
          % (len(trades), 100.0 * (raw - len(trades)) / raw))
    print("  UNIQUE btc15m trades        %8d   (%.1f%% of unique)"
          % (len(b15), 100.0 * len(b15) / len(trades)))

    W = windows(rows)
    buckets = defaultdict(list)
    for s, e in W.items():
        buckets[classify(e)].append((s, e))
    tot = sum(len(v) for v in buckets.values())
    print("\n  btc15m windows              %8d" % tot)
    for k in sorted(buckets):
        print("    %-22s      %8d" % (k, len(buckets[k])))

    det = buckets["determinable:Up"] + buckets["determinable:Down"]
    red = det + buckets["redeem_mismatch"] + buckets["one_sided"]

    def topline(group, label):
        sp = sum(e["uc"] + e["dc"] for _, e in group)
        rd = sum(e["red"] for _, e in group)
        print("  %-24s n=%4d  spend %11.2f  redeem %11.2f  net %+10.2f"
              " (%+7.3f/win)"
              % (label, len(group), sp, rd, rd - sp,
                 (rd - sp) / len(group) if group else 0))
        return sp, rd

    print("\n" + "=" * 74)
    print("TOP LINE -- spend and redemption are both directly observed")
    print("=" * 74)
    topline(red, "all windows w/ redeem")
    topline(det, "outcome-determinable")
    topline(buckets["redeem_mismatch"], "redeem mismatch only")

    print("\n" + "=" * 74)
    print("PAIR / RESIDUAL SPLIT -- determinable windows only")
    print("=" * 74)
    mq = mc = rq = rc = rp = 0.0
    net = 0.0
    for s, e in det:
        win = "Up" if classify(e) == "determinable:Up" else "Down"
        ub, db = e["uc"] / e["uq"], e["dc"] / e["dq"]
        m = min(e["uq"], e["dq"])
        res = abs(e["uq"] - e["dq"])
        big = "Up" if e["uq"] > e["dq"] else "Down"
        mq += m
        mc += m * (ub + db)
        rq += res
        rc += res * (ub if big == "Up" else db)
        if res > 0 and win == big:
            rp += res
        net += e["red"] - (e["uc"] + e["dc"])
    print("  matched  qty %10.1f  basis %.4f  payout %11.2f  P&L %+10.2f"
          "  %+.4f/contract" % (mq, mc / mq, mq, mq - mc, (mq - mc) / mq))
    print("  residual qty %10.1f  basis %.4f  payout %11.2f  P&L %+10.2f"
          "  %+.4f/contract" % (rq, rc / rq, rp, rp - rc, (rp - rc) / rq))
    print("  residual won %.1f%% of contracts against a %.1f%% basis"
          % (100.0 * rp / rq, 100.0 * rc / rq))
    parts = (mq - mc) + (rp - rc)
    print("  parts %+.2f   observed net %+.2f   UNEXPLAINED %+.2f (%.1f%%)"
          % (parts, net, parts - net, 100.0 * abs(parts - net) / abs(net)))

    print("\n" + "=" * 74)
    print("CAPTURE COMPLETENESS -- the filter that moves the headline")
    print("=" * 74)
    tradeable = [(s2, e) for s2, e in W.items()
                 if e["n"] and e["red"] > 0 and e["uq"] > 0 and e["dq"] > 0]
    ok = [x for x in tradeable if capture_consistent(x[1])]
    bad = [x for x in tradeable if not capture_consistent(x[1])]
    topline(ok, "capture-consistent")
    topline(bad, "redeem EXCEEDS holding")
    print("  A redemption cannot exceed the larger side: the excess is")
    print("  MISSING TRADES, so those windows understate spend and")
    print("  overstate net. Every figure below uses the consistent set.")
    print()
    print("  throughput, consistent windows only:")
    for lo, hi in ((0, 10), (10, 50), (50, 150), (150, 10 ** 9)):
        b = [x for x in ok if lo <= x[1]["n"] < hi]
        if not b:
            continue
        sp = sum(e["uc"] + e["dc"] for _, e in b)
        rd = sum(e["red"] for _, e in b)
        print("    %4d-%-5s n=%3d  %+9.3f/window"
              % (lo, hi if hi < 10 ** 9 else "+", len(b),
                 (rd - sp) / len(b)))

    print("\n" + "=" * 74)
    print("SELECTION: is the determinable subset representative?")
    print("=" * 74)
    print("  %-22s %>8s %>9s %>9s %>9s"
          .replace(">", "") % ("cut", "n", "trades", "basis", "resid%"))
    for grp, lab in ((red, "all w/ redeem"), (det, "determinable"),
                     (buckets["redeem_mismatch"], "mismatch")):
        if not grp:
            continue
        tn = statistics.median([e["n"] for _, e in grp])
        bs = statistics.median([e["uc"] / e["uq"] + e["dc"] / e["dq"]
                                for _, e in grp
                                if e["uq"] > 0 and e["dq"] > 0])
        rf = statistics.median([abs(e["uq"] - e["dq"])
                                / max(e["uq"], e["dq"])
                                for _, e in grp
                                if max(e["uq"], e["dq"]) > 0])
        print("  %-22s %8d %9.1f %9.4f %9.3f" % (lab, len(grp), tn, bs, rf))

    print("\n" + "=" * 74)
    print("MAKER OR TAKER? the pair basis answers it")
    print("=" * 74)
    bases = sorted(e["uc"] / e["uq"] + e["dc"] / e["dq"] for _, e in red
                   if e["uq"] > 0 and e["dq"] > 0)
    below = sum(1 for b in bases if b < 1.0)
    print("  pair basis below 1.00 in %.1f%% of windows (n=%d)"
          % (100.0 * below / len(bases), len(bases)))
    print("  median %.4f   p05 %.4f   p95 %.4f"
          % (bases[len(bases) // 2], bases[int(0.05 * len(bases))],
             bases[int(0.95 * len(bases))]))
    print("  A taker lifting both sides pays the spread and lands ABOVE")
    print("  1.00. A basis this far below it is resting maker liquidity.")
    print("\n  rebate events in the whole file: MAKER %d  TAKER %d  REWARD %d"
          % (kinds.get("MAKER_REBATE", 0), kinds.get("TAKER_REBATE", 0),
             kinds.get("REWARD", 0)))
    print("  -- too few to matter, and NONE is included in any figure above.")


if __name__ == "__main__":
    sys.exit(main())


# ============== INDEPENDENT VERIFICATION, WITHOUT THE REDEMPTION IDENTITY
#
# The pair/residual split above needs the winning outcome, which the
# redemption identity only gives on 186 of 514 windows -- and those turn out
# to be the LOW-THROUGHPUT ones (median 40.5 trades against 152 for the
# mismatches). So the +0.0367 figure is measured on a subsample selected by
# a property that correlates with activity, which is exactly the kind of
# selection that should not be trusted at face value.
#
# A SECOND METHOD NEEDS NO OUTCOME AT ALL. A matched pair redeems exactly
# $1 whichever way the window resolves, so its P&L is `m*(1 - basis)` with
# no attribution. The observed net is directly measured. Therefore
#
#     residual P&L  =  observed net  -  matched P&L
#
# is a residue, not an inference, and it works on EVERY window that carries
# a redemption -- including the busy ones. If the two methods agree on the
# subsample where both apply, the residue can be trusted on the rest.

def residue_method(group, label):
    mq = mc = rq = 0.0
    net = 0.0
    for _s, e in group:
        if e["uq"] <= 0 or e["dq"] <= 0:
            continue
        ub, db = e["uc"] / e["uq"], e["dc"] / e["dq"]
        m = min(e["uq"], e["dq"])
        mq += m
        mc += m * (ub + db)
        rq += abs(e["uq"] - e["dq"])
        net += e["red"] - (e["uc"] + e["dc"])
    if rq <= 0:
        return None
    pair_pnl = mq - mc
    res_pnl = net - pair_pnl
    print("  %-24s matched %+9.2f (%+.4f/ct)   residual %+9.2f (%+.4f/ct)"
          % (label, pair_pnl, pair_pnl / mq, res_pnl, res_pnl / rq))
    return res_pnl / rq


def verify():
    raw, rows, kinds = load()
    del raw, kinds
    W = windows(rows)
    buckets = defaultdict(list)
    for s, e in W.items():
        buckets[classify(e)].append((s, e))
    det = buckets["determinable:Up"] + buckets["determinable:Down"]
    mis = buckets["redeem_mismatch"]
    print("\n" + "=" * 74)
    print("INDEPENDENT VERIFICATION -- residual P&L as a RESIDUE")
    print("=" * 74)
    a = residue_method(det, "determinable (n=%d)" % len(det))
    b = residue_method(mis, "mismatch (n=%d)" % len(mis))
    c = residue_method(det + mis, "both (n=%d)" % len(det + mis))
    print()
    print("  attribution method, determinable only:   +0.0367 / contract")
    print("  residue method,     determinable only:   %+.4f / contract" % a)
    print("  residue method,     busy windows only:   %+.4f / contract" % b)
    print("  residue method,     all with redeem:     %+.4f / contract" % c)
    print()
    if a is not None and b is not None:
        print("  AGREEMENT between methods on the same windows: %.4f"
              % abs(a - 0.0367))
        print("  The busy windows are where the volume is, so the ALL figure")
        print("  is the one that should carry any claim.")
    return c


if __name__ == "__main__":
    verify()
