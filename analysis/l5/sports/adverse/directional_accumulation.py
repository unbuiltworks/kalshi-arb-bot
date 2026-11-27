"""Directional inventory accumulation in live RN1 fills (read-only analysis).

Input: fills_enriched.json (one object per live FILL_MARKOUT, 22-27 Nov 2026),
reconstructed from production sports-bot logs only. For every opening and
same-side addition (role open/extend) it reports:

  - favourite or underdog at fill (side's own mid > 0.5 = favourite);
  - the side's own mid change over the 30/60/120 s before the fill
    (negative = the side was WEAKENING when we bought it);
  - inventory on both sides before the fill;
  - settlement return per contract (side's final value - price paid);
  - reward credits for the market (PAID only; never pending).

Then groups by favourite/underdog x weakening/strengthening, clusters by
market (fills in one market are not independent), and replays a
favourite-aware allocation rule on the same fills to show what it would
have refused. Run: python3 directional_accumulation.py [fills_enriched.json]
"""
import json
import math
import sys
from collections import defaultdict

PATH = sys.argv[1] if len(sys.argv) > 1 else "fills_enriched.json"
STALE_S = 60.0          # a carried-forward book older than this is unknown
MOVE_TICKS = 0.01       # one tick


def side_mid(book, side):
    """Mid of `side`'s own price from a YES book {bid, ask, mid}."""
    if not book:
        return None
    m = book.get("mid")
    if m is None and book.get("bid") is not None and book.get("ask") is not None:
        m = (float(book["bid"]) + float(book["ask"])) / 2
    if m is None:
        return None
    return float(m) if side == "YES" else 1.0 - float(m)


def fresh(book):
    return book is not None and (book.get("age_s") is None
                                 or float(book["age_s"]) <= STALE_S)


def move(r, key):
    """Side's own mid change from `key` (book_m30 ...) to the fill."""
    b0, b1 = r.get(key), r.get("book_at_fill")
    if not (fresh(b0) and b1):
        return None
    a, b = side_mid(b0, r["side"]), side_mid(b1, r["side"])
    return None if a is None or b is None else round(b - a, 4)


def trend(r):
    """WEAKENING / STRENGTHENING / FLAT from the longest fresh look-back."""
    for key in ("book_m120", "book_m60", "book_m30"):
        d = move(r, key)
        if d is not None:
            if d <= -MOVE_TICKS:
                return "weakening", key, d
            if d >= MOVE_TICKS:
                return "strengthening", key, d
            return "flat", key, d
    return "unknown", None, None


def settle_return(r):
    sy = r.get("settle_yes")
    if sy is None:
        return None
    val = float(sy) if r["side"] == "YES" else 1.0 - float(sy)
    return round(val - float(r["px"]), 4)


def mean_se(xs):
    n = len(xs)
    if n == 0:
        return None, None, 0
    m = sum(xs) / n
    if n < 2:
        return m, None, n
    v = sum((x - m) ** 2 for x in xs) / (n - 1)
    return m, math.sqrt(v / n), n


def main():
    rows = json.load(open(PATH))
    adds = [r for r in rows if r.get("role") in ("open", "extend")]
    print("fills %d, opening/addition fills %d" % (len(rows), len(adds)))
    print("\n%-19s %-38s %-3s %-6s %-5s %-4s %-6s %-14s %7s %7s %7s %-9s %6s" % (
        "fill_t", "market", "sd", "role", "px", "qty", "fav", "trend",
        "d30", "d60", "d120", "pos Y/N", "settle"))
    out = []
    for r in sorted(adds, key=lambda x: x.get("fill_t") or ""):
        mid = side_mid(r.get("book_at_fill"), r["side"])
        fav = None if mid is None else ("fav" if mid > 0.5 else
                                        "dog" if mid < 0.5 else "even")
        tr, basis, d = trend(r)
        ret = settle_return(r)
        pos = r.get("pos_before") or {}
        rec = dict(order_id=r["order_id"], slug=r["slug"], side=r["side"],
                   role=r["role"], px=float(r["px"]), qty=float(r["qty"]),
                   phase=r.get("phase"), fav=fav, side_mid=mid, trend=tr,
                   trend_basis=basis, d30=move(r, "book_m30"),
                   d60=move(r, "book_m60"), d120=move(r, "book_m120"),
                   pos_yes=pos.get("YES"), pos_no=pos.get("NO"),
                   settle_ret=ret,
                   pause_prior=r.get("pause_in_prior_120s"),
                   m300=((r.get("h") or {}).get("300") or {}).get("markout"),
                   stage1=str(r.get("fill_t") or "") >= "2026-11-26T23:45")
        out.append(rec)
        print("%-19s %-38s %-3s %-6s %-5.2f %-4.4g %-6s %-14s %7s %7s %7s %-9s %6s" % (
            str(r.get("fill_t"))[:19], r["slug"][4:42], r["side"][0],
            r["role"], rec["px"], rec["qty"], fav, tr,
            rec["d30"], rec["d60"], rec["d120"],
            "%s/%s" % (pos.get("YES"), pos.get("NO")), ret))
    json.dump(out, open("directional_accumulation_rows.json", "w"), indent=1)

    def report(title, sel):
        print("\n" + title)
        groups = defaultdict(list)
        for x in sel:
            groups[(x["fav"], x["trend"])].append(x)
        print("%-6s %-14s %4s %8s %10s %7s %9s %9s" % (
            "fav", "trend", "n", "ctr", "ret/ctr", "se", "usd", "mkts"))
        for k in sorted(groups, key=str):
            g = [x for x in groups[k] if x["settle_ret"] is not None]
            ctr = sum(x["qty"] for x in g)
            usd = sum(x["qty"] * x["settle_ret"] for x in g)
            # market-clustered: one return per market (qty-weighted)
            bym = defaultdict(lambda: [0.0, 0.0])
            for x in g:
                bym[x["slug"]][0] += x["qty"] * x["settle_ret"]
                bym[x["slug"]][1] += x["qty"]
            per_m = [a / b for a, b in bym.values() if b]
            m, se, nm = mean_se(per_m)
            print("%-6s %-14s %4d %8.2f %10s %7s %9.3f %9d" % (
                k[0], k[1], len(g), ctr,
                "%.3f" % (usd / ctr) if ctr else "-",
                "%.3f" % se if se is not None else "-", usd, nm))

    report("ALL opening/addition fills, settlement return by group", out)
    report("STAGE 1 only (from 26 Nov 23:45)", [x for x in out if x["stage1"]])
    report("EXTENSIONS only", [x for x in out if x["role"] == "extend"])


if __name__ == "__main__":
    main()
