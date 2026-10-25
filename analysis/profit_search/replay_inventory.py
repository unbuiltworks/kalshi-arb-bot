"""Timestamp-causal replay of the INVENTORY controller against the pairing
controller, the WAIT baseline, and an oracle bound.

Reuses, and does not reimplement:
  * analysis/profit_search/replay_b.py  -- maker queue clearance by VOLUME only,
    the fee model, TTL cancellation, latency on crossing, and the window
    settlement accounting. Controller A is literally replay_b.run_window.
  * src/gemini_execution/inventory_engine.py  -- the decision rules.
  * src/gemini_execution/inventory_prob.py    -- the causal probability tilt.

Arms:
  A  pairing controller (existing engine; policy D = L5 opening band + bounded
     defensive completion, the best-performing configuration previously found)
  B  inventory controller (this work)
  C  WAIT / no-trade baseline -- exactly 0.00 by construction, stated not run
  D  L5's observed action sequence on the same 15m BTC windows (behavioural
     comparison only; L5 trades a different venue, so his prices are HIS, and
     no claim is made that his economics transfer)
  O  oracle-probability bound on arm B -- an upper bound, never a return

The split is chronological by window close: the earlier `--train-frac` of
windows fit the probability tilt, the remainder is untouched out-of-sample.
Nothing in the OOS half is consulted before its own decision timestamp, and
settlement labels are read only after the final frame of a window.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal as D
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..")))

import replay_b                                            # noqa: E402
from src.gemini_execution import inventory_prob as IP       # noqa: E402
from src.gemini_execution.inventory_engine import (         # noqa: E402
    BUY_DOWN, BUY_UP, CANCEL, MAKER, REPRICE, TAKER, WAIT,
    Book, Config, Inventory, Limits, RestingOrder, evaluate,
    fee_dollars, MAKER_THETA, TAKER_THETA, PER_ORDER,
)

ZERO = D(0)
ONE = D(1)


def _frames(w):
    return [(t, D(a), D(b), D(c), D(d)) for t, a, b, c, d in w["bbo"]]


def _prints(w):
    return [(t, D(p), D(q), sd) for t, p, q, sd in w["prints"]]


def _consumes(side, own, px, taker_side):
    """Identical rule to replay_b.consumes: our resting order is consumed only
    by volume on the opposite taker side at a price that reaches us."""
    wire = own if side == "UP" else (ONE - own)
    if side == "UP":
        return taker_side == "no" and px <= wire
    return taker_side == "yes" and px >= wire


# ------------------------------------------------------------------ arm B
def run_inventory_window(w, cal: IP.Calibration, cfg: Config, limits: Limits,
                         *, lam: float = 1.0, use_oracle: bool = False,
                         alpha: Optional[float] = None,
                         ledger: Optional[list] = None,
                         flow_lookback: float = 60.0):
    """One window under the inventory controller. Returns the same record shape
    replay_b.run_window returns, so summarise() works unchanged."""
    frames = _frames(w)
    prints = _prints(w)
    close = w["close"]
    yes_won = w["yes_won"]

    inv = Inventory()
    order: Optional[RestingOrder] = None
    order_cleared = ZERO
    pi = 0
    fills = []
    decisions = 0
    waits = 0
    cancels = 0
    extends = 0
    reduces = 0

    for i, (t, yb, ybq, nb, nbq) in enumerate(frames):
        if t >= close:
            break
        book = Book(yes_bid=yb, yes_bid_qty=ybq, no_bid=nb, no_bid_qty=nbq, ts=t)

        # ---- advance a live maker order's queue with volume that consumes it
        if order is not None:
            while pi < len(prints) and prints[pi][0] <= t:
                pt, px, q, sd = prints[pi]
                if pt >= order.t_live and _consumes(order.side, order.own, px, sd):
                    order_cleared += q
                pi += 1
            thresh = order.queue + order.remaining      # CONSERVATIVE: full clip
            if order_cleared >= thresh:
                newfill = min(order.qty, order_cleared - order.queue) - order.filled
                newfill = max(ZERO, newfill)
                if newfill > 0:
                    wire = book.wire(order.side, order.own)
                    f = fee_dollars(MAKER_THETA, newfill, wire)
                    f += PER_ORDER if order.filled == 0 else ZERO
                    inv = inv.add(order.side, newfill, order.own, f)
                    fills.append(dict(t=t, side=order.side, kind="MAKER",
                                      own=str(order.own), qty=str(newfill), frame=i))
                    order = RestingOrder(order.side, order.kind, order.own,
                                         order.qty, order.filled + newfill,
                                         order.t_live, order.queue)
                    if order.remaining <= 0:
                        order, order_cleared = None, ZERO
        while pi < len(prints) and prints[pi][0] <= t:
            pi += 1

        tte = close - t
        feats = IP.features_at(book, prints, t, close, lookback=flow_lookback)
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
            row = d.as_row()
            row["window"] = w["market"]
            row["series"] = w["series"]
            row["frame"] = i
            ledger.append(row)

        if d.action == WAIT:
            waits += 1
            continue
        if d.action in (CANCEL, REPRICE):
            cancels += 1
            order, order_cleared = None, ZERO
            continue

        c = d.chosen
        if c.extends:
            extends += 1
        else:
            reduces += 1

        if c.kind == TAKER:
            # latency: cross against the first frame at or after t + lam, and
            # take the depth that frame actually shows, not the one we saw.
            j = i
            while j < len(frames) and frames[j][0] < t + lam:
                j += 1
            if j >= len(frames):
                continue
            _, yb2, ybq2, nb2, nbq2 = frames[j]
            b2 = Book(yes_bid=yb2, yes_bid_qty=ybq2, no_bid=nb2, no_bid_qty=nbq2)
            own2 = b2.taker_own(c.side)
            depth = b2.taker_depth(c.side)
            exe = min(c.qty, depth)
            if exe < cfg.min_qty:
                continue
            f = fee_dollars(TAKER_THETA, exe, b2.wire(c.side, own2)) + PER_ORDER
            inv = inv.add(c.side, exe, own2, f)
            fills.append(dict(t=frames[j][0], side=c.side, kind="TAKER",
                              own=str(own2), qty=str(exe), frame=j,
                              slip=str(own2 - (c.own or own2))))
        else:
            order = RestingOrder(c.side, MAKER, c.own, c.qty, ZERO,
                                 t + lam, book.maker_queue(c.side))
            order_cleared = ZERO

    # ---- adverse selection: did the mid move against the side we were filled on
    adv = 0
    for f in fills:
        m0 = replay_b.mid_at(frames, f["t"])
        m1 = replay_b.mid_at(frames, f["t"] + 60.0)
        if m0 is None or m1 is None:
            continue
        if (f["side"] == "UP" and m1 < m0) or (f["side"] == "DOWN" and m1 > m0):
            adv += 1

    net = inv.settle(yes_won)
    return dict(market=w["market"], series=w["series"], close=close,
                up=str(inv.up_qty), dn=str(inv.dn_qty),
                matched=str(inv.matched), resid=str(inv.resid),
                cost=str(inv.cost), fees=str(inv.fees_paid), rebate="0",
                redeem=str(net + inv.cost + inv.fees_paid),
                net=str(net), net_pre_fee=str(net + inv.fees_paid),
                worst_case=str(inv.worst_case_settlement()),
                stranded=bool(inv.resid != 0), traded=bool(inv.up_qty > 0 or inv.dn_qty > 0),
                fills=fills, efeat=None, adverse=adv, nfills=len(fills),
                decisions=decisions, waits=waits, cancels=cancels,
                extends=extends, reduces=reduces)


# --------------------------------------------------------------- calibration
def collect_training_samples(windows, *, per_window: int = 8,
                             flow_lookback: float = 60.0):
    """One sample per sampled frame: features at t, label = this window's
    settlement. Only ever called on the training split."""
    out = []
    for w in windows:
        frames = _frames(w)
        prints = _prints(w)
        close = w["close"]
        if not frames:
            continue
        step = max(1, len(frames) // per_window)
        for i in range(0, len(frames), step):
            t, yb, ybq, nb, nbq = frames[i]
            if t >= close:
                break
            book = Book(yes_bid=yb, yes_bid_qty=ybq, no_bid=nb, no_bid_qty=nbq, ts=t)
            if book.spread < 0 or yb <= 0 or nb <= 0:
                continue
            f = IP.features_at(book, prints, t, close, lookback=flow_lookback)
            if f.n_prints < 1:
                continue
            out.append((f, w["yes_won"]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "windows.json"))
    ap.add_argument("--train-frac", type=float, default=0.5)
    ap.add_argument("--series", default="")
    ap.add_argument("--kappa", default="0.10")
    ap.add_argument("--min-edge-extend", default="0.06")
    ap.add_argument("--clip", default="5")
    ap.add_argument("--maker-only", action="store_true")
    ap.add_argument("--max-sigma", default="0.08",
                    help="probability-trust ceiling. Raising it past the fitted "
                         "residual sd FORCES the controller to trade a signal "
                         "it has no evidence for; the sweep reports what that "
                         "costs rather than leaving it to be assumed.")
    ap.add_argument("--alpha-sweep", default="",
                    help="comma-separated alphas for the partial-oracle "
                         "requirement sweep (how much settlement uncertainty a "
                         "forecaster must resolve to break even)")
    ap.add_argument("--resid-cap-sweep", default="",
                    help="pre-registered residual-cap grid; every cell reported")
    ap.add_argument("--ledger-out", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    ws = json.load(open(args.windows))
    if args.series:
        keep = set(args.series.split(","))
        ws = [w for w in ws if w["series"] in keep]
    ws.sort(key=lambda w: w["close"])
    cut = int(len(ws) * args.train_frac)
    train, oos = ws[:cut], ws[cut:]

    # ---- fit the tilt on the training split ONLY
    samples = collect_training_samples(train)
    cal = IP.fit(samples, train_until=(train[-1]["close"] if train else 0.0),
                 train_windows=len(train))

    cfg = Config(kappa=D(args.kappa), min_edge_extend=D(args.min_edge_extend),
                 clip_qty=D(args.clip), allow_taker=not args.maker_only,
                 max_sigma=D(args.max_sigma))
    limits = Limits()

    ledger: Optional[list] = [] if args.ledger_out else None

    # ---- arm A: the existing pairing controller, unchanged
    acfg = replay_b.Cfg(defensive_ceiling=D("1.20"), notional=D("0.80"))
    A = [replay_b.run_window(w, "D", acfg) for w in oos]
    # ---- arm B: the inventory controller
    B = [run_inventory_window(w, cal, cfg, limits, ledger=ledger) for w in oos]
    # ---- arm O: oracle-probability bound on the same controller
    O = [run_inventory_window(w, cal, cfg, limits, use_oracle=True) for w in oos]
    # ---- arm B on the training split, for a train-vs-OOS honesty check
    Btr = [run_inventory_window(w, cal, cfg, limits) for w in train]

    rows = [
        replay_b.summarise(A, "A pairing (policy D + defensive 1.20)"),
        replay_b.summarise(B, "B inventory controller  [OOS]"),
        dict(label="C WAIT / no-trade baseline", eligible=len(oos), traded=0,
             net=D(0), per_window=D(0), cost=D(0), roc=D(0), matched=D(0),
             stranded_units=D(0), stranded_windows=0, clean=0, max_dd=D(0),
             fees=D(0), rebate=D(0), takerfills=0, makerfills=0, defensive=0,
             fills=0, adverse=0, adv_rate=D(0)),
        replay_b.summarise(O, "O inventory + ORACLE p (bound, not a return)"),
        replay_b.summarise(Btr, "B inventory controller  [train, in-sample]"),
    ]

    print(f"windows total {len(ws)}  train {len(train)}  OOS {len(oos)}")
    print(f"train cut at close={cal.train_until}  series={args.series or 'ALL'}")
    print("calibration:", cal.to_json().replace("\n", " "))
    print()
    hdr = f"{'arm':44s} {'elig':>5s} {'trd':>4s} {'net':>10s} {'per_win':>9s} " \
          f"{'cost':>9s} {'maxDD':>8s} {'fees':>8s} {'mkr':>4s} {'tkr':>4s} " \
          f"{'resid':>8s} {'adv':>6s}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['label']:44s} {r['eligible']:5d} {r['traded']:4d} "
              f"{float(r['net']):10.4f} {float(r['per_window']):9.4f} "
              f"{float(r['cost']):9.3f} {float(r['max_dd']):8.4f} "
              f"{float(r['fees']):8.3f} {r['makerfills']:4d} {r['takerfills']:4d} "
              f"{float(r['stranded_units']):8.2f} {float(r['adv_rate']):6.3f}")

    # decision mix on the OOS inventory arm
    dec = sum(r["decisions"] for r in B)
    wait = sum(r["waits"] for r in B)
    print(f"\nOOS inventory decisions: {dec}  WAIT {wait} "
          f"({(100.0*wait/dec if dec else 0):.1f}%)  "
          f"extend {sum(r['extends'] for r in B)}  "
          f"reduce {sum(r['reduces'] for r in B)}  "
          f"cancel/reprice {sum(r['cancels'] for r in B)}")
    if ledger is not None:
        import collections as _c
        cen = _c.Counter(r["reason"] for r in ledger)
        print("WAIT/action reason census (OOS):",
              ", ".join(f"{k}={v}" for k, v in cen.most_common(8)))
    resid_ws = [r for r in B if r["stranded"]]
    print(f"OOS windows ending with residual exposure: {len(resid_ws)} / {len(B)}")
    if B:
        wc = [D(r["worst_case"]) for r in B if r["traded"]]
        if wc:
            print(f"worst-case settlement loss per traded window: "
                  f"min {float(min(wc)):.4f}  mean {float(sum(wc)/len(wc)):.4f}")

    if args.alpha_sweep:
        print("\npartial-oracle requirement sweep (alpha = fraction of settlement "
              "uncertainty resolved; NOT an achieved return)")
        print(f"{'alpha':>6s} {'trd':>4s} {'net':>10s} {'per_win':>9s} "
              f"{'maxDD':>8s} {'resid':>8s}")
        for a in [float(x) for x in args.alpha_sweep.split(",")]:
            R = [run_inventory_window(w, cal, cfg, limits, alpha=a) for w in oos]
            s2 = replay_b.summarise(R, f"alpha={a}")
            print(f"{a:6.3f} {s2['traded']:4d} {float(s2['net']):10.4f} "
                  f"{float(s2['per_window']):9.4f} {float(s2['max_dd']):8.4f} "
                  f"{float(s2['stranded_units']):8.2f}")

    if args.resid_cap_sweep:
        print("\npre-registered residual-cap sweep (grid fixed before this ran)")
        print(f"{'cap':>5s} {'trd':>5s} {'net':>10s} {'per_win':>9s} "
              f"{'maxDD':>8s} {'resid':>8s}")
        for c in [D(x) for x in args.resid_cap_sweep.split(",")]:
            R = [run_inventory_window(w, cal, cfg, Limits(max_resid_qty=c))
                 for w in oos]
            s2 = replay_b.summarise(R, f"cap={c}")
            print(f"{float(c):5.0f} {s2['traded']:5d} {float(s2['net']):10.4f} "
                  f"{float(s2['per_window']):9.4f} {float(s2['max_dd']):8.4f} "
                  f"{float(s2['stranded_units']):8.2f}")

    if args.ledger_out:
        with open(args.ledger_out, "w") as fh:
            for row in ledger or []:
                fh.write(json.dumps(row, default=str) + "\n")
        print(f"\ndecision ledger -> {args.ledger_out} ({len(ledger or [])} rows)")
    if args.out:
        json.dump(dict(calibration=json.loads(cal.to_json()),
                       arms={r["label"]: {k: str(v) for k, v in r.items()}
                             for r in rows},
                       oos_windows=[r["market"] for r in oos]),
                  open(args.out, "w"), indent=1)
        print(f"summary -> {args.out}")


if __name__ == "__main__":
    main()
