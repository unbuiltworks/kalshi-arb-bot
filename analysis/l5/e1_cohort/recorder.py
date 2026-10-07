"""Append-only recorder for the E1 cohort. Provenance on every row.

ONE FILE PER LEDGER, OPENED "a", fsynced, never rewritten and never
truncated. There is no update path and no delete path in this module, so a
row once written is a row forever; a correction is a NEW row carrying
`corrects`, which is how an append-only ledger says it changed its mind.

FOUR LEDGERS, deliberately separate:

  attempts.jsonl      one row per ELIGIBLE attempt, with everything the
                      mandate lists
  events.jsonl        the lifecycle tape: submitted, acknowledged, resting,
                      fill, partial, cancel, cancel-ack, race, deadman
  venue_money.jsonl   fees and rebates AS THE VENUE REPORTS THEM. Nothing
                      modelled is permitted in this ledger at all
  settlement.jsonl    settlement and cash confirmation, the only place a
                      row may ever carry `label: REALIZED`

WHY FEES ARE A SEPARATE LEDGER. CLAUDE.md requires maker rebates, taker
rebates and rewards to stay separate from trading P&L, and requires that
nothing be assumed credited until it is credited. A modelled fee and a
credited fee in one table become indistinguishable within a week, so they
do not go in one table. `record_venue_money` REFUSES a row whose source is
not a venue report.

LABELLING. Every row carries a `label` from the strict lifecycle
vocabulary, and `REALIZED` is reachable only through `record_settlement`
with a confirmed cash source. `record_attempt` cannot emit it; the
constant it would need is not exported to it.
"""
from __future__ import annotations

import json
import os
import time
from decimal import Decimal as D
from typing import Any, Dict, Mapping, Optional

from analysis.l5.e1_cohort.spec import (E1_FINGERPRINT, FEE_SCHEDULE_FINGERPRINT,
                                        PROBE, assert_frozen)

SCHEMA_VERSION = "l5_e1_cohort/1"

ATTEMPTS = "attempts.jsonl"
EVENTS = "events.jsonl"
VENUE_MONEY = "venue_money.jsonl"
SETTLEMENT = "settlement.jsonl"

#: The strict accounting lifecycle from CLAUDE.md, in order.
LABELS = ("HISTORICAL_REPLAY", "SHADOW", "LIVE_PROBE", "SUBMITTED", "FILLED",
          "SETTLED", "REALIZED")

#: The only label a settlement row may carry once cash is confirmed.
REALIZED = "REALIZED"

#: Sources that count as the venue telling us about money. Anything else is
#: a model, and a model does not belong in `venue_money.jsonl`.
VENUE_MONEY_SOURCES = ("venue_fill_report", "venue_fee_statement",
                       "venue_rebate_credit", "venue_settlement_statement",
                       "venue_balance_statement_with_source_identity")

TIMING_WEBSOCKET = "websocket_strict"
TIMING_REST = "rest_same_second_reachability"


class RecorderRefused(RuntimeError):
    """A row this ledger is not allowed to hold."""


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def envelope(*, kind: str) -> Dict[str, Any]:
    """Provenance carried by EVERY row, without exception."""
    return {
        "schema_version": SCHEMA_VERSION,
        "probe": PROBE,
        "spec_fingerprint": E1_FINGERPRINT,
        "fee_schedule_fingerprint": FEE_SCHEDULE_FINGERPRINT,
        "kind": kind,
        "written_utc": _utc(),
        "written_unix": time.time(),
        "written_monotonic_ns": time.monotonic_ns(),
        "pid": os.getpid(),
        "deployment": os.getenv("RAILWAY_DEPLOYMENT_ID"),
        "venue": "polymarket_us",
        "timing_authority": TIMING_WEBSOCKET,
        "rest_role": TIMING_REST,
        "rest_may_order_the_tape": False,
        "append_only": True,
    }


class Recorder:
    """Append-only, fsynced, four ledgers. No update path, no delete path."""

    def __init__(self, root: str) -> None:
        assert_frozen()
        self.root = root
        os.makedirs(root, exist_ok=True)

    # ------------------------------------------------------------- the write
    def _append(self, name: str, row: Mapping[str, Any]) -> Dict[str, Any]:
        out = dict(row)
        path = os.path.join(self.root, name)
        line = json.dumps(out, sort_keys=True, separators=(",", ":"),
                          default=str)
        with open(path, "a", encoding="utf8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return out

    def read(self, name: str) -> list:
        path = os.path.join(self.root, name)
        if not os.path.exists(path):
            return []
        rows = []
        with open(path, "r", encoding="utf8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    # ----------------------------------------------------------- attempt row
    def record_attempt(self, *, window: int, slot: int, market: str,
                       ws_state: Mapping[str, Any],
                       first_leg: Mapping[str, Any],
                       queue: Mapping[str, Any],
                       timestamps: Mapping[str, Any],
                       fill: Mapping[str, Any],
                       complement: Optional[Mapping[str, Any]],
                       outcome: Mapping[str, Any],
                       cancellations: Any,
                       markouts: Mapping[str, Any],
                       label: str = "SUBMITTED",
                       extra: Optional[Mapping[str, Any]] = None
                       ) -> Dict[str, Any]:
        """One row per ELIGIBLE attempt, carrying everything the mandate
        lists. `label` may not be REALIZED here: realization is a property
        of settlement and cash, not of an attempt."""
        if label == REALIZED:
            raise RecorderRefused(
                "an attempt row may never be labelled REALIZED. Realization "
                "requires settlement AND cash confirmation; use "
                "record_settlement.")
        if label not in LABELS:
            raise RecorderRefused("unknown lifecycle label %r" % (label,))
        if ws_state.get("timing_authority") != TIMING_WEBSOCKET:
            raise RecorderRefused(
                "the decision-time book state must carry "
                "timing_authority=%r. REST is reachability only and may "
                "never order the tape." % TIMING_WEBSOCKET)
        if not ws_state.get("both_tokens_streaming"):
            raise RecorderRefused(
                "an attempt may not be recorded without both tokens "
                "streaming on the live two-token websocket")
        row = envelope(kind="attempt")
        row.update({
            "window": window,
            "slot": slot,
            "market": market,
            "label": label,
            # the exact live two-token websocket state at decision time
            "ws_state_at_decision": dict(ws_state),
            # side, price, quantity
            "first_leg": dict(first_leg),
            # size resting AT our price and AHEAD of it
            "queue_state": dict(queue),
            # submitted / acknowledged / resting, and times to fill
            "timestamps": dict(timestamps),
            # fill / PARTIAL / no-fill, with filled_qty
            "first_leg_fill": dict(fill),
            # complement price, submission timing, fill/partial/no-fill
            "complement": dict(complement) if complement else None,
            # COMPLETE vs RESIDUAL, pair cost, residual size and duration
            "outcome": dict(outcome),
            # every cancellation, and whether a fill raced it
            "cancellations": cancellations,
            # which way the book moved after our fill, from the ws tape
            "markouts": dict(markouts),
            "REALIZED_CLAIM": False,
            "NOTE": ("SUBMITTED is not a fill; FILLED is not settled; "
                     "settled is not realized until cash is confirmed."),
        })
        if extra:
            row["extra"] = dict(extra)
        return self._append(ATTEMPTS, row)

    # ------------------------------------------------------------- event row
    def record_event(self, *, window: int, slot: int, event: str,
                     detail: Optional[Mapping[str, Any]] = None,
                     venue_ts: Optional[Any] = None,
                     monotonic_ns: Optional[int] = None) -> Dict[str, Any]:
        row = envelope(kind="event")
        row.update({"window": window, "slot": slot, "event": event,
                    "venue_timestamp": venue_ts,
                    "ordering_monotonic_ns": monotonic_ns,
                    "detail": dict(detail or {})})
        return self._append(EVENTS, row)

    # ------------------------------------------------------- venue money row
    def record_venue_money(self, *, window: int, slot: int, kind: str,
                           amount_usd: Any, source: str,
                           source_identity: Mapping[str, Any],
                           detail: Optional[Mapping[str, Any]] = None
                           ) -> Dict[str, Any]:
        """Fees and rebates AS THE VENUE REPORTED THEM. A modelled number is
        refused outright, and a cashflow without sufficient source identity
        is refused and is itself a FAIL trigger."""
        if source not in VENUE_MONEY_SOURCES:
            raise RecorderRefused(
                "venue_money.jsonl holds only venue-reported money. %r is "
                "not one of %s -- a modelled or estimated figure does not "
                "belong in this ledger at all."
                % (source, list(VENUE_MONEY_SOURCES)))
        ident = dict(source_identity or {})
        if not ident.get("sufficient"):
            raise RecorderRefused(
                "a cashflow without sufficient source identity may not be "
                "recorded as venue money. Per E1_COHORT_DESIGN.md this is a "
                "FAIL trigger and an instrument fault, not economics.")
        row = envelope(kind="venue_money")
        row.update({"window": window, "slot": slot, "money_kind": kind,
                    "amount_usd": str(D(str(amount_usd))),
                    "source": source, "source_identity": ident,
                    "modelled": False,
                    "kept_separate_from_trading_pnl": True,
                    "detail": dict(detail or {})})
        return self._append(VENUE_MONEY, row)

    # -------------------------------------------------------- settlement row
    def record_settlement(self, *, window: int, slot: int, market: str,
                          settled: bool, cash_confirmed: bool,
                          cash_usd: Optional[Any],
                          source_identity: Mapping[str, Any],
                          detail: Optional[Mapping[str, Any]] = None
                          ) -> Dict[str, Any]:
        """The ONLY place a row may be labelled REALIZED, and only when the
        market settled AND the cash is confirmed from an identified
        source."""
        ident = dict(source_identity or {})
        realized = bool(settled and cash_confirmed and ident.get("sufficient"))
        if cash_usd is not None and not ident.get("sufficient"):
            raise RecorderRefused(
                "a cashflow of %s has no sufficient source identity. Per "
                "E1_COHORT_DESIGN.md estimand 8 this is an instrument fault "
                "and a FAIL trigger." % (cash_usd,))
        row = envelope(kind="settlement")
        row.update({
            "window": window, "slot": slot, "market": market,
            "settled": bool(settled),
            "cash_confirmed": bool(cash_confirmed),
            "cash_usd": None if cash_usd is None else str(D(str(cash_usd))),
            "source_identity": ident,
            "label": REALIZED if realized else
                     ("SETTLED" if settled else "FILLED"),
            "REALIZED_CLAIM": realized,
            "NOTE": ("nothing is labelled REALIZED before settlement AND "
                     "cash confirmation from an identified source"),
            "detail": dict(detail or {}),
        })
        return self._append(SETTLEMENT, row)
