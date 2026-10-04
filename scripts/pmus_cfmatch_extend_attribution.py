"""Read-only attribution of every frozen CF_MATCH purchase. No controller.

Every purchased share is classified at the END of the accounting chain:

  MATCHED                     consumed into matched YES/NO inventory
  SETTLEMENT-WINNING RESIDUAL left directional, paid $1
  SETTLEMENT-LOSING RESIDUAL  left directional, paid $0

Two lot conventions are carried side by side, because the classification is
a convention and the economics are not:

  FIFO      the earliest shares on each side are the matched ones
  PRO-RATA  every share on a side is matched in proportion matched/side

The portfolio economics are identical under both -- only the attribution to
individual purchases moves. That distinction is reported, not hidden.

Features are recorded from information available AT PURCHASE only.
"""
from __future__ import annotations
import bisect, collections, json, os, statistics, sys
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib.util                                           # noqa: E402
from analysis.l5.next_side.model import Logit, auc              # noqa: E402

_s = importlib.util.spec_from_file_location(
    "dca", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "pmus_favorite_dca.py"))
DCA = importlib.util.module_from_spec(_s)
_s.loader.exec_module(DCA)
ONE = D(1)


def own(side, bid, ask, which="mid"):
    m = (bid + ask) / 2
    if which == "mid":
        return m if side == "UP" else ONE - m
    return bid if side == "UP" else ONE - ask


def build():
    by = DCA.load(DCA.TAPE)
    d = json.load(open("analysis/l5/favorite_dca/replay_summary.json"))
    rows = []
    for slug in sorted(d["windows"]):
        w = by[slug]
        books, trades = w["book"], w["trade"]
        bt = [b["t"] for b in books]
        tt = [t["t"] for t in trades]
        cf = d["windows"][slug]["CF_MATCH"]
        oc = cf["settlement_outcome"]
        tog = sorted(e["t"] for e in
                     d["windows"][slug]["CELL_C_reversal_only"]["exit_log"])
        start = DCA.window_start(slug)
        up = dn = D(0)
        up_cash = dn_cash = D(0)
        run_side, run_n = None, 0
        buys = []
        for a in cf["allocation_log"]:
            t, side = a["t"], a["side"]
            px, sh, fee = D(a["px"]), D(a["sh"]), D(a["fee"])
            j = bisect.bisect_left(bt, t) - 1
            b = books[max(0, j)]
            bid, ask = D(b["bid"]), D(b["ask"])
            # recent movement of THIS side's mid over the prior 10 s
            j10 = bisect.bisect_left(bt, t - 10) - 1
            mv = (own(side, bid, ask) - own(side, D(books[max(0, j10)]["bid"]),
                                            D(books[max(0, j10)]["ask"]))
                  if j10 >= 0 else D(0))
            prior = [x for x in tog if x < t]
            resid = up - dn
            matched_now = min(up, dn)
            cu = up_cash / up if up > 0 else D(0)
            cd = dn_cash / dn if dn > 0 else D(0)
            locked = matched_now * (ONE - (cu + cd)) if matched_now > 0 else D(0)
            run_n = run_n + 1 if side == run_side else 1
            run_side = side
            # flow in the prior 5 s
            i0 = bisect.bisect_right(tt, t - 5)
            i1 = bisect.bisect_right(tt, t)
            fav_q = adv_q = D(0)
            for tr in trades[i0:i1]:
                q = D(tr["qty"]); dr = tr.get("dir")
                if dr == "buy":
                    (fav_q, adv_q) = ((fav_q + q, adv_q) if side == "UP"
                                      else (fav_q, adv_q + q))
                elif dr == "sell":
                    (fav_q, adv_q) = ((fav_q, adv_q + q) if side == "UP"
                                      else (fav_q + q, adv_q))
            tot = fav_q + adv_q
            buys.append({
                "slug": slug, "t": t, "side": side, "px": float(px),
                "shares": float(sh), "cost": float(sh * px),
                "fee": float(fee),
                "won": 1 if side == oc else 0,
                "f": {
                    "px": float(px),
                    "fav_px": float(max(own("UP", bid, ask),
                                        own("DOWN", bid, ask))),
                    "is_favorite": 1.0 if own(side, bid, ask) > D("0.5") else 0.0,
                    "move_10s": float(mv),
                    "tte_s": float(start + 900 - t),
                    "resid_shares_before": float(abs(resid)),
                    "resid_dollars_before": float(
                        abs(resid) * (cu if resid > 0 else cd)),
                    "resid_same_side": (1.0 if (resid > 0 and side == "UP")
                                        or (resid < 0 and side == "DOWN")
                                        else 0.0),
                    "matched_before": float(matched_now),
                    "locked_matched_profit": float(locked),
                    "imbalance": float(resid),
                    "run_len": float(run_n),
                    "s_since_reversal": float(t - prior[-1]) if prior else 900.0,
                    "n_reversals_so_far": float(len(prior)),
                    "spread": float(ask - bid),
                    "flow_imbalance_5s": float((fav_q - adv_q) / tot)
                    if tot > 0 else 0.0,
                    "prints_5s": float(i1 - i0),
                },
            })
            if side == "UP":
                up += sh; up_cash += sh * px
            else:
                dn += sh; dn_cash += sh * px
        # ---- end-of-chain classification, both conventions
        M = min(up, dn)
        for conv in ("fifo", "prorata"):
            rem = {"UP": M, "DOWN": M}
            for b in buys:
                sh = D(str(b["shares"]))
                if conv == "fifo":
                    m = min(sh, rem[b["side"]])
                    rem[b["side"]] -= m
                else:
                    tot_side = up if b["side"] == "UP" else dn
                    m = sh * (M / tot_side) if tot_side > 0 else D(0)
                r = sh - m
                b["matched_sh_" + conv] = float(m)
                b["resid_sh_" + conv] = float(r)
        for b in buys:
            b["forward_move_10s"] = None
        rows.extend(buys)
    return rows


def main() -> int:
    rows = build()
    json.dump(rows, open("/tmp/claude-0/-home-user-kalshi-arb-bot/"
                         "671883cb-e673-5581-ae31-5d97cf169fd1/scratchpad/"
                         "cf_extends.json", "w"))
    print(json.dumps({"purchases": len(rows),
                      "shares": round(sum(r["shares"] for r in rows), 1),
                      "cost": round(sum(r["cost"] for r in rows), 2)},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
