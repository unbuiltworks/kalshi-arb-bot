"""P0-P3: entry band vs held-inventory band, three arms, two economics.

    python3 analysis/l5/band_semantics/run.py

SHADOW ONLY. Reads decisions the live engine already made and logged.
Submits nothing, changes nothing, imported by nothing armed.
"""
from __future__ import annotations

import gzip
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src import l5_band_semantics as bs            # noqa: E402

HERE = Path(__file__).resolve().parent
SESSIONS = ROOT / "analysis/l5/streak_shadow/live_sessions.jsonl.gz"
ROWS_OUT = HERE / "band_rows.json"
JSON_OUT = HERE / "band_arms.json"

# Venue-CONFIRMED realized numbers (settlement-only labelling), as the live
# engine logged them. A market absent here is UNSETTLED and contributes to
# no economics column.
REALIZED = {
    "aec-setkameua-sapole-salvas-2026-09-13": D("-1.0779"),
    "aec-nfl-buf-hou-2026-09-13": D("-2.5896"),
    "aec-mlb-lad-mia-2026-09-13": D("6.0034"),
}
CATEGORY = (("aec-mlb-", "baseball"), ("aec-nfl-", "football"),
            ("aec-cs2-", "esports"), ("aec-atp", "tennis"),
            ("aec-setka", "tennis"), ("aec-itf", "tennis"),
            ("atc-", "total"), ("asc-", "spread"), ("astatc-", "player_prop"),
            ("tsc-", "team_prop"), ("aec-", "moneyline"))


def _cat(slug: str) -> str:
    for p, c in CATEGORY:
        if slug.startswith(p):
            return c
    return "other"


def _ep(t: str) -> float:
    try:
        return datetime.fromisoformat(str(t)).timestamp()
    except Exception:                                        # noqa: BLE001
        return 0.0


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def load():
    sessions = [json.loads(l) for l in gzip.open(SESSIONS, "rt")]
    decisions, orders = [], []
    for s in sessions:
        ds = s.get("decisions") or []
        decisions.extend(ds)
        slug = ds[0].get("slug") if ds else None
        for o in (s.get("orders") or []):
            orders.append((slug, o))
    return decisions, orders


def main() -> int:
    decisions, orders = load()

    # --- where an actual EXTEND really filled, per market
    extend_fills = defaultdict(list)
    extend_stats = defaultdict(lambda: [0, 0])
    for slug, o in orders:
        if o.get("relation") != "extend":
            continue
        extend_stats[slug][0] += 1
        if _f(o.get("filled_qty")) > 0:
            extend_stats[slug][1] += 1
            # the order carries no timestamp; its chunk's decisions do, so
            # the market's own fill MOMENTS are recovered below from the
            # inventory deltas instead of guessed from order order
    by_market = defaultdict(list)
    for d in decisions:
        by_market[str(d.get("slug"))].append(d)
    for slug, ds in by_market.items():
        ds.sort(key=lambda d: (str(d.get("t_utc") or ""),
                               int(d.get("decision_idx") or 0)))
        prev = None
        for d in ds:
            inv = d.get("inventory_before") or {}
            cur = (_f(inv.get("up")), _f(inv.get("dn")))
            if prev is not None and (cur[0] > prev[0] + 1e-9
                                     or cur[1] > prev[1] + 1e-9):
                # inventory grew between these two decisions: SOMETHING
                # filled here. Whether it was an extend is settled by the
                # side that grew against the side already held.
                grew = "up" if cur[0] > prev[0] + 1e-9 else "dn"
                held = "up" if prev[0] > prev[1] else "dn"
                if grew == held and prev[0] + prev[1] > 0:
                    extend_fills[slug].append(_ep(d.get("t_utc")))
            prev = cur

    # --- P0: every decision the band refused
    rows = []
    for slug, ds in by_market.items():
        for d in ds:
            if "entry_band_left" not in (d.get("cap_binding") or []):
                continue
            eb = d.get("entry_band_left") or {}
            inv = d.get("inventory_before") or {}
            bid = bs._d(eb.get("bid"))
            held = bs._d(eb.get("held_inventory"), D("0")) or D("0")
            up, dn = bs._d(inv.get("up"), D("0")), bs._d(inv.get("dn"), D("0"))
            held_side = "up" if up > dn else "dn"
            spent = bs._d(inv.get("spent"), D("0"))
            sampled = d.get("acted_draw") or d.get("acted")
            px = bs.held_side_price(bid, held_side)
            size = bs._d((d.get("planned_safe_size") or {}).get("contracts"),
                         D("0")) or D("0")
            rr = {x["relation"]: x["p"] for x in (d.get("relation_ranked") or [])}
            t = _ep(d.get("t_utc"))
            rows.append({
                "decision_id": d.get("decision_id"),
                "market": slug, "category": _cat(slug),
                "t_utc": d.get("t_utc"),
                "concept": "ENTRY_BAND" if held <= 0 else "HELD_INVENTORY_BAND",
                "tte_bucket": str(d.get("state") or "||").split("|")[1],
                "state": d.get("state"),
                "bid": str(bid), "distance_outside_band": str(
                    bs.distance_outside(bid)),
                "held_side": held_side, "held_qty": str(max(up, dn)),
                "held_avg_cost": (inv.get("avg_up") if held_side == "up"
                                  else inv.get("avg_dn")),
                "matched_qty": inv.get("matched"),
                "residual_qty": inv.get("residual"),
                "residual_usd": inv.get("unmatched_usd"),
                "spent": str(spent),
                "sampled_probabilities": rr,
                "sampled": sampled,
                "shell_action": d.get("acted"),
                "shell_outcome": eb.get("outcome"),
                "planned_contracts": str(size),
                "held_side_price_proxy": str(px),
                "tape_supports_an_extend_fill": bs.tape_support(
                    t, slug, extend_fills),
                "t_epoch": t,
            })
    rows.sort(key=lambda r: (r["market"], r["t_utc"]))

    # --- settled winners, derived from OUR confirmed realized numbers
    winners, recon = {}, {}
    for slug, ds in by_market.items():
        if slug not in REALIZED:
            continue
        last = ds[-1].get("inventory_before") or {}
        up, dn = bs._d(last.get("up"), D("0")), bs._d(last.get("dn"), D("0"))
        spent = bs._d(last.get("spent"), D("0"))
        w = bs.winning_side(REALIZED[slug], up, dn, spent)
        winners[slug] = w
        recon[slug] = {"realized_usd": str(REALIZED[slug]),
                       "final_up": str(up), "final_dn": str(dn),
                       "spent": str(spent), "derived_winner": w,
                       "check_up": str(up - spent), "check_dn": str(dn - spent)}

    # --- P1 + P2: the arms, simulated SEQUENTIALLY with every other gate
    #     still in force. A counterfactual extend that fills MOVES the
    #     inventory, and the residual cap then adjudicates the next one --
    #     which is the difference between an arm and a wish. No completion
    #     is ever imputed on top of a counterfactual fill, so the shadow
    #     residual only ever grows and the cap binds permanently once hit.
    units = {}
    for slug, ds in by_market.items():
        for d in ds:
            u = bs._d(d.get("behavioral_unit"))
            if u:
                units[slug] = u
                break

    def simulate(arm: str, assume_fills: bool) -> dict:
        per_market, log = {}, []
        for slug, ds in by_market.items():
            extra_q = D("0")          # shadow contracts added on the held side
            extra_usd = D("0")
            opp = filled = blocked_resid = blocked_spend = blocked_band = 0
            for d in ds:
                if "entry_band_left" not in (d.get("cap_binding") or []):
                    continue
                r = next((x for x in rows
                          if x["decision_id"] == d.get("decision_id")), None)
                if r is None or r["concept"] != "HELD_INVENTORY_BAND":
                    continue
                if r["sampled"] not in ("extend", "open"):
                    continue
                opp += 1
                if not bs.admits(arm, bid=D(r["bid"]), held=D(r["held_qty"]),
                                 sampled=r["sampled"]):
                    blocked_band += 1
                    continue
                inv = d.get("inventory_before") or {}
                resid = bs._d(inv.get("residual"), D("0")) or D("0")
                spent = bs._d(inv.get("spent"), D("0")) or D("0")
                q = D(r["planned_contracts"])
                px = D(r["held_side_price_proxy"])
                # the shadow residual: our extra contracts are on the side
                # already held, so they push |residual| further out
                sign = D("1") if resid >= 0 else D("-1")
                shadow_resid = resid + sign * extra_q + sign * q
                if not bs.residual_cap_allows(shadow_resid, units.get(slug)):
                    blocked_resid += 1
                    continue
                if not bs.spend_cap_allows(spent, extra_usd + q * px):
                    blocked_spend += 1
                    continue
                if not (assume_fills or r["tape_supports_an_extend_fill"]):
                    continue
                extra_q += q
                extra_usd += q * px
                filled += 1
                log.append({"market": slug, "t_utc": r["t_utc"],
                            "contracts": str(q), "price_proxy": str(px)})
            if opp:
                w = winners.get(slug)
                pnl = bs.settlement_value(extra_q, (extra_usd / extra_q)
                                          if extra_q else D("0"),
                                          rows_side.get(slug, "dn"), w)
                per_market[slug] = {
                    "opportunities": opp, "shadow_fills": filled,
                    "blocked_by_band": blocked_band,
                    "blocked_by_residual_cap": blocked_resid,
                    "blocked_by_spend_cap": blocked_spend,
                    "extra_contracts": str(extra_q),
                    "extra_capital_usd": str(extra_usd.quantize(D("0.0001"))),
                    "avg_price_proxy": str((extra_usd / extra_q).quantize(
                        D("0.0001"))) if extra_q else None,
                    "winner": w,
                    "settlement_pnl_delta":
                        str(pnl.quantize(D("0.0001"))) if pnl is not None
                        else "UNSETTLED"}
        total = sum((D(v["settlement_pnl_delta"])
                     for v in per_market.values()
                     if v["settlement_pnl_delta"] != "UNSETTLED"), D("0"))
        return {"by_market": per_market,
                "total_settlement_pnl_delta_usd": str(total.quantize(D("0.0001"))),
                "unsettled_markets": sorted(
                    k for k, v in per_market.items()
                    if v["settlement_pnl_delta"] == "UNSETTLED"),
                "shadow_fills": sum(v["shadow_fills"] for v in per_market.values()),
                "extra_capital_usd": str(sum(
                    (D(v["extra_capital_usd"]) for v in per_market.values()),
                    D("0")))}

    rows_side = {}
    for r in rows:
        rows_side.setdefault(r["market"], r["held_side"])

    arms = {}
    for arm in bs.ARMS:
        sup = simulate(arm, assume_fills=False)
        asm = simulate(arm, assume_fills=True)
        arms[arm] = {
            "TAPE_SUPPORTED": sup,
            "FILL_ASSUMED_upper_bound": asm,
            # THE DECOMPOSITION THE OWNER ASKED FOR, and the honest shape
            # of it: because no COMPLETE may be imputed after a
            # counterfactual EXTEND, every arm adds directional residual
            # and ZERO matched inventory. Its entire P&L is directional.
            "economics_decomposition": {
                "matched_pair_pnl_delta": "0.0000 (by construction: no "
                                          "completion is imputed on top of "
                                          "a counterfactual fill)",
                "matched_inventory_produced": "0 contracts, same reason",
                "eventual_complete_opportunities": "UNCHANGED -- the arms "
                                                   "alter only whether an "
                                                   "EXTEND is attempted",
                "eventual_complete_fills": bs.UNKNOWN,
                "directional_residual_pnl_delta_usd": {
                    "TAPE_SUPPORTED": sup["total_settlement_pnl_delta_usd"],
                    "FILL_ASSUMED": asm["total_settlement_pnl_delta_usd"]},
                "terminal_residual_frequency": "1.00 -- every counterfactual "
                                               "fill is still held at "
                                               "settlement",
                "confirmed_incentives": "NOT ATTRIBUTABLE: PMUS credits "
                                        "rewards at account level with an "
                                        "empty conditionId",
                "fees": bs.UNKNOWN,
                "net_realized": "the directional line only; nothing else in "
                                "this table is a venue number",
            },
        }

    # --- P3: path dependence, one row per shell-forced EXTEND -> COMPLETE
    forced = [r for r in rows if r["sampled"] == "extend"
              and r["shell_action"] == "complete"]
    path = []
    for r in forced:
        w = winners.get(r["market"])
        q, px = D(r["planned_contracts"]), D(r["held_side_price_proxy"])
        path.append({
            "decision_id": r["decision_id"], "market": r["market"],
            "t_utc": r["t_utc"],
            "actual": {"action": "complete", "filled": False,
                       "note": "the forced completion is joined to its order "
                               "in live_rows; fills are counted there"},
            "shadow_extend": {
                "side": r["held_side"],
                "price_proxy": str(px),
                "contracts": str(q),
                "OBSERVED_tape_supports_a_fill":
                    r["tape_supports_an_extend_fill"],
                "UNKNOWN": ("book and order behaviour AFTER a hypothetical "
                            "fill; no completion is imputed on top of it"),
                "settlement_if_filled": (
                    str(bs.settlement_value(q, px, r["held_side"], w))
                    if w else "UNSETTLED")},
        })

    doc = {
        "labels": "SHADOW ONLY; nothing submitted, nothing repriced, "
                  "production unchanged",
        "bands": {
            "entry_band_applies_when": "flat (up+dn == 0)",
            "legal_price_band": f"{bs.LEGAL_LO}-{bs.LEGAL_HI}",
            "observation_band_flat_only": f"{bs.OBS_LO}-{bs.OBS_HI}",
            "held_inventory_is_gated_only_by": "the legal-price band",
        },
        "corpus": {
            "decisions": len(decisions),
            "markets": len(by_market),
            "band_firings": len(rows),
            "by_concept": dict(Counter(r["concept"] for r in rows)),
            "by_market": dict(Counter(r["market"] for r in rows)),
            "by_sampled_action": dict(Counter(r["sampled"] for r in rows)),
            "bid_position": {
                "below_legal_lo": sum(1 for r in rows
                                      if D(r["bid"]) < bs.LEGAL_LO),
                "above_legal_hi": sum(1 for r in rows
                                      if D(r["bid"]) > bs.LEGAL_HI),
                "inside_legal_band": sum(
                    1 for r in rows
                    if bs.LEGAL_LO <= D(r["bid"]) <= bs.LEGAL_HI)},
        },
        "observed_extend_fill_rates": {
            k: {"orders": v[0], "filled": v[1],
                "rate": round(v[1] / v[0], 4) if v[0] else None}
            for k, v in sorted(extend_stats.items(), key=lambda kv: -kv[1][0])},
        "settlement_reconciliation": recon,
        "arms": arms,
        "path_dependence": path,
    }
    ROWS_OUT.write_text(json.dumps(rows, indent=1, sort_keys=True))
    JSON_OUT.write_text(json.dumps(doc, indent=1, sort_keys=True))
    print(json.dumps({k: v for k, v in doc.items()
                      if k != "path_dependence"}, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
