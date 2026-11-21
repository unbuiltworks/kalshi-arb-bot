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
from typing import Any, Iterable, Mapping, Optional

HOT_FRAC_ENV = "PMUS_WS_HOT_FRAC"          # default 0.6 of the cap
COLD_SLOTS_ENV = "PMUS_WS_COLD_SLOTS"      # default max(2, cap//10)
MIN_HOLD_ENV = "PMUS_WS_MIN_HOLD_CYCLES"   # default 3
COOLDOWN_ENV = "PMUS_WS_PROMOTE_COOLDOWN"  # default 2 cycles after demote
WAKE_TRADES_ENV = "PMUS_WS_WAKE_TRADES"    # default 3 new trades
L5_ALLOC_ENV = "PMUS_WS_L5_ALLOC"          # default 10 (bounded 0..20; 0 = no L5 allocation, OWNER 5554106892)
CORE_MIN_FRAC_ENV = "PMUS_WS_CORE_MIN_FRAC"   # default 0.35 of HOT reserved for the liquid core
PROP_MAX_FRAC_ENV = "PMUS_WS_PROP_MAX_FRAC"   # default 0.50 ceiling on HOT seats held by props
BAND_LO, BAND_HI = 0.25, 0.75                 # mirrors SPRING_BAND_LO/HI
STALE_S_ENV = "PMUS_WS_STALE_S"            # default 900 s: an OBSERVED market whose authenticated
                                           # book/trade stream has been silent this long is STALE
BOOK_RATE_WINDOW_S = 600.0                 # recent-window book activity (issue #167 5571181823)


# ---------------------------------------------------------------------------
# MARKET CLASS -- the axis the seat allocation actually needed.
#
# The measured problem (funnel audit, 2026-09-13): 76 of 80 seats were refused
# on thin flow, because the QUALIFIED universe is 55% astatc- player stat props
# and 18% tsc- team stat props, while the liquid core -- aec- event moneylines --
# is TWENTY-ONE markets across the whole live venue. Ranking by score alone
# cannot fix that: 1,630 props each scoring respectably will still crowd out 21
# moneylines, because the mean-of-components score has no notion of scarcity.
# So class drives BOTH a score prior AND an explicit seat quota.
#
# PROPS ARE NEVER PERMANENTLY EXCLUDED. The cap is on HOT seats only; COLD's
# oldest-first sampling still guarantees every discovered market a look, and a
# prop that starts trading wakes through the same wake path as anything else.
CLASS_MONEYLINE, CLASS_SPREAD = "moneyline", "spread"
CLASS_TOTAL, CLASS_PROP, CLASS_OTHER = "total", "prop", "other"

# PMUS slug prefixes, from the live venue: aec- event/moneyline, asc- spread,
# atc- total, astatc- player stat prop, tsc- team stat prop.
_CLASS_PREFIX = (("astatc-", CLASS_PROP), ("tsc-", CLASS_PROP),
                 ("asc-", CLASS_SPREAD), ("atc-", CLASS_TOTAL),
                 ("aec-", CLASS_MONEYLINE))

# The liquid core: what a seat is most likely to turn into executable L5
# evidence. Spreads and totals are MAIN LINES and belong here; player/team stat
# props do not.
CORE_CLASSES = frozenset({CLASS_MONEYLINE, CLASS_SPREAD, CLASS_TOTAL})

CLASS_PRIOR = {CLASS_MONEYLINE: 1.0, CLASS_SPREAD: 0.7, CLASS_TOTAL: 0.6,
               CLASS_OTHER: 0.5, CLASS_PROP: 0.25}


def market_class(target: Optional[Mapping[str, Any]]) -> str:
    """Class from the venue's own slug prefix, falling back to its market-type
    fields. Unknown shapes land in OTHER (a mid prior), never in PROP -- being
    unrecognised must not silently demote a market."""
    if not target:
        return CLASS_OTHER
    slug = str(target.get("slug") or "").lower()
    for pre, cls in _CLASS_PREFIX:
        if slug.startswith(pre):
            return cls
    mt = str(target.get("sports_market_type") or target.get("sportsMarketType")
             or target.get("market_type") or target.get("marketType") or "").lower()
    if "player" in mt or "prop" in mt or "stat" in mt:
        return CLASS_PROP
    if "spread" in mt:
        return CLASS_SPREAD
    if "total" in mt or "over_under" in mt:
        return CLASS_TOTAL
    if "moneyline" in mt or mt in ("event", "winner"):
        return CLASS_MONEYLINE
    return CLASS_OTHER


def band_fit(bid: Optional[float]) -> Optional[float]:
    """1.0 inside the executable band, decaying outside it rather than a cliff.

    A market at 0.24 is nearly admissible and a market at 0.02 is not, and a
    hard 0/1 would rank those two identically. None (unknown price) stays None
    so it is treated as missing, not as bad."""
    if bid is None:
        return None
    if BAND_LO <= bid <= BAND_HI:
        return 1.0
    d = (BAND_LO - bid) if bid < BAND_LO else (bid - BAND_HI)
    return _clamp01(1.0 - d / BAND_LO)


def last_authenticated_t(s: Optional[Mapping[str, Any]]) -> Optional[float]:
    """Time of the last authenticated book update or trade for a market,
    from the bounded bbo_history / trades the recorder keeps; None when
    the market was never observed."""
    if not s:
        return None
    ts = []
    hist = s.get("bbo_history") or []
    if hist and isinstance(hist[-1], Mapping) and hist[-1].get("t") is not None:
        ts.append(float(hist[-1]["t"]))
    trades = s.get("trades") or []
    if trades and isinstance(trades[-1], Mapping) and trades[-1].get("t") is not None:
        ts.append(float(trades[-1]["t"]))
    return max(ts) if ts else None


def is_stale(s: Optional[Mapping[str, Any]], now: float,
             stale_s: Optional[float] = None) -> bool:
    """An observed market with no authenticated update for stale_s. A
    resolved/settled market's book freezes, so this is how the planner
    evicts yesterday's games without a venue status call (issue #167
    comment 5571181823: HOT pinned 39 of 46 top markets from the previous
    day, median silence 17.5 h). Never-observed markets are not stale."""
    if stale_s is None:
        stale_s = float(os.getenv(STALE_S_ENV, "900"))
    t = last_authenticated_t(s)
    if t is None:
        return False
    return (now - t) > stale_s


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
        # OWNER seat-value directive: price band, settlement compatibility and
        # market type were the three ranking inputs the scorer did not have.
        "band_fit": None, "settlement_ok": None, "class_prior": None,
    }
    # Only when a target is actually supplied: with no discovery row we do not
    # KNOW the class, and defaulting to the OTHER prior would be a fabricated
    # component -- it would also destroy the "never-observed scores None, ranks
    # last, stays COLD-coverable" invariant the planner depends on.
    if target is not None:
        comp["class_prior"] = CLASS_PRIOR[market_class(target)]
        status = str(target.get("status") or target.get("market_status") or "")
        if status:
            comp["settlement_ok"] = 1.0 if status.endswith("OPEN") else 0.0
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
        # OWNER seat-value directive: price band, settlement compatibility and
        # market type were the three ranking inputs the scorer did not have.
        "band_fit": None, "settlement_ok": None, "class_prior": None,
    }
    # Only when a target is actually supplied: with no discovery row we do not
    # KNOW the class, and defaulting to the OTHER prior would be a fabricated
    # component -- it would also destroy the "never-observed scores None, ranks
    # last, stays COLD-coverable" invariant the planner depends on.
    if target is not None:
        comp["class_prior"] = CLASS_PRIOR[market_class(target)]
        status = str(target.get("status") or target.get("market_status") or "")
        if status:
            comp["settlement_ok"] = 1.0 if status.endswith("OPEN") else 0.0
    observed = bool(s and (s.get("book_updates") or s.get("trades")))
    if not observed:
        comp.update(seed_components(target))
    if s:
        trades = [t for t in s.get("trades", [])
                  if now - t.get("t", 0) <= 600]
        comp["trade_rate"] = _clamp01(len(trades) / 50.0)
        hist = s.get("bbo_history") or []
        if hist:
            # recent-window rate: a frozen (resolved) book scores 0 here
            # instead of carrying its lifetime count forever
            recent = sum(1 for h in hist if isinstance(h, Mapping)
                         and now - float(h.get("t") or 0) <= BOOK_RATE_WINDOW_S)
            comp["book_rate"] = _clamp01(recent / 50.0)
        else:
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
        try:
            comp["band_fit"] = band_fit(float(s.get("bid")))
        except (TypeError, ValueError):
            pass
        counts = s.get("_slot_counts") or {}
        if isinstance(counts, Mapping):
            matched = int(counts.get("MATCHED") or 0)
            total = sum(int(v) for v in counts.values()
                        if isinstance(v, int))
            if total >= 4:
                comp["completion_prob"] = _clamp01(matched / total)
    if comp["band_fit"] is None and target is not None:
        for k in ("bid", "best_bid", "bestBid"):
            try:
                comp["band_fit"] = band_fit(float(target.get(k)))
                break
            except (TypeError, ValueError):
                continue
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
    l5_n = min(20, max(0, int(os.getenv(L5_ALLOC_ENV, "10"))))
    return ranked[:l5_n]


def plan(targets: list[Mapping[str, Any]], state: Mapping[str, Any],
         cap: int, cycle: int, prev: Optional[Mapping[str, Any]] = None,
         now: Optional[float] = None,
         l5_infos: Optional[Mapping[str, Mapping[str, Any]]] = None,
         protected: Optional[Iterable[str]] = None) -> dict:
    """One planning cycle. `prev` is the planner state returned by the
    previous cycle (tier memory, hysteresis clocks, wake baselines).

    `protected` are slugs that MUST keep a seat -- the markets the executor is
    actually holding risk in. Losing observation on a held market is the one
    eviction that can hurt: the settlement auditor and the residual accounting
    both read that market's stream, so it is pinned ahead of every ranking,
    quota and staleness rule. It is bounded by `cap` like everything else."""
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

    stale_s = float(os.getenv(STALE_S_ENV, "900"))
    # a discovered-LIVE event whose stream went quiet is a feed question,
    # not a resolved market: it keeps COLD eligibility so it is re-sampled
    discovered_live = {slug for slug, t in target_by_slug.items()
                       if ((t.get("event_state") or {}).get("live") is True)}
    stale = {slug for slug in slugs if is_stale(state.get(slug), now, stale_s)}
    evicted = stale - discovered_live
    # a stale market cannot hold or win a HOT/WARM seat: it ranks below
    # every live-or-unobserved market and is only re-sampled by COLD
    # (oldest-first), which is how a wrongly flagged one recovers
    ranked = sorted(slugs, key=lambda x: (
        x in stale,
        -(scored[x]["score"] if scored[x]["score"] is not None else -1.0),
        x))
    rank_of = {slug: i + 1 for i, slug in enumerate(ranked)}

    # Held markets are pinned FIRST, before retention, ranking or quota.
    pinned = [x for x in dict.fromkeys(protected or ()) if x in target_by_slug][:cap]
    prev_hot = {s for s, p in prev.items()
                if p.get("tier") == "hot" and s in scored and s not in stale}
    # Phase 1 — hysteresis: sitting hot members RETAIN their seat while
    # inside min_hold, and afterwards while still ranked within hot_n +
    # a demote margin. Retention has seat priority over new promotions
    # so a transient score dip cannot thrash the subscription set.
    margin = max(2, hot_n // 4)
    hot: list[str] = list(pinned)
    for slug in sorted(prev_hot, key=lambda x: rank_of[x]):
        if slug in hot:
            continue
        p = prev.get(slug) or {}
        held = cycle - int(p.get("since", cycle))
        if (held < min_hold or rank_of[slug] <= hot_n + margin) and \
                len(hot) < hot_n:
            hot.append(slug)
    # Phase 2 — fill remaining seats from the ranking, UNDER A CLASS QUOTA.
    #
    # Score alone cannot protect the liquid core. Measured 2026-09-13: the
    # qualified universe is 55% player props and 18% team-stat props against 21
    # aec- moneylines venue-wide, so 1,630 respectable-scoring props crowd out
    # the twenty-one markets most likely to produce a real two-sided fill. The
    # quota is two-sided:
    #   CORE RESERVE — at least core_min HOT seats go to moneyline/spread/total,
    #     bounded by how many such markets actually exist (never holds a seat
    #     empty waiting for a core market that is not there);
    #   PROP CEILING — props may hold at most prop_max HOT seats.
    # Props are NOT excluded: COLD's oldest-first sampling still guarantees them
    # a look, and the wake path still promotes one that starts trading.
    cls_of = {slug: market_class(target_by_slug.get(slug)) for slug in slugs}
    core_min = min(
        int(round(hot_n * float(os.getenv(CORE_MIN_FRAC_ENV, "0.35")))),
        sum(1 for x in ranked if cls_of[x] in CORE_CLASSES and x not in stale))
    prop_max = int(round(hot_n * float(os.getenv(PROP_MAX_FRAC_ENV, "0.50"))))

    def _n_core(seats): return sum(1 for x in seats if cls_of[x] in CORE_CLASSES)
    def _n_prop(seats): return sum(1 for x in seats if cls_of[x] == CLASS_PROP)

    def _fill(want_core_only: bool) -> None:
        for slug in ranked:
            if len(hot) >= hot_n:
                return
            # The core pass fills only up to the RESERVE, never the whole
            # bench: the reserve is a floor for the scarce core, not a ceiling
            # on everything else, and filling hot_n with core markets here
            # would starve the flow-ranked majority the score exists to pick.
            if want_core_only and _n_core(hot) >= core_min:
                return
            if slug in hot or slug in stale:
                continue
            is_core = cls_of[slug] in CORE_CLASSES
            if want_core_only and not is_core:
                continue
            if not want_core_only:
                # leave room for the core reserve that is still unmet
                if not is_core and \
                        hot_n - len(hot) <= max(0, core_min - _n_core(hot)):
                    continue
                if cls_of[slug] == CLASS_PROP and _n_prop(hot) >= prop_max:
                    continue
            in_cd = cycle - int((prev.get(slug) or {})
                                .get("demoted_at", -10**9)) < cooldown
            if slug in wakes or not in_cd:
                hot.append(slug)

    _fill(want_core_only=True)     # seat the scarce liquid core first
    _fill(want_core_only=False)    # then everything else under the ceiling

    rest = [s for s in ranked if s not in hot]
    warm = []
    fresh_rest = [s for s in rest if s not in stale]
    if fresh_rest and warm_n:
        start = (cycle * warm_n) % len(fresh_rest)
        warm = (fresh_rest + fresh_rest)[start:start + min(warm_n, len(fresh_rest))]
    cold_universe = [s for s in rest if s not in warm and s not in evicted]
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

    l5_n = min(20, max(0, int(os.getenv(L5_ALLOC_ENV, "10"))))
    l5_allocated = [s for s in ranked if s in sub_set][:l5_n]

    ages = [cycle - int(new_prev[s].get("last_obs", cycle)) for s in slugs]
    ages_sorted = sorted(ages)
    diag = {
        "cycle": cycle, "cap": cap,
        "hot": len(hot), "warm": len(warm), "cold_sampled": len(cold),
        "universe": len(slugs), "subscribed": len(subscribe),
        "wake_promotions": sorted(wakes),
        "stale_evicted": len(evicted),
        "stale_but_discovered_live": len(stale & discovered_live),
        "stale_s": stale_s,
        "fresh_live_subscribed": sum(1 for x in subscribe if x not in stale),
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
               "stale": slug in stale,
               "time_in_tier": cycle - int(new_prev[slug].get("since",
                                                              cycle)),
               "last_observed_age": cycle - int(new_prev[slug]
                                                .get("last_obs", cycle)),
               "reason": reasons.get(slug)}
        for slug in slugs}
    return {"subscribe": subscribe, "l5_allocated": l5_allocated,
            "diagnostics": diag, "per_market": per_market,
            "state": new_prev}
