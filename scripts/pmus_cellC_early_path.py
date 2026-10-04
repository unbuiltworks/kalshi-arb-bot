"""Early-path discriminator study on FROZEN Cell C. Read-only.

Cell C is not modified and no rule is produced. For each of its 300
episodes this reconstructs the state observable at fixed horizons after
entry -- 1 s, 2 s, 5 s, 10 s, 20 s, the tape's natural scales, chosen in
advance and never tuned -- and asks whether anything visible by then
separates the 107 economically productive episodes from the 193 sub-fee
ones.

NO FUTURE INFORMATION ENTERS A FEATURE. Every value comes from books and
trades with timestamp <= entry + H, and the label is attached only after the
feature vector is built.
"""
from __future__ import annotations
import bisect, collections, json, os, statistics, sys
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib.util                                            # noqa: E402
from analysis.l5.next_side.model import Logit, auc               # noqa: E402
from src import pmus_fees as FEES                                # noqa: E402

_s = importlib.util.spec_from_file_location(
    "dca", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "pmus_favorite_dca.py"))
DCA = importlib.util.module_from_spec(_s)
_s.loader.exec_module(DCA)

HORIZONS = (1, 2, 5, 10, 20)
ONE = D(1)


def own(side, bid, ask, which):
    """Prices in the episode's OWN side terms.

    `exit` is what we could sell at right now; `mid` is the side's mid.
    For UP they read off the book directly; for DOWN they are 1 - the UP
    quote, because the venue serves one long-side book.
    """
    if which == "exit":
        return bid if side == "UP" else ONE - ask
    m = (bid + ask) / 2
    return m if side == "UP" else ONE - m


def features(ep, books, trades, bt, H):
    te = ep["t_entry"]
    side = ep["side"]
    th = te + H
    j0 = bisect.bisect_left(bt, te) - 1
    j1 = bisect.bisect_left(bt, th) - 1
    if j0 < 0 or j1 < 0:
        return None
    b0, b1 = books[j0], books[j1]
    bid0, ask0 = D(b0["bid"]), D(b0["ask"])
    bid1, ask1 = D(b1["bid"]), D(b1["ask"])
    mid0 = own(side, bid0, ask0, "mid")
    mid1 = own(side, bid1, ask1, "mid")
    ex0 = own(side, bid0, ask0, "exit")
    # the path strictly inside (te, th]
    hi = lo = mid0
    new_highs = 0
    t_first_high = None
    for j in range(j0 + 1, j1 + 1):
        bb = books[j]
        m = own(side, D(bb["bid"]), D(bb["ask"]), "mid")
        if m > hi:
            hi = m
            new_highs += 1
            if t_first_high is None:
                t_first_high = bb["t"] - te
        lo = min(lo, m)
    fav_qty = adv_qty = D(0)
    n_fav = n_adv = 0
    ti0 = bisect.bisect_right([t["t"] for t in trades], te)
    for tr in trades[ti0:]:
        if tr["t"] > th:
            break
        q = D(tr["qty"])
        # a buyer lifting the ask pushes the UP price up: favourable to a
        # long UP, adverse to a long DOWN. `unknown` counts as neither.
        dr = tr.get("dir")
        if dr == "buy":
            (fav_qty, adv_qty) = ((fav_qty + q, adv_qty) if side == "UP"
                                  else (fav_qty, adv_qty + q))
            (n_fav, n_adv) = ((n_fav + 1, n_adv) if side == "UP"
                              else (n_fav, n_adv + 1))
        elif dr == "sell":
            (fav_qty, adv_qty) = ((fav_qty, adv_qty + q) if side == "UP"
                                  else (fav_qty + q, adv_qty))
            (n_fav, n_adv) = ((n_fav, n_adv + 1) if side == "UP"
                              else (n_fav + 1, n_adv))
    tot = fav_qty + adv_qty
    adds_by_h = sum(1 for t in ep["add_ts"] if t <= th)
    sh_by_h = sum(D(x) for t, x in zip(ep["add_ts"], ep["add_sh"]) if t <= th)
    cost_by_h = sum(D(p) * D(x) for t, p, x
                    in zip(ep["add_ts"], ep["add_px"], ep["add_sh"])
                    if t <= th)
    avg_by_h = (cost_by_h / sh_by_h) if sh_by_h > 0 else D(0)
    return {
        "dprice": float(mid1 - mid0),
        "mfe_to_date": float(hi - mid0),
        "mae_to_date": float(lo - mid0),
        "made_new_high": 1.0 if new_highs > 0 else 0.0,
        "n_new_highs": float(new_highs),
        "t_first_new_high": float(t_first_high if t_first_high is not None
                                  else H),
        "dbid": float(bid1 - bid0), "dask": float(ask1 - ask0),
        "spread": float(ask1 - bid1),
        "bid_qty": float(D(b1["bid_qty"])), "ask_qty": float(D(b1["ask_qty"])),
        "dbid_qty": float(D(b1["bid_qty"]) - D(b0["bid_qty"])),
        "dask_qty": float(D(b1["ask_qty"]) - D(b0["ask_qty"])),
        "fav_qty": float(fav_qty), "adv_qty": float(adv_qty),
        "trade_imbalance": float((fav_qty - adv_qty) / tot) if tot > 0 else 0.0,
        "n_prints": float(n_fav + n_adv),
        "print_qty": float(tot),
        "fav_price_now": float(mid1),
        "dist_from_50": float(abs(mid1 - D("0.5"))),
        "tte_s": float(ep["tte_s_at_entry"] - H),
        "adds_by_h": float(adds_by_h),
        "shares_by_h": float(sh_by_h),
        "unrealized_per_share": float(own(side, bid1, ask1, "exit") - avg_by_h),
        # the underscore fields are for the termination counterfactual, not
        # for the model; floats keep them JSON-serialisable.
        "_ex_now": float(own(side, bid1, ask1, "exit")),
        "_sh_now": float(sh_by_h), "_cost_now": float(cost_by_h),
        "_fee_now": float(sum(D(f) for t, f in zip(ep["add_ts"],
                                                   ep["add_fee"])
                              if t <= th)),
        "_ex0": float(ex0),
    }


NAMES = ["dprice", "mfe_to_date", "mae_to_date", "made_new_high",
         "n_new_highs", "t_first_new_high", "dbid", "dask", "spread",
         "dbid_qty", "dask_qty", "trade_imbalance", "n_prints", "print_qty",
         "fav_price_now", "dist_from_50", "tte_s", "adds_by_h",
         "unrealized_per_share"]


def main() -> int:
    by = DCA.load(DCA.TAPE)
    rows = []
    d = json.load(open("analysis/l5/favorite_dca/replay_summary.json"))
    for slug in sorted(d["windows"]):
        w = by[slug]
        books, trades = w["book"], w["trade"]
        bt = [b["t"] for b in books]
        for ep in d["windows"][slug]["CELL_C_reversal_only"]["episodes"]:
            rec = {"slug": slug,
                   "gross": float(ep["gross_pnl"]),
                   "fees": float(ep["total_fees"]),
                   "net": float(ep["net_pnl"]),
                   "holding_s": ep["holding_s"],
                   "shares": float(ep["shares"]),
                   "side": ep["side"], "t_entry": ep["t_entry"],
                   "exit_proceeds": float(ep["exit_proceeds_gross"]),
                   "f": {}}
            # LABEL ATTACHED ONLY AFTER THE FEATURES ARE BUILT
            for H in HORIZONS:
                f = features(ep, books, trades, bt, H)
                if f is not None:
                    rec["f"][H] = f
            rec["productive"] = 1 if rec["gross"] >= rec["fees"] else 0
            rows.append(rec)
    json.dump(rows, open("/tmp/claude-0/-home-user-kalshi-arb-bot/"
                         "671883cb-e673-5581-ae31-5d97cf169fd1/scratchpad/"
                         "cellC_paths.json", "w"))
    print(json.dumps({"episodes": len(rows),
                      "productive": sum(r["productive"] for r in rows),
                      "sub_fee": sum(1 - r["productive"] for r in rows),
                      "with_all_horizons": sum(
                          1 for r in rows if len(r["f"]) == len(HORIZONS))},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
