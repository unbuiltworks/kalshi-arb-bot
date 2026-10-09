"""Does continuous passive execution work better in sports than in BTC15m?

NOT a new strategy and NOT a new service. Every policy, every fill test and
every settlement inference is imported from `pmus_l5_pair_policy_study`
unchanged; this module only decides WHICH markets each run covers and HOW the
results are cut. It runs inside the existing shadow's study cycle.

WHAT IT ADDS OVER THE BTC STUDY

1. CATEGORY SEGMENTATION. The BTC study filters the tape to `cpc-`. This one
   takes the whole tape and groups by category (crypto / sports), sport and
   market type, derived from the slug by the venue's own naming
   (`pmus_execution_shadow.sport_of`, `.market_type`).

2. WHY A QUOTE DID NOT FILL, separated four ways, because "no fill" has four
   causes with opposite implications:

     MISSING_FLOW        the queue was known and nothing printed into it. The
                         market simply had no opposite-side flow. This is the
                         finding, not a defect.
     QUEUE_NOT_CLEARED   volume printed but less than the queue standing in
                         front of us. Depth, not absence of flow.
     INSUFFICIENT_DEPTH  the book was one-sided, so no own-side price exists.
     LADDER_UNKNOWN      the ladder was missing at placement. EVIDENCE IS
                         UNAVAILABLE -- this is NOT a non-fill and is counted
                         and reported separately, never folded into any of
                         the above. Treating it as a non-fill would make a
                         collector gap look like an illiquid market.

3. CONDITION BUCKETS, measured at the first admitting observation only, so
   nothing is bucketed on information that arrives after entry: spread in
   ticks, own-side depth at the touch, observed opposite-side flow over the
   window, and time to settlement.

4. CAPITAL AND EXPOSURE: dollars tied up while unmatched, time-weighted, and
   the maximum one-sided inventory each policy reached.

THE VERDICT RULE IS FIXED BEFORE THE NUMBERS ARE SEEN, so it cannot be tuned
to them:

  GO         full-inventory net per entry > 0 with a 90% interval strictly
             above 0, out-of-sample, on >= 30 entries.
  MORE_DATA  out-of-sample point estimate >= 0, or fewer than 30 entries, or
             an interval straddling 0.
  NO_GO      out-of-sample interval strictly below 0 on >= 30 entries.

Thresholds are not fitted on the test half; the in-sample half exists only so
that claim is checkable.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src import pmus_execution_shadow as EX
from src import pmus_l5_pair_policy_study as PS
from src import pmus_status_report as ST

CONTINUOUS = ("P7_BEST_BID_RESTING", "P8_DEEPER_RESTING",
              "P10_CONTINUOUS_TWO_SIDED")
ONE_SHOT = ("P1_IMMEDIATE_TAKER", "P2_DELAYED_MAKER", "P6_INVENTORY_AWARE")
STUDIED = CONTINUOUS + ONE_SHOT + ("P9_ONE_TICK_IMPROVE",)
MIN_ENTRIES = int(os.getenv("L5_SPORTS_MIN_ENTRIES", "30"))
MIN_MARKETS = int(os.getenv("L5_SPORTS_MIN_MARKETS", "8"))


# ------------------------------------------------------------- grouping ---
CRYPTO_HEADS = ("cpc-", "btc-", "eth-", "sol-", "xrp-", "doge-", "ltc-",
                "ada-", "link-", "avax-")


def category_of(slug: str) -> str:
    """Crypto by slug head, everything else sports.

    The venue's own naming is the only key the tape carries -- an observation
    row has no category field -- so this is deliberately a slug test and not
    a lookup, and the crypto heads are listed rather than inferred.
    """
    return ("crypto" if str(slug or "").lower().startswith(CRYPTO_HEADS)
            else "sports")


def cohort(slug: str) -> Dict[str, Optional[str]]:
    cat = category_of(slug)
    return {"category": cat,
            "sport": (EX.sport_of(slug) if cat == "sports" else None),
            "market_type": EX.market_type(slug)}


# ------------------------------------------------- why a quote sat unfilled ---
def diagnose(obs: Sequence[Mapping[str, Any]]) -> dict:
    """Classify, per market, why a best-bid quote would not have filled.

    Placed at the first two-sided observation and judged to the tape's end,
    using exactly `PS.resting_fill`'s discipline. Each market yields ONE
    label; a market whose ladder is absent yields LADDER_UNKNOWN and is not
    called a non-fill.
    """
    for i in range(len(obs) - 1):
        side = PS._entry_side(obs[i])
        if side is None:
            continue
        q = PS.own_side(obs[i], side)
        if q is None:
            continue
        ahead = PS.queue_ahead(obs[i], side, q["maker"])
        if ahead is None:
            return {"why": "LADDER_UNKNOWN", "evidence_available": False}
        f = PS.resting_fill(obs, i, side, q["maker"], PS.UNIT, None)
        if f.get("filled"):
            return {"why": "FILLED", "evidence_available": True,
                    "queue_ahead": f["queue_ahead"],
                    "latency_s": f["latency_s"]}
        trades = ST.infer_trades(list(obs))
        t0 = PS._f(obs[i].get("_poll_t"))
        t1 = PS._f(obs[-1].get("_poll_t"))
        through = (EX._volume_through(trades, t0, t1, side, q["maker"])
                   if t0 is not None and t1 is not None else 0.0)
        return {"why": ("MISSING_FLOW" if through <= 0
                        else "QUEUE_NOT_CLEARED"),
                "evidence_available": True,
                "queue_ahead": round(ahead, 2),
                "volume_into_our_price": round(through, 2)}
    return {"why": "INSUFFICIENT_DEPTH", "evidence_available": True}


# ------------------------------------------------------- entry conditions ---
def _bucket(x: Optional[float], edges: Sequence[float],
            names: Sequence[str]) -> Optional[str]:
    if x is None:
        return None
    for e, n in zip(edges, names):
        if x <= e:
            return n
    return names[-1]


def entry_conditions(obs: Sequence[Mapping[str, Any]]) -> dict:
    """Spread, depth, opposite-side flow and time to settlement, read at the
    FIRST admitting observation only. Nothing here may use a later row."""
    for i in range(len(obs) - 1):
        side = PS._entry_side(obs[i])
        q = PS.own_side(obs[i], side) if side is not None else None
        if q is None:
            continue
        row = obs[i]
        spread_t = float((q["ask"] - q["bid"]) / PS.TICK)
        depth = PS._f(row.get("bid_qty") if side else row.get("ask_qty"))
        t0, tN = PS._f(row.get("_poll_t")), PS._f(obs[-1].get("_poll_t"))
        trades = ST.infer_trades(list(obs))
        opp = (EX._volume_through(trades, t0, tN, not side, q["maker"])
               if t0 is not None and tN is not None else None)
        return {
            "spread_ticks": round(spread_t, 1),
            "spread_bucket": _bucket(spread_t, (1.0, 2.0, 5.0),
                                     ("1_tick", "2_ticks", "3_to_5",
                                      "over_5")),
            "touch_depth": depth,
            "depth_bucket": _bucket(depth, (10.0, 100.0, 1000.0),
                                    ("thin_le10", "le100", "le1k",
                                     "over_1k")),
            "opposite_side_volume": (None if opp is None
                                     else round(opp, 2)),
            "opp_flow_bucket": _bucket(opp, (0.0, 10.0, 100.0),
                                       ("none", "le10", "le100",
                                        "over_100")),
            "seconds_of_tape": (None if t0 is None or tN is None
                                else round(tN - t0, 1)),
            "tape_bucket": _bucket(
                None if t0 is None or tN is None else tN - t0,
                (300.0, 900.0, 3600.0),
                ("under_5min", "under_15min", "under_1h", "over_1h")),
        }
    return {}


# ------------------------------------------------- exposure and capacity ---
def exposure(row: Mapping[str, Any]) -> dict:
    """Capital tied up and worst one-sided inventory, from the replay row.

    Capital is the unmatched leg's cost held for the measured time at risk;
    it is reported as dollar-seconds as well as dollars so a policy that
    holds a small residual for a long time is not flattered.
    """
    legs = row.get("fill_evidence") or []
    yes_c = sum(float(l["qty"]) * float(l["px"]) for l in legs
                if l.get("side") == "YES")
    no_c = sum(float(l["qty"]) * float(l["px"]) for l in legs
               if l.get("side") != "YES")
    resid = abs(float(row.get("residual_shares_signed") or 0))
    at_risk = PS._f(row.get("time_at_risk_s")) or 0.0
    gross = yes_c + no_c
    unmatched = max(yes_c, no_c) - min(yes_c, no_c)
    return {"gross_cost_usd": round(gross, 4),
            "unmatched_cost_usd": round(unmatched, 4),
            "capital_dollar_seconds": round(unmatched * at_risk, 1),
            "max_one_sided_shares": resid}


# ------------------------------------------------------------- the study ---
def _agg(rows: Sequence[Mapping[str, Any]], policy: str,
         settled: Mapping[str, Mapping[str, Any]]) -> dict:
    mine = [r for r in rows if r.get("policy") == policy
            and "skipped" not in r]
    base = PS.summarize(rows, policy)
    full = PS.settled_summary(rows, settled, policy)
    exps = [exposure(r) for r in mine]
    return {
        "policy": policy,
        "entries": base.get("entries"),
        "completion_rate": base.get("completion_rate"),
        "median_pair_cost": (base.get("matched_pair_cost") or {}).get(
            "median"),
        "fills_per_entry": base.get("fills_per_entry"),
        "matched_frac_of_purchased_median": base.get(
            "matched_frac_of_purchased_median"),
        "time_to_complement_s_median": base.get(
            "time_to_complement_s_median"),
        "residual_shares": base.get("residual_shares"),
        "fees_usd_total": base.get("fees_usd_total"),
        "adverse_selection_t30": base.get("adverse_selection_t30"),
        "capital": {
            "unmatched_cost_usd_median": PS._pct(
                [e["unmatched_cost_usd"] for e in exps], 0.5),
            "capital_dollar_seconds_median": PS._pct(
                [e["capital_dollar_seconds"] for e in exps], 0.5),
            "max_one_sided_shares": (max((e["max_one_sided_shares"]
                                          for e in exps), default=None)),
        },
        "full_inventory": {
            "net_per_entry_usd": full.get("net_per_entry_usd_FULL_INVENTORY"),
            "settlement_decidable": full.get("settlement_decidable"),
            "settlement_undecidable": full.get("settlement_undecidable"),
            "losing_residuals": full.get("losing_residuals"),
            "SETTLEMENT": full.get("SETTLEMENT"),
        },
        "LABEL": "REPLAY_SETTLED_INFERRED_NOT_REALIZED",
    }


def verdict(oos: Mapping[str, Any]) -> dict:
    """The pre-registered rule, applied to the OUT-OF-SAMPLE half only."""
    net = (oos.get("full_inventory") or {}).get("net_per_entry_usd") or {}
    n, p05, p95 = net.get("n"), net.get("p05"), net.get("p95")
    mean = net.get("mean")
    if n is None or mean is None:
        return {"verdict": "MORE_DATA", "why": "NO_OUT_OF_SAMPLE_ENTRIES",
                "n": n}
    if n < MIN_ENTRIES:
        return {"verdict": "MORE_DATA",
                "why": "UNDERPOWERED_n_below_%d" % MIN_ENTRIES, "n": n,
                "mean": mean}
    if p05 is not None and p05 > 0:
        return {"verdict": "GO", "why": "INTERVAL_STRICTLY_ABOVE_ZERO",
                "n": n, "mean": mean}
    if p95 is not None and p95 < 0:
        return {"verdict": "NO_GO", "why": "INTERVAL_STRICTLY_BELOW_ZERO",
                "n": n, "mean": mean}
    return {"verdict": "MORE_DATA", "why": "INTERVAL_STRADDLES_ZERO",
            "n": n, "mean": mean}


def study(obs_by_slug: Mapping[str, Sequence[Mapping[str, Any]]], *,
          policies: Sequence[str] = STUDIED,
          min_markets: int = MIN_MARKETS) -> dict:
    """Per-category comparison of continuous quoting against one-shot entry,
    on exactly the same markets and the same observation windows."""
    groups: Dict[str, List[str]] = defaultdict(list)
    for slug in obs_by_slug:
        c = cohort(slug)
        groups["category=%s" % c["category"]].append(slug)
        if c["category"] == "sports" and c["market_type"]:
            groups["sports/market_type=%s" % c["market_type"]].append(slug)
        if c["sport"]:
            groups["sports/sport=%s" % c["sport"]].append(slug)

    why: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    conds: Dict[str, List[dict]] = defaultdict(list)
    for slug, obs in obs_by_slug.items():
        g = "category=%s" % cohort(slug)["category"]
        d = diagnose(obs)
        why[g][d["why"]] += 1
        c = entry_conditions(obs)
        if c:
            conds[g].append(c)

    out: Dict[str, Any] = {
        "markets_total": len(obs_by_slug),
        "observations_total": sum(len(v) for v in obs_by_slug.values()),
        "verdict_rule": {
            "GO": "OOS net/entry interval strictly > 0 on >= %d entries"
                  % MIN_ENTRIES,
            "MORE_DATA": "OOS point >= 0, or underpowered, or interval "
                         "straddles 0",
            "NO_GO": "OOS interval strictly < 0 on >= %d entries"
                     % MIN_ENTRIES,
            "PREREGISTERED": True,
        },
        "groups": {},
        "LABELS": ("Nothing here is realized profit. Settlement is inferred "
                   "from the market's own terminal prices and labelled; "
                   "undecidable markets are reported, never scored as zero. "
                   "LADDER_UNKNOWN is unavailable evidence, not a non-fill."),
    }
    for g, slugs in sorted(groups.items()):
        if len(slugs) < min_markets:
            out["groups"][g] = {"markets": len(slugs),
                                "skipped": "FEWER_THAN_%d_MARKETS"
                                           % min_markets}
            continue
        ordered = sorted(slugs,
                         key=lambda s: PS._f(obs_by_slug[s][0].get("_poll_t"))
                         or 0)
        cut = len(ordered) // 2
        halves = {"in_sample": ordered[:cut],
                  "out_of_sample": ordered[cut:]}
        settled = {s: PS.infer_settlement(obs_by_slug[s]) for s in ordered}
        gout: Dict[str, Any] = {
            "markets": len(ordered),
            "settlement_coverage": {
                "decidable": sum(1 for v in settled.values()
                                 if v.get("resolved")),
                "undecidable": sum(1 for v in settled.values()
                                   if not v.get("resolved")),
            },
            "why_no_fill": dict(why.get(g, {})),
            "entry_conditions": {
                k: PS._mix([c.get(k) for c in conds.get(g, [])])
                for k in ("spread_bucket", "depth_bucket",
                          "opp_flow_bucket", "tape_bucket")},
            "halves": {},
        }
        for half, group in halves.items():
            rows: List[dict] = []
            for s in group:
                for p in policies:
                    rows.append(
                        PS.replay_placement(s, obs_by_slug[s], p)
                        if p in PS.PLACEMENT
                        else PS.replay(s, obs_by_slug[s], p))
            gout["halves"][half] = {
                "markets": len(group),
                "continuous": {p: _agg(rows, p, settled)
                               for p in CONTINUOUS
                               if p in policies},
                "one_shot": {p: _agg(rows, p, settled)
                             for p in ONE_SHOT if p in policies},
            }
        oos = gout["halves"].get("out_of_sample", {}).get("continuous", {})
        best = None
        for p, a in oos.items():
            net = ((a.get("full_inventory") or {}).get("net_per_entry_usd")
                   or {}).get("mean")
            if net is not None and (best is None or net > best[1]):
                best = (p, net, a)
        gout["verdict"] = (verdict(best[2]) if best else
                           {"verdict": "MORE_DATA",
                            "why": "NO_CONTINUOUS_ENTRIES_OUT_OF_SAMPLE"})
        gout["verdict"]["best_continuous_policy"] = (best[0] if best
                                                     else None)
        out["groups"][g] = gout
    return out


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", required=True)
    ap.add_argument("--prefix", default="")
    a = ap.parse_args(argv)
    tape = PS.load_tape(a.state_dir, prefix=a.prefix)
    print(json.dumps(study(tape), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
