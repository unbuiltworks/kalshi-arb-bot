"""CANDIDATE V2 -- does contemporaneous BOOK STATE explain L5's SWITCH?

V1 reproduced EXTEND at 99.71% and predicted 0 of 17,096 held-out SWITCH
decisions. V1 had no book. V2 adds only information observable
IMMEDIATELY BEFORE each decision, from the verified Zenodo 22084977
1-second L2 (sha256 d86191506e416026f460338b1b238c628b3fcee37f468e646b3a
2256bd0be959, CC BY 4.0).

NOT a threshold tuning of V1. The question is whether the book carries the
signal V1 lacked, measured on rare-action discrimination rather than
accuracy -- always-EXTEND already scores ~60%.

POWER, STATED UP FRONT: the overlap is 27 markets / 3,073 fills, and fills
per market run max 390, median 13, min 1. A chronological 70/30 split
would leave ~9 OOS markets, and the independent unit here is the MARKET,
not the row. **That is not a defensible out-of-sample test, so none is
claimed.** V2 uses GROUPED LEAVE-ONE-MARKET-OUT cross-validation over all
27 markets and reports market-level uncertainty. The model specification
and its fingerprint are fixed before any scoring runs.

NO FUTURE INFORMATION. Every book read is the last row STRICTLY earlier
than the decision instant, carried at most 60s (the capture's own resync
interval) and refused beyond that. Same-timestamp opposite-outcome legs
are genuinely unordered in the tape and stay EXCLUDED, as in V1.
"""
from __future__ import annotations

import bisect
import collections
import gzip
import hashlib
import json
import statistics
from typing import Any, Dict, List, Optional, Tuple

PARQUET = ("/tmp/claude-0/-home-user-kalshi-arb-bot/"
           "f2f630e2-70fa-534f-9e92-d1857b7df8ca/scratchpad/l2/"
           "updown_15m.parquet")
SHA = "d86191506e416026f460338b1b238c628b3fcee37f468e646b3a2256bd0be959"
L5 = "0xb945945d5bca"
MAX_STALE_S = 60.0
ACTIONS = ("EXTEND_SAME_SIDE", "SWITCH_OUTCOME", "STOP")
KEY = lambda x: (x.get("transactionHash"), x.get("conditionId"),
                 x.get("outcomeIndex"), x.get("timestamp"),
                 str(x.get("price")), str(x.get("size")), x.get("type"))
#: predeclared REPORTING bins -- fixed before scoring, never searched
MINT_BINS = (("lt_0.95", -9e9, 0.95), ("0.95_0.98", 0.95, 0.98),
             ("0.98_1.00", 0.98, 1.0), ("ge_1.00", 1.0, 9e9))
MARG_BINS = (("lt_0.96", -9e9, 0.96), ("0.96_1.00", 0.96, 1.0),
             ("1.00_1.05", 1.0, 1.05), ("ge_1.05", 1.05, 9e9))
IMB_BINS = (("balanced", -9e9, 0.1), ("mild", 0.1, 0.4),
            ("heavy", 0.4, 0.8), ("one_sided", 0.8, 9e9))


def fl(v, d=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def band(v, bins):
    if v is None:
        return "unknown"
    for n, lo, hi in bins:
        if lo <= v < hi:
            return n
    return bins[-1][0]


def load_books():
    import pyarrow.parquet as pq
    t = pq.read_table(PARQUET, columns=[
        "market_slug", "coin", "outcome", "captured_at",
        "best_bid", "best_ask", "bid_sizes", "ask_sizes"])
    d = t.to_pydict()
    ser: Dict[Tuple[str, str], List[tuple]] = collections.defaultdict(list)
    for i in range(len(d["market_slug"])):
        if d["coin"][i] not in ("btc", "eth"):
            continue
        ca = d["captured_at"][i]
        ts = ca.timestamp() if hasattr(ca, "timestamp") else float(ca)
        bs = d["bid_sizes"][i] or []
        as_ = d["ask_sizes"][i] or []
        ser[(d["market_slug"][i], str(d["outcome"][i]).lower())].append(
            (ts, fl(d["best_bid"][i]), fl(d["best_ask"][i]),
             fl(bs[0]) if len(bs) else None,
             fl(as_[0]) if len(as_) else None))
    for k in ser:
        ser[k].sort()
    return ser


def before(series, ts):
    """Last row STRICTLY earlier than ts, refused if staler than 60s."""
    if not series:
        return None
    times = [r[0] for r in series]
    i = bisect.bisect_left(times, ts) - 1
    if i < 0:
        return None
    r = series[i]
    return r if (ts - r[0]) <= MAX_STALE_S else None


def load_fills(slugs):
    seen, per = set(), collections.defaultdict(list)
    for path, gz in (("data/l5/raw/activity.jsonl", False),
                     ("data/l5/raw/activity_gap.jsonl.gz", True)):
        op = gzip.open if gz else open
        with op(path, "rt", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                try:
                    env = json.loads(line)
                except Exception:                              # noqa: BLE001
                    continue
                if L5 not in str((env.get("params") or {})
                                 .get("user", "")).lower():
                    continue
                pl = env.get("payload")
                if not isinstance(pl, list):
                    continue
                for r in pl:
                    if not isinstance(r, dict) or r.get("type") != "TRADE":
                        continue
                    nm = r.get("name")
                    if nm is not None and nm != "l5Zn1bWoM8eTsK":
                        continue
                    k = KEY(r)
                    if k in seen:
                        continue
                    seen.add(k)
                    s = str(r.get("slug") or "") or str(r.get("eventSlug")
                                                        or "")
                    if s in slugs:
                        per[s].append(r)
    for s in per:
        per[s].sort(key=lambda r: fl(r.get("timestamp"), 0.0))
    return per


def legs_for(trades):
    g = collections.OrderedDict()
    for r in trades:
        oi = r.get("outcomeIndex")
        oc = str(r.get("outcome") or "").lower()
        if oi not in (0, 1):
            continue
        t = fl(r.get("timestamp"), 0.0)
        a = g.setdefault((t, oi), {"t": t, "oi": oi, "oc": oc, "q": 0.0,
                                   "notional": 0.0})
        q = fl(r.get("size"), 0.0)
        a["q"] += q
        a["notional"] += fl(r.get("price"), 0.0) * q
    legs = sorted(g.values(), key=lambda a: (a["t"], a["oi"]))
    for a in legs:
        a["px"] = a["notional"] / a["q"] if a["q"] > 0 else 0.0
    bt = collections.Counter(a["t"] for a in legs)
    for a in legs:
        a["ambiguous"] = bt[a["t"]] > 1
    return legs


def decisions(slug, legs, ser):
    """Causal decision points with BOOK STATE read strictly before."""
    names = sorted({a["oc"] for a in legs})
    qty = collections.defaultdict(float)
    cost = collections.defaultdict(float)
    out = []
    for i, a in enumerate(legs):
        qty[a["oc"]] += a["q"]
        cost[a["oc"]] += a["notional"]
        nxt = legs[i + 1] if i + 1 < len(legs) else None
        if nxt is not None and (a["ambiguous"] or nxt["ambiguous"]):
            continue
        label = ("STOP" if nxt is None else
                 ("EXTEND_SAME_SIDE" if nxt["oc"] == a["oc"]
                  else "SWITCH_OUTCOME"))
        own = a["oc"]
        comp = next((n for n in names if n != own), None)
        bo = before(ser.get((slug, own), []), a["t"])
        bc = before(ser.get((slug, comp), []), a["t"]) if comp else None
        d: Dict[str, Any] = {"slug": slug, "t": a["t"], "label": label,
                             "own": own, "px": a["px"], "i": i}
        if bo:
            _, d["own_bid"], d["own_ask"], d["own_bid_sz"], \
                d["own_ask_sz"] = bo
        if bc:
            _, d["comp_bid"], d["comp_ask"], d["comp_bid_sz"], \
                d["comp_ask_sz"] = bc
        ob, oa = d.get("own_bid"), d.get("own_ask")
        cb, ca = d.get("comp_bid"), d.get("comp_ask")
        d["own_spread"] = (oa - ob) if (ob is not None
                                        and oa is not None) else None
        d["comp_spread"] = (ca - cb) if (cb is not None
                                         and ca is not None) else None
        # THE MINT SUM -- a first-class state variable
        d["mint_sum"] = ((ob + cb) if (ob is not None and cb is not None)
                         else None)
        d["mint_feasible"] = (d["mint_sum"] >= 1.0
                              if d["mint_sum"] is not None else None)
        d["comp_executable_price"] = ca      # what buying the complement costs
        vw_own = cost[own] / qty[own] if qty[own] > 0 else None
        vw_comp = (cost[comp] / qty[comp]
                   if (comp and qty[comp] > 0) else None)
        d["inv_pair_cost"] = ((vw_own + vw_comp)
                              if (vw_own is not None and vw_comp is not None)
                              else None)
        # marginal pair cost of acting NOW
        d["marg_pair_if_switch"] = ((vw_own + ca)
                                    if (vw_own is not None
                                        and ca is not None) else None)
        d["marg_pair_if_extend"] = ((oa + vw_comp)
                                    if (oa is not None
                                        and vw_comp is not None) else None)
        d["prev_fill_vs_bid"] = ((a["px"] - ob) if ob is not None else None)
        d["prev_fill_vs_ask"] = ((a["px"] - oa) if oa is not None else None)
        prior = [z for z in legs[:i] if z["oc"] == own]
        d["move_since_prev_fill"] = ((a["px"] - prior[-1]["px"])
                                     if prior else 0.0)
        if prior and ob is not None:
            pb = before(ser.get((slug, own), []), prior[-1]["t"])
            d["book_move_since_prev_fill"] = ((ob - pb[1])
                                              if (pb and pb[1] is not None)
                                              else None)
        tot = qty[own] + (qty[comp] if comp else 0.0)
        matched = min(qty[own], qty[comp]) if comp else 0.0
        d["matched_fraction"] = (matched / max(qty[own],
                                               qty[comp] if comp else 0.0)
                                 if tot > 0 and max(
                                     qty[own],
                                     qty[comp] if comp else 0.0) > 0 else None)
        d["imbalance"] = (abs(qty[own] - (qty[comp] if comp else 0.0))
                          / tot if tot > 0 else None)
        d["mint_bin"] = band(d["mint_sum"], MINT_BINS)
        d["marg_switch_bin"] = band(d["marg_pair_if_switch"], MARG_BINS)
        d["imb_bin"] = band(d["imbalance"], IMB_BINS)
        out.append(d)
    return out


def monotone(pts, key, bins_name):
    tab = collections.defaultdict(collections.Counter)
    for p in pts:
        tab[p[key]][p["label"]] += 1
    rows = {}
    for b, c in tab.items():
        n = sum(c.values())
        rows[b] = {"n": n, "markets": len({p["slug"] for p in pts
                                           if p[key] == b}),
                   "p_switch": round(c.get("SWITCH_OUTCOME", 0) / n, 4),
                   "p_extend": round(c.get("EXTEND_SAME_SIDE", 0) / n, 4),
                   "p_stop": round(c.get("STOP", 0) / n, 4)}
    return {"feature": bins_name, "bins": rows}


def pr_auc(scored):
    """Average precision for the SWITCH label from ranked scores."""
    s = sorted(scored, key=lambda x: -x[0])
    pos = sum(1 for _, y in s if y)
    if not pos:
        return None
    tp = 0
    ap = 0.0
    for i, (_, y) in enumerate(s, 1):
        if y:
            tp += 1
            ap += tp / i
    return round(ap / pos, 4)


def main() -> int:
    ser = load_books()
    slugs = {k[0] for k in ser}
    per = load_fills(slugs)
    allpts = []
    for slug, tr in per.items():
        allpts.extend(decisions(slug, legs_for(tr), ser))
    mk = {p["slug"] for p in allpts}
    have_book = [p for p in allpts if p.get("mint_sum") is not None]

    spec = {"features": ["mint_sum", "marg_pair_if_switch", "imbalance",
                         "move_since_prev_fill", "own_spread",
                         "comp_executable_price", "matched_fraction"],
            "bins": {"mint": [b[0] for b in MINT_BINS],
                     "marg": [b[0] for b in MARG_BINS],
                     "imb": [b[0] for b in IMB_BINS]},
            "scorer": "P(SWITCH) from the grouped-CV empirical cell",
            "cv": "leave-one-market-out over all 27 markets",
            "no_oos_claimed": True}
    fp = hashlib.sha256(json.dumps(spec, sort_keys=True)
                        .encode()).hexdigest()

    # ---- grouped leave-one-market-out scoring
    scored: List[Tuple[float, bool]] = []
    base = sum(1 for p in have_book
               if p["label"] == "SWITCH_OUTCOME") / max(1, len(have_book))
    for held in sorted(mk):
        tr = [p for p in have_book if p["slug"] != held]
        te = [p for p in have_book if p["slug"] == held]
        if not tr or not te:
            continue
        cell = collections.defaultdict(collections.Counter)
        for p in tr:
            cell[(p["mint_bin"], p["marg_switch_bin"],
                  p["imb_bin"])][p["label"]] += 1
        for p in te:
            c = cell.get((p["mint_bin"], p["marg_switch_bin"],
                          p["imb_bin"]))
            n = sum(c.values()) if c else 0
            s = (c.get("SWITCH_OUTCOME", 0) / n) if n >= 20 else base
            scored.append((s, p["label"] == "SWITCH_OUTCOME"))

    # precision/recall at the base-rate threshold (not tuned)
    thr = base
    tp = sum(1 for s, y in scored if s >= thr and y)
    fp_ = sum(1 for s, y in scored if s >= thr and not y)
    fn = sum(1 for s, y in scored if s < thr and y)
    prec = round(tp / (tp + fp_), 4) if (tp + fp_) else None
    rec = round(tp / (tp + fn), 4) if (tp + fn) else None

    res = {
        "label": "L5_CANDIDATE_V2_BOOK_CONDITIONED",
        "spec_fingerprint_sha256": fp,
        "book_source": {"zenodo_record": 22084977, "sha256": SHA,
                        "licence": "CC-BY-4.0"},
        "POWER_STATEMENT": (
            "27 markets / %d fills; fills per market max 390, median 13, "
            "min 1. The independent unit is the MARKET. A chronological "
            "70/30 split would leave ~9 OOS markets, which is NOT a "
            "defensible out-of-sample test, so NONE IS CLAIMED. Grouped "
            "leave-one-market-out CV is used instead and reported as such."
            % sum(len(v) for v in per.values())),
        "join": {"markets": len(mk), "decision_points": len(allpts),
                 "with_book_state": len(have_book),
                 "book_coverage_pct": round(100.0 * len(have_book)
                                            / len(allpts), 2)},
        "label_base_rates": {a: round(sum(1 for p in have_book
                                          if p["label"] == a)
                                      / len(have_book), 4)
                             for a in ACTIONS},
        "MINT_SUM_IS_FIRST_CLASS": monotone(have_book, "mint_bin",
                                            "bid(own)+bid(complement)"),
        "marginal_pair_if_switch": monotone(have_book, "marg_switch_bin",
                                            "vw_own + complement ask"),
        "inventory_imbalance": monotone(have_book, "imb_bin", "imbalance"),
        "SWITCH_discrimination_groupedCV": {
            "PR_AUC_average_precision": pr_auc(scored),
            "base_rate_switch": round(base, 4),
            "precision_at_base_rate_threshold": prec,
            "recall_at_base_rate_threshold": rec,
            "n_scored": len(scored),
            "note": ("threshold is the base rate, NOT tuned. PR-AUC at or "
                     "near the base rate means no discrimination."),
        },
        "v1_comparison": {
            "v1_OOS_switch_recall": 0.0,
            "v1_OOS_switch_predictions": "0 of 17,096",
            "v1_overall_accuracy": 0.5996,
            "always_extend_baseline": 0.6027,
        },
        "accounting": ("historical replay on third-party public data; no "
                       "order sent; not realized profit for our account."),
    }
    with open("analysis/l5/CANDIDATE_V2.json", "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=2, default=str)
    print(json.dumps(res, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
