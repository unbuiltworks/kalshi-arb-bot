"""Time-conditioned profit-floor protection, replayed SEQUENTIALLY.

HYPOTHESIS. Once the book holds a positive GUARANTEED worst-case floor late
in the window, stop reopening directional residual.

    worst = min(q_up, q_down) - cash          # guaranteed, outcome-free

METHOD. Walk each window's fills in time order. At each state compute the
floor and the seconds remaining. When `s_left <= T` and `floor >= F`, the
conditional lock TRIGGERS: every subsequent fill in that window is dropped
and the book as it stands at that instant is settled. Nothing is added to
the final book after the fact, and the decision uses only the state at that
moment -- never the outcome.

VARIANT "floor-preserving": on trigger we do NOT stop. We keep accepting
subsequent fills, but only those that leave the post-trade floor at or above
the floor we locked. A fill that would cut into the guaranteed floor is
skipped. This tries to keep pair improvement without reopening a hole.

Settlement is used only to score. 304 windows with real venue resolution.
"""
import json
from decimal import Decimal as D

from scripts.l5_residual_rescue import settlement, fills, WINDOW_S

TIMES = [180, 120, 90, 60, 45, 30]
FLOORS = [D(0), D("0.5"), D(1), D(2), D(5)]


def clean_window(fs, slug):
    tail = slug.rsplit("-", 1)[-1]
    try:
        end = float(tail) + WINDOW_S
    except ValueError:
        return None
    out = []
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
        out.append({"t": float(it.get("timestamp") or 0), "side": side,
                    "px": px, "size": sz,
                    "s_left": end - float(it.get("timestamp") or 0)})
    return out


def settle(q_up, q_down, cash, winner):
    return (q_up if winner == "up" else q_down) - cash


def replay(rows, winner, *, t_thresh=None, floor=None, mode="lock"):
    """mode: carry | lock | frozen5 | preserve"""
    q_up = q_down = cash = D(0)
    locked_at = None
    for f in rows:
        nq_up = q_up + (f["size"] if f["side"] == "up" else D(0))
        nq_down = q_down + (f["size"] if f["side"] == "down" else D(0))
        ncash = cash + f["px"] * f["size"]
        nfloor = min(nq_up, nq_down) - ncash

        if mode == "frozen5":
            if locked_at is not None:
                break
        elif mode == "lock" and locked_at is not None:
            break
        elif mode == "preserve" and locked_at is not None:
            # only fills that keep the guaranteed floor intact
            if nfloor < locked_at:
                continue

        q_up, q_down, cash = nq_up, nq_down, ncash
        cur = min(q_up, q_down) - cash

        if mode == "frozen5":
            if cur >= D(5):
                locked_at = cur
        elif mode in ("lock", "preserve") and locked_at is None:
            if f["s_left"] <= t_thresh and cur >= floor:
                locked_at = cur
    return {"pnl": settle(q_up, q_down, cash, winner),
            "locked": locked_at is not None, "floor_at_lock": locked_at}


def grid():
    win = settlement()
    per = fills()
    W = []
    for slug, fs in per.items():
        w = win.get(slug)
        if w is None:
            continue
        rows = clean_window(fs, slug)
        if not rows:
            continue
        W.append((slug, w, rows))
    return W


def summarise(res):
    p = sorted(r["pnl"] for r in res)
    n = len(p)
    return {"n": n, "total": sum(p), "mean": sum(p) / n,
            "median": p[n // 2], "losing": sum(1 for x in p if x < 0),
            "worst": p[0],
            "triggered": sum(1 for r in res if r["locked"])}


if __name__ == "__main__":
    W = grid()
    carry = summarise([replay(r, w, mode="carry") for _, w, r in W])
    fz = summarise([replay(r, w, mode="frozen5") for _, w, r in W])
    print("windows %d" % carry["n"])
    print("CARRY-TO-SETTLEMENT   total $%-10s mean $%-7s median $%-7s "
          "losing %-4d worst $%s"
          % (round(carry["total"], 2), round(carry["mean"], 3),
             round(carry["median"], 3), carry["losing"],
             round(carry["worst"], 2)))
    print("FROZEN PROFIT_LOCK_5  total $%-10s mean $%-7s median $%-7s "
          "losing %-4d worst $%-9s trig %d"
          % (round(fz["total"], 2), round(fz["mean"], 3),
             round(fz["median"], 3), fz["losing"], round(fz["worst"], 2),
             fz["triggered"]))
    print()
    for mode, title in (("lock", "CONDITIONAL LOCK (stop on trigger)"),
                        ("preserve", "FLOOR-PRESERVING (keep floor-safe fills)")):
        print("== %s ==" % title)
        print("%-6s %-7s %6s %11s %9s %9s %7s %10s %10s %10s"
              % ("T(s)", "floor", "trig", "total", "mean", "median",
                 "losing", "worst", "d_carry", "d_frozen5"))
        for T in TIMES:
            for F in FLOORS:
                s = summarise([replay(r, w, t_thresh=T, floor=F, mode=mode)
                               for _, w, r in W])
                print("%-6d %-7s %6d %11s %9s %9s %7d %10s %10s %10s"
                      % (T, F, s["triggered"], round(s["total"], 2),
                         round(s["mean"], 3), round(s["median"], 3),
                         s["losing"], round(s["worst"], 2),
                         "%+.2f" % (s["total"] - carry["total"]),
                         "%+.2f" % (s["total"] - fz["total"])))
            print()
