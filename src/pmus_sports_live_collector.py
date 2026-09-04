"""Authoritative all-live-sports runner for the read-only PMUS collector.

This is a production wrapper around :mod:`src.pmus_sports_collector` that
fixes the remaining discovery choke point without changing any execution
path.  It uses the venue's server-side live sports surface as the primary
universe, guarantees broad event coverage under the polling cap, and keeps
stale incentive-only markets from crowding out genuinely live sports.

READ ONLY: every venue call is delegated to the base collector's GET-only
``http_get_json`` helper; persistence uses the base append/state helpers.
"""
from __future__ import annotations

import math
import os
import time
from collections import defaultdict
from typing import Any, Iterable, Mapping, Optional

from src import pmus_sports_collector as col

# Owner directive 5534173817 item 2: the authoritative all-sports runner
# targets the full live universe — at least 64 seats, ranked before
# capping — even when the deployment env is absent. The base module's
# legacy DEFAULT_MAX_MARKETS=24 stays untouched for the fallback tools.
DEFAULT_LIVE_MAX_MARKETS = 64


def _number(value: Any) -> float:
    if isinstance(value, Mapping):
        value = value.get("value") or value.get("px")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# Bounded continuously-polled priority set (issue #167 comment
# 5541422910). Rotation makes per-market cadence jittery, and
# analyze_market fails a market for ANY inter-poll gap over 60s, so a
# rotated market is structurally unable to earn readiness evidence no
# matter how good its book is. Pinned slugs are selected EVERY cycle so
# their cadence equals the cycle time; the rest of the slate keeps
# rotating under the unchanged cap and rate budget. Read-only: this
# changes only WHICH markets are polled, never a gate, threshold, fee,
# freshness, residual or loss rule.
PIN_SLUGS_ENV = "PMUS_PIN_SLUGS"        # operator-pinned, comma-separated
PIN_MAX_ENV = "PMUS_PIN_MAX"            # hard-capped below
PIN_MAX_DEFAULT = 6
PIN_MAX_CEILING = 12
_auto_pins: list[str] = []              # fed from the L5 candidate path


# Being IN every cycle is necessary but not sufficient: a cycle is a
# sequential sweep of ~64 markets x 2 GETs, so its wall clock lands near
# 60s — right at the gap threshold. Pinned markets therefore re-enter the
# poll order every PIN_INTERLEAVE_EVERY markets, making their cadence a
# fraction of the sweep instead of equal to it. The rate plan is computed
# on the INTERLEAVED order so the venue budget is still honoured.
#
# The default is 6, not 15, because 15 does not actually clear the gate it
# exists to clear. Measured live against the PMUS slate on 2026-09-04:
# every=15 left pinned markets at a 56.5s median and a 127s worst gap —
# still tripping analyze_market's 60s rule — while every=6 held the same
# markets to a 10.5s median and an 11.9s worst gap. Raising this value
# trades pinned-market cadence for slate breadth and can silently put a
# pinned market back over 60s, so re-measure before doing so.
PIN_INTERLEAVE_ENV = "PMUS_PIN_INTERLEAVE_EVERY"
PIN_INTERLEAVE_DEFAULT = 6


def pin_interleave_every() -> int:
    try:
        return max(0, int(os.getenv(PIN_INTERLEAVE_ENV,
                                    str(PIN_INTERLEAVE_DEFAULT))))
    except ValueError:
        return PIN_INTERLEAVE_DEFAULT


def interleaved_order(targets: Iterable[Mapping[str, Any]],
                      pin_slugs: Iterable[str],
                      every: Optional[int] = None) -> list[dict]:
    """Poll order where pinned markets recur every `every` markets."""
    every = pin_interleave_every() if every is None else every
    pin_set = {str(s) for s in pin_slugs}
    rows = [dict(t) for t in targets]
    pins = [t for t in rows if str(t.get("slug")) in pin_set]
    rest = [t for t in rows if str(t.get("slug")) not in pin_set]
    if not pins:
        return rest
    out = list(pins)
    for i, t in enumerate(rest, 1):
        out.append(t)
        if every and i % every == 0 and i < len(rest):
            out.extend(pins)
    return out


def pin_max() -> int:
    try:
        return max(0, min(PIN_MAX_CEILING,
                          int(os.getenv(PIN_MAX_ENV, str(PIN_MAX_DEFAULT)))))
    except ValueError:
        return PIN_MAX_DEFAULT


def operator_pins() -> list[str]:
    return [s.strip() for s in os.getenv(PIN_SLUGS_ENV, "").split(",")
            if s.strip()]


# INCENTIVE AUTO-PIN. Every active PMUS liquidityProgram observed to date
# sits on college-football moneyline, and a program market only earns while
# its game is in play. Live discovery does surface such a market once the
# game starts, but it lands in the rotating surplus and therefore spends its
# first hour tripping the 60s gap rule -- losing exactly the window the
# reward pool is paid over. Pinning it the moment it appears live is what
# "target live events as they come" requires. Pure priority: no gate,
# threshold or admission changes, and breadth is untouched because pins are
# bounded by the same pin_max() as every other pin source.
INCENTIVE_AUTOPIN_ENV = "PMUS_INCENTIVE_AUTOPIN"     # "0" disables


def incentive_autopin_enabled() -> bool:
    return os.getenv(INCENTIVE_AUTOPIN_ENV, "1").strip().lower() not in (
        "0", "false", "no")


# TOP-PRIMARY AUTO-PIN. Ranking the moneyline first is necessary but not
# sufficient: with 64 seats over a ~476-market universe every seat still
# turns at roughly sweep cadence, so the MLB moneyline measured at ~200k
# shares/hour with a half-cent spread was polled about once a minute and
# kept tripping the 60s freshness rule. Flow that dense deserves the
# pinned lane. Bounded by the same pin_max() as every other source and
# restricted to IN-BAND primaries, because an out-of-band moneyline
# cannot pass the observation gate anyway.
TOP_PRIMARY_AUTOPIN_ENV = "PMUS_TOP_PRIMARY_AUTOPIN"   # "0" disables
TOP_PRIMARY_AUTOPIN_DEFAULT = 3


def top_primary_autopin_n() -> int:
    raw = os.getenv(TOP_PRIMARY_AUTOPIN_ENV,
                    str(TOP_PRIMARY_AUTOPIN_DEFAULT)).strip()
    if raw.lower() in ("0", "false", "no"):
        return 0
    try:
        return max(0, min(pin_max(), int(raw)))
    except ValueError:
        return TOP_PRIMARY_AUTOPIN_DEFAULT


def top_primary_pins(universe: Iterable[Mapping[str, Any]]) -> list[str]:
    """Highest-scoring IN-BAND primary markets from the live universe."""
    n = top_primary_autopin_n()
    if not n:
        return []
    cands = []
    for t in universe or ():
        if t.get("prop", False):
            continue
        meta = t.get("market_meta") or {}
        bid = _number(meta.get("bestBidQuote"))
        ask = _number(meta.get("bestAskQuote"))
        mid = (bid + ask) / 2.0 if (bid > 0 and ask > 0 and ask >= bid) else bid
        if not (0.35 <= mid <= 0.65):
            continue
        cands.append((-_market_score(t), str(t.get("slug"))))
    cands.sort()
    return [slug for _, slug in cands[:n]]


def priority_pins(program_slugs: Iterable[str] = (),
                  primary_slugs: Iterable[str] = ()) -> list[dict]:
    """Operator pins, live incentive-program markets, top in-band
    primaries, then the L5 candidate ranking.

    Operator intent outranks everything. Program markets come next
    because their earning window is bounded by the game clock. Top
    primaries come before the L5 ranking because the L5 ranking can only
    surface a market that already has enough observation to score, which
    is precisely what a market starved of cadence does not have.
    """
    out, seen = [], set()

    def add(slug, reason):
        if slug and slug not in seen:
            seen.add(slug)
            out.append({"slug": slug, "reason": reason})

    for slug in operator_pins():
        add(slug, "operator_pin")
    if incentive_autopin_enabled():
        for slug in program_slugs or ():
            add(slug, "live_incentive_program")
    for slug in primary_slugs or ():
        add(slug, "top_in_band_primary")
    for slug in _auto_pins:
        add(slug, "l5_candidate_auto_pin")
    return out[:pin_max()]


def update_auto_pins(report: Optional[Mapping[str, Any]]) -> list[str]:
    """Feed the priority set from the L5 sports decision ranking, which
    is already ordered fresh-Q1 first, then SHADOW_CANDIDATE."""
    global _auto_pins
    dec = (report or {}).get("l5_sports_decisions") or {}
    _auto_pins = [r["slug"] for r in (dec.get("rows") or [])
                  if r.get("slug")][:pin_max()]
    return _auto_pins


# Measured on the live board 2026-09-04: ONE MLB doubleheader contributed
# 349 of 473 live markets -- almost all player props (hits/total-bases/HR
# lines), most resting at a 0.01 bid. Those props also serve volume24hr
# and liquidity as NULL, so the volume terms below collapse to zero and
# ranking became effectively arbitrary: 29 of the 30 seats that event won
# went to dead props while its moneyline -- bid 0.4750, half-cent spread,
# ~200k shares/hour, squarely in the 35-65 band -- got exactly one.
#
# That is a selection defect, not a property of sports. A prop at a 0.01
# bid cannot pass the observation band and cannot carry L5 flow, so
# spending observation budget on it starves the markets that can. The
# structural signals (is this the primary market; is the served touch in
# band) therefore have to outrank absent volume metadata rather than sit
# below it. Selection only: no gate, threshold or admission changes, and
# the prop tail is still polled, just after the markets that can qualify.
PRIMARY_MARKET_BONUS = 40.0
IN_BAND_BONUS = 25.0
DEAD_TOUCH_PENALTY = 30.0       # bid <=0.10 or >=0.90: unreachable band


def _market_score(target: Mapping[str, Any]) -> float:
    """Cheap pre-poll L5-relevance rank from venue-served metadata only.

    Structure first (primary market, touch in the observation band), then
    volume/liquidity where the venue actually serves them. No missing
    field is invented and this score is selection-only, never admission.
    """
    meta = target.get("market_meta") or {}
    volume = max(_number(meta.get("volume24hr")), _number(meta.get("volume")))
    liquidity = _number(meta.get("liquidity"))
    score = math.log1p(max(volume, 0.0)) * 5.0 + math.log1p(max(liquidity, 0.0))

    # a moneyline outranks a prop even when the venue serves no volume
    if not target.get("prop", False):
        score += PRIMARY_MARKET_BONUS

    bid = _number(meta.get("bestBidQuote"))
    ask = _number(meta.get("bestAskQuote"))
    mid = (bid + ask) / 2.0 if (bid > 0 and ask > 0 and ask >= bid) else bid
    if mid > 0:
        if 0.35 <= mid <= 0.65:
            score += IN_BAND_BONUS
        elif mid <= 0.10 or mid >= 0.90:
            score -= DEAD_TOUCH_PENALTY
    if bid > 0 and ask > 0 and ask >= bid:
        score += max(0.0, 2.0 - 10.0 * (ask - bid))
    return score


def live_targets(live_events: Iterable[Mapping[str, Any]],
                 prefixes: Iterable[str] = ()) -> list[dict]:
    """Flatten the full authoritative live slate and retain event identity."""
    out: list[dict] = []
    for ev in live_events or []:
        event_slug = str(ev.get("slug") or ev.get("id") or "unknown-event")
        for row in col.live_event_targets([ev], prefixes):
            out.append({**row, "event_slug": event_slug})
    return out


def select_for_poll(targets: Iterable[Mapping[str, Any]], cap: int,
                    cycle: int = 0,
                    pins: Iterable[Any] = ()) -> tuple[list[dict], dict]:
    """Priority pins every cycle, then cover events, then rotate surplus.

    With ~50 live events and a cap of 64 this guarantees at least one
    market from every event before extra markets consume seats.  Remaining
    seats rotate across the ranked surplus on each discovery cycle so the
    full discovered universe is eventually sampled rather than silently
    excluded forever.  A small pinned set is exempt from rotation so it
    can hold sub-60s cadence and become gate-eligible.
    """
    cap = max(1, int(cap))
    groups: dict[str, list[dict]] = defaultdict(list)
    for raw in targets:
        row = dict(raw)
        key = str(row.get("event_slug") or row.get("league") or row.get("slug"))
        groups[key].append(row)
    for rows in groups.values():
        rows.sort(key=lambda r: (-_market_score(r), str(r.get("slug"))))

    event_order = sorted(
        groups,
        key=lambda k: (-_market_score(groups[k][0]), k),
    )
    selected: list[dict] = []
    chosen: set[str] = set()

    # Pass 0: the bounded priority set, every cycle, never rotated out.
    by_slug = {str(r.get("slug")): r
               for rows in groups.values() for r in rows}
    pin_rows, pin_absent = [], []
    for pin in list(pins)[:pin_max()]:
        slug = str(pin.get("slug") if isinstance(pin, Mapping) else pin)
        reason = (pin.get("reason") if isinstance(pin, Mapping)
                  else "operator_pin")
        row = by_slug.get(slug)
        if row is None:
            pin_absent.append(slug)      # not in the live universe now
            continue
        if slug in chosen or len(selected) >= cap:
            continue
        selected.append(row)
        chosen.add(slug)
        pin_rows.append({"slug": slug, "reason": reason})

    # First pass: one best market per event.
    for key in event_order:
        if len(selected) >= cap:
            break
        row = groups[key][0]
        slug = str(row.get("slug"))
        if slug and slug not in chosen:
            selected.append(row)
            chosen.add(slug)

    # Second pass: ranked surplus, rotated once the event-coverage floor is met.
    surplus = [r for key in event_order for r in groups[key][1:]
               if str(r.get("slug")) not in chosen]
    surplus.sort(key=lambda r: (-_market_score(r), str(r.get("slug"))))
    room = cap - len(selected)
    if room > 0 and surplus:
        start = (max(0, int(cycle)) * room) % len(surplus)
        rotated = (surplus + surplus)[start:start + room]
        for row in rotated:
            slug = str(row.get("slug"))
            if slug and slug not in chosen and len(selected) < cap:
                selected.append(row)
                chosen.add(slug)

    return selected, {
        "discovered_markets": sum(len(v) for v in groups.values()),
        "discovered_events": len(groups),
        "selected_markets": len(selected),
        "selected_events": len({str(r.get("event_slug") or r.get("league"))
                                for r in selected}),
        "rotation_cycle": int(cycle),
        "pinned_markets": pin_rows,
        "pinned_count": len(pin_rows),
        "pinned_absent_from_universe": pin_absent,
        "pin_cap": pin_max(),
        "selection": ("priority_pins_then_event_floor_then_rotating_l5_rank"
                      if pin_rows else
                      "event_floor_then_rotating_l5_rank"),
    }


def _legacy_targets(leagues: list[str], prefixes: list[str], incentives: dict,
                    include_inc: bool) -> tuple[list[dict], dict]:
    """Visible fallback only when the authoritative live surface is unavailable."""
    if leagues == ["all"]:
        try:
            raw = col.http_get_json("/v2/leagues")
            rows = raw if isinstance(raw, list) else raw.get("leagues") or []
            leagues = [str(r.get("slug")) for r in rows if r.get("slug")]
        except Exception:
            leagues = ["mlb"]
    events_by_league: dict[str, list] = {}
    errors: dict[str, str] = {}
    for league in leagues:
        try:
            raw = col.http_get_json(
                f"/v2/leagues/{league}/events?limit={col.EVENTS_PAGE_LIMIT}")
            events_by_league[league] = raw if isinstance(raw, list) \
                else raw.get("events") or []
        except Exception as exc:
            events_by_league[league] = []
            errors[league] = f"{type(exc).__name__}:{str(exc)[:80]}"
    rows = col.discover_targets(events_by_league, incentives, prefixes, include_inc)
    for row in rows:
        row["event_slug"] = str((row.get("market_meta") or {}).get("eventSlug")
                                or row.get("league") or row.get("slug"))
    return rows, {"fallback_leagues": leagues, "fallback_errors": errors}


def discover_poll_targets(incentives: dict, include_inc: bool, cap: int,
                          cycle: int = 0) -> tuple[list[dict], dict]:
    """Primary live-sports discovery with explicit, measured fallback."""
    # Empty by default: the full live sports market set (props included).
    prefixes = [x.strip() for x in os.getenv(col.PREFIXES_ENV, "").split(",")
                if x.strip()]
    leagues = [x.strip() for x in
               os.getenv(col.LEAGUES_ENV, "all").split(",") if x.strip()]

    program_slugs = set(col.active_incentive_slugs(incentives or {}))

    live_events, live_diag = col.fetch_live_events(col.http_get_json)
    if live_events and live_diag.get("v1_live_error") is None:
        universe = live_targets(live_events, prefixes)
        # Incentive-only slugs are still NOT appended to the poll set: a
        # program market whose game has not started is a closed book and
        # polling it densely buys nothing. What DOES matter is that the
        # moment such a market appears in the live universe it is pinned,
        # so it holds sub-60s cadence across the window its pool is paid
        # over instead of rotating for its first hour.
        live_programs = sorted(
            {str(t.get("slug")) for t in universe
             if str(t.get("slug")) in program_slugs})
        primaries = top_primary_pins(universe)
        selected, selection_diag = select_for_poll(
            universe, cap, cycle,
            pins=priority_pins(live_programs, primaries))
        return selected, {
            **live_diag,
            **selection_diag,
            "source_mode": "v1_live_sports_authoritative",
            "incentive_only_admitted": 0,
            "top_in_band_primaries": primaries,
            "incentive_programs_known": len(program_slugs),
            "incentive_programs_live": live_programs,
            "incentive_autopin": incentive_autopin_enabled(),
        }

    fallback, fallback_diag = _legacy_targets(
        leagues, prefixes, incentives, include_inc)
    live_programs = sorted(
        {str(t.get("slug")) for t in fallback
         if str(t.get("slug")) in program_slugs})
    selected, selection_diag = select_for_poll(
        fallback, cap, cycle, pins=priority_pins(live_programs))
    return selected, {
        **live_diag,
        **fallback_diag,
        **selection_diag,
        "source_mode": "legacy_visible_fallback",
        "incentive_only_admitted": sum(
            1 for r in selected if r.get("why") == "active_liquidity_program"),
        "incentive_programs_known": len(program_slugs),
        "incentive_programs_live": live_programs,
        "incentive_autopin": incentive_autopin_enabled(),
    }


def run(minutes: Optional[float] = None) -> int:
    col.assert_service_role()
    root = col.root_dir()
    include_inc = os.getenv(col.INCENTIVE_MARKETS_ENV, "1").strip().lower() not in \
        {"0", "false", "no", "off"}
    poll_s = float(os.getenv(col.POLL_ENV, str(col.DEFAULT_POLL_S)))
    budget = float(os.getenv(col.BUDGET_ENV, str(col.DEFAULT_BUDGET_RPS)))
    # default >=64; an explicit env stays the operator's rate-safety knob
    max_markets = int(os.getenv(col.MAX_MARKETS_ENV,
                                str(DEFAULT_LIVE_MAX_MARKETS)))
    discovery_s = float(os.getenv(col.DISCOVERY_ENV, str(col.DEFAULT_DISCOVERY_S)))
    inc_snap_s = float(os.getenv(col.INCENTIVES_SNAP_ENV,
                                 str(col.DEFAULT_INCENTIVES_SNAP_S)))

    state = col.load_state(root)
    state["restarts"] = int(state.get("restarts", 0)) + 1
    col.save_state(root, state)
    col.append_rows(root, [{
        "kind": "collector_start", "t": col._utcnow().isoformat(),
        "source": col.SOURCE, "read_only": True,
        "discovery_mode": "v1_live_sports_authoritative",
        "poll_s": poll_s, "budget_rps": budget, "max_markets": max_markets,
    }])

    deadline = time.monotonic() + minutes * 60 if minutes else None
    targets: list[dict] = []
    incentives: dict = {}
    discovery_diag: dict = {}
    last_discovery = last_inc_snap = 0.0
    discovery_cycle = 0

    while deadline is None or time.monotonic() < deadline:
        now_mono = time.monotonic()
        if now_mono - last_inc_snap >= inc_snap_s or not incentives:
            try:
                incentives = col.http_get_json("/v1/incentives")
                col.append_rows(root, [{
                    "kind": "incentives_snapshot", "t": col._utcnow().isoformat(),
                    "active_market_slugs": col.active_incentive_slugs(incentives),
                    "programs": incentives.get("programs"), "source": col.SOURCE,
                }])
            except Exception as exc:
                col.append_rows(root, [{"kind": "error", "stage": "incentives",
                                       "t": col._utcnow().isoformat(),
                                       "err": str(exc)[:120]}])
            last_inc_snap = now_mono

        if now_mono - last_discovery >= discovery_s or not targets:
            try:
                targets, discovery_diag = discover_poll_targets(
                    incentives, include_inc, max_markets, discovery_cycle)
                discovery_cycle += 1
                plan = col.plan_cycle(len(targets), poll_s, budget, max_markets)
                col.append_rows(root, [{
                    "kind": "discovery", "t": col._utcnow().isoformat(),
                    "targets": [{"slug": t.get("slug"),
                                 "league": t.get("league"),
                                 "event_slug": t.get("event_slug"),
                                 "why": t.get("why")}
                                for t in targets],
                    "plan": plan, "diagnostics": discovery_diag,
                    "source": col.SOURCE,
                }])
            except Exception as exc:
                discovery_diag = {"source_mode": "discovery_error",
                                  "error": f"{type(exc).__name__}:{str(exc)[:100]}"}
                col.append_rows(root, [{"kind": "error", "stage": "discovery",
                                       "t": col._utcnow().isoformat(),
                                       "err": str(exc)[:120]}])
            last_discovery = now_mono

        # The interleave must see the SAME pin set the selection used, or a
        # program market is seated every cycle yet polled only once per
        # sweep -- ~60s cadence, which trips the very gap rule the pin
        # exists to clear. discovery_diag carries the live program slugs
        # and persists between discovery cycles, exactly as `targets` does.
        program_live = list(
            (discovery_diag or {}).get("incentive_programs_live") or [])
        primary_live = list(
            (discovery_diag or {}).get("top_in_band_primaries") or [])
        pin_slugs = [p["slug"]
                     for p in priority_pins(program_live, primary_live)]
        order = interleaved_order(targets, pin_slugs)
        # rate plan computed on the INTERLEAVED length so pin re-polls
        # are paid for out of the same budget, never on top of it
        plan = col.plan_cycle(len(order), poll_s, budget,
                              max(max_markets, len(order)))
        rows = []
        for target in order:
            slug = target["slug"]
            try:
                bbo = col.http_get_json(f"/v1/markets/{slug}/bbo")["marketData"]
                book = col.http_get_json(f"/v1/markets/{slug}/book")["marketData"]
                rows.append(col.bbo_book_row(slug, target, bbo, book))
            except Exception as exc:
                rows.append({"kind": "error", "stage": "poll", "slug": slug,
                             "t": col._utcnow().isoformat(),
                             "err": str(exc)[:120]})
        if rows:
            col.append_rows(root, rows)

        state["cycles"] = int(state.get("cycles", 0)) + 1
        state["rows_written"] = int(state.get("rows_written", 0)) + len(rows)
        col.save_state(root, state)
        col.heartbeat(root, markets=len(targets), cycles=state["cycles"],
                      rows_written=state["rows_written"],
                      effective_poll_s=plan["effective_poll_s"],
                      discovery=discovery_diag)

        status_every = float(os.getenv(col.STATUS_EVERY_ENV,
                                       str(col.DEFAULT_STATUS_EVERY_S)))
        if status_every > 0 and \
                now_mono - getattr(run, "_last_status", 0.0) >= status_every:
            try:
                from src import pmus_status_report
                # the L5 decision ranking inside the report feeds the
                # bounded priority set for the NEXT discovery cycle
                update_auto_pins(pmus_status_report.emit(root))
            except Exception as exc:
                col.append_rows(root, [{"kind": "error", "stage": "status",
                                       "t": col._utcnow().isoformat(),
                                       "err": str(exc)[:120]}])
            run._last_status = now_mono

        time.sleep(plan["effective_poll_s"] if targets else max(poll_s, 20.0))
    return 0


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--minutes", type=float, default=None)
    args = ap.parse_args()
    return run(args.minutes)


if __name__ == "__main__":
    raise SystemExit(main())
