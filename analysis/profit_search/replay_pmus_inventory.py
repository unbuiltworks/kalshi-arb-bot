"""PMUS replay of the inventory controller, on PMUS fees and PMUS-measured fills.

WHAT IS DIFFERENT FROM replay_inventory.py, AND WHY IT HAD TO BE

  * Venue. The windows are real PMUS `cpc-btc-updown-15m-*` windows assembled
    from the live tape service (see pmus_windows_from_logs.py), settled from the
    venue's own terminal print after expiry.
  * Fees. PMUS's published schedule via `src/pmus_fees.py`: no per-order
    charge, banker's rounding, and the maker rebate held OUT of selection as a
    credit. The Kalshi constants in replay_b overstated cost on every decision.
  * FILLS ARE NOT SIMULATED FROM A QUEUE. The shadow was shown to be wrong
    about fills, so this replay does not model one. A resting order fills by a
    Bernoulli draw at the probability MEASURED on 965 of our own submitted
    orders, and an order whose rest time exceeds the 32.2 s calibrated horizon
    cannot be priced at all -- the engine refuses it rather than extrapolate.
  * THE RESULT IS A DISTRIBUTION, NOT A PATH. A single seed is one draw of a
    random fill sequence and tells you almost nothing. Every arm is run over
    many seeds and reported as mean +/- sd with its worst seed, so a result
    cannot be a lucky path.

Arm A (the pairing controller) is NOT RUN here and is not reported as zero:
`replay_b` clears a maker queue from the trade tape, and this quote-only stream
has no trade tape. Saying so is the honest outcome; substituting a fill rule it
never had would be inventing its result.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
from decimal import Decimal as D
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..")))

from src.gemini_execution import inventory_prob as IP
from src.gemini_execution.inventory_engine import (
    BUY_DOWN, BUY_UP, CANCEL, MAKER, REPRICE, TAKER, WAIT,
    Book, Config, Inventory, Limits, RestingOrder, evaluate,
)
from src.gemini_execution.pmus_execution_model import CalibratedFillModel, pmus_config

ZERO, ONE = D(0), D(1)


def _frames(w):
    return [(t, D(a), D(b), D(c), D(d)) for t, a, b, c, d in w["bbo"]]


def run_window(w, cal, cfg, limits, rng, *, alpha=None, use_oracle=False,
               ledger=None):
    frames = _frames(w)
    close, yes_won = w["close"], w["yes_won"]
    inv = Inventory()
    order: Optional[RestingOrder] = None
    order_leg = None          # RestingOrder is frozen; the calibrated leg
                              # relation is tracked beside it.
    rebate_accrued = ZERO
    fills, decisions, waits, extends, reduces, cancels = [], 0, 0, 0, 0, 0
    unestimable = 0

    for i, (t, yb, ybq, nb, nbq) in enumerate(frames):
        if t >= close:
            break
        book = Book(yes_bid=yb, yes_bid_qty=ybq, no_bid=nb, no_bid_qty=nbq, ts=t)

        # ---- resolve a resting order by a MEASURED fill probability
        if order is not None:
            rested = t - order.t_live
            if rested > 0:
                p = cfg.venue.p_fill(leg=order_leg,
                                     rest_s=min(rested, cfg.maker_rest_s))
                if p is None:
                    order, order_leg = None, None     # past our evidence
                    unestimable += 1
                elif rng.random() < float(p) * (1.0 if rested >= cfg.maker_rest_s
                                               else rested / cfg.maker_rest_s):
                    wire = book.wire(order.side, order.own)
                    inv = inv.add(order.side, order.remaining, order.own,
                                  cfg.venue.maker_fee(order.remaining, wire))
                    rebate_accrued += cfg.venue.maker_rebate_accrual(
                        order.remaining, wire)
                    fills.append(dict(t=t, side=order.side, kind="MAKER",
                                      own=str(order.own),
                                      qty=str(order.remaining), frame=i))
                    order, order_leg = None, None
                elif rested >= cfg.order_ttl_s:
                    order, order_leg = None, None     # TTL cancel
                    cancels += 1

        tte = close - t
        feats = IP.features_at(book, [], t, close)
        feats.n_prints = 1          # this stream has no trade tape; flow is 0
        if use_oracle:
            prob = IP.oracle(yes_won)
        elif alpha is not None:
            prob = IP.partial_oracle(feats, yes_won, alpha)
        else:
            prob = IP.estimate(feats, cal)

        d = evaluate(market=w["market"], t=t, tte=tte, inv=inv, book=book,
                     prob=prob, resting=((order,) if order else ()),
                     limits=limits, cfg=cfg)
        decisions += 1
        if ledger is not None:
            row = d.as_row(); row["window"] = w["market"]; row["frame"] = i
            ledger.append(row)

        if d.action == WAIT:
            waits += 1
            continue
        if d.action in (CANCEL, REPRICE):
            cancels += 1
            order, order_leg = None, None
            continue

        c = d.chosen
        extends += 1 if c.extends else 0
        reduces += 0 if c.extends else 1
        if c.kind == TAKER:
            exe = min(c.qty, book.taker_depth(c.side))
            if exe < cfg.min_qty:
                continue
            wire = book.wire(c.side, c.own)
            inv = inv.add(c.side, exe, c.own, cfg.venue.taker_fee(exe, wire))
            fills.append(dict(t=t, side=c.side, kind="TAKER", own=str(c.own),
                              qty=str(exe), frame=i))
        else:
            order = RestingOrder(c.side, MAKER, c.own, c.qty, ZERO, t,
                                 book.maker_queue(c.side))
            order_leg = cfg.venue.leg_for(resid_before=c.resid_before,
                                          side=c.side)

    net = inv.settle(yes_won)
    return dict(market=w["market"], close=close, yes_won=yes_won,
                up=str(inv.up_qty), dn=str(inv.dn_qty), resid=str(inv.resid),
                matched=str(inv.matched), cost=str(inv.cost),
                fees=str(inv.fees_paid), rebate_accrued=str(rebate_accrued),
                net=str(net), worst_case=str(inv.worst_case_settlement()),
                traded=bool(inv.up_qty > 0 or inv.dn_qty > 0),
                stranded=bool(inv.resid != 0), nfills=len(fills), fills=fills,
                decisions=decisions, waits=waits, extends=extends,
                reduces=reduces, cancels=cancels, unestimable=unestimable)


def run_arm(ws, cal, cfg, limits, *, seeds=40, **kw):
    nets, dds, resids, trades, waits, decs, rebates, wcs, unest = \
        [], [], [], [], [], [], [], [], []
    for s in range(seeds):
        rng = random.Random(1000 + s)
        rs = [run_window(w, cal, cfg, limits, rng, **kw) for w in ws]
        tr = [r for r in rs if r["traded"]]
        nets.append(sum(D(r["net"]) for r in tr))
        run = peak = dd = D(0)
        for r in sorted(tr, key=lambda r: r["close"]):
            run += D(r["net"]); peak = max(peak, run); dd = max(dd, peak - run)
        dds.append(dd)
        resids.append(sum(abs(D(r["resid"])) for r in tr))
        trades.append(len(tr))
        waits.append(sum(r["waits"] for r in rs))
        decs.append(sum(r["decisions"] for r in rs))
        rebates.append(sum(D(r["rebate_accrued"]) for r in tr))
        wcs.append(min([D(r["worst_case"]) for r in tr], default=D(0)))
        unest.append(sum(r["unestimable"] for r in rs))
    f = [float(x) for x in nets]
    return dict(net_mean=statistics.mean(f),
                net_sd=(statistics.stdev(f) if len(f) > 1 else 0.0),
                net_worst=min(f), net_best=max(f),
                per_window=statistics.mean(f) / len(ws) if ws else 0.0,
                dd_mean=statistics.mean([float(x) for x in dds]),
                traded=statistics.mean(trades),
                wait_pct=100.0 * statistics.mean(waits) / max(1, statistics.mean(decs)),
                resid=statistics.mean([float(x) for x in resids]),
                rebate=statistics.mean([float(x) for x in rebates]),
                worst_case=min([float(x) for x in wcs]),
                unestimable=statistics.mean(unest), seeds=seeds)


def main():
    ap = argparse.ArgumentParser()
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--windows", default=os.path.join(here, "pmus_windows.json"))
    ap.add_argument("--train-frac", type=float, default=0.5)
    ap.add_argument("--seeds", type=int, default=40)
    ap.add_argument("--kappa", default="0.10")
    ap.add_argument("--max-sigma", default="0.08")
    ap.add_argument("--clip", default="5")
    ap.add_argument("--alpha-sweep", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    ws = json.load(open(a.windows))
    ws.sort(key=lambda w: w["close"])
    cut = int(len(ws) * a.train_frac)
    train, oos = ws[:cut], ws[cut:]

    from analysis.profit_search.replay_inventory import collect_training_samples
    cal = IP.fit(collect_training_samples(train), train_windows=len(train))
    model = CalibratedFillModel.load()
    cfg = pmus_config(kappa=D(a.kappa), max_sigma=D(a.max_sigma),
                      clip_qty=D(a.clip))
    limits = Limits()

    print(f"PMUS windows {len(ws)}  train {len(train)}  OOS {len(oos)}  "
          f"seeds {a.seeds}")
    print("fill model:", json.dumps(model.describe()))
    print("calibration:", cal.to_json().replace("\n", " "))
    print("\nARM A (pairing controller): NOT RUN -- replay_b clears its maker "
          "queue from a trade tape, and this quote-only stream has none. "
          "Reported as unavailable, not as zero.\n")

    rows = [("B inventory [OOS, PMUS fees+fills]", run_arm(oos, cal, cfg, limits, seeds=a.seeds)),
            ("C WAIT baseline", dict(net_mean=0.0, net_sd=0.0, net_worst=0.0,
                                     net_best=0.0, per_window=0.0, dd_mean=0.0,
                                     traded=0, wait_pct=100.0, resid=0.0,
                                     rebate=0.0, worst_case=0.0,
                                     unestimable=0.0, seeds=a.seeds)),
            ("O inventory + ORACLE p (bound)", run_arm(oos, cal, cfg, limits, seeds=a.seeds, use_oracle=True)),
            ("B inventory [train, in-sample]", run_arm(train, cal, cfg, limits, seeds=a.seeds))]

    hdr = (f"{'arm':38s} {'trd':>5s} {'net_mean':>9s} {'sd':>7s} {'worst':>8s} "
           f"{'per_win':>8s} {'maxDD':>7s} {'WAIT%':>6s} {'resid':>7s} {'rbt':>6s}")
    print(hdr); print("-" * len(hdr))
    for label, r in rows:
        print(f"{label:38s} {r['traded']:5.1f} {r['net_mean']:9.4f} "
              f"{r['net_sd']:7.4f} {r['net_worst']:8.4f} {r['per_window']:8.4f} "
              f"{r['dd_mean']:7.4f} {r['wait_pct']:6.1f} {r['resid']:7.1f} "
              f"{r['rebate']:6.3f}")

    if a.alpha_sweep:
        print("\npartial-oracle requirement sweep on PMUS "
              "(alpha = fraction of settlement uncertainty resolved)")
        print(f"{'alpha':>6s} {'trd':>5s} {'net_mean':>9s} {'sd':>7s} {'per_win':>8s}")
        for al in [float(x) for x in a.alpha_sweep.split(",")]:
            r = run_arm(oos, cal, pmus_config(kappa=D(a.kappa),
                                              max_sigma=D("0.60"),
                                              clip_qty=D(a.clip)),
                        limits, seeds=a.seeds, alpha=al)
            print(f"{al:6.3f} {r['traded']:5.1f} {r['net_mean']:9.4f} "
                  f"{r['net_sd']:7.4f} {r['per_window']:8.4f}")

    if a.out:
        json.dump(dict(fill_model=model.describe(),
                       calibration=json.loads(cal.to_json()),
                       windows=len(ws), oos=[w["market"] for w in oos],
                       arms={k: v for k, v in rows}), open(a.out, "w"), indent=1)
        print(f"\n-> {a.out}")


if __name__ == "__main__":
    main()
