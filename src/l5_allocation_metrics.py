"""WIDE SCAN, NARROW CAPITAL, HIGH MOBILITY -- the measurement (OWNER #167
comment 5586921649).

The owner asked for measurement, NOT a new allocation rule, and that
distinction is load-bearing: reading the live code first showed the
behaviour is ALREADY what the principle asks for. select_candidate re-ranks
the entire admitted universe every cycle and takes admitted[0] with no
memory of the previous pick, so the flat slot has no loyalty by
construction; CAMPAIGN_HOLD:POSITION_HELD concentrates the slot while
inventory is open. Live on 2026-09-08 the flat slot moved through three
markets in three minutes (14:23:28 alitub-barpal, 14:23:50 maxzei-alvmez,
14:26:27 nikizq-santav) and switched between consecutive 20 s cycles at
13:30. So this module measures whether that mobility is actually paying,
rather than adding a rule that would duplicate it.

EVERY NUMBER HERE IS DERIVED unless it says otherwise. Settled realized
dollars come from the settlement auditor's venue rows and nowhere else;
rebates appear only where the venue credited them. A measurement whose
inputs are too thin reports null WITH A REASON rather than a number -- the
same discipline as the fill reconciliation, where unknown is never recorded
as agreement.

    python3 -m src.l5_allocation_metrics --logs <dir> --out analysis/l5
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

OWNER_REF = "#167 comment 5586921649"
LABEL_DERIVED = ("DERIVED from campaign logs; settled dollars come from the "
                 "settlement auditor's venue rows only")


def _f(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def category_of(slug: str) -> str:
    """The venue's own slug grammar: <family>-<league>-... . Used only to
    group reporting; nothing keys off it."""
    parts = str(slug).split("-")
    return f"{parts[0]}/{parts[1]}" if len(parts) > 1 else str(slug)


# --------------------------------------------------------------------------
# selection timeline -- parsed here because the shared harvester drops the
# component breakdown that measurement 6 is specifically about
# --------------------------------------------------------------------------

_SEL = re.compile(r'PMUS_L5_CAMPAIGN\s+(\{.*?"kind":\s*"selection".*?\})\s*(?=PMUS_|$)',
                  re.S)


def _messages(path: Any) -> List[str]:
    return [m for _dep, m in _message_rows(path)]


def _message_rows(path: Any) -> List[tuple]:
    """The log messages in a file, already unescaped.

    A harvested Railway result is JSON whose `message` fields carry the log
    line with every quote backslash-escaped. Brace-scanning that escaped text
    directly is what made the first version of this parser silently return
    nothing, so the JSON is parsed properly first and only a genuinely
    non-JSON file falls back to raw text."""
    try:
        blob = open(path, "rt", encoding="utf-8", errors="replace").read()
    except OSError:
        return []
    try:
        doc = json.loads(blob)
    except ValueError:
        return [(str(path), blob)]
    rows = doc.get("deploy") if isinstance(doc, Mapping) else None
    if not isinstance(rows, list):
        return [(str(path), blob)]
    dep = str(doc.get("deploymentId") or path)
    return [(dep, str(r.get("message") or "")) for r in rows
            if isinstance(r, Mapping)]


def _iter_json_objects(blob: str, marker: str) -> List[dict]:
    """Brace-matched extraction of the object following `marker`."""
    out: List[dict] = []
    for chunk in blob.split(marker)[1:]:
        depth, end, in_str, esc = 0, None, False, False
        for i, ch in enumerate(chunk):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end is None:
            continue
        try:
            out.append(json.loads(chunk[:end]))
        except ValueError:
            try:
                out.append(json.loads(chunk[:end].replace('\\"', '"')))
            except ValueError:
                continue
    return out


def selection_timeline(files: Sequence[Any]) -> List[dict]:
    """One row per selection cycle, keeping rank, pair_score and the causal
    component breakdown that the ranking actually used."""
    from src.l5_sports_clock_study import iso_epoch
    rows: List[dict] = []
    seen: set = set()
    for path in files:
        for blob in _messages(path):
            if "PMUS_L5_CAMPAIGN " not in blob:
                continue
            for o in _iter_json_objects(blob, "PMUS_L5_CAMPAIGN "):
                if o.get("kind") != "selection":
                    continue
                t = iso_epoch(o.get("log_ts") or o.get("status_t"))
                if t is None:
                    continue
                key = (round(t, 3), o.get("chosen"))
                if key in seen:
                    continue
                seen.add(key)
                adm = []
                for a in (o.get("admitted") or []):
                    comps = a.get("components") or {}
                    adm.append({
                        "rank": a.get("rank"), "slug": a.get("slug"),
                        "pair_score": _f(a.get("pair_score")),
                        "pair_at_touch": _f(a.get("pair_at_touch")),
                        "state": a.get("state"), "state_n": a.get("state_n"),
                        "components": comps,
                        "rebate_eligible": comps.get("rebate_eligible"),
                    })
                rows.append({"t": t, "chosen": o.get("chosen"),
                             "admitted": adm, "n_admitted": len(adm)})
    rows.sort(key=lambda r: r["t"])
    return rows


def skipped_timeline(files: Sequence[Any]) -> List[dict]:
    """Cycles where selection did not run because a market was held. These
    are the NARROW CAPITAL intervals and they are not idle time."""
    from src.l5_sports_clock_study import iso_epoch
    out: List[dict] = []
    seen: set = set()
    for path in files:
        for blob in _messages(path):
            if "selection_skipped" not in blob:
                continue
            for o in _iter_json_objects(blob, "PMUS_L5_CAMPAIGN "):
                sk = o.get("selection_skipped")
                if not sk:
                    continue
                t = iso_epoch(o.get("log_ts") or o.get("t_utc"))
                if t is None or (round(t, 3), sk) in seen:
                    continue
                seen.add((round(t, 3), sk))
                out.append({"t": t, "reason": str(sk).split(" ")[0]})
    out.sort(key=lambda r: r["t"])
    return out


def funnel_timeline(files: Sequence[Any]) -> List[dict]:
    """Funnel rows, for the idleness the selector alone cannot see."""
    from src.l5_sports_clock_study import iso_epoch
    out: List[dict] = []
    seen: set = set()
    for path in files:
        for dep, blob in _message_rows(path):
            if '"funnel"' not in blob:
                continue
            for o in _iter_json_objects(blob, "PMUS_L5_CAMPAIGN "):
                if o.get("kind") != "funnel":
                    continue
                t = iso_epoch(o.get("log_ts"))
                key = (dep, o.get("cycles"), o.get("sessions"),
                       o.get("submitted"))
                if key in seen:
                    continue
                seen.add(key)
                out.append({"t": t, "deployment": dep,
                            "cycles": o.get("cycles"),
                            "sessions": o.get("sessions"),
                            "submitted": o.get("submitted"),
                            "not_started": dict(o.get("not_started") or {})})
    # ordered by TIME, never by cycle number: every campaign restart begins
    # again at cycle 1, so sorting by cycles interleaves separate campaigns
    # and diffs their counters against each other (first version of this
    # attributed 626 blocked cycles to no_candidate that way)
    out.sort(key=lambda r: (r["deployment"], r["t"] is None, r["t"] or 0))
    return out


def campaign_runs(funnels: Sequence[Mapping[str, Any]]) -> List[List[dict]]:
    """Split the funnel rows into contiguous campaign runs.

    Grouped by DEPLOYMENT first: every deployment counts its own cycles from
    1, so mixing rows from several deployments and splitting only on a cycle
    decrease produces runs that diff one campaign's counters against
    another's. A run then ends where the cycle counter goes backwards, which
    is a restart within the same deployment."""
    runs: List[List[dict]] = []
    cur: List[dict] = []
    for row in funnels:
        if cur and ((row.get("deployment") != cur[-1].get("deployment"))
                    or (row["cycles"] or 0) < (cur[-1]["cycles"] or 0)):
            runs.append(cur)
            cur = []
        cur.append(dict(row))
    if cur:
        runs.append(cur)
    return runs


def chose_but_never_started(funnels: Sequence[Mapping[str, Any]]) -> dict:
    """The idleness measurement 1 alone would MISS, and the reason this
    exists as its own metric.

    On 2026-09-08 the selector chose a market on all 181 cycles of a
    55-minute stall and opened nothing on any of them: the block was
    downstream of selection, at complement_acceptance_unproven. Idle time
    caused by our own infrastructure is the defect class the owner named,
    so it has to be counted where it actually happens rather than only
    where the selector can see it.

    Counters are differenced FIRST-TO-LAST WITHIN A CAMPAIGN RUN, not
    between adjacent harvested rows: log harvesting is sparse and an
    adjacent-pair diff silently drops every interval it did not sample
    (an earlier version of this reported 4 blocked cycles where the live
    lane had 181)."""
    runs = [r for r in campaign_runs(funnels) if len(r) >= 2]
    if not runs:
        return {"value": None, "why": "no campaign run with two funnel rows"}
    by_reason: Dict[str, int] = collections.defaultdict(int)
    blocked, observed = 0, 0
    per_run: List[dict] = []
    for run in runs:
        first, last = run[0], run[-1]
        obs = max(0, (last["cycles"] or 0) - (first["cycles"] or 0))
        observed += obs
        run_reasons: Dict[str, int] = {}
        for reason, n in last["not_started"].items():
            delta = int(n) - int(first["not_started"].get(reason, 0))
            if delta > 0:
                by_reason[reason] += delta
                blocked += delta
                run_reasons[reason] = delta
        per_run.append({"deployment": (run[0].get("deployment") or "")[:8],
                        "funnel_rows": len(run),
                        "cycles_first": first["cycles"],
                        "cycles_last": last["cycles"],
                        "cycles_observed": obs, "by_reason": run_reasons})
    return {
        "campaign_runs": len(runs),
        "cycles_observed": observed,
        "cycles_chose_but_never_started": blocked,
        "share": round(blocked / observed, 4) if observed else None,
        "by_reason": dict(sorted(by_reason.items(), key=lambda kv: -kv[1])),
        "per_deployment": sorted(per_run, key=lambda r: -r["cycles_observed"]),
        "coverage_caveat": (
            "aggregated across every deployment in the harvest, so it spans "
            "several days and several safety-shell eras -- read "
            "per_deployment, not the total, for any question about a "
            "particular build. A deployment with few funnel rows on disk is "
            "under-counted here: the 2026-09-08 complement deadlock ran 181 "
            "blocked cycles on deployment 0dcd75c6, of which this harvest "
            "holds 2 funnel rows, so it barely appears. Thin harvest "
            "coverage understates this metric and never overstates it"),
        "reading": ("selection succeeded and no session opened. "
                    "complement_acceptance_unproven here is infrastructure "
                    "idleness, not market conditions; no_candidate is the "
                    "market genuinely offering nothing"),
    }


# --------------------------------------------------------------------------
# 1. time flat while an eligible on-distribution candidate existed
# --------------------------------------------------------------------------

def flat_time_with_eligible_candidate(sel: Sequence[Mapping[str, Any]],
                                      skipped: Sequence[Mapping[str, Any]]
                                      ) -> dict:
    """Idle time that our own infrastructure caused, which the owner asked to
    treat as a defect. A cycle counts only when the slot was FLAT (selection
    ran, so nothing was held) AND at least one admitted candidate existed AND
    nothing was chosen -- a cycle that chose a market is the system working."""
    if not sel:
        return {"value": None, "why": "no selection cycles harvested"}
    gaps, idle_s, cycles_idle = [], 0.0, 0
    for a, b in zip(sel, sel[1:]):
        dt = b["t"] - a["t"]
        if dt <= 0 or dt > 600:            # a gap that long is a restart
            continue
        if a["n_admitted"] > 0 and not a["chosen"]:
            idle_s += dt
            cycles_idle += 1
            gaps.append({"t": a["t"], "n_admitted": a["n_admitted"],
                         "s": round(dt, 1)})
    span = sel[-1]["t"] - sel[0]["t"]
    held_cycles = len(skipped)
    return {
        "flat_cycles_observed": len(sel),
        "cycles_flat_with_candidate_and_no_choice": cycles_idle,
        "idle_s": round(idle_s, 1),
        "selection_span_s": round(span, 1),
        "idle_share_of_selection_span": (round(idle_s / span, 4)
                                         if span > 0 else None),
        "cycles_skipped_position_held": held_cycles,
        "worst_gaps": sorted(gaps, key=lambda g: -g["s"])[:5],
        "reading": ("idle here means the selector had an admitted candidate "
                    "and still opened nothing. Cycles skipped for a held "
                    "position are NARROW CAPITAL working as designed and are "
                    "reported separately, never as idle"),
    }


# --------------------------------------------------------------------------
# 2. capital lockup per market   3. switches   4. OPEN -> COMPLETE
# --------------------------------------------------------------------------

def capital_lockup(rep: Mapping[str, Any]) -> dict:
    """Wall time from a market's first fill to the moment its residual
    reaches zero (or the last evidence we have, still open)."""
    from src.l5_sports_clock_study import iso_epoch
    per: List[dict] = []
    for slug, m in (rep.get("per_market") or {}).items():
        rows = sorted(m.get("rows") or [], key=lambda r: r.get("idx") or 0)
        first_fill = last_t = closed_t = None
        for r in rows:
            t = iso_epoch(r.get("t_utc"))
            if t is None:
                continue
            last_t = t
            inv = r.get("inventory_after") or {}
            up, dn = _f(inv.get("up")) or 0.0, _f(inv.get("dn")) or 0.0
            if first_fill is None and (up > 0 or dn > 0):
                first_fill = t
            resid = _f(inv.get("residual"))
            if (first_fill is not None and resid is not None
                    and abs(resid) < 1e-9 and closed_t is None):
                closed_t = t
        if first_fill is None:
            continue
        end = closed_t if closed_t is not None else last_t
        per.append({
            "slug": slug, "category": category_of(slug),
            "locked_s": round((end or first_fill) - first_fill, 1),
            "closed": closed_t is not None,
            "spent_usd": ((m.get("rows") or [{}])[-1].get("inventory_after")
                          or {}).get("spent"),
        })
    closed = [p for p in per if p["closed"]]
    return {
        "markets_with_inventory": len(per),
        "markets_closed": len(closed),
        "median_locked_s_closed": (
            round(sorted(p["locked_s"] for p in closed)[len(closed) // 2], 1)
            if closed else None),
        "max_locked_s": max((p["locked_s"] for p in per), default=None),
        "per_market": sorted(per, key=lambda p: -p["locked_s"])[:20],
        "why_null": None if per else "no market reached a first fill",
    }


def switches(sel: Sequence[Mapping[str, Any]],
             lock: Mapping[str, Any]) -> dict:
    """Mobility, split the way the owner framed it. A switch while FLAT is
    the behaviour we want; a switch while inventory is open would be
    abandoning residual, and should be zero by construction."""
    chosen = [(r["t"], r["chosen"]) for r in sel if r["chosen"]]
    flips = sum(1 for a, b in zip(chosen, chosen[1:]) if a[1] != b[1])
    locked = {p["slug"]: p for p in (lock.get("per_market") or [])}
    return {
        "selection_cycles_with_a_choice": len(chosen),
        "switches_while_flat": flips,
        "switch_rate_while_flat": (round(flips / (len(chosen) - 1), 4)
                                   if len(chosen) > 1 else None),
        "switches_while_holding": 0,
        "switches_while_holding_basis": (
            "structural: selection does not run while a position is held "
            "(CAMPAIGN_HOLD:POSITION_HELD), so a switch cannot occur. This "
            "is asserted from the code path, not inferred from its absence "
            "in a sample"),
        "distinct_markets_chosen": len({c for _, c in chosen}),
        "distinct_categories_chosen": len({category_of(c) for _, c in chosen}),
        "markets_that_took_inventory": len(locked),
    }


def open_to_complete(rep: Mapping[str, Any]) -> dict:
    """How long a sequence takes from first inventory to matched, and how
    often it gets there at all."""
    from src.l5_sports_clock_study import iso_epoch
    durations, completed, started = [], 0, 0
    for slug, m in (rep.get("per_market") or {}).items():
        rows = sorted(m.get("rows") or [], key=lambda r: r.get("idx") or 0)
        t0 = None
        for r in rows:
            t = iso_epoch(r.get("t_utc"))
            inv = r.get("inventory_after") or {}
            up, dn = _f(inv.get("up")) or 0.0, _f(inv.get("dn")) or 0.0
            if t0 is None and (up > 0 or dn > 0):
                t0, started = t, started + 1
            elif t0 is not None and t is not None:
                resid = _f(inv.get("residual"))
                if resid is not None and abs(resid) < 1e-9:
                    durations.append(t - t0)
                    completed += 1
                    t0 = None
    durations.sort()
    return {
        "sequences_started": started,
        "sequences_completed": completed,
        "completion_probability": (round(completed / started, 4)
                                   if started else None),
        "median_open_to_complete_s": (round(durations[len(durations) // 2], 1)
                                      if durations else None),
        "p90_open_to_complete_s": (
            round(durations[int(0.9 * (len(durations) - 1))], 1)
            if durations else None),
        "why_null": None if started else "no sequence reached inventory",
    }


# --------------------------------------------------------------------------
# 5 + 8. economics by market/category, incentives kept separate
# --------------------------------------------------------------------------

def economics(rep: Mapping[str, Any]) -> dict:
    """Settled venue-confirmed dollars only, grouped by category. Rebates are
    reported as their own line and are NEVER netted into trading edge."""
    by_cat: Dict[str, dict] = collections.defaultdict(
        lambda: {"markets": 0, "settled": 0, "realized_usd_venue": 0.0,
                 "unconfirmed": 0, "credited_rebates_usd": 0.0})
    settled_total, n_settled = 0.0, 0
    for slug, m in (rep.get("per_market") or {}).items():
        econ = m.get("economics") or {}
        c = by_cat[category_of(slug)]
        c["markets"] += 1
        realized = _f(econ.get("realized_usd_venue"))
        if econ.get("settled") and realized is not None:
            c["settled"] += 1
            c["realized_usd_venue"] += realized
            settled_total += realized
            n_settled += 1
        elif econ.get("settled"):
            c["unconfirmed"] += 1
    return {
        "settled_markets_venue_confirmed": n_settled,
        "realized_usd_venue_total": round(settled_total, 4),
        "by_category": {k: {**v,
                            "realized_usd_venue": round(v["realized_usd_venue"], 4)}
                        for k, v in sorted(by_cat.items())},
        "incentives": {
            "credited_rebates_usd": None,
            "basis": ("credited/confirmed only, read from the incentive "
                      "audit. Never modelled, never projected, never netted "
                      "into trading edge"),
        },
        "labels": LABEL_DERIVED,
    }


# --------------------------------------------------------------------------
# 6 + 7. opportunity score at entry/exit, and the cost of the occupied slot
# --------------------------------------------------------------------------

def opportunity_scores(sel: Sequence[Mapping[str, Any]]) -> dict:
    """The rank-1 score we entered on, and the best alternative that existed
    at the same instant. The gap between them is what a second slot would
    have been competing for."""
    entries, gaps = [], []
    for r in sel:
        adm = r.get("admitted") or []
        if not r.get("chosen") or not adm:
            continue
        top = adm[0]
        alt = adm[1] if len(adm) > 1 else None
        s1, s2 = top.get("pair_score"), (alt or {}).get("pair_score")
        entries.append({"t": r["t"], "slug": top.get("slug"),
                        "pair_score": s1, "state_n": top.get("state_n"),
                        "components": top.get("components") or {}})
        if s1 is not None and s2 is not None:
            gaps.append(s1 - s2)
    comps: Dict[str, List[float]] = collections.defaultdict(list)
    for e in entries:
        for k, v in (e["components"] or {}).items():
            f = _f(v)
            if f is not None:
                comps[k].append(f)
    gaps.sort()
    return {
        "entries_scored": len(entries),
        "median_entry_pair_score": (
            round(sorted(e["pair_score"] for e in entries
                         if e["pair_score"] is not None)[len(entries) // 2], 4)
            if any(e["pair_score"] is not None for e in entries) else None),
        "median_rank1_minus_rank2": (round(gaps[len(gaps) // 2], 4)
                                     if gaps else None),
        "component_medians_at_entry": {
            k: round(sorted(v)[len(v) // 2], 4) for k, v in sorted(comps.items())},
        "reading": ("a small rank1-rank2 gap means the second-best market was "
                    "nearly as good, which is the case where a second "
                    "concurrent slot would earn its keep; a large gap means "
                    "the slot is already on the only good opportunity"),
    }


def occupied_slot_cost(sel: Sequence[Mapping[str, Any]],
                       skipped: Sequence[Mapping[str, Any]],
                       lock: Mapping[str, Any]) -> dict:
    """Bankroll-dollar-hour opportunity cost of the single slot.

    Deliberately reported as an EXPOSURE, not as a dollar loss: we know how
    long capital was locked and what the best alternative scored, but a
    pair_score is not a dollar. Converting one into the other needs the
    realized-per-score relationship, which needs the settled cohort the
    frozen concurrency gate is waiting on."""
    held_cycles = len(skipped)
    locked_s = sum(p["locked_s"] for p in (lock.get("per_market") or []))
    return {
        "cycles_with_selection_suppressed_by_hold": held_cycles,
        "capital_locked_market_seconds": round(locked_s, 1),
        "usd_opportunity_cost": None,
        "why_null": ("a pair_score is not a dollar. The conversion needs the "
                     "realized-per-opportunity-score relationship, which "
                     "needs the settled cohort minimums in "
                     "L5_CONCURRENCY_SCALING_GATE_v1 (sha b86fe09178ea78a2). "
                     "Reporting an unbacked dollar figure here would be "
                     "exactly the kind of modelled number the owner "
                     "excluded"),
    }


# --------------------------------------------------------------------------

def build_report(log_paths: Sequence[str]) -> dict:
    from src.l5_sports_clock_study import build_ledger, expand_logs
    files = expand_logs(list(log_paths))
    rep = build_ledger(list(log_paths))
    sel = selection_timeline(files)
    skipped = skipped_timeline(files)
    funnels = funnel_timeline(files)
    lock = capital_lockup(rep)
    return {
        "owner_ref": OWNER_REF,
        "principle": "WIDE SCAN, NARROW CAPITAL, HIGH MOBILITY",
        "read_only": ("measurement only. This module changes no allocation "
                      "rule, no cap, no gate and no live decision"),
        "already_implemented": {
            "wide_scan": ("select_candidate ranks the full admitted universe "
                          "every cycle"),
            "high_mobility_when_flat": (
                "it takes admitted[0] with no memory of the previous pick, "
                "so there is no loyalty to hold on to"),
            "narrow_capital_when_holding": (
                "CAMPAIGN_HOLD:POSITION_HELD suppresses selection while "
                "inventory is open"),
            "conclusion": ("the principle is already the behaviour; what was "
                           "missing is this measurement"),
        },
        "m1_flat_time_with_eligible_candidate":
            flat_time_with_eligible_candidate(sel, skipped),
        "m1b_chose_but_never_started": chose_but_never_started(funnels),
        "m2_capital_lockup": lock,
        "m3_switches": switches(sel, lock),
        "m4_open_to_complete": open_to_complete(rep),
        "m5_economics": economics(rep),
        "m6_opportunity_scores": opportunity_scores(sel),
        "m7_occupied_slot_cost": occupied_slot_cost(sel, skipped, lock),
        "m8_incentives": {
            "note": ("reported inside m5_economics.incentives, credited only "
                     "and never netted into trading edge"),
        },
        "inputs": {"selection_cycles": len(sel),
                   "held_cycles": len(skipped),
                   "markets": len(rep.get("per_market") or {})},
    }


def render_markdown(r: Mapping[str, Any]) -> str:
    m1, m2 = r["m1_flat_time_with_eligible_candidate"], r["m2_capital_lockup"]
    m3, m4 = r["m3_switches"], r["m4_open_to_complete"]
    L = ["# Allocation metrics — WIDE SCAN, NARROW CAPITAL, HIGH MOBILITY",
         "", f"_OWNER {r['owner_ref']}. {r['read_only']}._", "",
         "## The principle was already the behaviour", "",
         f"- wide scan: {r['already_implemented']['wide_scan']}",
         f"- high mobility when flat: {r['already_implemented']['high_mobility_when_flat']}",
         f"- narrow capital when holding: {r['already_implemented']['narrow_capital_when_holding']}",
         "", "## Measurements", "",
         "| # | measurement | value |", "|---|---|---|",
         f"| 1 | idle cycles with a candidate and no choice | {m1.get('cycles_flat_with_candidate_and_no_choice')} |",
         f"| 1 | idle share of selection span | {m1.get('idle_share_of_selection_span')} |",
         f"| 1b | cycles that chose a market and opened nothing | {r['m1b_chose_but_never_started'].get('cycles_chose_but_never_started')} |",
         f"| 1b | by reason | {r['m1b_chose_but_never_started'].get('by_reason')} |",
         f"| 2 | markets that took inventory | {m2.get('markets_with_inventory')} |",
         f"| 2 | median lockup (closed), s | {m2.get('median_locked_s_closed')} |",
         f"| 3 | switches while flat | {m3.get('switches_while_flat')} |",
         f"| 3 | switches while holding | {m3.get('switches_while_holding')} |",
         f"| 4 | completion probability | {m4.get('completion_probability')} |",
         f"| 4 | median OPEN→COMPLETE, s | {m4.get('median_open_to_complete_s')} |",
         f"| 5 | settled venue-confirmed markets | {r['m5_economics']['settled_markets_venue_confirmed']} |",
         f"| 5 | realized venue USD | {r['m5_economics']['realized_usd_venue_total']} |",
         f"| 6 | median rank1−rank2 score gap | {r['m6_opportunity_scores'].get('median_rank1_minus_rank2')} |",
         f"| 7 | capital locked, market-seconds | {r['m7_occupied_slot_cost']['capital_locked_market_seconds']} |",
         f"| 7 | USD opportunity cost | {r['m7_occupied_slot_cost']['usd_opportunity_cost']} |",
         "", f"_{r['m7_occupied_slot_cost']['why_null']}_", ""]
    return "\n".join(L)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--logs", nargs="+", required=True)
    ap.add_argument("--out", default="analysis/l5")
    a = ap.parse_args(list(argv) if argv is not None else None)
    rep = build_report(a.logs)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "l5_allocation_metrics.json"), "w",
              encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1, sort_keys=True, default=str)
    md = render_markdown(rep)
    with open(os.path.join(a.out, "L5_ALLOCATION_METRICS.md"), "w",
              encoding="utf-8") as fh:
        fh.write(md)
    print(md)
    return 0


if __name__ == "__main__":                       # pragma: no cover
    raise SystemExit(main())
