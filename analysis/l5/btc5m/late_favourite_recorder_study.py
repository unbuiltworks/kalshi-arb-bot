"""The late-favourite taker candidate on every recorded PMUS window.

CANDIDATE, fixed before this data was pulled (taker_fee_adjusted.py found
it on 187 windows of 2026-11-11/12): with at most T seconds left, lift the
offer on whichever side is asked at or above X.

INPUT: `BTC5M_QUOTE` lines from the existing public recorder
(l5-btc5m-public-recorder), final 30 s of each window:
slug, t, utc, bid, ask (UP side), to_beat. DOWN's ask is 1 - UP's bid.
THERE IS NO SIZE in this tape and none exists for these markets' final
30 s anywhere in the project's logs, so depth and size slippage CANNOT be
measured here; price slippage from delay can, and is.

WINDOW HYGIENE. A window is used only if it has at least 8 quotes in its
final 30 s, no gap over 8 s between them, and one of them in the last 6 s.
Duplicates are removed on (slug, utc).

OUTCOME. Two independent readings, and a window counts only when it has
one: (a) the final quote: UP's bid at or above 0.90, or UP's ask at or
below 0.10; (b) the next window's price-to-beat against this one's, when
the next window was recorded. Where both exist and DISAGREE the window is
reported and excluded from win/loss, and its entries are shown as unknown.

EXECUTION. The public quote can lag the live book by seconds. So each
entry is priced three ways: at the quote that triggered it, at the next
quote (~2 s later) and at the one after (~4 s). If the side has no ask at
that later quote, or it is 1.00, there is nothing to lift and the entry
is NOT filled. One entry per window.

FEE: 0.095 x p x (1 - p) per share, no rebate. Net per share =
1 if the side won else 0, minus price, minus fee. Worst case per share is
the price plus the fee.

DISCOVERY windows (up to 2026-11-12T13:30Z) are reported apart from the
rest: only the rest is out of sample.
"""
import collections
import json
import math
import sys

K = 0.095
SPLIT = "2026-11-12T13:30"


def fee(p):
    return K * p * (1 - p)


def wilson_upper(k, n, z=1.96):
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c + r) / d


def num(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def load(path):
    by, seen = collections.defaultdict(list), set()
    for line in open(path):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        k = (r.get("slug"), r.get("utc"))
        t = num(r.get("t"))
        if k in seen or t is None or not (270 <= t <= 300) \
                or r.get("closed"):
            continue
        seen.add(k)
        by[r["slug"]].append({"t": t, "utc": r["utc"],
                              "bid": num(r.get("bid")),
                              "ask": num(r.get("ask")),
                              "to_beat": num(r.get("to_beat"))})
    for s in by:
        by[s].sort(key=lambda r: r["t"])
    return by


def next_slug(slug):
    import datetime as dt
    d = dt.datetime.strptime(slug[-16:], "%Y-%m-%d-%H%Mz") \
        + dt.timedelta(minutes=5)
    return slug[:-16] + d.strftime("%Y-%m-%d-%H%Mz")


def outcome(slug, rows, by):
    last = rows[-1]
    a = None
    if last["bid"] is not None and last["bid"] >= 0.90:
        a = "UP"
    elif last["ask"] is not None and last["ask"] <= 0.10:
        a = "DOWN"
    b = None
    nx = by.get(next_slug(slug))
    if nx and rows[-1]["to_beat"] is not None \
            and nx[0]["to_beat"] is not None \
            and nx[0]["to_beat"] != rows[-1]["to_beat"]:
        b = "UP" if nx[0]["to_beat"] > rows[-1]["to_beat"] else "DOWN"
    return a, b


def side_ask(r, side):
    if side == "UP":
        return r["ask"]
    return None if r["bid"] is None else round(1 - r["bid"], 4)


def main():
    by = load(sys.argv[1])
    P = print
    usable, why = {}, collections.Counter()
    for slug, rows in by.items():
        gaps = [b["t"] - a["t"] for a, b in zip(rows, rows[1:])]
        if len(rows) < 8:
            why["fewer than 8 quotes"] += 1
        elif gaps and max(gaps) > 8:
            why["gap over 8 s"] += 1
        elif rows[-1]["t"] < 294:
            why["no quote in the last 6 s"] += 1
        else:
            usable[slug] = rows
    P("windows with late quotes: %d   usable: %d   excluded: %s" % (
        len(by), len(usable), dict(why)))
    res, agree, disagree, none = {}, 0, [], 0
    for slug, rows in usable.items():
        a, b = outcome(slug, rows, by)
        if a and b and a != b:
            disagree.append(slug)
            res[slug] = None
        elif a or b:
            res[slug] = a or b
            agree += bool(a and b)
        else:
            none += 1
            res[slug] = None
    P("outcome known: %d (both readings agree on %d)   readings disagree: "
      "%d   no reading: %d" % (sum(1 for v in res.values() if v), agree,
                               len(disagree), none))
    dates = collections.Counter(s[-16:-6] for s in usable)
    P("usable windows by date: %s" % dict(sorted(dates.items())))

    def entries(X, T, delay, pick):
        out = []
        for slug, rows in usable.items():
            if not pick(slug):
                continue
            for i, r in enumerate(rows):
                if 300 - r["t"] > T:
                    continue
                hit = None
                for side in ("UP", "DOWN"):
                    a = side_ask(r, side)
                    if a is not None and X <= a < 1:
                        hit = side
                        break
                if not hit:
                    continue
                j = i + delay
                px = side_ask(rows[j], hit) if j < len(rows) else None
                if px is None or not (0 < px < 1):
                    out.append((slug, hit, None, None))
                else:
                    w = res[slug]
                    out.append((slug, hit, px, None if w is None
                                else (1.0 if w == hit else 0.0)))
                break
        return out

    def report(label, es):
        sig = len(es)
        fills = [e for e in es if e[2] is not None]
        known = [e for e in fills if e[3] is not None]
        unk = len(fills) - len(known)
        n = len(known)
        if not n:
            return "%-26s signals %4d  fills %4d  (no known outcomes)" % (
                label, sig, len(fills))
        losses = sum(1 for e in known if e[3] == 0.0)
        px = sum(e[2] for e in known) / n
        net = sum(e[3] - e[2] - fee(e[2]) for e in known) / n
        ub = wilson_upper(losses, n)
        at_ub = (1 - ub) * (1 - px) - ub * px - fee(px)
        #: unknown outcomes counted as losses: the pessimistic reading
        ub2 = wilson_upper(losses + unk, n + unk)
        return ("%-26s signals %4d fills %4d known %4d lost %3d (%.2f%%, "
                "<=%.2f%%; unknown-as-loss <=%.2f%%)  avg px %.4f  "
                "net %+.4f  at bound %+.4f  worst %.4f" % (
                    label, sig, len(fills), n, losses, 100 * losses / n,
                    100 * ub, 100 * ub2, px, net, at_ub,
                    max(e[2] + fee(e[2]) for e in known)))

    disc = lambda s: s[-16:].replace("z", "") < "2026-11-12-1330"  # noqa
    oos = lambda s: not disc(s)                                  # noqa: E731
    for X, T in ((0.97, 30), (0.97, 20), (0.97, 10), (0.95, 30), (0.98, 30),
                 (0.99, 30), (0.90, 30)):
        P("\n== ask >= %.2f with <= %d s left ==" % (X, T))
        for d in (0, 1, 2):
            P("  delay %d quote(s):" % d)
            P("    " + report("discovery (to 11-12 13:30Z)",
                              entries(X, T, d, disc)))
            P("    " + report("OUT OF SAMPLE", entries(X, T, d, oos)))
    P("\n== OUT OF SAMPLE by date: ask >= 0.97, <= 30 s, one quote of "
      "delay ==")
    for day in sorted(dates):
        pick = lambda s, day=day: oos(s) and s[-16:-6] == day    # noqa: E731
        P("    " + report(day, entries(0.97, 30, 1, pick)))
    P("\n== OUT OF SAMPLE by UTC session: ask >= 0.97, <= 30 s, one quote "
      "of delay ==")
    for lab, lo, hi in (("00-08 UTC", 0, 8), ("08-16 UTC", 8, 16),
                        ("16-24 UTC", 16, 24)):
        pick = lambda s, lo=lo, hi=hi: oos(s) and lo <= int(     # noqa: E731
            s[-5:-3]) < hi
        P("    " + report(lab, entries(0.97, 30, 1, pick)))
    P("\n== OUT OF SAMPLE by how contested the window was at 30 s left ==")
    def fav30(slug):
        r = usable[slug][0]
        asks = [a for a in (side_ask(r, "UP"), side_ask(r, "DOWN"))
                if a is not None]
        return max(asks) if asks else None
    for lab, lo, hi in (("favourite < 0.90 at 30 s", 0, .90),
                        ("favourite 0.90-0.97", .90, .97),
                        ("favourite >= 0.97", .97, 1.01)):
        pick = lambda s, lo=lo, hi=hi: oos(s) and fav30(s) is not None \
            and lo <= fav30(s) < hi                              # noqa: E731
        P("    " + report(lab, entries(0.97, 30, 1, pick)))
    if disagree:
        P("\nwindows whose two outcome readings disagree (excluded): %s"
          % disagree[:12])
    return 0


if __name__ == "__main__":
    sys.exit(main())
