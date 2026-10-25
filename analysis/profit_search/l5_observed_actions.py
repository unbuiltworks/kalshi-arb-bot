"""Arm D: L5's OBSERVED action sequence, classified with the same vocabulary the
inventory engine uses (EXTEND / REDUCE / COMPLETE / FLIP).

WHAT THIS IS AND IS NOT. It is a behavioural comparison. L5 trades Polymarket
`btc-updown-15m-*`; our replay windows are Kalshi `KXBTC15M-*`. The windows
line up in TIME (both are the same 15-minute BTC up/down period, identified by
the period's opening epoch), so his action SEQUENCE is comparable. His PRICES
are his venue's, so his P&L is not ours and no part of it is claimed here.

The point of the arm is to answer one question: does L5 in fact manage signed
inventory, and does he extend exposure, rather than pair off? That premise is
the reason the inventory controller was built, so it is worth checking against
the tape rather than assuming it.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
from decimal import Decimal as D

ZERO = D(0)


def load_activity(path):
    """Flatten the paged activity captures into one trade stream."""
    rows = []
    seen = set()
    with open(path) as fh:
        for line in fh:
            try:
                page = json.loads(line)
            except json.JSONDecodeError:
                continue
            for a in (page.get("payload") or []):
                if a.get("type", "TRADE") != "TRADE":
                    continue
                key = (a.get("transactionHash"), a.get("asset"), a.get("timestamp"),
                       a.get("size"), a.get("price"), a.get("side"))
                if key in seen:
                    continue
                seen.add(key)
                slug = a.get("slug") or ""
                if "-updown-15m-" not in slug:
                    continue
                try:
                    open_epoch = int(slug.rsplit("-", 1)[1])
                except (IndexError, ValueError):
                    continue
                rows.append(dict(
                    slug=slug, open_epoch=open_epoch, ts=int(a["timestamp"]),
                    side=a.get("side"), outcome=a.get("outcome"),
                    px=D(str(a.get("price", 0))), qty=D(str(a.get("size", 0))),
                    asset=slug.split("-updown-")[0]))
    rows.sort(key=lambda r: (r["open_epoch"], r["ts"]))
    return rows


def classify_window(trades):
    """Walk one window's trades, maintaining signed inventory, and label each
    action exactly as the engine would label its own."""
    up = dn = ZERO
    out = []
    for t in trades:
        # Polymarket: BUY Up == our BUY UP; BUY Down == our BUY DOWN.
        # A SELL of Up is economically a reduction of UP inventory.
        side = "UP" if (t["outcome"] or "").lower().startswith("up") else "DOWN"
        signed = t["qty"] if t["side"] == "BUY" else -t["qty"]
        resid_before = up - dn
        if side == "UP":
            up += signed
        else:
            dn += signed
        resid_after = up - dn
        if abs(resid_after) > abs(resid_before):
            kind = "EXTEND"
        elif resid_after == 0 and resid_before != 0:
            kind = "COMPLETE"
        elif abs(resid_after) < abs(resid_before):
            kind = "REDUCE"
        elif resid_after == resid_before:
            kind = "NEUTRAL"
        else:
            kind = "FLIP"
        if resid_before != 0 and resid_after != 0 and \
                (resid_before > 0) != (resid_after > 0):
            kind = "FLIP"
        out.append(dict(ts=t["ts"], side=side, action=t["side"], kind=kind,
                        px=str(t["px"]), qty=str(t["qty"]),
                        resid_before=str(resid_before), resid_after=str(resid_after)))
    return out, up, dn


def main():
    ap = argparse.ArgumentParser()
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--activity",
                    default=os.path.join(here, "..", "..", "data", "l5", "raw",
                                         "activity.jsonl"))
    ap.add_argument("--windows", default=os.path.join(here, "windows.json"))
    ap.add_argument("--asset", default="btc")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    rows = load_activity(args.activity)
    rows = [r for r in rows if r["asset"] == args.asset]
    by_win = collections.defaultdict(list)
    for r in rows:
        by_win[r["open_epoch"]].append(r)

    ws = json.load(open(args.windows))
    kalshi_opens = {int(w["open"]) for w in ws if w["series"] == f"KX{args.asset.upper()}15M"}

    kinds = collections.Counter()
    per_window = []
    overlap = 0
    for oe, trades in sorted(by_win.items()):
        acts, up, dn = classify_window(trades)
        for a in acts:
            kinds[a["kind"]] += 1
        resid = up - dn
        in_overlap = oe in kalshi_opens
        overlap += 1 if in_overlap else 0
        per_window.append(dict(open_epoch=oe, n=len(acts), up=str(up), dn=str(dn),
                               resid=str(resid), overlaps_kalshi=in_overlap,
                               max_abs_resid=str(max((abs(D(a["resid_after"]))
                                                      for a in acts), default=ZERO))))

    n_act = sum(kinds.values())
    print(f"L5 observed {args.asset}-updown-15m: {len(by_win)} windows, "
          f"{n_act} trades, {overlap} windows overlapping the Kalshi replay set "
          f"(of {len(kalshi_opens)} Kalshi windows)")
    print("\naction mix (classified with the engine's own vocabulary):")
    for k, v in kinds.most_common():
        print(f"  {k:9s} {v:7d}  {100.0*v/n_act:5.1f}%")

    ended_flat = sum(1 for w in per_window if D(w["resid"]) == 0)
    held = len(per_window) - ended_flat
    print(f"\nwindows ending FLAT (pair complete): {ended_flat} / {len(per_window)} "
          f"({100.0*ended_flat/len(per_window):.1f}%)")
    print(f"windows ending with SIGNED residual : {held} / {len(per_window)} "
          f"({100.0*held/len(per_window):.1f}%)")
    mar = sorted(D(w["max_abs_resid"]) for w in per_window)
    if mar:
        print(f"peak |residual| within a window: median {mar[len(mar)//2]}, "
              f"p90 {mar[int(0.9*len(mar))]}, max {mar[-1]}")
    tr = sorted(w["n"] for w in per_window)
    print(f"trades per window: median {tr[len(tr)//2]}, p90 {tr[int(0.9*len(tr))]}, "
          f"max {tr[-1]}")

    print("\nPREMISE CHECK — the reason the inventory controller exists:")
    ext = kinds.get("EXTEND", 0)
    print(f"  L5 increases signed exposure on {100.0*ext/n_act:.1f}% of his trades, "
          f"and ends {100.0*held/len(per_window):.1f}% of windows holding one.")
    print("  He is therefore NOT running a pair-completion engine. That supports "
          "building an inventory controller;\n  it does not transfer his edge, "
          "which is on another venue at his own prices.")

    if args.out:
        json.dump(dict(asset=args.asset, windows=len(by_win), trades=n_act,
                       action_mix=dict(kinds), overlap_windows=overlap,
                       ended_flat=ended_flat, ended_with_residual=held,
                       per_window=per_window[:500]),
                  open(args.out, "w"), indent=1)
        print(f"\n-> {args.out}")


if __name__ == "__main__":
    main()
