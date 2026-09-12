"""One-time, fail-closed repair for the two R5FT preflight ledger overshoots.

Production 2026-09-12: a buggy venue backfill appended every historical
venue trade for two already-booked, venue-confirmed SETTLED markets.  This
module does not invent fills or alter venue state.  It appends an explicit
negative local accounting adjustment only when the current ledger is above
the already-observed authoritative venue quantity.  Re-running is a no-op.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

from src import pmus_q1_executor_v4 as q1
from src import pmus_settlement_auditor as auditor

TARGETS = {
    "aec-setkameua-skaser-abaspa-2026-09-08": D("17.1800"),
    "aec-setkameua-vervas-kohboh-2026-09-08": D("13.8200"),
}
SOURCE = "r5ft_ledger_reconciliation_adjustment_20260912"


def _already_done(slug: str) -> bool:
    path = q1.state_dir() / "ledger.jsonl"
    if not path.exists():
        return False
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        if row.get("slug") == slug and row.get("source") == SOURCE:
            return True
    return False


def main() -> None:
    results = []
    for slug, target in TARGETS.items():
        current_raw = auditor.ledger_qty_total(slug)
        if current_raw is None:
            raise RuntimeError(f"{slug}: ledger quantity unavailable")
        current = D(str(current_raw))
        if current == target:
            results.append({"slug": slug, "status": "already_reconciled", "qty": str(current)})
            continue
        if _already_done(slug):
            raise RuntimeError(f"{slug}: repair marker exists but ledger is {current}, target {target}")
        if current < target:
            raise RuntimeError(f"{slug}: ledger {current} is below authoritative target {target}; refuse synthetic fill")

        adjustment = target - current  # negative: removes only the local overcount
        q1.ledger_append({
            "kind": "filled",
            "stage": "venue_reconciliation_adjustment",
            "slug": slug,
            "qty": str(adjustment),
            "price": None,
            "order_id": f"reconcile:{slug}:20260912",
            "source": SOURCE,
            "labels": "LOCAL ACCOUNTING CORRECTION ONLY: reverses duplicate venue_trade_backfill quantity on a venue-confirmed settled market; not an exchange fill, order, P&L event, fee, or rebate",
        })
        after = D(str(auditor.ledger_qty_total(slug)))
        if after != target:
            raise RuntimeError(f"{slug}: repair did not converge: {after} != {target}")
        results.append({"slug": slug, "status": "repaired", "before": str(current), "adjustment": str(adjustment), "after": str(after)})

    print("PMUS_R5FT_LEDGER_REPAIR " + json.dumps(results, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
