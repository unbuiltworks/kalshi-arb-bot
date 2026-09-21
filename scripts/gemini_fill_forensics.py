"""READ-ONLY forensic capture of this account's historical prediction fills.

WHY THIS EXISTS. The repository holds NO raw per-fill record for the Aug/Sep
Gemini activity -- only aggregates (`/v1/tradevolume` notional by day and by
symbol) in the rebate reconciliation. Those aggregates cannot answer the
question actually being asked, for two independent reasons:

  * every prediction symbol ends `-UP` because that is the CONTRACT. YES and
    NO are OUTCOMES INSIDE it, so a per-symbol notional cannot distinguish one
    YES fill from a YES+NO pair on the same contract; and
  * the table is maker-only (`aggressor=false`). The same capture recorded
    `rows_returned 105` against `n_maker_trades 62`, so roughly forty rows are
    taker or non-prediction and are invisible in it.

WHY NOT /v1/mytrades?symbol=. That is the SPOT trades endpoint. Asked with a
long-delisted prediction symbol it answers 400 -- measured across nine
Aug/Sep symbols on 2026-09-21, every one of them. The prediction record lives
at /v1/prediction-markets/orders/history, which is also the only surface that
carries the OUTCOME, and it is read here through the existing read-only
`metadata.order_history` rather than a new transport.

THIS SUBMITS NOTHING AND CANCELS NOTHING. It has no order transport, takes no
authorization, and writes one artifact. Raw rows are preserved verbatim so a
later reading can disagree with this one.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List

from src.gemini_execution import metadata as MD

LABEL = "GEMINI_FILL_FORENSICS_V1"


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(
        iso.replace("Z", "+00:00")).timestamp() * 1000)


def _utc(ms: Any) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000.0,
                                      timezone.utc).isoformat()
    except Exception:                                          # noqa: BLE001
        return ""


def _row_symbol(r: Dict[str, Any]) -> str:
    for k in ("symbol", "instrument", "instrumentName", "market"):
        if r.get(k):
            return str(r[k])
    return ""


def _row_outcome(r: Dict[str, Any]) -> str:
    for k in ("outcome", "O", "side_outcome", "predictionOutcome"):
        if r.get(k):
            return str(r[k]).upper()
    return ""


async def capture(since: str, until: str, statuses: tuple) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "label": LABEL, "utc": datetime.now(timezone.utc).isoformat(),
        "window": {"since": since, "until": until},
        "statuses": list(statuses),
        "reads": {}, "rows": [], "by_contract": {},
        "labels": ("RAW venue rows, preserved verbatim. Nothing here is a "
                   "position, a P&L or a settlement; it is what the account "
                   "record says about orders and their fill state."),
    }

    hist = await MD.order_history(since_ms=_ms(since), until_ms=_ms(until),
                                  statuses=statuses, limit=500,
                                  include_rows=True)
    # ROWS LIVE UNDER per_status[*]["orders"], not at the top level: the
    # reader asks once PER STATUS because `status` is a scalar in the venue's
    # request body. Reading a top-level "rows" key that is never produced
    # would return [] and look like "readable and empty" -- the exact
    # substitution this codebase has been bitten by before.
    out["reads"]["order_history"] = {
        k: hist.get(k) for k in
        ("readable", "verdict", "path", "window_ms", "total_rows")}
    out["reads"]["per_status"] = [
        {k: e.get(k) for k in ("status_filter", "http", "rows", "unreadable",
                               "page_is_full", "detail")}
        for e in (hist.get("per_status") or [])]
    rows: List[Dict[str, Any]] = []
    for e in (hist.get("per_status") or []):
        rows.extend(e.get("orders") or [])
    out["rows"] = rows

    # the authoritative position surface, as it stands NOW. It cannot speak
    # to what was held three weeks ago, and is recorded for what it is.
    pos = await MD.positions()
    out["reads"]["positions_now"] = {
        "ok": pos.get("ok"), "rows": pos.get("rows"),
        "why": ("the CURRENT position surface. It is evidence about the "
                "representation the venue uses, never about a historical "
                "moment that has since settled."),
    }

    # group by contract, chronologically, so a both-outcomes contract is
    # visible as a sequence rather than as a total.
    by: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[_row_symbol(r)].append(r)
    for sym, rs in by.items():
        rs.sort(key=lambda x: str(x.get("createdAt")
                                  or x.get("timestampms") or ""))
        outcomes = {_row_outcome(r) for r in rs if _row_outcome(r)}
        filled = [r for r in rs
                  if str(r.get("status", "")).lower() in ("filled", "closed")
                  or float(r.get("filledQuantity") or 0) > 0]
        filled_outcomes = {_row_outcome(r) for r in filled if _row_outcome(r)}
        out["by_contract"][sym] = {
            "n_rows": len(rs),
            "outcomes_ordered": sorted(outcomes),
            "outcomes_FILLED": sorted(filled_outcomes),
            "both_outcomes_filled": len(filled_outcomes & {"YES", "NO"}) == 2,
            "first_ts": rs[0].get("createdAt") if rs else None,
            "last_ts": rs[-1].get("createdAt") if rs else None,
        }

    both = [s for s, v in out["by_contract"].items()
            if v["both_outcomes_filled"]]
    out["verdict"] = {
        "contracts_seen": len(out["by_contract"]),
        "contracts_with_BOTH_outcomes_filled": both,
        "n_both": len(both),
        "why": ("a contract qualifies only when rows on BOTH outcomes carry a "
                "fill. Accepted-but-unfilled opposite-side orders do NOT "
                "qualify, which is exactly the distinction the question "
                "turns on."),
    }
    return out


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default="2026-08-26T00:00:00Z")
    ap.add_argument("--until", default="2026-09-03T00:00:00Z")
    ap.add_argument("--statuses", default="filled,cancelled")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    rep = asyncio.run(capture(a.since, a.until,
                              tuple(s.strip() for s in a.statuses.split(",")
                                    if s.strip())))
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print("GEMINI_FILL_FORENSICS %s" % json.dumps({
        "reads": rep["reads"]["order_history"],
        "verdict": rep["verdict"],
        "positions_now": rep["reads"]["positions_now"]["rows"],
    }, default=str))
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
