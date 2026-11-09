"""Read-only empirical joint-fill calibration from venue-attested settled windows.

Input rows are produced by reconciliation, never from shadow or order intents.
A row is one predeclared eligible quote opportunity, including no-fill cases.
Calibration is descriptive; holdout validation is a separate promotion gate.
"""
from collections import Counter
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json


def _d(v):
    try:
        d = Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("invalid numeric evidence")
    if not d.is_finite():
        raise ValueError("non-finite evidence")
    return d


def calibrate_confirmed_windows(rows, *, min_windows=30, max_book_age_s="2"):
    """Require a complete, uniquely identified, time-aligned population.

    Each record requires venue-attested terminal fills and settlement and
    an explicit observed opportunity, even when neither leg fills. Missing
    cases are refused rather than silently excluded from denominators.
    """
    data = list(rows)
    if len(data) < min_windows:
        return {"eligible": False, "reason": "INSUFFICIENT_WINDOWS",
                "observed": len(data), "required": min_windows}
    ids = set()
    counts = Counter()
    up_only_settled = down_only_settled = 0
    for row in data:
        required = ("market_id", "opportunity_id", "book_observation_id",
                    "book_age_s", "up_filled_qty", "down_filled_qty",
                    "settlement_side", "settlement_confirmed",
                    "fills_venue_confirmed", "orders_terminal",
                    "inventory_reconciled", "fees_attested",
                    "rebates_attested", "observation_complete",
                    "opportunity_predeclared")
        if any(k not in row for k in required):
            return {"eligible": False, "reason": "MISSING_EVIDENCE_FIELDS"}
        key = (row["market_id"], row["opportunity_id"])
        if not all(isinstance(x, str) and x for x in key + (row["book_observation_id"],)):
            return {"eligible": False, "reason": "INVALID_EVIDENCE_ID"}
        if key in ids:
            return {"eligible": False, "reason": "DUPLICATE_OPPORTUNITY"}
        ids.add(key)
        if any(row[k] is not True for k in (
            "settlement_confirmed", "fills_venue_confirmed",
            "orders_terminal", "inventory_reconciled", "fees_attested",
            "rebates_attested", "observation_complete",
            "opportunity_predeclared")):
            return {"eligible": False, "reason": "UNATTESTED_EVIDENCE"}
        if row["settlement_side"] not in ("UP", "DOWN"):
            return {"eligible": False, "reason": "UNCONFIRMED_SETTLEMENT"}
        try:
            age = _d(row["book_age_s"])
            up = _d(row["up_filled_qty"])
            down = _d(row["down_filled_qty"])
        except ValueError:
            return {"eligible": False, "reason": "INVALID_NUMERIC_EVIDENCE"}
        if age < 0 or age > _d(max_book_age_s):
            return {"eligible": False, "reason": "STALE_BOOK"}
        if up < 0 or down < 0:
            return {"eligible": False, "reason": "NEGATIVE_FILLED_QUANTITY"}
        if up and down:
            category = "both"
        elif up:
            category = "up_only"
            up_only_settled += row["settlement_side"] == "UP"
        elif down:
            category = "down_only"
            down_only_settled += row["settlement_side"] == "UP"
        else:
            category = "neither"
        counts[category] += 1
    if not counts["up_only"] or not counts["down_only"] or not counts["both"]:
        return {"eligible": False, "reason": "UNOBSERVED_REQUIRED_OUTCOME"}
    n = len(data)
    distribution = {f"p_{k}": str(Decimal(counts[k]) / n)
                    for k in ("both", "up_only", "down_only", "neither")}
    distribution.update(
        p_up_settles_given_up_only=str(
            Decimal(up_only_settled) / counts["up_only"]),
        p_up_settles_given_down_only=str(
            Decimal(down_only_settled) / counts["down_only"]))
    fingerprint = sha256(json.dumps(sorted(ids), separators=(",", ":")).encode()).hexdigest()
    return {"eligible": True, "model_only": True, "production_authorized": False,
            "window_count": n, "counts": dict(counts),
            "calibration_id": fingerprint, **distribution,
            "limitation": "Empirical in-sample frequencies, not validated out-of-sample probabilities"}
