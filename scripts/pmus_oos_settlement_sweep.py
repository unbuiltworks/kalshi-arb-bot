"""GET-only settlement sweep for the out-of-sample BTC-15m windows.

WHY. `data/pmus_btc15m_tape_oos/oos_tape.jsonl` carries `last`, never
settlementPx, and the frozen sample's own notes record a window where
settlementPx 1.0000 != lastTradePx 0.9900. So the out-of-sample replay's net
had to be reported as a BRACKET over both outcomes. This replaces the bracket
with the venue's own number.

SAFE BY CONSTRUCTION, not by intention:
  * `pmus_auth.ReadOnlyTransport` raises PermissionError on any method but
    GET, so no POST can leave this process even by mistake;
  * the transport is never armed, so `submit()` would raise NotArmed first;
  * this module imports no engine, no money path, no mandate and no
    authorization -- there is nothing here that could construct an order.

It writes one JSON document to stdout and nothing to disk.
"""
from __future__ import annotations

import json
import os
import sys

from src import pmus_auth as Q1
from src import l5_pmus_transport as TP


def slugs_from_tape(path: str) -> list:
    import scripts.pmus_completeness_rule as CR
    from scripts.audit_economic_complete import USABLE
    by = CR.load_tape(path)
    return [s for s, rows in sorted(by.items())
            if CR.evaluate(s, rows).get("tape_test_passes")
            and s not in USABLE]


def sweep(slugs) -> dict:
    ro = Q1.ReadOnlyTransport()
    out, errs = {}, {}
    for slug in slugs:
        tp = TP.PmusTransport(market_slug=slug, http=ro)
        try:
            s = tp.settlement(slug)
        except BaseException as exc:                          # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            errs[slug] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
            continue
        px = s.get("settlement_px")
        # UP wins at 1, DOWN at 0. Anything else is UNKNOWN and is NOT
        # coerced -- a window whose winner the venue did not state is
        # excluded from the replay rather than guessed.
        winner = None
        if s.get("terminal") and px is not None:
            if px == 1:
                winner = "up"
            elif px == 0:
                winner = "down"
        out[slug] = {"state": s.get("state"),
                     "settlement_px": (None if px is None else str(px)),
                     "terminal": s.get("terminal"), "winner": winner}
    resolved = {k: v for k, v in out.items() if v["winner"]}
    return {"LABEL": "OOS_SETTLEMENT_SWEEP_READONLY",
            "post_capable": False,
            "requested": len(slugs), "answered": len(out),
            "resolved": len(resolved), "errors": errs,
            "unresolved": sorted(k for k, v in out.items() if not v["winner"]),
            "winners": {k: v["winner"] for k, v in resolved.items()},
            "rows": out,
            "labels": ("GET only; ReadOnlyTransport raises on any other "
                       "method. A window the venue did not resolve is left "
                       "UNKNOWN and never inferred from lastTradePx.")}


if __name__ == "__main__":
    tape = os.getenv("L5_OOS_TAPE",
                     "data/pmus_btc15m_tape_oos/oos_tape.jsonl")
    sl = slugs_from_tape(tape)
    print("OOS_SWEEP_SLUGS %d" % len(sl), flush=True)
    doc = sweep(sl)
    print("OOS_SETTLEMENT " + json.dumps(doc, default=str), flush=True)
    sys.exit(0)
