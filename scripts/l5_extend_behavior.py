"""What does L5 actually do when his own inventory is overweight?

INPUT: data/l5/raw/trades.jsonl, L5's public fills. We see FILLS, not
orders, so "next action" is the next fill in the same window. That is a real
limitation and it is stated rather than papered over: a WAIT is inferred
from the absence of a further fill, and a cancelled order is invisible.

SETTLEMENT IS NEVER AN INPUT. No outcome, no winner, no redemption appears
in any feature or label here.
"""
import collections
import json
import statistics
from decimal import Decimal as D

CLIP_EQ = 10.0            # our clip, for expressing L5 sizes in clip units


def load():
    per = collections.defaultdict(list)
    for ln in open("data/l5/raw/trades.jsonl"):
        try:
            d = json.loads(ln)
        except Exception:
            continue
        for it in (d.get("payload") or []):
            slug = str(it.get("eventSlug") or "")
            if not slug.startswith("btc-updown-15m"):
                continue
            per[slug].append(it)
    for k in per:
        # dedupe identical fills, then order in time
        seen, out = set(), []
        for it in per[k]:
            f = (it.get("transactionHash"), it.get("asset"),
                 it.get("size"), it.get("price"), it.get("timestamp"))
            if f in seen:
                continue
            seen.add(f)
            out.append(it)
        per[k] = sorted(out, key=lambda r: r.get("timestamp") or 0)
    return per


def side_of(it):
    """UP/DOWN in own-outcome terms. A SELL of YES is a buy of NO."""
    oc = str(it.get("outcome") or "").strip().lower()
    sd = str(it.get("side") or "").strip().upper()
    if oc in ("yes", "up"):
        return "up" if sd == "BUY" else "down"
    if oc in ("no", "down"):
        return "down" if sd == "BUY" else "up"
    return None


def own_price(it):
    p = float(it.get("price") or 0)
    oc = str(it.get("outcome") or "").strip().lower()
    sd = str(it.get("side") or "").strip().upper()
    # price is the YES price on the wire for a YES row
    if oc in ("yes", "up"):
        return p if sd == "BUY" else 1.0 - p
    return p if sd == "BUY" else 1.0 - p


def episodes():
    """One row per fill, carrying the state BEFORE it."""
    out = []
    for slug, fills in load().items():
        if len(fills) < 2:
            continue
        end = max(f.get("timestamp") or 0 for f in fills)
        q = {"up": 0.0, "down": 0.0}
        prev_px, prev_side_inc = None, None
        for i, f in enumerate(fills):
            s = side_of(f)
            if s is None:
                continue
            sz = float(f.get("size") or 0)
            px = own_price(f)
            ts = f.get("timestamp") or 0
            resid = q["up"] - q["down"]           # UP-positive
            matched = min(q["up"], q["down"])
            held = "up" if resid > 0 else ("down" if resid < 0 else None)
            # the YES probability, for momentum
            yes_px = px if s == "up" else 1.0 - px
            mom = None if prev_px is None else yes_px - prev_px
            if held is not None:
                if s == held:
                    action = "EXTEND_OVERWEIGHT"
                else:
                    # complement. Economic completion iff the all-in pair
                    # cost clears theta, using the frozen fee model.
                    basis = (q[held] and 0.0) or 0.0
                    action = "BUY_UNDERWEIGHT"
            else:
                action = "OPEN_BALANCED"
            out.append({
                "slug": slug, "i": i, "ts": ts,
                "s_to_end": max(0, end - ts),
                "up": q["up"], "down": q["down"],
                "resid": resid, "abs_resid": abs(resid),
                "matched": matched,
                "ratio": (abs(resid) / matched) if matched else None,
                "held": held, "action": action,
                "side": s, "size": sz, "own_px": px, "yes_px": yes_px,
                "mom": mom,
                "prev_increased_imbalance": prev_side_inc,
                "clips": abs(resid) / CLIP_EQ,
            })
            q[s] += sz
            prev_px = yes_px
            prev_side_inc = (held is not None and s == held)
    return out


def rate(rows, label):
    n = len(rows)
    if not n:
        return "%-34s n=0" % label
    c = collections.Counter(r["action"] for r in rows)
    ext = c["EXTEND_OVERWEIGHT"]
    imb = [r for r in rows if r["held"] is not None]
    p = (ext / len(imb) * 100) if imb else float("nan")
    return ("%-34s n=%-5d imbalanced=%-5d EXTEND=%-5d (%5.1f%%)  "
            "UNDER=%-5d" % (label, n, len(imb), ext, p, c["BUY_UNDERWEIGHT"]))


if __name__ == "__main__":
    E = episodes()
    imb = [r for r in E if r["held"] is not None]
    print("BTC-15m L5 fills: %d, windows: %d, with imbalance before: %d"
          % (len(E), len({r["slug"] for r in E}), len(imb)))
    print()
    print(rate(E, "ALL"))
    print()
    print("-- 1. by residual in CLIP units (our clip = 10) --")
    for lo, hi in ((0, 1), (1, 2), (2, 3), (3, 5), (5, 10), (10, 1e9)):
        print(rate([r for r in imb if lo <= r["clips"] < hi],
                   "  residual %g-%g clips" % (lo, hi)))
    print()
    print("-- 2. by residual/matched ratio --")
    for lo, hi in ((0, .25), (.25, .5), (.5, 1), (1, 2), (2, 1e9)):
        print(rate([r for r in imb
                    if r["ratio"] is not None and lo <= r["ratio"] < hi],
                   "  ratio %g-%g" % (lo, hi)))
    print(rate([r for r in imb if r["ratio"] is None], "  matched=0"))
    print()
    print("-- 3. by recent momentum (YES px change since prev fill) --")
    for lo, hi, nm in ((-1, -.02, "down >2c"), (-.02, -.005, "down 0.5-2c"),
                       (-.005, .005, "flat"), (.005, .02, "up 0.5-2c"),
                       (.02, 1, "up >2c")):
        print(rate([r for r in imb
                    if r["mom"] is not None and lo <= r["mom"] < hi],
                   "  %s" % nm))
    print()
    print("-- 3b. momentum ALIGNED with the overweight side? --")
    al = [r for r in imb if r["mom"] is not None]
    for nm, f in (("momentum favours held side",
                   lambda r: (r["mom"] > 0) == (r["held"] == "up")
                   and abs(r["mom"]) >= .005),
                  ("momentum against held side",
                   lambda r: (r["mom"] > 0) != (r["held"] == "up")
                   and abs(r["mom"]) >= .005)):
        print(rate([r for r in al if f(r)], "  %s" % nm))
    print()
    print("-- 4. by own price of the held side --")
    for lo, hi in ((0, .3), (.3, .45), (.45, .55), (.55, .7), (.7, 1)):
        print(rate([r for r in imb if lo <= r["yes_px"] < hi],
                   "  yes px %g-%g" % (lo, hi)))
    print()
    print("-- 5. by seconds to last fill in window --")
    for lo, hi in ((0, 30), (30, 120), (120, 300), (300, 1e9)):
        print(rate([r for r in imb if lo <= r["s_to_end"] < hi],
                   "  %g-%gs remaining" % (lo, hi)))
    print()
    print("-- 7. did the PREVIOUS fill increase imbalance? --")
    for v, nm in ((True, "yes"), (False, "no")):
        print(rate([r for r in imb if r["prev_increased_imbalance"] is v],
                   "  prev increased imbalance=%s" % nm))
    print()
    sizes = [r["size"] for r in imb if r["action"] == "EXTEND_OVERWEIGHT"]
    su = [r["size"] for r in imb if r["action"] == "BUY_UNDERWEIGHT"]
    if sizes and su:
        print("size: EXTEND median %.2f mean %.2f  |  UNDERWEIGHT median %.2f "
              "mean %.2f" % (statistics.median(sizes),
                             statistics.mean(sizes),
                             statistics.median(su), statistics.mean(su)))
    mx = [max(abs(r["resid"]) for r in E if r["slug"] == s)
          for s in {r["slug"] for r in E}]
    mx.sort()
    print("per-window MAX |residual|: median %.1f  p90 %.1f  max %.1f"
          % (statistics.median(mx), mx[int(.9 * len(mx))], mx[-1]))
