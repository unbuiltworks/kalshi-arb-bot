#!/usr/bin/env python3
"""L5 STATE -> NEXT ACTION decision tape, from the canonical sequence.

Read-only. Builds one decision row per fill: the state AFTER that fill, and the
class of the NEXT action in the same window. Settlement is used for scoring only
and never enters a state feature.

DEV/OOS boundary is the registered 2026-08-29 (RUN_LENGTH_PREREGISTRATION.md).
"""
from __future__ import annotations
import csv, gzip, json, math, collections, statistics as st

SRC = "analysis/l5/l5_canonical_sequence.csv.gz"
OUT = "analysis/l5/next_action/decisions.jsonl.gz"
BOUNDARY = "2026-08-29"


def isup(r):
    return r["outcome"].lower().startswith("up") or r["side"].lower() in ("up", "yes")


def main():
    R = list(csv.DictReader(gzip.open(SRC, "rt")))
    W = collections.defaultdict(list)
    for r in R:
        r["trade_seq"] = int(r["trade_seq"])
        W[r["window_id"]].append(r)
    for v in W.values():
        v.sort(key=lambda r: r["trade_seq"])

    out = gzip.open(OUT, "wt")
    n = 0
    for wid, rows in W.items():
        up = dn = 0.0
        upsp = dnsp = 0.0
        prev_t = None
        prev_px_side = {}          # last traded price per side
        own_sum = collections.defaultdict(float); own_n = collections.defaultdict(int)
        run = 0
        for i, r in enumerate(rows):
            u = isup(r)
            q = float(r["quantity"]); p = float(r["price"])
            t = float(r["timestamp"])
            res_before = up - dn

            # ---- classify THIS action relative to the state before it
            if res_before > 0:
                act = "EXTEND" if u else "COMPLETE"
            elif res_before < 0:
                act = "EXTEND" if not u else "COMPLETE"
            else:
                act = "OPEN"
            run = run + 1 if act == "EXTEND" else 0

            # ---- apply the fill
            if u:
                up += q; upsp += q * p
            else:
                dn += q; dnsp += q * p
            own_sum[u] += q * p; own_n[u] += q

            # ---- state AFTER this fill = the decision state for the NEXT action
            res = up - dn
            gross = up + dn
            matched = min(up, dn)
            pair_cost = None
            if matched > 0:
                # average cost of the matched portion, both legs
                pair_cost = (upsp / up if up else 0) + (dnsp / dn if dn else 0)
            held_is_up = res > 0
            held_px = prev_px_side.get(held_is_up)
            opp_px = prev_px_side.get(not held_is_up)
            prev_px_side[u] = p

            nxt = rows[i + 1] if i + 1 < len(rows) else None
            if nxt is None:
                nact, nq, npx, ndelay = "STOP", None, None, None
            else:
                nu = isup(nxt)
                if res > 0:
                    nact = "EXTEND" if nu else "COMPLETE"
                elif res < 0:
                    nact = "EXTEND" if not nu else "COMPLETE"
                else:
                    nact = "FLAT_OPEN"
                nq = float(nxt["quantity"]); npx = float(nxt["price"])
                ndelay = float(nxt["timestamp"]) - t

            own_mean = (own_sum[u] / own_n[u]) if own_n[u] else None
            row = {
                "window_id": wid, "slug": r["market_slug"], "asset": r["asset"],
                "dur": int(r["duration_minutes"]), "day": r["datetime_utc"][:10],
                "dev": r["datetime_utc"][:10] < BOUNDARY,
                "fill_idx": i, "is_first_fill": i == 0,
                "this_action": act, "this_run": run,
                "this_side_up": u, "this_px": p, "this_qty": q,
                "maker_taker": r["maker_taker"],
                "px_vs_own_side_mean": None if own_mean is None else p - own_mean,
                # ---- state after
                "up_qty": up, "dn_qty": dn, "gross": gross,
                "matched_qty": matched, "residual_signed": res,
                "abs_residual": abs(res),
                "imbalance": abs(res) / gross if gross else 0.0,
                "pair_cost": pair_cost,
                "held_side_up": held_is_up, "held_px": held_px, "opp_px": opp_px,
                "dt_since_prev": None if prev_t is None else t - prev_t,
                "secs_from_open": float(r["seconds_from_window_start"]),
                "secs_to_settle": float(r["seconds_to_settlement"]),
                # ---- target
                "next_action": nact, "next_qty": nq, "next_px": npx,
                "next_delay": ndelay,
                # ---- scoring only, never a feature
                "settlement_up": r["settlement_outcome"].lower().startswith("up"),
            }
            # price movement of the side just traded, vs its own previous print
            pv = None
            for j in range(i - 1, -1, -1):
                if isup(rows[j]) == u:
                    pv = p - float(rows[j]["price"]); break
            row["px_move_same_side"] = pv
            row["px_move_any"] = None if i == 0 else p - float(rows[i - 1]["price"])
            out.write(json.dumps(row) + "\n")
            n += 1
            prev_t = t
    out.close()
    print(f"wrote {OUT}: {n:,} decision rows over {len(W):,} windows")


if __name__ == "__main__":
    main()
