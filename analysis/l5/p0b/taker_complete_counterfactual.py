"""P0-D: what a TAKER completion would have been worth, offline.

NOT A PROPOSAL AND NOT DEPLOYED. This measures a counterfactual the owner asked
for after BUF-HOU: our resting COMPLETE sat one tick under an ask that never
came to us, at a pair cost of 0.9627. If the complement had simply been TAKEN,
what would it have been worth -- and how often is that even available?

THE HARD RULE, from the instruction: NO ASSUMED FILL FOR STALE ASKS. Only a
contemporaneously observed, executable ask counts. That rule bites hard here,
and the honest consequence is reported rather than worked around:

  * `recorder_book.authenticated_age_s` has a MEDIAN of 6.2 s and a p90 of
    402.7 s. Only 41 of 263 COMPLETE attempts carry a book fresher than 5 s.
    Using the placement-time ask for all of them would be assuming fills on
    books up to fourteen minutes old.

  * `rest_book.min_opp_ask` IS contemporaneous by construction: it is the best
    ask actually observed while our order rested, across ~26 polls. At that
    instant that price existed.

So two cohorts are reported separately and never merged:

  STRICT      placement-time ask, only where authenticated_age_s <= 5 s.
              Small, conservative, no staleness assumption at all.
  REST_WINDOW the best ask observed during our own rest. Larger, and the most
              FAVOURABLE moment in the window -- an upper bound on what taking
              could have achieved, not an expectation.

FEES ARE NOT INVENTED. The venue does not report a per-fill fee on these rows.
`feeCoefficient` (0.06) appears on market metadata but the application rule is
not source-confirmed, so fee-inclusive economics are reported as UNKNOWN and
the locked-edge thresholds are stated PRE-fee. Manufacturing a zero fee would
turn a marginal edge into a positive one on paper.

DEPTH IS NOT INVENTED EITHER. The recorder rows carry no ask SIZE for these
orders, so "could we have filled our whole clip" is UNKNOWN. Every count below
is an opportunity count, not a guaranteed fill.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import complete_execution_envelope as C          # noqa: E402

PAIR_THRESHOLDS = (D("0.97"), D("0.98"), D("0.99"), D("1.00"))
EDGE_THRESHOLDS = (D("0.005"), D("0.01"), D("0.02"), D("0.03"))
FRESH_MAX_AGE_S = 5.0
UNKNOWN = "unknown_venue_does_not_report"


def _ask_strict(o: dict) -> Optional[D]:
    """Placement-time ask, ONLY if the book behind it was fresh."""
    rb = o.get("recorder_book") or {}
    age = rb.get("authenticated_age_s")
    if age is None or float(age) > FRESH_MAX_AGE_S:
        return None
    return C._d(o.get("fresh_opp_ask")) or C._d(rb.get("opp_ask_authenticated"))


def _ask_rest_window(o: dict) -> Optional[D]:
    """Best ask actually observed while we rested -- contemporaneous by
    construction, and the most favourable instant in the window."""
    return C._d((o.get("rest_book") or {}).get("min_opp_ask"))


def opportunities(sessions: list[dict], cohort: str) -> list[dict]:
    pick = _ask_strict if cohort == "strict" else _ask_rest_window
    out: list[dict] = []
    for s in sessions:
        avg, side = C.held_avg_cost(s)
        realized_resid = None
        for o in (s.get("orders") or []):
            if o.get("relation") != "complete":
                continue
            ask = pick(o)
            if ask is None or avg is None:
                continue
            qty = C._d(o.get("qty")) or D("0")
            pair = avg + ask
            out.append({
                "slug": s.get("slug"), "cohort": cohort,
                "held_side": side, "held_avg_cost": avg,
                "our_px": C._d(o.get("px")), "taker_ask": ask,
                "qty": qty,
                "pair_taker": pair,
                "locked_edge_per_pair": D("1") - pair,   # PRE-FEE
                "capital_required": (ask * qty),
                "actually_filled": (C._d(o.get("filled_qty")) or D("0")) > 0,
                "fees": UNKNOWN, "depth_available": UNKNOWN,
            })
    return out


def grid(rows: list[dict]) -> dict:
    """opportunities x (pair threshold, minimum pre-fee locked edge)."""
    g: dict[str, Any] = {}
    for pt in PAIR_THRESHOLDS:
        for et in EDGE_THRESHOLDS:
            sel = [r for r in rows
                   if r["pair_taker"] <= pt and r["locked_edge_per_pair"] >= et]
            key = f"pair<={pt}|edge>={et}"
            g[key] = {
                "opportunities": len(sel),
                "capital_required_usd": str(sum((r["capital_required"]
                                                 for r in sel), D("0"))),
                "matched_profit_usd_PRE_FEE": str(
                    sum((r["locked_edge_per_pair"] * r["qty"] for r in sel),
                        D("0"))),
                "fees_usd": UNKNOWN,
                "net_usd": "UNKNOWN_until_fees_are_source_confirmed",
                "markets": len({r["slug"] for r in sel}),
            }
    return g


def main(argv: list[str]) -> int:
    sessions = C.load_sessions(Path(argv[1]))
    doc: dict[str, Any] = {
        "sessions": len(sessions),
        "complete_attempts": sum(1 for s in sessions
                                 for o in (s.get("orders") or [])
                                 if o.get("relation") == "complete"),
        "labels": {
            "fees": "NOT modelled. The venue reports no per-fill fee on these "
                    "rows; feeCoefficient 0.06 exists on market metadata but "
                    "its application rule is not source-confirmed. All profit "
                    "figures are PRE-FEE and net is UNKNOWN.",
            "depth": "NOT modelled. No ask size on these rows, so full-clip "
                     "fills are not established -- these are OPPORTUNITY "
                     "counts, not guaranteed fills.",
            "strict": "placement-time ask, authenticated_age_s <= 5 s only",
            "rest_window": "best ask observed during our own rest; an UPPER "
                           "BOUND on what taking could have achieved",
        },
    }
    for cohort in ("strict", "rest_window"):
        rows = opportunities(sessions, cohort)
        doc[cohort] = {
            "attempts_with_usable_ask": len(rows),
            "markets": len({r["slug"] for r in rows}),
            "pair_taker_distribution": {
                "min": str(min((r["pair_taker"] for r in rows), default="-")),
                "median": str(sorted(r["pair_taker"] for r in rows)[len(rows) // 2])
                if rows else "-",
                "max": str(max((r["pair_taker"] for r in rows), default="-")),
                "share_le_1_00": round(
                    sum(1 for r in rows if r["pair_taker"] <= D("1.00")) / len(rows), 4)
                if rows else None,
                "share_le_0_98": round(
                    sum(1 for r in rows if r["pair_taker"] <= D("0.98")) / len(rows), 4)
                if rows else None,
            },
            "grid": grid(rows),
        }
    print(json.dumps(doc, indent=1, default=str))
    if len(argv) > 2:
        Path(argv[2]).write_text(json.dumps(doc, indent=1, default=str),
                                 encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
