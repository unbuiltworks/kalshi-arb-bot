"""The EXISTING crypto controller vs the same controller with the shared
allocator judging its exposure-adding orders, on IDENTICAL data and the
IDENTICAL fill rule (entry_filter_study.run_window: a resting bid fills at
its price when a later quote's ask reaches it within LIFE_S). 187 recorded
BTC 5-minute windows (btc5m_aligned_20261112). ARITHMETIC REPLAY ON PUBLIC
QUOTES -- THEORETICAL, not executable profit.
Run from the repo root: python3 analysis/l5/btc5m/allocator_replay.py"""
import sys
from decimal import Decimal as D

sys.path.insert(0, ".")
from analysis.l5.btc5m import entry_filter_study as E   # noqa: E402
from analysis.l5.btc5m.one_sided_value import load, quote  # noqa: E402
from src import l5_btc5m_attribution as A               # noqa: E402
from src import l5_btc5m_controller as C                # noqa: E402
from src import l5_crypto_alloc_adapter as AD           # noqa: E402

POLICIES = [
    ("existing controller (live configuration)", None),
    ("+ allocator, fill loss 0", {"fill_loss": "0", "cfg": {}}),
    ("+ allocator, fill loss 0.01", {"fill_loss": "0.01", "cfg": {}}),
    ("+ allocator, fill loss 0.02", {"fill_loss": "0.02", "cfg": {}}),
    ("+ allocator, fill loss 0.01, additions only",
     {"fill_loss": "0.01", "cfg": {"judge_openings": False}}),
    ("+ allocator, fill loss 0.01, lambda 0.02",
     {"fill_loss": "0.01", "cfg": {"risk_aversion": "0.02"}}),
]


def run_window(rows, par, alloc):
    inv = {s: [D(0), D(0)] for s in C.SIDES}
    fills, nxt = [], 0.0
    refused = 0
    for i, r in enumerate(rows):
        t = r["t_in_window_s"]
        if t < nxt:
            continue
        nxt = t + E.LIFE_S
        ub, ua = quote(r, "UP")
        if ub is None or ua is None:
            continue
        p = r["p_up"]
        pu = p["value"] if p.get("ok") and p.get("value") is not None \
            else None
        invd = {s: {"qty": str(inv[s][0]), "cost": str(inv[s][1])}
                for s in C.SIDES}
        d = C.decide(
            book={"bid": str(ub), "ask": str(ua), "age_s": 0.1},
            t_in_window_s=t, inventory=invd, resting=[], params=par,
            p_up=None if pu is None else str(round(pu, 6)),
            p_source="VENUE_INDEX", p_age_s=r["index"].get("age_s"))
        if alloc is not None:
            prob = AD.probability_from_model(
                p, index_age_s=r["index"].get("age_s"))
            n0 = len([o for o in d["orders"] if o.get("op", "PLACE") ==
                      "PLACE"])
            d = AD.filter_decision(d, inventory=invd, prob=prob,
                                   tick=D("0.01"), min_qty=D("1"),
                                   fill_loss=alloc["fill_loss"],
                                   alloc_cfg=alloc["cfg"])
            refused += n0 - len([o for o in d["orders"]
                                 if o.get("op", "PLACE") == "PLACE"])
        for o in d["orders"]:
            if o.get("op", "PLACE") != "PLACE":
                continue
            side, px, q = o["side"], D(str(o["price"])), D(str(o["qty"]))
            for j in range(i + 1, len(rows)):
                if rows[j]["t_in_window_s"] - t > E.LIFE_S:
                    break
                _b, a = quote(rows[j], side)
                if a is not None and D(str(a)) <= px:
                    inv[side][0] += q
                    inv[side][1] += q * px
                    fills.append({"side": side, "qty": q, "cost": q * px,
                                  "fee": "0"})
                    break
    att = A.attribute(fills, rows[0]["settled_outcome"] == "UP")
    spend = sum(float(f["cost"]) for f in fills)
    return (float(att["matched"]["pnl"]), float(att["unmatched"]["pnl"]),
            float(att["net_usd"]), len(fills), spend, refused)


def main():
    data = load()
    names = list(data)
    h1, h2 = names[:len(names) // 2], names[len(names) // 2:]
    par = C.Params(**E.BASE)
    base = None
    print("windows: %d (THEORETICAL replay on public quotes; maker fee 0)"
          % len(names))
    for lab, alloc in POLICIES:
        res = {m: run_window(data[m], par, alloc) for m in names}
        if base is None:
            base = res
        print("-- %s" % lab)
        for hn, ws in (("FIRST HALF", h1), ("SECOND HALF", h2),
                       ("ALL", names)):
            print("   %-11s fills %4d  spend $%7.2f  matched %s  residual %s"
                  % (hn, sum(res[m][3] for m in ws),
                     sum(res[m][4] for m in ws),
                     E.fmt([res[m][0] for m in ws]),
                     E.fmt([res[m][1] for m in ws])))
            print("   %-11s net %s  total $%+.3f  refused %d" % (
                "", E.fmt([res[m][2] for m in ws]),
                sum(res[m][2] for m in ws),
                sum(res[m][5] for m in ws)))
        if alloc is not None:
            diff = [res[m][2] - base[m][2] for m in names]
            print("   PAIRED vs existing, net per window %s, total $%+.3f"
                  % (E.fmt(diff), sum(diff)))


if __name__ == "__main__":
    main()
