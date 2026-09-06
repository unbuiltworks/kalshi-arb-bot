"""L5 FIDELITY GATE (OWNER 5556232035 item 5).

Phase order: REPLICATE L5 -> PROVE FIDELITY -> RUN L5 -> MEASURE REALIZED
RESULTS -> THEN OPTIMIZE PROFITABILITY. This module decides the label
"L5 replication in validation" vs "L5 operational" from the frozen spec
analysis/l5/l5_fidelity_gate.json and the evidence it names. Realized
P&L is never an input.

Evidence:
  * bounded LIVE: PMUS spring session ``decisions[]`` records (as logged
    in session_end / the ledger's session_report rows) plus their
    ``orders[]``;
  * realtime SHADOW: the Gemini shadow rolling_report
    (action_scoring_untouched / research_arm_stochastic);
  * historical holdout: l5_fidelity_pass.json (reported where present).

Every dimension is reported with its measured value, its threshold, its
sample size, and whether it PASSES, FAILS, is UNMEASURED, or is a
DECLARED OVERRIDE (an owner safety rule that is logged, not excused).
"""
from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "analysis" / "l5" / "l5_fidelity_gate.json"
MODEL_PATH = ROOT / "analysis" / "l5" / "l5_action_model_v2.json"
RELATIONS = ("complete", "extend", "open")


def load_spec(path: Path = SPEC_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _f(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _masses(d: Mapping[str, Any]) -> dict:
    out = {k: 0.0 for k in RELATIONS}
    for r in d.get("relation_ranked") or []:
        if isinstance(r, Mapping) and r.get("relation") in out:
            out[r["relation"]] = _f(r.get("p"), 0.0) or 0.0
    return out


def _freq(items: Iterable[Mapping[str, Any]], key: str) -> tuple[dict, int]:
    c = {k: 0 for k in RELATIONS}
    n = 0
    for d in items:
        v = d.get(key)
        if v in c:
            c[v] += 1
            n += 1
    return ({k: round(c[k] / n, 4) if n else 0.0 for k in RELATIONS}, n)


def _expected(items: list) -> dict:
    n = len(items) or 1
    agg = {k: 0.0 for k in RELATIONS}
    for d in items:
        for k, v in _masses(d).items():
            agg[k] += v
    return {k: round(v / n, 4) for k, v in agg.items()}


def _l1(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    return round(sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in RELATIONS), 4)


def _sampled(d: Mapping[str, Any]) -> Optional[str]:
    sh = d.get("shadow") or {}
    st = sh.get("stochastic") or {}
    return st.get("sampled")


def _imbalance_units(d: Mapping[str, Any]) -> float:
    state = str(d.get("state") or "")
    # state key: price band | tte bucket | imbalance bucket ('flat',
    # 'imb1-10', 'imb10+' ...); the numeric residual, when present, wins
    inv = d.get("inventory_before") or {}
    up, dn = _f(inv.get("up"), 0.0), _f(inv.get("dn"), 0.0)
    unit = _f(d.get("behavioral_unit"))
    if unit and unit > 0:
        return abs((up or 0.0) - (dn or 0.0)) / unit
    if "|flat" in state:
        return 0.0
    if "imb" in state:
        return 1.0
    return 0.0


def model_imbalanced_share(model: Optional[Mapping[str, Any]]) -> Optional[float]:
    """L5's share of transitions taken from imbalanced base states."""
    if not model:
        return None
    base = model.get("base") or {}
    tot = imb = 0
    for state, dist in base.items():
        n = sum(int(v) for v in (dist or {}).values()) if isinstance(dist, Mapping) else 0
        tot += n
        if "|flat" not in str(state):
            imb += n
    return round(imb / tot, 4) if tot else None


def _dim(status: str, value: Any, threshold: Any, n: int, **extra: Any) -> dict:
    return {"status": status, "value": value, "threshold": threshold,
            "n": n, **extra}


def score_live(decisions: list, orders: list, spec: Mapping[str, Any],
               model: Optional[Mapping[str, Any]] = None,
               maintenance: Optional[list] = None) -> dict:
    """Score the bounded live evidence, dimension by dimension."""
    dims = spec["dimensions"]
    me = spec["minimum_evidence"]
    decs = [d for d in decisions if d.get("relation_ranked")]
    by_market: dict = {}
    for d in decs:
        by_market[str(d.get("slug"))] = by_market.get(str(d.get("slug")), 0) + 1
    ts = sorted(str(d["t_utc"]) for d in decs if d.get("t_utc"))
    out: dict[str, Any] = {"scored_decisions": len(decs),
                           "markets": len({d.get("slug") for d in decs
                                           if d.get("slug")}),
                           "decisions_by_market": by_market,
                           "unique_states": len({d.get("state") for d in decs}),
                           "first_t_utc": ts[0] if ts else None,
                           "last_t_utc": ts[-1] if ts else None}
    enough = len(decs) >= me["live_scored_decisions"]

    # --- action distribution (relation)
    exp_all = _expected(decs) if decs else {}
    samp, n_s = _freq(decs, "sampled_relation")
    if n_s == 0:
        samp, n_s = _freq([{"sampled_relation": _sampled(d)} for d in decs],
                          "sampled_relation")
    acted, n_a = _freq(decs, "acted")
    holds = sum(1 for d in decs if d.get("acted") == "hold")
    l1_s = _l1(samp, exp_all) if decs else None
    # HOLD counts as disagreement: acted freq over ALL decisions
    acted_all = {k: round(acted[k] * n_a / len(decs), 4) if decs else 0.0
                 for k in RELATIONS}
    l1_a = _l1(acted_all, exp_all) if decs else None
    thr = dims["action_distribution_relation"]["pass"]
    ok = (l1_s is not None and l1_s <= thr["l1_sampled_vs_l5_max"]
          and l1_a is not None and l1_a <= thr["l1_acted_vs_l5_max"])
    refusals: dict = {}
    for d in decs:
        if d.get("acted") == "hold":
            k = str(d.get("refusal")).split(":")[0]
            refusals[k] = refusals.get(k, 0) + 1
    out["action_distribution_relation"] = _dim(
        "UNMEASURED" if not decs else ("PASS" if ok and enough
                                       else ("INSUFFICIENT" if ok else "FAIL")),
        {"l1_sampled_vs_l5": l1_s, "l1_acted_vs_l5": l1_a,
         "expected_l5": exp_all, "sampled": samp, "acted_incl_hold": acted_all,
         "hold_share": round(holds / len(decs), 4) if decs else None,
         "hold_refusals": refusals},
        thr, len(decs))

    # --- side convention (OPEN decisions only)
    opens = [o for o in orders if o.get("relation") == "open"]
    agree = [o for o in opens if o.get("side_convention_ok") is not None]
    if agree:
        rate = sum(1 for o in agree if o["side_convention_ok"]) / len(agree)
    else:
        rate = None
    thr = dims["action_distribution_side"]["pass"]
    out["action_distribution_side"] = _dim(
        "UNMEASURED" if rate is None else (
            "PASS" if rate >= thr["live_side_convention_agreement_min"] else "FAIL"),
        rate, thr, len(agree),
        note="live OPEN sides follow the shared cheaper-touch convention by "
             "construction; the check is against the recorded convention flag")

    # --- post-fill complete gap
    post = [d for d in decs if _imbalance_units(d) >= 1]
    thr = dims["post_fill_complete_gap"]["pass"]
    if post:
        exp_p = _expected(post)
        s_p, _ = _freq([{"s": _sampled(d)} for d in post], "s")
        a_p, n_ap = _freq(post, "acted")
        a_all = {k: round(a_p[k] * n_ap / len(post), 4) for k in RELATIONS}
        gap_s = round(100 * (s_p["complete"] - exp_p["complete"]), 1)
        gap_a = round(100 * (a_all["complete"] - exp_p["complete"]), 1)
        ok = (abs(gap_s) <= thr["abs_gap_pp_max_sampled"]
              and abs(gap_a) <= thr["abs_gap_pp_max_acted"])
        st = ("PASS" if ok and len(post) >= me["live_post_fill_decisions"]
              else ("INSUFFICIENT" if ok else "FAIL"))
        val = {"gap_pp_sampled": gap_s, "gap_pp_acted": gap_a,
               "expected_l5": exp_p, "sampled": s_p, "acted_incl_hold": a_all}
    else:
        st, val = "UNMEASURED", None
    out["post_fill_complete_gap"] = _dim(st, val, thr, len(post))

    # --- price / aggressiveness
    thr = dims["price_aggressiveness"]["pass"]
    joined = [o for o in orders if o.get("touch_join") is not None]
    join_rate = (sum(1 for o in joined if o["touch_join"]) / len(joined)
                 if joined else None)
    p_taker_l5 = [_f(((d.get("shadow") or {}).get("l5_target_aggressiveness")
                      or {}).get("p_taker")) for d in decs]
    p_taker_l5 = [p for p in p_taker_l5 if p is not None]
    taker_gap = (round(abs(0.0 - statistics.fmean(p_taker_l5)), 4)
                 if p_taker_l5 else None)
    out["price_aggressiveness"] = _dim(
        "UNMEASURED" if join_rate is None else (
            "PASS" if join_rate >= thr["touch_join_rate_min"] else "FAIL"),
        {"touch_join_rate": join_rate, "live_taker_share": 0.0,
         "l5_p_taker_mean": (round(statistics.fmean(p_taker_l5), 4)
                             if p_taker_l5 else None),
         "taker_share_abs_gap": taker_gap},
        thr, len(joined),
        override="override:taker_disabled (owner rule; reported, not blocking)")

    # --- size
    thr = dims["size"]["pass"]
    ratios = []
    for o in orders:
        ex = _f((o.get("executed_safe_size") or {}).get("contracts"))
        p50 = _f((o.get("l5_target_size") or {}).get("shares_p50"))
        if ex and p50:
            ratios.append(ex / p50)
    med = round(statistics.median(ratios), 6) if ratios else None
    out["size"] = _dim(
        "UNMEASURED" if med is None else (
            "PASS" if med >= thr["median_size_ratio_min"] else "FAIL"),
        {"median_executed_over_l5_p50": med}, thr, len(ratios),
        override="override:size_cap ($0.05 market cap / venue minimum; "
                 "reported, not blocking)")

    # --- timing / cadence: intervals only between CONSECUTIVE unique
    # decisions of the same session (OWNER 5556815940 #6) -- never across
    # session or duplicate boundaries; sessions without an id fall back
    # to the market as the grouping key
    thr = dims["timing_cadence"]["pass"]
    intervals, ratios_c = [], []
    # OWNER 5557988072 #6: an interval that overlaps a declared venue
    # maintenance window is a planned outage, never cadence evidence
    mwin = [(float(w.get("start") or 0), float(w.get("end") or 0))
            for w in (maintenance or []) if w.get("start") and w.get("end")]
    excluded_m = 0
    by_slug: dict[str, list] = {}
    for d in decs:
        t = d.get("t_utc")
        if t and d.get("slug"):
            key = d.get("session_id") or d["slug"]
            by_slug.setdefault(key, []).append(d)
    for slug, ds in by_slug.items():
        ds = sorted(ds, key=lambda d: d["t_utc"])
        for a, b in zip(ds, ds[1:]):
            try:
                ta = datetime.fromisoformat(str(a["t_utc"]))
                tb = datetime.fromisoformat(str(b["t_utc"]))
            except ValueError:
                continue
            dt = (tb - ta).total_seconds()
            cad = _f(a.get("next_reevaluation_s"))
            if mwin:
                sa, sb = ta.timestamp(), tb.timestamp()
                if any(sa <= e and sb >= s for s, e in mwin):
                    excluded_m += 1
                    continue
            if dt > 0:
                intervals.append(dt)
                if cad and cad > 0:
                    ratios_c.append(dt / cad)
    ratio = round(statistics.median(ratios_c), 3) if ratios_c else None
    ok = ratio is not None and thr["cadence_ratio_min"] <= ratio <= thr["cadence_ratio_max"]
    out["timing_cadence"] = _dim(
        "UNMEASURED" if ratio is None else (
            "PASS" if ok and len(ratios_c) >= me["live_timed_intervals"]
            else ("INSUFFICIENT" if ok else "FAIL")),
        {"median_interval_s": (round(statistics.median(intervals), 1)
                               if intervals else None),
         "median_interval_over_model_cadence": ratio,
         "intervals_excluded_maintenance": excluded_m},
        thr, len(ratios_c))
    out["intervals_excluded_maintenance"] = excluded_m

    # --- inventory trajectory
    thr = dims["inventory_trajectory"]["pass"]
    units = [_imbalance_units(d) for d in decs]
    live_share = (round(sum(1 for u in units if u >= 1) / len(units), 4)
                  if units else None)
    l5_share = model_imbalanced_share(model)
    gap = (round(abs(live_share - l5_share), 4)
           if live_share is not None and l5_share is not None else None)
    mx = max(units) if units else None
    ok = (gap is not None and gap <= thr["imbalanced_share_abs_gap_max"]
          and mx is not None and mx <= thr["max_imbalance_units_max"])
    out["inventory_trajectory"] = _dim(
        "UNMEASURED" if live_share is None or l5_share is None else (
            "PASS" if ok and enough else ("INSUFFICIENT" if ok else "FAIL")),
        {"live_imbalanced_share": live_share, "l5_imbalanced_share": l5_share,
         "abs_gap": gap, "max_imbalance_units": mx},
        thr, len(units), override="residual cap 3 units (owner rule)")

    # --- maker / taker
    thr = dims["maker_taker"]["pass"]
    out["maker_taker"] = _dim(
        "UNMEASURED" if not p_taker_l5 else (
            "PASS" if taker_gap is not None and taker_gap <= thr["maker_share_abs_gap_max"]
            else "FAIL"),
        {"live_maker_share": 1.0,
         "l5_maker_share_mean": (round(1 - statistics.fmean(p_taker_l5), 4)
                                 if p_taker_l5 else None),
         "abs_gap": taker_gap},
        thr, len(p_taker_l5),
        override="override:taker_disabled (owner rule; reported, not blocking)")

    # --- off-distribution
    thr = dims["off_distribution_rate"]["pass"]
    off = (round(100.0 * sum(1 for d in decs if d.get("off_distribution")) / len(decs), 2)
           if decs else None)
    out["off_distribution_rate"] = _dim(
        "UNMEASURED" if off is None else (
            "PASS" if off <= thr["max_pct"] and enough
            else ("INSUFFICIENT" if off <= thr["max_pct"] else "FAIL")),
        off, thr, len(decs))
    return out


def score_shadow(rolling: Optional[Mapping[str, Any]], spec: Mapping[str, Any]) -> dict:
    """Realtime-shadow evidence from the Gemini rolling report."""
    if not rolling:
        return {"status": "UNMEASURED", "note": "no rolling report given"}
    ra = rolling.get("research_arm_stochastic") or {}
    # OWNER 5558646019 #2/#3: the gate consumes ONLY rows whose lookup
    # version is proven (the canonical v2 lookup). A report without lookup
    # provenance cannot prove which lookup produced a row -> fail closed
    # (UNMEASURED with the reason); the v1 numbers stay a diagnostic.
    prov = ra.get("lookup_provenance")
    gate_sc = ra.get("action_scoring_corrected_gate") or {}
    # the service and this evaluator must agree on WHICH lookup version is
    # canonical; a service gating on another version proves nothing here
    svc_ver = (prov or {}).get("lookup_version_gate")
    if prov is None:
        sc, lookup_status = {}, "UNPROVABLE_LOOKUP_VERSION: report carries no lookup_provenance"
    elif svc_ver is not None and int(svc_ver) != CANONICAL_LOOKUP_VERSION:
        sc, lookup_status = {}, (f"LOOKUP_VERSION_MISMATCH: service gates lookup_version "
                                 f"{svc_ver}, evaluator requires {CANONICAL_LOOKUP_VERSION}")
    elif int(gate_sc.get("scored_decisions") or 0) == 0:
        sc, lookup_status = {}, "NO_CANONICAL_LOOKUP_ROWS_YET"
    else:
        sc, lookup_status = gate_sc, f"OK:lookup_version_{CANONICAL_LOOKUP_VERSION}"
    thr = spec["dimensions"]["action_distribution_relation"]["pass"]
    thr_p = spec["dimensions"]["post_fill_complete_gap"]["pass"]
    thr_o = spec["dimensions"]["off_distribution_rate"]["pass"]
    # canonical count (OWNER 5557209522 #4): unique ENTERED windows since
    # the freeze -- the same set the frozen arm spec scores; falls back to
    # the older fields only when the service predates the provenance keys
    windows = (ra.get("windows_entered_unique")
               if ra.get("windows_entered_unique") is not None
               else (ra.get("windows_since_freeze") or ra.get("windows") or 0))
    enough = int(windows) >= spec["minimum_evidence"]["shadow_windows"]
    l1s, l1a = _f(sc.get("l1_sampled_vs_l5")), _f(sc.get("l1_acted_vs_l5"))
    post = sc.get("post_fill") or {}
    gap_s = _f(post.get("complete_freq_gap_pp_sampled"))
    gap_a = _f(post.get("complete_freq_gap_pp_acted"))
    off = _f(sc.get("off_distribution_rate_pct"))

    def st(ok: Optional[bool]) -> str:
        if ok is None:
            return "UNMEASURED"
        if not ok:
            return "FAIL"
        return "PASS" if enough else "INSUFFICIENT"
    return {
        "arm": ra.get("arm"), "windows_since_freeze": windows,
        # OWNER 5557786528 #6: raw / unique / dropped, only unique is wired
        "shadow_raw_windows": ra.get("shadow_raw_windows", ra.get("window_rows")),
        "shadow_unique_windows": ra.get("shadow_unique_windows", windows),
        "shadow_duplicates_dropped": ra.get("shadow_duplicates_dropped"),
        "shadow_first_entry_utc": ra.get("first_entry_utc"),
        "shadow_last_entry_utc": ra.get("last_entry_utc"),
        "lookup_status": lookup_status,
        "lookup_provenance": prov,
        "windows_entered_unique_v2": (prov or {}).get("v2_windows"),
        "off_distribution_corrected_gate": ra.get("off_distribution_corrected_gate"),
        "off_distribution_v1_diagnostic": ra.get("off_distribution_v1_diagnostic"),
        "action_scoring_v1_diagnostic": ra.get("action_scoring_v1_diagnostic"),
        "action_distribution_relation": _dim(
            st(None if l1s is None else (l1s <= thr["l1_sampled_vs_l5_max"]
                                         and (l1a is None or l1a <= thr["l1_acted_vs_l5_max"]))),
            {"l1_sampled_vs_l5": l1s, "l1_acted_vs_l5": l1a}, thr,
            int(sc.get("scored_decisions") or 0)),
        "post_fill_complete_gap": _dim(
            st(None if gap_s is None else (abs(gap_s) <= thr_p["abs_gap_pp_max_sampled"]
                                           and (gap_a is None or abs(gap_a) <= thr_p["abs_gap_pp_max_acted"]))),
            {"gap_pp_sampled": gap_s, "gap_pp_acted": gap_a}, thr_p,
            int(post.get("decisions") or 0)),
        "off_distribution_rate": _dim(
            st(None if off is None else off <= thr_o["max_pct"]),
            off, thr_o, int(sc.get("scored_decisions") or 0)),
    }


def verdict(live: Mapping[str, Any], shadow: Mapping[str, Any],
            spec: Mapping[str, Any]) -> dict:
    """The label, from the dimensions only."""
    blocking, overrides, unmeasured, failing, insufficient = [], [], [], [], []
    for name, dim in list(live.items()) + [(f"shadow:{k}", v) for k, v in shadow.items()]:
        if not isinstance(dim, Mapping) or "status" not in dim:
            continue
        s = dim["status"]
        if dim.get("override"):
            overrides.append({"dimension": name, "status": s,
                              "override": dim["override"]})
            continue
        if s == "PASS":
            continue
        {"FAIL": failing, "INSUFFICIENT": insufficient,
         "UNMEASURED": unmeasured}.get(s, blocking).append(name)
    met = not failing and not insufficient and not unmeasured
    return {
        "label": spec["labels"]["when_met"] if met else spec["labels"]["until_met"],
        "phase_1_met": met,
        "failing": failing, "insufficient_sample": insufficient,
        "unmeasured": unmeasured, "declared_overrides": overrides,
        "rule": spec["verdict_rule"],
        "labels": "decided from fidelity dimensions only; realized P&L is not an input",
    }


CANONICAL_LOOKUP_VERSION = 3          # == pmus_l5_persistence_v2.CANONICAL_LOOKUP_VERSION
CLEAN_ROW_RULE = ("evidence_schema >= 2 (or the post-mark_noop fields attempted / "
                  "size_transformations) AND lookup.lookup_version == "
                  f"{CANONICAL_LOOKUP_VERSION} (canonical state lookup); anything "
                  "else is LEGACY / UNVERIFIABLE")
CADENCE_REF_PATH = Path("analysis/l5/l5_cadence_behavioral_unit.json")


def is_clean_row(d: Mapping[str, Any]) -> bool:
    """Post-mark_noop evidence row written by the canonical lookup (OWNER
    5558167805 #1, 5558646019 #3: the gate must prove which lookup
    produced a row)."""
    post_fix = False
    try:
        post_fix = int(d.get("evidence_schema") or 0) >= 2
    except (TypeError, ValueError):
        pass
    post_fix = post_fix or "size_transformations" in d or "attempted" in d
    lk = d.get("lookup")
    try:
        ver = int((lk or {}).get("lookup_version") or 0) if isinstance(lk, Mapping) else 0
    except (TypeError, ValueError):
        ver = 0
    return post_fix and ver == CANONICAL_LOOKUP_VERSION


def load_cadence_reference(path: Path = CADENCE_REF_PATH) -> Optional[dict]:
    """Both historical L5 cadence units, persisted as diagnostics (OWNER
    5558167805 #6): the fill-arrival gap (the frozen model's unit) and the
    order-episode gap (fresh decisions, same-price partial fills collapsed)."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    fill = doc.get("trade_to_trade_gap_s") or {}
    epi = doc.get("order_episode_start_to_start_gap_s") or {}
    by_rel = doc.get("order_episode_gap_by_relation_s") or {}
    return {"fill_gap_reference": {
                "unit": "consecutive L5 fills in one window (public activity)",
                **{k: fill.get(k) for k in ("n", "median", "p75", "p90", "buckets")}},
            "order_episode_gap_reference": {
                "unit": "consecutive L5 order episodes (same outcome/side/price "
                        "fills collapsed) start-to-start",
                **{k: epi.get(k) for k in ("n", "median", "p75", "p90", "buckets")},
                "by_relation": {r: {k: v.get(k) for k in ("n", "median", "p75", "p90")}
                                for r, v in by_rel.items() if isinstance(v, dict)}}}


def summarize_complete_counterfactual(decs: list, orders: list) -> dict:
    """OWNER 5558167805 #7: the post-fix COMPLETE sample -- L5 touch vs the
    executed fee-safe price, reported separately; no price optimization."""
    rows = [d.get("counterfactual_l5_price") for d in decs
            if isinstance(d.get("counterfactual_l5_price"), Mapping)]
    if not rows:
        return {"n": 0}
    deltas = [float(r["delta_ticks"]) for r in rows if r.get("delta_ticks") not in (None, "None")]
    classes: dict = {}
    for r in rows:
        c = r.get("class") or "unclassified"
        classes[c] = classes.get(c, 0) + 1
    reached = [o.get("l5_touch_reached_by_opposite_ask") for o in orders
               if o.get("relation") == "complete"
               and o.get("l5_touch_reached_by_opposite_ask") is not None]
    return {"n": len(rows), "classes": classes,
            "median_delta_ticks_touch_minus_executed": (
                round(statistics.median(deltas), 2) if deltas else None),
            "fee_safe_at_touch_share": round(
                sum(1 for r in rows if r.get("fee_safe_at_touch")) / len(rows), 4),
            "would_cross_at_touch_share": round(
                sum(1 for r in rows if r.get("would_cross_at_touch")) / len(rows), 4),
            "orders_where_opposite_ask_reached_l5_touch": (
                {"n": len(reached), "share": round(sum(1 for x in reached if x) / len(reached), 4)}
                if reached else None),
            "labels": "READ-ONLY counterfactual on the clean cohort; live orders unchanged"}


def decision_identity(d: Mapping[str, Any]) -> Optional[str]:
    """Stable identity of ONE live decision from immutable provenance
    (OWNER 5556815940 #2): the persisted decision_id when present, else a
    deterministic hash of session_id | slug | decision_idx | t_utc | state
    | acted. Never evaluator-run time. None = provenance missing (no
    t_utc and no decision_id): such a row is reported, never counted."""
    if d.get("decision_id"):
        return str(d["decision_id"])
    if not d.get("t_utc"):
        return None
    import hashlib
    raw = "|".join(str(d.get(k) or "") for k in (
        "session_id", "slug", "decision_idx", "t_utc", "state", "acted"))
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def order_identity(o: Mapping[str, Any]) -> Optional[str]:
    oid = o.get("order_id")
    return f"order:{oid}" if oid else None


def dedupe(decs: list, orders: list) -> dict:
    """Idempotent evidence set (OWNER 5556815940 #3): re-reading the same
    session/ledger rows N times yields exactly the one-copy set."""
    seen: set = set()
    uniq_d, dup_d, missing = [], 0, 0
    for d in decs:
        ident = decision_identity(d)
        if ident is None:
            missing += 1
            continue
        if ident in seen:
            dup_d += 1
            continue
        seen.add(ident)
        d = dict(d)
        d["decision_id"] = ident
        uniq_d.append(d)
    seen_o: set = set()
    uniq_o, dup_o = [], 0
    for o in orders:
        ident = order_identity(o)
        if ident is None:
            uniq_o.append(o)               # no venue id: cannot dedupe, keep
            continue
        if ident in seen_o:
            dup_o += 1
            continue
        seen_o.add(ident)
        uniq_o.append(o)
    return {"decisions": uniq_d, "orders": uniq_o,
            "duplicates_dropped": dup_d + dup_o,
            "decision_duplicates_dropped": dup_d,
            "order_duplicates_dropped": dup_o,
            "provenance_missing": missing}


def decisions_from_session_reports(reports: Iterable[Mapping[str, Any]]) -> tuple[list, list]:
    """Flatten session reports (session_end / session_report rows) into
    decision and order records tagged with their market and session."""
    decs, orders = [], []
    for rep in reports:
        slug = rep.get("slug")
        sid = rep.get("session_id")
        for d in rep.get("decisions") or []:
            d = dict(d)
            d.setdefault("slug", slug)
            if sid and not d.get("session_id"):
                d["session_id"] = sid
            decs.append(d)
        for o in rep.get("orders") or []:
            o = dict(o)
            o.setdefault("slug", slug)
            if sid and not o.get("session_id"):
                o["session_id"] = sid
            orders.append(o)
    return decs, orders


def evidence_progress(live: Mapping[str, Any], shadow: Mapping[str, Any],
                      spec: Mapping[str, Any],
                      accounting: Optional[Mapping[str, Any]] = None) -> dict:
    """Progress toward EVERY minimum from UNIQUE evidence (OWNER 5556815940
    #1/#4): counts are deduped by decision identity; the live minimums are
    EVIDENCE_ACCOUNTING_INVALID whenever provenance is missing on any
    counted row, and no verdict is emitted from an invalid count."""
    me = spec["minimum_evidence"]
    acct = dict(accounting or {})
    have = {
        "live_scored_decisions": int(live.get("scored_decisions") or 0),
        "live_markets": int(live.get("markets") or 0),
        "live_post_fill_decisions": int((live.get("post_fill_complete_gap") or {}).get("n") or 0),
        "live_timed_intervals": int((live.get("timing_cadence") or {}).get("n") or 0),
        "shadow_windows": int(shadow.get("windows_since_freeze") or 0),
    }
    invalid = bool(acct.get("provenance_missing")) or acct.get("status") == "EVIDENCE_ACCOUNTING_INVALID"
    rows = {}
    for k in me:
        row = {"have": have[k], "need": me[k],
               "pct": round(100.0 * min(have[k], me[k]) / me[k], 1),
               "met": have[k] >= me[k]}
        if invalid and k.startswith("live_"):
            row["status"] = "EVIDENCE_ACCOUNTING_INVALID"
            row["met"] = False
        rows[k] = row
    return {"minimums": rows,
            "all_met": all(r["met"] for r in rows.values()) and not invalid,
            "accounting": {
                "method": "dedupe by decision_id (persisted) or sha256(session_id|slug|"
                          "decision_idx|t_utc|state|acted); orders by venue order_id",
                "status": "EVIDENCE_ACCOUNTING_INVALID" if invalid else "OK",
                **{k: acct.get(k, 0) for k in ("duplicates_dropped",
                                               "decision_duplicates_dropped",
                                               "order_duplicates_dropped",
                                               "provenance_missing")},
                # OWNER 5557786528 #3 visible counters (canonical names)
                "raw_decision_rows": int(acct.get("raw_decision_rows") or 0),
                "identity_missing_rows": int(acct.get("provenance_missing") or 0),
                "summary_rows_ignored": int((acct.get("ingest") or {}).get("summary_rows_ignored") or 0),
                "second_pass_idempotent": bool(acct.get("second_pass_idempotent", True)),
                "acted_without_order_rows": int(acct.get("acted_without_order_rows") or 0),
                "acted_without_order_rows_clean": int(acct.get("acted_without_order_rows_clean") or 0),
                "acted_without_order_rows_legacy": int(acct.get("acted_without_order_rows_legacy") or 0),
                "cohort": acct.get("cohort"),
                "evidence_accounting_valid": not invalid,
                "ingest": acct.get("ingest"),
                "maintenance": acct.get("maintenance"),
                "seed_provenance": acct.get("seed_provenance"),
                "unique_decisions": have["live_scored_decisions"],
                "per_market": live.get("decisions_by_market"),
                "unique_states": live.get("unique_states"),
                "first_t_utc": live.get("first_t_utc"),
                "last_t_utc": live.get("last_t_utc"),
                "side_convention_opens": int((live.get("action_distribution_side") or {}).get("n") or 0),
            }}


def largest_mismatch(live: Mapping[str, Any], shadow: Mapping[str, Any]) -> dict:
    """The failing dimension with the biggest normalized excess over its
    threshold; the report leads with it."""
    worst = {"dimension": None, "excess": 0.0}

    def consider(name, value, thr):
        if value is None or thr in (None, 0):
            return
        ex = (float(value) - float(thr)) / float(thr)
        if ex > worst["excess"]:
            worst.update({"dimension": name, "value": value,
                          "threshold": thr, "excess": round(ex, 3)})
    rel = live.get("action_distribution_relation") or {}
    if isinstance(rel.get("value"), Mapping):
        consider("live:action_distribution_relation(acted)",
                 rel["value"].get("l1_acted_vs_l5"),
                 (rel.get("threshold") or {}).get("l1_acted_vs_l5_max"))
        consider("live:action_distribution_relation(sampled)",
                 rel["value"].get("l1_sampled_vs_l5"),
                 (rel.get("threshold") or {}).get("l1_sampled_vs_l5_max"))
    pf = live.get("post_fill_complete_gap") or {}
    if isinstance(pf.get("value"), Mapping):
        g = pf["value"].get("gap_pp_acted")
        consider("live:post_fill_complete_gap(acted)",
                 abs(g) if g is not None else None,
                 (pf.get("threshold") or {}).get("abs_gap_pp_max_acted"))
    off = live.get("off_distribution_rate") or {}
    consider("live:off_distribution_rate", off.get("value"),
             (off.get("threshold") or {}).get("max_pct"))
    srel = shadow.get("action_distribution_relation") or {}
    if isinstance(srel.get("value"), Mapping):
        consider("shadow:action_distribution_relation(acted)",
                 srel["value"].get("l1_acted_vs_l5"),
                 (srel.get("threshold") or {}).get("l1_acted_vs_l5_max"))
    soff = shadow.get("off_distribution_rate") or {}
    consider("shadow:off_distribution_rate", soff.get("value"),
             (soff.get("threshold") or {}).get("max_pct"))
    return worst


def layered_view(live: Mapping[str, Any], spec: Mapping[str, Any]) -> dict:
    """OWNER (two layers, thresholds unchanged): the same frozen
    dimensions read twice -- BEHAVIORAL = what the frozen model wanted to
    do (the sampled draw) vs L5, EXECUTABLE = what reached the venue after
    the safety / economic shell. A size, cadence or inventory failure that
    the shell demonstrably causes shows up in the executable layer only;
    the combined dimension statuses above are untouched."""
    dims = spec["dimensions"]
    rel = live.get("action_distribution_relation") or {}
    pf = live.get("post_fill_complete_gap") or {}
    off = live.get("off_distribution_rate") or {}
    rv, pv = rel.get("value") or {}, pf.get("value") or {}
    thr_r = dims["action_distribution_relation"]["pass"]
    thr_p = dims["post_fill_complete_gap"]["pass"]
    thr_o = dims["off_distribution_rate"]["pass"]

    def st(ok: Optional[bool], n: int, need: int) -> str:
        if ok is None:
            return "UNMEASURED"
        if not ok:
            return "FAIL"
        return "PASS" if n >= need else "INSUFFICIENT"
    me = spec["minimum_evidence"]
    l1s, l1a = _f(rv.get("l1_sampled_vs_l5")), _f(rv.get("l1_acted_vs_l5"))
    gs_, ga_ = _f(pv.get("gap_pp_sampled")), _f(pv.get("gap_pp_acted"))
    offv = _f(off.get("value"))
    behavioral = {
        "action_distribution_relation": _dim(
            st(None if l1s is None else l1s <= thr_r["l1_sampled_vs_l5_max"],
               int(rel.get("n") or 0), me["live_scored_decisions"]),
            {"l1_sampled_vs_l5": l1s, "sampled": rv.get("sampled"),
             "expected_l5": rv.get("expected_l5")},
            {"l1_sampled_vs_l5_max": thr_r["l1_sampled_vs_l5_max"]}, int(rel.get("n") or 0)),
        "post_fill_complete_gap": _dim(
            st(None if gs_ is None else abs(gs_) <= thr_p["abs_gap_pp_max_sampled"],
               int(pf.get("n") or 0), me["live_post_fill_decisions"]),
            {"gap_pp_sampled": gs_, "sampled": pv.get("sampled"),
             "expected_l5": pv.get("expected_l5")},
            {"abs_gap_pp_max_sampled": thr_p["abs_gap_pp_max_sampled"]}, int(pf.get("n") or 0)),
        "off_distribution_rate": _dim(
            st(None if offv is None else offv <= thr_o["max_pct"],
               int(off.get("n") or 0), me["live_scored_decisions"]),
            offv, thr_o, int(off.get("n") or 0)),
        "action_distribution_side": live.get("action_distribution_side"),
        "labels": "what the frozen model wanted to do (sampled draw) vs L5; "
                  "no safety / economic shell in this layer",
    }
    executable = {
        "action_distribution_relation": _dim(
            st(None if l1a is None else l1a <= thr_r["l1_acted_vs_l5_max"],
               int(rel.get("n") or 0), me["live_scored_decisions"]),
            {"l1_acted_vs_l5": l1a, "acted_incl_hold": rv.get("acted_incl_hold"),
             "expected_l5": rv.get("expected_l5")},
            {"l1_acted_vs_l5_max": thr_r["l1_acted_vs_l5_max"]}, int(rel.get("n") or 0)),
        "post_fill_complete_gap": _dim(
            st(None if ga_ is None else abs(ga_) <= thr_p["abs_gap_pp_max_acted"],
               int(pf.get("n") or 0), me["live_post_fill_decisions"]),
            {"gap_pp_acted": ga_, "acted_incl_hold": pv.get("acted_incl_hold"),
             "expected_l5": pv.get("expected_l5")},
            {"abs_gap_pp_max_acted": thr_p["abs_gap_pp_max_acted"]}, int(pf.get("n") or 0)),
        **{k: live.get(k) for k in ("price_aggressiveness", "size", "timing_cadence",
                                    "inventory_trajectory", "maker_taker")},
        "labels": "what reached the venue after the safety / economic shell "
                  "(size cap, rest, residual cap, fee-safe complement bound)",
    }
    return {"behavioral": behavioral, "executable": executable,
            "rule": "same frozen thresholds; the combined dimension statuses are "
                    "unchanged -- this is a second reading, not a relabel"}


MAX_RESIDUAL_UNITS_SHELL = 3          # == pmus_spring_l5_session.MAX_RESIDUAL_UNITS
CAP_OVERRIDES = ("policy_flag:cap_fallback_complete", "safety_adaptation:spend_cap")


def feasibility_decomposition(decisions: list, spec: Mapping[str, Any]) -> dict:
    """OWNER 5559167134 #2/#3 (read-only): for every clean decision the
    canonical stochastic action (the seeded draw), the canonical
    executable action before the safety shell (identical: the canonical
    lookup already makes pre-fill = OPEN), the action actually taken and
    the exact override; relation L1 and post-fill COMPLETE gap for (a)
    sampled, (b) canonical-before-shell, (c) acted; and the BEST acted
    fidelity achievable by replaying the same states under the current
    rules ($0.05 cap, residual cap 3, taker off, fee-safe complement):
    a cap-forced conversion cannot be undone under those rules, every
    other decision could have followed its draw. If that best value
    cannot meet the frozen acted threshold the result is
    GATE_FEASIBILITY_CONFLICT with the binding rules named -- the gate
    and the caps are untouched. Cap-fallback attribution: a fallback
    COMPLETE whose recorded residual was below the cap is an ANOMALY
    (implementation defect candidate), never a safety override."""
    thr = spec["dimensions"]["action_distribution_relation"]["pass"]
    thr_p = spec["dimensions"]["post_fill_complete_gap"]["pass"]
    rows: list[dict] = []
    for d in decisions:
        sh = (d.get("shadow") or {}).get("stochastic") or {}
        sampled = sh.get("sampled") or d.get("sampled")
        acted = d.get("acted")
        over = str(d.get("divergence") or "")
        lk = d.get("lookup") or {}
        try:
            resid = abs(float(lk.get("residual_units") if lk.get("residual_units") is not None
                              else d.get("residual_units") or 0))
        except (TypeError, ValueError):
            resid = 0.0
        forced: Optional[str] = None
        if over == "policy_flag:cap_fallback_complete":
            forced = ("residual_cap" if resid >= MAX_RESIDUAL_UNITS_SHELL
                      else "ANOMALY:residual_cap_fallback_below_cap")
        elif over.startswith("safety_adaptation:spend_cap"):
            forced = "spend_cap"
        elif acted == "hold" and d.get("refusal"):
            forced = f"hold:{str(d['refusal']).split(':')[0]}"
        would = d.get("would_act_without_safety_cap") or (
            sampled if (forced and not forced.startswith("hold")) else acted)
        rows.append({"sampled": sampled, "canonical_before_shell": sampled,
                     "acted": acted, "override": over or None, "forced_by": forced,
                     "would_act_without_safety_cap": would,
                     "best_under_current_shell": (acted if forced else (sampled or acted)),
                     "post_fill": resid >= 1,
                     "masses": _masses_of(d)})
    if not rows:
        return {"status": "UNMEASURED", "decisions": 0}

    def freq(key, items):
        c = {"complete": 0, "extend": 0, "open": 0}
        for r in items:
            v = r.get(key)
            if v in c:
                c[v] += 1
        n = sum(c.values()) or 1
        return {k: round(v / n, 4) for k, v in c.items()}

    def expected(items):
        agg = {"complete": 0.0, "extend": 0.0, "open": 0.0}
        for r in items:
            for k, v in r["masses"].items():
                agg[k] += v
        n = len(items) or 1
        return {k: round(v / n, 4) for k, v in agg.items()}

    def l1(a, b):
        return round(sum(abs(a.get(k, 0) - b.get(k, 0)) for k in a), 4)
    exp_all = expected(rows)
    post = [r for r in rows if r["post_fill"]]
    exp_post = expected(post) if post else None
    layers = {}
    for name, key in (("sampled", "sampled"), ("canonical_before_shell", "canonical_before_shell"),
                      ("acted", "acted"), ("best_achievable_under_current_shell",
                                          "best_under_current_shell")):
        f_all = freq(key, rows)
        gap = (round(100 * (freq(key, post)["complete"] - exp_post["complete"]), 1)
               if post else None)
        layers[name] = {"relation_freq": f_all, "l1_vs_l5": l1(f_all, exp_all),
                        "post_fill_complete_gap_pp": gap}
    forced_counts: dict = {}
    for r in rows:
        if r["forced_by"]:
            forced_counts[r["forced_by"]] = forced_counts.get(r["forced_by"], 0) + 1
    anomalies = [r for r in rows if str(r["forced_by"] or "").startswith("ANOMALY")]
    best = layers["best_achievable_under_current_shell"]
    conflict = (best["l1_vs_l5"] > thr["l1_acted_vs_l5_max"]
                or (best["post_fill_complete_gap_pp"] is not None
                    and abs(best["post_fill_complete_gap_pp"]) > thr_p["abs_gap_pp_max_acted"]))
    binding = sorted(k for k in forced_counts if not k.startswith("ANOMALY"))
    return {
        "status": ("GATE_FEASIBILITY_CONFLICT" if conflict
                   else "FEASIBLE_UNDER_CURRENT_SHELL"),
        "decisions": len(rows), "post_fill_decisions": len(post),
        "expected_l5": exp_all, "expected_l5_post_fill": exp_post,
        "layers": layers,
        "frozen_acted_thresholds": {"l1_acted_vs_l5_max": thr["l1_acted_vs_l5_max"],
                                    "abs_gap_pp_max_acted": thr_p["abs_gap_pp_max_acted"]},
        "forced_by_rule": forced_counts,
        "binding_rules": binding,
        "cap_fallback_attribution": {
            "fallback_completes": sum(1 for r in rows
                                      if r["override"] == "policy_flag:cap_fallback_complete"),
            "at_residual_cap": forced_counts.get("residual_cap", 0),
            "anomalies_below_cap": len(anomalies),
            "verdict": ("ALL_CAP_FORCED" if not anomalies else "IMPLEMENTATION_DEFECT_CANDIDATE")},
        "reading": ("best_achievable = the same states replayed under the current caps: "
                    "cap-forced conversions kept, every other decision follows its own "
                    "draw; a conflict means the frozen ACTED thresholds are unreachable "
                    "under the current safety shell -- gate and caps untouched, not a "
                    "model deficiency"),
    }


def _masses_of(d: Mapping[str, Any]) -> dict:
    out = {"complete": 0.0, "extend": 0.0, "open": 0.0}
    rr = d.get("relation_ranked") or []
    if rr:
        for r in rr:
            if isinstance(r, Mapping) and r.get("relation") in out:
                out[str(r["relation"])] = float(r.get("p") or 0.0)
        return out
    pc, pe = float(d.get("p_complete") or 0), float(d.get("p_extend") or 0)
    return {"complete": pc, "extend": pe, "open": max(0.0, 1 - pc - pe)}


def score_pmus_shadow(report: Optional[Mapping[str, Any]],
                      spec: Mapping[str, Any]) -> dict:
    """The recorder's READ-ONLY multi-market L5 shadow (behavioral layer
    over every observable live market). Reported beside the frozen
    minimums with the same thresholds; it does NOT count toward the
    frozen live or shadow minimums (that would be a spec change and is
    the owner's decision)."""
    if not report:
        return {"status": "UNMEASURED", "note": "no pmus shadow report given"}
    b = report.get("behavioral_layer") or {}
    prov = report.get("lookup_provenance") or {}
    n = int(b.get("scored_decisions") or 0)
    thr = spec["dimensions"]["action_distribution_relation"]["pass"]
    thr_p = spec["dimensions"]["post_fill_complete_gap"]["pass"]
    thr_o = spec["dimensions"]["off_distribution_rate"]["pass"]
    me = spec["minimum_evidence"]
    ver = prov.get("lookup_version_gate")
    if ver is not None and int(ver) != CANONICAL_LOOKUP_VERSION:
        return {"status": "LOOKUP_VERSION_MISMATCH", "lookup_version_gate": ver,
                "decisions": n}
    l1s = _f(b.get("l1_sampled_vs_l5"))
    post = b.get("post_fill") or {}
    gap = _f(post.get("complete_freq_gap_pp_sampled"))
    off = _f(b.get("off_distribution_rate_pct"))

    def st(ok: Optional[bool], have: int, need: int) -> str:
        if ok is None:
            return "UNMEASURED"
        if not ok:
            return "FAIL"
        return "PASS" if have >= need else "INSUFFICIENT"
    return {
        "status": "OK" if n else "NO_DECISIONS_YET",
        "decisions": n,
        "markets": report.get("markets_with_decisions"),
        "markets_shadowed": report.get("markets_shadowed"),
        "post_fill_decisions": int(post.get("decisions") or 0),
        "theoretical_fills": report.get("theoretical_fills"),
        "lookup_provenance": prov,
        "funnel": report.get("funnel"),
        "executable_layer": report.get("executable_layer"),
        "action_distribution_relation": _dim(
            st(None if l1s is None else l1s <= thr["l1_sampled_vs_l5_max"], n,
               me["live_scored_decisions"]),
            {"l1_sampled_vs_l5": l1s, "sampled": b.get("sampled_relation_freq"),
             "expected_l5": b.get("expected_relation_freq_l5")},
            {"l1_sampled_vs_l5_max": thr["l1_sampled_vs_l5_max"]}, n),
        "post_fill_complete_gap": _dim(
            st(None if gap is None else abs(gap) <= thr_p["abs_gap_pp_max_sampled"],
               int(post.get("decisions") or 0), me["live_post_fill_decisions"]),
            {"gap_pp_sampled": gap}, {"abs_gap_pp_max_sampled": thr_p["abs_gap_pp_max_sampled"]},
            int(post.get("decisions") or 0)),
        "off_distribution_rate": _dim(
            st(None if off is None else off <= thr_o["max_pct"], n,
               me["live_scored_decisions"]), off, thr_o, n),
        "counts_toward_frozen_minimums": False,
        "labels": "READ-ONLY multi-market shadow on the live venue: the frozen model's "
                  "own draw acted in shadow, theoretical fills from public prints; "
                  "same thresholds, reported beside the frozen minimums, never "
                  "counted toward them (spec unchanged)",
    }


def evaluate(reports: Iterable[Mapping[str, Any]],
             rolling: Optional[Mapping[str, Any]] = None,
             spec: Optional[Mapping[str, Any]] = None,
             pmus_shadow: Optional[Mapping[str, Any]] = None,
             model: Optional[Mapping[str, Any]] = None,
             ingest: Optional[Mapping[str, Any]] = None,
             maintenance: Optional[list] = None) -> dict:
    spec = spec or load_spec()
    reports = [r for r in reports
               if isinstance(r, Mapping) and r.get("kind") not in SUMMARY_KINDS]
    raw_d, raw_o = decisions_from_session_reports(reports)
    dd = dedupe(raw_d, raw_o)
    # OWNER 5557786528 #4: fail closed when the same raw corpus does not
    # reproduce the same unique set on a second pass (non-idempotent
    # identity) -- the counters below then carry evidence_accounting_valid
    # = false and no verdict is emitted
    second = dedupe(raw_d, raw_o)
    second_pass_ok = (len(second["decisions"]) == len(dd["decisions"])
                      and [d["decision_id"] for d in second["decisions"]]
                      == [d["decision_id"] for d in dd["decisions"]]
                      and len(second["orders"]) == len(dd["orders"]))
    dd["raw_decision_rows"] = len(raw_d)
    dd["raw_order_rows"] = len(raw_o)
    # OWNER 5558072959 #1 (disagreement attribution, LIVE 02:29-04:03Z):
    # a decision recorded as acted (open/extend/complete) without a
    # matching submitted order in the same session report was a no-op the
    # record misreported as executed (implementation defect, fixed at the
    # source); counted here per report from provenance so the acted view
    # of pre-fix rows is never mistaken for executed evidence
    # OWNER 5558859447 #8: the counter is also split by cohort -- a
    # session report whose decisions are all clean rows is a clean
    # session -- so the clean cohort's value (which must be 0) is read
    # directly rather than inferred from the corpus-wide number
    no_op, no_op_clean, no_op_legacy = 0, 0, 0
    for rep in reports:
        decs = [d for d in (rep.get("decisions") or []) if isinstance(d, Mapping)]
        n_acted = sum(1 for d in decs
                      if d.get("acted") in ("open", "extend", "complete"))
        n_orders = len(rep.get("orders") or [])
        if n_acted > n_orders:
            no_op += n_acted - n_orders
            if decs and all(is_clean_row(d) for d in decs):
                no_op_clean += n_acted - n_orders
            else:
                no_op_legacy += n_acted - n_orders
    dd["acted_without_order_rows"] = no_op
    dd["acted_without_order_rows_clean"] = no_op_clean
    dd["acted_without_order_rows_legacy"] = no_op_legacy
    dd["second_pass_idempotent"] = second_pass_ok
    if ingest:
        dd["ingest"] = {k: ingest.get(k) for k in (
            "rows_total", "summary_rows_ignored", "other_rows_ignored",
            "raw_decision_rows", "source") if k in ingest}
        if ingest.get("raw_decision_rows") not in (None, len(raw_d)):
            second_pass_ok = False            # ingest and flatten disagree
            dd["second_pass_idempotent"] = False
    if not second_pass_ok:
        dd["status"] = "EVIDENCE_ACCOUNTING_INVALID"
    # SYNTHETIC 2 s cadence counterfactual (OWNER 5556751941 #1): scored
    # separately, never merged into live evidence; deduped by seed
    cf_seen: set = set()
    cf = []
    for rep in reports:
        for c in rep.get("counterfactual_2s") or []:
            key = c.get("seed") or (c.get("t_utc"), c.get("state"))
            if key in cf_seen:
                continue
            cf_seen.add(key)
            cf.append(c)
    if cf:
        exp_cf = _expected(cf)
        samp_cf, _ = _freq([{"s": c.get("sampled")} for c in cf], "s")
        counterfactual = {"n": len(cf), "expected_l5": exp_cf,
                          "sampled": samp_cf,
                          "l1_sampled_vs_l5": _l1(samp_cf, exp_cf),
                          "off_distribution_pct": round(
                              100.0 * sum(1 for c in cf if c.get("off_distribution")) / len(cf), 2),
                          "labels": "SYNTHETIC 2s-cadence counterfactual; NOT live evidence"}
    else:
        counterfactual = {"n": 0}
    decs, orders = dd["decisions"], dd["orders"]
    # OWNER 5558167805 #1: rows written before mark_noop (no-op decisions
    # recorded as executed actions) form a separate LEGACY cohort. Every
    # minimum and verdict input comes from the CLEAN cohort only; the
    # legacy view is reported beside it and never combined with it.
    clean = [d for d in decs if is_clean_row(d)]
    legacy = [d for d in decs if not is_clean_row(d)]
    clean_sids = {d.get("session_id") for d in clean}
    if legacy:
        orders_clean = [o for o in orders if o.get("session_id") in clean_sids]
        orders_legacy = [o for o in orders if o.get("session_id") not in clean_sids]
    else:
        orders_clean, orders_legacy = orders, []
    live = score_live(clean, orders_clean, spec, model, maintenance=maintenance)
    live["cohort"] = {"rule": CLEAN_ROW_RULE, "clean_decisions": len(clean),
                      "legacy_decisions": len(legacy),
                      "clean_orders": len(orders_clean),
                      "legacy_orders": len(orders_legacy)}
    dd["cohort"] = live["cohort"]
    live_legacy = (score_live(legacy, orders_legacy, spec, model,
                              maintenance=maintenance) if legacy else None)
    if live_legacy is not None:
        live_legacy["labels"] = ("LEGACY pre-mark_noop rows: acted view overstates "
                                 "execution; reported, never a verdict input")
    # OWNER 5558167805 #6: both cadence references persisted as diagnostics
    # beside the frozen dimension (fill-arrival gap = the frozen unit;
    # order-episode gap = the fresh-decision unit); no spec change
    ref = load_cadence_reference()
    if ref and isinstance(live.get("timing_cadence"), dict):
        val = live["timing_cadence"].get("value") or {}
        if isinstance(val, dict):
            val["fill_gap_reference"] = ref.get("fill_gap_reference")
            val["order_episode_gap_reference"] = ref.get("order_episode_gap_reference")
            live["timing_cadence"]["value"] = val
    # OWNER 5558167805 #7: the first post-fix COMPLETE price sample,
    # summarized separately (read-only counterfactual fields)
    complete_cf = summarize_complete_counterfactual(clean, orders_clean)
    # OWNER 5557988072 #6: declared maintenance windows are diagnostics
    # inputs only -- a decision time-stamped inside one is a provenance
    # anomaly to report (the venue was down), never evidence to count
    mwin = [(float(w.get("start") or 0), float(w.get("end") or 0))
            for w in (maintenance or []) if w.get("start") and w.get("end")]
    inside_m = 0
    for d in decs:
        try:
            ts = datetime.fromisoformat(str(d.get("t_utc"))).timestamp()
        except (ValueError, TypeError):
            continue
        if any(s <= ts <= e for s, e in mwin):
            inside_m += 1
    # OWNER 5556815940 / 5557786528: rows are provenance-unique, but the
    # seeded draw before f465fbc9 restarted decision_idx per resumed
    # session, so several unique decisions share ONE seed -- their sampled
    # relations are not independent draws. Reported, never excluded or
    # relabeled; the owner decides what such rows count for.
    seeds: dict = {}
    for d in decs:
        s = ((d.get("shadow") or {}).get("stochastic") or {}).get("seed")
        if s:
            seeds[s] = seeds.get(s, 0) + 1
    dd["seed_provenance"] = {
        "decisions_with_seed": sum(seeds.values()),
        "distinct_seeds": len(seeds),
        "seed_repeat_rows": sum(n - 1 for n in seeds.values() if n > 1),
        "max_rows_per_seed": max(seeds.values()) if seeds else 0,
        "note": "rows sharing a seed replay the same draw (pre-f465fbc9 "
                "decision_idx restart); reported, not excluded"}
    dd["maintenance"] = {"windows": [{k: w.get(k) for k in (
                                          "id", "start_utc", "end_utc", "effective_end_utc",
                                          "boundary", "overrun_s")}
                                     for w in (maintenance or [])],
                         "windows_declared": len(mwin),
                         "decisions_inside_maintenance": inside_m,
                         "intervals_excluded_maintenance": int(
                             live.get("intervals_excluded_maintenance") or 0),
                         "class": "planned venue outage; excluded from cadence "
                                  "evidence, never an L5 fidelity failure"}
    shadow = score_shadow(rolling, spec)
    classes: dict = {}
    unclassified_samples: list = []
    for d in clean:
        c = d.get("divergence_class") or d.get("divergence") or "unclassified"
        classes[c] = classes.get(c, 0) + 1
        if "unclassified" in c and len(unclassified_samples) < 5:
            # name the persisted divergence so the class can be assigned
            # from the ledger row, never guessed
            unclassified_samples.append({"divergence": d.get("divergence"),
                                         "refusal": d.get("refusal"),
                                         "sampled": (d.get("shadow") or {}).get(
                                             "stochastic", {}).get("sampled"),
                                         "acted": d.get("acted"),
                                         "decision_id": d.get("decision_id")})
    classes_legacy: dict = {}
    for d in legacy:
        c = d.get("divergence_class") or d.get("divergence") or "unclassified"
        classes_legacy[c] = classes_legacy.get(c, 0) + 1
    progress = evidence_progress(live, shadow, spec, dd)
    v = verdict(live, shadow, spec)
    if progress["accounting"]["status"] != "OK":
        # OWNER 5556815940 #1: never a verdict from an invalid count
        v = {**v, "label": spec["labels"]["until_met"], "phase_1_met": False,
             "blocked_by": "EVIDENCE_ACCOUNTING_INVALID"}
    return {"kind": "l5_fidelity_gate",
            "t": datetime.now(timezone.utc).isoformat(),
            "spec_frozen_utc": spec["frozen_utc"],
            "phase_order": "REPLICATE L5 -> PROVE FIDELITY -> RUN L5 -> "
                           "MEASURE REALIZED RESULTS -> THEN OPTIMIZE PROFITABILITY",
            "verdict": v,
            "evidence_progress": progress,
            "largest_mismatch": largest_mismatch(live, shadow),
            "divergence_classes": classes,
            "divergence_unclassified_samples": unclassified_samples,
            "divergence_classes_legacy": classes_legacy,
            "counterfactual_2s": counterfactual,
            "complete_price_counterfactual": complete_cf,
            "layers": layered_view(live, spec),
            "feasibility": feasibility_decomposition(clean, spec),
            "pmus_shadow": score_pmus_shadow(pmus_shadow, spec),
            "live": live, "live_legacy": live_legacy, "shadow": shadow}


RAW_EVIDENCE_KINDS = ("session_end", "session_report")
# OWNER 5557786528 #2: periodic fidelity / L5_FIDELITY summaries are
# SNAPSHOTS of the evidence, never evidence rows; they are rejected by
# kind BEFORE aggregation and the rejection is counted.
SUMMARY_KINDS = ("fidelity_gate", "fidelity_verdict", "fidelity_gate_error",
                 "l5_fidelity_gate", "L5_FIDELITY", "l5_fidelity",
                 "fidelity_summary", "campaign_summary", "funnel",
                 "campaign_state", "advance_gate")


def read_reports_accounted(path: Path) -> dict:
    """Raw session_end / session_report rows from a log or ledger file
    with the ingest accounting the evidence boundary needs: how many rows
    were read, how many were summary snapshots (ignored by kind), how many
    raw decision rows the accepted reports carry."""
    reports, rows_total, summary_ignored, other_ignored = [], 0, 0, 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("PMUS_L5_CAMPAIGN "):
            line = line.split(" ", 1)[1]
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        rows_total += 1
        if not isinstance(row, Mapping):
            other_ignored += 1
            continue
        kind = row.get("kind")
        if kind in SUMMARY_KINDS or str(kind or "").lower().startswith("fidelity"):
            summary_ignored += 1              # snapshot, never evidence
            continue
        if kind in RAW_EVIDENCE_KINDS and row.get("decisions"):
            reports.append(row)
        else:
            other_ignored += 1
    return {"reports": reports, "rows_total": rows_total,
            "summary_rows_ignored": summary_ignored,
            "other_rows_ignored": other_ignored,
            "raw_decision_rows": sum(len(r.get("decisions") or []) for r in reports),
            "source": str(path)}


def _read_reports(path: Path) -> list:
    """session_end / session_report rows from a log or ledger file."""
    return read_reports_accounted(path)["reports"]


def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: l5_fidelity_gate <session_log_or_ledger.jsonl> "
              "[gemini_rolling_report.json]", file=sys.stderr)
        return 2
    reports = _read_reports(Path(argv[0]))
    rolling = json.loads(Path(argv[1]).read_text(encoding="utf-8")) if len(argv) > 1 else None
    model = None
    if MODEL_PATH.exists():
        model = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    print(json.dumps(evaluate(reports, rolling, model=model), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
