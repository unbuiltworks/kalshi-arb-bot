"""Calibrate the scaled-down RN1 replica from RN1's own public fills.

READ-ONLY arithmetic on saved public files. HISTORICAL: another trader on
another venue (Polymarket international); nothing here is our P&L.

usage: rn1_replica_calibration.py <rn1_activity.jsonl.gz> <rn1_taker_trades.jsonl.gz> [out.json]

Inputs are the files committed on origin/claude/runner-executor-verification-utzgrh
under analysis/l5/sports_wallets/ (rn1_activity_20261022_20261121.jsonl.gz,
rn1_taker_trades_20261022_20261121.jsonl.gz).

What the public rows show (OBSERVABLE): every BUY fill with timestamp, token,
price, shares, and (via the taker-trade join) whether the fill was taker.
What they do not show (UNKNOWN): resting quote size and price relative to the
book, cancels, the fill rate of resting orders. A maker fill's size is set by
the incoming taker, so fill size is not quote size.

Fills of the same token within MERGE_S seconds are merged into one "event"
(one resting order hit in pieces, or one decision). Every statistic below is
per event unless it says otherwise.
"""
from __future__ import annotations

import collections
import gzip
import json
import sys

MERGE_S = 10
PAIR_CAPS = (0.90, 0.94, 0.96, 0.97, 0.98, 1.00)


def pct(xs, p):
    xs = sorted(xs)
    return xs[int(p * (len(xs) - 1))] if xs else None


def q(xs, ps=(0.1, 0.25, 0.5, 0.75, 0.9)):
    return {f"p{int(p * 100)}": (round(pct(xs, p), 4) if xs else None)
            for p in ps} | {"n": len(xs)}


def sport_of(slug: str) -> str:
    return (slug or "?").split("-")[0]


def mtype(slug: str) -> str:
    s = slug or ""
    if "-total-" in s or s.endswith("-total"):
        return "total"
    if "-spread-" in s:
        return "spread"
    if any(k in s for k in ("-btts", "-exact-", "-first-half", "-halftime",
                            "-team-total", "-map-", "-handicap")):
        return "other"
    return "moneyline"


def load(act, tak):
    taker = set()
    for line in gzip.open(tak, "rt"):
        r = json.loads(line)
        taker.add((r["transaction_hash"], r["token_id"]))
    conds = collections.defaultdict(list)
    for line in gzip.open(act, "rt"):
        r = json.loads(line)
        if r.get("type") != "TRADE" or r.get("side") != "BUY":
            continue
        r["taker"] = (r["transaction_hash"], r["token_id"]) in taker
        conds[r["condition_id"]].append(r)
    return conds


def events_of(rows):
    """Merge same-token fills within MERGE_S into events, in time order."""
    rows = sorted(rows, key=lambda r: r["timestamp"])
    ev = []
    for r in rows:
        if ev and ev[-1]["token"] == r["token_id"] and \
                r["timestamp"] - ev[-1]["t1"] <= MERGE_S:
            e = ev[-1]
            e["sh"] += r["size"]
            e["usd"] += r["usdc_size"]
            e["t1"] = r["timestamp"]
            e["taker"] = e["taker"] or r["taker"]
        else:
            ev.append({"token": r["token_id"], "t0": r["timestamp"],
                       "t1": r["timestamp"], "sh": r["size"],
                       "usd": r["usdc_size"], "taker": r["taker"]})
    for e in ev:
        e["px"] = e["usd"] / e["sh"] if e["sh"] else 0.0
    return ev


def main(act, tak, out=None):
    conds = load(act, tak)
    st = collections.defaultdict(list)
    cnt = collections.Counter()
    mix = collections.Counter()
    comp_cap_hits = collections.Counter()
    for cid, rows in conds.items():
        toks = {r["token_id"] for r in rows}
        if len(toks) > 2:
            cnt["skip_multi_token"] += 1
            continue
        slug = rows[0].get("slug") or ""
        usd = sum(r["usdc_size"] for r in rows)
        mix[(sport_of(slug), mtype(slug))] += usd
        ev = events_of(rows)
        cnt["conditions"] += 1
        cnt["events"] += len(ev)
        sh = collections.defaultdict(float)
        cost = collections.defaultdict(float)
        last_t = {}
        run_tok, run = None, 0
        max_unm_frac = 0.0
        first = ev[0]
        st["first_px"].append(first["px"])
        for e in ev:
            t = e["token"]
            o = next((x for x in toks if x != t), None)
            qs, qo = sh[t], (sh[o] if o else 0.0)
            st["event_px"].append(e["px"])
            st["event_shares"].append(e["sh"])
            st["event_usd"].append(e["usd"])
            if e["taker"]:
                cnt["taker_events"] += 1
            if t in last_t:
                st["same_side_gap_s"].append(e["t0"] - last_t[t])
            if qo > qs + 1e-9:
                # COMPLETING: buying the lighter side
                avg_o = cost[o] / sh[o]
                pc = avg_o + e["px"]
                st["completion_pair_cost"].append(pc)
                for cap in PAIR_CAPS:
                    if pc <= cap:
                        comp_cap_hits[cap] += 1
                comp_cap_hits["all"] += 1
                st["completion_fraction_of_gap"].append(
                    min(1.0, e["sh"] / (qo - qs)))
                st["completion_px"].append(e["px"])
                if run_tok is not None:
                    st["run_before_switch"].append(run)
                run_tok, run = t, 1
            else:
                # OPENING (flat) or EXTENDING the heavier/equal side
                if qs > 0:
                    avg_s = cost[t] / sh[t]
                    st["extend_px_minus_avg"].append(e["px"] - avg_s)
                    st["extend_px"].append(e["px"])
                    cnt["extend_events"] += 1
                else:
                    cnt["open_events"] += 1
                if run_tok == t:
                    run += 1
                else:
                    if run_tok is not None:
                        st["run_before_switch"].append(run)
                    run_tok, run = t, 1
            sh[t] += e["sh"]
            cost[t] += e["usd"]
            last_t[t] = e["t1"]
            tot = sum(sh.values())
            if tot > 0 and len(sh) == 2:
                a, b = sorted(sh.values())
                max_unm_frac = max(max_unm_frac, (b - a) / b if b else 0)
        if len(sh) == 2 and min(sh.values()) > 0:
            cnt["both_sided"] += 1
            a, b = sorted(sh.values())
            st["final_unmatched_share_frac"].append((b - a) / b)
            st["max_unmatched_share_frac"].append(max_unm_frac)
            k = max(sh, key=sh.get)
            st["final_unmatched_usd"].append(
                (sh[k] - min(sh.values())) * cost[k] / sh[k])
            pcost = sum(cost[x] / sh[x] for x in sh)
            st["final_pair_cost"].append(pcost)
        else:
            cnt["one_sided"] += 1
            st["one_sided_usd"].append(usd)
    tot_mix = sum(mix.values())
    top_mix = [{"sport": s, "type": m, "usd_share": round(v / tot_mix, 4)}
               for (s, m), v in mix.most_common(25)]
    res = {
        "label": "HISTORICAL RN1 (Polymarket international), public fills; "
                 "not our P&L. OBSERVABLE = fills; resting quotes UNKNOWN.",
        "merge_s": MERGE_S,
        "counts": dict(cnt),
        "taker_event_share": round(cnt["taker_events"] / cnt["events"], 4),
        "first_px": q(st["first_px"]),
        "event_px": q(st["event_px"], (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)),
        "event_shares": q(st["event_shares"]),
        "event_usd": q(st["event_usd"]),
        "same_side_gap_s": q(st["same_side_gap_s"]),
        "run_before_switch": q(st["run_before_switch"]),
        "extend_px_minus_avg": q(st["extend_px_minus_avg"]),
        "extend_below_avg_share": round(
            sum(1 for x in st["extend_px_minus_avg"] if x < 0) /
            max(1, len(st["extend_px_minus_avg"])), 4),
        "completion_pair_cost": q(st["completion_pair_cost"]),
        "completion_share_at_or_below": {
            str(c): round(comp_cap_hits[c] / max(1, comp_cap_hits["all"]), 4)
            for c in PAIR_CAPS},
        "completion_fraction_of_gap": q(st["completion_fraction_of_gap"]),
        "final_unmatched_share_frac": q(st["final_unmatched_share_frac"]),
        "max_unmatched_share_frac": q(st["max_unmatched_share_frac"]),
        "final_unmatched_usd": q(st["final_unmatched_usd"]),
        "final_pair_cost_both_sided": q(st["final_pair_cost"]),
        "one_sided_usd": q(st["one_sided_usd"]),
        "market_mix_top": top_mix,
    }
    txt = json.dumps(res, indent=1)
    if out:
        with open(out, "w") as f:
            f.write(txt + "\n")
    print(txt)
    return res


if __name__ == "__main__":
    main(*sys.argv[1:4])
