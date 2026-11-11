"""P10-I: G2 and G5 by BTC duration family. **DIAGNOSTIC ONLY.**

    python3 analysis/gemini/p10i_subpop.py

THIS DOES NOT SCORE THE GATE AND MUST NOT BE USED TO. The pre-registered gate
in `P10_DECODER_GATE.md` is an AGGREGATE over BTC-family rows, and it was
written before the data. A per-family split that happened to show one
subpopulation clearing a threshold the aggregate misses is exactly the
subgroup-shopping the pre-registration exists to prevent. `p10h_gate.py` is
deliberately a separate file with no slicing knob, so a diagnostic cannot be
mistaken for a verdict or quietly become one.

The question this DOES answer, and only for after the aggregate decision:
BTC15m's book is about a quarter as wide as BTC5m's (0.060 vs 0.280 median), so
the spec's own structural rule -- a price is decodable when |p - 0.5| > w/2 --
predicts BTC15m should be far more decodable. Is it? If the aggregate gate
fails, that is still worth knowing before any future lane is proposed; it is
not a reason to reopen this one.
"""
import os
import sys
from collections import defaultdict
from fractions import Fraction as F

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import p10b_decode as B                                        # noqa: E402
import p10e_audit_decode as E                                  # noqa: E402
import p10h_gate as G                                          # noqa: E402

#: BTC contracts come in three shapes. The hourly ladder is strike-based, so
#: its prices spread across the [0, 1] range rather than clustering at a coin
#: flip -- a different decodability regime from the two duration contracts, and
#: worth separating for that reason rather than because it is a third bucket.
def family_of(symbol):
    core = symbol.split("-")[1] if "-" in symbol else symbol
    if core.startswith("BTC05M"):
        return "BTC5m"
    if core.startswith("BTC15M"):
        return "BTC15m"
    if core.startswith("BTC"):
        return "BTC hourly"
    return None


def collect(max_stale_s=5.0):
    prim, widths = {}, {}
    for path in G.captures():
        try:
            books, trades = B.load(path)
        except Exception:                                      # noqa: BLE001
            continue
        bk, _tr = E.read(path)
        for tr in trades:
            key = (tr["symbol"], tr.get("tid"))
            if key[1] is None or family_of(tr["symbol"]) is None:
                continue
            r = B.classify_one(tr, books, max_stale_s)
            old = prim.get(key)
            if old is None or G._rank(r["label"]) < G._rank(old["label"]):
                prim[key] = r
            a = E.decode(tr, bk, max_stale_s)
            if a.get("book_width") is not None:
                w = a["book_width"]
                widths[key] = w if key not in widths else min(widths[key], w)
    return prim, widths


def main():
    prim, widths = collect()
    rows = defaultdict(lambda: {"dec": 0, "amb": 0, "stale": 0,
                                "sn": 0, "sh": 0, "w": [], "h2": 0})
    for key, r in prim.items():
        fam = family_of(key[0])
        d = rows[fam]
        lab = r["label"]
        if lab in ("H1", "H2"):
            d["dec"] += 1
            d["h2"] += (lab == "H2")
        elif lab in ("AMBIGUOUS_BOTH", "AMBIGUOUS_NEITHER"):
            d["amb"] += 1
        elif lab == "STALE":
            d["stale"] += 1
        if lab in ("H1", "H2", "AMBIGUOUS_BOTH", "AMBIGUOUS_NEITHER"):
            w = widths.get(key)
            if w is not None:
                d["w"].append(w)
                p = float(F(str(r["price"])))
                d["sn"] += 1
                d["sh"] += ((abs(p - 0.5) > w / 2) == (lab in ("H1", "H2")))

    import statistics as st
    print("DIAGNOSTIC ONLY -- this does not score the pre-registered gate.")
    print(f"{'family':<12}{'obs':>6}{'dec':>6}{'H2':>4}"
          f"{'G2 amb':>9}{'G5 struct':>11}{'med width':>11}")
    for fam in ("BTC5m", "BTC15m", "BTC hourly"):
        d = rows.get(fam)
        if not d:
            continue
        obs = d["dec"] + d["amb"] + d["stale"]
        amb = d["amb"] / obs if obs else 0.0
        stru = d["sh"] / d["sn"] if d["sn"] else 0.0
        med = st.median(d["w"]) if d["w"] else float("nan")
        print(f"{fam:<12}{obs:>6}{d['dec']:>6}{d['h2']:>4}"
              f"{amb:>8.1%}{stru:>11.1%}{med:>11.3f}")
    print("\nThe aggregate gate remains the only verdict. A family clearing a "
          "threshold the\naggregate misses is a hypothesis for a later lane, "
          "not a pass for this one.")


if __name__ == "__main__":
    main()
