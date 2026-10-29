"""OWNER 5569845340 A -- the L5 economic scoreboard for every market this
account has settled, from the venue's own settlement rows.

    realized expectancy = matched-pair economics + venue-credited maker
    rebates + venue-credited liquidity rewards - residual losses - fees

Every term is measured from executable/filled/settled evidence:
  * the probe's settlement audit rows (kind "settlement", settled true) --
    the venue's trade prints (time, YES-terms price, qty), the venue's
    realized number when it emitted a POSITION_RESOLUTION, the DERIVED
    breakdown (bought/avg/cost basis/matched pairs/pair cost/contribution/
    residual), and the resolution time;
  * the venue's own per-print maker-rebate credits, unioned across every
    incentive audit (analysis/l5/venue_rebate_credits.json);
  * liquidity rewards: zero unless the venue credited one (none so far);
  * fees: the venue charged $0.00 on every print (maker only).

Capital-hours are computed two ways and labelled: 'first_fill_bound' =
cost basis x hours from first fill to resolution (an upper bound), and
'fill_weighted' = sum over prints of (print notional x hours from that
print to resolution) where the print's own side is known.

Cohort labels come from the safety_shell_version of the decisions logged
for the market (R1 / R2C / R2C2 / R3L / R4); a market whose decisions were
not captured is labelled by the fill date. SUBMITTED / FILLED / SETTLED /
REALIZED stay separate: 'realized_usd_venue' is the venue's number and is
null on a flat account; 'net_usd' = venue realized (or, on a flat account,
the derived matched contribution) + credited rebates - fees, labelled by
which it is.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal as D
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

OUT_PATH = os.path.join("analysis", "l5", "l5_economic_scoreboard.json")
REBATES_PATH = os.path.join("analysis", "l5", "venue_rebate_credits.json")
PREFIX = "PMUS_L5_CAMPAIGN "

CATEGORY = {
    "atp": "tennis", "atpcq": "tennis", "wta": "tennis", "itfme": "tennis",
    "itfwo": "tennis", "setkamecz": "tennis", "setkameua": "tennis", "setkawoua": "tennis",
    "cfb": "football", "nfl": "football", "mlb": "baseball",
    "cs2": "esports", "lol": "esports", "r6": "esports", "dota2": "esports",
    "lal": "soccer", "epl": "soccer", "tsl": "soccer", "bra": "soccer", "lgscup": "soccer",
    "lmx": "soccer",
}
SHAPE = {"aec": "match_winner", "tsc": "total", "asc": "spread", "atc": "three_way",
         "astatc": "player_prop", "aqc": "qualifier"}


def _f(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _iso(s: Optional[str]) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def category(slug: str) -> tuple:
    parts = slug.split("-")
    shape = SHAPE.get(parts[0], parts[0]) if parts else "?"
    league = parts[1] if len(parts) > 1 else "?"
    return shape, league, CATEGORY.get(league, "other")


def _events(paths: Iterable[str]):
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        for e in d.get("deploy") or []:
            m = e.get("message") or ""
            if not m.startswith(PREFIX):
                continue
            try:
                j = json.loads(m[len(PREFIX):])
            except json.JSONDecodeError:
                continue
            yield e.get("timestamp") or "", j


def _expand(paths: Sequence[str]) -> List[str]:
    out: List[str] = []
    for p in paths:
        if os.path.isdir(p):
            out.extend(os.path.join(p, n) for n in sorted(os.listdir(p))
                       if n.endswith(".txt") or n.endswith(".json"))
        else:
            out.append(p)
    return out


def harvest(paths: Sequence[str]) -> dict:
    """Settled settlement rows (latest per slug), cohort shells per slug,
    and per-order fills with their own side where sessions were captured."""
    settlements: Dict[str, dict] = {}
    shells: Dict[str, Counter] = defaultdict(Counter)
    fills: Dict[str, Dict[str, dict]] = defaultdict(dict)
    campaign_starts: Dict[str, str] = {}
    venue_settled: Dict[str, dict] = {}
    for ts, j in _events(_expand(paths)):
        kind = j.get("kind")
        if kind == "settlement" and j.get("settled"):
            slug = j.get("slug")
            if slug and (slug not in settlements or ts > settlements[slug]["log_ts"]):
                settlements[slug] = dict(j, log_ts=ts)
        elif kind in ("campaign_start", "campaign_continuation_start"):
            cid = j.get("campaign_id") or (j.get("approval") or {}).get("campaign_id")
            if cid and (cid not in campaign_starts or ts < campaign_starts[cid]):
                campaign_starts[cid] = ts
        elif kind == "session_end":
            slug = j.get("slug")
            for d in j.get("decisions") or []:
                sh = str(d.get("safety_shell_version") or "").split("/")[0]
                if sh:
                    shells[slug][sh] += 1
            for o in j.get("orders") or []:
                if _f(o.get("filled_qty")) > 0 and o.get("order_id"):
                    fills[slug][o["order_id"]] = {"side": o.get("side"), "px": o.get("px"),
                                                  "qty": o.get("filled_qty"), "log_ts": ts}
        pf = ((j.get("preflight") or {}).get("checks") or {}).get("venue_settled") or {}
        for slug, v in (pf.get("markets") or {}).items():
            if v.get("settled") and v.get("realized_confirmed") and v.get("realized_usd") is not None:
                venue_settled[slug] = dict(v, log_ts=ts)
    return {"settlements": settlements,
            "cohorts": {s: c.most_common(1)[0][0] for s, c in shells.items()},
            "fills": {s: list(v.values()) for s, v in fills.items()},
            "campaign_starts": campaign_starts,
            "venue_settled": venue_settled}


def cohort_by_timeline(t_first: Optional[float], campaign_starts: Mapping[str, str]) -> Optional[str]:
    """Cohort of a market whose decisions were not captured: the campaign
    that had started most recently before its first print. Before the first
    logged campaign start everything is R1 (R1-INITIAL)."""
    if t_first is None:
        return None
    best, best_t = "R1", None
    for cid, ts in campaign_starts.items():
        t = _iso(ts)
        if t is not None and t <= t_first and (best_t is None or t > best_t):
            best, best_t = cid.split("-RUN")[0].split("-EVIDENCE")[0].split("-INITIAL")[0], t
    return best


def resolution_time(s: Mapping[str, Any], t_last: Optional[float]) -> Optional[float]:
    """When capital came back. POSITION_RESOLUTION.updateTime when the venue
    emitted one; on a flat account the public settlement-set time only if it
    is after the last print (the venue re-uses that field for pre-game
    reference prices), else the audit row's own time, an upper bound."""
    res = s.get("resolution") or {}
    t = _iso(res.get("updateTime"))
    if t is not None:
        return t
    t_pub = _iso(res.get("public_settlement_set_time"))
    if t_pub is not None and (t_last is None or t_pub >= t_last):
        return t_pub
    return _iso(s.get("log_ts"))


def load_rebates(path: str = REBATES_PATH) -> dict:
    if not os.path.exists(path):
        return {"rows": [], "by_market": {}}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def market_row(slug: str, s: Mapping[str, Any], cohort: Optional[str],
               rebates_by_market: Mapping[str, Any], own_fills: Sequence[dict]) -> dict:
    bd = s.get("breakdown") or {}
    trades = ((s.get("trades") or {}).get("rows") or [])
    res = s.get("resolution") or {}
    times = [_iso(r.get("t")) for r in trades if _iso(r.get("t"))]
    t_first, t_last = (min(times), max(times)) if times else (None, None)
    t_res = resolution_time(s, t_last)
    cost_basis = D(str(bd.get("cost_basis_usd") or 0))
    matched = D(str(bd.get("matched_pairs") or 0))
    pair_cost = bd.get("matched_pair_cost")
    matched_usd = (matched * D(str(pair_cost))) if pair_cost else D(0)
    contribution = D(str(bd.get("matched_contribution_usd") or 0))
    bought = D(str(bd.get("bought_yes") or 0)) + D(str(bd.get("bought_no") or 0))
    residual_qty = abs(D(str(bd.get("directional_residual") or 0)))   # signed in the row: + YES, - NO
    resid_side_avg = None
    if residual_qty > 0:
        by, bn = D(str(bd.get("bought_yes") or 0)), D(str(bd.get("bought_no") or 0))
        resid_side_avg = D(str(bd.get("avg_yes") or 0)) if by > bn else D(str(bd.get("avg_no") or 0))
    residual_usd = (residual_qty * resid_side_avg) if resid_side_avg is not None else D(0)
    realized = s.get("realized_usd")
    realized_d = D(str(realized)) if realized is not None else None
    reb = rebates_by_market.get(slug) or {}
    rebate_usd = D(str(reb.get("credited_usd") or 0)).quantize(D("0.01"))
    fees = D(0)                                     # venue charged 0.00 on every print
    liquidity = D(0)                                # none credited on any L5-cohort market
    if realized_d is not None:
        settled_basis, net_core = "venue_realized", realized_d
    else:
        settled_basis, net_core = "derived_matched_pairs_flat_account", contribution
    net = net_core + rebate_usd + liquidity - fees
    hours_first = ((t_res - t_first) / 3600.0) if (t_res and t_first) else None
    cap_hours_first = (float(cost_basis) * hours_first) if hours_first is not None else None
    cap_hours_fw = None
    if own_fills and t_res:
        acc = 0.0
        for f in own_fills:
            t = _iso(f.get("log_ts"))
            if t is None:
                continue
            acc += _f(f.get("qty")) * _f(f.get("px")) * max(0.0, (t_res - t) / 3600.0)
        cap_hours_fw = acc
    shape, league, cat = category(slug)
    peak_unmatched = None
    if own_fills:
        q = {"YES": D(0), "NO": D(0)}
        sp = {"YES": D(0), "NO": D(0)}
        peak = D(0)
        for f in sorted(own_fills, key=lambda x: x.get("log_ts") or ""):
            side = str(f.get("side") or "").upper()
            if side not in q:
                continue
            q[side] += D(str(f["qty"]))
            sp[side] += D(str(f["qty"])) * D(str(f["px"]))
            long_side = "YES" if q["YES"] >= q["NO"] else "NO"
            n = abs(q["YES"] - q["NO"])
            avg = (sp[long_side] / q[long_side]) if q[long_side] else D(0)
            peak = max(peak, n * avg)
        peak_unmatched = float(peak)
    avg_entry = None
    if bought:
        avg_entry = float(cost_basis / bought)
    return {
        "slug": slug, "cohort": cohort or "shell_not_captured",
        "shape": shape, "league": league, "category": cat,
        "first_fill_utc": trades[-1]["t"] if trades else None,
        "resolution_utc": (datetime.utcfromtimestamp(t_res).strftime("%Y-%m-%dT%H:%M:%SZ")
                           if t_res is not None else None),
        "resolution_time_source": ("position_resolution" if res.get("updateTime") else
                                   "public_settlement_set_time" if (t_res is not None and t_res == _iso(res.get("public_settlement_set_time"))) else
                                   "audit_row_time_upper_bound"),
        "breakdown_source": "settlement_row",
        "settled_by": s.get("settled_by"), "settlement_status": s.get("status"),
        "prints": len(trades), "maker_prints": len(trades), "taker_prints": 0,
        "filled_usd_turnover": float(cost_basis),
        "bought_shares": float(bought),
        "matched_pairs": float(matched),
        "matched_usd": float(matched_usd),
        "matched_share_of_bought": round(float(2 * matched / bought), 4) if bought else None,
        "pair_cost": float(pair_cost) if pair_cost else None,
        "pair_contribution_usd": float(contribution),
        "pair_contribution_bp_of_turnover": round(float(contribution / cost_basis) * 1e4, 1) if cost_basis else None,
        "residual_shares": float(residual_qty),
        "residual_usd_at_settlement": float(residual_usd),
        "peak_unmatched_usd": peak_unmatched,
        "residual_duration_h": (None if hours_first is None else
                                round(hours_first, 3) if residual_qty > 0 else 0.0),
        "resolution_side": res.get("side") or bd.get("resolution_side"),
        "realized_usd_venue": float(realized_d) if realized_d is not None else None,
        "residual_contribution_usd": (float(bd["residual_contribution_usd"])
                                      if bd.get("residual_contribution_usd") is not None else None),
        "rebate_credited_usd": float(rebate_usd),
        "rebate_prints_credited": reb.get("prints"),
        "rebate_per_filled_usd": round(float(rebate_usd / cost_basis), 5) if cost_basis else None,
        "rebate_per_share": round(float(rebate_usd / bought), 5) if bought else None,
        "liquidity_rewards_credited_usd": float(liquidity),
        "fees_usd": float(fees),
        "net_usd": float(net),
        "net_basis": settled_basis,
        "net_per_turnover": round(float(net / cost_basis), 5) if cost_basis else None,
        "capital_hours_first_fill_bound": round(cap_hours_first, 3) if cap_hours_first is not None else None,
        "capital_hours_fill_weighted": round(cap_hours_fw, 3) if cap_hours_fw is not None else None,
        "net_per_capital_hour": (round(float(net) / cap_hours_first, 5)
                                 if cap_hours_first else None),
        "avg_entry_price": round(avg_entry, 4) if avg_entry is not None else None,
        "price_band": (None if avg_entry is None else
                       "p<0.35" if avg_entry < 0.35 else "p0.35-0.45" if avg_entry < 0.45
                       else "p0.45-0.55" if avg_entry < 0.55 else "p0.55-0.65" if avg_entry < 0.65
                       else "p>=0.65"),
        "labels": {"SUBMITTED": "see r4/r3l order records", "FILLED": "prints from the venue",
                   "SETTLED": s.get("status"), "REALIZED": ("venue" if realized_d is not None
                                                            else "none (flat account)")},
    }


def venue_only_row(slug: str, v: Mapping[str, Any], cohort: str, rebate_usd: D) -> dict:
    """A market the venue settled with a realized number that the probe's
    preflight reproduced, whose settlement/breakdown row is no longer
    retrievable (deployment logs before the retention window). Realized is
    the venue's; turnover, matched and residual are unknown, not zero."""
    shape, league, cat = category(slug)
    realized = D(str(v["realized_usd"]))
    net = realized + rebate_usd
    return {
        "slug": slug, "cohort": cohort, "shape": shape, "league": league, "category": cat,
        "first_fill_utc": None, "resolution_utc": None, "resolution_time_source": None,
        "breakdown_source": "preflight_venue_settled_snapshot_only",
        "settled_by": v.get("settled_by"), "settlement_status": v.get("status"),
        "prints": None, "maker_prints": None, "taker_prints": 0,
        "filled_usd_turnover": 0.0, "bought_shares": 0.0, "matched_pairs": 0.0,
        "matched_usd": 0.0, "matched_share_of_bought": None, "pair_cost": None,
        "pair_contribution_usd": 0.0, "pair_contribution_bp_of_turnover": None,
        "residual_shares": None, "residual_usd_at_settlement": None, "peak_unmatched_usd": None,
        "residual_duration_h": None, "resolution_side": None,
        "realized_usd_venue": float(realized), "residual_contribution_usd": None,
        "rebate_credited_usd": float(rebate_usd), "rebate_prints_credited": None,
        "rebate_per_filled_usd": None, "rebate_per_share": None,
        "liquidity_rewards_credited_usd": 0.0, "fees_usd": 0.0,
        "net_usd": float(net), "net_basis": "venue_realized",
        "net_per_turnover": None, "capital_hours_first_fill_bound": None,
        "capital_hours_fill_weighted": None, "net_per_capital_hour": None,
        "avg_entry_price": None, "price_band": None,
        "labels": {"SUBMITTED": "not retrievable", "FILLED": "not retrievable (turnover unknown, not zero)",
                   "SETTLED": v.get("status"), "REALIZED": "venue"},
    }


def aggregate(rows: Sequence[dict], key: str) -> dict:
    groups: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        groups[str(r.get(key))].append(r)
    out = {}
    for k, rs in sorted(groups.items()):
        turn = sum(r["filled_usd_turnover"] for r in rs)
        net = sum(r["net_usd"] for r in rs)
        pair = sum(r["pair_contribution_usd"] for r in rs)
        reb = sum(r["rebate_credited_usd"] for r in rs)
        resid = sum((r["residual_contribution_usd"] or 0.0) for r in rs)
        caph = sum((r["capital_hours_first_fill_bound"] or 0.0) for r in rs)
        realized = [r for r in rs if r["realized_usd_venue"] is not None]
        out[k] = {
            "markets": len(rs), "markets_with_venue_realized": len(realized),
            "turnover_usd": round(turn, 4),
            "pair_contribution_usd": round(pair, 4),
            "rebates_credited_usd": round(reb, 4),
            "residual_contribution_usd": round(resid, 4),
            "fees_usd": 0.0, "liquidity_rewards_usd": 0.0,
            "net_usd": round(net, 4),
            "net_per_turnover": round(net / turn, 5) if turn else None,
            "capital_hours_first_fill_bound": round(caph, 3),
            "net_per_capital_hour": round(net / caph, 5) if caph else None,
            "rebate_per_filled_usd": round(reb / turn, 5) if turn else None,
            "matched_share_of_bought": (round(sum(r["matched_pairs"] * 2 for r in rs)
                                              / sum(r["bought_shares"] for r in rs), 4)
                                        if sum(r["bought_shares"] for r in rs) else None),
        }
    return out


def scoreboard(paths: Sequence[str], rebates_path: str = REBATES_PATH) -> dict:
    h = harvest(paths)
    reb = load_rebates(rebates_path)
    by_market = reb.get("by_market") or {}
    rows = []
    for slug, s in sorted(h["settlements"].items(), key=lambda kv: kv[1].get("log_ts") or ""):
        cohort = h["cohorts"].get(slug)
        source = "session_decisions"
        if not cohort:
            trades = ((s.get("trades") or {}).get("rows") or [])
            t_first = min((_iso(r.get("t")) for r in trades if _iso(r.get("t"))), default=None)
            if t_first is None:      # older row format without prints: the row's own time
                t_first = _iso(((s.get("resolution") or {}).get("updateTime"))) or _iso(s.get("log_ts"))
            cohort = cohort_by_timeline(t_first, h["campaign_starts"])
            source = "campaign_timeline" if cohort else "not_captured"
        row = market_row(slug, s, cohort, by_market, h["fills"].get(slug) or [])
        row["cohort_source"] = source
        rows.append(row)
    venue_only = []
    for slug, v in sorted(h["venue_settled"].items()):
        if slug in h["settlements"]:
            continue
        rebate = D(str((by_market.get(slug) or {}).get("credited_usd") or 0)).quantize(D("0.01"))
        row = venue_only_row(slug, v, "R1", rebate)
        row["cohort_source"] = "campaign_timeline"
        venue_only.append(row)
    rows = venue_only + rows
    total = aggregate(rows, "cohort")
    return {
        "owner_direction": "#167 5569845340 A",
        "equation": "realized expectancy = matched-pair economics + venue-credited maker "
                    "rebates + venue-credited liquidity rewards - residual losses - fees",
        "markets": rows,
        "coverage": {"markets": len(rows),
                     "with_settlement_breakdown": len(rows) - len(venue_only),
                     "venue_realized_only": [r["slug"] for r in venue_only],
                     "campaign_starts_seen": h["campaign_starts"]},
        "by_cohort": total,
        "by_category": aggregate(rows, "category"),
        "by_shape": aggregate(rows, "shape"),
        "by_price_band": aggregate(rows, "price_band"),
        "all": (aggregate([dict(r, _all="all") for r in rows], "_all").get("all", {}) if rows else {}),
        "rebate_census": {"credited_prints": len(reb.get("rows") or []),
                          "credited_usd": round(sum(_f(-_f(r.get("our_commission")))
                                                    for r in reb.get("rows") or []), 4),
                          "audits_unioned": reb.get("audits")},
        "labels": "venue realized only where the venue emitted POSITION_RESOLUTION; on a "
                  "flat account the market's net is the DERIVED matched-pair contribution "
                  "(labelled); rebates are the venue's own credits; liquidity rewards 0 "
                  "unless credited; fees 0.00 charged; capital-hours as defined in the module",
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", nargs="+", required=True)
    ap.add_argument("--rebates", default=REBATES_PATH)
    ap.add_argument("--out", default=OUT_PATH)
    a = ap.parse_args(argv)
    out = scoreboard(a.logs, a.rebates)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, sort_keys=True, default=str)
    for r in out["markets"]:
        print(r["cohort"], r["slug"][-30:], "turn", r["filled_usd_turnover"], "pair", round(r["pair_contribution_usd"], 4),
              "resid", r["residual_contribution_usd"], "reb", r["rebate_credited_usd"], "net", round(r["net_usd"], 4),
              r["net_basis"], "cap-h", r["capital_hours_first_fill_bound"])
    print(json.dumps(out["by_cohort"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
