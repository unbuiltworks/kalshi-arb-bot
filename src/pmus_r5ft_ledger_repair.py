"""Fail-closed R5FT production reconciliation repairs.

Two narrow, auditable repairs live here because this module runs before the
R5FT continuous session on every production boot:

1. Ledger overshoot repair. A buggy historical venue backfill duplicated
   already-booked quantity on two venue-confirmed SETTLED markets. We append
   explicit negative local accounting adjustments only when the ledger is
   above the authoritative observed venue quantity. Re-running is a no-op.

2. Campaign-scope repair. The first R5FT campaign inherited one already-
   settled R5F market into its campaign-local ``filled_markets`` /
   ``settled_here`` quota. R5FT allows one market, so that historical market
   made the fresh R5FT canary immediately report RUNG_COMPLETE and restart
   forever without submitting an R5FT order. We clear ONLY that exact known
   campaign-local singleton, and only when all provenance checks identify the
   approved R5FT campaign and its R5F predecessor. Ledger, venue history,
   prior campaigns, risk state, limits, and P&L are untouched. Re-running is
   a no-op.
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

R5FT_CAMPAIGN_ID = "R5FT-CANARY-2026-09-11"
R5FT_RUNG = "R5FT"
R5FT_PREDECESSOR = "R5F-RUN-2026-09-07"
R5FT_CONTAMINANT = "aec-mlb-sea-ath-2026-09-11"
CAMPAIGN_STATE_FILE = "l5_campaign_state.json"


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


def _repair_ledger() -> list[dict]:
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

        adjustment = target - current
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
    return results


def _repair_campaign_scope() -> dict:
    """Remove one proven predecessor fill from the fresh R5FT quota.

    This is deliberately NOT a generic state reset. Any mismatch makes it a
    no-op so an unknown future state can never be silently rewritten.
    """
    path = q1.state_dir() / CAMPAIGN_STATE_FILE
    if not path.exists():
        return {"status": "no_state_file"}

    state = json.loads(path.read_text(encoding="utf-8"))
    filled = list(state.get("filled_markets") or [])
    settled = list(state.get("settled_here") or [])
    previous = [
        str(x.get("campaign_id") or "") if isinstance(x, dict) else str(x)
        for x in (state.get("previous_campaigns") or [])
    ]

    exact = (
        str(state.get("campaign_id") or "") == R5FT_CAMPAIGN_ID
        and str(state.get("rung") or "") == R5FT_RUNG
        and filled == [R5FT_CONTAMINANT]
        and settled == [R5FT_CONTAMINANT]
        and R5FT_PREDECESSOR in previous
    )
    already_clean = (
        str(state.get("campaign_id") or "") == R5FT_CAMPAIGN_ID
        and str(state.get("rung") or "") == R5FT_RUNG
        and filled == []
        and settled == []
        and R5FT_PREDECESSOR in previous
    )

    if already_clean:
        return {"status": "already_clean", "filled_markets": filled, "settled_here": settled}
    if not exact:
        return {
            "status": "refused_state_mismatch",
            "campaign_id": state.get("campaign_id"),
            "rung": state.get("rung"),
            "filled_markets": filled,
            "settled_here": settled,
            "predecessor_present": R5FT_PREDECESSOR in previous,
        }

    state["filled_markets"] = []
    state["settled_here"] = []
    tmp = path.with_suffix(path.suffix + ".r5ft-scope.tmp")
    tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    tmp.replace(path)

    verify = json.loads(path.read_text(encoding="utf-8"))
    if verify.get("filled_markets") != [] or verify.get("settled_here") != []:
        raise RuntimeError("R5FT campaign-scope repair failed verification")
    return {
        "status": "repaired",
        "removed_from_campaign_quota": R5FT_CONTAMINANT,
        "ledger_untouched": True,
        "previous_campaigns_untouched": True,
    }


def main() -> None:
    ledger_results = _repair_ledger()
    scope_result = _repair_campaign_scope()
    print("PMUS_R5FT_LEDGER_REPAIR " + json.dumps(ledger_results, separators=(",", ":")), flush=True)
    print("PMUS_R5FT_SCOPE_REPAIR " + json.dumps(scope_result, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
