"""Run the bounded taker policy over the recorded SHADOW tape.

WHY THE TAPE AND NOT THE LIVE PATH. Under the Stage B shadow lock there is
never any REAL inventory, so `maybe_ts_completion` can only ever answer
NO_INVENTORY_TO_COMPLETE -- and with crossing correctly disabled the policy
also short-circuits at OWNER_AUTHORIZATION_ABSENT before reaching any of its
economic gates. Neither is evidence about the policy.

But the engine ALREADY emits, every 5 seconds, on live books, 24/7:
`L5_SHADOW_DEFENSIVE_DECISION` over MODELLED unmatched inventory, carrying
`unmatched_qty`, `first_leg_own_cost`, the executable `exit_price_own` (the
engine's own taker walk against displayed depth) and `pair_if_taker`. That is
exactly what the policy needs, measured by the engine itself rather than
reconstructed here.

THE ARITHMETIC IS SIDE-INDEPENDENT, which is why no held_side is needed:
holding Q of one outcome and buying Q of the other is a matched pair that
redeems exactly Q whichever way the window settles, so both outcomes are
equal and

    worst_after = Q * (1 - pair_if_taker) - fees

with `pair_if_taker = first_leg_own_cost + exit_price_own`. The taker fee is
charged on BOTH legs, matching the engine's own conservative convention.

Usage: python3 -I analysis/l5/stage_b/replay_shadow_completion_tape.py <files...>
"""

from __future__ import annotations

import collections
import datetime as dt
import glob
import json
import sys
from decimal import Decimal as D, ROUND_CEILING

TAKER_THETA = D("0.0695")
MARGIN_FLOOR = D("0.25")
MAX_OUTLAY = D("1.50")
WINDOW_CAP = D("3.00")
MAKER_PATIENCE_S = 90.0


def fee(q: D, p: D) -> D:
    if q <= 0 or p <= 0 or p >= 1:
        return D(0)
    return (TAKER_THETA * q * p * (D(1) - p)).quantize(
        D("0.01"), rounding=ROUND_CEILING)


def slug_start(slug: str):
    try:
        d = dt.datetime.strptime(slug[-16:], "%Y-%m-%d-%H%Mz")
    except ValueError:
        return None
    return d.replace(tzinfo=dt.timezone.utc).timestamp()


def load(paths):
    seen, out = set(), []
    for p in paths:
        try:
            doc = json.load(open(p))
        except Exception:
            continue
        for ent in doc.get("deploy") or ():
            msg = ent.get("message") or ""
            if "L5_SHADOW_DEFENSIVE_DECISION" not in msg:
                continue
            try:
                d = json.loads(msg.split(" ", 1)[1])
            except Exception:
                continue
            ts = ent.get("timestamp")
            k = (ts, d.get("slug"), d.get("shadow_variant"),
                 d.get("unmatched_qty"), d.get("exit_price_own"))
            if k in seen:
                continue
            seen.add(k)
            d["_ts"] = ts
            out.append(d)
    return out


def main(paths):
    rows = load(paths)
    if not rows:
        print("no records found")
        return 1
    # one OPPORTUNITY per (window, shadow variant, stranded-leg state)
    byw = collections.defaultdict(list)
    for d in rows:
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
        t = dt.datetime.fromisoformat(
            str(d["_ts"]).replace("Z", "+00:00")).timestamp()
        byw[(slug, str(d.get("shadow_variant")))].append(
            {"t_in_window_s": round(t - ws, 1), "q": q, "b": b, "p": p,
             "pair": b + p})

    windows = sorted({k[0] for k in byw})
    print("Bounded taker policy over the recorded shadow tape")
    print("margin floor %s | per-cross cap %s | window cap %s | "
          "maker-first %ss | taker fee on BOTH legs\n"
          % (MARGIN_FLOOR, MAX_OUTLAY, WINDOW_CAP, MAKER_PATIENCE_S))
    print("distinct BTC15m windows: %d | variants: %d | samples: %d"
          % (len(windows), len({k[1] for k in byw}),
             sum(len(v) for v in byw.values())))

    auth_rows, stats = [], collections.Counter()
    for (slug, var), samples in sorted(byw.items()):
        samples.sort(key=lambda s: s["t_in_window_s"])
        best = None
        for s in samples:
            q, b, p = s["q"], s["b"], s["p"]
            cost = q * b + fee(q, b) + q * p + fee(q, p)
            wa = q - cost
            outlay = q * p + fee(q, p)
            if s["t_in_window_s"] < MAKER_PATIENCE_S:
                stats["MAKER_PATIENCE_NOT_SPENT"] += 1
                continue
            if wa <= 0:
                stats["WORST_OUTCOME_NOT_POSITIVE"] += 1
                continue
            if wa < MARGIN_FLOOR:
                stats["MARGIN_BELOW_FLOOR"] += 1
                continue
            if outlay > MAX_OUTLAY:
                stats["OUTLAY_ABOVE_CAP"] += 1
                continue
            stats["AUTHORIZED"] += 1
            cand = dict(s, worst_after=wa, outlay=outlay)
            if best is None or wa > best["worst_after"]:
                best = cand
        if best is not None:
            auth_rows.append((slug, var, best))

    print("\nper-sample verdicts (the policy's own gates):")
    for k, v in stats.most_common():
        print("   %-32s %6d" % (k, v))

    nw = len({r[0] for r in auth_rows})
    print("\nINDEPENDENT OPPORTUNITIES (best per window+variant): %d"
          % len(auth_rows))
    print("windows with at least one authorized cross: %d of %d (%.1f%%)"
          % (nw, len(windows), 100.0 * nw / max(1, len(windows))))
    if auth_rows:
        wa = sorted(r[2]["worst_after"] for r in auth_rows)
        ol = sorted(r[2]["outlay"] for r in auth_rows)
        tw = sorted(r[2]["t_in_window_s"] for r in auth_rows)
        def q3(v, n, f="%.4f"):
            print("   %-26s p10=%s median=%s p90=%s  total=%s"
                  % (n, f % v[len(v)//10], f % v[len(v)//2],
                     f % v[len(v)*9//10], f % sum(v)))
        print("\nAUTHORIZED CROSS ECONOMICS (modelled, per opportunity):")
        q3(wa, "worst_after_usd"); q3(ol, "additional_outlay_usd")
        q3(tw, "t_in_window_s", "%.1f")
        print("\n   sample of authorized opportunities:")
        for slug, var, b in auth_rows[:12]:
            print("     %s %-22s t=%6.1fs q=%-6s pair=%.4f "
                  "worst_after=%+.4f outlay=%.4f"
                  % (slug[-5:], var[:22], b["t_in_window_s"], b["q"],
                     b["pair"], b["worst_after"], b["outlay"]))
    print("\nMODELLED inventory, REAL book, REAL executable price. Nothing "
          "submitted, filled or realized.")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:] or sorted(glob.glob(
        "/root/.claude/projects/-home-user-kalshi-arb-bot/"
        "1ec9ca12-5dab-5253-8ef5-126eaccc8b25/tool-results/"
        "mcp-Railway-get-logs-*.txt"))
    raise SystemExit(main(args))
