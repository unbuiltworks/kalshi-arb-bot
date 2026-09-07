"""R4 action-by-action comparison: what L5 would have done at each live
state, what the engine sampled, what it executed, and what stopped it.

OWNER (chat, 2026-09-07 ~11:00Z): "prepare the R4 action-by-action
comparison so we can make the next decision the moment three markets
finish." OWNER 5567269295 asked per market for: sampled vs acted, fills,
pair cost, unmatched-dollar path, complement opportunities blocked/allowed
by old vs new bound, rebates credited, residual settlement P&L, pair
contribution, fees, venue balance delta.

SOURCE. The probe's ledger lives on the Railway volume; its decision rows
are also emitted verbatim in the PMUS_L5_CAMPAIGN session_end log lines
(the last 8 decisions of each session; sessions run at most 6 actions, so
every decision is present). harvest() reads Railway log dumps -- the JSON
the MCP get-logs tool writes: {"deploy": [{"timestamp", "message"}, ...]}
-- and dedupes decisions by decision_id and orders by order_id, keeping
only rows whose safety_shell_version names the R4 shell. Settlement audits
(kind "settlement", settled true) and selections are kept for the same
markets. The compact harvest is written to analysis/l5/r4_*.jsonl so the
comparison is reproducible without the raw logs.

DISCIPLINE. Realized money is the venue's number only (settlement rows);
matched-pair cash is DERIVED and labelled; rebates only where the venue's
trade rows credited them (the incentive audit), never estimated here.

THREE PAIR-COST BASES are reported on every completion, because they
disagree on the one par-band completion so far (market 2, NO 5.01 @0.51):
  engine     -- the rule the engine applied: average cost of ALL contracts
                ever bought on the surplus side (matched ones included) +
                complement price. 0.4892 + 0.51 = 0.9992, inside 1.00.
  open_lots  -- average cost of the surplus side's still-UNMATCHED lots +
                complement price. (0.11@0.47 + 4.90@0.51)/5.01 + 0.51 =
                1.0191, above par.
  marginal   -- lot by lot, FIFO: the 4.90@0.51 lot paired at 1.02, above
                par; the 0.11@0.47 lot at 0.98.
The engine basis is what ran; the other two are the owner's open question
(#167 5568536360). Nothing here changes the engine.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from decimal import Decimal as D
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

OUT_DIR = os.path.join("analysis", "l5")
SHELL_PREFIX = "R4/"
PREFIX = "PMUS_L5_CAMPAIGN "
PAR = D("1.00")
OLD_BOUND = D("0.98")


def _f(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _d(x: Any) -> Optional[D]:
    try:
        return D(str(x))
    except Exception:                                  # noqa: BLE001
        return None


# ------------------------------ harvest ------------------------------

def _iter_events(paths: Iterable[str]):
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


def harvest(paths: Sequence[str], shell_prefix: str = SHELL_PREFIX) -> dict:
    decisions: Dict[str, dict] = {}
    orders: Dict[str, dict] = {}
    sessions: Dict[str, dict] = {}
    settlements: Dict[str, dict] = {}
    selections: List[dict] = []
    markets: set = set()
    for ts, j in _iter_events(_expand(paths)):
        kind = j.get("kind")
        if kind == "session_end":
            decs = [d for d in (j.get("decisions") or [])
                    if str(d.get("safety_shell_version") or "").startswith(shell_prefix)]
            if not decs:
                continue
            slug = j.get("slug")
            markets.add(slug)
            sid = decs[0].get("session_id") or f"{slug}:{ts}"
            for d in decs:
                did = d.get("decision_id") or f"{sid}:{d.get('decision_idx')}"
                decisions.setdefault(did, dict(d, slug=slug, log_ts=ts, session_id=sid))
            for o in (j.get("orders") or []):
                oid = o.get("order_id") or f"{slug}:{o.get('stage')}:{ts}"
                orders.setdefault(oid, dict(o, slug=slug, log_ts=ts, session_id=sid,
                                            session_status=j.get("status")))
            sessions.setdefault(sid, {
                "session_id": sid, "slug": slug, "log_ts": ts,
                "status": j.get("status"), "reason": j.get("reason"),
                "stop_reason": j.get("stop_reason"),
                "resumed": bool(j.get("resumed")),
                "submitted": j.get("submitted"),
                "filled_actions": j.get("filled_actions"),
                "inventory": j.get("inventory"),
                "resumed_inventory": j.get("resumed_inventory"),
                "pair_economics": j.get("pair_economics"),
                "spring_gates": j.get("spring_gates"),
                "decision_idx": sorted(int(_f(d.get("decision_idx"), -1)) for d in decs),
            })
        elif kind == "settlement" and j.get("settled"):
            settlements[j.get("slug")] = dict(j, log_ts=ts)
        elif kind == "selection" and j.get("chosen"):
            selections.append({"log_ts": ts, "chosen": j.get("chosen"),
                               "admitted": [{k: a.get(k) for k in
                                             ("rank", "slug", "pair_score", "state",
                                              "state_n", "game_period", "game_proximity",
                                              "rebate_eligible", "bid", "ask",
                                              "bid_touch_qty")}
                                            for a in (j.get("admitted") or [])],
                               "refused_n": len(j.get("refused") or [])})
    settlements = {k: v for k, v in settlements.items() if k in markets}
    selections = [s for s in selections if s["chosen"] in markets]
    return {"decisions": sorted(decisions.values(),
                                key=lambda d: (d.get("slug") or "", int(_f(d.get("decision_idx"), -1)))),
            # session time, then ACTION SLOT (numeric): "spring_complete_6"
            # sorts after "spring_open_3" in time, not before it alphabetically
            "orders": sorted(orders.values(), key=lambda o: (o.get("log_ts") or "", _stage_no(o.get("stage")))),
            "sessions": sorted(sessions.values(), key=lambda s: s.get("log_ts") or ""),
            "settlements": settlements,
            "selections": sorted(selections, key=lambda s: s["log_ts"]),
            "markets": sorted(markets)}


def write_harvest(h: Mapping[str, Any], out_dir: str = OUT_DIR) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    paths = {}
    for name in ("decisions", "orders", "sessions", "selections"):
        p = os.path.join(out_dir, f"r4_{name}.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            for row in h[name]:
                f.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        paths[name] = p
    p = os.path.join(out_dir, "r4_settlements.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(h["settlements"], f, indent=1, sort_keys=True, default=str)
    paths["settlements"] = p
    return paths


# ------------------------------ pairing ------------------------------

def _stage_no(stage: Any) -> int:
    try:
        return int(str(stage).rsplit("_", 1)[-1])
    except (TypeError, ValueError):
        return 0


def pair_orders_with_decisions(orders: Sequence[dict], decisions: Sequence[dict]) -> dict:
    """order_id -> the decision that produced it. The stage suffix
    (spring_complete_5) is the ACTION SLOT inside the session, 1-based, and
    every decision -- a hold included, because a refusal consumes its slot
    -- occupies one slot in decision_idx order. Verified on all 29 R4
    sessions (e.g. decisions 38-41 hold, 42 complete -> spring_complete_5).
    A pairing is only kept when the order's relation agrees with the
    decision's acted relation, so a mismatch is visible, never silent."""
    by_sess: Dict[str, List[dict]] = defaultdict(list)
    for d in decisions:
        by_sess[d.get("session_id")].append(d)
    out: Dict[str, dict] = {}
    for sid, decs in by_sess.items():
        slots = sorted(decs, key=lambda x: int(_f(x.get("decision_idx"), -1)))
        sess_orders = sorted([o for o in orders if o.get("session_id") == sid],
                             key=lambda o: _stage_no(o.get("stage")))
        for o in sess_orders:
            k = _stage_no(o.get("stage")) - 1
            if 0 <= k < len(slots) and slots[k].get("acted") == o.get("relation"):
                out[o.get("order_id")] = slots[k]
    return out


def completions(orders: Sequence[dict], pairing: Mapping[str, dict]) -> List[dict]:
    """Every FILLED complement with its three pair-cost bases (see module
    docstring), the old/new bound flags, and FIFO lot detail."""
    lots = {"YES": [], "NO": []}
    out = []
    for o in orders:
        if _f(o.get("filled_qty")) <= 0:
            continue
        side = str(o.get("side") or "").upper()
        other = "NO" if side == "YES" else "YES"
        qty, px = D(str(o.get("filled_qty"))), D(str(o.get("px")))
        if not lots[other]:
            lots[side].append((qty, px))
            continue
        tot_q = sum(q for q, _ in lots[other])
        tot_c = sum(q * p for q, p in lots[other])
        open_lots_pair = (tot_c / tot_q + px) if tot_q else None
        dec = pairing.get(o.get("order_id")) or {}
        inv = dec.get("inventory_before") or {}
        surplus_avg = _d(inv.get("avg_dn") if side == "YES" else inv.get("avg_up"))
        engine_pair = (surplus_avg + px) if surplus_avg is not None else None
        cf = dec.get("counterfactual_l5_price") or {}
        lot_rows = []
        remaining = qty
        while remaining > 0 and lots[other]:
            lq, lp = lots[other][0]
            take = min(remaining, lq)
            lot_rows.append({"matched_qty": str(take), "lot_px": str(lp),
                             "marginal_pair": str(lp + px),
                             "over_par": bool(lp + px > PAR)})
            remaining -= take
            if take == lq:
                lots[other].pop(0)
            else:
                lots[other][0] = (lq - take, lp)
        if remaining > 0:
            lots[side].append((remaining, px))
        out.append({
            "order_id": o.get("order_id"), "stage": o.get("stage"),
            "side": side, "px": str(px), "qty": str(qty),
            "decision_idx": dec.get("decision_idx"),
            "engine_pair": str(engine_pair) if engine_pair is not None else None,
            "open_lots_pair": str(open_lots_pair) if open_lots_pair is not None else None,
            "marginal_lots": lot_rows,
            "engine_within_old_bound_0_98": (engine_pair <= OLD_BOUND) if engine_pair is not None else None,
            "engine_within_par": (engine_pair <= PAR) if engine_pair is not None else None,
            "open_lots_within_par": (open_lots_pair <= PAR) if open_lots_pair is not None else None,
            "marginal_all_within_par": all(not r["over_par"] for r in lot_rows),
            "unlocked_by_rung_bound": cf.get("unlocked_by_rung_bound"),
            "within_old_bound_0_98_recorded": cf.get("within_old_bound_0_98"),
        })
    return out


# ------------------------------ comparison ------------------------------

def _l1(a: Mapping[str, float], b: Mapping[str, float]) -> Optional[float]:
    keys = set(a) | set(b)
    if not keys:
        return None
    return round(sum(abs(_f(a.get(k)) - _f(b.get(k))) for k in keys), 4)


def _norm(c: Counter) -> Dict[str, float]:
    n = sum(c.values())
    return {k: round(v / n, 4) for k, v in sorted(c.items())} if n else {}


def _sampled(d: Mapping[str, Any]):
    return (((d.get("shadow") or {}).get("stochastic") or {}).get("sampled")
            or d.get("attempted") or d.get("would_act_without_safety_cap"))


def _iso(s: Optional[str]) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _dist(xs: Sequence[float], nd: int = 4) -> dict:
    ys = sorted(xs)
    n = len(ys)
    if not n:
        return {"n": 0}
    q = lambda p: round(ys[min(n - 1, int(p * n))], nd)  # noqa: E731
    return {"n": n, "mean": round(sum(ys) / n, nd), "p50": q(0.5), "p90": q(0.9),
            "max": round(ys[-1], nd)}


def _relation_stats(decs: Sequence[dict]) -> dict:
    sampled = Counter(_sampled(d) for d in decs)
    acted = Counter(d.get("acted") for d in decs)
    acted_ex = Counter(d.get("acted") for d in decs if d.get("acted") != "hold")
    expected = defaultdict(float)
    for d in decs:
        for r in (d.get("relation_ranked") or []):
            expected[r.get("relation")] += _f(r.get("p"))
    exp = {k: round(v / len(decs), 4) for k, v in expected.items()} if decs else {}
    refusals = Counter(str(d.get("refusal") or "").split(":")[0]
                       for d in decs if d.get("acted") == "hold")
    return {
        "sampled_relation": _norm(sampled),
        "acted_relation_incl_hold": _norm(acted),
        "acted_relation_excl_hold": _norm(acted_ex),
        "expected_l5_relation": exp,
        "l1_sampled_vs_l5": _l1(_norm(sampled), exp),
        "l1_acted_excl_hold_vs_l5": _l1(_norm(acted_ex), exp),
        "hold_share": round(acted.get("hold", 0) / len(decs), 4) if decs else None,
        "holds_by_refusal": dict(refusals.most_common()),
        "divergence_classes": dict(Counter(d.get("divergence_class") for d in decs).most_common()),
        "off_distribution_decisions": sum(1 for d in decs if d.get("off_distribution")),
    }


def market_report(slug: str, h: Mapping[str, Any]) -> dict:
    decs = [d for d in h["decisions"] if d.get("slug") == slug]
    orders = [o for o in h["orders"] if o.get("slug") == slug]
    sessions = [s for s in h["sessions"] if s.get("slug") == slug]
    settle = h["settlements"].get(slug) or {}
    pairing = pair_orders_with_decisions(orders, decs)
    fills = [o for o in orders if _f(o.get("filled_qty")) > 0]
    rejects = Counter(str(o.get("end_reason") or "").split(":")[0] for o in orders
                      if _f(o.get("filled_qty")) <= 0)
    comp_cf = [d.get("counterfactual_l5_price") for d in decs if d.get("counterfactual_l5_price")]
    comps = completions(orders, pairing)
    times = sorted(_iso(d.get("t_utc")) for d in decs if d.get("t_utc"))
    gaps = [b - a for a, b in zip(times, times[1:]) if b > a]
    size = [(_f(((d.get("shadow") or {}).get("l5_target_size") or {}).get("shares_p50")),
             _f((d.get("planned_safe_size") or {}).get("contracts")))
            for d in decs if d.get("planned_safe_size")]
    bd = settle.get("breakdown") or {}
    stats = _relation_stats(decs)
    return {
        "slug": slug,
        "selection": next((s for s in h["selections"] if s["chosen"] == slug), None),
        "decisions": len(decs),
        "decision_idx_range": [int(_f(decs[0].get("decision_idx"))),
                               int(_f(decs[-1].get("decision_idx")))] if decs else None,
        "states": dict(Counter(d.get("state") for d in decs).most_common()),
        **stats,
        "orders_submitted": len(orders),
        "orders_filled": len(fills),
        "orders_paired_with_a_decision": sum(1 for o in orders if o.get("order_id") in pairing),
        "fills": [{"stage": o.get("stage"), "side": o.get("side"), "px": o.get("px"),
                   "qty": o.get("filled_qty"), "relation": o.get("relation"),
                   "rest_s": o.get("rest_s"), "order_id": o.get("order_id"),
                   "decision_idx": (pairing.get(o.get("order_id")) or {}).get("decision_idx")}
                  for o in fills],
        "maker_fills": len(fills), "taker_fills": 0,
        "unfilled_by_end_reason": dict(rejects.most_common()),
        "complement_decisions": {
            "with_counterfactual": len(comp_cf),
            "within_old_bound_0_98": sum(1 for c in comp_cf if c.get("within_old_bound_0_98")),
            "unlocked_by_rung_bound": sum(1 for c in comp_cf if c.get("unlocked_by_rung_bound")),
            "priced_down_to_par_from_above": sum(
                1 for c in comp_cf if str(c.get("class") or "").startswith("safety_override")),
        },
        "completions_filled": comps,
        "completions_summary": {
            "filled": len(comps),
            "unlocked_by_rung_bound": sum(1 for c in comps if c.get("unlocked_by_rung_bound")),
            "engine_over_par": sum(1 for c in comps if c.get("engine_within_par") is False),
            "open_lots_over_par": sum(1 for c in comps if c.get("open_lots_within_par") is False),
            "marginal_lots_over_par": sum(1 for c in comps for r in c["marginal_lots"] if r["over_par"]),
        },
        "unmatched_usd_path": [{"idx": int(_f(d.get("decision_idx"), -1)), "t": d.get("t_utc"),
                                "before": (d.get("inventory_before") or {}).get("unmatched_usd"),
                                "acted": d.get("acted"), "refusal": d.get("refusal")}
                               for d in decs],
        "size": {"executed_over_l5_p50": _dist([round(e / l, 4) for l, e in size if l > 0 and e > 0])},
        "cadence_s": _dist(gaps, 1),
        "sessions": [{k: s.get(k) for k in ("log_ts", "status", "reason", "stop_reason",
                                           "resumed", "submitted", "filled_actions")}
                     for s in sessions],
        "economics": {
            "settled": bool(settle.get("settled")),
            "settlement_status": settle.get("status"),
            "settled_by": settle.get("settled_by"),
            "realized_usd_venue": settle.get("realized_usd"),
            "resolution_side": (settle.get("resolution") or {}).get("side"),
            "derived": {k: bd.get(k) for k in (
                "bought_yes", "bought_no", "avg_yes", "avg_no", "cost_basis_usd",
                "matched_pairs", "matched_pair_cost", "matched_contribution_usd",
                "directional_residual", "residual_contribution_usd", "fees_usd")},
            "labels": "realized_usd_venue is the venue's own number; everything "
                      "under 'derived' is arithmetic on ledger fills; rebates come "
                      "from the incentive audit and are not estimated here",
        },
    }


def campaign_report(h: Mapping[str, Any]) -> dict:
    per = {slug: market_report(slug, h) for slug in h["markets"]}
    decs = h["decisions"]
    stats = _relation_stats(decs)
    realized = [_f(m["economics"]["realized_usd_venue"]) for m in per.values()
                if m["economics"].get("realized_usd_venue") is not None]
    derived = [_f(m["economics"]["derived"].get("matched_contribution_usd"))
               for m in per.values()
               if m["economics"]["derived"].get("matched_contribution_usd") is not None]
    return {
        "markets": h["markets"],
        "markets_settled": sum(1 for m in per.values() if m["economics"]["settled"]),
        "decisions": len(decs),
        "submitted": len(h["orders"]),
        "filled": sum(1 for o in h["orders"] if _f(o.get("filled_qty")) > 0),
        **stats,
        "cap_refusals": stats["holds_by_refusal"].get("unmatched_usd_cap", 0),
        "completions": {
            "filled": sum(m["completions_summary"]["filled"] for m in per.values()),
            "unlocked_by_rung_bound": sum(m["completions_summary"]["unlocked_by_rung_bound"] for m in per.values()),
            "engine_over_par": sum(m["completions_summary"]["engine_over_par"] for m in per.values()),
            "open_lots_over_par": sum(m["completions_summary"]["open_lots_over_par"] for m in per.values()),
            "marginal_lots_over_par": sum(m["completions_summary"]["marginal_lots_over_par"] for m in per.values()),
        },
        "realized_usd_venue_sum": round(sum(realized), 4) if realized else None,
        "derived_matched_contribution_sum": round(sum(derived), 4) if derived else None,
        "per_market": per,
        "labels": "sampled = the stochastic draw from the frozen L5 distribution; "
                  "acted = what the engine executed after caps/venue; expected = "
                  "mean L5 relation probabilities over the same states; pair-cost "
                  "bases engine / open_lots / marginal as in the module docstring",
    }


def render_md(rep: Mapping[str, Any]) -> str:
    c = rep["completions"]
    L = ["# R4 action-by-action comparison", "",
         f"Markets: {len(rep['markets'])} ({rep['markets_settled']} settled) | "
         f"decisions {rep['decisions']} | submitted {rep['submitted']} | filled {rep['filled']}", "",
         "## Campaign", "",
         f"- sampled relation: {rep['sampled_relation']}",
         f"- acted (excl. hold): {rep['acted_relation_excl_hold']}",
         f"- expected L5: {rep['expected_l5_relation']}",
         f"- L1 sampled vs L5: {rep['l1_sampled_vs_l5']} | acted vs L5: {rep['l1_acted_excl_hold_vs_l5']}",
         f"- hold share {rep['hold_share']}; holds by refusal: {rep['holds_by_refusal']}",
         f"- cap refusals: {rep['cap_refusals']} | off-distribution decisions: {rep['off_distribution_decisions']}",
         f"- completions filled {c['filled']}: unlocked by the 1.00 bound {c['unlocked_by_rung_bound']}; "
         f"over par on the engine basis {c['engine_over_par']}, on open lots {c['open_lots_over_par']}, "
         f"marginal lots over par {c['marginal_lots_over_par']}",
         f"- venue realized sum: {rep['realized_usd_venue_sum']} | derived matched-pair sum: {rep['derived_matched_contribution_sum']}",
         ""]
    for slug, m in rep["per_market"].items():
        e = m["economics"]
        cs = m["completions_summary"]
        L += [f"## {slug}", "",
              f"- decisions {m['decisions']} idx {m['decision_idx_range']}; states {m['states']}",
              f"- sampled {m['sampled_relation']} | acted excl. hold {m['acted_relation_excl_hold']} | expected L5 {m['expected_l5_relation']}",
              f"- L1 sampled vs L5 {m['l1_sampled_vs_l5']} | acted vs L5 {m['l1_acted_excl_hold_vs_l5']}",
              f"- hold share {m['hold_share']}; holds by refusal {m['holds_by_refusal']}; off-distribution {m['off_distribution_decisions']}",
              f"- orders {m['orders_submitted']} submitted / {m['orders_filled']} filled (all maker); unfilled by reason {m['unfilled_by_end_reason']}",
              "- fills: " + "; ".join(f"{x['relation']} {x['side']} {x['qty']}@{x['px']} (idx {x['decision_idx']})" for x in m["fills"]),
              f"- complement decisions: {m['complement_decisions']}",
              f"- completions: {cs}",
              ]
        for comp in m["completions_filled"]:
            L.append(f"  - {comp['stage']} {comp['side']} {comp['qty']}@{comp['px']}: engine {comp['engine_pair']}, "
                     f"open-lots {comp['open_lots_pair']}, marginal "
                     + ", ".join(f"{r['matched_qty']}x{r['lot_px']}->{r['marginal_pair']}" for r in comp["marginal_lots"])
                     + f"; unlocked_by_rung_bound {comp['unlocked_by_rung_bound']}")
        L += [f"- size executed/L5 p50: {m['size']['executed_over_l5_p50']}; cadence s: {m['cadence_s']}",
              f"- economics: settled {e['settled']} ({e['settlement_status']}, {e['settled_by']}); "
              f"venue realized {e['realized_usd_venue']}; derived {e['derived']}",
              ""]
    L += ["Labels: realized = venue number only; derived = arithmetic on ledger fills; "
          "engine / open_lots / marginal pair bases as defined in the module.", ""]
    return "\n".join(L)


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", nargs="+", required=True,
                    help="Railway log dump files or directories")
    ap.add_argument("--out", default=OUT_DIR)
    a = ap.parse_args(argv)
    h = harvest(a.logs)
    paths = write_harvest(h, a.out)
    rep = campaign_report(h)
    with open(os.path.join(a.out, "r4_action_comparison.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, sort_keys=True, default=str)
    with open(os.path.join(a.out, "R4_ACTION_COMPARISON.md"), "w", encoding="utf-8") as f:
        f.write(render_md(rep))
    print(json.dumps({"markets": h["markets"], "decisions": len(h["decisions"]),
                      "orders": len(h["orders"]), "sessions": len(h["sessions"]),
                      "settled": rep["markets_settled"], "completions": rep["completions"],
                      "paths": paths}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
