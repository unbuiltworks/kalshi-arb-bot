"""One executable preflight for a capped live probe, and its verdict.

PRIORITY 3. Everything here is a READ. The module holds no transport,
places nothing, cancels nothing, clears no gate and changes no limit. Its
only output is a verdict dict, and `ok` false means "do not spend the
window" rather than "the engine is broken".

WHY A MODULE AND NOT A CHECKLIST. The pre-flight conditions have been
written down three times in prose (`ORDER_OBSERVER_TRACE.md`,
`ONE_WINDOW_MAKER_ONLY_PROPOSAL.md`, `GO_NOGO_READINESS.md`) and a prose
condition cannot be run before a window or re-run after settlement. This
can be both.

THE TWO HALVES.

  `preflight(...)` runs BEFORE the window. It answers: are the gates in
  force, is the plan admissible, is the market the one we pre-registered,
  and is the recorder able to produce a usable calibration?

  `audit(...)` runs AFTER settlement. It answers the only question that
  matters afterwards: is this result a MEASUREMENT or is it
  UNINTERPRETABLE? A probe market that ends in `UNKNOWN_COST_UPPER_BOUND`
  or appears in `unknown_basis_keys` is uninterpretable, and saying so is
  the whole point -- an upper bound reported as realized P&L is how a
  placeholder becomes a performance claim.

NOTHING HERE AUTHORIZES ANYTHING. `preflight` returning ok True means the
checks passed, not that a window may be spent; that remains an owner
decision, and `authorization_preflight` plus the shadow lock remain the
things that actually refuse.
"""
from __future__ import annotations

from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

#: a check that could not be evaluated. NEVER read as a pass.
UNKNOWN = "UNKNOWN"


def _d(v: Any) -> Optional[D]:
    try:
        if v is None or str(v).strip() == "":
            return None
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _row(name: str, ok: Any, **detail: Any) -> Dict[str, Any]:
    return dict({"check": name, "ok": ok}, **detail)


# ----------------------------------------------------------------- before
def preflight(*, heartbeat: Mapping[str, Any],
              plan: Optional[Mapping[str, Any]],
              markets: Sequence[str],
              calibration: Optional[Mapping[str, Any]] = None,
              expect_depth_ticks: int = 2,
              pair_ceiling: Any = "0.96") -> Dict[str, Any]:
    """Every condition that can be checked before an order exists.

    `heartbeat` is the live `L5_LIVE_HB` mapping; `plan` is the output of
    `TS.build_orders`; `markets` are the EXACT pre-registered market ids.
    Each is passed in rather than fetched, so this module needs no
    transport and no credentials.
    """
    rows: List[Dict[str, Any]] = []
    hb = heartbeat or {}

    # --- the gates that must still be refusing until the owner clears them
    gate = hb.get("submission_gate") or {}
    rows.append(_row("submission_gate_blocked_until_owner_clears",
                     gate.get("blocked") if "blocked" in gate else UNKNOWN,
                     reason=gate.get("reason"), armed=gate.get("armed")))
    rows.append(_row("halt_latch_readable",
                     (str((gate.get("halt_latch_telemetry") or {})
                          .get("kind")) == "PRESENT")
                     if gate.get("halt_latch_telemetry") else UNKNOWN,
                     latch=gate.get("halt_latch")))

    # --- exposure and loss limits, each read from the EFFECTIVE accessor
    cash = hb.get("effective_cash_gate") or {}
    wcd = _d(cash.get("max_worst_case_deficit_usd"))
    rows.append(_row("worst_case_deficit_check_active",
                     str(cash.get("worst_case_deficit_check")) == "ACTIVE"
                     and wcd is not None and wcd > 0,
                     max_worst_case_deficit_usd=str(wcd) if wcd else None,
                     state=cash.get("worst_case_deficit_check")))
    rows.append(_row("cash_policy_is_no_recycling",
                     str(cash.get("cash_policy")) == "NO_RECYCLING",
                     max_cash_deployed_usd=cash.get("max_cash_deployed_usd")))
    unmatched = _d(cash.get("max_unmatched_contracts"))
    rows.append(_row("unmatched_contract_cap_present",
                     unmatched is not None and unmatched > 0,
                     max_unmatched_contracts=cash.get(
                         "max_unmatched_contracts")))

    day = hb.get("day_realized") or {}
    room = _d(day.get("room_usd"))
    rows.append(_row("daily_loss_room_remains",
                     (room is not None and room > 0)
                     and day.get("would_halt") is False,
                     room_usd=day.get("room_usd"),
                     halt_usd=day.get("halt_usd"),
                     would_halt=day.get("would_halt")))

    camp = hb.get("campaign") or {}
    res = camp.get("completion_reserve") or {}
    rows.append(_row("completion_capacity_reserved",
                     res.get("may_open") if "may_open" in res else UNKNOWN,
                     submissions_per_obligation=res.get(
                         "submissions_per_obligation"),
                     why_not=res.get("why_not")))

    # --- the authenticated book must be fit to price against
    book = hb.get("auth_book") or {}
    age = _d(book.get("stream_age_s"))
    rows.append(_row("authenticated_book_connected_and_fresh",
                     bool(book.get("connected")) and age is not None
                     and 0 <= age <= D("2") and not book.get("gap_suspected"),
                     connected=book.get("connected"),
                     stream_age_s=book.get("stream_age_s"),
                     gap_suspected=book.get("gap_suspected"),
                     stall_reconnects=book.get("stall_reconnects")))

    # --- the plan itself, which is where the economics are judged
    if plan is None:
        rows.append(_row("plan_admissible", UNKNOWN, why="no plan supplied"))
    else:
        legs = list(plan.get("legs") or [])
        pair = _d(plan.get("pair_cost"))
        cap = _d(pair_ceiling)
        rows.append(_row("plan_admissible", bool(plan.get("ok")),
                         reason=plan.get("reason")))
        rows.append(_row("plan_has_exactly_two_legs", len(legs) == 2,
                         legs=len(legs)))
        rows.append(_row("both_legs_post_only_maker",
                         bool(legs) and all(
                             o.get("post_only") is True
                             and str(o.get("mode")).upper() == "MAKER"
                             for o in legs),
                         modes=[o.get("mode") for o in legs]))
        rows.append(_row("plan_depth_is_the_expected_depth",
                         int(plan.get("depth_ticks") or -1)
                         == int(expect_depth_ticks),
                         depth_ticks=plan.get("depth_ticks"),
                         expected=expect_depth_ticks))
        rows.append(_row("pair_cost_inside_ceiling",
                         (pair is not None and cap is not None
                          and pair <= cap),
                         pair_cost=plan.get("pair_cost"),
                         ceiling=str(cap) if cap is not None else None))
        ff = plan.get("fee_floor") or {}
        rows.append(_row("fee_floor_ok_for_post_only",
                         bool(ff.get("ok")) and bool(
                             ff.get("requires_post_only")),
                         net_if_maker=ff.get("net_if_maker"),
                         net_if_taker=ff.get("net_if_taker")))

    # --- the market must be exactly the one pre-registered
    want = [str(m) for m in (markets or []) if str(m).strip()]
    rows.append(_row("markets_pre_registered", bool(want), markets=want))
    if plan is not None and want:
        rows.append(_row("plan_market_is_pre_registered",
                         str(plan.get("market")) in want,
                         plan_market=plan.get("market")))

    # --- the recorder must be able to produce a usable calibration
    if calibration is None:
        rows.append(_row("recorder_can_calibrate", UNKNOWN,
                         why="no calibration report supplied"))
    else:
        try:
            orders = int(calibration.get("orders") or 0)
        except (TypeError, ValueError, OverflowError):
            orders = 0
        # Orders seen in memory do not prove that their events survived a write.
        # A missing or zero append count must fail closed before live arming.
        appended_raw = calibration.get("events_appended")
        try:
            appended = int(appended_raw) if appended_raw is not None else None
        except (TypeError, ValueError, OverflowError):
            appended = None
        rows.append(_row("recorder_has_observed_something",
                         orders > 0 and appended is not None and appended > 0,
                         orders=orders, events_appended=appended_raw))
        errs = dict(calibration.get("errors") or {})
        try:
            write_failures = int(errs.get("ledger_write_failed", 0))
        except (TypeError, ValueError, OverflowError):
            write_failures = None
        rows.append(_row("recorder_has_no_write_failures",
                         write_failures == 0, errors=errs))
        # ========== DURABILITY IS A READ-BACK, NOT A COUNTER
        #
        # #212 closed the first half of this: `orders > 0` alone could pass
        # with `events_appended` zero or missing. The owner named the
        # remaining half -- "append counts alone are insufficient" -- and it
        # is a property of the counter itself. `CalibrationRecorder.appended` is
        # initialised to 0 in `__init__` and incremented only in `_append`,
        # so it counts what THIS PROCESS wrote and is zeroed by every
        # restart. It can therefore be satisfied by a process whose writes
        # nothing has read back, and refused by a process holding a fully
        # durable ledger it has just reloaded. Neither direction is
        # durability.
        #
        # `CalibrationRecorder.verify_readback()` re-opens the file and reports
        # what parses back, and `events_replayed_from_disk` counts events a
        # PREVIOUS process wrote that this one recovered -- the only field
        # in the report that cannot be true without a surviving write.
        #
        # ABSENT IS UNKNOWN, NOT CLEAN. A calibration report from before
        # this field existed must not read as a pass, and must not read as
        # a failure either: it is a report that cannot answer.
        rb = calibration.get("readback")
        if rb is None:
            rows.append(_row("recorder_ledger_reads_back", UNKNOWN,
                             why="no readback report supplied"))
            rows.append(_row("recorder_events_survive_a_restart", UNKNOWN,
                             why="no readback report supplied"))
        elif not isinstance(rb, Mapping):
            rows.append(_row("recorder_ledger_reads_back", False,
                             why="readback report is not a mapping",
                             got=str(type(rb).__name__)))
            rows.append(_row("recorder_events_survive_a_restart", False,
                             why="readback report is not a mapping"))
        else:
            _readable = rb.get("readable") is True
            _ev = _int_or_none(rb.get("events_readable"))
            _ord = _int_or_none(rb.get("orders_readable"))
            _bad = _int_or_none(rb.get("unreadable_lines"))
            # An UNREADABLE ledger is a refusal and never an empty one: the
            # difference between "nothing was written" and "we cannot tell"
            # is the whole point of this gate.
            rows.append(_row(
                "recorder_ledger_reads_back",
                bool(_readable and _ev is not None and _ev > 0
                     and _ord is not None and _ord > 0
                     and _bad == 0),
                readable=rb.get("readable"), error=rb.get("error"),
                events_readable=rb.get("events_readable"),
                orders_readable=rb.get("orders_readable"),
                unreadable_lines=rb.get("unreadable_lines")))
            _rep = _int_or_none(rb.get("events_replayed_from_disk"))
            rows.append(_row(
                "recorder_events_survive_a_restart",
                bool(_readable and _rep is not None and _rep > 0),
                events_replayed_from_disk=rb.get("events_replayed_from_disk"),
                events_appended_this_process=rb.get(
                    "events_appended_this_process"),
                why=("a per-process append count cannot establish this; "
                     "only events recovered from a previous process can")))

    unknown = [r for r in rows if r["ok"] is UNKNOWN]
    failed = [r for r in rows if r["ok"] is False]
    return {
        "ok": not failed and not unknown,
        "checks": rows,
        "failed": [r["check"] for r in failed],
        "unknown": [r["check"] for r in unknown],
        "labels": ("ok true means the CHECKS passed. It is not an "
                   "authorization: the shadow lock, the absolute deadline "
                   "and the campaign approval are what actually refuse, "
                   "and clearing them is an owner decision. An UNKNOWN is "
                   "never a pass."),
    }


def _int_or_none(v: Any) -> Optional[int]:
    """An int, or None for anything that is not one. Never a silent zero:
    a missing count and a count of zero must be distinguishable, because
    one is "we cannot tell" and the other is "nothing is there"."""
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return None


def exact_window_consistency(*, heartbeat: Mapping[str, Any],
                             plan: Mapping[str, Any],
                             market: str) -> Dict[str, Any]:
    """Fail-closed, read-only market identity gate for a live probe.

    The runner's heartbeat market is the transport's active market_slug.
    A startup/document-only authorization verdict is not market coverage.
    This gate must be called by the live-probe orchestrator before arming;
    it does not itself authorize or submit anything.
    """
    hb = heartbeat or {}
    wanted = str(market or "").strip()
    active = str(hb.get("market") or "").strip()
    planned = str((plan or {}).get("market") or "").strip()
    verdict = hb.get("authorization_preflight") or {}
    evaluated = str(verdict.get("evaluated_market") or "").strip()
    camp = hb.get("campaign") or {}
    checks = [
        _row("exact_target_present", bool(wanted), target=wanted),
        _row("executor_active_market_matches", bool(wanted) and active == wanted,
             active=active, target=wanted),
        _row("plan_market_matches_executor", bool(wanted) and planned == wanted,
             plan_market=planned, target=wanted),
        _row("authorization_evaluated_same_market",
             bool(wanted) and evaluated == wanted,
             evaluated_market=evaluated, target=wanted),
        _row("campaign_permits_exact_window",
             camp.get("permits_this_window") is True,
             permits_this_window=camp.get("permits_this_window")),
        _row("authorization_verdict_passes", verdict.get("ok") is True,
             authorization_ok=verdict.get("ok")),
    ]
    return {"ok": all(row["ok"] is True for row in checks),
            "checks": checks,
            "failed": [row["check"] for row in checks if row["ok"] is not True]}


# ------------------------------------------------------------------ after
def audit(*, markets: Sequence[str], heartbeat: Mapping[str, Any],
          leg_cost: Mapping[str, Any],
          positions: Mapping[str, Any],
          fills: Optional[Sequence[Mapping[str, Any]]] = None,
          latency: Optional[Mapping[str, Any]] = None,
          live_orders: Optional[Mapping[str, Any]] = None,
          restarts: Optional[int] = None,
          fees: Optional[Mapping[str, Any]] = None,
          fill_count: Optional[Mapping[str, Any]] = None,
          gross_quarantine: Optional[Mapping[str, Any]] = None,
          max_book_age_s: Any = "2.0") -> Dict[str, Any]:
    """Is the probe's result a MEASUREMENT, or is it UNINTERPRETABLE?

    `verdict` is the field to read:

      MEASURED         every filled leg has a known basis and the market
                       is absent from the placeholder set
      UNINTERPRETABLE  the basis is unknown or upper-bounded, so the
                       settled number is a BOUND and not a result
      INCOMPLETE       nothing filled, so there is nothing to interpret
    """
    want = [str(m) for m in (markets or []) if str(m).strip()]
    rows: List[Dict[str, Any]] = []
    hb = heartbeat or {}
    realized = hb.get("realized") or {}
    acct = hb.get("accounting") or {}

    # ABSENT IS NOT CLEAN, and this module failed that test. An empty
    # heartbeat has no `unknown_basis_keys`, so "not hit" was true and the
    # audit returned MEASURED on no evidence at all -- silence read as a
    # clean bill of health, which is the worst failure mode available to a
    # module whose whole job is to refuse. So the ABSENCE of the field is
    # UNKNOWN and only a field that is PRESENT AND EMPTY is a pass.
    if "unknown_basis_keys" not in realized:
        rows.append(_row("probe_market_absent_from_unknown_basis_keys",
                         UNKNOWN,
                         why="heartbeat carries no realized."
                             "unknown_basis_keys to check against"))
    else:
        unknown_keys = [str(k) for k in (realized.get("unknown_basis_keys")
                                         or [])]
        hit = [k for k in unknown_keys
               if any(k.startswith(m + "|") or k == m for m in want)]
        rows.append(_row("probe_market_absent_from_unknown_basis_keys",
                         not hit, offending=hit))

    detail = acct.get("unknown_detail")
    if not isinstance(detail, Mapping) or (
            "post_baseline_settlements_not_attested" not in detail):
        rows.append(_row("probe_market_not_priced_at_upper_bound", UNKNOWN,
                         why="heartbeat carries no accounting."
                             "unknown_detail.post_baseline_settlements_"
                             "not_attested to check against"))
    else:
        unattested = [dict(r) for r in
                      (detail.get("post_baseline_settlements_not_attested")
                       or [])]
        bad = [r for r in unattested if str(r.get("market")) in want]
        rows.append(_row("probe_market_not_priced_at_upper_bound", not bad,
                         offending=bad))

    # POSITIVE EVIDENCE, NOT THE ABSENCE OF A MARKER.
    #
    # `adopt_venue_position` prices an adopted leg at
    # qty * UNKNOWN_PRICE_WORST_CASE -- a DOLLAR A SHARE -- so a
    # placeholder leg has `leg_cost > 0` and passed the old check. It was
    # caught only by the separate unknown-basis-keys row, which reads a
    # field the heartbeat may not have caught up on yet. Demonstrated: an
    # adopted leg with an empty `unknown_basis_keys` returned verdict
    # MEASURED.
    #
    # So the basis must now be established POSITIVELY, per leg:
    #
    #   1. our own fills built it        -- fill_count >= 1
    #   2. it is not priced at the bound -- leg_cost != qty * 1.00
    #   3. adoption did not fire         -- no gross_quarantine entry
    #
    # (1) is the primary evidence; `fill_count` is 0 for quantity that
    # arrived from the venue's net, which is what `book_violations` uses
    # to tell an adopted leg from a genuine $0.00 fill. (2) is a
    # corroborating signature and is safe for THIS probe specifically: at
    # depth 2 under a 0.96 pair ceiling no leg can legitimately cost a
    # dollar a share, so that price is diagnostic here even though it
    # would not be in general.
    held, priced, unfilled, at_bound = [], [], [], []
    for m in want:
        for side in ("UP", "DOWN"):
            k = "%s|%s" % (m, side)
            q = _d((positions or {}).get(k))
            if q is None or q <= 0:
                continue
            held.append(k)
            c = _d((leg_cost or {}).get(k))
            if c is not None and c > 0:
                priced.append(k)
            if int((fill_count or {}).get(k, 0) or 0) < 1:
                unfilled.append(k)
            if c is not None and q is not None and c == q * D(1):
                at_bound.append(k)
    rows.append(_row("every_filled_leg_has_a_cost_basis",
                     set(held) == set(priced),
                     held=held, priced=priced))
    if fill_count is None:
        rows.append(_row("every_held_leg_was_built_by_our_own_fills",
                         UNKNOWN, why="no fill_count supplied"))
    else:
        rows.append(_row("every_held_leg_was_built_by_our_own_fills",
                         not unfilled, without_fills=unfilled,
                         note=("fill_count 0 is quantity that arrived from "
                               "the venue's net, not a leg we filled")))
    rows.append(_row("no_leg_is_priced_at_the_one_dollar_bound",
                     not at_bound, at_bound=at_bound,
                     note=("qty * 1.00 is the adopted-placeholder "
                           "signature; no depth-2 leg under a 0.96 pair "
                           "ceiling can legitimately cost that")))
    qrows = {m: (gross_quarantine or {}).get(m) for m in want}
    fired = [m for m, v in qrows.items() if v]
    rows.append(_row("adoption_did_not_replace_our_book", not fired,
                     markets=fired,
                     recovery=({m: {"preserved_leg_cost":
                                    (qrows[m] or {}).get("leg_cost"),
                                    "preserved_positions":
                                    (qrows[m] or {}).get("positions"),
                                    "why": (qrows[m] or {}).get("why")}
                                for m in fired} or None),
                     note=("the pre-adoption gross book is preserved "
                           "verbatim in gross_quarantine, so the basis is "
                           "RECOVERABLE -- but it is not automatically the "
                           "truth: at 0230z our gross was the "
                           "double-booked one and the venue was right. "
                           "Reconcile; do not quote either side")))

    vids = [str(f.get("venue_order_id") or "") for f in (fills or [])]
    rows.append(_row("every_fill_carries_a_venue_order_id",
                     bool(fills) and all(vids),
                     fills=len(fills or []), missing=vids.count("")))
    # EVERY VENUE ID MUST BE DISTINCT. Two fills reported under one id is
    # either a double-booked poll or two orders we cannot tell apart, and
    # both break the reconciliation the settled figure rests on.
    rows.append(_row("venue_order_ids_are_distinct",
                     (len(set(vids)) == len(vids)) if vids else UNKNOWN,
                     ids=len(vids), distinct=len(set(vids))))

    # --- DURABLE RESTART. A probe interrupted mid-window and resumed must
    # reconcile against the venue by exact id, and must not carry an order
    # whose state it could not establish. `nothing_held` reading OUR ledger
    # instead of the venue is what cost us 1445z, so an UNCERTAIN order is
    # a refusal here rather than a footnote.
    live = dict(live_orders or {})
    uncertain = [c for c, o in live.items()
                 if "UNCERTAIN" in str((o or {}).get("state") or "").upper()]
    rows.append(_row("no_order_is_in_an_UNCERTAIN_state", not uncertain,
                     uncertain=uncertain, live=len(live)))
    no_vid = [c for c, o in live.items()
              if not str((o or {}).get("venue_order_id") or "").strip()]
    rows.append(_row("every_live_order_has_a_venue_id_to_cancel_by",
                     not no_vid, without_id=no_vid))
    if restarts is None:
        rows.append(_row("restart_boundaries_reported", UNKNOWN,
                         why="no restart count supplied"))
    else:
        # A RECORD THAT SPANS A RESTART IS STILL EVIDENCE; it simply may
        # not be read as one continuous observation. So this is reported,
        # not failed, and the caller decides.
        rows.append(_row("restart_boundaries_reported", True,
                         orders_straddling_a_restart=int(restarts),
                         note=("a straddled record is evidence but not a "
                               "continuous observation")))

    # --- FEES AND REBATES, actual kept apart from estimated.
    if fees is None:
        rows.append(_row("actual_fees_and_rebates_present", UNKNOWN,
                         why="no fee record supplied"))
    else:
        act_fee = _d(fees.get("fee_usd"))
        act_reb = _d(fees.get("rebate_usd"))
        src = str(fees.get("source") or "")
        rows.append(_row("actual_fees_and_rebates_present",
                         act_fee is not None and act_reb is not None
                         and bool(src),
                         fee_usd=fees.get("fee_usd"),
                         rebate_usd=fees.get("rebate_usd"), source=src))
        # CREDITED, NOT ACCRUED. $1.48 is currently accrued and not paid,
        # and accrued rebate counted as income is how a receivable becomes
        # a performance claim.
        rows.append(_row("rebate_is_credited_not_accrued",
                         str(fees.get("state") or "").upper()
                         not in ("PENDING", "SKIPPED", "ACCRUED"),
                         state=fees.get("state")))
        est = _d((fees.get("estimate") or {}).get("maker_rebate_usd")
                 if isinstance(fees.get("estimate"), Mapping) else None)
        rows.append(_row("estimated_and_actual_rebate_not_summed",
                         True, estimate=str(est) if est is not None else None,
                         actual=fees.get("rebate_usd"),
                         note=("reported side by side and never added; the "
                               "estimate is a schedule figure and only the "
                               "actual is income")))

    if latency is None:
        rows.append(_row("book_age_at_evaluation_measured", UNKNOWN,
                         why="no latency report supplied"))
    else:
        ages = (latency.get("book_age_at_decision_s") or {})
        n = int(ages.get("n") or 0)
        p95 = _d(ages.get("p95"))
        lim = _d(max_book_age_s)
        rows.append(_row("book_age_at_evaluation_measured", n > 0,
                         n=n, median=ages.get("median"), p95=ages.get("p95")))
        # PRE-REGISTERED REFUSAL, independent of the economics.
        rows.append(_row("p95_book_age_within_requirement",
                         (p95 is not None and lim is not None
                          and p95 <= lim) if n > 0 else UNKNOWN,
                         p95=ages.get("p95"), limit=str(lim)))
        f2d = latency.get("detection_to_first_decision_s")
        rows.append(_row("first_fill_to_second_leg_decision_measured",
                         f2d is not None, seconds=f2d))

    failed = [r for r in rows if r["ok"] is False]
    unk = [r for r in rows if r["ok"] is UNKNOWN]
    # EVERY basis row must be a positive True. An UNKNOWN among them is
    # not a pass, which is the fix above expressed in the verdict.
    BASIS_CHECKS = (
        "probe_market_absent_from_unknown_basis_keys",
        "probe_market_not_priced_at_upper_bound",
        "every_filled_leg_has_a_cost_basis",
        "every_held_leg_was_built_by_our_own_fills",
        "no_leg_is_priced_at_the_one_dollar_bound",
        "adoption_did_not_replace_our_book")
    basis_rows = [r for r in rows if r["check"] in BASIS_CHECKS]
    basis_ok = (len(basis_rows) == len(BASIS_CHECKS)
                and all(r["ok"] is True for r in basis_rows))
    if not held:
        verdict = "INCOMPLETE"
    elif basis_ok:
        verdict = "MEASURED"
    else:
        verdict = "UNINTERPRETABLE"
    return {
        "verdict": verdict,
        "ok": verdict == "MEASURED" and not failed and not unk,
        "checks": rows,
        "failed": [r["check"] for r in failed],
        "unknown": [r["check"] for r in unk],
        "labels": ("UNINTERPRETABLE is not a disappointing result, it is "
                   "the ABSENCE of one: a settled figure built on an "
                   "unknown cost basis is an upper bound and must never be "
                   "reported as realized P&L. INCOMPLETE means nothing "
                   "filled. Only MEASURED may be quoted as a settled "
                   "outcome, and only after settlement."),
    }
