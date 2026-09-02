"""READ-ONLY authenticated WS evidence recorder for netting reconciliation.

Gemini's authenticated Prediction Markets WebSocket carries the exact
source evidence the netting reconciliation needs (per current docs):
  * order fill events — incl. `n` (fee amount) and `m` (maker flag)
  * balance updates  — `c` (confirmed balance, incl. pending) and
                       `f` (available balance)

This module ONLY subscribes and records. It reuses the proven
authenticated session class (`GeminiMakerSession`) for its connection and
event stream and never sends an order, cancel, or any mutating frame.
Every received message is appended verbatim (plus a parsed tag) to an
append-only JSONL under the supervisor's durable root, so the exact
clientOrderIds of a campaign can later be reconciled by
`gemini_l5_settlement_audit.apply_netting_evidence` — turning
MATCHED_PAIR_NO_SETTLED_ROWS into MATCHED_NETTED_CONFIRMED only when fees
(`n`) and the confirmed-balance (`c`) delta reconcile exactly with the
pair economics. Balance drift alone never confirms anything.

Runs on the credentialed host alongside a live campaign (start it before
arming, stop after reconcile). Fails closed with NO_CREDENTIALS anywhere
else.

    python3 -m src.gemini_l5_ws_evidence --minutes 30
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.gemini_prediction_auth import has_credentials

OUT_DIR_ENV = "GEMINI_L5_WS_EVIDENCE_DIR"
DEFAULT_DIR = "/state/l5-supervisor/ws-evidence"


def _tag(msg: Mapping[str, Any]) -> str:
    if "n" in msg and ("c" in msg or "clientOrderId" in msg or "X" in msg):
        return "order_fill_event"
    if "c" in msg and "f" in msg:
        return "balance_update"
    if "X" in msg or "clientOrderId" in msg:
        return "order_event"
    return "other"


async def record(minutes: float) -> Path:
    from src.gemini_l5_ladder_q1 import base   # proven session class
    out_dir = Path(os.getenv(OUT_DIR_ENV, DEFAULT_DIR))
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"ws_evidence_{stamp}.jsonl"
    session = base.GeminiMakerSession()
    await session.connect()                      # subscribe-only from here on
    deadline = time.monotonic() + minutes * 60
    try:
        with path.open("a", encoding="utf-8") as sink:
            sink.write(json.dumps({
                "kind": "recorder_start", "utc": stamp, "read_only": True,
                "note": ("subscribe-only; no order/cancel frames are ever "
                         "sent by this module")}) + "\n")
            while time.monotonic() < deadline:
                try:
                    msg = await asyncio.wait_for(session.events.get(),
                                                 timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if not isinstance(msg, Mapping):
                    msg = {"raw": str(msg)}
                sink.write(json.dumps({
                    "recorded_utc": datetime.now(timezone.utc).isoformat(),
                    "tag": _tag(msg), "msg": dict(msg)},
                    separators=(",", ":"), default=str) + "\n")
                sink.flush()
    finally:
        await session.close()
    return path


def load_evidence(path: Path, client_ids: set) -> dict:
    """Assemble the evidence dict apply_netting_evidence() expects."""
    ws_fills, balances = [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = row.get("msg") or {}
        if row.get("tag") == "balance_update":
            balances.append({"c": msg.get("c"), "f": msg.get("f"),
                             "ts": row.get("recorded_utc")})
        elif row.get("tag") in ("order_fill_event", "order_event"):
            cid = str(msg.get("clientOrderId") or msg.get("c") or "")
            if not client_ids or cid in client_ids:
                ws_fills.append({"clientOrderId": cid,
                                 "n": msg.get("n"), "m": msg.get("m"),
                                 "price": msg.get("price") or msg.get("p"),
                                 "quantity": msg.get("q") or msg.get("Z")})
    return {"ws_fills": ws_fills, "balance_updates": balances}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--minutes", type=float, default=30.0)
    args = ap.parse_args()
    if not has_credentials():
        print(json.dumps({"verdict": "NO_CREDENTIALS",
                          "note": "run on the credentialed host (read-only)"}))
        return 2
    path = asyncio.run(record(args.minutes))
    print(f"ws evidence: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
