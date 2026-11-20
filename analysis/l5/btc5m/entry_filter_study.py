"""Where does the two-sided strategy lose, and does any decision-time
filter fix it across independent windows?

INPUT: the same 187 resolved windows as one_sided_value.py. No sizes, no
fills: a quote FILLS when the side's ask reaches its price within the 10 s
before the next requote (THROUGH). Maker fee 0, no rebate, no incentive.

PART A -- one two-sided quote round, per share, by what was observable
when it was placed. Both legs at their own bids:
    both fill   -> a pair costing bid_up + bid_down, paying 1.00
    one fills   -> a lone leg, valued (i) at settlement, (ii) at that
                   side's bid when it filled (what it could be sold for)
    none        -> nothing
The split shows which line carries the loss. Buckets were fixed before
looking; FIRST HALF is discovery, SECOND HALF is the check.

PART B -- the EXISTING controller under settings it already has, replayed
window by window (as one_sided_policy_replay.py). Each setting is judged
on the half it was not chosen on.

ARITHMETIC REPLAY ON PUBLIC QUOTES. Not an executable profit.
"""
import os
import random
import sys
from decimal import Decimal as D

sys.path.insert(0, ".")
os.environ["L5_BTC5M_CONTROLLER"] = "ON"
from src import l5_btc5m_attribution as A          # noqa: E402
from src import l5_btc5m_controller as C           # noqa: E402
from analysis.l5.btc5m.one_sided_value import load, quote  # noqa: E402

LIFE_S = 10.0


def boot(vals, n=3000, seed=5):
    if len(vals) < 8:
        return None
    rng = random.Random(seed)
    k = len(vals)
    ms = sorted(sum(rng.choice(vals) for _ in range(k)) / k
                for _ in range(n))
    return ms[int(.025 * n)], ms[int(.975 * n)]


def fmt(vals):
    if not vals:
        return "      n/a"
    m = sum(vals) / len(vals)
    c = boot(vals)
    return "%+.4f %s" % (m, "[%+.3f,%+.3f]" % c if c else "[few]")


# ---------------------------------------------------------------- PART A

def rounds(rows):
    out, nxt = [], 0.0
    for i, r in enumerate(rows):
        t = r["t_in_window_s"]
        if t < nxt or t > 270:
            continue
        nxt = t + LIFE_S
        ub, ua = quote(r, "UP")
        db, da = quote(r, "DOWN")
        if None in (ub, ua, db, da) or not (0 < ub < ua < 1):
            continue
        fill = {}
        for side, px in (("UP", ub), ("DOWN", db)):
            for j in range(i + 1, len(rows)):
                if rows[j]["t_in_window_s"] - t > LIFE_S:
                    break
                b, a = quote(rows[j], side)
                if a is not None and a <= px:
                    fill[side] = (px, b)
                    break
        won = r["settled_outcome"]
        pair = settle = mark = 0.0
        kind = "none"
        if len(fill) == 2:
            kind, pair = "both", 1.0 - (ub + db)
        elif len(fill) == 1:
            side, (px, b) = next(iter(fill.items()))
            kind = "one"
            settle = (1.0 if won == side else 0.0) - px
            mark = (b - px) if b is not None else 0.0
        p = r["p_up"]
        pv = p["value"] if p.get("ok") and p.get("value") is not None \
            else None
        prev = None
        for j in range(i - 1, -1, -1):
            if t - rows[j]["t_in_window_s"] >= 10:
                prev = quote(rows[j], "UP")[0]
                break
        out.append({
            "t": t, "spread": round(ua - ub, 4), "kind": kind,
            "pair": pair, "settle": settle, "mark": mark,
            "fav": max(ub, db),
            "move": None if prev is None else abs(ub - prev),
            "gap": None if pv is None else abs(pv - (ub + ua) / 2)})
    return out


BUCKETS = [
    ("all rounds", lambda r: True),
    ("t < 60 s", lambda r: r["t"] < 60),
    ("t 60-150 s", lambda r: 60 <= r["t"] < 150),
    ("t >= 150 s", lambda r: r["t"] >= 150),
    ("spread 0.01", lambda r: r["spread"] <= 0.0101),
    ("spread 0.02", lambda r: 0.0101 < r["spread"] <= 0.0201),
    ("spread >= 0.03", lambda r: r["spread"] > 0.0201),
    ("favourite bid < 0.60", lambda r: r["fav"] < 0.60),
    ("favourite bid 0.60-0.80", lambda r: 0.60 <= r["fav"] < 0.80),
    ("favourite bid >= 0.80", lambda r: r["fav"] >= 0.80),
    ("moved < 1c in last 10 s", lambda r: r["move"] is not None
     and r["move"] < 0.0101),
    ("moved 1-3c in last 10 s", lambda r: r["move"] is not None
     and 0.0101 <= r["move"] < 0.0301),
    ("moved >= 3c in last 10 s", lambda r: r["move"] is not None
     and r["move"] >= 0.0301),
    ("model within 3c of mid", lambda r: r["gap"] is not None
     and r["gap"] < 0.03),
    ("model >= 8c from mid", lambda r: r["gap"] is not None
     and r["gap"] >= 0.08),
    ("calm: spread>=0.02, moved<1c, t<150", lambda r: r["spread"] > 0.0101
     and r["move"] is not None and r["move"] < 0.0101 and r["t"] < 150),
]


def part_a(data, names, P):
    per = {m: rounds(data[m]) for m in names}
    halves = (("FIRST HALF", names[:len(names) // 2]),
              ("SECOND HALF", names[len(names) // 2:]))
    P("== PART A: one quote round, USD per share, mean of window means "
      "[95% over windows] ==")
    P("   net = pair gain + lone leg at SETTLEMENT;  net@mark = pair gain "
      "+ lone leg at its bid when filled")
    for lab, pred in BUCKETS:
        P("-- %s" % lab)
        for hn, ws in halves:
            nb = no = n = 0
            wv = {"pair": [], "settle": [], "net": [], "netmark": []}
            for m in ws:
                xs = [r for r in per[m] if pred(r)]
                if not xs:
                    continue
                n += len(xs)
                nb += sum(1 for r in xs if r["kind"] == "both")
                no += sum(1 for r in xs if r["kind"] == "one")
                k = len(xs)
                wv["pair"].append(sum(r["pair"] for r in xs) / k)
                wv["settle"].append(sum(r["settle"] for r in xs) / k)
                wv["net"].append(sum(r["pair"] + r["settle"]
                                     for r in xs) / k)
                wv["netmark"].append(sum(r["pair"] + r["mark"]
                                         for r in xs) / k)
            if not n:
                P("   %-11s no rounds" % hn)
                continue
            P("   %-11s n=%5d both %4.1f%% one %4.1f%%  pair %s  lone %s"
              % (hn, n, 100 * nb / n, 100 * no / n, fmt(wv["pair"]),
                 fmt(wv["settle"])))
            P("   %-11s net %s   net@mark %s" % (
                "", fmt(wv["net"]), fmt(wv["netmark"])))


# ---------------------------------------------------------------- PART B

BASE = dict(
    entry_mode=C.STRADDLE, straddle_price=D("0.08"), clip_usd=D("0.05"),
    hedge_enabled=False, reserve_mode="NONE", max_exposure_usd=D("0.60"),
    straddle_book_relative=True, straddle_fill_model_measured=True,
    straddle_pair_ceiling=D("0.99"), straddle_repeat_enabled=True,
    straddle_max_side_usd=D("0.30"), straddle_max_unmatched_usd=D("0.10"),
    straddle_requote_ticks=2, straddle_max_window_loss_usd=D("0.20"),
    straddle_leg_max_age_s=240.0, straddle_ev_waiver=C.EV_WAIVER_TOKEN)
CONFIGS = [
    ("live configuration", {}),
    ("pair ceiling 0.98", {"straddle_pair_ceiling": D("0.98")}),
    ("pair ceiling 0.97", {"straddle_pair_ceiling": D("0.97")}),
    ("no new entry after 150 s", {"no_new_entry_after_s": 150.0}),
    ("no new entry after 60 s", {"no_new_entry_after_s": 60.0}),
    ("unmatched cap $0.03", {"straddle_max_unmatched_usd": D("0.03")}),
    ("shade heavy side 1 tick/clip",
     {"straddle_reprice_ticks_per_clip": 1}),
    ("shade heavy side 3 ticks/clip",
     {"straddle_reprice_ticks_per_clip": 3}),
    ("one-sided rule", {"straddle_one_sided_control": True}),
    ("ceiling 0.98 + cap $0.03 + stop 150 s",
     {"straddle_pair_ceiling": D("0.98"),
      "straddle_max_unmatched_usd": D("0.03"),
      "no_new_entry_after_s": 150.0}),
]


def run_window(rows, par):
    inv = {s: [D(0), D(0)] for s in C.SIDES}
    fills, nxt = [], 0.0
    for i, r in enumerate(rows):
        t = r["t_in_window_s"]
        if t < nxt:
            continue
        nxt = t + LIFE_S
        ub, ua = quote(r, "UP")
        if ub is None or ua is None:
            continue
        p = r["p_up"]
        pu = p["value"] if p.get("ok") and p.get("value") is not None \
            else None
        d = C.decide(
            book={"bid": str(ub), "ask": str(ua), "age_s": 0.1},
            t_in_window_s=t,
            inventory={s: {"qty": str(inv[s][0]), "cost": str(inv[s][1])}
                       for s in C.SIDES},
            resting=[], params=par,
            p_up=None if pu is None else str(round(pu, 6)),
            p_source="VENUE_INDEX", p_age_s=r["index"].get("age_s"))
        for o in d["orders"]:
            if o.get("op", "PLACE") != "PLACE":
                continue
            side, px, q = o["side"], D(str(o["price"])), D(str(o["qty"]))
            for j in range(i + 1, len(rows)):
                if rows[j]["t_in_window_s"] - t > LIFE_S:
                    break
                _b, a = quote(rows[j], side)
                if a is not None and D(str(a)) <= px:
                    inv[side][0] += q
                    inv[side][1] += q * px
                    fills.append({"side": side, "qty": q, "cost": q * px,
                                  "fee": "0"})
                    break
    att = A.attribute(fills, rows[0]["settled_outcome"] == "UP")
    return (float(att["matched"]["pnl"]), float(att["unmatched"]["pnl"]),
            float(att["net_usd"]), len(fills),
            float(att["matched"]["pair_cost"] or 0),
            float(att["matched"]["qty"]))


def part_b(data, names, P):
    h1, h2 = names[:len(names) // 2], names[len(names) // 2:]
    P("\n== PART B: the existing controller, USD per window, mean "
      "[95% over windows] ==")
    for lab, over in CONFIGS:
        par = C.Params(**dict(BASE, **over))
        res = {m: run_window(data[m], par) for m in names}
        P("-- %s" % lab)
        for hn, ws in (("FIRST HALF", h1), ("SECOND HALF", h2)):
            mq = sum(res[m][5] for m in ws)
            pc = (sum(res[m][4] * res[m][5] for m in ws) / mq) if mq else 0
            P("   %-11s fills %4d  matched %s  residual %s" % (
                hn, sum(res[m][3] for m in ws),
                fmt([res[m][0] for m in ws]), fmt([res[m][1] for m in ws])))
            P("   %-11s net %s   avg matched pair cost %.4f" % (
                "", fmt([res[m][2] for m in ws]), pc))


def main():
    data = load()
    names = list(data)
    P = print
    P("windows: %d (first half %d, second half %d)" % (
        len(names), len(names) // 2, len(names) - len(names) // 2))
    part_a(data, names, P)
    part_b(data, names, P)
    return 0


if __name__ == "__main__":
    sys.exit(main())
