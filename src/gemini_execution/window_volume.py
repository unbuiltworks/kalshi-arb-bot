"""WINDOW VOLUME, from the venue's own print tape. Executed, never displayed.

THE SEPARATION THIS MODULE EXISTS TO ENFORCE: resting liquidity and executed
volume are different things and are never mixed here. Displayed touch depth
appears in its own column and contributes to no volume figure. CONTINUOUS-8
is why: it joined a bid showing 483 contracts, and by the next cycle that
level showed 0.01 -- ours, alone. Displayed size predicted nothing.

WHAT THE TAPE ACTUALLY GIVES US, checked rather than assumed:

    GET /v1/trades/{symbol}   PUBLIC, unauthenticated
    {timestamp, timestampms, tid, price, amount, exchange, type}

`amount` IS the print size, so contracts traded and dollar notional are both
measured, not inferred. And the tape SERVES EXPIRED CONTRACTS -- C8B's
contract still returned its complete six-print life ten hours after it
settled. So every window this programme has captured can be read in full,
including the live windows that recorded no tape at the time.

WHY THAT MATTERS MORE THAN IT SOUNDS. The shadow soak reads the tape once per
cycle and runs 22 cycles at 8 seconds from TTE ~280, so it stops observing
around TTE ~104. Anything traded in the last hundred seconds of a contract's
life was never in the soak's numbers. Fetching the whole tape afterwards is
the difference between "this market is dead" and "we stopped watching before
it woke up".

SIDE MAPPING, stated because it is easy to get wrong. The venue publishes ONE
tape per contract, the YES contract's. `type: buy` is an aggressor buying YES;
`type: sell` is an aggressor selling YES, which is economically NO demand.
There is no separate NO tape and none is invented here.
"""
from __future__ import annotations

import json as _json
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

TRADES_PATH = "/v1/trades"

#: the SETTLEMENT cadence: one contract per five minutes.
SLOT_S = 300.0

#: BUT NOT THE CONTRACT'S LIFE. Measured 2026-09-19 across 19 captured
#: windows, the earliest print on a BTC5m tape sits at TTE 378.2 s -- the
#: contract is listed and trading well before the five-minute slot it is
#: named for, and 44% of all prints land before TTE 300. Treating 300 s as
#: the life would have hidden nearly half the executed volume behind an
#: assumption, so the life is MEASURED from the tape and 300 is used for
#: nothing but the slot name.
NOMINAL_SLOT_ONLY = ("SLOT_S is the settlement cadence, not the contract's "
                     "life. Life is measured from the tape.")

#: the regions the owner asked to be counted separately, as TTE bounds
ENTRY_REGION = (240.0, 280.0)
#: from entry to the envelope's withdraw-all; orders can rest across this
QUOTING_REGION = (30.0, 280.0)


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def fetch_tape(symbol: str, *, limit: int = 500,
               timeout: float = 8.0) -> Dict[str, Any]:
    """The contract's whole PUBLIC print tape. Never raises."""
    from src.l5_shadow import venues as V
    out: Dict[str, Any] = {
        "symbol": symbol,
        "source": "GET %s%s/{symbol} (PUBLIC, unauthenticated)" % (
            V.GEMINI, TRADES_PATH),
        "limit_requested": limit,
    }
    try:
        r = V.get("%s%s/%s?limit_trades=%d" % (V.GEMINI, TRADES_PATH, symbol,
                                               limit), timeout=timeout)
    except Exception as exc:                                    # noqa: BLE001
        out.update({"readable": False,
                    "why": "%s: %s" % (type(exc).__name__, exc)})
        return out
    out["rtt_s"] = r.get("rtt_s")
    if not r.get("ok"):
        out.update({"readable": False, "why": r.get("err")})
        return out
    try:
        rows = _json.loads(r["body"])
    except ValueError:
        out.update({"readable": False, "why": "unparseable tape"})
        return out
    if not isinstance(rows, list):
        out.update({"readable": False, "why": "tape was not a list"})
        return out
    prints: List[Dict[str, Any]] = []
    for t in rows:
        if not isinstance(t, Mapping):
            continue
        px, amt = _dec(t.get("price")), _dec(t.get("amount"))
        ms = t.get("timestampms")
        if px is None or amt is None or not isinstance(ms, (int, float)):
            continue
        prints.append({"tid": t.get("tid"), "utc_ms": int(ms),
                       "price": str(px), "amount": str(amt),
                       "type": str(t.get("type") or "").lower()})
    prints.sort(key=lambda p: p["utc_ms"])
    out.update({"readable": True, "n_prints": len(prints), "prints": prints,
                "truncated": len(prints) >= limit,
                "size_field": ("`amount` IS present, so contracts and notional "
                               "are MEASURED, not inferred")})
    return out


def window_volume(tape: Mapping[str, Any], *, expiry_ms: Any,
                  our_tids: Sequence[Any] = (),
                  touch_depth: Any = None,
                  gap_floor_s: float = 0.0) -> Dict[str, Any]:
    """Everything the owner asked for, per contract, from the tape alone."""
    sym = tape.get("symbol")
    out: Dict[str, Any] = {"symbol": sym}
    exp = _dec(expiry_ms)
    if not tape.get("readable") or exp is None:
        out.update({"readable": False,
                    "why": (tape.get("why") if not tape.get("readable")
                            else "no expiry known for this contract"),
                    "touch_depth_displayed": touch_depth})
        return out

    exp_ms = int(exp)
    prints = list(tape.get("prints") or [])
    for p in prints:
        p["tte_s"] = round((exp_ms - p["utc_ms"]) / 1000.0, 3)
    # THE LIFE IS MEASURED, NOT ASSUMED. The first print we can see is the
    # earliest evidence the contract was tradeable; the nominal five-minute
    # slot is not.
    first_tte = max((p["tte_s"] for p in prints), default=None)
    start_ms = (exp_ms - int(first_tte * 1000) if first_tte is not None
                else exp_ms - int(SLOT_S * 1000))
    life_s = round((exp_ms - start_ms) / 1000.0, 1)

    amts = [_dec(p["amount"]) for p in prints]
    amts = [a for a in amts if a is not None]
    notional = sum(((_dec(p["amount"]) or D("0")) * (_dec(p["price"]) or D("0"))
                    for p in prints), D("0"))
    buys = [p for p in prints if p["type"] == "buy"]
    sells = [p for p in prints if p["type"] == "sell"]

    def in_region(p, lo, hi):
        return lo <= p["tte_s"] <= hi

    entry = [p for p in prints if in_region(p, *ENTRY_REGION)]
    quoting = [p for p in prints if in_region(p, *QUOTING_REGION)]
    late = [p for p in prints if p["tte_s"] < QUOTING_REGION[0]]
    # BEFORE OUR BAND EVER OPENS. The envelope enters at TTE 280 at the
    # earliest, so anything above that is volume we can never be present for
    # under the current entry rule.
    before_band = [p for p in prints if p["tte_s"] > QUOTING_REGION[1]]
    hist: Dict[str, int] = {}
    for p in prints:
        t = p["tte_s"]
        k = (">300" if t > 300 else "240-300" if t > 240 else
             "180-240" if t > 180 else "120-180" if t > 120 else
             "60-120" if t > 60 else "30-60" if t > 30 else "0-30")
        hist[k] = hist.get(k, 0) + 1

    # ACTIVE SECONDS: distinct whole seconds carrying at least one print.
    active_s = len({p["utc_ms"] // 1000 for p in prints})
    # LONGEST DEAD GAP across the contract's whole life, start to expiry.
    edges = [start_ms] + [p["utc_ms"] for p in prints] + [exp_ms]
    gaps = [(edges[i + 1] - edges[i]) / 1000.0 for i in range(len(edges) - 1)]
    longest = max(gaps) if gaps else None
    gi = gaps.index(longest) if longest is not None else None

    ours = {str(t) for t in (our_tids or [])}
    our_prints = [p for p in prints if str(p.get("tid")) in ours]

    srt = sorted(amts)
    out.update({
        "readable": True,
        "start_utc_ms": start_ms, "settlement_utc_ms": exp_ms,
        "first_print_tte_s": first_tte,
        "observed_life_s": life_s,
        "life_why": NOMINAL_SLOT_ONLY,
        "prints": len(prints),
        "contracts_traded": str(sum(amts, D("0"))),
        "notional_usd": str(notional),
        "prints_per_min": (round(len(prints) / (life_s / 60.0), 3)
                           if life_s else None),
        "tte_histogram": hist,
        "prints_before_our_band_opens_tte_over_280": len(before_band),
        "contracts_before_our_band_opens": str(
            sum((_dec(p["amount"]) or D("0") for p in before_band), D("0"))),
        "notional_before_our_band_opens": str(
            sum(((_dec(p["amount"]) or D("0")) * (_dec(p["price"]) or D("0"))
                 for p in before_band), D("0"))),
        "active_seconds_with_a_print": active_s,
        "active_share_of_life": (round(active_s / life_s, 4)
                                 if life_s else None),
        "longest_dead_gap_s": (round(longest, 1) if longest is not None
                               else None),
        "longest_dead_gap_share_of_life": (round(longest / life_s, 3)
                                           if longest is not None and life_s
                                           else None),
        "longest_dead_gap_position": (
            "before the first print" if gi == 0 else
            ("after the last print" if gi == len(gaps) - 1 else "mid-window")
            if gi is not None else None),
        "prints_in_entry_region_240_280": len(entry),
        "prints_while_we_could_be_quoting_30_280": len(quoting),
        "contracts_while_we_could_be_quoting": str(
            sum((_dec(p["amount"]) or D("0") for p in quoting), D("0"))),
        "notional_while_we_could_be_quoting": str(
            sum(((_dec(p["amount"]) or D("0")) * (_dec(p["price"]) or D("0"))
                 for p in quoting), D("0"))),
        "prints_after_our_withdraw_tte_30": len(late),
        "contracts_after_our_withdraw": str(
            sum((_dec(p["amount"]) or D("0") for p in late), D("0"))),
        "notional_after_our_withdraw": str(
            sum(((_dec(p["amount"]) or D("0")) * (_dec(p["price"]) or D("0"))
                 for p in late), D("0"))),
        "yes_demand_prints": len(buys),
        "no_demand_prints": len(sells),
        "yes_demand_contracts": str(
            sum((_dec(p["amount"]) or D("0") for p in buys), D("0"))),
        "no_demand_contracts": str(
            sum((_dec(p["amount"]) or D("0") for p in sells), D("0"))),
        "side_mapping": ("the venue publishes ONE tape, the YES contract's. "
                         "`buy` is an aggressor buying YES; `sell` is an "
                         "aggressor selling YES, which is NO demand. There is "
                         "no separate NO tape and none is invented."),
        "median_print_size": (str(srt[len(srt) // 2]) if srt else None),
        "max_print_size": (str(max(amts)) if amts else None),
        "min_print_size": (str(min(amts)) if amts else None),
        "largest_print": (max(
            ({"price": p["price"], "amount": p["amount"], "tte_s": p["tte_s"],
              "type": p["type"]} for p in prints),
            key=lambda x: _dec(x["amount"]) or D("0")) if prints else None),
        # DISPLAYED, AND KEPT APART. It contributes to no volume figure above.
        "touch_depth_displayed": touch_depth,
        "depth_note": ("DISPLAYED RESTING SIZE. It is not volume, it is not "
                       "added to anything above, and C8B showed a 483-deep "
                       "level evaporate to 0.01 in one cycle."),
        "our_fill_tids": sorted(ours) if ours else [],
        "our_fills_on_tape": len(our_prints),
        "our_fill_detail": our_prints,
        "tape_truncated": tape.get("truncated"),
        "labels": ("EXECUTED VOLUME from the PUBLIC tape. Nothing here is "
                   "derived from order-book depth."),
    })
    return out


def venue_reported_volume(symbol: str, *,
                          timeout: float = 8.0) -> Dict[str, Any]:
    """Can the venue tell us the contract's volume independently of the tape?

    Tried so the tape-derived total has a cross-check rather than standing
    alone. A field that does not exist is reported as absent, which is a
    result.
    """
    from src.gemini_execution import metadata as MD
    out: Dict[str, Any] = {"symbol": symbol,
                           "source": "contract object (PUBLIC event read)"}
    try:
        tk = MD.event_ticker(symbol)
        ev = MD.fetch_event(tk) if tk else {"readable": False}
        if not ev.get("readable"):
            out.update({"readable": False, "why": ev.get("err") or "no event"})
            return out
        c = MD.contract_of(ev.get("event") or {}, symbol) or {}
    except Exception as exc:                                    # noqa: BLE001
        out.update({"readable": False,
                    "why": "%s: %s" % (type(exc).__name__, exc)})
        return out
    # WHATEVER the contract object calls it, found by name rather than assumed
    found = {k: v for k, v in c.items()
             if "volume" in str(k).lower() or "traded" in str(k).lower()
             or "openinterest" in str(k).lower().replace("_", "")}
    out.update({"readable": True, "volume_fields": found,
                "contract_keys": sorted(c.keys()),
                "why": (None if found else
                        "the contract object publishes NO volume or "
                        "open-interest field; the tape is the only "
                        "executed-volume source")})
    return out


def table(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """The compact table, ranked by ACTUAL EXECUTED ACTIVITY (notional)."""
    ok = [r for r in rows if r.get("readable")]
    ok.sort(key=lambda r: (_dec(r.get("notional_usd")) or D("0")),
            reverse=True)
    out = []
    for r in ok:
        out.append({
            "WINDOW": r.get("symbol"),
            "PRINTS": r.get("prints"),
            "SHARES_TRADED": r.get("contracts_traded"),
            "NOTIONAL_USD": r.get("notional_usd"),
            "PRINTS_PER_MIN": r.get("prints_per_min"),
            "LONGEST_DEAD_GAP_S": r.get("longest_dead_gap_s"),
            "TOUCH_DEPTH": r.get("touch_depth_displayed"),
            "OUR_FILL": ("YES" if r.get("our_fills_on_tape") else "no"),
        })
    for r in rows:
        if not r.get("readable"):
            out.append({"WINDOW": r.get("symbol"), "PRINTS": None,
                        "SHARES_TRADED": None, "NOTIONAL_USD": None,
                        "PRINTS_PER_MIN": None, "LONGEST_DEAD_GAP_S": None,
                        "TOUCH_DEPTH": r.get("touch_depth_displayed"),
                        "OUR_FILL": "unreadable: %s" % r.get("why")})
    return out


def totals(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Aggregates, and the one comparison that answers the owner's question:
    how much of each window's volume falls where we could never have been."""
    ok = [r for r in rows if r.get("readable")]
    if not ok:
        return {"windows": 0, "why": "no readable window"}

    def s(key):
        return sum((_dec(r.get(key)) or D("0") for r in ok), D("0"))

    total_n = s("notional_usd")
    quoting_n = s("notional_while_we_could_be_quoting")
    late_n = s("notional_after_our_withdraw")
    return {
        "windows": len(ok),
        "prints": sum(int(r.get("prints") or 0) for r in ok),
        "contracts_traded": str(s("contracts_traded")),
        "notional_usd": str(total_n),
        "notional_while_we_could_be_quoting": str(quoting_n),
        "notional_after_our_withdraw_tte_30": str(late_n),
        "share_after_our_withdraw": (str(round(late_n / total_n, 4))
                                     if total_n else None),
        "notional_before_our_band_opens": str(s(
            "notional_before_our_band_opens")),
        "share_before_our_band_opens": (
            str(round(s("notional_before_our_band_opens") / total_n, 4))
            if total_n else None),
        "prints_before_our_band_opens": sum(
            int(r.get("prints_before_our_band_opens_tte_over_280") or 0)
            for r in ok),
        "earliest_print_tte_s": max(
            (float(r.get("first_print_tte_s") or 0) for r in ok),
            default=None),
        "prints_in_entry_region": sum(
            int(r.get("prints_in_entry_region_240_280") or 0) for r in ok),
        "windows_with_a_fill": sum(1 for r in ok if r.get("our_fills_on_tape")),
        "labels": ("EXECUTED, from the tape. Displayed depth is excluded from "
                   "every figure here."),
    }
