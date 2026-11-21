"""The deployed controller against the same controller with the
heavy-underdog stop, replayed on recorded quotes.

WHAT RUNS  `src.l5_btc5m_controller.decide`, unmodified, under the 17:05Z
live window's configuration (book-relative two-sided bids, clip $0.05,
side cap $0.30, unmatched cap $0.10, window-loss ceiling $0.20, requote
2 ticks, leg age limit 240 s, entry-EV gate waived as it was live).
  BASE  that configuration, as deployed.
  NEW   BASE + straddle_stop_adding_heavy_underdog.
One decision at every recorded quote (~2 s on 11-17..20, ~10 s on
11-11..16). Orders REST between decisions and leave only by filling or by
the controller's own CANCEL, so requoting and the new cancels are
exercised. A post-only bid at or above the ask is rejected.

FILLS (the recorder has no trades, so a fill is inferred)
  STRICT  the side's ask later reaches the bid's price: certain fill,
          worst selection.
  TOUCH   STRICT, or the side's bid later drops below our price.
Both are run; the truth lies between. Fill is in full at our price.
Maker fee 0, no rebate. Size and queue are not observed.

ACCOUNTING  research_accounting.Ledger: settlement proceeds minus cost,
summed over every fill and every window run. Downside is reported per
window: worst window, the 5% worst-window level, and windows losing more
than 5 cents. PAIRED = NEW minus BASE in the same window.
"""
import collections
import datetime as dt
import gzip
import json
import os
import random
import sys
from decimal import Decimal as D
from multiprocessing import Pool

sys.path.insert(0, ".")
sys.path.insert(0, "analysis/l5/btc5m")
os.environ["L5_BTC5M_CONTROLLER"] = "ON"
from src import l5_btc5m_controller as C            # noqa: E402
from research_accounting import Ledger              # noqa: E402

E = "analysis/l5/btc5m/evidence/"
FILES = (E + "recorder_10s_quotes_20261111_20261120.jsonl.gz",
         E + "recorder_2s_quotes_rest_20261117_20261120.jsonl.gz")


def params(new):
    return C.Params(
        entry_mode=C.STRADDLE, straddle_price=D("0.08"), clip_usd=D("0.05"),
        hedge_enabled=False, reserve_mode="NONE", max_exposure_usd=D("0.60"),
        straddle_book_relative=True, straddle_fill_model_measured=True,
        straddle_pair_ceiling=D("0.99"), straddle_repeat_enabled=True,
        straddle_max_side_usd=D("0.30"), straddle_max_unmatched_usd=D("0.10"),
        straddle_requote_ticks=2, straddle_max_window_loss_usd=D("0.20"),
        straddle_leg_max_age_s=240.0, straddle_ev_waiver=C.EV_WAIVER_TOKEN,
        straddle_stop_adding_heavy_underdog=new)


PAR = {False: params(False), True: params(True)}


def run_window(job):
    slug, rows, new, touch = job
    inv = {s: [D(0), D(0)] for s in C.SIDES}
    rest, fills, nid = [], [], 0
    stats = collections.Counter()
    for t, b, a in rows:
        sb = {C.UP: D(str(b)), C.DOWN: D(1) - D(str(a))}
        sa = {C.UP: D(str(a)), C.DOWN: D(1) - D(str(b))}
        keep = []
        for o in rest:
            hit = sa[o["side"]] <= o["price"] or (
                touch and sb[o["side"]] < o["price"])
            if hit:
                inv[o["side"]][0] += o["qty"]
                inv[o["side"]][1] += o["qty"] * o["price"]
                fills.append((o["side"], float(o["qty"]),
                              float(o["price"])))
            else:
                keep.append(o)
        rest = keep
        d = C.decide(
            book={"bid": str(b), "ask": str(a), "age_s": 0.1},
            t_in_window_s=t,
            inventory={s: {"qty": str(inv[s][0]), "cost": str(inv[s][1])}
                       for s in C.SIDES},
            resting=[{"id": o["id"], "side": o["side"],
                      "price": str(o["price"]), "qty": str(o["qty"]),
                      "age_s": max(t - o["t"], 0.0), "reason": o["reason"]}
                     for o in rest],
            params=PAR[new])
        for o in d["orders"]:
            if o.get("op") == "CANCEL":
                n0 = len(rest)
                rest = [x for x in rest if x["id"] != o["id"]]
                stats["cancels"] += n0 - len(rest)
        for o in d["orders"]:
            if o.get("op", "PLACE") != "PLACE":
                continue
            px, q = D(str(o["price"])), D(str(o["qty"]))
            if px >= sa[o["side"]]:
                stats["post_only_rejects"] += 1
                continue
            nid += 1
            rest.append({"id": "o%d" % nid, "side": o["side"], "price": px,
                         "qty": q, "t": t, "reason": o.get("reason")})
            stats["placed"] += 1
        hu = (d.get("evaluation") or {}).get("heavy_underdog") or {}
        stats["steps_stopped"] += int(any(hu.values()))
    return slug, new, touch, fills, dict(stats)


def main():
    q = collections.defaultdict(dict)
    tb = {}
    for path in FILES:
        for line in gzip.open(path, "rt"):
            r = json.loads(line)
            if r.get("to_beat") is not None:
                tb.setdefault(r["slug"], float(r["to_beat"]))
            if r.get("closed") or r.get("bid") is None \
                    or r.get("ask") is None:
                continue
            b, a = float(r["bid"]), float(r["ask"])
            if not (0 < b < a < 1):
                continue
            q[r["slug"]][r["utc"]] = (float(r["t"]), b, a)

    def nxt(s):
        d = dt.datetime.strptime(s[-16:], "%Y-%m-%d-%H%Mz") \
            + dt.timedelta(minutes=5)
        return "cpc-btc-updown-5m-" + d.strftime("%Y-%m-%d-%H%Mz")

    res = {s: ("UP" if tb[nxt(s)] >= tb[s] else "DOWN")
           for s in q if s in tb and nxt(s) in tb}
    only = sys.argv[1] if len(sys.argv) > 1 else None
    slugs = sorted(s for s in res if only is None or s[-16:-6] >= only)
    jobs = [(s, sorted(q[s].values()), new, touch) for s in slugs
            for new in (False, True) for touch in (False, True)]
    out = collections.defaultdict(dict)
    st = collections.defaultdict(collections.Counter)
    with Pool(os.cpu_count() or 2) as pool:
        for slug, new, touch, fills, stats in pool.imap_unordered(
                run_window, jobs, chunksize=8):
            out[(new, touch)][slug] = fills
            st[(new, touch)].update(stats)

    def pnl(fills, slug):
        return sum(sh * ((1.0 if res[slug] == sd else 0.0) - px)
                   for sd, sh, px in fills)

    def block(name, ws):
        if not ws:
            return
        print("\n==== %s: %d windows ====" % (name, len(ws)))
        for touch in (False, True):
            print("  fill model %s" % ("TOUCH" if touch else "STRICT"))
            per = {}
            for new in (False, True):
                led = Ledger(ws, size_known=False)
                unm = 0.0
                for s in ws:
                    u = d = 0.0
                    for sd, sh, px in out[(new, touch)][s]:
                        led.fill(s, sh, px, 0.0, res[s] == sd)
                        if sd == "UP":
                            u += sh
                        else:
                            d += sh
                    unm += abs(u - d)
                per[new] = [pnl(out[(new, touch)][s], s) for s in ws]
                v = sorted(per[new])
                t = led.totals()
                print("   %-4s fills %5d  cost $%8.2f  proceeds $%8.2f  "
                      "NET $%+8.3f  per window %+.5f | worst window %+.3f  "
                      "5%% worst %+.3f  windows losing >5c: %d | unmatched "
                      "shares/window %.3f" % (
                          "NEW" if new else "BASE", t["fills"], t["cost"],
                          t["proceeds"], t["net"],
                          t["net"] / t["windows_run"], v[0],
                          v[int(.05 * len(v))],
                          sum(1 for x in v if x < -0.05), unm / len(ws)))
            diff = [a - b for a, b in zip(per[True], per[False])]
            rng = random.Random(9)
            k = len(diff)
            ms = sorted(sum(rng.choice(diff) for _ in range(k))
                        for _ in range(3000))
            print("   PAIRED NEW - BASE: total $%+.3f [%+.3f,%+.3f]; windows "
                  "better %d, worse %d, same %d" % (
                      sum(diff), ms[75], ms[2924],
                      sum(1 for x in diff if x > 1e-9),
                      sum(1 for x in diff if x < -1e-9),
                      sum(1 for x in diff if abs(x) <= 1e-9)))

    for k in sorted(st):
        print("config %s/%s: %s" % ("NEW" if k[0] else "BASE",
                                    "TOUCH" if k[1] else "STRICT",
                                    dict(st[k])))
    block("11-17, 11-18 (2 s quotes)",
          [s for s in slugs if s[-16:-6] in ("2026-11-17", "2026-11-18")])
    block("11-19, 11-20 (2 s quotes)",
          [s for s in slugs if s[-16:-6] in ("2026-11-19", "2026-11-20")])
    early = [s for s in slugs if s[-16:-6] < "2026-11-17"]
    if early:
        block("11-11 .. 11-16 (10 s quotes: coarser fills)", early)
    block("ALL", slugs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
