"""Read-only spring-watch enrichment for the PMUS WS evidence recorder.

This module does not discover markets, subscribe, mutate venue state, or alter
any execution gate. It adds explicit ordinary-Q1 and strict SPRING <=0.96
slot evidence to advisory ``spring_watch`` rows, reserves a bounded subset of
the existing expensive L5-shadow compute seats for already-subscribed
spring-shaped markets, then delegates to the canonical read-only recorder.
"""
from __future__ import annotations

import os
from typing import Any, Mapping, Optional

from src import pmus_ws_evidence_recorder as base
from src import pmus_priority_queue as pq
from src import pmus_spring_shadow_replay as spring_replay
from src.pmus_l5_compute_allocator import allocate_with_spring_reserve

_ORIGINAL_ALLOCATE_L5 = pq.allocate_l5
_ORIGINAL_WS_GATE = base.ws_gate
_ORIGINAL_PLAN = pq.plan

# Spring evidence-pursuit WS retention (issue #167 comments 5531925556/
# 5531961242): a market with exactly one canonical strict SPRING_MATCHED
# kept losing its WS seat to flow-ranked rotation before a second 300s
# completion could ever be observed, leaving stale in-memory state that
# LOOKED alive in status rows. A small bounded reserve keeps such
# candidates on the live tape — eligibility requires FRESH canonical
# strict evidence AND authenticated source freshness, and expires fail
# closed. Never promotes loose Q1 MATCHED; never touches admission.
PURSUIT_SEATS_ENV = "PMUS_SPRING_PURSUIT_SEATS"   # default 0 (off), clamped 0..4
PURSUIT_TTL_ENV = "PMUS_SPRING_PURSUIT_TTL_S"     # default 900s
# Operator-named slugs to bring under AUTHENTICATED observation so a
# corrected sports candidate can earn observed spring_watch evidence
# (issue #167 comment 5541176027 item 4). Read-only: pinning changes
# what this recorder WATCHES, never any gate, threshold or admission.
PIN_SLUGS_ENV = "PMUS_WS_PIN_SLUGS"               # comma-separated
PIN_SLUGS_MAX = 4

# Same-stream/category evidence bar (OWNER DECISION 5533476555 item 3,
# replacing the per-market >=2-in-60m rule the representativeness study
# proved unrealistic even on L5's home venue). Conservative deterministic
# pick, documented: >=2 strict completions within a rolling 60 minutes
# among markets of the SAME category, from >=2 DISTINCT markets — an
# isolated one-off market can never satisfy the bar by itself. The
# advisory review floor (PMUS_SPRING_PROBE_MIN_COMPLETIONS, default 3)
# now applies to CATEGORY completions and stays >= the bar.
# Canonical definitions live in the keyless strict-replay module so the
# authenticated recorder and the keyless sports path cannot drift
# (issue #167 comment 5540937360 item 2). Re-exported under the original
# names because callers and tests already reference them here.
from src.pmus_spring_shadow_replay import (      # noqa: E402
    CATEGORY_BAR_COMPLETIONS,
    CATEGORY_BAR_DISTINCT_MARKETS,
    CATEGORY_BAR_SPAN_S,
    category_spring_evidence,
    slug_category,
)


def _last_authenticated_update_age(s: Mapping[str, Any],
                                   now: float) -> Optional[float]:
    """Age of the newest authenticated book/trade update for one market;
    None when the state has never carried a timestamped update. This is
    the stale-state detector: a frozen status row keeps its old counts
    but this age keeps growing."""
    ts = []
    for t in s.get("trades") or []:
        try:
            ts.append(float(t.get("t", 0)))
        except (TypeError, ValueError):
            pass
    for h in s.get("bbo_history") or []:
        try:
            ts.append(float(h.get("t", 0)))
        except (TypeError, ValueError):
            pass
    return (now - max(ts)) if ts else None


def evidence_pursuit_candidates(state: Mapping[str, Any], now: float,
                                exclude: set, ttl: float) -> list[dict]:
    """Markets worth a bounded WS evidence-pursuit seat: canonical strict
    spring evidence (>=1 SPRING_MATCHED inside the rolling replay window
    — loose Q1 MATCHED never qualifies) AND a fresh authenticated source
    (last book/trade update within ttl). A rolling strict match that has
    aged out of the replay window yields matched=0 here and loses the
    seat; a stale source fails closed regardless of remembered counts."""
    out = []
    for slug, s in state.items():
        if slug in exclude or not isinstance(s, Mapping):
            continue
        age = _last_authenticated_update_age(s, now)
        if age is None or age > ttl:
            continue
        try:
            summary = spring_replay.summarize_state(s, now)
        except Exception:
            continue
        matched = int(summary.get("spring_matched_slots") or 0)
        if matched < 1:
            continue
        out.append({"slug": slug, "reason": "strict_match_pursuit",
                    "strict_matches": matched,
                    "last_update_age_s": round(age, 1)})
    out.sort(key=lambda r: (-r["strict_matches"], r["last_update_age_s"],
                            r["slug"]))
    return out


def plan(targets, state, cap, cycle, prev=None, now=None, l5_infos=None,
         protected=None):
    """Canonical priority plan plus a bounded evidence-pursuit reserve.

    `protected` is FORWARDED, not handled here. This wrapper monkeypatches
    pq.plan (see the assignment at the bottom of this module), so any argument
    the canonical planner grows must be mirrored in this signature or the
    recorder dies at the first discovery cycle with a TypeError -- which is
    exactly what happened on 2026-09-13T18:12Z when `protected` was added to
    pq.plan and not here.

    The global cap is UNCHANGED: each reserved candidate replaces the
    lowest-ranked WARM seat only — hot seats and the COLD/starvation
    sampler are never evicted, so broad coverage is preserved. With no
    warm seat available, fewer (or zero) seats are reserved: fail closed
    rather than widen the subscription set."""
    import time as _time
    now = now or _time.time()
    p = _ORIGINAL_PLAN(targets, state, cap, cycle, prev, now=now,
                       l5_infos=l5_infos, protected=protected)
    # OWNER 5553166357: no category gets a hardcoded seat reserve that can
    # starve others. The strict-match pursuit reserve (completion-evidence
    # chasing, a bar removed from OPEN admission in 5551236878) was
    # evicting live CFB warm seats for tennis. Default 0 = off; an
    # operator can still set PMUS_SPRING_PURSUIT_SEATS explicitly.
    seats = max(0, min(4, int(os.getenv(PURSUIT_SEATS_ENV, "0"))))
    ttl = float(os.getenv(PURSUIT_TTL_ENV, "900"))
    p["diagnostics"]["spring_ws_reserved"] = []
    p["diagnostics"]["spring_pinned"] = []
    subscribe = list(p["subscribe"])
    per = p.get("per_market") or {}
    warm_tail = [s for s in reversed(subscribe)
                 if per.get(s, {}).get("tier") == "warm"]

    # PROVENANCE BRIDGE (issue #167 comment 5541176027 item 4). The
    # keyless collector infers trade direction; the spring guard requires
    # OBSERVED trades from this authenticated recorder. When the
    # collector surfaces a corrected candidate that this recorder is not
    # yet watching, the operator names it here and the recorder starts
    # observing it — closing the bridge by ADDING authenticated coverage,
    # never by weakening the provenance gate. Bounded exactly like the
    # pursuit reserve: warm-tail eviction only, cap unchanged, fail
    # closed when no warm seat is free.
    pinned = [s.strip() for s in os.getenv(PIN_SLUGS_ENV, "").split(",")
              if s.strip()][:PIN_SLUGS_MAX]
    for slug in pinned:
        if slug in subscribe:
            p["diagnostics"]["spring_pinned"].append(
                {"slug": slug, "reason": "already_subscribed"})
            continue
        if not warm_tail:
            p["diagnostics"]["spring_pinned"].append(
                {"slug": slug, "reason": "no_warm_seat_available"})
            continue
        evicted = warm_tail.pop(0)
        subscribe[subscribe.index(evicted)] = slug
        p["diagnostics"]["spring_pinned"].append(
            {"slug": slug, "reason": "pinned_for_authenticated_provenance",
             "evicted_warm_seat": evicted})

    if seats:
        cands = evidence_pursuit_candidates(state, now, set(subscribe),
                                            ttl)[:seats]
        reserved = []
        for cand in cands:
            if not warm_tail:
                break
            evicted = warm_tail.pop(0)
            subscribe[subscribe.index(evicted)] = cand["slug"]
            reserved.append({**cand, "evicted_warm_seat": evicted})
        p["diagnostics"]["spring_ws_reserved"] = reserved
    p["subscribe"] = subscribe
    return p

# Conservative, configurable completion floor for the binary advisory
# status (issue #167 comment 5525966568): no invented P&L threshold —
# evidence sufficiency + existing spring invariants only, raw counts
# always reported so the stage decision stays with the operator/owner.
PROBE_MIN_ENV = "PMUS_SPRING_PROBE_MIN_COMPLETIONS"
PROBE_MIN_TRADES_1H = 10


def probe_evidence(row: Mapping[str, Any]) -> dict:
    """ADVISORY-ONLY probe-evidence status for one spring-watch row.
    Never mutates or relaxes spring_candidate_gate / micro_eligible.
    Repetition is judged at CATEGORY level (OWNER DECISION 5533476555):
    the bar needs >=2 completions from >=2 distinct same-category
    markets in the rolling hour, and the review floor applies to
    category completions."""
    min_completions = max(CATEGORY_BAR_COMPLETIONS,
                          int(os.getenv(PROBE_MIN_ENV, "3")))
    reasons: list[str] = []
    trades = row.get("trades_1h")
    if trades is None or int(trades) < PROBE_MIN_TRADES_1H:
        reasons.append(f"thin_flow:{trades}")
    if row.get("two_sided_trades") is not True:
        reasons.append("one_directional_flow")
    off = row.get("off_distribution")
    if off is True:
        reasons.append("v2_off_distribution")
    elif off is None:
        reasons.append("l5_state_unknown")
    missing = row.get("spring_evidence_missing") or []
    reasons.extend(missing)
    cat = row.get("spring_category_evidence") or {}
    if not cat.get("bar_met"):
        reasons.append(
            "category_bar_not_met:"
            f"{cat.get('completions_60m', 0)}completions/"
            f"{cat.get('distinct_markets_60m', 0)}markets"
            f"<{CATEGORY_BAR_COMPLETIONS}/"
            f"{CATEGORY_BAR_DISTINCT_MARKETS}")
    n_cat = int(cat.get("completions_60m") or 0)
    if n_cat < min_completions:
        reasons.append(
            f"insufficient_category_completions:{n_cat}<{min_completions}")
    return {"status": ("READY_FOR_REVIEW" if not reasons
                       else "INSUFFICIENT_EVIDENCE"),
            "reasons": reasons,
            "min_completions_required": min_completions,
            "category_bar": f">={CATEGORY_BAR_COMPLETIONS} completions / "
                            f">={CATEGORY_BAR_DISTINCT_MARKETS} distinct "
                            "same-category markets / 60m",
            "labels": "ADVISORY ONLY; stage decision is operator/owner's; "
                      "no gate is mutated or relaxed by this field"}


def _count(counts: Mapping[str, Any], key: str) -> int:
    try:
        return max(0, int(counts.get(key, 0) or 0))
    except (TypeError, ValueError):
        return 0


def ws_gate(s: Mapping[str, Any], now: float,
            window_s: float = 3600.0) -> dict:
    """Canonical Q1 gate plus independent strict spring shadow evidence.

    The canonical ``gate_status`` is preserved byte-for-byte.  The extra
    spring fields are advisory/theoretical and cannot promote execution.
    """
    out = dict(_ORIGINAL_WS_GATE(s, now, window_s))
    out.update(spring_replay.summarize_state(s, now, window_s))
    # stale-state visibility (issue #167 comment 5531961242): frozen
    # counters on a rotated-out market keep their values, but this age
    # keeps growing — the proof of continuous vs stale observation
    age = _last_authenticated_update_age(s, now)
    out["last_authenticated_update_age_s"] = (round(age, 1)
                                              if age is not None else None)
    return out


def spring_watch(rows: Mapping[str, Mapping[str, Any]],
                 l5_alloc: Optional[set] = None,
                 limit: Optional[int] = None) -> list[dict]:
    """Canonical spring-watch ranking plus explicit completion evidence.

    ``repeated_completion`` is the existing ordinary 99c/Q1 advisory label.
    ``spring_repeated_completion`` is stricter: only slots that themselves
    STARTED in the observation band and later showed a maker-complement
    path inside the observation cap count. Neither is an execution
    admission. Rows additionally carry the CATEGORY evidence bar
    (OWNER DECISION 5533476555).
    """
    import time as _time
    now = _time.time()
    cats = category_spring_evidence(rows, now)
    picked = []
    for slug, r in rows.items():
        try:
            bid = float(r.get("bid"))
        except (TypeError, ValueError):
            continue
        if not (base.SPRING_WATCH_LO <= bid <= base.SPRING_WATCH_HI):
            continue
        if not r.get("both_sides_served"):
            continue
        try:
            depth = min(float(r.get("bid_qty") or 0),
                        float(r.get("ask_qty") or 0))
        except (TypeError, ValueError):
            depth = 0.0

        shadow = r.get("l5_shadow") or {}
        counts = dict(r.get("slot_counts_theoretical") or {})
        matched = _count(counts, "MATCHED")
        one_sided = _count(counts, "ONE_SIDED_HOLD")
        no_fill = _count(counts, "NO_FILL")
        spring_counts = dict(r.get("spring_slot_counts_theoretical") or {})
        spring_matched = _count(spring_counts, "SPRING_MATCHED")
        v2 = shadow.get("v2") if isinstance(shadow.get("v2"), Mapping) \
            else {}
        off = v2.get("off_distribution")
        if off is None:
            off = shadow.get("off_distribution")
        evidence = {
            # completion-evidence fields (issue #167 comment 5525966568),
            # copied verbatim from the strict replay; explicit missingness
            "spring_evaluable_slots": r.get("spring_evaluable_slots"),
            "spring_matched_fraction": r.get("spring_matched_fraction"),
            "spring_latest_slot": r.get("spring_latest_slot"),
            "spring_completion_latency_s":
            r.get("spring_completion_latency_s"),
            "spring_pair_costs": r.get("spring_pair_costs") or [],
            "spring_le_096_completions":
            r.get("spring_le_096_completions"),
            "spring_fee_safe_completions":
            r.get("spring_fee_safe_completions"),
            "spring_evidence_missing":
            r.get("spring_evidence_missing") or [],
            "last_authenticated_update_age_s":
            r.get("last_authenticated_update_age_s"),
        }
        cat_key = slug_category(slug)
        cat_ev = cats.get(cat_key) or {"completions_60m": 0,
                                       "distinct_markets_60m": 0,
                                       "markets": [], "bar_met": False}
        evidence["spring_category"] = cat_key
        evidence["spring_category_evidence"] = cat_ev
        evidence["spring_category_bar_met"] = bool(cat_ev["bar_met"])
        picked.append(({
            "slug": slug,
            "bid": r.get("bid"),
            "ask": r.get("ask"),
            "bid_qty": r.get("bid_qty"),
            "ask_qty": r.get("ask_qty"),
            "trades_1h": r.get("trades_1h"),
            "two_sided_trades": r.get("two_sided_trades"),
            "gate_status": r.get("gate_status"),
            "slot_counts_theoretical": counts,
            "matched_slots": matched,
            "one_sided_holds": one_sided,
            "no_fill_slots": no_fill,
            "repeated_completion": matched >= 2,
            "spring_pair_target": r.get("spring_pair_target", "0.96"),
            "spring_slot_counts_theoretical": spring_counts,
            "spring_matched_slots": spring_matched,
            "spring_repeated_completion": spring_matched >= 2,
            "spring_matches_theoretical": r.get(
                "spring_matches_theoretical") or [],
            **evidence,
            "l5_allocated": (slug in l5_alloc) if l5_alloc is not None
            else None,
            "off_distribution": off,
            "labels": "SHADOW/THEORETICAL; advisory only, not an "
                      "execution admission",
        }, depth))

    picked.sort(key=lambda rd: (not rd[0].get("two_sided_trades"),
                                -int(rd[0].get("trades_1h") or 0),
                                -rd[1], str(rd[0]["slug"])))
    lim = base.SPRING_WATCH_LIMIT if limit is None else limit
    out = [row for row, _ in picked[:max(0, lim)]]
    for row in out:
        row["probe_evidence"] = probe_evidence(row)
        _attach_open_facts(row, now)
    return out


# --- OPEN safety facts the admission guard REQUIRES (OWNER 5551236878) ---
#
# The guard fails closed on each of these, so a recorder that does not
# emit them cannot admit anything -- rebuilding it without this change
# would have produced a status document the probe still refuses.

MARKET_STATE_TTL_S = 120.0        # bounded: <=10 watch rows, cached
# same 60s threshold the collector tape uses, so "cadence" means one
# thing across both evidence paths
CADENCE_MAX_AGE_S = 60.0
_MARKET_STATE_CACHE: dict = {}


def market_state(slug: str, now: float, get=None) -> Optional[dict]:
    """The venue's authoritative open-state, cached and bounded.

    PMUS serves no settlement timestamp for live sports, so the guard
    uses active/archived/closed/status/endDate from
    `/v1/market/slug/{slug}`. The recorder is a WS process; this is the
    one small REST read it needs, over at most SPRING_WATCH_LIMIT slugs
    and cached for MARKET_STATE_TTL_S. Never raises: a failed read
    leaves the field ABSENT, and absent fails closed downstream, which
    is the correct outcome -- it must never look like a pass.
    """
    hit = _MARKET_STATE_CACHE.get(slug)
    if hit and now - hit[0] < MARKET_STATE_TTL_S:
        return hit[1]
    try:
        from src import pmus_sports_collector as col
        get = col.http_get_json if get is None else get
        m = (get(f"/v1/market/slug/{slug}") or {}).get("market") or {}
        state = {k: m.get(k) for k in
                 ("active", "archived", "closed", "status", "endDate")}
        state["minimumTradeQty"] = m.get("minimumTradeQty")
        state["orderPriceMinTickSize"] = m.get("orderPriceMinTickSize")
    except Exception:
        return None
    _MARKET_STATE_CACHE[slug] = (now, state)
    return state


def probe_entry_action(row: Mapping[str, Any]) -> Optional[dict]:
    """What the canonical L5 model would do FIRST here, from flat.

    Computed with the frozen v2 artifact and the shared planner, so the
    recorder cannot drift from the decision layer. Absent on any failure
    -- the guard then refuses, which is correct.
    """
    try:
        from decimal import Decimal as D
        from src import pmus_sports_l5_decision as l5d
        from src import pmus_l5_persistence_v2 as pv2
        bid, ask = row.get("bid"), row.get("ask")
        if bid is None or ask is None:
            return None
        b, a = D(str(bid)), D(str(ask))
        inv = l5d.Inventory()
        d = pv2.v2_dist(pv2.load_v2(),
                        l5d.state_key(float(b), l5d.SPORTS_TTE_DEFAULT_S, 0.0),
                        "open", "q<5")
        if d.get("backed_off"):
            return {"decision": "no_action", "reason": "v2_off_distribution",
                    "state": d.get("state")}
        rels = pv2.relation_mass(d["dist"])
        plan = l5d.plan_action(max(rels, key=rels.get), inv,
                               {"bid": b, "ask": a})
        return {k: v for k, v in plan.items() if not k.startswith("_")}
    except Exception:
        return None


def _attach_open_facts(row: dict, now: float) -> None:
    # both_sides_served is the filter above, so it is True for every row
    # that reaches here -- emit it EXPLICITLY rather than leaving the
    # consumer to infer it from the row's existence.
    row["both_sides_served"] = True
    row["bid_touch_qty"] = row.get("bid_qty")
    age = row.get("last_authenticated_update_age_s")
    # cadence: the authenticated stream is either current or it is not.
    # An unknown age stays unknown; it must not read as zero gaps.
    row["collector_gaps"] = (None if age is None else
                             (0 if float(age) <= CADENCE_MAX_AGE_S else 1))
    state = market_state(str(row.get("slug") or ""), now)
    if state is not None:
        row["market_state"] = state
    action = probe_entry_action(row)
    if action is not None:
        row["probe_entry_action"] = action


def allocate_l5(subscribed_slugs, state, targets=None, now=None,
                l5_infos=None):
    """Keep total L5 seats fixed while reserving a few for spring watch."""
    return allocate_with_spring_reserve(
        _ORIGINAL_ALLOCATE_L5, subscribed_slugs, state, targets=targets,
        now=now, l5_infos=l5_infos)


def cached_market_state(slug: str) -> Optional[dict]:
    """The venue open-state already cached for spring watch (no network):
    the multi-market shadow's execution-eligibility layer reads it to flag
    closed/halted markets; absent stays absent."""
    hit = _MARKET_STATE_CACHE.get(slug)
    return hit[1] if hit else None


def install() -> None:
    """Install read-only status/replay and compute-allocation enrichments."""
    base.ws_gate = ws_gate
    base.spring_watch = spring_watch
    base.SHADOW_MARKET_STATE_OF = cached_market_state
    pq.allocate_l5 = allocate_l5
    pq.plan = plan


def main() -> int:
    install()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
