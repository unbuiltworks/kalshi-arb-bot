"""S1 vs S2 attribution over the 18 spent 2026-09-30 05Z-09Z windows.

SPENT-WINDOW FORENSIC ANALYSIS. Not OOS. Not validation. Not realized profit.

S1  A-refinement (restated basis, BASIS_DRIFT_TOL=1e-20) + old B + repaired C
S2  S1 + the Case-2 repair, and nothing else
=> S1 -> S2 is the PURE Case-2 execution-semantics effect.

S0 (old subtractive _dispose + exact-equality invariant) does not appear: it
raised AssertionError on window 1 and produced no windows at all. Preserved,
not weakened. delta S0 -> S1 is therefore NOT MEASURABLE on this window set.
"""
import json, math, sys

ARMS = ("CONTROL_ONE_LEG", "L5_REPLICATION", "V1_EXTEND_ONLY",
        "V2_EXTEND_NO_BASIS_GATE", "V3_CONTINUOUS_TWO_SIDED", "V4_CONTINUOUS_PLUS_READJUST")
CASE2 = ("marketable_quote_refused_not_resting",
         "replenish_marketable_refused_not_resting",
         "queue_position_unknowable", "replenish_queue_position_unknowable")
T975_17 = 2.110


def windows(path):
    t = open(path).read()
    i = t.index('"windows"'); i = t.rindex("{", 0, i)
    d, j = 0, i
    while True:
        if t[j] == "{": d += 1
        elif t[j] == "}":
            d -= 1
            if d == 0: break
        j += 1
    return {r["slug"]: r for r in json.loads(t[i:j + 1])["windows"]}


def stats(xs):
    n = len(xs); m = sum(xs) / n
    v = sum((x - m) ** 2 for x in xs) / (n - 1)
    se = math.sqrt(v / n)
    if se == 0:
        return m, None, None
    return m, m / se, (m - T975_17 * se, m + T975_17 * se)


def main(p1, p2):
    A, B = windows(p1), windows(p2)
    slugs = sorted(set(A) & set(B)); n = len(slugs)
    print(__doc__)
    print(f"windows compared: {n}   ({slugs[0][-5:]} .. {slugs[-1][-5:]})\n")

    def g(S, arm, k): return [float(S[s][arm][k] or 0) for s in slugs]

    print("=" * 100)
    print("source_settled_total, $/window   (modelled maker rebates EXCLUDED, "
          "per CLAUDE.md)")
    print("=" * 100)
    print("%-26s%11s%11s%13s%9s%28s" %
          ("arm", "S1", "S2", "d S1->S2", "t", "95% CI on the delta"))
    for arm in ARMS:
        a, b = g(A, arm, "source_settled_total"), g(B, arm, "source_settled_total")
        d = [y - x for x, y in zip(a, b)]
        m, t, ci = stats(d)
        print("%-26s%11.4f%11.4f%+13.4f%9s%28s" % (
            arm, sum(a)/n, sum(b)/n, m,
            "%.2f" % t if t is not None else "exact 0",
            "[%+.4f, %+.4f]" % ci if ci else "identical in every window"))

    print("\n" + "=" * 100)
    print("PAIRED PER-WINDOW DELTAS, S1 -> S2 (pure Case-2)")
    print("=" * 100)
    for arm in ARMS:
        a, b = g(A, arm, "source_settled_total"), g(B, arm, "source_settled_total")
        d = [y - x for x, y in zip(a, b)]
        print("%-26s worse %2d/%d   better %2d/%d   unchanged %2d/%d   "
              "min %+8.4f   max %+8.4f" % (
                  arm, sum(1 for x in d if x < 0), n,
                  sum(1 for x in d if x > 0), n,
                  sum(1 for x in d if x == 0), n, min(d), max(d)))

    print("\n" + "=" * 100)
    print("FILLS AND MATCHED QUANTITY (totals over the 18 windows)")
    print("=" * 100)
    print("%-26s%12s%12s%10s%14s%14s%10s" % (
        "arm", "maker S1", "maker S2", "d", "matched S1", "matched S2", "d"))
    for arm in ARMS:
        mk1, mk2 = sum(g(A, arm, "maker_fills")), sum(g(B, arm, "maker_fills"))
        m1, m2 = sum(g(A, arm, "matched_contracts")), sum(g(B, arm, "matched_contracts"))
        print("%-26s%12.0f%12.0f%+10.0f%14.1f%14.1f%+10.1f" % (
            arm, mk1, mk2, mk2 - mk1, m1, m2, m2 - m1))

    print("\n" + "=" * 100)
    print("CASE-2 REFUSAL COUNTERS (totals; these counters exist only in S2)")
    print("=" * 100)
    for arm in ARMS:
        tot = {}
        for s in slugs:
            for k, v in (B[s][arm].get("blocked_reasons") or {}).items():
                if k in CASE2:
                    tot[k] = tot.get(k, 0) + v
        print("  %-26s %s" % (arm, tot if tot else "ZERO Case-2 refusals"))

    print("\n" + "=" * 100)
    print("EXACT S1 == S2 CHECK over every reported metric, every window")
    print("=" * 100)
    keys = list(A[slugs[0]][ARMS[0]])
    for arm in ARMS:
        bad = [(s[-5:], k) for s in slugs for k in keys
               if str(A[s][arm].get(k)) != str(B[s][arm].get(k))]
        print("  %-26s %s" % (arm, "IDENTICAL on all %d metrics x %d windows"
                              % (len(keys), n) if not bad else
                              "%d metric-window differences, e.g. %s"
                              % (len(bad), bad[:5])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:3]))
