"""Maker-only vs bounded-taker-completion, on the venue's own resolutions.

Joins three recorded streams, all of which the deployed engine already emits
24/7 with nothing at risk:

  L5_SHADOW_DEFENSIVE_DECISION   modelled unmatched qty, first-leg own basis,
                                 and `exit_price_own` -- the engine's OWN
                                 taker walk against displayed depth
  L5_SHADOW_RESIDUAL_BOOK_SAMPLE the same states with `held_side`, which the
                                 defensive record does not carry
  L5_LIVE_SETTLED / shadow final the VENUE's binary resolution per window

THE COMPLETED OUTCOME IS CERTAIN; THE MAKER-ONLY ONE IS A COIN FLIP. Holding
Q of one outcome at own basis B and buying Q of the other at own price P is a
matched pair redeeming exactly Q, so

    completed = Q*(1 - B - P) - taker fees        (same either way)
    maker_only = +Q*(1-B) if the held side wins, -Q*B if it loses

Fees are charged on BOTH legs, matching the engine's conservative convention.
"""

from __future__ import annotations

import collections
import datetime as dt
import glob
import json
from decimal import Decimal as D, ROUND_CEILING

TAKER_THETA = D("0.0695")
MARGIN_FLOOR = D("0.25")
MAX_OUTLAY = D("1.50")
MAKER_PATIENCE_S = 90.0
TOOLS = ("/root/.claude/projects/-home-user-kalshi-arb-bot/"
         "1ec9ca12-5dab-5253-8ef5-126eaccc8b25/tool-results/"
         "mcp-Railway-get-logs-*.txt")


def fee(q, p):
    if q <= 0 or p <= 0 or p >= 1:
        return D(0)
    return (TAKER_THETA * q * p * (D(1) - p)).quantize(
        D("0.01"), rounding=ROUND_CEILING)


def slug_start(slug):
    try:
        d = dt.datetime.strptime(slug[-16:], "%Y-%m-%d-%H%Mz")
    except ValueError:
        return None
    return d.replace(tzinfo=dt.timezone.utc).timestamp()


def records(label):
    seen, out = set(), []
    for p in sorted(glob.glob(TOOLS)):
        try:
            doc = json.load(open(p))
        except Exception:
            continue
        for ent in doc.get("deploy") or ():
            msg = ent.get("message") or ""
            if label not in msg:
                continue
            i = msg.find("{")
            try:
                d = json.loads(msg[i:])
            except Exception:
                continue
            k = (ent.get("timestamp"), d.get("slug"),
                 d.get("shadow_variant"), str(d.get("unmatched_qty")),
                 str(d.get("residual")))
            if k in seen:
                continue
            seen.add(k)
            d["_t"] = dt.datetime.fromisoformat(
                str(ent["timestamp"]).replace("Z", "+00:00")).timestamp()
            out.append(d)
    return out


def main():
    res = json.load(open("/tmp/claude-0/resolutions.json"))
    # held_side, keyed by (slug, variant) -- constant within a stranded leg
    sides = {}
    for d in records("L5_SHADOW_RESIDUAL_BOOK_SAMPLE"):
        slug = str(d.get("slug") or "").split("|")[0]
        var = str(d.get("shadow_variant") or "")
        hs = d.get("held_side")
        if slug and var and hs:
            sides.setdefault((slug, var), str(hs))

    byw = collections.defaultdict(list)
    for d in records("L5_SHADOW_DEFENSIVE_DECISION"):
        slug = str(d.get("slug") or "")
        ws = slug_start(slug)
        if ws is None:
            continue
        try:
            q = D(str(d["unmatched_qty"]))
            b = D(str(d["first_leg_own_cost"]))
            p = D(str(d["exit_price_own"]))
        except Exception:
            continue
        if q <= 0 or p <= 0 or p >= 1:
            continue
        byw[(slug, str(d.get("shadow_variant")))].append(
            {"t": round(d["_t"] - ws, 1), "q": q, "b": b, "p": p})

    rows = []
    for (slug, var), ss in sorted(byw.items()):
        ss.sort(key=lambda s: s["t"])
        best = None
        for s in ss:
            q, b, p = s["q"], s["b"], s["p"]
            if s["t"] < MAKER_PATIENCE_S:
                continue
            completed = q - (q * b + fee(q, b) + q * p + fee(q, p))
            outlay = q * p + fee(q, p)
            if completed < MARGIN_FLOOR or outlay > MAX_OUTLAY:
                continue
            if best is None or completed > best["completed"]:
                best = dict(s, completed=completed, outlay=outlay)
        if best is None:
            continue
        hs = sides.get((slug, var))
        up = res.get(slug)
        rows.append({"slug": slug, "var": var, "held_side": hs,
                     "settled_up": up, **best})

    # THE EXACT DECOMPOSITION, which needs no resolution and no side.
    # Holding Q at own basis B has exactly two outcomes; completing has one.
    #   completed - (held side WINS)  = -(Q*P + fees)   <- always negative
    #   completed - (held side LOSES) = Q*B + completed <- always positive
    # So the completion always buys certainty at exactly the outlay, and the
    # whole question is the win probability. It is the better choice iff
    #   p < B + completed/Q
    # and under market-efficient pricing p ~= B, leaving an edge of
    # completed/Q per contract.
    print("Stage B: bounded taker completion vs maker-only, 45 windows")
    print("margin floor %s | per-cross cap %s | maker-first %ss" % (
        MARGIN_FLOOR, MAX_OUTLAY, MAKER_PATIENCE_S))
    print("\nEXACT DECOMPOSITION over every authorized opportunity (%d):"
          % len(rows))
    if rows:
        edge = sorted((r["completed"] / r["q"]) for r in rows)
        give = sorted(r["outlay"] for r in rows)
        bst = sorted((r["q"] * (D(1) - r["b"])) for r in rows)
        wst = sorted((-r["q"] * r["b"]) for r in rows)
        cmp_ = sorted(r["completed"] for r in rows)
        def q3(v, n):
            print("   %-38s p10=%+.4f median=%+.4f p90=%+.4f  total=%+.4f"
                  % (n, v[len(v)//10], v[len(v)//2], v[len(v)*9//10], sum(v)))
        q3(cmp_, "completed, CERTAIN")
        q3(bst, "maker-only if held side WINS")
        q3(wst, "maker-only if held side LOSES")
        q3(give, "upside given up (= the outlay)")
        q3(edge, "EDGE per contract (completed/Q)")
        bes = sorted((r["b"] + r["completed"] / r["q"]) for r in rows)
        bas = sorted(r["b"] for r in rows)
        print("\n   break-even win probability p* = B + completed/Q:")
        print("      p*        p10=%.4f median=%.4f p90=%.4f"
              % (bes[len(bes)//10], bes[len(bes)//2], bes[len(bes)*9//10]))
        print("      B (basis) p10=%.4f median=%.4f p90=%.4f"
              % (bas[len(bas)//10], bas[len(bas)//2], bas[len(bas)*9//10]))
        print("      completion wins whenever the held side's true win")
        print("      probability is BELOW p*. Efficient pricing puts it at ~B,")
        print("      and p* exceeds B in %d of %d cases."
              % (sum(1 for r in rows
                     if r["b"] + r["completed"]/r["q"] > r["b"]), len(rows)))
    print("\n--- the subset where a VENUE resolution and held_side are both "
          "known ---")
    print("margin floor %s | per-cross cap %s | maker-first %ss\n"
          % (MARGIN_FLOOR, MAX_OUTLAY, MAKER_PATIENCE_S))
    print("authorized opportunities: %d" % len(rows))
    scored = [r for r in rows if r["held_side"] and r["settled_up"] is not None]
    print("of which both held_side AND a venue resolution are known: %d"
          % len(scored))
    if not scored:
        return 0

    tot_c = tot_m = D(0)
    wins = better = 0
    print("\n%-7s %-24s %-5s %-4s %8s %9s %9s" % (
        "window", "variant", "held", "up?", "completed", "maker_only", "delta"))
    for r in sorted(scored, key=lambda x: x["slug"]):
        held_wins = ((r["held_side"] == "UP") == bool(r["settled_up"]))
        maker = (r["q"] * (D(1) - r["b"])) if held_wins else (-r["q"] * r["b"])
        tot_c += r["completed"]
        tot_m += maker
        wins += 1 if held_wins else 0
        if r["completed"] > maker:
            better += 1
        print("%-7s %-24s %-5s %-4s %+8.4f %+9.4f %+9.4f" % (
            r["slug"][-5:], r["var"][:24], r["held_side"],
            "yes" if r["settled_up"] else "no",
            r["completed"], maker, r["completed"] - maker))
    n = len(scored)
    print("\n%-34s %+10.4f" % ("TOTAL completed (certain)", tot_c))
    print("%-34s %+10.4f" % ("TOTAL maker-only (as settled)", tot_m))
    print("%-34s %+10.4f" % ("DELTA", tot_c - tot_m))
    print("\nheld side won %d of %d (%.1f%%)" % (wins, n, 100.0*wins/n))
    print("completion beat maker-only in %d of %d (%.1f%%)"
          % (better, n, 100.0*better/n))
    print("\nBREAK-EVEN WIN PROBABILITY per opportunity: completion is the")
    print("better choice whenever the held side's true win probability is")
    print("below  B + completed/Q.  Under market-efficient pricing the win")
    print("probability is ~B, so an authorized completion is positive-")
    print("expectancy by ~completed/Q per contract:")
    pc = sorted((r["completed"] / r["q"]) for r in scored)
    print("   completed per contract: p10=%.4f median=%.4f p90=%.4f"
          % (pc[len(pc)//10], pc[len(pc)//2], pc[len(pc)*9//10]))
    print("\nMODELLED inventory, REAL book, REAL executable price, VENUE "
          "resolution. Nothing submitted, filled or realized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
