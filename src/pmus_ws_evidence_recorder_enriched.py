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
PURSUIT_SEATS_ENV = "PMUS_SPRING_PURSUIT_SEATS"   # default 3, clamped 0..4
PURSUIT_TTL_ENV = "PMUS_SPRING_PURSUIT_TTL_S"     # default 900s


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


def plan(targets, state, cap, cycle, prev=None, now=None, l5_infos=None):
    """Canonical priority plan plus a bounded evidence-pursuit reserve.

    The global cap is UNCHANGED: each reserved candidate replaces the
    lowest-ranked WARM seat only — hot seats and the COLD/starvation
    sampler are never evicted, so broad coverage is preserved. With no
    warm seat available, fewer (or zero) seats are reserved: fail closed
    rather than widen the subscription set."""
    import time as _time
    now = now or _time.time()
    p = _ORIGINAL_PLAN(targets, state, cap, cycle, prev, now=now,
                       l5_infos=l5_infos)
    seats = max(0, min(4, int(os.getenv(PURSUIT_SEATS_ENV, "3"))))
    ttl = float(os.getenv(PURSUIT_TTL_ENV, "900"))
    p["diagnostics"]["spring_ws_reserved"] = []
    if seats == 0:
        return p
    subscribe = list(p["subscribe"])
    cands = evidence_pursuit_candidates(state, now, set(subscribe),
                                        ttl)[:seats]
    if not cands:
        return p
    per = p.get("per_market") or {}
    warm_tail = [s for s in reversed(subscribe)
                 if per.get(s, {}).get("tier") == "warm"]
    reserved = []
    for cand in cands:
        if not warm_tail:
            break
        evicted = warm_tail.pop(0)
        subscribe[subscribe.index(evicted)] = cand["slug"]
        reserved.append({**cand, "evicted_warm_seat": evicted})
    p["subscribe"] = subscribe
    p["diagnostics"]["spring_ws_reserved"] = reserved
    return p

# Conservative, configurable completion floor for the binary advisory
# status (issue #167 comment 5525966568): no invented P&L threshold —
# evidence sufficiency + existing spring invariants only, raw counts
# always reported so the stage decision stays with the operator/owner.
PROBE_MIN_ENV = "PMUS_SPRING_PROBE_MIN_COMPLETIONS"
PROBE_MIN_TRADES_1H = 10


def probe_evidence(row: Mapping[str, Any]) -> dict:
    """ADVISORY-ONLY probe-evidence status for one spring-watch row.
    Never mutates or relaxes spring_candidate_gate / micro_eligible."""
    min_completions = max(1, int(os.getenv(PROBE_MIN_ENV, "3")))
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
    matched = int(row.get("spring_matched_slots") or 0)
    if matched < min_completions:
        reasons.append(
            f"insufficient_completions:{matched}<{min_completions}")
    return {"status": ("READY_FOR_REVIEW" if not reasons
                       else "INSUFFICIENT_EVIDENCE"),
            "reasons": reasons,
            "min_completions_required": min_completions,
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
    STARTED 45-55c and later showed a <=0.96 maker-complement path count.
    Neither is an execution admission.
    """
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
    return out


def allocate_l5(subscribed_slugs, state, targets=None, now=None,
                l5_infos=None):
    """Keep total L5 seats fixed while reserving a few for spring watch."""
    return allocate_with_spring_reserve(
        _ORIGINAL_ALLOCATE_L5, subscribed_slugs, state, targets=targets,
        now=now, l5_infos=l5_infos)


def install() -> None:
    """Install read-only status/replay and compute-allocation enrichments."""
    base.ws_gate = ws_gate
    base.spring_watch = spring_watch
    pq.allocate_l5 = allocate_l5
    pq.plan = plan


def main() -> int:
    install()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
