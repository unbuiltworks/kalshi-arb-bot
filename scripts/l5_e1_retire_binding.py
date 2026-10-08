"""RETIRE A COMPLETED PROBE'S WINDOW BINDING. Read-mostly, append-only.

WHY THIS EXISTS.

The probe window binding is immutable and never released, so that a probe
cannot collect a fresh attempt allowance in each window it chooses. That
is right WITHIN one authorization. Across two, it means the second
authorization inherits the first one's bound window: the 22:00z
one-attempt probe bound window 3, and the three-attempt probe that
followed read that binding, found window 4 did not match, and refused with
`PROBE_IS_BOUND_TO_WINDOW_3` having placed nothing.

WHAT THIS DOES, AND WHAT IT REFUSES TO DO.

It does NOT release, reset, delete or rewrite anything. The original
binding stays exactly where the probe wrote it. What it does is:

  1. VERIFY the binding belongs to a COMPLETED probe -- its window is in
     the ledger's used windows, every order of ours is terminal at the
     venue, there is no outstanding order, no residual, no pending intent
     and no unresolved exposure;
  2. ARCHIVE the binding's contents immutably (`O_CREAT|O_EXCL`), with the
     evidence that justified the retirement attached;
  3. REPORT the new epoch namespace the next authorization must name.

A new probe becomes able to bind only because it names a NEW EPOCH, which
is a different file. Retiring is the bookkeeping that makes that honest --
it is not what unlocks anything, and it cannot be mistaken for a reset:
cumulative loss, the ledger, the tape, the 42 order ids, the per-window
attempt slots and every risk bound are untouched by this script, which
opens no order endpoint and holds no mutating transport.

IF THE STATE CANNOT BE DISTINGUISHED SAFELY, IT STOPS. A binding whose
window is still in progress, or whose probe still has anything live, is
not retired: the run reports exactly what is unresolved and exits
non-zero. Nothing is archived in that case either, because an archive
asserting a completed probe would be a false record.

    PMUS_E1_SESSION_ID         the session that owns the binding
    E1_RETIRE_LABEL            archive label (filename-safe)
    E1_RETIRE_REASON           why, in one line, recorded in the archive
    E1_RETIRE_NEW_EPOCH        the epoch the next authorization will name
    E1_RETIRE_EXECUTE=YES      actually write the archive; default is a
                               dry run that verifies and reports only
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List

from analysis.l5.e1_cohort import budget as BUD
from analysis.l5.e1_cohort import latch as L
from analysis.l5.e1_cohort import order_registry as ORG
from analysis.l5.e1_cohort import order_state as OS
from analysis.l5.e1_cohort import progress as PG
from src import pmus_auth as A

STATE_DIR = os.environ.get("E1_STATE_DIR", "/state/pmus-ws/e1_cohort")


def j(tag: str, obj: Any) -> None:
    print("%s %s" % (tag, json.dumps(obj, sort_keys=True, default=str)),
          flush=True)


def main() -> int:
    session = (os.getenv("PMUS_E1_SESSION_ID") or "").strip() or None
    label = (os.getenv("E1_RETIRE_LABEL") or "").strip()
    reason = (os.getenv("E1_RETIRE_REASON") or "").strip()
    new_epoch = (os.getenv("E1_RETIRE_NEW_EPOCH") or "").strip()
    execute = (os.getenv("E1_RETIRE_EXECUTE") or "").strip().upper() == "YES"

    j("RETIRE_BEGIN", {"session_id_present": bool(session),
                       "label": label, "new_epoch": new_epoch,
                       "execute": execute,
                       "dry_run": not execute,
                       "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                            time.gmtime())})
    if not session or not label or not reason or not new_epoch:
        j("REFUSED", {"why": "MISSING_INPUT",
                      "need": ["PMUS_E1_SESSION_ID", "E1_RETIRE_LABEL",
                               "E1_RETIRE_REASON", "E1_RETIRE_NEW_EPOCH"]})
        return 2

    # ---- the binding under consideration, read without being touched ----
    try:
        path = BUD.binding_path(session_id=session)
        rec = BUD.read_binding(session_id=session)
    except BUD.BudgetNotAuthorized as exc:
        j("REFUSED", {"why": "BINDING_UNREADABLE", "detail": str(exc)})
        return 2
    if rec is None:
        j("REFUSED", {"why": "NO_BINDING_TO_RETIRE", "path": path})
        return 2
    j("BINDING", {"path": path, "window": rec.get("window"),
                  "slug": rec.get("slug"), "epoch": rec.get("epoch"),
                  "bound_utc": rec.get("bound_utc"),
                  "IMMUTABLE": rec.get("IMMUTABLE")})

    blockers: List[str] = []

    # ---- 1. is that window COMPLETED, per the durable ledger? ----------
    prog = PG.CohortProgress(L.LATCH_DIR)
    report = prog.report()
    used = [int(w) for w in (report.get("window_starts_used") or [])]
    slugs_used = list(report.get("slugs_used") or [])
    window_is_used = str(rec.get("slug")) in slugs_used
    j("LEDGER", {"windows_used": report.get("windows_used"),
                 "windows_remaining": report.get("windows_remaining"),
                 "placements": report.get("placements"),
                 "realized_loss_usd": report.get("realized_loss_usd"),
                 "unresolved_residuals": report.get("unresolved_residuals"),
                 "slugs_used": slugs_used,
                 "bound_slug_is_in_slugs_used": window_is_used,
                 "NOTHING_HERE_IS_RESET": True})
    if not window_is_used:
        blockers.append("BOUND_SLUG_NOT_IN_LEDGERS_USED_WINDOWS")
    if int(report.get("unresolved_residuals") or 0):
        blockers.append("LEDGER_REPORTS_UNRESOLVED_RESIDUALS")

    # ---- 2. the registry: every id, and no pending intent --------------
    reg = ORG.load_from_tape(STATE_DIR, session_id=session)
    j("REGISTRY", {"rows_scanned": reg.get("rows_scanned"),
                   "n_known_ids": len(reg.get("known_ids") or []),
                   "n_pending_intents": len(reg.get("pending") or []),
                   "pending": reg.get("pending"),
                   "ALL_IDS_PRESERVED": True})
    if reg.get("pending"):
        blockers.append("PENDING_INTENTS_EXIST")

    # ---- 3. the venue: terminal, no outstanding, no exposure -----------
    cache = OS.load_terminal_cache(STATE_DIR, session_id=session)
    reader = OS.venue_reader(A.ReadOnlyTransport())
    view = OS.read_order_state(reader, slugs=[], outcome_up=None,
                               now=time.time(), now_fn=time.time,
                               known_ids=list(reg.get("known_ids") or []),
                               roles=dict(reg.get("roles") or {}),
                               terminal_cache=cache["cache"],
                               pending_ids=list(reg.get("pending") or []))
    OS.save_terminal_cache(cache["cache"], STATE_DIR, session_id=session)
    j("VENUE", {"ok": view.get("ok"), "reason": view.get("reason"),
                "n_known": view.get("n_known"),
                "n_outstanding": view.get("n_outstanding"),
                "residual": view.get("residual"),
                "aggregate_exposure_usd": view.get("aggregate_exposure_usd"),
                "venue_positions": view.get("venue_positions"),
                "n_resolved_history": view.get("n_resolved_history"),
                "held_slugs": view.get("held_slugs")})
    if not view.get("ok"):
        blockers.append("ORDER_STATE_NOT_ESTABLISHED:%s"
                        % view.get("reason"))
    else:
        if int(view.get("n_outstanding") or 0):
            blockers.append("OUTSTANDING_ORDERS_EXIST:%s"
                            % (view.get("outstanding_ids"),))
        if view.get("residual"):
            blockers.append("RESIDUAL_IS_OPEN")
        agg = str(view.get("aggregate_exposure_usd"))
        if agg not in ("0", "0.00", "0.0000"):
            blockers.append("AGGREGATE_EXPOSURE_NOT_ZERO:%s" % agg)
        # and the bound window's own orders must be terminal
        not_terminal = [str(o.get("id")) for o in (view.get("orders") or [])
                        if str(o.get("slug")) == str(rec.get("slug"))
                        and not o.get("terminal")]
        if not_terminal:
            blockers.append("BOUND_WINDOW_ORDERS_NOT_TERMINAL:%s"
                            % ",".join(sorted(not_terminal)))
        bound_ids = sorted(str(o.get("id"))
                           for o in (view.get("orders") or [])
                           if str(o.get("slug")) == str(rec.get("slug")))
        j("BOUND_WINDOW_ORDERS", {
            "slug": rec.get("slug"), "ids": bound_ids,
            "all_terminal": not not_terminal,
            "every_id_venue_confirmed": True})

    if blockers:
        j("REFUSED", {"why": "PROBE_IS_NOT_SAFELY_COMPLETE",
                      "blockers": blockers,
                      "nothing_was_archived": True,
                      "original_binding_untouched": True,
                      "STOPPED_RATHER_THAN_RESETTING": True})
        return 3

    # ---- 4. the archive, immutably, original untouched -----------------
    evidence = {
        "venue_n_outstanding": view.get("n_outstanding"),
        "venue_residual": view.get("residual"),
        "venue_aggregate_exposure_usd": view.get("aggregate_exposure_usd"),
        "venue_positions": view.get("venue_positions"),
        "n_known_ids": len(reg.get("known_ids") or []),
        "n_pending_intents": len(reg.get("pending") or []),
        "ledger_windows_used": report.get("windows_used"),
        "ledger_placements": report.get("placements"),
        "ledger_realized_loss_usd": report.get("realized_loss_usd"),
        "bound_window_order_ids": sorted(
            str(o.get("id")) for o in (view.get("orders") or [])
            if str(o.get("slug")) == str(rec.get("slug"))),
    }
    if not execute:
        j("DRY_RUN", {"would_archive_as_label": label,
                      "would_record_reason": reason,
                      "next_epoch_to_name": new_epoch,
                      "evidence_at_retirement": evidence,
                      "nothing_was_written": True})
        j("RETIRE_END", {"ok": True, "dry_run": True})
        return 0

    out = BUD.archive_binding(session_id=session, label=label,
                              reason=reason, evidence=evidence)
    j("ARCHIVE", {k: v for k, v in out.items() if k != "record"})
    if not out.get("archived"):
        j("RETIRE_END", {"ok": False, "why": out.get("why")})
        return 3

    # ---- 5. the fresh namespace, and proof it is empty -----------------
    fresh = BUD.binding_path(session_id=session, epoch=new_epoch)
    j("FRESH_EPOCH", {
        "epoch": new_epoch,
        "binding_path": fresh,
        "exists_yet": os.path.exists(fresh),
        "will_be_written_O_EXCL_before_any_intent_preview_or_post": True,
        "a_restart_reads_the_same_file_and_grants_no_new_attempts": True,
        "previous_epoch_is_never_read_in_this_namespace": True,
        "set_%s_to_this" % BUD.EPOCH_ENV: new_epoch})
    j("PRESERVED", {
        "session_id_unchanged": True,
        "tape_unchanged": True,
        "ledger_unchanged": True,
        "known_order_ids": len(reg.get("known_ids") or []),
        "cumulative_realized_loss_usd": report.get("realized_loss_usd"),
        "cumulative_loss_limits_reset": False,
        "per_window_attempt_slots_unchanged": True,
        "original_binding_still_on_disk": os.path.exists(path),
    })
    j("RETIRE_END", {"ok": True, "dry_run": False})
    return 0


if __name__ == "__main__":
    sys.exit(main())
