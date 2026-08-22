"""Production combo supervisor v32: persist non-P&L cycle telemetry.

Adds observability only. After v29's durable RFQ feedback merge, emit one best-effort
diagnostic heartbeat to the shared trade_events ledger. A telemetry failure is
ignored and cannot change candidate ranking, thresholds, order entry, or hard-halt
behavior. All v31 execution and safety semantics remain unchanged.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from src import sports_kp_live_combo_supervisor_v31 as v31
from src import sports_kp_live_combo_supervisor_v29 as v29
from src import sports_supervisor_trade_telemetry as telemetry

D = Decimal
_ORIGINAL_MERGE = v29.feedback_store.merge_feedback_delta


def _feedback_summary(after: Mapping[str, Mapping[str, Any]], persisted: int) -> dict[str, Any]:
    rows = []
    for signature, row in after.items():
        try:
            best = D(str(row.get("best_lock", "-9")))
            last = D(str(row.get("last_lock", "-9")))
            observations = int(row.get("observations", 0))
            quoted = int(row.get("quoted", 0))
        except Exception:
            continue
        rows.append((best, last, observations, quoted, str(signature)))
    rows.sort(reverse=True)
    best_row = rows[0] if rows else None
    return {
        "rfq_signatures_in_memory": len(after),
        "rfq_signatures_updated_this_cycle": int(persisted),
        "best_conservative_lock": str(best_row[0]) if best_row else None,
        "best_signature_last_lock": str(best_row[1]) if best_row else None,
        "best_signature_observations": best_row[2] if best_row else 0,
        "best_signature_quoted": best_row[3] if best_row else 0,
        "best_signature": best_row[4] if best_row else None,
        "durable_feedback_hook": True,
        "active_supervisor_version": "v32/v31",
        "execution_semantics_changed": False,
    }


async def _merge_with_telemetry(before, after) -> int:
    persisted = await _ORIGINAL_MERGE(before, after)
    try:
        await telemetry.emit_cycle(_feedback_summary(after, persisted))
    except Exception:
        pass
    return persisted


v29.feedback_store.merge_feedback_delta = _merge_with_telemetry

run = v31.run
main = v31.main

if __name__ == "__main__":
    main()
