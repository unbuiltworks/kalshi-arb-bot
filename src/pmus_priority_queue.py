"""HOT/WARM/COLD market priority queue over full-universe discovery.

Issue #167 comment 5521210994: the funnel is
  full REST discovery (hundreds+) -> cheap screening -> WS observation
  (tens, capped) -> expensive L5 sequential state (top 5-20) ->
  execution (ONE market, DISARMED elsewhere).

This module is the WS-slot allocator between screening and observation.
It is PURE (no network, no mutation, no order path of any kind) and
replaces blunt rotation with three tiers under the subscription cap:

* HOT  — highest-ranked markets pinned continuously (hysteresis:
  min-hold before demotion, promotion cooldown so transient spikes do
  not thrash subscriptions).
* WARM — fast rotation over the next-ranked band.
* COLD — slow GUARANTEED sampling of everything else, ordered by
  last-observed age (starvation term: every valid discovered market —
  props and alt lines included — is observed within a bounded number of
  cycles, never silently excluded by market type).
* WAKEUP — a cold market with sudden trade activity promotes rapidly,
  bypassing the cooldown.

Scoring discipline (per the directive): every component is bounded to
[0,1] or None (missing); the score is the mean of PRESENT components —
never a multiplicative product, so one noisy/unknown component cannot
zero a market. Hard exclusions do not exist at this layer; admission is
discovery's job and the only unrankable state is "never observed",
which lands in COLD where the aging term guarantees a first look.

Expensive per-market L5 sequential state is allocated only to the top
`l5_alloc_n` subscribed markets (configurable 5-20); everything else
keeps cheap discovery metadata only.
"""
from __future__ import annotations

import os
import time
from typing import Any, Mapping, Optional

HOT_FRAC_ENV = "PMUS_WS_HOT_FRAC"          # default 0.6 of the cap
COLD_SLOTS_ENV = "PMUS_WS_COLD_SLOTS"      # default max(2, cap//10)
MIN_HOLD_ENV = "PMUS_WS_MIN_HOLD_CYCLES"   # default 3
COOLDOWN_ENV = "PMUS_WS_PROMOTE_COOLDOWN"  # default 2 cycles after demote
WAKE_TRADES_ENV = "PMUS_WS_WAKE_TRADES"    # default 3 new trades
L5_ALLOC_ENV = "PMUS_WS_L5_ALLOC"          # default 10 (bounded 5..20)


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else (1.0 if x > 1 else x)


def _qnum(v) -> Optional[float]:
    """Tolerant numeric from venue quote/price shapes ({value:..},
    {price:..}, scalar). None when absent/unparseable — never guessed."""
    if isinstance(v, Mapping):
        v = v.get("value", v.get("price", v.get("px")))
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def seed_components(target: Optional[Mapping[str, Any]]) -> dict:
    """Cheap PRE-WS screening from discovery metadata (issue #167
    comments 5521287107/5521333079): scores a never-observed market from
    fields the venue actually served — public BBO/spread, volume/
    activity, live/phase. Unknown stays None (neutral), never zero."""
    comp: dict[str, Optional[float]] = {
        "seed_spread_quality": None, "seed_activity": None,
        "seed_live_phase": None,
    }
    if not target:
        return comp
    meta = target.get("market_meta") or {}
    bid = _qnum(meta.get("bestBidQuote"))
    ask = _qnum(meta.get("bestAskQuote"))
    if bid is not None and ask is not None and ask >= bid:
        comp["seed_spread_quality"] = _clamp01(1.0 - (ask - bid) / 0.05)
    vol = _qnum(meta.get("volume24hr"))
    if vol is None:
        vol = _qnum(meta.get("volume"))
    if vol is None:
        vol = _qnum(meta.get("liquidity"))
    if vol is not None:
        comp["seed_activity"] = _clamp01(vol / 1000.0)
    ev = target.get("event_state")
    if isinstance(ev, Mapping) and "live" in ev:
        comp["seed_live_phase"] = 1.0 if ev.get("live") else 0.2
    return comp


def seed_signature(target: Optional[Mapping[str, Any]]) -> tuple:
    """Public-metadata fingerprint: a CHANGE in it on a cold/idle market
    is a wake signal even before authenticated sampling."""
    if not target:
        return ()
    meta = target.get("market_meta") or {}
    return (_qnum(meta.get("bestBidQuote")), _qnum(meta.get("bestAskQuote")),
            _qnum(meta.get("volume24hr")) or _qnum(meta.get("volume")))


def score_market(s: Optional[Mapping[str, Any]], now: float,
                 l5_info: Optional[Mapping[str, Any]] = None,
                 target: Optional[Mapping[str, Any]] = None) -> dict:
    """Bounded component scores. AUTHENTICATED evidence dominates: once a
    market has been WS-observed, only authenticated components score it
    and the seed components go None; a never-observed market scores from
    the cheap discovery seed instead of sitting at None until sampled.
    None = missing (exposed, never treated as zero)."""
    comp: dict[str, Optional[float]] = {
        "trade_rate": None, "book_rate": None, "two_sided": None,
        "depth": None, "spread_quality": None, "tte_phase": None,
        "l5_suitability": None, "completion_prob": None,
        "seed_spread_quality": None, "seed_activity": None,
        "seed_live_phase": None,
    }
    observed = bool(s and (s.get("book_updates") or s.get("trades")))
    if not observed:
        comp.update(seed_components(target))
    if s:
        trades = [t for t in s.get("trades", [])
                  if now - t.get("t", 0) <= 600]
        comp["trade_rate"] = _clamp01(len(trades) / 50.0)
        comp["book_rate"] = _clamp01(float(s.get("book_updates", 0)) / 500.0)
        comp["two_sided"] = 1.0 if s.get("both_sides_served") else 0.0
        try:
            bq, aq = float(s.get("bid_qty")), float(s.get("ask_qty"))
            comp["depth"] = _clamp01(min(bq, aq) / 100.0)
        except (TypeError, ValueError):
            pass
        try:
            spread = float(s.get("ask")) - float(s.get("bid"))
            comp["spread_quality"] = _clamp01(1.0 - spread / 0.05)
        except (TypeError, ValueError):
            pass
        counts = s.get("_slot_counts") or {}
        if isinstance(counts, Mapping):
            matched = int(counts.get("MATCHED") or 0)
            total = sum(int(v) for v in counts.values()
                        if isinstance(v, int))
            if total >= 4:
                comp["completion_prob"] = _clamp01(matched / total)
    if l5_info:
        if l5_info.get("off_distribution"):
            comp["l5_suitability"] = 0.0
        else:
            comp["l5_suitability"] = _clamp01(
                float(l5_info.get("act_p") or 0.0))
    present = {k: v for k, v in comp.items() if v is not None}
    score = (sum(present.values()) / len(present)) if present else None
    return {"score": score, "components": comp,
            "present": len(present), "missing": len(comp) - len(present)}


def allocate_l5(subscribed_slugs, state: Mapping[str, Any],
                targets: Optional[list[Mapping[str, Any]]] = None,
                now: Optional[float] = None,
                l5_infos: Optional[Mapping[str, Mapping[str, Any]]] = None
                ) -> list[str]:
    """Fast intra-cycle L5 allocation (issue #167 comment 5524827113):
    re-rank ONLY the already-subscribed slugs on CURRENT evidence, with
    the exact score_market ranking and PMUS_WS_L5_ALLOC bound plan()
    uses. Pure and read-only — no tier or planner-state mutation, no
    subscription change; callers keep the live subscribe set as-is and
    only move the expensive per-market L5 shadow between status ticks."""
    now = now or time.time()
    target_by_slug: dict[str, Mapping[str, Any]] = {}
    for t in targets or []:
        if t.get("slug") and t["slug"] not in target_by_slug:
            target_by_slug[t["slug"]] = t
    slugs = list(dict.fromkeys(subscribed_slugs))
    scored = {s: score_market(state.get(s), now, (l5_infos or {}).get(s),
                              target=target_by_slug.get(s)) for s in slugs}
    ranked = sorted(slugs, key=lambda x: (
        -(scored[x]["score"] if scored[x]["score"] is not None else -1.0),
        x))
    l5_n = min(20, max(5, int(os.getenv(L5_ALLOC_ENV, "10"))))
    return ranked[:l5_n]


def plan(targets: list[Mapping[str, Any]], state: Mapping[str, Any],
         cap: int, cycle: int, prev: Optional[Mapping[str, Any]] = None,
         now: Optional[float] = None,
         l5_infos: Optional[Mapping[str, Mapping[str, Any]]] = None) -> dict:
    """One planning cycle. `prev` is the planner state returned by the
    previous cycle (tier memory, hysteresis clocks, wake baselines)."""
    now = now or time.time()
    prev = dict(prev or {})
    cap = max(1, int(cap))
    hot_n = max(1, int(cap * float(os.getenv(HOT_FRAC_ENV, "0.6"))))
    cold_slots = int(os.getenv(COLD_SLOTS_ENV, str(max(2, cap // 10))))
    cold_slots = min(cold_slots, max(cap - hot_n - 1, 0))
    warm_n = max(cap - hot_n - cold_slots, 0)
    min_hold = int(os.getenv(MIN_HOLD_ENV, "3"))
    cooldown = int(os.getenv(COOLDOWN_ENV, "2"))
    wake_trades = int(os.getenv(WAKE_TRADES_ENV, "3"))

    target_by_slug: dict[str, Mapping[str, Any]] = {}
    for t in targets:
        if t.get("slug") and t["slug"] not in target_by_slug:
            target_by_slug[t["slug"]] = t
    slugs = list(target_by_slug)
    scored: dict[str, dict] = {}
    wakes: set[str] = set()
    sigs: dict[str, tuple] = {}
    for slug in slugs:
        s = state.get(slug)
        sc = score_market(s, now, (l5_infos or {}).get(slug),
                          target=target_by_slug.get(slug))
        scored[slug] = sc
        p = prev.get(slug) or {}
        n_trades = len((s or {}).get("trades", []))
        # a not-currently-observed market (cold OR idle) that suddenly
        # trades must promote rapidly, bypassing the cooldown
        if p.get("tier") in ("cold", "idle") and \
                n_trades - int(p.get("trades_seen", 0)) >= wake_trades:
            wakes.add(slug)
        # public-metadata wake (issue #167 comment 5521287107): a CHANGE
        # in the cheap discovery fingerprint on a cold/idle market lifts
        # it before authenticated sampling ever reached it
        sig = seed_signature(target_by_slug.get(slug))
        sigs[slug] = sig
        old_sig = p.get("seed_sig")
        if p.get("tier") in ("cold", "idle") and old_sig is not None and \
                tuple(old_sig) != sig and any(v is not None for v in sig):
            wakes.add(slug)

    ranked = sorted(slugs, key=lambda x: (
        -(scored[x]["score"] if scored[x]["score"] is not None else -1.0),
        x))
    rank_of = {slug: i + 1 for i, slug in enumerate(ranked)}

    prev_hot = {s for s, p in prev.items()
                if p.get("tier") == "hot" and s in scored}
    # Phase 1 — hysteresis: sitting hot members RETAIN their seat while
    # inside min_hold, and afterwards while still ranked within hot_n +
    # a demote margin. Retention has seat priority over new promotions
    # so a transient score dip cannot thrash the subscription set.
    margin = max(2, hot_n // 4)
    hot: list[str] = []
    for slug in sorted(prev_hot, key=lambda x: rank_of[x]):
        p = prev.get(slug) or {}
        held = cycle - int(p.get("since", cycle))
        if (held < min_hold or rank_of[slug] <= hot_n + margin) and \
                len(hot) < hot_n:
            hot.append(slug)
    # Phase 2 — fill remaining seats from the ranking; a freshly-demoted
    # market waits out the cooldown unless it woke on sudden activity.
    for slug in ranked:
        if len(hot) >= hot_n:
            break
        if slug in hot:
            continue
        in_cd = cycle - int((prev.get(slug) or {})
                            .get("demoted_at", -10**9)) < cooldown
        if slug in wakes or not in_cd:
            hot.append(slug)

    rest = [s for s in ranked if s not in hot]
    warm = []
    if rest and warm_n:
        start = (cycle * warm_n) % len(rest)
        warm = (rest + rest)[start:start + min(warm_n, len(rest))]
    cold_universe = [s for s in rest if s not in warm]
    cold_universe.sort(key=lambda x: (
        int((prev.get(x) or {}).get("last_obs", -1)), x))   # oldest first
    cold = cold_universe[:cold_slots]
    for w in wakes:                          # rapid promotion of wakeups
        if w not in hot and w not in warm and w not in cold:
            if cold:
                cold[-1] = w
            elif warm:
                warm[-1] = w

    subscribe = list(dict.fromkeys(hot + warm + cold))[:cap]
    sub_set = set(subscribe)

    new_prev: dict[str, dict] = {}
    reasons: dict[str, str] = {}
    for slug in slugs:
        p = dict(prev.get(slug) or {})
        old = p.get("tier")
        tier = ("hot" if slug in hot else
                "warm" if slug in warm else
                "cold" if slug in cold else "idle")
        if tier != old:
            if old == "hot" and tier != "hot":
                p["demoted_at"] = cycle
                reasons[slug] = f"demote:{old}->{tier}:rank={rank_of[slug]}"
            elif slug in wakes:
                reasons[slug] = "wake:sudden_activity"
            else:
                reasons[slug] = f"promote:{old}->{tier}:rank={rank_of[slug]}"
            p["since"] = cycle
        p["tier"] = tier
        if slug in sub_set:
            p["last_obs"] = cycle
        p["trades_seen"] = len((state.get(slug) or {}).get("trades", []))
        p["seed_sig"] = sigs.get(slug, ())
        new_prev[slug] = p

    l5_n = min(20, max(5, int(os.getenv(L5_ALLOC_ENV, "10"))))
    l5_allocated = [s for s in ranked if s in sub_set][:l5_n]

    ages = [cycle - int(new_prev[s].get("last_obs", cycle)) for s in slugs]
    ages_sorted = sorted(ages)
    diag = {
        "cycle": cycle, "cap": cap,
        "hot": len(hot), "warm": len(warm), "cold_sampled": len(cold),
        "universe": len(slugs), "subscribed": len(subscribe),
        "wake_promotions": sorted(wakes),
        "l5_state_allocated": len(l5_allocated),
        "coverage_age_median": ages_sorted[len(ages) // 2] if ages else 0,
        "coverage_age_p95": ages_sorted[int(0.95 * (len(ages) - 1))]
        if ages else 0,
        "coverage_age_max": max(ages) if ages else 0,
    }
    per_market = {
        slug: {"score": scored[slug]["score"],
               "components": scored[slug]["components"],
               "missing": scored[slug]["missing"],
               "rank": rank_of[slug],
               "tier": new_prev[slug]["tier"],
               "time_in_tier": cycle - int(new_prev[slug].get("since",
                                                              cycle)),
               "last_observed_age": cycle - int(new_prev[slug]
                                                .get("last_obs", cycle)),
               "reason": reasons.get(slug)}
        for slug in slugs}
    return {"subscribe": subscribe, "l5_allocated": l5_allocated,
            "diagnostics": diag, "per_market": per_market,
            "state": new_prev}
