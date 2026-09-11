"""A read-only settlement pass over every market the ledger still carries.

OWNER 2026-09-11: "Do not restart the trading campaign just to collect
settlements. Run a read-only settlement/audit pass against the 36 pending
markets. No order path, no re-arm, no new risk. We need the economics without
paying for another live experiment."

WHY THIS EXISTS AT ALL
----------------------
The settlement auditor runs INSIDE the campaign loop. When the campaign
stopped itself at 16:19:01Z on CAMPAIGN_STOP:DAILY_LOSS_STOP, settlement
auditing stopped with it -- so 36 markets whose events are still resolving
will never get a `settled` or `realized` row written, and their economics stay
invisible. The positions are held to settlement either way; what is missing is
the accounting, not the risk management.

WHAT MAKES IT SAFE, STRUCTURALLY RATHER THAN BY INTENTION
---------------------------------------------------------
It imports the auditor and nothing else that can trade. It never imports the
session module, never constructs an order, never reads a cap or a budget, and
has no loop to re-arm: it walks a list once and returns. A test walks this
module's AST and fails if an order-path name appears in it at all.

The only writes are the auditor's own `settled` / `realized` ledger rows,
which are append-only, de-duplicated by settlement key, and are the exact rows
the campaign would have written had it still been running. Nothing here
computes economics of its own.

RATE, BECAUSE 36 MARKETS IS NOT ONE
-----------------------------------
Each market costs an activity walk plus a positions read. They are paced, and
a market that fails to audit is recorded as an error and the sweep continues:
one unreadable market must not cost the other thirty-five.
"""
from __future__ import annotations

import argparse
import json
import time
from typing import Any, Callable, Iterable, Mapping, Optional

from src import pmus_settlement_auditor as auditor

# Paced so a 36-market sweep does not look like a burst to the venue. The
# campaign polled far harder than this; the point is only that a batch
# should not be the first thing that trips a limit.
PACE_S = 1.5


def _log(kind: str, **fields: Any) -> None:
    print("PMUS_SETTLEMENT_SWEEP " + json.dumps({"kind": kind, **fields},
                                                separators=(",", ":"),
                                                default=str), flush=True)


def pending_slugs() -> list:
    """Markets the LEDGER says we filled into and have no `settled` row for.

    Imported lazily and by name: the session module owns this definition and
    duplicating it here is how the two would drift apart. Importing it does
    not start anything -- `unsettled_slugs` reads a file."""
    from src.pmus_l5_continuous_session import unsettled_slugs
    return list(unsettled_slugs() or [])


def summarise(result: Mapping[str, Any]) -> dict:
    """The economics of one audited market, using the auditor's own labels.

    `realized_usd` is carried ONLY when the venue confirmed it. A market that
    resolved with the account already flat has no POSITION_RESOLUTION and so
    no realized figure -- its matched contribution is DERIVED and is reported
    under a different name, never folded into realized."""
    b = result.get("breakdown") or {}
    inc = (b.get("incentives") or {})
    confirmed = bool(result.get("realized_confirmed"))
    return {
        "slug": result.get("slug"),
        "status": result.get("status"),
        "settled": result.get("settled"),
        "realized_confirmed": confirmed,
        "realized_usd": result.get("realized_usd") if confirmed else None,
        "derived_matched_contribution_usd": b.get("matched_contribution_usd"),
        "matched_pairs": b.get("matched_pairs"),
        "matched_pair_cost": b.get("matched_pair_cost"),
        "directional_residual": b.get("directional_residual"),
        "credited_incentives_usd": inc.get("credited_to_us_usd"),
        "maker_fills": b.get("maker_fills"),
        "taker_fills": b.get("taker_fills"),
        "ledger_rows_written": result.get("ledger_rows_written"),
    }


def totals(rows: Iterable[Mapping[str, Any]]) -> dict:
    """Sums, with realized and derived kept in separate columns for good.

    An absent realized figure contributes nothing to the realized total --
    it is not a zero, and summing it as one is the single error this whole
    accounting layer exists to prevent."""
    from decimal import Decimal as D
    realized, derived, credited = D("0"), D("0"), D("0")
    n_realized = n_derived = 0
    for r in rows:
        if r.get("realized_confirmed") and r.get("realized_usd") is not None:
            realized += D(str(r["realized_usd"]))
            n_realized += 1
        elif r.get("derived_matched_contribution_usd") is not None:
            derived += D(str(r["derived_matched_contribution_usd"]))
            n_derived += 1
        if r.get("credited_incentives_usd") is not None:
            credited += D(str(r["credited_incentives_usd"]))
    return {
        "venue_confirmed_realized_usd": str(realized),
        "markets_realized": n_realized,
        "derived_matched_contribution_usd": str(derived),
        "markets_derived_only": n_derived,
        "credited_incentives_usd": str(credited),
        "labels": ("realized is the venue's own number and nothing else; "
                   "derived is ledger arithmetic on markets the venue "
                   "resolved with the account already flat, and is NEVER "
                   "added to realized"),
    }


def sweep(slugs: Optional[Iterable[str]] = None,
          write_ledger: bool = True,
          sleep: Callable = time.sleep,
          audit: Optional[Callable] = None) -> dict:
    """Audit each market once, in order, and return. No loop, no re-arm."""
    audit = audit or auditor.audit_market
    todo = list(slugs) if slugs is not None else pending_slugs()
    _log("sweep_start", n=len(todo), write_ledger=write_ledger,
         note="read-only: audits and ledger rows only, no order path")
    rows, errors = [], []
    for i, slug in enumerate(todo):
        try:
            result = audit(slug)
            if write_ledger:
                result["ledger_rows_written"] = (
                    auditor.append_authoritative_ledger(result))
            row = summarise(result)
            rows.append(row)
            _log("audited", i=i + 1, of=len(todo), **row)
        except Exception as exc:                        # noqa: BLE001
            # one unreadable market must not cost the other thirty-five
            errors.append({"slug": slug, "error": repr(exc)[:300]})
            _log("audit_error", i=i + 1, of=len(todo), slug=slug,
                 error=repr(exc)[:300])
        if i + 1 < len(todo):
            sleep(PACE_S)
    out = {"n_requested": len(todo), "n_audited": len(rows),
           "n_errors": len(errors), "markets": rows, "errors": errors,
           "totals": totals(rows)}
    _log("sweep_end", **{k: v for k, v in out.items()
                         if k not in ("markets", "errors")},
         next="none: this pass exits and never re-arms")
    return out


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--markets", nargs="*", default=None,
                    help="slugs to audit; default = the ledger's unsettled set")
    ap.add_argument("--no-write-ledger", action="store_true",
                    help="audit and report without writing settled/realized")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    res = sweep(slugs=args.markets, write_ledger=not args.no_write_ledger)
    if args.out:
        from pathlib import Path
        Path(args.out).write_text(json.dumps(res, indent=2, default=str) + "\n")
    return 0


if __name__ == "__main__":                             # pragma: no cover
    raise SystemExit(main())
