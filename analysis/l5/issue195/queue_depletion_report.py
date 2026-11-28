"""Depth comparison on recorded ladders, with bounded fill verdicts.

ENTRY MODEL. At each candidate frame, rest BOTH legs simultaneously, each
`d` ticks behind its own touch -- which is exactly what the deployed
two-sided path does (`depth_ticks`, `L5_DUAL_QUOTE`, `L5_MAX_LIVE_ORDERS=2`).
The pair those two quotes would buy is then 1 - spread - 2*d*tick.

THE OSCILLATION REQUIREMENT, made rigorous by the PROVEN condition. An UP
buy at (bid - d) is proven filled only if the ask later falls to it, i.e.
the market drops by at least (spread + d) ticks. A DOWN buy at wire
(ask + d) is proven filled only if the bid later rises to it: a rise of at
least (spread + d) ticks. So a PROVEN PAIR needs the market to fall AND
rise by that much after entry, within the window.

SETTLEMENT PROXY for the unmatched case: the last frame's mid, which is the
only calibrated probability in this data. Labelled as a proxy everywhere and
never called a realized result.

NOTHING HERE IS A FILL OR A REALIZED P&L.
"""
import json
import sys
from collections import Counter
from decimal import Decimal as D

sys.path.insert(0, ".")
from src import l5_queue_depletion as QD        # noqa: E402

TICK = D("0.01")
DEPTHS = (0, 1, 2, 3)

IN_SAMPLE = [
    ("analysis/l5/completion_requote/tape_20261028.jsonl", "flat"),
    ("analysis/l5/issue195/residual_book_samples_20261106_0300z_0315z.json",
     "wrapped"),
]
OUT_OF_SAMPLE = [
    ("data/l5_strand_replay/residual_books_20261025.jsonl", "flat"),
    ("analysis/l5/issue195/"
     "oos_shadow_residual_samples_20261106_1130z_1145z.json", "wrapped"),
]


def _load(path, shape):
    """-> {slug: [frame]} with frames time-ordered. Reuses the recordings
    already in the repository; no new collection."""
    out = {}
    if shape == "flat":
        rows = []
        for line in open(path):
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    else:
        rows = [s.get("rec") or {} for s in json.load(open(path))
                .get("samples", [])]
    for r in rows:
        slug = r.get("slug")
        bids = r.get("bids")
        asks = r.get("asks")
        if bids is None or asks is None:
            row = ((r.get("book") or {}).get("row") or {})
            bids, asks = row.get("bids"), row.get("asks")
        t = r.get("t")
        if not slug or t is None or not bids or not asks:
            continue
        out.setdefault(slug, []).append(
            {"t": float(t), "bids": bids, "asks": asks})
    for slug in out:
        out[slug].sort(key=lambda f: f["t"])
    return out


def _touches(frame):
    b = [(D(p), D(s)) for p, s in frame["bids"]]
    a = [(D(p), D(s)) for p, s in frame["asks"]]
    if not b or not a:
        return None
    bb, ba = max(p for p, _ in b), min(p for p, _ in a)
    return (bb, ba) if ba > bb else None


def _mid(frame):
    t = _touches(frame)
    return None if t is None else (t[0] + t[1]) / 2


def run(group, label):
    print("#" * 72)
    print("# %s" % label)
    print("#" * 72)
    tally = {d: Counter() for d in DEPTHS}
    econ = {d: {"pair_edge": D(0), "resid_ev": D(0), "pairs": 0,
                "singles": 0} for d in DEPTHS}
    windows = 0
    for path, shape in group:
        for slug, frames in sorted(_load(path, shape).items()):
            if len(frames) < 10:
                continue
            windows += 1
            final_mid = None
            for f in reversed(frames):
                final_mid = _mid(f)
                if final_mid is not None:
                    break
            for i, f in enumerate(frames[:-1]):
                tt = _touches(f)
                if tt is None:
                    continue
                bb, ba = tt
                spread = ba - bb
                for d in DEPTHS:
                    up_own = bb - d * TICK
                    dn_own = (D(1) - ba) - d * TICK
                    if up_own <= 0 or dn_own <= 0:
                        continue
                    vu = QD.fill_verdict("UP", up_own, frames, start=i)
                    vd = QD.fill_verdict("DOWN", dn_own, frames, start=i)
                    ru, rd = vu.get("verdict"), vd.get("verdict")
                    if ru is None or rd is None:
                        tally[d]["UNREADABLE"] += 1
                        continue
                    key = "%s+%s" % (ru[:4], rd[:4])
                    tally[d][key] += 1
                    pair = up_own + dn_own
                    if ru == QD.PROVEN and rd == QD.PROVEN:
                        econ[d]["pairs"] += 1
                        econ[d]["pair_edge"] += (D(1) - pair)
                    elif ru == QD.PROVEN or rd == QD.PROVEN:
                        econ[d]["singles"] += 1
                        if final_mid is not None:
                            if ru == QD.PROVEN:
                                econ[d]["resid_ev"] += (final_mid - up_own)
                            else:
                                econ[d]["resid_ev"] += (
                                    (D(1) - final_mid) - dn_own)
    print("windows: %d" % windows)
    for d in DEPTHS:
        t = tally[d]
        n = sum(t.values())
        if not n:
            continue
        both = t["PROV+PROV"]
        one = t["PROV+POSS"] + t["POSS+PROV"] + t["PROV+IMPO"] \
            + t["IMPO+PROV"]
        imposs = sum(v for k, v in t.items()
                     if k != "UNREADABLE" and "IMPO" in k)
        e = econ[d]
        pair_px = D(1) - D("0.01") - 2 * d * TICK
        print("-" * 72)
        print("depth %d ticks   nominal pair at a 1-tick spread %s "
              "(edge %s/contract)" % (d, pair_px, D(1) - pair_px))
        print("  entries evaluated                  %d" % n)
        print("  BOTH legs PROVEN filled (pair)     %d  (%.1f%%)  "
              "<- lower bound on paired fills" % (both, 100.0 * both / n))
        print("  exactly ONE leg PROVEN (unmatched) %d  (%.1f%%)"
              % (one, 100.0 * one / n))
        print("  at least one leg IMPOSSIBLE        %d  (%.1f%%)"
              % (imposs, 100.0 * imposs / n))
        if e["pairs"]:
            print("  realized-style pair edge, proven pairs only: "
                  "$%s over %d pairs ($%s each)"
                  % (e["pair_edge"].quantize(D("0.0001")), e["pairs"],
                     (e["pair_edge"] / e["pairs"]).quantize(D("0.0001"))))
        if e["singles"]:
            print("  unmatched leg EV at the final mid (PROXY): $%s over "
                  "%d singles ($%s each)"
                  % (e["resid_ev"].quantize(D("0.0001")), e["singles"],
                     (e["resid_ev"] / e["singles"]).quantize(D("0.0001"))))
        net = e["pair_edge"] + e["resid_ev"]
        print("  pair edge + unmatched proxy EV:    $%s"
              % net.quantize(D("0.0001")))


if __name__ == "__main__":
    run(IN_SAMPLE, "IN SAMPLE  (2026-10-28 tape, 2026-11-06 0300z)")
    print()
    run(OUT_OF_SAMPLE,
        "OUT OF SAMPLE  (2026-10-25 strand replay, 2026-11-06 1130z)")
    print()
    print("LOWER BOUNDS ONLY on paired fills: BOTH-PROVEN requires the")
    print("opposite touch to reach each leg, which is sufficient for a fill")
    print("but not necessary -- a fill can also happen inside the queue")
    print("without the touch crossing. The POSSIBLE cases are the upper")
    print("bound and are NOT counted as pairs here.")
