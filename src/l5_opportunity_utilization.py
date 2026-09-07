"""OWNER 5570474405 -- opportunity_utilization: while our bankroll was idle
or stranded, how many other live markets were on-distribution and
executable for L5, what their expected completion/turnover economics were,
and exactly which gate prevented rotation.

Timeline (from the probe's own rows): a market is ACTIVE from its first
session to its last non-hold session; STRANDED from then until the venue
settles it while a residual is held (the owner's HOLD-to-settlement rule);
FLAT_WAIT when the account is flat but the campaign still waits for the
market to settle before selecting (CAMPAIGN_HOLD:POSITION_HELD); IDLE when
no market is held and no candidate is admitted; STOPPED when the process
was down. For each non-active interval the nearest selection sample gives
the candidate pool the probe actually saw (rows, refusal reasons, live
events with progress), and the public-gateway snapshot gives what existed
(in-distribution live markets; scorecards executable on public features).

The gate is named per interval. Read-only: no selector, recorder, cap or
rung change; expected economics are ESTIMATES from L5's own history.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

A = os.path.join("analysis", "l5")
OUT_PATH = os.path.join(A, "l5_opportunity_utilization.json")
RANK_PATH = os.path.join(A, "pmus_universe_rank.json")
CARDS_PATH = os.path.join(A, "l5_market_scorecards.json")
EXTEND_PATH = os.path.join(A, "l5_extend_productivity.json")
SCOREBOARD_PATH = os.path.join(A, "l5_economic_scoreboard.json")
PREFIX = "PMUS_L5_CAMPAIGN "
HOLD_STATUSES = {"SPRING_RESIDUAL_HOLD", "SPRING_MATCHED"}


def _iso(s: Optional[str]) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _fmt(t: Optional[float]) -> Optional[str]:
    return None if t is None else datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load(p: str) -> dict:
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def events(paths: Sequence[str]):
    files: List[str] = []
    for p in paths:
        if os.path.isdir(p):
            files += [os.path.join(p, n) for n in sorted(os.listdir(p))]
        else:
            files.append(p)
    seen = set()
    for p in files:
        try:
            d = _load(p)
        except (OSError, json.JSONDecodeError):
            continue
        for e in d.get("deploy") or []:
            m = e.get("message") or ""
            if not m.startswith(PREFIX):
                continue
            key = (e.get("timestamp"), m[:200])
            if key in seen:
                continue
            seen.add(key)
            try:
                yield e.get("timestamp"), json.loads(m[len(PREFIX):])
            except json.JSONDecodeError:
                continue


def pool_summary(j: Mapping[str, Any]) -> dict:
    """Composition of one selection sample."""
    if j.get("kind") == "selection_sample":
        return {"t": j.get("status_t"), "n_rows": j.get("n_rows"), "closed": j.get("closed"), "thin_flow": j.get("thin_flow"),
                "other": (j.get("n_rows") or 0) - (j.get("closed") or 0) - (j.get("thin_flow") or 0),
                "live_events": j.get("live_events"), "markets_with_progress": j.get("markets_with_progress"),
                "admitted": len(j.get("admitted") or []), "chosen": j.get("chosen"),
                "thin_flow_slugs": j.get("thin_flow_slugs") or []}
    reasons = Counter()
    thin = []
    for r in j.get("refused") or []:
        reason = str(r.get("reason") or "").split(":")[0]
        reasons[reason] += 1
        if reason == "strict_evidence_thin_flow":
            thin.append(r.get("slug"))
    gp = j.get("game_progress") or {}
    return {"t": j.get("status_t"), "n_rows": j.get("n_rows"), "closed": reasons.get("strict_evidence_market_closed", 0),
            "thin_flow": reasons.get("strict_evidence_thin_flow", 0),
            "other": sum(v for k, v in reasons.items() if k not in ("strict_evidence_market_closed", "strict_evidence_thin_flow")),
            "live_events": gp.get("live_events"), "markets_with_progress": gp.get("markets_with_progress"),
            "admitted": len(j.get("admitted") or []), "chosen": j.get("chosen"), "thin_flow_slugs": thin,
            "reasons": dict(reasons)}


def timeline(paths: Sequence[str], now: Optional[float] = None) -> dict:
    sessions: Dict[str, List[tuple]] = defaultdict(list)
    settled: Dict[str, float] = {}
    starts: List[float] = []
    restores: List[float] = []
    selections: List[tuple] = []
    funnel_last = None
    for ts, j in events(paths):
        t = _iso(ts)
        if t is None:
            continue
        k = j.get("kind")
        if k == "session_end":
            st = str(j.get("status") or j.get("session_status") or "")
            n_orders = len(j.get("orders") or [])
            sessions[j["slug"]].append((t, st, n_orders))
        elif k == "settlement" and j.get("settled"):
            settled[j["slug"]] = min(t, settled.get(j["slug"], t))
        elif k in ("campaign_start", "campaign_continuation_start"):
            starts.append(t)
        elif k == "campaign_state_restored":
            restores.append(t)
        elif k in ("selection", "selection_sample"):
            selections.append((t, pool_summary(j)))
        elif k == "funnel":
            funnel_last = (t, j)
    selections.sort()
    markets, examined = [], []
    for slug, ss in sessions.items():
        ss.sort()
        acting = [t for t, st, n in ss if n > 0 or st in HOLD_STATUSES]
        if not acting:                      # examined and skipped (SPRING_SKIP_*), never acted
            examined.append({"slug": slug, "sessions": len(ss), "statuses": sorted({st for _, st, _ in ss})})
            continue
        first, last_act = min(acting), max(acting)
        flat_from = min((t for t, st, _ in ss if st == "SPRING_MATCHED"), default=None)
        markets.append({"slug": slug, "first_session": first, "last_acting_session": last_act,
                        "flat_from": flat_from, "settled": settled.get(slug),
                        "sessions": len(ss), "statuses": dict(Counter(st for _, st, _ in ss))})
    markets.sort(key=lambda m: m["first_session"])
    end = now or (funnel_last[0] if funnel_last else max(m["settled"] or m["last_acting_session"] for m in markets))
    return {"markets": markets, "examined_not_acted": examined, "selections": selections, "starts": sorted(starts),
            "restores": sorted(restores), "end": end, "funnel_last": funnel_last[1] if funnel_last else None}


def intervals(tl: Mapping[str, Any], gaps_stopped: Sequence[tuple] = ()) -> List[dict]:
    """Classify the campaign's time into ACTIVE / STRANDED / FLAT_WAIT / IDLE / STOPPED intervals."""
    out: List[dict] = []
    mk = tl["markets"]
    for i, m in enumerate(mk):
        a0, a1 = m["first_session"], m["last_acting_session"]
        out.append({"kind": "ACTIVE", "slug": m["slug"], "t0": a0, "t1": a1})
        settle = m["settled"]
        if settle and settle > a1:
            if m["flat_from"] is not None and m["flat_from"] >= a1 - 1:     # went flat on its last action
                out.append({"kind": "FLAT_WAIT", "slug": m["slug"], "t0": a1, "t1": settle,
                            "gate": "CAMPAIGN_HOLD:POSITION_HELD (one market at a time; the campaign waits for settlement even with a flat account)"})
            else:
                out.append({"kind": "STRANDED", "slug": m["slug"], "t0": a1, "t1": settle,
                            "gate": "HOLD residual to settlement (owner rule); residual held until the venue resolved"})
        nxt = mk[i + 1]["first_session"] if i + 1 < len(mk) else tl["end"]
        gap0 = settle if (settle and settle > a1) else a1
        if nxt > gap0 + 1:
            out.append({"kind": "IDLE", "slug": None, "t0": gap0, "t1": nxt, "gate": "CAMPAIGN_NO_CANDIDATE (no admitted candidate in the recorder pool)"})
    for s0, s1, why in gaps_stopped:
        out.append({"kind": "STOPPED", "slug": None, "t0": s0, "t1": s1, "gate": why})
    out.sort(key=lambda x: x["t0"])
    for x in out:
        x["hours"] = round((x["t1"] - x["t0"]) / 3600.0, 3)
    return out


def nearest_pool(selections: Sequence[tuple], t0: float, t1: float) -> Optional[dict]:
    inside = [p for t, p in selections if t0 <= t <= t1]
    if inside:
        # composition at the start, middle and end of the interval
        return {"samples_in_interval": len(inside), "first": inside[0], "last": inside[-1],
                "pool_rows_range": [min(p["n_rows"] or 0 for p in inside), max(p["n_rows"] or 0 for p in inside)],
                "closed_range": [min(p["closed"] or 0 for p in inside), max(p["closed"] or 0 for p in inside)],
                "thin_flow_range": [min(p["thin_flow"] or 0 for p in inside), max(p["thin_flow"] or 0 for p in inside)],
                "admitted_total": sum(p["admitted"] for p in inside),
                "live_events_range": [min((p["live_events"] or 0) for p in inside), max((p["live_events"] or 0) for p in inside)],
                "thin_flow_slugs_seen": sorted({s for p in inside for s in p["thin_flow_slugs"]})}
    before = [(t, p) for t, p in selections if t < t0]
    return {"samples_in_interval": 0, "nearest_before": before[-1][1] if before else None}


def universe_view(rank_path: str = RANK_PATH, cards_path: str = CARDS_PATH) -> dict:
    out = {"snapshot_utc": None}
    if os.path.exists(rank_path):
        r = _load(rank_path)
        out.update({"snapshot_utc": r.get("t_utc"), "universe_markets": (r.get("diag") or {}).get("universe_markets"),
                    "live_events": ((r.get("diag") or {}).get("live") or {}).get("live_events"),
                    "scored": (r.get("diag") or {}).get("scored"), "in_distribution": r.get("in_distribution"),
                    "two_sided_in_band_0_35_0_65": r.get("two_sided_in_band_0_35_0_65"),
                    "top_missing_from_pool": len(((r.get("pool_diff") or {}).get("top_missing_from_pool") or []))})
    if os.path.exists(cards_path):
        c = _load(cards_path)
        d = c.get("distribution") or {}
        cards = c.get("all") or []
        exec_all = [x for x in cards if x["executability"]["terms"]["on_distribution"] == 1.0
                    and x["executability"]["terms"]["depth"] >= 1.0 and x["executability"]["terms"]["two_sided"] == 1.0]
        exec_reb = [x for x in exec_all if x["executability"]["terms"]["incentive"] == 1.0]
        live_exec = [x for x in exec_all if x.get("live")]
        out.update({"cards": d.get("cards"), "on_distribution": d.get("on_distribution"), "depth_ok": d.get("depth_ok_for_5sh_both_sides"),
                    "executable_on_distribution_depth_two_sided": len(exec_all),
                    "of_which_live": len(live_exec), "of_which_rebate_eligible_at_price": len(exec_reb),
                    "executable_slugs_live": [x["slug"] for x in live_exec][:40]})
    return out


def expected_economics(extend_path: str = EXTEND_PATH, sb_path: str = SCOREBOARD_PATH) -> dict:
    """What an executable market was worth to the process, as ESTIMATES with provenance."""
    out = {"labels": "ESTIMATES; not realized; L5 priors from its own history, ours from settled markets"}
    if os.path.exists(extend_path):
        e = _load(extend_path)
        r = e.get("by_relation") or {}
        out["l5_completed_share_of_bought_usd"] = {k: v.get("productive_share_of_usd") for k, v in r.items()}
        out["l5_net_per_usd_by_relation"] = {k: v.get("net_per_usd") for k, v in r.items()}
        tte = (e.get("extend_conditioned") or {}).get("tte") or {}
        out["l5_time_to_complement_p50_s_by_tte"] = {k: (v.get("time_to_complement_s") or {}).get("p50") for k, v in tte.items()}
    if os.path.exists(sb_path):
        s = _load(sb_path)
        r4 = (s.get("by_cohort") or {}).get("R4") or {}
        out["our_r4_turnover_per_market_usd"] = round(r4.get("turnover_usd", 0) / r4["markets"], 3) if r4.get("markets") else None
        out["our_r4_net_per_turnover"] = r4.get("net_per_turnover")
        out["our_r4_capital_hours_per_market"] = round(r4.get("capital_hours_first_fill_bound", 0) / r4["markets"], 3) if r4.get("markets") else None
        at = [m for m in s.get("markets") or [] if m.get("cohort") in ("R3L", "R4")]
        out["our_at_size_net_per_turnover"] = round(sum(m["net_usd"] for m in at) / sum(m["filled_usd_turnover"] for m in at), 5) if at else None
    return out


def attribute(iv: Mapping[str, Any], pool: Optional[Mapping[str, Any]], uni: Mapping[str, Any]) -> dict:
    """Name the gate and whether the interval was 'no opportunity' or a bottleneck."""
    k = iv["kind"]
    if k == "ACTIVE":
        return {"gate": None, "verdict": "in market"}
    if k == "STOPPED":
        return {"gate": iv["gate"], "verdict": "process down (bounded in 43777804); not a selection question"}
    if k == "STRANDED":
        return {"gate": iv["gate"], "verdict": "residual held by design; rotation blocked by concurrency 1, an owner parameter"}
    if k == "FLAT_WAIT":
        return {"gate": iv["gate"], "verdict": "flat account but no rotation until settlement: an implementation gate, not an L5 behaviour (L5 re-enters within 60 s of settlement 77% of the time, never before)"}
    # IDLE
    p = pool or {}
    f = p.get("last") or p.get("nearest_before") or {}
    n, closed, thin = f.get("n_rows") or 0, f.get("closed") or 0, f.get("thin_flow") or 0
    live_exec = uni.get("of_which_live")
    if n and closed + thin >= n and (live_exec or 0) > 0:
        verdict = ("selection/coverage bottleneck: the recorder pool (%d rows) held %d resolved markets and %d thin-flow ones, "
                   "nothing live and evidenced, while the public gateway showed %s in-distribution live markets (%s executable on "
                   "public features, %s of them live). The strict-evidence gate could not admit what the recorder was not serving."
                   % (n, closed, thin, uni.get("in_distribution"), uni.get("executable_on_distribution_depth_two_sided"), live_exec))
        gate = "recorder pool composition (strict_evidence_market_closed / strict_evidence_thin_flow on every row) -> CAMPAIGN_NO_CANDIDATE"
    elif n and closed + thin >= n:
        verdict = "pool held nothing live and evidenced; the public universe view is not available for this interval"
        gate = "recorder pool composition -> CAMPAIGN_NO_CANDIDATE"
    else:
        verdict = "pool had live rows refused on evidence; see reasons"
        gate = "strict-evidence refusals"
    return {"gate": gate, "verdict": verdict}


def study(paths: Sequence[str], now: Optional[float] = None, stopped: Sequence[tuple] = ()) -> dict:
    tl = timeline(paths, now)
    ivs = intervals(tl, stopped)
    uni = universe_view()
    econ = expected_economics()
    rows = []
    for iv in ivs:
        pool = nearest_pool(tl["selections"], iv["t0"], iv["t1"]) if iv["kind"] != "ACTIVE" else None
        att = attribute(iv, pool, uni)
        rows.append({**iv, "t0": _fmt(iv["t0"]), "t1": _fmt(iv["t1"]), "pool": pool, **att})
    by_kind = Counter()
    for iv in ivs:
        by_kind[iv["kind"]] += iv["hours"]
    total = sum(by_kind.values())
    active = by_kind.get("ACTIVE", 0.0)
    idle_exec = sum(iv["hours"] for iv, r in zip(ivs, rows) if iv["kind"] == "IDLE" and "bottleneck" in (r.get("verdict") or ""))
    foregone = None
    if econ.get("our_r4_turnover_per_market_usd") and econ.get("our_r4_capital_hours_per_market"):
        # ESTIMATE: markets the process could have run sequentially in the bottlenecked idle hours at R4's own pace
        hours_per_market = econ["our_r4_capital_hours_per_market"] / econ["our_r4_turnover_per_market_usd"]
        markets = idle_exec / hours_per_market if hours_per_market else None
        foregone = {"markets_at_r4_pace_ESTIMATE": round(markets, 2) if markets else None,
                    "turnover_usd_ESTIMATE": round((markets or 0) * econ["our_r4_turnover_per_market_usd"], 2),
                    "labels": "capacity foregone, not P&L; the sign of the per-market net is not established"}
    return {"owner_direction": "#167 5570474405 (opportunity_utilization); 5570456323",
            "markets": [dict(m, first_session=_fmt(m["first_session"]), last_acting_session=_fmt(m["last_acting_session"]),
                             flat_from=_fmt(m["flat_from"]), settled=_fmt(m["settled"])) for m in tl["markets"]],
            "examined_not_acted": tl["examined_not_acted"],
            "campaign_window": {"t0": _fmt(ivs[0]["t0"]) if ivs else None, "t1": _fmt(tl["end"]), "hours": round(total, 3)},
            "hours_by_kind": {k: round(v, 3) for k, v in by_kind.items()},
            "opportunity_utilization": {"active_share": round(active / total, 4) if total else None,
                                        "idle_bottleneck_hours": round(idle_exec, 3),
                                        "idle_bottleneck_share": round(idle_exec / total, 4) if total else None,
                                        "definition": "active hours / campaign hours; bottleneck = idle while the public universe held executable on-distribution live markets the recorder pool did not serve"},
            "intervals": rows,
            "universe_at_snapshot": uni,
            "expected_economics": econ,
            "capacity_foregone_in_bottleneck_hours": foregone,
            "funnel_last": tl["funnel_last"],
            "labels": ("read-only; the gate names are the probe's own; the public-universe view is one snapshot (%s) applied to the "
                       "intervals around it; nothing here admits a market, changes the recorder, the selector, a cap or a rung"
                       % uni.get("snapshot_utc"))}


def render_md(o: Mapping[str, Any]) -> str:
    L = ["# opportunity_utilization (OWNER 5570474405)\n", o["labels"] + "\n",
         "Campaign window %s -> %s, %s h. Hours by kind: %s. Active share **%s**; idle-bottleneck hours **%s** (%s of the window).\n"
         % (o["campaign_window"]["t0"], o["campaign_window"]["t1"], o["campaign_window"]["hours"], json.dumps(o["hours_by_kind"]),
            o["opportunity_utilization"]["active_share"], o["opportunity_utilization"]["idle_bottleneck_hours"], o["opportunity_utilization"]["idle_bottleneck_share"]),
         "| from | to | h | kind | market | pool (rows / closed / thin) | live events seen | gate | verdict |", "|---|---|---|---|---|---|---|---|---|"]
    for r in o["intervals"]:
        p = r.get("pool") or {}
        f = p.get("last") or p.get("nearest_before") or {}
        pool = "%s / %s / %s" % (f.get("n_rows"), f.get("closed"), f.get("thin_flow")) if f else ""
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (r["t0"], r["t1"], r["hours"], r["kind"], r.get("slug") or "", pool,
                                                                    f.get("live_events", ""), (r.get("gate") or "").replace("|", "/"), (r.get("verdict") or "").replace("|", "/")))
    u = o["universe_at_snapshot"]
    L.append("\nPublic universe at %s: %s markets in %s live events, %s scored, %s in-distribution; scorecards: %s on-distribution, %s with 5-share depth both sides, **%s executable on public features (%s live, %s rebate-eligible at price)**. %s of the top-25 in-distribution live markets were absent from the recorder pool.\n"
             % (u.get("snapshot_utc"), u.get("universe_markets"), u.get("live_events"), u.get("scored"), u.get("in_distribution"), u.get("on_distribution"), u.get("depth_ok"),
                u.get("executable_on_distribution_depth_two_sided"), u.get("of_which_live"), u.get("of_which_rebate_eligible_at_price"), u.get("top_missing_from_pool")))
    L.append("Expected economics (ESTIMATES): " + json.dumps(o["expected_economics"]) + "\n")
    L.append("Capacity foregone in bottleneck hours: " + json.dumps(o["capacity_foregone_in_bottleneck_hours"]) + "\n")
    return "\n".join(L)


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", nargs="+", required=True)
    ap.add_argument("--stopped", nargs="*", default=[], help="t0,t1,reason triples for process-down intervals")
    ap.add_argument("--out", default=OUT_PATH)
    ap.add_argument("--now", default=None, help="end of the window (last time the loop was seen alive)")
    a = ap.parse_args(argv)
    stopped = []
    for s in a.stopped:
        t0, t1, why = s.split(",", 2)
        stopped.append((_iso(t0), _iso(t1), why))
    out = study(a.logs, now=_iso(a.now) if a.now else None, stopped=stopped)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, sort_keys=True, default=str)
    with open(os.path.join(os.path.dirname(a.out), "L5_OPPORTUNITY_UTILIZATION.md"), "w", encoding="utf-8") as f:
        f.write(render_md(out))
    print(render_md(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
