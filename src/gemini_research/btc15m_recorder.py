"""BTC15m TWO-SIDED RESEARCH RECORDER. Read-only, unauthenticated.

NO ORDER PATH EXISTS IN THIS FILE. There is no credential, no signer, no
mutation endpoint, and nothing imported from src.gemini_execution. It reads
public market data. The workflow proves that over the AST before it runs.

WHY THIS FILE EXISTS. BTC15m is the only alternative habitat whose failures
are EVIDENTIARY rather than structural, and the one file that disqualified it,
data/transfer_backtest/gemini_bbo_btc15m.jsonl.gz, is UP-LEG ONLY: 23,318 of
23,318 rows carry a -UP symbol and ZERO carry -DOWN (verified 2026-10-06, span
2026-08-30T23:18:46Z -> 2026-09-02T13:39:34Z). gemini_bbo_btc05m.jsonl.gz has
the same shape (61,497 of 61,497 UP). So the complement leg was never
recorded, and every pair-cost conclusion drawn from those files rests on one
leg.

WHAT "BOTH LEGS" MEANS HERE, STATED HONESTLY. On every tape this programme has
taken, the venue lists exactly ONE instrument per window and it is the -UP
instrument (121,492 of 121,492 market-bearing rows in analysis/gemini/btc5m_rec
on 2026-10-05). Buying DOWN is therefore selling UP, and the DOWN book is a
DERIVATION, not a separate feed:

    DOWN ask_i = 1 - UP bid_i   (carrying the UP bid's size)
    DOWN bid_i = 1 - UP ask_i   (carrying the UP ask's size)

This recorder does three distinct things about that, and never conflates them:

  1. It records the RAW UP ladder in full, price and size, every level, so no
     downstream reader has to trust any derivation of ours.
  2. It records the DERIVED DOWN ladder beside it, labelled `derived: true`
     with the formula in the row, in `pair_snapshot` rows that also carry the
     fee-safe pair cost (UP ask + DOWN ask == 1 - UP bid + UP ask, i.e. the
     single-leg spread plus one dollar) so the two-leg and one-leg quantities
     can never be mistaken for each other again.
  3. It PROBES for a real separate DOWN instrument rather than assuming there
     is none: once per window it subscribes the hypothetical `-DOWN` symbol
     and persists the venue's ack or error verbatim as a `down_probe` row. If
     a genuine second instrument exists, this is how we find out; if it does
     not, the venue's own refusal is the evidence that the complement can only
     be derived. Either answer is recorded, neither is assumed.

HISTORICAL LESSONS THIS FILE AND ITS WORKFLOW HONOUR.
  * NO PERIODIC FLUSH killed a tape once: a 9-hour run's evidence was only
    gzipped and committed at job end, so a cancellation would have destroyed
    all of it. This module therefore runs in SEGMENTS (--segments N
    --duration-s S), closing and finalising a separate stamped tape after each
    segment, so the workflow can commit after every one and a cancellation
    costs at most one segment.
  * A `KeyError` IN AN INFORMATIONAL ECHO once failed a PASSING gate. Every
    lookup in this module's reporting path uses .get(); the real gate is the
    `pass` boolean and lives in its own workflow step.
  * A MISSING python-dotenv once silently refused 12 live windows. `--preflight`
    imports every module the job needs and exits non-zero if any is absent, and
    the workflow runs it as its own step before anything long starts.
  * `kind == "window"` ROWS CARRY `strike: null` before availableAt; the
    authoritative strike arrives on `window_poll` re-reads. meta_task emits
    those re-reads and the validator REQUIRES a window_poll row with a
    non-null strike, so a tape that only ever saw the null first listing
    cannot pass.

Accounting label: public market-data recording. No order was sent. Nothing
produced by this file is realized profit for our account.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from src.gemini_research.btc5m_recorder import (
    GEMINI_WS,
    Book,
    Sink,
    book_task,
    derived_no_ladder,
    index_task,
    meta_task,
    now_ns,
    proxy_url,
    settlement_task,
    ssl_ctx,
)

LABEL = "GEMINI_BTC15M_TWO_SIDED_RESEARCH_RECORDER_V1"
SERIES_REGEX_DEFAULT = r"^BTC15M\d{10}$"
PAIR_TICK_S = 1.0

#: WINDOW LENGTH of this series, seconds. A BTC15m contract is 900 s long.
WINDOW_S = 900.0

#: HOW LONG BEFORE EXPIRY THIS RECORDER OPENS A MARKET'S STREAMS.
#:
#: This module used to inherit `btc5m_recorder.SUBSCRIBE_LEAD_S = 420.0`, a
#: value sized for a 300-second window. On a 900-second window that opened the
#: streams at TTE 420 and left TTE 900 -> 420 unrecorded. The 2026-10-06 tape
#: proves it: across 15,149 book events and 51 venue prints, the maximum
#: observed TTE was 419.9 s and NOTHING existed above the wall, so 58% of the
#: continuous-inventory policy's quoting band [120, 840] was absent -- and the
#: only two-sided maker completion this programme has obtained came from
#: windows entered at TTE 865-879, inside the gap.
#:
#: 960 s is the window plus a 60-second margin, so a market's streams are open
#: before its window begins. See
#: `analysis/l5/btc15m_execution_audit/REPORT.md`.
SUBSCRIBE_LEAD_S = 960.0

#: A SEGMENT MUST OBSERVE A STREAMED ROW AT OR ABOVE THIS TTE OR IT FAILS.
#: Set to the top of the policy's quoting band. The validator turns this into
#: a REQUIRED check, so a lead regression fails the Stage 1 gate instead of
#: producing a confident fill statistic over the late window only.
COVERAGE_MIN_TTE_S = 840.0
#: modules the job genuinely needs at runtime; a missing one must fail closed
#: rather than silently record nothing (see the dotenv lesson above).
PREFLIGHT_MODULES = ("httpx", "websockets", "src.gemini_research.btc5m_recorder",
                     "src.gemini_research.btc15m_recorder")

COMPLEMENT_FORMULA = ("DOWN ask = 1 - UP bid (UP bid size); "
                      "DOWN bid = 1 - UP ask (UP ask size)")


def _top(rows: List[List[str]]) -> Optional[List[str]]:
    return list(rows[0]) if rows else None


def _f(x: Any) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def pair_row(market: str, bk: Book, meta: Dict[str, Any],
             t: int) -> Dict[str, Any]:
    """One per-instant two-sided record: raw UP, derived DOWN, pair cost."""
    yes = bk.ladder()
    no = derived_no_ladder(yes)
    ub, ua = _top(yes["bids"]), _top(yes["asks"])
    ubp, uap = (_f(ub[0]) if ub else None), (_f(ua[0]) if ua else None)
    spread = (uap - ubp) if (ubp is not None and uap is not None) else None
    return {
        "t_ns": t, "kind": "pair_snapshot", "market": market,
        "duration": meta.get("duration"),
        "ticker": meta.get("ticker"),
        "strike": meta.get("strike"),
        "strike_source": meta.get("strike_source"),
        "availableAt": meta.get("availableAt"),
        "t0": meta.get("t0"), "t0_source": meta.get("t0_source"),
        "expiry_t": meta.get("expiry_t"),
        "tte_s": (meta["expiry_t"] - time.time()
                  if meta.get("expiry_t") else None),
        "age_s": (time.time() - meta["t0"] if meta.get("t0") else None),
        "book_state": bk.state(),
        "book_integrity": "GAP" if bk.resync_pending else "OK",
        "gaps": bk.gaps,
        "last_update_id": bk.last_update_id,
        # venue timestamps, where the venue's own fields permit them
        "venue_event_ns": bk.last_event_ns,
        "recv_ns": bk.last_recv_ns,
        "quote_age_s": ((t - bk.last_recv_ns) / 1e9
                        if bk.last_recv_ns else None),
        # leg 1: RAW, unmodified
        "up_ladder_raw": yes,
        "up_bid": ub, "up_ask": ua,
        "up_depth_top3_bids": yes["bids"][:3],
        "up_depth_top3_asks": yes["asks"][:3],
        # leg 2: DERIVED, and it says so
        "down_ladder_derived": no,
        "down_bid": _top(no["bids"]), "down_ask": _top(no["asks"]),
        "derived": True,
        "derivation": COMPLEMENT_FORMULA,
        # the two quantities that must never be confused
        "single_leg_spread": spread,
        "pair_cost_up_ask_plus_down_ask": (
            (uap + (1.0 - ubp)) if (ubp is not None and uap is not None)
            else None),
        "pair_cost_excess_over_1": spread,
        "units": {"single_leg_spread": "$ ask-bid on the UP instrument",
                  "pair_cost_excess_over_1": "$ over 1.00, two legs"},
        "no_order_path": True,
    }


async def pair_task(sink: Sink, stop: asyncio.Event,
                    known: Dict[str, Dict[str, Any]],
                    books: Dict[str, Book]) -> None:
    """Emit a two-sided pair_snapshot per live window on a 1 Hz tick."""
    while not stop.is_set():
        t = now_ns()
        for sym, meta in list(known.items()):
            bk = books.get(sym)
            if bk is None:
                continue
            exp = meta.get("expiry_t") or 0
            if not (exp - 1200.0 <= time.time() <= exp + 120.0):
                continue
            try:
                sink.write(pair_row(sym, bk, meta, t))
            except Exception as exc:                           # noqa: BLE001
                sink.write({"t_ns": now_ns(), "kind": "error",
                            "where": "pair", "market": sym,
                            "error": type(exc).__name__,
                            "detail": str(exc)[:200]})
        try:
            await asyncio.wait_for(stop.wait(), timeout=PAIR_TICK_S)
        except asyncio.TimeoutError:
            pass


async def down_probe_task(sink: Sink, stop: asyncio.Event,
                          known: Dict[str, Dict[str, Any]]) -> None:
    """Ask the venue whether a separate -DOWN instrument exists.

    Read-only: a SUBSCRIBE frame on a public market-data socket. The ack or
    error is persisted verbatim, so the answer is the venue's, not ours.
    """
    import websockets
    probed: Set[str] = set()
    while not stop.is_set():
        targets = [s for s in list(known) if s.endswith("-UP")
                   and s not in probed][:4]
        if targets:
            for s in targets:
                probed.add(s)
            down = [s[:-3] + "-DOWN" for s in targets]
            try:
                async with websockets.connect(
                        GEMINI_WS, proxy=proxy_url(), ssl=ssl_ctx(),
                        open_timeout=15, ping_interval=15,
                        ping_timeout=10) as ws:
                    params: List[str] = []
                    for d in down:
                        params += ["%s@depth20" % d, "%s@trade" % d]
                    await ws.send(json.dumps({"id": "dn",
                                              "method": "SUBSCRIBE",
                                              "params": params}))
                    sink.write({"t_ns": now_ns(), "kind": "down_probe",
                                "phase": "sent", "up_markets": targets,
                                "down_markets": down, "params": params})
                    end = time.monotonic() + 8.0
                    while time.monotonic() < end and not stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(),
                                                         timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        try:
                            m = json.loads(raw)
                        except Exception:                      # noqa: BLE001
                            m = {"unparsed": str(raw)[:400]}
                        sink.write({"t_ns": now_ns(), "kind": "down_probe",
                                    "phase": "reply", "down_markets": down,
                                    "raw": m})
            except Exception as exc:                           # noqa: BLE001
                sink.write({"t_ns": now_ns(), "kind": "down_probe",
                            "phase": "transport_error",
                            "down_markets": down,
                            "error": type(exc).__name__,
                            "detail": str(exc)[:200]})
        try:
            await asyncio.wait_for(stop.wait(), timeout=120.0)
        except asyncio.TimeoutError:
            pass


async def strike_task(sink: Sink, stop: asyncio.Event,
                      known: Dict[str, Dict[str, Any]]) -> None:
    """Resolve the AUTHORITATIVE strike and fold it onto `known`.

    `kind == "window"` rows carry `strike: null` before availableAt; the real
    strike only appears on a later re-read. meta_task emits those re-reads as
    `window_poll`, which is the archival authority, but a pair_snapshot is far
    more useful with the strike inline -- so this polls the same PUBLIC
    listing, folds the first NON-NULL strike onto the per-market metadata, and
    writes a `strike_resolved` row recording the null -> value transition with
    the lag, so the upgrade is auditable rather than silent.
    """
    import httpx
    from src.gemini_research.btc5m_recorder import EVENTS, CA, parse_ts
    resolved: Set[str] = set()
    while not stop.is_set():
        try:
            async with httpx.AsyncClient(timeout=25.0, proxy=proxy_url(),
                                         verify=CA) as http:
                r = await http.get(EVENTS, params=[("status", "active"),
                                                   ("category", "Crypto"),
                                                   ("limit", "500")])
                data = r.json().get("data", [])
            for ev in data:
                for c in (ev.get("contracts") or []):
                    sym = (c.get("instrumentSymbol") or "").upper()
                    if not sym or "BTC15M" not in sym or sym in resolved:
                        continue
                    strike = c.get("strike") or {}
                    val = strike.get("value")
                    if val is None:
                        continue
                    av = parse_ts(strike.get("availableAt"))
                    meta = known.get(sym)
                    if isinstance(meta, dict):
                        meta["strike"] = val
                        meta["strike_source"] = "window_poll_non_null"
                        # ONLY NOW is this market done. The first poll can win
                        # the race against meta_task's own listing read, so
                        # `known` may not hold the market yet; marking it
                        # resolved then would strand strike:null on every
                        # later pair_snapshot, which is exactly what the first
                        # live tape showed.
                        resolved.add(sym)
                    sink.write({
                        "t_ns": now_ns(), "kind": "strike_resolved",
                        "market": sym, "ticker": ev.get("ticker"),
                        "strike": val,
                        "strike_type": strike.get("type"),
                        "availableAt": strike.get("availableAt"),
                        "resolved_lag_s": ((time.time() - av)
                                           if av else None),
                        "note": ("window rows carry strike:null before "
                                 "availableAt; this is the re-read value"),
                    })
        except Exception as exc:                               # noqa: BLE001
            sink.write({"t_ns": now_ns(), "kind": "error",
                        "where": "strike", "error": type(exc).__name__,
                        "detail": str(exc)[:200]})
        try:
            await asyncio.wait_for(stop.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            pass


# --------------------------------------------------------------- validation
REQUIRED = {
    "btc15m_window_seen": "at least one BTC15M window listed",
    "up_book_two_sided": "a pair_snapshot whose book_state is TWO_SIDED",
    "up_ladder_has_sizes": "a pair_snapshot with a priced level AND a size "
                           "on both UP sides",
    "down_leg_derived_present": "a pair_snapshot carrying a derived DOWN "
                                "bid and ask -- the complement leg the "
                                "disqualifying file never had",
    "pair_cost_present": "a pair_snapshot with a numeric two-leg pair cost",
    "single_leg_spread_present": "a pair_snapshot with a numeric single-leg "
                                 "spread, recorded separately from pair cost",
    "trade_prints_present": "at least one p_trade row (venue print tape)",
    "venue_timestamps": "a p_depth row carrying a venue E timestamp",
    "window_poll_strike_resolved": "a window_poll (or strike_resolved) row "
                                   "with a NON-NULL strike -- the "
                                   "authoritative strike that arrives after "
                                   "availableAt, not the null first listing",
    "down_instrument_probed": "a down_probe row: the venue was asked whether "
                              "a separate -DOWN instrument exists",
    "subscribe_lead_verified": "a SUBSCRIBE row whose recorded per-market "
                               "tte_s reaches COVERAGE_MIN_TTE_S -- the lead "
                               "actually in force, read off the tape, not "
                               "assumed from a constant",
    "early_window_streamed": "a STREAMED row (p_snapshot / p_depth / "
                             "p_trade) at or above COVERAGE_MIN_TTE_S -- "
                             "proof the opening portion of a window was "
                             "received, not merely subscribed to",
}


def validate(path: Path) -> Dict[str, Any]:
    got = {k: False for k in REQUIRED}
    kinds: Dict[str, int] = {}
    states: Dict[str, int] = {}
    markets: Set[str] = set()
    spreads: List[float] = []
    examples: Dict[str, Any] = {}
    probe_replies = 0
    #: COVERAGE. Two independent readings, both off the tape:
    #:   subscribe rows  -- the lead the recorder actually applied
    #:   streamed rows   -- the earliest window moment data was received for
    expiry_t: Dict[str, float] = {}
    max_sub_tte: Optional[float] = None
    leads: Set[float] = set()
    stream_max_tte: Dict[str, float] = {}
    deferred: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except Exception:                                  # noqa: BLE001
                continue
            k = str(r.get("kind"))
            kinds[k] = kinds.get(k, 0) + 1
            if k == "subscribe":
                if r.get("subscribe_lead_s") is not None:
                    leads.add(float(r["subscribe_lead_s"]))
                for _m, _t in (r.get("tte_s") or {}).items():
                    try:
                        _tf = float(_t)
                    except (TypeError, ValueError):
                        continue
                    if max_sub_tte is None or _tf > max_sub_tte:
                        max_sub_tte = _tf
                    examples.setdefault("subscribe_tte", {
                        "market": _m, "tte_s": _tf,
                        "subscribe_lead_s": r.get("subscribe_lead_s")})
            elif k in ("p_snapshot", "p_depth", "p_trade"):
                #: expiry_t is learned from pair_snapshot rows, which may
                #: arrive after the first streamed row, so streamed rows are
                #: resolved in a second pass rather than dropped.
                deferred.append({"market": str(r.get("market")),
                                 "t": (r.get("t_ns") or 0) / 1e9})
            if k == "pair_snapshot" and r.get("expiry_t") is not None:
                try:
                    expiry_t[str(r.get("market"))] = float(r["expiry_t"])
                except (TypeError, ValueError):
                    pass
            if k in ("window", "window_poll"):
                if str(r.get("duration") or "").upper() == "BTC15M":
                    got["btc15m_window_seen"] = True
                if k == "window_poll" and r.get("strike") is not None:
                    got["window_poll_strike_resolved"] = True
                    examples.setdefault("window_poll_strike", {
                        "market": r.get("market"), "strike": r.get("strike"),
                        "availableAt": r.get("availableAt")})
            elif k == "strike_resolved" and r.get("strike") is not None:
                got["window_poll_strike_resolved"] = True
                examples.setdefault("strike_resolved", r)
            elif k == "p_trade":
                got["trade_prints_present"] = True
                examples.setdefault("p_trade", r)
            elif k == "p_depth" and r.get("E_ns") is not None:
                got["venue_timestamps"] = True
            elif k == "down_probe":
                got["down_instrument_probed"] = True
                if r.get("phase") == "reply":
                    probe_replies += 1
                    examples.setdefault("down_probe_reply", r)
            elif k == "pair_snapshot":
                markets.add(str(r.get("market")))
                st = str(r.get("book_state"))
                states[st] = states.get(st, 0) + 1
                if st == "TWO_SIDED":
                    got["up_book_two_sided"] = True
                ub, ua = r.get("up_bid"), r.get("up_ask")
                if (ub and ua and len(ub) >= 2 and len(ua) >= 2
                        and ub[1] not in (None, "") and ua[1] not in (None, "")):
                    got["up_ladder_has_sizes"] = True
                if r.get("down_bid") and r.get("down_ask"):
                    got["down_leg_derived_present"] = True
                    examples.setdefault("two_sided_pair", {
                        "market": r.get("market"),
                        "up_bid": ub, "up_ask": ua,
                        "down_bid": r.get("down_bid"),
                        "down_ask": r.get("down_ask"),
                        "single_leg_spread": r.get("single_leg_spread"),
                        "pair_cost": r.get(
                            "pair_cost_up_ask_plus_down_ask"),
                        "derived": r.get("derived")})
                if r.get("pair_cost_up_ask_plus_down_ask") is not None:
                    got["pair_cost_present"] = True
                s = r.get("single_leg_spread")
                if isinstance(s, (int, float)):
                    got["single_leg_spread_present"] = True
                    spreads.append(float(s))
    for d in deferred:
        E = expiry_t.get(d["market"])
        if E is None or not d["t"]:
            continue
        tte = E - d["t"]
        if tte > stream_max_tte.get(d["market"], float("-inf")):
            stream_max_tte[d["market"]] = tte
    max_stream_tte = max(stream_max_tte.values()) if stream_max_tte else None

    got["subscribe_lead_verified"] = (max_sub_tte is not None
                                      and max_sub_tte >= COVERAGE_MIN_TTE_S)
    got["early_window_streamed"] = (max_stream_tte is not None
                                    and max_stream_tte >= COVERAGE_MIN_TTE_S)

    band_lo, band_hi = 120.0, COVERAGE_MIN_TTE_S
    observed_hi = (min(max_stream_tte, band_hi)
                   if max_stream_tte is not None else band_lo)
    band_fraction = round(max(0.0, observed_hi - band_lo)
                          / (band_hi - band_lo), 4)
    coverage = {
        "window_s": WINDOW_S,
        "subscribe_lead_s_configured": SUBSCRIBE_LEAD_S,
        "subscribe_lead_s_observed_on_tape": sorted(leads) or None,
        "coverage_min_tte_s": COVERAGE_MIN_TTE_S,
        "max_subscribe_tte_s": (None if max_sub_tte is None
                                else round(max_sub_tte, 1)),
        "max_streamed_tte_s": (None if max_stream_tte is None
                               else round(max_stream_tte, 1)),
        "per_market_max_streamed_tte_s": {
            m: round(v, 1) for m, v in sorted(stream_max_tte.items())},
        "markets_streamed_from_above_min_tte": sum(
            1 for v in stream_max_tte.values() if v >= COVERAGE_MIN_TTE_S),
        "markets_streamed": len(stream_max_tte),
        "policy_band_tte": [band_lo, band_hi],
        "policy_band_fraction_observed": band_fraction,
        "reading": ("A segment that starts mid-window legitimately shows a "
                    "low max TTE for the market already in progress, so the "
                    "gate asks for AT LEAST ONE market streamed from above "
                    "coverage_min_tte_s. per_market_max_streamed_tte_s is "
                    "published so a partially covered window can be excluded "
                    "downstream instead of silently averaged in."),
    }
    spreads.sort()
    return {
        "path": str(path), "rows": sum(kinds.values()), "kinds": kinds,
        "checks": got,
        "coverage": coverage,
        "missing": sorted(k for k, v in got.items() if not v),
        "pass": all(got.values()),
        "requirements": REQUIRED,
        "pair_markets": sorted(markets),
        "pair_market_count": len(markets),
        "book_states": states,
        "down_probe_replies": probe_replies,
        "single_leg_spread_n": len(spreads),
        "single_leg_spread_median": (spreads[len(spreads) // 2]
                                     if spreads else None),
        "examples": examples,
        "accounting": "public market-data recording; no order was sent; "
                      "nothing here is realized profit",
        "reading": ("down_leg_derived_present is the point of this recorder: "
                    "the disqualifying BTC15M file had 23,318 UP rows and 0 "
                    "DOWN rows, so no complement leg existed at all. A DOWN "
                    "leg here is DERIVED from the raw UP ladder and labelled "
                    "as such; down_probe records what the venue says about a "
                    "separate DOWN instrument."),
    }


# ------------------------------------------------------------------ driver
async def run_segment(args, seg: int) -> Dict[str, Any]:
    series_re = re.compile(args.series_regex)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sink = Sink(Path(args.outdir) / ("btc15m_rec_%s.jsonl" % stamp))
    sink.write({"t_ns": now_ns(), "kind": "recorder_start", "label": LABEL,
                "series_regex": args.series_regex, "segment": seg,
                "segments": args.segments, "duration_s": args.duration_s,
                "primary": "BTC15M", "captures": ["up_book_raw",
                                                  "down_ladder_derived",
                                                  "trade_prints",
                                                  "window_poll_strike",
                                                  "strike_resolved",
                                                  "down_instrument_probe",
                                                  "index_reference"],
                "no_order_path": True})
    known: Dict[str, Dict[str, Any]] = {}
    books: Dict[str, Book] = {}
    latest: Dict[str, Dict[str, Any]] = {}
    stop = asyncio.Event()
    tasks = [asyncio.create_task(meta_task(sink, stop, known, series_re)),
             asyncio.create_task(settlement_task(sink, stop, series_re)),
             asyncio.create_task(index_task(sink, stop, known, latest)),
             asyncio.create_task(book_task(sink, stop, known, books,
                                           subscribe_lead_s=SUBSCRIBE_LEAD_S)),
             asyncio.create_task(pair_task(sink, stop, known, books)),
             asyncio.create_task(strike_task(sink, stop, known)),
             asyncio.create_task(down_probe_task(sink, stop, known))]
    try:
        await asyncio.wait_for(stop.wait(), timeout=max(args.duration_s, 1.0))
    except asyncio.TimeoutError:
        pass
    stop.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    sink.write({"t_ns": now_ns(), "kind": "recorder_stop", "segment": seg,
                "rows": sink.n, "kinds": sink.kinds})
    sink.close()
    v = validate(sink.path)
    if args.validate_out:
        out = Path(args.validate_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(v, indent=2, sort_keys=True, default=str)
                       + "\n", encoding="utf-8")
    print(json.dumps({"segment": seg, "path": str(sink.path),
                      "rows": sink.n,
                      "validation_pass": v.get("pass"),
                      "missing": v.get("missing")}, sort_keys=True))
    return v


async def main_async(args) -> int:
    worst = 0
    for seg in range(1, max(args.segments, 1) + 1):
        v = await run_segment(args, seg)
        if not v.get("pass"):
            worst = 2
    return worst


def preflight() -> int:
    import importlib
    bad = []
    for m in PREFLIGHT_MODULES:
        try:
            importlib.import_module(m)
        except Exception as exc:                               # noqa: BLE001
            bad.append("%s: %s" % (m, type(exc).__name__))
    print(json.dumps({"preflight_modules": list(PREFLIGHT_MODULES),
                      "failed": bad, "pass": not bad}, sort_keys=True))
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="data/gemini_btc15m_research")
    ap.add_argument("--series-regex", default=SERIES_REGEX_DEFAULT)
    ap.add_argument("--duration-s", type=float, default=1200.0)
    ap.add_argument("--segments", type=int, default=1,
                    help="run this many back-to-back segments, each its own "
                         "finalised tape, so a cancellation costs one segment")
    ap.add_argument("--validate-out", default=None)
    ap.add_argument("--validate-only", default=None)
    ap.add_argument("--preflight", action="store_true",
                    help="import every module the job needs; fail closed")
    a = ap.parse_args(argv)
    if a.preflight:
        return preflight()
    if a.validate_only:
        v = validate(Path(a.validate_only))
        print(json.dumps(v, indent=2, sort_keys=True, default=str))
        return 0 if v.get("pass") else 2
    return asyncio.run(main_async(a))


if __name__ == "__main__":
    raise SystemExit(main())
