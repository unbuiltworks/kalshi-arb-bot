"""P10-E: agreement between the two decoders, plus a sample to check by hand.

    python3 analysis/gemini/p10e_compare.py <capture.jsonl> [...]

The two decoders share no code and decide by different rules -- interval
containment (`p10b`) against nearest-touch distance with a margin (`p10e`).
Agreement on the rows BOTH classify is the number that has to be high before
any historical conclusion becomes canonical; disagreement is a finding about
the rule, not a row to be split.
"""
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import p10b_decode as B                                        # noqa: E402
import p10e_audit_decode as E                                  # noqa: E402


def run(paths, max_stale=5.0):
    rows_b, rows_e = [], []
    for p in paths:
        rows_b += B.main(p, max_stale)
        rows_e += E.main(p, max_stale)
    idx = {(r["symbol"], r["tid"]): r for r in rows_e}
    both = 0
    agree = 0
    pairs = Counter()
    for r in rows_b:
        e = idx.get((r["symbol"], r["tid"]))
        if not e:
            continue
        lb, le = r.get("label"), e.get("label")
        if lb in ("H1", "H2") and le in ("H1", "H2"):
            both += 1
            agree += int(lb == le)
        pairs[(lb, le)] += 1
    print(f"\nAGREEMENT between p10b (interval) and p10e (nearest touch)")
    print(f"  rows both decoders classified as H1/H2: {both}")
    if both:
        print(f"  they agree on {agree}/{both} = {agree / both:.1%}")
    print(f"  label cross-tab (p10b, p10e):")
    for k, v in pairs.most_common():
        print(f"    {str(k):>44}: {v}")
    E.sample(paths[0], rows_b, rows_e, k=16)
    return rows_b, rows_e


if __name__ == "__main__":
    run(sys.argv[1:] or ["analysis/gemini/p10_capture/"])
