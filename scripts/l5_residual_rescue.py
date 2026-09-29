"""Residual rescue: inventory economics first, market direction second.

THE ARITHMETIC. With q_up, q_down shares and total cash C, settlement P&L
is q_up - C if UP wins and q_down - C if DOWN wins, so

    worst_case = min(q_up, q_down) - C

Buying n shares of the UNDERWEIGHT side at an all-in price p (price plus
fee) while n <= residual moves the minimum leg:

    worst_case' = (q_under + n) - (C + n*p) = (q_under - C) + n*(1 - p)

which is INCREASING in n for any p < 1. So a rescue never needs the market
to move our way, and never needs a view: it needs a price below par and
enough depth. Past n = residual the minimum leg swaps and further buying
stops helping, so the useful size is capped at the residual.

The maximum all-in price that reaches a target T with a FULL rescue is

    p_max = 1 - (T - q_under + C) / residual

MARKET DIRECTION IS NOT AN INPUT to any decision here. It enters only when
asking whether a price at or below p_max was actually observed afterwards.

EXECUTABILITY, conservatively: a rescue counts as executable only if L5's
OWN subsequent fills in that window, on the side we need, traded at or
below p_max after a one-tick adverse adjustment. Those are real prints. We
do not assume a touch fill and we do not invent depth: the available size
is what actually traded at that price or better.

SETTLEMENT is used only to score the outcome, never to choose a rescue.
"""
import collections
import json
from decimal import Decimal as D

TICK = D("0.01")
THETA_TAKER = D("0.0695")
CLIP = D(10)
WINDOW_S = 900.0


def all_in(px):
    return px + THETA_TAKER * px * (D(1) - px)


def p_max_for(target, q_under, cash, residual):
    """Max ALL-IN price at which a full rescue reaches `target`."""
    if residual <= 0:
        return None
    return D(1) - (target - q_under + cash) / residual


def settlement():
    out = {}
    for ln in open("data/l5/raw/markets.jsonl"):
        try:
            d = json.loads(ln)
        except Exception:
            continue
        for m in (d.get("payload") or []):
            slug = str(m.get("slug") or "")
            if not slug.startswith("btc-updown-15m"):
                continue
            if str(m.get("umaResolutionStatus")) != "resolved":
                continue
            try:
                oc = json.loads(m.get("outcomes") or "[]")
                px = json.loads(m.get("outcomePrices") or "[]")
            except Exception:
                continue
            for name, p in zip(oc, px):
                if str(p) in ("1", "1.0", "1.00"):
                    out[slug] = str(name).strip().lower()
    return out


def fills():
    seen, per = set(), collections.defaultdict(list)
    for fn in ("data/l5/raw/trades.jsonl",
               "data/l5/raw/trades_takeronly.jsonl"):
        try:
            fh = open(fn)
        except OSError:
            continue
        for ln in fh:
            try:
                d = json.loads(ln)
            except Exception:
                continue
            for it in (d.get("payload") or []):
                slug = str(it.get("eventSlug") or "")
                if not slug.startswith("btc-updown-15m"):
                    continue
                f = (it.get("transactionHash"), it.get("asset"),
                     it.get("size"), it.get("price"), it.get("timestamp"))
                if f in seen:
                    continue
                seen.add(f)
                per[slug].append(it)
    for k in per:
        per[k].sort(key=lambda r: r.get("timestamp") or 0)
    return per


def window_states(fs):
    """Sequential inventory states, and the remaining prints after each."""
    q = {"up": D(0), "down": D(0)}
    cash = D(0)
    seq = []
    clean = []
    for it in fs:
        side = str(it.get("outcome") or "").strip().lower()
        if side not in ("up", "down"):
            continue
        try:
            px, sz = D(str(it.get("price"))), D(str(it.get("size")))
        except Exception:
            continue
        if not (D(0) < px < D(1)) or sz <= 0:
            continue
        clean.append({"t": float(it.get("timestamp") or 0), "side": side,
                      "px": px, "size": sz})
    for i, f in enumerate(clean):
        q[f["side"]] += f["size"]
        cash += f["px"] * f["size"]
        resid = q["up"] - q["down"]
        seq.append({
            "i": i, "t": f["t"], "q_up": q["up"], "q_down": q["down"],
            "cash": cash, "matched": min(q["up"], q["down"]),
            "residual": abs(resid),
            "heavy": "up" if resid > 0 else ("down" if resid < 0 else None),
            "under": "down" if resid > 0 else ("up" if resid < 0 else None),
            "worst": min(q["up"], q["down"]) - cash,
            "pnl_up": q["up"] - cash, "pnl_down": q["down"] - cash,
            "future": clean[i + 1:],
        })
    return seq


def executable(state, p_max, need):
    """Cheapest subsequent print on the needed side at or below p_max.

    Conservative: L5's own print price plus one adverse tick must still
    clear p_max, and the size available is what actually traded there.
    """
    if p_max is None or p_max <= 0:
        return None
    got = D(0)
    cost = D(0)
    first_t = None
    for f in state["future"]:
        if f["side"] != state["under"]:
            continue
        p = all_in(f["px"] + TICK)
        if p > p_max:
            continue
        take = min(need - got, f["size"])
        if take <= 0:
            break
        got += take
        cost += p * take
        if first_t is None:
            first_t = f["t"]
        if got >= need:
            break
    if got <= 0:
        return None
    return {"qty": got, "cost": cost, "avg_all_in": cost / got,
            "first_t": first_t, "full": got >= need}


def run():
    win = settlement()
    per = fills()
    rows = []
    for slug, fs in per.items():
        w = win.get(slug)
        if w is None:
            continue
        seq = window_states(fs)
        if not seq:
            continue
        final = seq[-1]
        realized = (final["q_up"] if w == "up" else final["q_down"]) - final["cash"]
        # the FIRST state where worst-case is negative and a residual exists
        cand = None
        for st in seq:
            if st["under"] is None or st["residual"] <= 0:
                continue
            if st["worst"] >= 0:
                continue
            cand = st
            break
        rows.append({"slug": slug, "winner": w, "realized": realized,
                     "final": final, "first_bad": cand, "seq": seq})
    return rows


def evaluate(rows, target, mode):
    """mode: full | q10 | q10x3"""
    improved = flattened = profitable = 0
    delta_tot = D(0)
    n_exec = 0
    losing = [r for r in rows if r["realized"] < 0]
    for r in losing:
        st = r["first_bad"]
        if st is None:
            continue
        need = st["residual"]
        if mode == "q10":
            need = min(need, CLIP)
        elif mode == "q10x3":
            need = min(need, CLIP * 3)
        pm = p_max_for(target, st["q_down"] if st["under"] == "down"
                       else st["q_up"], st["cash"], st["residual"])
        ex = executable(st, pm, need)
        if ex is None:
            continue
        n_exec += 1
        # apply the rescue to the FINAL book, then settle
        q_up, q_down = r["final"]["q_up"], r["final"]["q_down"]
        cash = r["final"]["cash"] + ex["cost"]
        if st["under"] == "down":
            q_down += ex["qty"]
        else:
            q_up += ex["qty"]
        new = (q_up if r["winner"] == "up" else q_down) - cash
        d = new - r["realized"]
        delta_tot += d
        if d > 0:
            improved += 1
        if new >= 0:
            flattened += 1
        if new > 0:
            profitable += 1
    return {"losing": len(losing), "executable": n_exec,
            "improved": improved, "flattened": flattened,
            "profitable": profitable, "total_delta": delta_tot,
            "avg_delta": (delta_tot / n_exec) if n_exec else None}


if __name__ == "__main__":
    R = run()
    losing = [r for r in R if r["realized"] < 0]
    print("windows with settlement: %d | losing: %d | total realized $%s"
          % (len(R), len(losing),
             round(sum(r["realized"] for r in R), 2)))
    print("losing windows with a negative-worst-case residual state: %d"
          % sum(1 for r in losing if r["first_bad"] is not None))
    print()
    for target, tname in ((D(0), ">= $0"), (D(1), ">= +$1"), (D(5), ">= +$5")):
        print("== target worst-case %s ==" % tname)
        for mode in ("full", "q10", "q10x3"):
            e = evaluate(R, target, mode)
            print("  %-6s exec=%-4d improved=%-4d flattened=%-4d "
                  "profitable=%-4d total=$%-11s avg=$%s"
                  % (mode, e["executable"], e["improved"], e["flattened"],
                     e["profitable"], round(e["total_delta"], 2),
                     "n/a" if e["avg_delta"] is None
                     else round(e["avg_delta"], 3)))
        print()


def worst_case_view(rows, target, mode):
    """The question as asked: can WORST CASE be moved to >= target?

    This is deterministic -- it needs no view and no outcome. Realized P&L
    is reported beside it because a rescue is INSURANCE: it raises the
    floor and lowers the ceiling, so 'worst case flattened' and 'realized
    improved' are different questions and must not be conflated.
    """
    reachable = executable_n = 0
    wc_before = wc_after = D(0)
    realized_delta = D(0)
    considered = 0
    for r in rows:
        st = r["first_bad"]
        if st is None:
            continue
        considered += 1
        need = st["residual"]
        if mode == "q10":
            need = min(need, CLIP)
        elif mode == "q10x3":
            need = min(need, CLIP * 3)
        q_under = st["q_down"] if st["under"] == "down" else st["q_up"]
        pm = p_max_for(target, q_under, st["cash"], st["residual"])
        if pm is not None and pm > 0:
            reachable += 1
        ex = executable(st, pm, need)
        if ex is None:
            continue
        executable_n += 1
        wc_before += st["worst"]
        new_under = q_under + ex["qty"]
        heavy = st["q_up"] if st["under"] == "down" else st["q_down"]
        wc_after += min(new_under, heavy) - (st["cash"] + ex["cost"])
        q_up, q_down = r["final"]["q_up"], r["final"]["q_down"]
        cash = r["final"]["cash"] + ex["cost"]
        if st["under"] == "down":
            q_down += ex["qty"]
        else:
            q_up += ex["qty"]
        realized_delta += ((q_up if r["winner"] == "up" else q_down)
                           - cash) - r["realized"]
    return {"considered": considered, "price_reachable": reachable,
            "executable": executable_n,
            "worst_before": wc_before, "worst_after": wc_after,
            "worst_delta": wc_after - wc_before,
            "realized_delta": realized_delta}
