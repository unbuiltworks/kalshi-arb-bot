"""BOTH LANES, COMPARED ONLY WHERE MODEL A CAN TRADE.

The whole-window BTC15m promotion is withdrawn. A 15-minute contract's tape
is mostly flow the frozen policy never sees: `TTE_policy
quote_between_tte_s = [0, 300]` is a MEASURED L5 parameter and is not touched
here or anywhere. So every comparison below is restricted to **TTE <= 300**,
which is the only interval either lane can act in, and the restriction is
applied to the VENUE's tape as well as to ours.

This module measures the two running lanes. It chooses nothing, ranks
nothing and proposes nothing.
"""
from __future__ import annotations

import glob
import json
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

#: the only interval the frozen policy quotes in. Read, never set.
REACHABLE_TTE_S = 300.0

#: a matched pair above this is a matched loss
PAR = D("1")


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def quoting_window() -> Sequence[float]:
    """Model A's own quoting window, read from the frozen spec."""
    from src.gemini_execution import continuous_probe as CP
    lo, hi = CP.probe_params().get("TTE_policy")["quote_between_tte_s"]
    return (float(lo), float(hi))


def lane_evidence(evidence_glob: str) -> Dict[str, List[Dict[str, Any]]]:
    """Read what a RUNNING lane has actually persisted.

    Not the `--out` report: a lane started with `--windows 0` runs for ever,
    so the report at the end of its loop is never written. The durable record
    is the append-only evidence stream, one row per cycle and one per window,
    and that is what a continuously running lane can be read from at all.
    """
    windows: List[Dict[str, Any]] = []
    cycles: List[Dict[str, Any]] = []
    for path in sorted(glob.glob(evidence_glob)):
        try:
            fh = open(path, encoding="utf-8")
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                row["_file"] = path
                (windows if row.get("kind") == "window" else cycles).append(row)
    return {"windows": windows, "cycles": cycles}


def lane_windows(evidence_glob: str) -> List[Dict[str, Any]]:
    """Completed windows, each carrying the cycles that belong to it.

    The join is by symbol and bounded by the window's own start/finish, so a
    lane that revisits a symbol does not pool two visits into one window.
    """
    ev = lane_evidence(evidence_glob)
    by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    for c in ev["cycles"]:
        by_symbol.setdefault(str(c.get("symbol")), []).append(c)
    out: List[Dict[str, Any]] = []
    for w in ev["windows"]:
        w = dict(w)
        mine = by_symbol.get(str(w.get("symbol")), [])
        lo, hi = w.get("started_utc"), w.get("finished_utc")
        w["cycles_detail"] = [
            c for c in mine
            if (lo is None or str(c.get("utc") or "") >= str(lo))
            and (hi is None or str(c.get("utc") or "") <= str(hi))
        ]
        out.append(w)
    return out


def tape_in_window(prints: Sequence[Mapping[str, Any]], *,
                   expiry_ms: Any) -> Dict[str, Any]:
    """The venue's own flow, restricted to the reachable interval."""
    exp = _dec(expiry_ms)
    n = 0
    notional = D("0")
    buys = sells = 0
    for t in prints or []:
        ms, px, amt = (_dec(t.get("utc_ms")), _dec(t.get("price")),
                       _dec(t.get("amount")))
        if None in (ms, px, amt) or exp is None:
            continue
        tte = float((exp - ms) / D("1000"))
        if tte > REACHABLE_TTE_S or tte < 0:
            continue
        n += 1
        notional += amt * px
        if str(t.get("type")) == "buy":
            buys += 1
        else:
            sells += 1
    return {"prints": n, "notional_usd": str(notional),
            "buy_prints": buys, "sell_prints": sells,
            "two_sided": bool(buys > 0 and sells > 0)}


def lane_census(windows: Sequence[Mapping[str, Any]],
                tapes: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """THE THIRTEEN COLUMNS, over the reachable interval only.

    Prospective: these are windows a running lane actually lived through,
    not a replay of a tape we fetched afterwards.

    THE FILL COLUMNS ARE MEASURED ONLY WHERE THE LANE RAN THE HYPOTHETICAL
    LEDGER, AND ARE ABSENT -- NOT ZERO -- ANYWHERE ELSE. `shadow_soak` places
    no order, so a window recorded before the ledger existed has no fill
    mechanism at all; printing 0 first fills beside a real print count would
    read as a market that declined our quote rather than as a lane that never
    quoted. Where the ledger did run, the numbers are HYPOTHETICAL: no order
    was submitted and nothing is filled, settled, realized or a cost basis.
    """
    done = [w for w in windows if w.get("cycles")]
    quotes = vetoes = 0
    ledger = rungs = firsts = comps = unknown_q = 0
    matched = gross = D("0")
    cap = D("0")
    prints = D("0")
    notional = D("0")
    two_sided = 0
    buys = sells = 0
    tape_seen = 0
    for w in done:
        sym = w.get("symbol")
        t = tapes.get(sym)
        if t is not None:
            tape_seen += 1
            prints += D(int(t.get("prints") or 0))
            notional += _dec(t.get("notional_usd")) or D("0")
            two_sided += 1 if t.get("two_sided") else 0
            buys += int(t.get("buy_prints") or 0)
            sells += int(t.get("sell_prints") or 0)
        vetoes += int(w.get("adapter_vetoes") or 0)
        # the window's own terminal inventory where it has one; otherwise
        # the LAST cycle's, which is the nearest thing the record carries
        last_inv: Mapping[str, Any] = dict(w.get("inventory") or {})
        cyc_inv: Mapping[str, Any] = {}
        for r in (w.get("cycles_detail") or []):
            quotes += len(r.get("model_a_wanted") or [])
            cyc_inv = r.get("inventory") or cyc_inv
        last_inv = last_inv or cyc_inv
        cap += _dec(last_inv.get("total_spend_usd")) or D("0")
        h = w.get("hypothetical")
        if h:
            ledger += 1
            rungs += int(h.get("rungs_placed") or 0)
            firsts += int(h.get("first_fills") or 0)
            comps += int(h.get("complete_fills") or 0)
            unknown_q += int(h.get("fills_resting_on_an_unknown_queue") or 0)
            m = _dec(last_inv.get("matched_qty")) or D("0")
            matched += m
            # gross is what was bought, matched or not: the par-constrained
            # denominator the completion work ranks on
            matched_pairs_gross = m * D("2")
            gross += (matched_pairs_gross
                      + abs(_dec(last_inv.get("directional_residual"))
                            or D("0")))
    absent = ("this lane rested no rung and ran no hypothetical ledger, so "
              "the quantity has no mechanism here. Not a measured zero.")
    measured = ledger > 0
    return {
        "windows": len(done),
        "windows_with_tape": tape_seen,
        "prints_in_reachable_window": int(prints),
        "notional_in_reachable_window_usd": str(notional),
        "two_sided_windows": two_sided,
        "two_sided_share": (round(two_sided / tape_seen, 3)
                            if tape_seen else None),
        "buy_prints": buys,
        "sell_prints": sells,
        "model_a_quotes": quotes,
        "quotes_per_window": (round(quotes / len(done), 2) if done else None),
        "adapter_vetoes": vetoes,
        "windows_with_a_hypothetical_ledger": ledger,
        "hypothetical_rungs_placed": (rungs if measured else None),
        "first_fills": (firsts if measured else None),
        "complete_fills": (comps if measured else None),
        "fills_resting_on_an_unknown_queue": (unknown_q if measured
                                              else None),
        "matched_contracts": (str(matched) if measured else None),
        "gross_contracts": (str(gross) if measured else None),
        "matched_over_gross": (str(round(matched / gross, 4))
                               if measured and gross > 0 else None),
        "capital_at_risk_usd": str(cap),
        "unmeasurable_here": ({} if measured else {
            "first_fills": absent, "complete_fills": absent,
            "matched_contracts": absent, "gross_contracts": absent,
            "matched_over_gross": absent,
        }),
        "fills_are_hypothetical": ("every fill counted here is HYPOTHETICAL: "
                                   "no order was submitted and nothing is "
                                   "filled, settled, realized or a cost "
                                   "basis. An upper bound." if measured
                                   else None),
        "reachable_tte_s": REACHABLE_TTE_S,
        "labels": ("PROSPECTIVE, and restricted to TTE <= 300 -- the only "
                   "interval the frozen policy quotes in. SHADOW: no order "
                   "existed and nothing is realized."),
    }


def the_sequence(windows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """THE TRIGGER: OPEN fill -> inventory change -> COMPLETE fill -> matched.

    Counted rather than described, because repetition is what would justify
    preparing a bounded live probe.

    IT CAN ONLY FIRE WHERE THE HYPOTHETICAL LEDGER RAN, and it says which.
    A window recorded without the ledger rests no rung, so the inventory it
    recovers at entry is the inventory it reports at exit; zero occurrences
    there is the absence of a fill mechanism, NOT evidence that the flow
    declined to complete. Reporting it as the latter would be the strongest
    claim in this file and the least supported.

    Where the ledger did run, an occurrence is HYPOTHETICAL: the rung was
    never placed, so this is what the flow would have done to one that was.
    """
    hits = []
    moved = 0
    with_ledger = 0
    for w in windows:
        seen = set()
        if w.get("hypothetical"):
            with_ledger += 1
        wi = w.get("inventory") or {}
        rows_ = ([{"inventory": wi}] if wi else []) \
            + list(w.get("cycles_detail") or [])
        for r in rows_:
            inv = r.get("inventory") or {}
            m = _dec(inv.get("matched_qty")) or D("0")
            seen.add(str(inv.get("n_fills") or 0))
            if m <= 0:
                continue
            basis = _dec(inv.get("effective_matched_pair_cost"))
            hits.append({
                "symbol": w.get("symbol"), "cohort": w.get("cohort"),
                "matched": str(m),
                "effective_pair_basis": (str(basis) if basis is not None
                                         else None),
                "at_or_below_par": bool(basis is not None and basis <= PAR),
                "started_utc": w.get("started_utc"),
            })
            break
        if len(seen) > 1:
            moved += 1
    return {
        "occurrences": len(hits),
        "at_or_below_par": sum(1 for h in hits if h["at_or_below_par"]),
        "windows_whose_inventory_moved": moved,
        "windows_with_a_hypothetical_ledger": with_ledger,
        "rows": hits,
        "is_the_trigger": bool(len(hits) >= 2),
        "can_fire_in_these_lanes": bool(with_ledger > 0),
        "why_not": (None if with_ledger else
                    "shadow_soak calls neither Driver.place nor "
                    "Driver.cancel, so no rung rests and no REAL fill can "
                    "occur. Without the hypothetical ledger this lane has no "
                    "fill mechanism at all, and zero here is that absence."),
        "occurrences_are_hypothetical": ("the rung was never placed. This is "
                                         "what the flow would have done to "
                                         "one that was." if with_ledger
                                         else None),
        "why": ("one occurrence is an anecdote. Repetition of OPEN fill -> "
                "inventory change -> COMPLETE fill -> matched inventory is "
                "what would justify preparing a bounded live proposal, and "
                "nothing is prepared before it."),
    }
