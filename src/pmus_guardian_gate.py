"""The mandatory gate: nothing is submitted until the venue has been read.

Separate from `pmus_order_guardian` so the policy is in one readable place
and can be tested without a transport. The money path holds the store; this
holds the rule about when the store permits trading.

WHY A HARD GATE AND NOT A WARNING. R6's orphan existed because nothing
checked. A gate that can be bypassed by a flag is the same gate.
"""
from __future__ import annotations

import signal
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from src import pmus_order_guardian as G


class SubmissionBlocked(RuntimeError):
    """Raised instead of submitting. Never caught inside this module."""


def open_for_business(store: G.OwnershipStore, *,
                      list_open: Callable[[], Sequence[Mapping[str, Any]]],
                      read_order: Callable[[str], Mapping[str, Any]],
                      list_recent: Optional[Callable[
                          [], Sequence[Mapping[str, Any]]]] = None,
                      ) -> dict:
    """Run startup reconciliation and decide whether submission may begin.

    Order matters: id-less intents are bound FIRST, because an intent bound
    by attribute then needs its terminal state read like any other order.
    """
    unbound = (G.reconcile_unbound(store, list_recent)
               if list_recent is not None else
               {"skipped": "NO_RECENT_ORDER_READER"})
    recon = G.startup_reconcile(store, list_open=list_open,
                                read_order=read_order)
    verdict = store.may_submit()
    return {"unbound": unbound, "reconcile": recon, "verdict": verdict,
            "may_submit": bool(verdict["may_submit"]),
            "LABEL": "startup reconciliation is mandatory; submission is "
                     "refused until the venue has been read and is clean"}


def require_open_for_business(report: Mapping[str, Any]) -> None:
    """Fail closed. Call immediately before the first submission."""
    if not report.get("may_submit"):
        raise SubmissionBlocked(
            "startup reconciliation did not clear: %s"
            % (report.get("verdict") or {}))


def arm_shutdown(store: G.OwnershipStore, *,
                 cancel: Callable[[str], Any],
                 read_order: Callable[[str], Mapping[str, Any]],
                 on_report: Optional[Callable[[dict], None]] = None,
                 ) -> dict:
    """Cancel-and-verify on SIGTERM/SIGINT, and say what is not covered.

    This is the GRACEFUL path only. A hard kill runs nothing, which is why
    `open_for_business` above is mandatory for the NEXT process.
    """
    def _handler(signum: int) -> None:
        try:
            rep = G.cancel_outstanding(store, cancel=cancel,
                                       read_order=read_order)
            rep["signal"] = signum
        except BaseException as exc:                           # noqa: BLE001
            rep = {"signal": signum, "error": type(exc).__name__}
            store.mark_uncertain("SHUTDOWN_SWEEP_FAILED:%s"
                                 % type(exc).__name__)
        if on_report is not None:
            try:
                on_report(rep)
            except BaseException:                              # noqa: BLE001
                pass

    wired = G.install_signal_handlers(_handler)
    return {**wired, "graceful_only": True,
            "hard_kill_recovery": "the next process's open_for_business"}
