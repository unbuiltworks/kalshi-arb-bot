#!/usr/bin/env python3
"""COMPLETE FILL CONVERSION FORENSICS -- read-only.

Reads the live order tape the engine already wrote, the post-cutover cohort
store, and L5's canonical public-fill sequence. Writes a ranked failure
decomposition, the L5 comparison, and an economic sensitivity.

Nothing here submits, prices, cancels, reprices or deploys.
"""
from __future__ import annotations

import csv
import gzip
import json
import sys
from collections import Counter, defaultdict
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src import l5_complete_conversion as cc            # noqa: E402
SESSIONS = ROOT / "analysis/l5/streak_shadow/live_sessions.jsonl.gz"
COHORT = ROOT / "analysis/l5/cross_shadow_live/cohort.jsonl"
L5SEQ = ROOT / "analysis/l5/l5_canonical_sequence.csv.gz"
BAND = ROOT / "analysis/l5/band_semantics/band_arms.json"
BRIDGE = ROOT / "analysis/l5/realized_bridge.json"
OUT = Path(__file__).resolve().parent / "complete_conversion.json"

# confirmed PMUS schedule (docs.polymarket.us/fees, fetched 2026-09-13)
MAKER_REBATE_RATE = D("0.0125")


def _f(x):
    try:
        return float(D(str(x)))
    except Exception:                                        # noqa: BLE001
        return None


def pctl(v, p):
    v = sorted(x for x in v if x is not None)
    return None if not v else v[min(len(v) - 1, int(p * (len(v) - 1)))]


def quant(v, name):
    v = sorted(x for x in v if x is not None)
    if not v:
        return {"n": 0}
    return {"n": len(v), "p10": pctl(v, .10), "p25": pctl(v, .25),
            "p50": pctl(v, .50), "p75": pctl(v, .75), "p90": pctl(v, .90),
            "max": v[-1], "label": name}


def load_sessions():
    out = []
    for line in gzip.open(SESSIONS, "rt"):
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def main() -> int:
    sessions = load_sessions()

    # ---- our clone: every COMPLETE attempt, in market order
    by_market = defaultdict(list)
    pair_econ = []
    for s in sessions:
        ds = s.get("decisions") or []
        slug = ds[0].get("slug") if ds else s.get("slug")
        pe = s.get("pair_economics")
        cs = [o for o in (s.get("orders") or [])
              if o.get("relation") == "complete"]
        if isinstance(pe, dict) and pe and cs:
            pair_econ.append((pe, any((_f(o.get("filled_qty")) or 0) > 0
                                      for o in cs)))
        for i, o in enumerate(cs):
            by_market[slug].append((str(s.get("_ts")), i, o))

    ticks = {}
    for slug, v in by_market.items():
        prices = []
        for _t, _i, o in v:
            b = o.get("recorder_book") or {}
            prices += [o.get("px"), o.get("l5_touch_px"), o.get("fresh_opp_ask"),
                       b.get("yes_bid"), b.get("yes_ask")]
        ticks[slug] = cc.infer_tick([o.get("tick") for _t, _i, o in v], prices)

    # the threshold is measured on this corpus, not assumed
    fill_lat = sorted(_f(o["rest_s"]) for v in by_market.values()
                      for _t, _i, o in v if (_f(o.get("filled_qty")) or 0) > 0)
    median_fill_latency = fill_lat[len(fill_lat) // 2]

    rows, modes, co_modes = [], Counter(), Counter()
    per_market = defaultdict(Counter)
    for slug, v in by_market.items():
        v.sort(key=lambda x: (x[0], x[1]))
        tick, prov = ticks[slug]
        prev = None
        for _t, _i, o in v:
            feat = cc.attempt_features(o, tick, prev)
            feat["market"] = slug
            feat["tick_provenance"] = prov
            verdict = cc.classify(feat, median_fill_latency)
            feat.update(verdict)
            rows.append(feat)
            if verdict["mode"]:
                modes[verdict["mode"]] += 1
                per_market[slug][verdict["mode"]] += 1
            for c in verdict["co_occurring"]:
                co_modes[c] += 1
            prev = o

    unfilled = [r for r in rows if D(str(r["filled_qty"])) == 0]
    filled = [r for r in rows if D(str(r["filled_qty"])) > 0]

    # ---- THE SPLIT: priced at our own touch vs below it
    split = {}
    for name, pred in (("priced_AT_our_touch",
                        lambda r: r["priced_below_our_touch"] is False),
                       ("priced_BELOW_our_touch",
                        lambda r: r["priced_below_our_touch"] is True)):
        v = [r for r in rows if pred(r)]
        live = [r for r in v if not r["rejected"]]
        fl = [r for r in live if D(str(r["filled_qty"])) > 0]
        split[name] = {
            "attempts": len(v), "rejected": len(v) - len(live),
            "rested": len(live), "fills": len(fl),
            "fill_rate_of_rested": round(len(fl) / len(live), 4) if live else None,
            "median_rest_s": pctl([r["rest_s"] for r in live], .5),
            "single_poll_rows": sum(1 for r in live
                                    if r["book_polls_while_resting"] == 1),
            "cancelled_to_requote": sum(1 for r in live
                                        if r["we_cancelled_to_requote"])}

    # ---- repeat-attempt structure
    seq_same_px = sum(1 for r in rows if r["repeat_same_price_as_previous"])
    seq_same_book = sum(1 for r in rows if r["repeat_same_book_as_previous"])
    seq_n = sum(1 for slug, v in by_market.items() for _ in v[1:])

    # ---- pair economics of sessions that filled vs did not
    pe_split = {}
    for name, want in (("session_FILLED", True), ("session_NO_FILL", False)):
        g = [p for p, got in pair_econ if got is want]
        pe_split[name] = {
            "sessions": len(g),
            "pair_at_chosen": quant([_f(p.get("pair_at_chosen")) for p in g],
                                    "pair_at_chosen"),
            "capped_by_pair_ceiling": sum(1 for p in g
                                          if p.get("capped_by_pair_ceiling")),
            "fee_safe": sum(1 for p in g if p.get("fee_safe"))}

    # ---- L5, public fills only
    l5_rows = []
    with gzip.open(L5SEQ, "rt") as fh:
        for d in csv.DictReader(fh):
            l5_rows.append(d)
    windows = defaultdict(list)
    for r in l5_rows:
        windows[r["window_id"]].append(r)
    agg = defaultdict(list)
    mk_gaps = []
    for w, v in windows.items():
        v.sort(key=lambda r: int(r["trade_seq"]))
        fills_ = [{"t": int(r["timestamp"]), "outcome": r["outcome"],
                   "price": r["price"], "quantity": r["quantity"]} for r in v]
        st = cc.l5_switch_process(fills_)
        for k in ("seconds_to_opposite_side_fill",
                  "seconds_between_same_side_fills", "same_side_run_lengths",
                  "switch_prices", "switch_clips",
                  "consecutive_fills_at_one_price"):
            agg[k] += st[k]
        prev = None
        for r in v:
            if prev is not None and r["outcome"] != prev["outcome"] \
                    and r["maker_taker"] == "maker":
                mk_gaps.append(float(int(r["timestamp"])
                                     - int(prev["timestamp"])))
            prev = r
    l5 = {k: quant(v, k) for k, v in agg.items()}
    l5["seconds_to_opposite_side_fill_MAKER_ONLY"] = quant(
        mk_gaps, "maker opposite-side fill latency")
    l5["maker_taker_mix"] = dict(Counter(r["maker_taker"] for r in l5_rows))
    l5["fills"] = len(l5_rows)
    l5["windows"] = len(windows)
    l5["NOT_OBSERVABLE"] = {
        "queue_position": "UNKNOWN", "cancels": "UNKNOWN",
        "reprices": "UNKNOWN", "resting_quotes": "UNKNOWN",
        "orders_that_never_filled": "UNKNOWN"}

    # ---- economic sensitivity
    band = json.loads(BAND.read_text())
    bridge = json.loads(BRIDGE.read_text())["bridge"]
    bridge_A = bridge["A_settlement_or_trading_pnl_usd"]
    sr = band["settlement_reconciliation"]
    settled_residual = D("0")
    last_inv = {}
    for s in sessions:
        ds = s.get("decisions") or []
        slug = ds[0].get("slug") if ds else s.get("slug")
        inv = s.get("inventory")
        if slug and isinstance(inv, dict) and inv:
            last_inv[slug] = inv
    settled_detail = {}
    for slug, rec in sr.items():
        inv = last_inv.get(slug) or {}
        up, dn = D(str(inv.get("up") or 0)), D(str(inv.get("dn") or 0))
        au = D(str(inv.get("avg_up") or 0))
        ad = D(str(inv.get("avg_dn") or 0))
        matched, resid = min(up, dn), abs(up - dn)
        rside = "up" if up > dn else ("dn" if dn > up else None)
        rcost = au if rside == "up" else ad
        rp = resid * ((D("1") if rside == rec["derived_winner"] else D("0"))
                      - rcost)
        settled_residual += rp
        settled_detail[slug] = {
            "matched_contracts": str(matched), "pair_cost": str(au + ad),
            "matched_margin_usd": str((matched * (D("1") - au - ad))
                                      .quantize(D("0.0001"))),
            "residual_contracts": str(resid),
            "residual_pnl_usd": str(rp.quantize(D("0.0001"))),
            "venue_realized_usd": rec["realized_usd"],
            "identity_check_usd": str(
                (matched * (D("1") - au - ad) + rp
                 - D(rec["realized_usd"])).quantize(D("0.0001")))}

    # the hard ceiling: a COMPLETE can only match inventory we already hold
    residual_avail = D("0")
    residual_by_market = {}
    for slug, inv in last_inv.items():
        up, dn = D(str(inv.get("up") or 0)), D(str(inv.get("dn") or 0))
        r = abs(up - dn)
        if r > 0:
            residual_by_market[slug] = str(r)
            residual_avail += r

    nofill_pairs = [_f(p.get("pair_at_chosen")) for p, got in pair_econ
                    if not got]
    marginal_pair = D(str(pctl(nofill_pairs, .5)))
    mean_qty = D(str(round(sum(_f(r["requested_qty"]) or 0 for r in unfilled)
                           / max(1, len(unfilled)), 4)))
    obs_rate = D(str(round(len(filled) / len(rows), 6)))
    rebate_pc = (MAKER_REBATE_RATE * D("0.5") * D("0.5")).quantize(D("0.0001"))
    sens = cc.sensitivity(
        unfilled_attempts=len(unfilled), mean_qty=mean_qty,
        marginal_pair_cost=marginal_pair,
        maker_rebate_per_contract=rebate_pc,
        residual_pnl_usd=settled_residual.quantize(D("0.0001")),
        residual_contracts_available=residual_avail,
        observed_fill_rate=obs_rate,
        targets=[D(x) for x in ("0.10", "0.20", "0.30", "0.50", "0.75",
                                "1.00")])
    # the OTHER axis: what pair cost would clear the loss if the residual
    # were completed in full
    settled_loss = -settled_residual if settled_residual < 0 else D("0")
    acct_loss = -D(bridge_A) if D(bridge_A) < 0 else D("0")
    pair_axis = {
        "to_cover_the_settled_residual_loss": {
            "loss_usd": str(settled_loss.quantize(D("0.0001"))),
            "residual_contracts": str(residual_avail),
            "required_pair_cost": str(cc.pair_cost_needed(
                residual_avail, settled_loss))},
        "to_cover_the_account_settlement_line": {
            "loss_usd": str(acct_loss),
            "residual_contracts": str(residual_avail),
            "required_pair_cost": str(cc.pair_cost_needed(
                residual_avail, acct_loss))},
        "pair_we_actually_quote_on_unfilled_attempts": str(marginal_pair)}


    out = {
        "scope": ("READ-ONLY forensics. No production change, no taker, no "
                  "band/cap/admission/sizing/concurrency change, 392b5c0e "
                  "still deferred."),
        "corpus": {
            "sessions": len(sessions),
            "complete_attempts": len(rows),
            "markets": len(by_market),
            "filled": len(filled), "unfilled": len(unfilled),
            "observed_fill_rate": str(obs_rate),
            "median_observed_fill_latency_s": median_fill_latency,
            "tick_by_market": {k: [str(v[0]), v[1]] for k, v in ticks.items()}},
        "ranked_failure_decomposition": [
            {"mode": m, "attempts": n,
             "share_of_attempts": round(n / len(rows), 4)}
            for m, n in modes.most_common()],
        "co_occurring_signals": dict(co_modes),
        "per_market": {k: dict(v) for k, v in per_market.items()},
        "THE_SPLIT": split,
        "repeat_attempt_structure": {
            "consecutive_attempts": seq_n,
            "same_price_as_previous": seq_same_px,
            "same_price_share": round(seq_same_px / seq_n, 4),
            "same_book_as_previous": seq_same_book},
        "pair_economics_filled_vs_not": pe_split,
        "l5_public_fill_process": l5,
        "economic_sensitivity": {
            "method": ("marginal, not average: the pair cost is taken from "
                       "the attempts that did NOT fill, because those are the "
                       "contracts a higher fill rate would add"),
            "marginal_pair_cost": str(marginal_pair),
            "mean_unfilled_qty_contracts": str(mean_qty),
            "maker_rebate_per_contract_accrual": str(rebate_pc),
            "settled_residual_pnl_usd": str(settled_residual.quantize(
                D("0.0001"))),
            "settled_markets": settled_detail,
            "residual_contracts_available": str(residual_avail),
            "residual_by_market": residual_by_market,
            "account_bridge_for_context": bridge,
            "arms": sens,
            "the_other_axis_pair_cost": pair_axis},
        "labels": {
            "not_realized": ("every sensitivity figure is arithmetic on "
                             "observed inputs, never realized P&L and never "
                             "an assumed fill"),
            "queue": ("queue position is NOT observable on either side and is "
                      "reported as UNKNOWN, never inferred")},
    }
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True, default=str)
                   + "\n")

    print(f"COMPLETE attempts {len(rows)}  filled {len(filled)} "
          f"({float(obs_rate):.2%})  median fill latency "
          f"{median_fill_latency:.2f}s\n")
    print("RANKED FAILURE DECOMPOSITION")
    for m, n in modes.most_common():
        print(f"  {n:4d}  {n/len(rows):6.1%}  {m}")
    print("\nTHE SPLIT")
    for k, v in split.items():
        print(f"  {k:24s} rested={v['rested']:3d} fills={v['fills']:2d} "
              f"rate={v['fill_rate_of_rested']} "
              f"med_rest={v['median_rest_s']}s "
              f"single_poll={v['single_poll_rows']}")
    print("\nL5 opposite-side fill latency (MAKER only): "
          f"{l5['seconds_to_opposite_side_fill_MAKER_ONLY']}")
    print(f"\nSENSITIVITY (residual available to match: "
          f"{residual_avail} contracts; marginal pair {marginal_pair})")
    for a in sens:
        print(f"  rate {a['complete_fill_rate']:>5} -> wants "
              f"{a['contracts_those_fills_would_want']:>8} contracts, can "
              f"match {a['extra_matched_contracts_capped_by_residual']:>6}, "
              f"margin ${a['matched_margin_usd']:>7}, total "
              f"${a['total_if_residual_unchanged_usd']:>8}  "
              f"positive={a['turns_positive']}")
    print("\nTHE OTHER AXIS -- pair cost, if the whole residual completed:")
    for k, v in pair_axis.items():
        print(f"  {k}: {v}")
    print(f"\nwrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
