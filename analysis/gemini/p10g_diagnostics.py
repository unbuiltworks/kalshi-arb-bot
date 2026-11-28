"""P10-G: what the decoder can and cannot resolve, broken down and stress-tested.

    python3 analysis/gemini/p10g_diagnostics.py <capture.jsonl> [...]

Three questions, none of them "what is the answer":

1. WHERE does classification succeed? Coverage by price band, book width, TTE,
   aggressor type and book age -- so a coverage number is never quoted without
   the conditions that produced it.

2. DOES THE SPEC'S STRUCTURAL PREDICTION HOLD? `P10_DECODER_SPEC.md` claims a
   trade is decodable roughly when |p - 0.5| > w/2, because a book of width w
   centred near the middle contains both p and 1-p. That is falsifiable:
   predicted-decodable and observed-decodable are tabulated against each
   other, and a poor match is evidence the decoder is wrong rather than the
   market being awkward.

3. CAN A DISAGREEING BRACKET BE RESOLVED RATHER THAN DISCARDED? When the book
   before a trade and the book after it give different verdicts, the trade sat
   inside a transition. Discarding those throws away exactly the fast-moving
   moments a maker cares about. Each one is re-examined with the timing we
   actually recorded -- the trade's `timestampms`, and both polls' request and
   response times -- to ask whether one book state PROVABLY existed at trade
   time: a book whose response came back BEFORE the trade's millisecond was
   true at that moment, and one whose request left AFTER it cannot describe it.

Nothing here classifies anything the decoder refused. It reports.
"""
import json
import os
import sys
from collections import defaultdict
from fractions import Fraction as F

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import p10b_decode as B                                        # noqa: E402

BANDS = ((F(0), F(20, 100), "<0.20"), (F(20, 100), F(35, 100), "0.20-0.35"),
         (F(35, 100), F(50, 100), "0.35-0.50"),
         (F(50, 100), F(65, 100), "0.50-0.65"),
         (F(65, 100), F(80, 100), "0.65-0.80"), (F(80, 100), F(1), ">=0.80"))


def band(p):
    for lo, hi, nm in BANDS:
        if lo <= p < hi:
            return nm
    return ">=0.80"


def bucket(v, cuts, names):
    for c, n in zip(cuts, names):
        if v is not None and v <= c:
            return n
    return names[-1]


def load_all(paths):
    books, trades = {}, []
    for p in paths:
        b, t = B.load(p)
        for k, v in b.items():
            books.setdefault(k, []).extend(v)
        trades += t
    for v in books.values():
        v.sort(key=lambda r: r["t_recv"])
    return books, trades


def width_of(row):
    if not row:
        return None
    return float(F(str(row["ask"])) - F(str(row["bid"])))


def enrich(rows, books, max_stale):
    """Attach the conditions a coverage number must be read against."""
    out = []
    for r in rows:
        if r.get("label") in ("PRE_CAPTURE", "STALE"):
            out.append(r)
            continue
        p = F(str(r["price"]))
        w = width_of(r.get("book_prev") and {"bid": r["book_prev"][0],
                                             "ask": r["book_prev"][1]})
        r = dict(r)
        r["band"] = band(p)
        r["dist_50"] = float(abs(p - F(1, 2)))
        r["width"] = w
        #: THE SPEC'S PREDICTION, stated per row so it can be scored
        r["predicted_decodable"] = (None if w is None
                                    else r["dist_50"] > w / 2)
        r["decoded"] = r.get("label") in ("H1", "H2")
        out.append(r)
    return out


def table(rows, key, title, fmt=str):
    agg = defaultdict(lambda: [0, 0])
    for r in rows:
        if r.get("label") in ("PRE_CAPTURE", "STALE"):
            continue
        k = r.get(key)
        if k is None:
            continue
        agg[k][0] += 1
        agg[k][1] += int(r["decoded"])
    if not agg:
        return
    print(f"\n  {title}")
    print(f"    {'bucket':>14} {'n':>5} {'decoded':>8}")
    for k in sorted(agg, key=lambda x: str(x)):
        n, d = agg[k]
        print(f"    {fmt(k):>14} {n:>5} {d / n:>7.1%}")


def main(paths, max_stale=5.0):
    max_stale = float(max_stale)
    books, trades = load_all(paths)
    rows = [B.classify_one(t, books, max_stale) for t in trades]
    rows = enrich(rows, books, max_stale)
    obs = [r for r in rows if r.get("label") not in ("PRE_CAPTURE",)]
    dec = [r for r in obs if r.get("decoded")]
    print(f"captures: {len(paths)} | trades {len(rows)} | observed {len(obs)} "
          f"| decoded {len(dec)} ({(len(dec) / len(obs) if obs else 0):.1%})")

    table(rows, "band", "by price band")
    table(rows, "type", "by aggressor type")
    for r in rows:
        if r.get("width") is not None:
            r["width_b"] = bucket(r["width"], [0.03, 0.08, 0.15, 0.25],
                                  ["<=0.03", "<=0.08", "<=0.15", "<=0.25", ">0.25"])
        if r.get("age_prev_s") is not None:
            r["age_b"] = bucket(r["age_prev_s"], [1, 2, 3, 5],
                                ["<=1s", "<=2s", "<=3s", "<=5s", ">5s"])
        if r.get("prev_next_agree") is not None:
            r["agree_b"] = ("prev/next agree" if r["prev_next_agree"]
                            else "prev/next DISAGREE")
    table(rows, "width_b", "by book width")
    table(rows, "age_b", "by book age before the trade")
    table(rows, "agree_b", "by whether the bracketing books agree")

    # ---- the spec's structural prediction, scored
    pred = [r for r in obs if r.get("predicted_decodable") is not None]
    if pred:
        cells = defaultdict(lambda: [0, 0])
        for r in pred:
            cells[bool(r["predicted_decodable"])][0] += 1
            cells[bool(r["predicted_decodable"])][1] += int(r["decoded"])
        print(f"\n  THE SPEC'S PREDICTION: decodable when |p-0.5| > width/2")
        print(f"    {'predicted':>14} {'n':>5} {'observed decoded':>18}")
        for k in (True, False):
            n, d = cells[k]
            if n:
                print(f"    {str(k):>14} {n:>5} {d / n:>17.1%}")
        tp = cells[True][1]
        fn = cells[False][1]
        tot = cells[True][0] + cells[False][0]
        print(f"    agreement with the prediction: "
              f"{(tp + (cells[False][0] - fn)) / tot:.1%} of {tot}")

    # ---- the disagreeing brackets, examined rather than dropped
    bad = [r for r in obs if r.get("prev_next_agree") is False]
    print(f"\n  BRACKETS THAT DISAGREE: {len(bad)}")
    if bad:
        print(f"    a book RESPONSE that arrived before the trade's own "
              f"millisecond was true at that moment;")
        print(f"    a book REQUEST that left after it cannot describe it.")
        print(f"    {'tid':>20} {'px':>6} {'book prev':>13} {'book next':>13} "
              f"{'age-':>6} {'age+':>6} {'resolvable':>11}")
        n_res = 0
        for r in bad[:25]:
            resolvable = (r.get("age_prev_s") is not None
                          and r["age_prev_s"] >= 0)
            n_res += int(bool(resolvable))
            bp = "-" if not r.get("book_prev") else "/".join(map(str, r["book_prev"]))
            bn = "-" if not r.get("book_next") else "/".join(map(str, r["book_next"]))
            ap = "-" if r.get("age_prev_s") is None else f"{r['age_prev_s']:.2f}"
            an = "-" if r.get("age_next_s") is None else f"{r['age_next_s']:.2f}"
            print(f"    {str(r.get('tid'))[-18:]:>20} {str(r.get('price')):>6} "
                  f"{bp:>13} {bn:>13} {ap:>6} {an:>6} "
                  f"{str(bool(resolvable)):>11}")
        print(f"    a PRIOR book observed strictly before the trade exists for "
              f"{n_res}/{min(len(bad), 25)} of the rows shown")

        #: WHICH WITNESS CONTAINS THE PRINT, and under which reading. If the
        #: disagreement were decode ambiguity we would see the mirror fitting
        #: one of the two books. If it is staleness -- our earlier book
        #: predating a move the trade printed into -- then p fits the LATER
        #: book and the mirror fits neither, and the row is not ambiguous at
        #: all, only late.
        def fits(px, bk):
            if not bk:
                return False
            lo, hi = F(str(bk[0])) - B.TOL, F(str(bk[1])) + B.TOL
            return lo <= px <= hi
        def on_path(px, bk_a, bk_b):
            """Does the price lie BETWEEN the two observed book states?

            THE RULE THAT CONTAINMENT MISSES. When the book is moving, a print
            can sit outside BOTH snapshots and still be perfectly ordinary: it
            happened on the way from one to the other. Asking "which book
            contains it" then picks whichever snapshot is accidentally nearer,
            and on a mirrored ladder that can hand the row to the wrong side.
            Asking "is it on the path" uses the direction of the move, which
            containment throws away.
            """
            if not bk_a or not bk_b:
                return False
            bid_a, ask_a = F(str(bk_a[0])), F(str(bk_a[1]))
            bid_b, ask_b = F(str(bk_b[0])), F(str(bk_b[1]))
            #: THE GAP THE MOVE CROSSED, not the union of the two books. The
            #: union spans both spreads and is wide enough to contain a price
            #: and its mirror, which is exactly the discrimination we need.
            #: The gap is the ground the market covered between the snapshots:
            #: moving up it is [ask_before, bid_after], moving down
            #: [ask_after, bid_before]. A print on the way sits in there.
            if bid_b > ask_a:                       # moved up
                lo, hi = ask_a - B.TOL, bid_b + B.TOL
            elif bid_a > ask_b:                     # moved down
                lo, hi = ask_b - B.TOL, bid_a + B.TOL
            else:
                return False                        # the books overlap: no gap
            return lo <= px <= hi

        cls = defaultdict(int)
        for r in bad:
            p_ = F(str(r["price"]))
            q_ = F(1) - p_
            a = (fits(p_, r.get("book_prev")), fits(p_, r.get("book_next")))
            m = (fits(q_, r.get("book_prev")), fits(q_, r.get("book_next")))
            pa = on_path(p_, r.get("book_prev"), r.get("book_next"))
            pm = on_path(q_, r.get("book_prev"), r.get("book_next"))
            if not any(a) and not any(m):
                cls["neither reading fits either book"] += 1
                continue
            if any(m) and not any(a) and pa and not pm:
                cls["mirror fits a book, but only p is ON THE PATH -- H1"] += 1
                continue
            if a == (False, True) and not any(m):
                cls["p fits the LATER book only -- staleness, reads H1"] += 1
            elif a == (True, False) and not any(m):
                cls["p fits the EARLIER book only -- reads H1"] += 1
            elif any(m) and not any(a):
                cls["only the MIRROR fits, and p is NOT on the path -- H2"] += 1
            elif any(m) and any(a):
                cls["both readings fit one of the two books"] += 1
            else:
                cls["other"] += 1
        print(f"\n    what the disagreement actually is:")
        for k, v in sorted(cls.items(), key=lambda kv: -kv[1]):
            print(f"      {v:>4}  {k}")
    return rows


if __name__ == "__main__":
    main(sys.argv[1:] or ["analysis/gemini/p10_capture/"])
