"""The persistence/frequency signature, in ONE shape for every source.

WHY ONE MODULE. The question is whether our clone's action sequence now looks
like L5's. That is only answerable if "actions per minute" means the same
arithmetic for L5's 501-window history, for R5's live journal and for the
shadow runner. Three implementations would guarantee three answers, so there
is one, and every caller feeds it the same normalised action list.

WHAT AN ACTION IS. One decision that produced an intent, with the state it
was taken FROM. A tick that produced no intent is counted separately, as
no_intent, and never as an action -- R5's defect expressed itself precisely
as ticks that looked alive and emitted nothing.

`l5_completion_behavior` measures the same quantities over L5's own fills;
the key names here are deliberately identical so the cohorts can be compared
without a translation layer.

SHADOW ONLY where the source is the shadow runner: no field here is a
realized economic quantity, and nothing in this module reads or writes money.
"""
from __future__ import annotations

import statistics
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

#: an action's `mode`, matching l5_completion_behavior
FLAT_OPEN, EXTEND, REBALANCE = "flat_open", "extend", "rebalance"


def _num(v: Any) -> Optional[float]:
    """A number, or None. Never a raise: this module only observes."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f          # NaN is not a price


def classify(side: str, *, up: float, down: float) -> str:
    """EXTEND adds to the heavy side; REBALANCE adds to the light one."""
    resid = up - down
    if resid == 0:
        return FLAT_OPEN
    heavy = "up" if resid > 0 else "down"
    return EXTEND if side == heavy else REBALANCE


def signature(actions: Sequence[Mapping[str, Any]], *,
              no_intent_ticks: int = 0,
              window_s: Optional[float] = None,
              label: str = "SHADOW") -> Dict[str, Any]:
    """The comparable signature of one window's action sequence.

    `actions` each need: t (epoch seconds), side ("up"/"down"), and the
    inventory BEFORE the action as up/down; px and qty are optional.
    """
    # An action with no readable side is still an ACTION: it happened, at a
    # time, and cadence is the thing being measured. It is kept for counts
    # and timings and skipped only where a side is genuinely required.
    acts = [a for a in actions if a.get("t") is not None]
    acts = sorted(acts, key=lambda a: a["t"])
    n = len(acts)
    ticks = n + int(no_intent_ticks)

    modes: List[str] = []
    streaks: List[int] = []
    dts: List[float] = []
    matched: List[float] = []
    residual: List[float] = []
    transitions: Dict[str, int] = {}
    streak = 0
    prev_side = prev_mode = None
    prev_t = None
    prev_px_by_side: Dict[str, float] = {}
    same_price = reprice = 0

    for a in acts:
        up = float(a.get("up") or 0.0)
        dn = float(a.get("down") or 0.0)
        matched.append(min(up, dn))
        residual.append(abs(up - dn))
        if prev_t is not None:
            dts.append(float(a["t"]) - float(prev_t))
        raw_side = a.get("side")
        if not raw_side:
            # cadence counted, side metrics left alone
            prev_t = a["t"]
            continue
        side = str(raw_side).lower()
        mode = classify(side, up=up, down=dn)
        modes.append(mode)
        streak = streak + 1 if (prev_side is not None and side == prev_side) else 1
        streaks.append(streak)
        if prev_mode is not None:
            transitions["%s->%s" % (prev_mode, mode)] = transitions.get(
                "%s->%s" % (prev_mode, mode), 0) + 1
        # A px we cannot read is UNKNOWN: it contributes to neither
        # same_price nor reprice, rather than being coerced. An upstream
        # `str(None)` once reached here as the literal "None" and raised.
        px = _num(a.get("px"))
        if px is not None:
            last = prev_px_by_side.get(side)
            if last is not None:
                if abs(px - last) < 1e-9:
                    same_price += 1
                else:
                    reprice += 1
            prev_px_by_side[side] = px
        prev_side, prev_mode, prev_t = side, mode, a["t"]

    span_s = ((acts[-1]["t"] - acts[0]["t"]) if n >= 2 else 0.0) or 0.0
    basis_s = window_s if window_s else span_s

    def med(v: Iterable[float]) -> Optional[float]:
        v = [x for x in v if x is not None]
        return statistics.median(v) if v else None

    def frac(m: str) -> Optional[float]:
        # denominator is SIDED actions, not all actions: a fraction over a
        # denominator that includes sideless actions understates every mode.
        return (modes.count(m) / len(modes)) if modes else None

    return {
        "label": label,
        "n_actions": n,
        "n_ticks": ticks,
        # ---- frequency and persistence: the signature that matters
        "actions_per_min": (n * 60.0 / basis_s) if basis_s else None,
        "median_dt_prev": med(dts),
        "no_intent_tick_frac": ((ticks - n) / ticks) if ticks else None,
        "last_action_s_to_expiry": (acts[-1].get("s_to_expiry") if n else None),
        "first_action_s_to_expiry": (acts[0].get("s_to_expiry") if n else None),
        # ---- inventory through time
        "median_matched": med(matched),
        "max_matched": max(matched) if matched else None,
        "median_residual": med(residual),
        "max_residual": max(residual) if residual else None,
        "matched_series": matched,
        "residual_series": residual,
        # ---- mode structure
        "extend_frac": frac(EXTEND),
        "rebalance_frac": frac(REBALANCE),
        "flat_open_frac": frac(FLAT_OPEN),
        "transitions": transitions,
        "max_streak": max(streaks) if streaks else 0,
        "median_streak": med(streaks),
        "same_price_frac": (same_price / (same_price + reprice)
                            if (same_price + reprice) else None),
        "labels": ("counts and timings only; no realized economics. A tick "
                   "that emitted no intent is no_intent, never an action."),
    }


def from_native_journal(rows: Iterable[Mapping[str, Any]], *,
                        label: str = "SHADOW") -> Dict[str, Any]:
    """Signature of a live/shadow window from its `native_tick` journal.

    A native_tick row carries the decision's verdict; rows whose `submitted`
    list is empty are the no-intent ticks that R5's defect produced in bulk.
    Inventory is carried forward from the engine's own reported book where the
    journal has it, so nothing is recomputed from prices.
    """
    acts: List[Dict[str, Any]] = []
    no_intent = 0
    unsided = 0
    up = dn = 0.0
    for r in rows:
        if r.get("kind") != "native_tick":
            continue
        # SCHEMA VARIES ACROSS VINTAGES, and getting this wrong is silent.
        # R5's journal carries `submitted` as an INT COUNT, not a list, which
        # made an earlier version of this loop raise TypeError on the live
        # file. A count tells us an action happened but not its side, so it
        # is recorded as UNSIDED rather than being invented a side: the
        # side-dependent metrics then report None instead of a wrong number.
        raw = r.get("intents")
        if not isinstance(raw, (list, tuple)):
            raw = r.get("submitted")
        if isinstance(raw, (list, tuple)):
            intents = list(raw)
        elif isinstance(raw, bool):
            intents = []
        elif isinstance(raw, (int, float)) and raw > 0:
            unsided += int(raw)
            acts.extend({"t": r.get("t"), "side": None, "up": up, "down": dn,
                         "px": None, "s_to_expiry": r.get("s_to_expiry")}
                        for _ in range(int(raw)))
            continue
        else:
            intents = []
        if not intents:
            no_intent += 1
            continue
        for it in intents:
            side = str(it.get("side") or "").lower()
            if side not in ("up", "down"):
                continue
            acts.append({"t": r.get("t"), "side": side, "up": up, "down": dn,
                         "px": it.get("px"),
                         "s_to_expiry": r.get("s_to_expiry")})
    out = signature(acts, no_intent_ticks=no_intent, label=label)
    out["actions_without_side"] = unsided
    if unsided:
        out["side_metrics_unavailable"] = (
            "%d action(s) came from an integer `submitted` count carrying no "
            "side, so extend/rebalance fractions, streaks and transitions "
            "are NOT reported for this source rather than being guessed."
            % unsided)
        for k in ("extend_frac", "rebalance_frac", "flat_open_frac",
                  "max_streak", "median_streak", "transitions",
                  "same_price_frac"):
            out[k] = None
    return out


# ------------------------------------------------- the clone's own journal

#: R5's vintage of `native_tick` records the DECISION but not the intents:
#: l5_pmus_live_oneshot writes `**{k: v for k, v in res.items() if k !=
#: "submitted"}`, so the per-tick action list is explicitly excluded. Reading
#: it therefore yields n_actions 0 and no_intent_tick_frac 1.0 -- which looks
#: exactly like the R5 idleness thesis being confirmed, and is in fact a
#: wrong key lookup. The authoritative record of what was SUBMITTED is the
#: mandate: one `mandate_admission` journal row per admitted order, and one
#: `mandate_commit` ledger row per commitment carrying side and price_wire.
ADMISSION_KIND = "mandate_admission"
COMMIT_KIND = "mandate_commit"


def from_mandate_records(journal_rows: Iterable[Mapping[str, Any]],
                         ledger_rows: Iterable[Mapping[str, Any]] = (),
                         *, label: str = "CLONE",
                         expect_actions: Optional[int] = None,
                         window_s: Optional[float] = None) -> Dict[str, Any]:
    """Signature of a live clone window, from the mandate's own records.

    `expect_actions` is a RECONCILIATION INVARIANT, not a hint. R5 submitted
    13 orders; if the reconstruction does not find exactly that many, this
    returns a refusal instead of statistics, because a cadence computed from
    the wrong number of actions is worse than no cadence at all.

    Side and price come from the ledger's `mandate_commit` rows, joined to
    the journal's admissions by `commit_id`. Where the ledger is absent the
    actions are UNSIDED and the side-dependent metrics report None.
    """
    admitted = [r for r in journal_rows
                if r.get("kind") == ADMISSION_KIND and r.get("admits") is True]
    by_commit: Dict[str, Mapping[str, Any]] = {}
    for r in ledger_rows:
        if r.get("kind") == COMMIT_KIND and r.get("commit_id"):
            by_commit[str(r["commit_id"])] = r

    acts: List[Dict[str, Any]] = []
    for r in admitted:
        c = by_commit.get(str(r.get("commit_id") or ""))
        side = None
        px = None
        if c is not None:
            raw = str(c.get("side") or "").upper()
            if "YES" in raw:
                side = "up"
            elif "NO" in raw:
                side = "down"
            px = _num(c.get("price_wire"))
        acts.append({"t": _num(r.get("t")), "side": side, "px": px,
                     "up": 0.0, "down": 0.0,
                     "qty": _num(r.get("quantity")),
                     "cash_at_risk": _num(r.get("cash_at_risk"))})

    if expect_actions is not None and len(acts) != int(expect_actions):
        return {"label": label, "RECONCILIATION_FAILED": True,
                "expected_actions": int(expect_actions),
                "reconstructed_actions": len(acts),
                "admission_rows_seen": len(admitted),
                "ledger_commit_rows_seen": len(by_commit),
                "why": ("the reconstruction does not match the known "
                        "submitted-order count, so no comparison statistics "
                        "are produced: a cadence computed from the wrong "
                        "number of actions is worse than none"),
                "labels": "STOPPED on the reconciliation invariant"}

    out = signature(acts, no_intent_ticks=0, window_s=window_s, label=label)
    out["source"] = "mandate_admission + mandate_commit"
    out["reconciled_actions"] = len(acts)
    out["ledger_joined"] = sum(1 for a in acts if a.get("side"))
    qty = [a["qty"] for a in acts if a.get("qty") is not None]
    cash = [a["cash_at_risk"] for a in acts if a.get("cash_at_risk") is not None]
    out["median_qty"] = statistics.median(qty) if qty else None
    out["total_cash_at_risk"] = sum(cash) if cash else None
    # no_intent has NO meaning here: the mandate records only what was
    # admitted, so ticks that decided nothing are simply not in this source.
    out["no_intent_tick_frac"] = None
    out["no_intent_note"] = ("not computable from mandate records -- they "
                             "contain only admitted orders, not declined "
                             "ticks. Do not read the absence as idleness.")
    if out["ledger_joined"] == 0:
        out["side_metrics_unavailable"] = (
            "no mandate_commit row joined, so side and price are unknown and "
            "extend/rebalance, streaks, transitions and same_price are NOT "
            "reported rather than guessed.")
        for k in ("extend_frac", "rebalance_frac", "flat_open_frac",
                  "max_streak", "median_streak", "transitions",
                  "same_price_frac"):
            out[k] = None
    return out
