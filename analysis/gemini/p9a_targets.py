"""P9-A: how often does a market later offer the other side, and how fast?

    python3 analysis/gemini/p9a_targets.py analysis/gemini/p7_bulk_tape.json

Every print is a candidate first leg. For each one the module labels what the
tape did NEXT: whether the complement ever appeared, whether it appeared cheap
enough to pair at 1.00 / 0.98 / 0.95, how many such chances there were, and how
long the first one took. Settlement is never read.

DEV 09-16..18, VALIDATION 09-19..20. 09-21..22 is previously exposed and is
reported only where the instruction is to report it; 09-23+ is sealed.
"""
import os
import sys
from collections import defaultdict
from fractions import Fraction as F

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
import p8_dataset as DS                                        # noqa: E402
from src.gemini_execution import completion_labels as CL       # noqa: E402

DEV = {"2026-09-16", "2026-09-17", "2026-09-18"}
VAL = {"2026-09-19", "2026-09-20"}


def split_of(day):
    return "dev" if day in DEV else ("val" if day in VAL else "exposed")


def chain(contracts):
    """Attach each contract's PREDECESSOR summary in its own series.

    Causal for these lanes: at the instant market k lists, market k-1 has
    already expired, so its realized two-sidedness is public history.
    """
    by_lane = defaultdict(list)
    for c in contracts:
        by_lane[c["duration"]].append(c)
    out = []
    for lane, cs in by_lane.items():
        cs.sort(key=lambda c: (c["day"], c["hour"], c["symbol"]))
        prev = None
        for c in cs:
            c = dict(c)
            c["_prior"] = prev
            out.append(c)
            prev = CL.contract_summary(c)
    return out


def build(path, lanes=("5m", "15m")):
    rows, rep = DS.load([path])
    rep.pop("quarantined_detail", None)
    print(rep, flush=True)
    rows = [c for c in rows if c["duration"] in lanes and c.get("prints")]
    rows = chain(rows)
    opens = []
    for c in rows:
        for r in CL.label_opens(c, prior=c.get("_prior")):
            r["split"] = split_of(c["day"])
            opens.append(r)
    return opens


def pct(n, d):
    return f"{n / d:.1%}" if d else "-"


def report(opens):
    print(f"\n{'='*118}\nP9-A BASE RATES -- every print treated as a candidate "
          f"first leg\n{'='*118}")
    print(f"{'lane':>5} {'split':>8} {'opens':>7} {'comp ever':>10} "
          f"{'pair<=1.00':>11} {'pair<=0.98':>11} {'pair<=0.95':>11} "
          f"{'>=3 chances@1.00':>17} {'med latency 1.00':>17}")
    for lane in ("5m", "15m"):
        for sp in ("dev", "val", "exposed"):
            rs = [r for r in opens if r["duration"] == lane and r["split"] == sp]
            if not rs:
                continue
            n = len(rs)
            lat = sorted(r["y_latency_1.00_s"] for r in rs
                         if r["y_latency_1.00_s"] is not None)
            print(f"{lane:>5} {sp:>8} {n:>7} "
                  f"{pct(sum(r['y_comp_any'] for r in rs), n):>10} "
                  f"{pct(sum(r['y_pair_le_1.00'] for r in rs), n):>11} "
                  f"{pct(sum(r['y_pair_le_0.98'] for r in rs), n):>11} "
                  f"{pct(sum(r['y_pair_le_0.95'] for r in rs), n):>11} "
                  f"{pct(sum(r['y_n_qualifying_1.00'] >= 3 for r in rs), n):>17} "
                  f"{(f'{lat[len(lat)//2]:.0f}s' if lat else '-'):>17}", flush=True)

    print(f"\nlatency to the first complement that pairs at <= 1.00, DEV only")
    print(f"{'lane':>5} {'n':>7} {'p10':>7} {'p25':>7} {'median':>7} {'p75':>7} "
          f"{'p90':>7} {'>60s':>7} {'never':>7}")
    for lane in ("5m", "15m"):
        rs = [r for r in opens if r["duration"] == lane and r["split"] == "dev"]
        lat = sorted(r["y_latency_1.00_s"] for r in rs
                     if r["y_latency_1.00_s"] is not None)
        if not lat:
            continue
        q = lambda p: lat[int(p * (len(lat) - 1))]             # noqa: E731
        print(f"{lane:>5} {len(rs):>7} {q(.10):>7.0f} {q(.25):>7.0f} "
              f"{q(.50):>7.0f} {q(.75):>7.0f} {q(.90):>7.0f} "
              f"{pct(sum(1 for x in lat if x > 60), len(rs)):>7} "
              f"{pct(len(rs) - len(lat), len(rs)):>7}")

    print(f"\ncompletion availability by the FIRST-LEG PRICE it would be paired "
          f"with, DEV (pair <= 1.00)")
    print(f"{'lane':>5} {'price band':>12} {'opens':>7} {'comp ever':>10} "
          f"{'pair<=1.00':>11} {'pair<=0.95':>11}")
    BANDS = ((F(0), F(20, 100), "<0.20"), (F(20, 100), F(35, 100), "0.20-0.35"),
             (F(35, 100), F(50, 100), "0.35-0.50"),
             (F(50, 100), F(65, 100), "0.50-0.65"),
             (F(65, 100), F(80, 100), "0.65-0.80"), (F(80, 100), F(1), ">0.80"))
    for lane in ("5m", "15m"):
        for lo, hi, nm in BANDS:
            rs = [r for r in opens if r["duration"] == lane
                  and r["split"] == "dev" and lo <= r["price"] < hi]
            if not rs:
                continue
            n = len(rs)
            print(f"{lane:>5} {nm:>12} {n:>7} "
                  f"{pct(sum(r['y_comp_any'] for r in rs), n):>10} "
                  f"{pct(sum(r['y_pair_le_1.00'] for r in rs), n):>11} "
                  f"{pct(sum(r['y_pair_le_0.95'] for r in rs), n):>11}")


if __name__ == "__main__":
    report(build(sys.argv[1]))
