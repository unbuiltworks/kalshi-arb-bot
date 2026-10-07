"""Run the H-TURN mechanism wherever two-token book evidence actually exists.

WHERE THE BOOK COMES FROM, AND WHAT THAT COSTS US.

The decisive artifact `FORENSIC.md` §4 asks for -- a per-second top-of-book
series for BOTH outcome tokens over the canonical scorecard's own windows --
DOES NOT EXIST. The only two-token L2 on disk is Zenodo record 22084977
(`data/zenodo_22084977/`, 1 s full ladders, 2026-08-21..24) and it overlaps the
canonical sequence in ZERO windows. A realtime two-token collector is being
built separately (`analysis/l5/book_shadow/`, untouched here).

So the mechanism is run on every window where BOTH tokens' books and L5's own
fills exist at once, across all four families the archive covers -- BTC15m,
ETH15m, BTC5m, ETH5m, because `CLAUDE.md` requires the engine to stay
market-agnostic -- and N is stated honestly, small as it is. L5's fills come
from the SAME corpus the canonical sequence is built from
(`data/l5/raw/activity*.jsonl*`), joined by `slug` + `outcome`, never by name.

REGIME. Every evaluable window falls on 2026-08-21..22, i.e. entirely inside
R4 = REGIME_2026-08-19_ON (`analysis/l5/canon_regimes.json`). There are ZERO
evaluable rows in R1, R2 or R3. The pooled figure IS the R4 figure; it is not
current expectancy for anything else and is not advertised as such.

SHADOW. Nothing here places, signs, previews, amends or cancels an order. The
blocking `mutation_incapable` gate runs first and the process exits non-zero
if it fails.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import os
import statistics
import sys
from decimal import Decimal as D
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from analysis.l5.hturn_shadow import mechanism as M

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_JSON = os.path.join(HERE, "MECHANISM.json")
PARQUETS = ("data/zenodo_22084977/updown_15m.parquet",
            "data/zenodo_22084977/updown_5m.parquet")
ACTIVITY = (("data/l5/raw/activity_gap.jsonl.gz", gzip.open),
            ("data/l5/raw/activity.jsonl", open))

REGIME_LABEL = "REGIME_2026-08-19_ON"


# ------------------------------------------------------------------- loading
def load_books(paths: Sequence[str] = PARQUETS
               ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """(books_by_slug_outcome, meta_by_slug). Both outcomes or nothing."""
    import pyarrow.parquet as pq
    from analysis.l5.historical_l2.bbo_offset import Book
    books: Dict[str, Dict[str, Any]] = collections.defaultdict(dict)
    meta: Dict[str, Dict[str, Any]] = {}
    cols = ["market_slug", "coin", "contract_length", "outcome", "token_id",
            "captured_at", "seconds_to_close", "bid_prices", "bid_sizes",
            "ask_prices", "ask_sizes", "best_bid", "best_ask"]
    for p in paths:
        if not os.path.exists(p):
            continue
        d = pq.read_table(p, columns=cols).to_pydict()
        n = len(d["market_slug"])
        for i in range(n):
            slug, out = d["market_slug"][i], d["outcome"][i]
            ts = d["captured_at"][i].timestamp()
            row = {"bid_prices": d["bid_prices"][i],
                   "bid_sizes": d["bid_sizes"][i],
                   "ask_prices": d["ask_prices"][i],
                   "ask_sizes": d["ask_sizes"][i],
                   "best_bid": d["best_bid"][i], "best_ask": d["best_ask"][i],
                   "seconds_to_close": d["seconds_to_close"][i]}
            bk = books[slug].get(out)
            if bk is None:
                bk = books[slug][out] = Book()
            bk.add(ts, row)
            meta.setdefault(slug, {"coin": d["coin"][i],
                                   "contract_length": d["contract_length"][i],
                                   "token_ids": {}})
            meta[slug]["token_ids"][out] = str(d["token_id"][i])
    for slug in books:
        for bk in books[slug].values():
            bk.sort()
    return dict(books), meta


def load_l5_fills(slugs: Iterable[str]) -> Dict[str, List[Dict[str, Any]]]:
    """L5's public TRADE fills for these slugs, from the canonical corpus.

    Identity is provenance: these files are L5's own activity pulls
    (`analysis/l5/canon_regimes.json :: identity_rule`). Nothing is matched by
    display name. De-duplicated on (tx, slug, outcome, side, price, size).
    """
    want = set(slugs)
    out: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    seen = set()
    for path, opener in ACTIVITY:
        if not os.path.exists(path):
            continue
        with opener(path, "rt") as fh:
            for line in fh:
                try:
                    env = json.loads(line)
                except ValueError:
                    continue
                for r in (env.get("payload") or []):
                    if r.get("type") != "TRADE":
                        continue
                    slug = r.get("slug")
                    if slug not in want:
                        continue
                    key = (r.get("transactionHash"), slug, r.get("outcome"),
                           r.get("side"), r.get("price"), r.get("size"),
                           r.get("timestamp"))
                    if key in seen:
                        continue
                    seen.add(key)
                    out[slug].append({
                        "t": float(r["timestamp"]), "outcome": r["outcome"],
                        "side": r.get("side", "BUY"),
                        "price": r["price"], "size": r["size"],
                        "usdcSize": r.get("usdcSize"),
                        "tx": r.get("transactionHash")})
    for slug in out:
        out[slug].sort(key=lambda f: f["t"])
        for i, f in enumerate(out[slug]):
            f["_i"] = i
    return dict(out)


# ------------------------------------------------------------------ reporting
def _p(xs: Sequence[float], q: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return round(s[i], 3)


def summarise(windows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate the TURN-anchored comparison. Behaviour first, P&L nowhere.

    There is no aggregate P&L in this summary at all, by choice. `CLAUDE.md`'s
    validation standard says positive aggregate replay P&L with materially
    different behaviour is not evidence of replication, and this mechanism's
    legs are not filled, not settled and not realized -- so the only honest
    numbers are behavioural ones plus the pair cost the mechanism would have
    locked IF a resting bid had been hit.
    """
    turns = [t for w in windows for t in w["turns"]]
    status = collections.Counter(t["status"] for t in turns)
    n = len(turns)
    ev = [t for t in turns if t["status"] == M.EVALUABLE]
    hit = [t for t in ev if t["shadow_turn_t"] is not None]
    late = [t for t in ev if t["shadow_turn_t"] is None
            and t["shadow_turn_t_by_next_run"] is not None]
    deltas = [t["turn_timing_delta_s"] for t in hit]
    deltas_h = [t["turn_timing_delta_s_by_next_run"] for t in ev
                if t["turn_timing_delta_s_by_next_run"] is not None]
    pxd = [float(t["price_delta"]) for t in ev if t["price_delta"] is not None]
    pair_if = [float(t["shadow_pair_cost_if_hit"]) for t in ev
               if t["shadow_pair_cost_if_hit"] is not None]
    pair_sh = [float(t["shadow_pair_cost_running"]) for t in hit
               if t["shadow_pair_cost_running"] is not None]
    pair_l5 = [float(t["l5_pair_cost_running"]) for t in turns
               if t["l5_pair_cost_running"] is not None]
    inv = [w["inventory_comparison"] for w in windows]

    def flt(key):
        return [float(x[key]) for x in inv if x.get(key) is not None]

    return {
        "label": M.LABEL,
        "n_windows": len(windows),
        "n_l5_fills": sum(w["n_l5_fills"] for w in windows),
        "n_l5_turns": n,
        "turn_status": dict(status),
        "unevaluable_fraction": (round(sum(v for k, v in status.items()
                                           if k in M.UNEVALUABLE_LABELS) / n, 4)
                                 if n else None),
        "evaluable_turns": len(ev),
        "side": {
            "agrees": sum(1 for t in ev if t["side_agrees"]),
            "disagrees": sum(1 for t in ev if t["side_agrees"] is False),
            "note": ("side agreement is BY CONSTRUCTION -- the mechanism rests "
                     "the complement of its own surplus -- and is reported as "
                     "a construction check, never as a result")},
        "fill_timing": {
            "shadow_hit_inside_the_run": len(hit),
            "shadow_hit_only_after_the_run_started_again": len(late),
            "shadow_never_hit": len(ev) - len(hit) - len(late),
            "hit_rate_given_evaluable": (round(len(hit) / len(ev), 4)
                                         if ev else None),
            "delta_s_inside_run": {
                "n": len(deltas), "p10": _p(deltas, 0.10),
                "median": _p(deltas, 0.50), "p90": _p(deltas, 0.90),
                "shadow_earlier_or_same": sum(1 for d in deltas if d <= 0),
                "shadow_later": sum(1 for d in deltas if d > 0),
                "within_1s": sum(1 for d in deltas if abs(d) <= 1),
                "within_5s": sum(1 for d in deltas if abs(d) <= 5)},
            "delta_s_to_next_run": {
                "n": len(deltas_h), "median": _p(deltas_h, 0.50),
                "p90": _p(deltas_h, 0.90)},
            "reading": ("delta < 0 means the mechanism's resting complement was "
                        "hit BEFORE L5's own turn fill. A turn the mechanism "
                        "never gets hit on is a MISS and is counted as one")},
        "price": {
            "n": len(pxd), "median_delta": _p(pxd, 0.50),
            "p10_delta": _p(pxd, 0.10), "p90_delta": _p(pxd, 0.90),
            "shadow_strictly_cheaper": sum(1 for x in pxd if x < 0),
            "shadow_at_or_above_l5": sum(1 for x in pxd if x >= 0),
            "reading": ("shadow complement limit price minus L5's own complete "
                        "price, both in the complement outcome's own terms")},
        "pair_cost": {
            "shadow_if_hit_n": len(pair_if),
            "shadow_if_hit_median": _p(pair_if, 0.50),
            "shadow_if_hit_max": max(pair_if) if pair_if else None,
            "shadow_if_hit_frac_le_ceiling": (
                round(sum(1 for x in pair_if
                          if D(str(round(x, 6))) <= M.pair_ceiling())
                      / len(pair_if), 4) if pair_if else None),
            "shadow_realised_basis_median": _p(pair_sh, 0.50),
            "l5_running_median": _p(pair_l5, 0.50),
            "note": ("the ceiling check is a CONSTRUCTION invariant: the "
                     "mechanism cannot quote above it. L5's own running pair "
                     "cost is a MEASUREMENT of him. Neither is realized P&L "
                     "for our account")},
        "inventory_trajectory": {
            "l5_mean_abs_residual_median": _p(
                flt("l5_mean_abs_residual_time_weighted"), 0.50),
            "shadow_mean_abs_residual_median": _p(
                flt("shadow_mean_abs_residual_time_weighted"), 0.50),
            "l5_peak_abs_residual_median": _p(flt("l5_peak_abs_residual"), 0.50),
            "shadow_peak_abs_residual_median": _p(
                flt("shadow_peak_abs_residual"), 0.50),
            "l5_residual_end_median": _p(flt("l5_residual_end"), 0.50),
            "shadow_residual_end_median": _p(flt("shadow_residual_end"), 0.50),
            "l5_matched_qty_total": sum(flt("l5_matched_qty")),
            "shadow_matched_qty_total": sum(flt("shadow_matched_qty")),
            "note": "SHADOW residual. Not submitted, filled, settled, realized"},
        "partial_fills": {
            "shadow_complement_fills": sum(w["n_shadow_complement_fills"]
                                           for w in windows),
            "of_which_partial": sum(w["n_shadow_partial_fills"]
                                    for w in windows),
            "note": ("inventory is booked on every one of these, partial "
                     "included; a partial leaves the remainder resting")},
        "mirror_dependency": {
            "turns_whose_run_had_no_post_only_achievable_arrival":
                status.get(M.U_MIRROR, 0),
            "turns_with_any_non_post_only_arrival":
                sum(1 for t in turns
                    if t["arrivals_not_post_only_achievable"]),
            "why": ("26.4% of L5's BTC15m maker fills price at or above the "
                    "prevailing ask -- a complete-set mint a resting bid "
                    "cannot do (ZENODO_OVERLAP_FINDINGS.md §2)")},
        "NEVER": list(M.NEVER),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT_JSON)
    ap.add_argument("--fee-allowance", default=str(M.FEE_ALLOWANCE_PER_SHARE))
    ap.add_argument("--max-windows", type=int, default=0)
    ap.add_argument("--rows", action="store_true",
                    help="keep the per-action rows in the output")
    a = ap.parse_args(argv)

    M.install_mutation_barrier()
    gate = M.mutation_incapable_gate()
    print("%-26s %s  %s" % ("mutation_incapable",
                            "PASS" if gate["pass"] else "FAIL", gate["detail"]),
          flush=True)
    if not gate["pass"]:
        print("BLOCKING GATE FAILED -- shadow refused", flush=True)
        return 1

    from analysis.l5.hturn_shadow import shadow as S

    books, meta = load_books()
    fills = load_l5_fills(books.keys())
    slugs = sorted(s for s in fills
                   if len(books.get(s) or {}) == 2 and fills[s])
    if a.max_windows:
        slugs = slugs[:a.max_windows]

    arms: Dict[str, Any] = {}
    for arm_name, allowance in (("fee_allowance_measured_max",
                                 D(str(a.fee_allowance))),
                                ("fee_allowance_zero_sensitivity", D("0"))):
        windows = []
        for slug in slugs:
            w = S.replay_window(slug, fills[slug], books[slug],
                                fee_allowance=allowance)
            w["family"] = "%s%s" % (meta[slug]["coin"],
                                    meta[slug]["contract_length"])
            if not a.rows:
                for k in ("l5_actions", "shadow_trajectory", "l5_trajectory"):
                    w.pop(k, None)
            windows.append(w)
        by_family: Dict[str, Any] = {}
        for fam in sorted({w["family"] for w in windows}):
            by_family[fam] = summarise([w for w in windows
                                        if w["family"] == fam])
        arms[arm_name] = {"fee_allowance_per_share": str(allowance),
                          "pooled_is_R4_only": summarise(windows),
                          "by_family": by_family,
                          "windows": windows}

    out = {
        "generated_utc": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(),
        "label": M.LABEL,
        "hypothesis": "H-TURN as a MECHANISM: rest the fee-safe complement "
                      "while extending and let FILLS determine the turn. Not "
                      "a classifier, not a prediction.",
        "gates": {"mutation_incapable": gate},
        "economics": {
            "pair_ceiling": str(M.pair_ceiling()),
            "pair_ceiling_source": "src.l5_shadow.two_sided."
                                   "ADAPTED_EXECUTABLE_PAIR_CEILING",
            "near_par_tolerance": "LOCKED",
            "maker_first_post_only": True,
            "rebates_credited_usd": 0,
            "rewards_credited_usd": 0,
            "fee_allowance_source": "analysis/l5/POLYMARKET_FEE_SOURCES.json "
                                    "fee_per_contract_fee_bearing.max",
        },
        "book_provenance": {
            "dataset": "Zenodo 22084977 updown_15m/5m.parquet, 1s full "
                       "ladders, 2026-08-21..24, CC-BY-4.0",
            "overlap_with_canonical_scorecard_windows": 0,
            "decisive_artifact_exists": False,
            "why": "FORENSIC.md §4 asks for both tokens' top-of-book over the "
                   "canonical sequence's own windows (2026-03-27..04-21, "
                   "2026-08-28..31). No such series is on disk.",
            "anonymous_depth": "every level is SIZE AT A PRICE and is never "
                               "attributed to L5 or any trader",
        },
        "regime": {
            "all_evaluable_rows_fall_in": REGIME_LABEL,
            "rows_in_R1_R2_R3": 0,
            "note": "the pooled figure IS the R4 figure. L5's ex-incentive "
                    "baseline is NEGATIVE in R4 (-$5,851, -30.4bp), so no "
                    "pooled number here is advertised as current expectancy.",
        },
        "arms": arms,
        "NEVER": list(M.NEVER),
    }
    with open(a.out, "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    s = arms["fee_allowance_measured_max"]["pooled_is_R4_only"]
    print(json.dumps({k: s[k] for k in
                      ("n_windows", "n_l5_fills", "n_episodes",
                       "episode_status", "unevaluable_fraction",
                       "evaluable_episodes", "shadow_turned",
                       "comparable_turns_both_ledgers",
                       "turn_timing_delta_s", "pair_cost")},
                     indent=1, default=str), flush=True)
    print("wrote %s" % a.out, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
