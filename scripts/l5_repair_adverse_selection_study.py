"""The repair adverse-selection study, over every usable BTC15m window.

Read-only. Authenticated observer rows for the actions and the BBO,
authenticated trade tape for the prints. No order is placed, no fill is
inferred, and an observed trade is never called a fill.
"""
import collections
import datetime as dt
import json
import statistics
from decimal import Decimal as D

from src.l5_repair_adverse_selection import (
    L5Inventory, RestingOffer, classify, own_price, quantiles,
    NO_INTERACTION, MARKETABLE, TRADE_AT_PRICE, TRADE_THROUGH_PRICE,
    UP, DOWN, _d)

ROWS = "analysis/pmus/evidence/observer_rows_all_windows.jsonl"
TAPE = "analysis/pmus/evidence/auth_book_tape.jsonl"
MAKER_COPYABLE = "MAKER_COPYABLE"
WINDOW_S = 900.0


def load_rows():
    rows = [json.loads(l) for l in open(ROWS) if l.strip()]
    out = []
    for r in rows:
        if r.get("backlog") or not r.get("still_in_window_at_receipt"):
            continue
        b = (r.get("pmus") or {}).get("bbo") or {}
        if b.get("bid") is None or b.get("ask") is None:
            continue
        out.append(r)
    return out


def load_tape():
    tape = collections.defaultdict(list)
    for l in open(TAPE, errors="replace"):
        if "{" not in l:
            continue
        try:
            d = json.loads(l[l.index("{"):], strict=False)
        except Exception:                                      # noqa: BLE001
            continue
        for m, rs in (d.get("markets") or {}).items():
            for x in rs:
                if isinstance(x, dict) and x.get("t") and x.get("px"):
                    tape[m].append((float(x["t"]), D(str(x["px"]))))
    for m in tape:
        tape[m].sort()
    return tape


def ts(r):
    return dt.datetime.fromisoformat(r["receipt_utc"]).timestamp()


def build():
    rows = load_rows()
    tape = load_tape()
    by_window = collections.defaultdict(list)
    for r in rows:
        by_window[r["alignment"]["pmus_slug"]].append(r)

    offers = []
    per_window = {}
    for slug, rs in sorted(by_window.items()):
        rs.sort(key=ts)
        start = ts(rs[0])
        end = start + WINDOW_S
        inv = L5Inventory()
        prints = tape.get(slug, [])
        made = 0
        for r in rs:
            l5 = r.get("l5") or {}
            side = l5.get("pmus_side")
            px = _d(l5.get("l5_price"))
            sz = _d(l5.get("l5_size"))
            if side not in (UP, DOWN) or px is None or sz is None or sz <= 0:
                continue
            klass = classify(inv.residual(), side)
            copy = (r.get("copyability") or {}).get("klass")
            if copy == MAKER_COPYABLE:
                b = (r.get("pmus") or {}).get("bbo") or {}
                bid, ask = _d(b.get("bid")), _d(b.get("ask"))
                spread = (ask - bid) if (bid is not None and ask is not None) else None
                opp = inv.avg(DOWN if side == UP else UP)
                o = RestingOffer(
                    key=r["action_key"], market=slug, side=side, own_px=px,
                    qty=sz, t=ts(r), window_end=end, klass=klass,
                    residual_before=inv.residual(), opposite_avg=opp,
                    spread=spread, tte_s=r.get("tte_s_at_receipt"))
                for t, wire in prints:
                    if t < o.t:
                        continue
                    if t > end:
                        break
                    o.observe_trade(t, wire)
                offers.append(o)
                made += 1
            # L5's own row is a completed trade: it moves HIS inventory
            inv.acquire(side, sz, px)
        per_window[slug] = {"actions": len(rs), "offers": made,
                            "prints": len(prints)}
    return offers, per_window


def summarise(rows, label):
    if not rows:
        print("  %-18s  n=0" % label)
        return
    n = len(rows)
    inter = [r for r in rows
             if r["interaction"] in (TRADE_AT_PRICE, TRADE_THROUGH_PRICE)]
    expired = [r for r in rows if r["expired_without_interaction"]]
    det = [D(r["max_adverse"]) for r in rows]      # 0 counted, not dropped
    lat = [r["latency_s"] for r in rows if r["latency_s"] is not None]
    pc0 = [D(r["pair_cost_at_offer"]) for r in rows if r["pair_cost_at_offer"]]
    pcd = [D(r["pair_cost_deteriorated"]) for r in rows
           if r["pair_cost_deteriorated"]]
    q = quantiles(det)
    print("  %-18s n=%-4d interaction %5.1f%%  expired-no-interaction %5.1f%%"
          % (label, n, 100.0 * len(inter) / n, 100.0 * len(expired) / n))
    print("      adverse move    p50 %-10s p75 %-10s p90 %-10s (n=%s, zeros kept)"
          % (q["p50"], q["p75"], q["p90"], q["n"]))
    if lat:
        print("      latency_s       p50 %-8.1f p90 %-8.1f max %-8.1f"
              % (statistics.median(lat), sorted(lat)[int(len(lat) * .9)],
                 max(lat)))
    if pc0:
        qq = quantiles(pc0)
        print("      pair @ offer    p50 %-8s p75 %-8s p90 %-8s"
              % (qq["p50"], qq["p75"], qq["p90"]))
    if pcd:
        qq = quantiles(pcd)
        print("      pair @ deterior p50 %-8s p75 %-8s p90 %-8s"
              % (qq["p50"], qq["p75"], qq["p90"]))


def bucket(rows, fn, label, order=None):
    g = collections.defaultdict(list)
    for r in rows:
        g[fn(r)].append(r)
    print("\n  by %s" % label)
    keys = order or sorted(g, key=str)
    for k in keys:
        if k in g:
            summarise(g[k], str(k))


def main():
    offers, per_window = build()
    rows = [o.as_dict() for o in offers]

    print("REPAIR ADVERSE SELECTION -- read-only, %d windows" % len(per_window))
    print("no order placed, no fill inferred; an observed trade is not a fill")
    print()
    covered = set()
    for slug, d in sorted(per_window.items()):
        ok = d["prints"] > 0
        if ok:
            covered.add(slug)
        print("  %-42s actions %3d  offers %3d  prints %5d  %s"
              % (slug, d["actions"], d["offers"], d["prints"],
                 "TAPE" if ok else "NO TAPE -- EXCLUDED"))
    print()
    print("  A window with no prints cannot show an interaction, so its")
    print("  offers are DROPPED rather than counted as non-interacting.")
    print("  Keeping them would have read as 94.5%% expiry-without-")
    print("  interaction when the truth is that we never watched.")
    dropped = [r for r in rows if r["market"] not in covered]
    rows = [r for r in rows if r["market"] in covered]
    print("  windows kept %d/%d   offers kept %d, dropped %d"
          % (len(covered), len(per_window), len(rows), len(dropped)))
    print()

    repair = [r for r in rows if r["class"] == "REDUCE_RESIDUAL"]
    flat = [r for r in rows if r["class"] == "FLAT_OPEN"]
    deepen = [r for r in rows if r["class"] == "INCREASE_RESIDUAL"]

    print("HEADLINE")
    summarise(repair, "REDUCE_RESIDUAL")
    summarise(deepen, "INCREASE_RESIDUAL")
    summarise(flat, "FLAT_OPEN (control)")
    print()
    print("  INCREASE_RESIDUAL is the better-powered control: it is the SAME")
    print("  inventory state at the SAME moment, resting on the other side.")
    print("  FLAT_OPEN is structurally rare -- L5's residual is only zero")
    print("  before his first trade of a window -- so it cannot carry a")
    print("  comparison on its own.")
    print()
    print("INTERACTION MIX")
    for label, rs in (("REDUCE_RESIDUAL", repair),
                      ("INCREASE_RESIDUAL", deepen), ("FLAT_OPEN", flat)):
        c = collections.Counter(r["interaction"] for r in rs)
        print("  %-18s %s" % (label, dict(c)))

    def px_bucket(r):
        p = D(r["offer_own_px"])
        for hi in ("0.20", "0.40", "0.60", "0.80"):
            if p < D(hi):
                return "<%s" % hi
        return ">=0.80"

    def tte_bucket(r):
        t = r["tte_s"]
        if t is None:
            return "unknown"
        for hi in (300, 600):
            if t < hi:
                return "<%ds" % hi
        return ">=600s"

    def spread_bucket(r):
        s = r["spread"]
        if s is None:
            return "unknown"
        s = D(s)
        return "<=0.02" if s <= D("0.02") else ("<=0.05" if s <= D("0.05")
                                                else ">0.05")

    def resid_bucket(r):
        a = abs(D(r["abs_residual_before"]))
        for hi in ("10", "50"):
            if a < D(hi):
                return "<%s" % hi
        return ">=50"

    print("\n" + "=" * 70)
    print("REPAIR ORDERS, BROKEN OUT")
    print("=" * 70)
    bucket(repair, px_bucket, "repair price",
           ["<0.20", "<0.40", "<0.60", "<0.80", ">=0.80"])
    bucket(repair, tte_bucket, "time to expiry", ["<300s", "<600s", ">=600s",
                                                  "unknown"])
    bucket(repair, spread_bucket, "spread", ["<=0.02", "<=0.05", ">0.05",
                                             "unknown"])
    bucket(repair, resid_bucket, "residual magnitude", ["<10", "<50", ">=50"])
    print("\n" + "=" * 70)
    print("CONTROL (INCREASE_RESIDUAL -- same state, other side)")
    print("=" * 70)
    bucket(deepen, px_bucket, "offer price",
           ["<0.20", "<0.40", "<0.60", "<0.80", ">=0.80"])
    bucket(deepen, tte_bucket, "time to expiry", ["<300s", "<600s", ">=600s",
                                                  "unknown"])

    json.dump(rows, open("analysis/pmus/evidence/probe08/"
                         "repair_adverse_selection.json", "w"), indent=1)
    print("\n  rows written: %d" % len(rows))


if __name__ == "__main__":
    main()
