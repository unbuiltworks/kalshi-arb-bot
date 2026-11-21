"""Canonical L5 behaviour BY ENTRY PRICE BAND.

The question is NOT whether L5 trades outside [0.20, 0.80] -- that is already
established (21.1% of first fills). It is whether its behaviour there is
STRUCTURALLY DIFFERENT, which is what would make a band a behavioural rule
rather than an economic one.
"""
import collections, csv, gzip, os

HERE = os.path.dirname(os.path.abspath(__file__))
SEQ = os.path.join(HERE, "..", "l5_canonical_sequence.csv.gz")
BANDS = [(-1, .05, "<.05"), (.05, .10, ".05-.10"), (.10, .20, ".10-.20"),
         (.20, .35, ".20-.35"), (.35, .65, ".35-.65"), (.65, .80, ".65-.80"),
         (.80, .90, ".80-.90"), (.90, .95, ".90-.95"), (.95, 2, ">.95")]


def _f(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def band_of(p):
    for lo, hi, n in BANDS:
        if lo < p <= hi:
            return n
    return None


def q(v, p):
    v = sorted(x for x in v if x is not None)
    return v[min(len(v) - 1, int(p * (len(v) - 1)))] if v else None


def main():
    win = collections.defaultdict(list)
    with gzip.open(SEQ, "rt") as fh:
        for r in csv.DictReader(fh):
            win[r["window_id"]].append(r)
    agg = collections.defaultdict(lambda: {
        "entries": 0, "cont": 0, "switch": 0, "runlen": [], "gap": [],
        "matched_frac": [], "tte": [], "pair": []})
    for rows in win.values():
        rows.sort(key=lambda r: (_f(r.get("trade_seq")) or 0))
        outs = [r.get("outcome") for r in rows]
        pxs = [_f(r.get("price")) for r in rows]
        ts = [_f(r.get("timestamp")) for r in rows]
        # run boundaries
        starts = [0] + [i for i in range(1, len(rows)) if outs[i] != outs[i - 1]]
        for si, i0 in enumerate(starts):
            i1 = starts[si + 1] if si + 1 < len(starts) else len(rows)
            p0 = pxs[i0]
            if p0 is None:
                continue
            b = band_of(p0)
            if b is None:
                continue
            a = agg[b]
            a["entries"] += 1
            a["runlen"].append(i1 - i0)
            a["cont"] += (i1 - i0) - 1
            if i1 < len(rows):
                a["switch"] += 1
            for i in range(i0 + 1, i1):
                if ts[i] is not None and ts[i - 1] is not None:
                    a["gap"].append(ts[i] - ts[i - 1])
            r_end = rows[i1 - 1]
            u, d = _f(r_end.get("cum_up_qty")), _f(r_end.get("cum_down_qty"))
            if u is not None and d is not None and (u + d) > 0:
                a["matched_frac"].append(2 * min(u, d) / (u + d))
            tte = _f(r_end.get("seconds_to_settlement"))
            if tte is not None:
                a["tte"].append(tte)
            pc = _f(r_end.get("matched_pair_unit_cost"))
            if pc is not None and pc > 0:
                a["pair"].append(pc)
    hdr = (f"{'band':10}{'runs':>7}{'share':>7}{'fills/run':>10}{'cont/run':>9}"
           f"{'P(switch)':>10}{'gap p50':>9}{'matched%':>9}{'pair p50':>9}{'tte p50':>9}")
    print(hdr); print("-" * len(hdr))
    tot = sum(a["entries"] for a in agg.values())
    order = [n for _, _, n in BANDS]
    inband = outband = 0
    for b in order:
        a = agg.get(b)
        if not a or not a["entries"]:
            continue
        n = a["entries"]
        if b in (".20-.35", ".35-.65", ".65-.80"):
            inband += n
        else:
            outband += n
        print(f"{b:10}{n:>7}{n/tot*100:>6.1f}%"
              f"{sum(a['runlen'])/n:>10.2f}{a['cont']/n:>9.2f}"
              f"{a['switch']/n*100:>9.1f}%"
              f"{(q(a['gap'], .5) or 0):>9.1f}"
              f"{(q(a['matched_frac'], .5) or 0)*100:>8.1f}%"
              f"{(q(a['pair'], .5) or 0):>9.4f}{(q(a['tte'], .5) or 0):>9.0f}")
    print(f"\nruns STARTING inside [0.20,0.80]: {inband}/{tot} = {inband/tot*100:.1f}%")
    print(f"runs STARTING outside          : {outband}/{tot} = {outband/tot*100:.1f}%")


if __name__ == "__main__":
    main()
