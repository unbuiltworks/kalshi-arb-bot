"""Sequential taker-transition autopsy on the venue-labelled L5 fill corpus.

THE QUESTION. H1: does L5 disproportionately use TAKER fills to acquire the
OPPOSITE outcome after a same-side MAKER run? Nothing here optimizes anything,
and no strategy is inferred from P&L -- P&L is not read at all.

WHERE THE MAKER/TAKER LABEL COMES FROM, AND WHY IT BOUNDS EVERYTHING. It is
venue-supplied: `/trades?takerOnly=true` returns the subset of a user's trades
where they were the taker, so a fill present in the full set and absent from the
taker set is a MAKER fill. Verified as a subset, not assumed: within the
overlapping window 0.40% of taker rows were absent from the full set (pagination
boundary) against 80.86% of full rows absent from the taker set.

THE ENDPOINT CANNOT LABEL HISTORY. Measured, not assumed: the maximum usable
offset is exactly 10,000 (10,001 returns HTTP 400), and startTs, endTs, before,
after, from and min_timestamp are ALL silently ignored -- every one returned the
newest trades unchanged. So only the most recent ~10,500 rows per takerOnly
value are reachable, and the 760,817-fill historical corpus CANNOT be labelled
this way. The autopsy therefore runs on two complete labelled windows three
weeks apart rather than on the whole corpus, and that is a coverage limit of the
venue API, not a sampling choice.

THE ORDERING HAZARD, STATED UP FRONT. Timestamps are whole seconds and 53.4% of
fills share a second with another fill in the same market. Only 13.2% of those
groups are a single transaction, so for most of them the true order is NOT
recoverable. Run length, EXTEND/OPPOSITE and every transition statistic depend
on that order. Every headline number is therefore recomputed under several
tie-breaking rules and the SPREAD is reported; a result that moves under
tie-breaking is reported as unidentifiable rather than as a finding.

L5 NEVER SELLS in either window (100% BUY, n=20,988), so inventory is monotone
accumulation and EXTEND/OPPOSITE is just whether the outcome repeated.
"""
from __future__ import annotations

import collections
import json
import math
import random
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# Tie-breakers applied within one (market, second). "corpus" preserves the order
# the venue returned; "tx" sorts by transaction hash; "shuffle" randomizes. If
# the three disagree, the ordering is not identified.
TIE_BREAKS = ("corpus", "tx", "shuffle")
RUN_BUCKETS = ((1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 10), (11, 10 ** 9))
IMB_BUCKETS = ((0, 0), (1, 2), (3, 5), (6, 10), (11, 25), (26, 10 ** 9))
TTE_BUCKETS = ((0, 60), (61, 180), (181, 420), (421, 900), (901, 10 ** 9))
GAP_BUCKETS = ((0, 1), (2, 5), (6, 15), (16, 60), (61, 300), (301, 10 ** 9))


def bucket(v: Optional[float], spec) -> Optional[str]:
    if v is None:
        return None
    for lo, hi in spec:
        if lo <= v <= hi:
            return "%d-%d" % (lo, hi) if hi < 10 ** 9 else "%d+" % lo
    return None


def fill_key(x: Mapping) -> tuple:
    return (x["transactionHash"], x["conditionId"], int(x["outcomeIndex"]),
            x["side"], round(float(x["price"]), 6), round(float(x["size"]), 6),
            int(x["timestamp"]))


def label(full: Sequence[dict], taker: Iterable[dict]) -> List[dict]:
    """Attach the venue-supplied maker/taker label to every fill in `full`."""
    tk = {fill_key(x) for x in taker}
    out = []
    for i, x in enumerate(full):
        y = dict(x)
        y["_i"] = i
        y["is_taker"] = fill_key(x) in tk
        y["label_provenance"] = "VENUE_TRADES_ENDPOINT_takerOnly_SUBSET"
        out.append(y)
    return out


def order(fills: Sequence[dict], how: str, seed: int = 0) -> List[dict]:
    """Order one market's fills, breaking same-second ties by `how`."""
    rnd = random.Random(seed)
    def k(x):
        if how == "corpus":
            t2 = x["_i"]
        elif how == "tx":
            t2 = x["transactionHash"]
        else:
            t2 = rnd.random()
        return (int(x["timestamp"]), t2)
    return sorted(fills, key=k)


def sequential_state(by_market: Dict[str, List[dict]], ends: Dict[str, float],
                     how: str = "corpus", seed: int = 0) -> List[dict]:
    """Reconstruct the state immediately BEFORE each fill.

    Every field is read from fills strictly EARLIER in the same market. Nothing
    downstream of the fill -- not its own price, not the outcome, not settlement
    -- enters the state it is predicted from.
    """
    rows: List[dict] = []
    for cond, fills in by_market.items():
        seq = order(fills, how, seed)
        inv = {0: 0.0, 1: 0.0}
        prev: Optional[dict] = None
        run = 0
        last_opp_t: Optional[int] = None
        hist: List[bool] = []            # is_taker of preceding fills
        for x in seq:
            oi = int(x["outcomeIndex"])
            t = int(x["timestamp"])
            if prev is None:
                action = "OPEN"
            else:
                action = "EXTEND" if oi == int(prev["outcomeIndex"]) else "OPPOSITE"
            end = ends.get(cond)
            rows.append({
                "conditionId": cond, "slug": x.get("slug"),
                "timestamp": t, "is_taker": bool(x["is_taker"]),
                "outcome_index": oi, "price": float(x["price"]),
                "size": float(x["size"]), "side": x["side"],
                "prev_outcome_index": (None if prev is None else int(prev["outcomeIndex"])),
                "prev_price": (None if prev is None else float(prev["price"])),
                "action": action,
                "run_before": run,
                "inv_0_before": inv[0], "inv_1_before": inv[1],
                "residual_before": inv[0] - inv[1],
                "abs_imbalance_before": abs(inv[0] - inv[1]),
                "secs_since_prev_fill": (None if prev is None else t - int(prev["timestamp"])),
                "secs_since_prev_opposite": (None if last_opp_t is None else t - last_opp_t),
                "secs_to_settlement": (None if end is None else end - t),
                "prev1_taker": (hist[-1] if len(hist) >= 1 else None),
                "prev2_taker": (hist[-2] if len(hist) >= 2 else None),
                "prev3_taker": (hist[-3] if len(hist) >= 3 else None),
                "tie_break": how,
            })
            if action == "OPPOSITE":
                last_opp_t = t
                run = 1
            elif action == "EXTEND":
                run += 1
            else:
                run = 1
            inv[oi] += float(x["size"]) if x["side"] == "BUY" else -float(x["size"])
            hist.append(bool(x["is_taker"]))
            prev = x
    return rows


# ---------------------------------------------------------------- statistics
def rate(rows: Sequence[dict], num, den) -> Tuple[int, int, Optional[float]]:
    d = [r for r in rows if den(r)]
    n = sum(1 for r in d if num(r))
    return n, len(d), (n / len(d) if d else None)


def cluster_boot(rows: Sequence[dict], stat, key="conditionId",
                 n: int = 3000, seed: int = 17) -> Optional[Tuple[float, float]]:
    """Resample MARKETS, not fills: fills in one market are not independent."""
    g: Dict[Any, List[dict]] = collections.defaultdict(list)
    for r in rows:
        g[r[key]].append(r)
    ks = list(g)
    if len(ks) < 2:
        return None
    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        s = [x for k in (rnd.choice(ks) for _ in ks) for x in g[k]]
        v = stat(s)
        if v is not None:
            out.append(v)
    if not out:
        return None
    out.sort()
    return out[int(0.025 * (len(out) - 1))], out[int(0.975 * (len(out) - 1))]


def contrast(rows: Sequence[dict], num, den_a, den_b) -> Dict[str, Any]:
    """Risk difference and risk ratio between two conditioning sets."""
    na, da, pa = rate(rows, num, den_a)
    nb, db, pb = rate(rows, num, den_b)
    rd = (pa - pb) if (pa is not None and pb is not None) else None
    rr = (pa / pb) if (pa is not None and pb not in (None, 0.0)) else None
    ci = cluster_boot(rows, lambda s: (
        (rate(s, num, den_a)[2] - rate(s, num, den_b)[2])
        if (rate(s, num, den_a)[2] is not None and rate(s, num, den_b)[2] is not None)
        else None))
    return {"a_n": na, "a_d": da, "a_rate": pa, "b_n": nb, "b_d": db, "b_rate": pb,
            "risk_difference": rd, "risk_ratio": rr, "rd_ci95_cluster": ci}


def by_bucket(rows: Sequence[dict], field, spec, num) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for lo, hi in spec:
        name = "%d-%d" % (lo, hi) if hi < 10 ** 9 else "%d+" % lo
        sub = [r for r in rows if r.get(field) is not None and lo <= r[field] <= hi]
        n, d, p = rate(sub, num, lambda r: True)
        out[name] = {"n": n, "d": d, "rate": p}
    return out


IS_TAKER = lambda r: r["is_taker"]                                    # noqa: E731
IS_OPP = lambda r: r["action"] == "OPPOSITE"                          # noqa: E731
IS_EXT = lambda r: r["action"] == "EXTEND"                            # noqa: E731
OPP_TAKER = lambda r: r["is_taker"] and r["action"] == "OPPOSITE"     # noqa: E731


def transitions(rows: Sequence[dict]) -> Dict[str, Any]:
    """Frequencies of (prev maker/taker, prev action) -> (this, this action)."""
    by: Dict[str, List[dict]] = collections.defaultdict(list)
    for r in rows:
        by[r["conditionId"]].append(r)
    c: collections.Counter = collections.Counter()
    for v in by.values():
        v = sorted(v, key=lambda r: r["timestamp"])
        for a, b in zip(v, v[1:]):
            if a["action"] == "OPEN":
                continue
            c[("TAKER" if a["is_taker"] else "MAKER", a["action"],
               "TAKER" if b["is_taker"] else "MAKER", b["action"])] += 1
    tot = sum(c.values())
    named = {}
    for (r0, a0, r1, a1), n in c.items():
        named["%s %s -> %s %s" % (r0, a0, r1, a1)] = {
            "n": n, "share_of_all_transitions": (n / tot if tot else None)}
    src = collections.Counter()
    for (r0, a0, _, _), n in c.items():
        src[(r0, a0)] += n
    for (r0, a0, r1, a1), n in c.items():
        named["%s %s -> %s %s" % (r0, a0, r1, a1)]["share_of_source_state"] = \
            n / src[(r0, a0)] if src[(r0, a0)] else None
    return {"total_transitions": tot, "transitions": named}


# --------------------------------------------------------------------- nulls
def null_shuffle_labels(rows: Sequence[dict], seed: int = 3) -> List[dict]:
    """Permute maker/taker WITHIN each market.

    Keeps every market's action sequence, run structure, prices, timing and its
    own taker RATE exactly as observed, and destroys only the association
    between the label and the action. If H1 is real it must beat this.
    """
    rnd = random.Random(seed)
    by: Dict[str, List[int]] = collections.defaultdict(list)
    for i, r in enumerate(rows):
        by[r["conditionId"]].append(i)
    out = [dict(r) for r in rows]
    for idx in by.values():
        lab = [rows[i]["is_taker"] for i in idx]
        rnd.shuffle(lab)
        for i, v in zip(idx, lab):
            out[i]["is_taker"] = v
    return out


def null_shuffle_actions(rows: Sequence[dict], seed: int = 5) -> List[dict]:
    """Permute the EXTEND/OPPOSITE label WITHIN each market.

    The mirror of the first null: labels stay attached to their fills, the
    action stream is scrambled. OPEN rows are left alone -- a market has exactly
    one and moving it would invent a second first fill.
    """
    rnd = random.Random(seed)
    by: Dict[str, List[int]] = collections.defaultdict(list)
    for i, r in enumerate(rows):
        if r["action"] != "OPEN":
            by[r["conditionId"]].append(i)
    out = [dict(r) for r in rows]
    for idx in by.values():
        act = [rows[i]["action"] for i in idx]
        rnd.shuffle(act)
        for i, v in zip(idx, act):
            out[i]["action"] = v
    return out


def state_matched(rows: Sequence[dict], num, den_a, den_b,
                  strata=("price_b", "tte_b")) -> Dict[str, Any]:
    """Compare a against b WITHIN strata of price and time-to-settlement.

    A raw contrast can be produced entirely by composition: if OPPOSITE fills
    happen at different prices or closer to settlement than EXTEND fills, and
    the taker rate varies with those, the difference says nothing about the
    action. This reweights b onto a's stratum distribution and reports the
    residual difference -- only strata where BOTH sides have observations can
    contribute, and the coverage that survives is reported.
    """
    def keyof(r):
        return tuple(r.get(s) for s in strata)
    A_: Dict[tuple, List[dict]] = collections.defaultdict(list)
    B_: Dict[tuple, List[dict]] = collections.defaultdict(list)
    for r in rows:
        if den_a(r):
            A_[keyof(r)].append(r)
        if den_b(r):
            B_[keyof(r)].append(r)
    shared = [k for k in A_ if k in B_ and None not in k]
    wa = sum(len(A_[k]) for k in shared)
    if not wa:
        return {"strata_used": 0, "adjusted_risk_difference": None,
                "coverage_of_a": 0.0}
    obs = sum(sum(1 for r in A_[k] if num(r)) for k in shared) / wa
    exp = sum(len(A_[k]) * (sum(1 for r in B_[k] if num(r)) / len(B_[k]))
              for k in shared) / wa
    tot_a = sum(1 for r in rows if den_a(r))
    return {"strata_used": len(shared), "a_rows_in_shared_strata": wa,
            "coverage_of_a": (wa / tot_a if tot_a else 0.0),
            "observed_rate_in_shared_strata": obs,
            "expected_from_b_same_strata": exp,
            "adjusted_risk_difference": obs - exp}


def annotate_strata(rows: Sequence[dict]) -> None:
    """Price and time-to-settlement strata used by the state-matched null."""
    for r in rows:
        r["price_b"] = int(min(max(r["price"], 0.0), 0.999) * 10)
        r["tte_b"] = bucket(r.get("secs_to_settlement"), TTE_BUCKETS)


def h1(rows: Sequence[dict]) -> Dict[str, Any]:
    """H1: taker is used disproportionately to acquire the OPPOSITE outcome
    after a same-side run. Reported as P(TAKER|OPPOSITE) - P(TAKER|EXTEND)."""
    return contrast(rows, IS_TAKER, IS_OPP, IS_EXT)
