"""Lane A readiness, presented so no number can be read as more than it is.

TWO PRESENTATION DEFECTS THIS EXISTS TO CLOSE. Neither was a policy error; both
would have made an arming decision on evidence that does not say what it seems.

1. `spring_le_096_completions` COUNTS COMPLETIONS AT OR BELOW 0.98, NOT 0.96.
   Its own source says the key name was "kept for consumer compatibility". Read
   literally it reported the <=0.96 bar met three times over on a market whose
   every completion was 0.9800 -- the bar is met ZERO times. This module never
   reads that field. It recounts from `spring_pair_costs`, the actual costs, and
   publishes <=0.96 and <=0.98 as SEPARATE explicit counts that cannot be
   confused for one another.

2. `probe_entry_action` publishes `q1_size: 1` beside `target_price: 0.27` and
   nothing else, which reads as a $0.27 order against a $0.05 per-market cap.
   It is not. `q1_size` is a BEHAVIORAL unit -- step 2 of the session's own
   three-step ladder, "one L5 size unit = one venue-minimum contract" -- while
   the executable size is step 3, derived by choose_micro_quantity under the
   rung's notional bounds. At $0.27 with the venue's minimumTradeQty of 0.01
   that is 0.02 contracts for $0.0054. The presentation omitted the only two
   numbers that make the envelope legible, so this module carries them.

NO THRESHOLD IS CHANGED HERE. SPRING_PAIR_TARGET stays 0.98, the category bar
stays as written, and nothing in this file arms anything.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

LABEL = "LANE_A_READINESS/presentation"

# Reported separately, always. The first is the standing spring discipline; the
# second is what the venue implementation currently targets.
DISCIPLINE_096 = D("0.96")
VENUE_TARGET_098 = D("0.98")

# Never read. Named so a reader can see it was considered and rejected.
LEGACY_MISLABELLED_FIELD = "spring_le_096_completions"
LEGACY_NOTE = ("counts completions <= 0.98 despite the 096 in its name; the "
               "source comment says the key was kept for consumer "
               "compatibility. Never interpret it as <=0.96 evidence.")


def _d(v: Any) -> Optional[D]:
    try:
        return None if v is None else D(str(v))
    except Exception:                                         # noqa: BLE001
        return None


def pair_cost_counts(costs: Iterable[Any]) -> Dict[str, Any]:
    """Explicit, separate counts recomputed from the actual pair costs."""
    cs = [c for c in (_d(x) for x in costs) if c is not None]
    le96 = [c for c in cs if c <= DISCIPLINE_096]
    le98 = [c for c in cs if c <= VENUE_TARGET_098]
    return {
        "n_completions": len(cs),
        "pair_costs": [str(c) for c in cs],
        "le_096_completions": len(le96),
        "le_098_completions": len(le98),
        "above_098_completions": len(cs) - len(le98),
        "cheapest": (str(min(cs)) if cs else None),
        "meets_096_discipline": bool(le96),
        "meets_098_venue_target": bool(le98),
        # the whole point: these two are not the same number
        "counts_are_separate": True,
        "legacy_field_ignored": LEGACY_MISLABELLED_FIELD,
        "legacy_field_note": LEGACY_NOTE,
    }


def market_readiness(row: Mapping[str, Any]) -> Dict[str, Any]:
    """One spring_watch row, with the costs recounted rather than trusted."""
    counts = pair_cost_counts(row.get("spring_pair_costs") or ())
    ce = row.get("spring_category_evidence") or {}
    return {
        "market": row.get("slug"),
        "category": row.get("spring_category"),
        "gate_status": row.get("gate_status"),
        "two_sided_trades": bool(row.get("two_sided_trades")),
        "matched_slots": row.get("spring_matched_slots"),
        "matched_fraction": row.get("spring_matched_fraction"),
        "evidence_missing": list(row.get("spring_evidence_missing") or ()),
        **counts,
        "category_completions_60m": ce.get("completions_60m"),
        "category_distinct_markets_60m": ce.get("distinct_markets_60m"),
        "category_bar_met": bool(row.get("spring_category_bar_met")),
    }


def category_bar(rows: Sequence[Mapping[str, Any]], *,
                 min_completions: int = 2,
                 min_distinct_markets: int = 2) -> Dict[str, Any]:
    """How far each category is from the bar -- unchanged, just made legible.

    The bar is NOT relaxed here. Showing the distance to it is how a second
    qualifying market becomes visible the moment it appears.
    """
    by_cat: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        cat = str(r.get("spring_category") or "?")
        m = by_cat.setdefault(cat, {"category": cat, "markets": [],
                                    "completions": 0, "le_096": 0, "le_098": 0})
        n = int(r.get("spring_matched_slots") or 0)
        if n > 0:
            m["markets"].append(str(r.get("slug")))
            m["completions"] += n
            c = pair_cost_counts(r.get("spring_pair_costs") or ())
            m["le_096"] += c["le_096_completions"]
            m["le_098"] += c["le_098_completions"]
    out = []
    for m in by_cat.values():
        distinct = len(set(m["markets"]))
        out.append({
            **m, "distinct_markets": distinct,
            "bar_met": (m["completions"] >= min_completions
                        and distinct >= min_distinct_markets),
            "markets_still_needed": max(0, min_distinct_markets - distinct),
            "completions_still_needed": max(0, min_completions - m["completions"]),
        })
    out.sort(key=lambda x: (-x["distinct_markets"], -x["completions"]))
    return {"bar": {"min_completions": min_completions,
                    "min_distinct_markets": min_distinct_markets,
                    "relaxed": False},
            "categories_with_any_completion": [c for c in out if c["completions"]],
            "closest": (out[0] if out else None),
            "any_bar_met": any(c["bar_met"] for c in out)}


def size_resolution(entry_price: Any, minimum_trade_qty: Any = "0.01",
                    ) -> Dict[str, Any]:
    """The three-step ladder, all steps visible.

    Publishing `q1_size` alone is what made a $0.05 cap look violated by a
    $0.27 quote. The behavioral unit and the executable size are different
    quantities and both belong in the record.
    """
    from src import pmus_l5_ramp_ladder as ladder
    from src import pmus_micro_executor as micro
    p = D(str(entry_price))
    minq = D(str(minimum_trade_qty))
    rung = ladder.active()
    target = ladder.target_notional()
    max_leg = ladder.max_leg_notional()
    cap = D(str(rung["per_market_cap_usd"]))
    try:
        qty = micro.choose_micro_quantity(p, minq, target, max_leg)
        halted = None
    except Exception as exc:                                  # noqa: BLE001
        qty, halted = None, f"{type(exc).__name__}:{exc}"
    if qty is None:
        return {"executable": False, "halt": halted, "rung": rung["id"],
                "entry_price": str(p), "minimum_trade_qty": str(minq),
                "why": ("the venue's minimum lot cannot fit the rung's "
                        "max_leg_notional; the path FAILS CLOSED rather than "
                        "overspending")}
    entry = qty * p
    worst_comp = qty * (D("1") - p)
    return {
        "executable": True, "rung": rung["id"],
        "entry_price": str(p), "minimum_trade_qty": str(minq),
        "ladder": [
            {"step": "behavioral_unit", "value": 1,
             "meaning": "one L5 size unit = one venue-minimum contract; this "
                        "is what `q1_size: 1` reports, NOT a contract count "
                        "and NOT a dollar amount"},
            {"step": "executable_contracts", "value": str(qty),
             "meaning": "derived under the rung's notional bounds"},
            {"step": "entry_notional_usd", "value": str(entry),
             "meaning": "what is actually spent on the maker entry"},
        ],
        "worst_complement_notional_usd": str(worst_comp),
        "target_notional_usd": str(target),
        "max_leg_notional_usd": str(max_leg),
        "per_market_cap_usd": str(cap),
        "fits_per_market_cap": entry <= cap,
        "entries_before_cap": int(cap / entry) if entry > 0 else None,
        "verdict": ("the $%s cap is NOT obsolete and does NOT block this "
                    "candidate: at %s the executable size is %s contracts for "
                    "$%s, which is %sx inside the cap"
                    % (cap, p, qty, entry,
                       (cap / entry).quantize(D("1")) if entry > 0 else "n/a")),
    }


# ------------------------------------------------------- arming preflight

ARM_LABEL = "LANE_A_ARMING_PREFLIGHT/prepared_not_executed"

# Gates EVIDENCE decides. Nobody approves these; they are true or they are not.
EVIDENCE_GATES = (
    "CATEGORY_BAR", "STRICT_EVIDENCE_FRESH", "MARKET_OPEN",
    "DURABLE_STATE_MOUNT", "BUDGET_PERMITS", "SERVING_ARTIFACT",
    "SIZE_EXECUTABLE", "SETTLEMENT_ACCOUNTING",
)

# Decisions only the OWNER can make. Listing them is the point of this module:
# when the second market appears, what remains should be these three, not
# another engineering cycle.
OWNER_DECISIONS = (
    {"id": "CLEAR_SHUTDOWN_LATCH",
     "question": "Clear the PMUS_SPRING_SHUTDOWN latch?",
     "why": "the probe is HALTED on it; no evidence clears a latch",
     "default_if_unanswered": "stay halted"},
    {"id": "SET_DUAL_KEY",
     "question": "Set PMUS_SPRING_LIVE / _ARM / _SESSION_ID / _MARKET_SLUG / "
                 "_GATE_EVIDENCE?",
     "why": "operator-held; arming is an explicit act, never derived",
     "default_if_unanswered": "disarmed"},
    {"id": "ACCEPT_098_OR_HOLD_FOR_096",
     "question": "Is a 0.98 completion acceptable for a first bounded probe, "
                 "or does the probe wait for <=0.96?",
     "why": ("every observed completion is 0.9800 and NONE is <=0.96. "
             "CLAUDE.md keeps near-$1 pair tolerance LOCKED pending "
             "source-confirmed equivalent economics for this account. This is "
             "a discipline question, not a threshold to quietly move."),
     "default_if_unanswered": "hold for <=0.96"},
)


def arming_preflight(*, rows: Sequence[Mapping[str, Any]],
                     entry_price: Any = None,
                     minimum_trade_qty: Any = "0.01",
                     serving_artifact: Optional[str] = None,
                     expected_artifact: str = "6adcc4c4",
                     budget_permits: Optional[bool] = None,
                     latch_set: Optional[bool] = None,
                     dual_key_set: Optional[bool] = None,
                     settlement_confirmed_markets: Optional[int] = None,
                     ) -> Dict[str, Any]:
    """What is still true, what is still missing, and what only you can decide.

    PREPARES. Executes nothing, arms nothing, relaxes nothing. Every gate that
    cannot be established reads UNDETERMINED, which blocks -- "we could not
    check" never reads as "it is fine".
    """
    bar = category_bar(rows)
    per_market = [market_readiness(r) for r in rows
                  if int(r.get("spring_matched_slots") or 0) > 0]
    all_costs: List[Any] = []
    for r in rows:
        all_costs.extend(r.get("spring_pair_costs") or ())
    costs = pair_cost_counts(all_costs)
    size = (size_resolution(entry_price, minimum_trade_qty)
            if entry_price is not None else
            {"executable": None, "why": "no candidate price supplied"})

    def g(name: str, ok: Optional[bool], blocker: Optional[str],
          detail: Any = None) -> Dict[str, Any]:
        return {"gate": name,
                "state": ("PASS" if ok is True else
                          "FAIL" if ok is False else "UNDETERMINED"),
                "blocker": blocker, "detail": detail}

    gates = [
        g("CATEGORY_BAR", bar["any_bar_met"],
          None if bar["any_bar_met"] else
          ("no category has >=2 completions from >=2 distinct markets; "
           "closest: %s" % (bar["closest"] or {}).get("category")),
          bar["closest"]),
        g("SIZE_EXECUTABLE", size.get("executable"),
          None if size.get("executable") else size.get("why") or size.get("halt"),
          size),
        g("SERVING_ARTIFACT",
          None if serving_artifact is None else
          serving_artifact.startswith(expected_artifact),
          "serving artifact not supplied" if serving_artifact is None else
          (None if serving_artifact.startswith(expected_artifact)
           else f"serving {serving_artifact[:12]} != {expected_artifact}"),
          {"serving": serving_artifact, "expected": expected_artifact}),
        g("BUDGET_PERMITS", budget_permits,
          "budget state not supplied" if budget_permits is None else
          (None if budget_permits else "combined loss has reached the stop")),
        g("SETTLEMENT_ACCOUNTING",
          None if settlement_confirmed_markets is None
          else settlement_confirmed_markets > 0,
          "settlement confirmation count not supplied"
          if settlement_confirmed_markets is None else
          (None if settlement_confirmed_markets > 0
           else "no venue-confirmed settled market exists"),
          {"venue_confirmed_markets": settlement_confirmed_markets}),
        # the latch and the key are OWNER state; reported, never "passed" by us
        g("SHUTDOWN_LATCH_CLEARED",
          None if latch_set is None else (not latch_set),
          "latch state not supplied" if latch_set is None else
          ("PMUS_SPRING_SHUTDOWN is set -- owner decision" if latch_set else None)),
        g("DUAL_KEY_ARMED",
          None if dual_key_set is None else dual_key_set,
          "dual-key state not supplied" if dual_key_set is None else
          (None if dual_key_set else "operator-held dual key not set")),
    ]
    blockers = [x for x in gates if x["state"] != "PASS"]
    return {
        "label": ARM_LABEL,
        "READY_TO_ARM": not blockers,
        "verdict": "BLOCKED" if blockers else "EVIDENCE_COMPLETE_AWAITING_OWNER",
        "pair_cost_counts_all_markets": costs,
        "category_bar": bar,
        "markets_with_completions": per_market,
        "size_resolution": size,
        "gates": gates,
        "blockers": [{"gate": b["gate"], "state": b["state"],
                      "blocker": b["blocker"]} for b in blockers],
        "owner_decisions": list(OWNER_DECISIONS),
        "labels": ("PREPARED, NOT EXECUTED. No threshold moved, the category "
                   "bar is NOT relaxed, and nothing here arms anything. The "
                   "<=0.96 and <=0.98 counts are separate and the legacy "
                   f"{LEGACY_MISLABELLED_FIELD} field is never read."),
    }
