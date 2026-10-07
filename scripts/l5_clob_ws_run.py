"""Entrypoint for the strict-timing dual-token websocket arm.

Runs `probe()` first and REFUSES to continue unless the venue contract is
proven live (both tokens streaming on one socket). An unproven subscription
is recorded as WS_UNPROVEN and the process exits non-zero rather than
accumulating a tape whose gaps cannot be distinguished from quiet books.
"""

import argparse
import json
import os
import sys

from src.l5_book_shadow import ENV_ROOT, boot_gate
from src.l5_clob_ws import ClobWsCollector, probe

ENV_MARKET = "CLOB_WS_MARKET"
ENV_TOKEN_UP = "CLOB_WS_TOKEN_UP"
ENV_TOKEN_DOWN = "CLOB_WS_TOKEN_DOWN"


def discover_two_token_market():
    """One live market quoting BOTH outcome tokens. Read-only.

    Uses the collector's own venue/discovery/pacer code so the probe cannot
    drift from what the REST arm actually tracks, and cannot exceed the
    shared request ceiling.
    """
    from src.l5_book_shadow import (Pacer, PolymarketVenue, ReadOnlyFetcher,
                                    poly_tokens, select_l5_active)
    venue = PolymarketVenue(ReadOnlyFetcher(Pacer()))
    try:
        # select_l5_active returns a TUPLE (selections, raw_actions) -- not a
        # list. Iterating the tuple yields two LISTS, neither of which has a
        # .tokens attribute, so every market silently looked single-token
        # and the probe refused. Unpack it.
        selections, _actions = select_l5_active(venue, limit=100,
                                                max_markets=8)
    except Exception as exc:                                  # noqa: BLE001
        print("discovery failed: %s: %s"
              % (type(exc).__name__, str(exc)[:200]), flush=True)
        return "", {}
    for sel in selections:
        # Selection.tokens is [{"token": <id>, "outcome": <label>}, ...] --
        # a LIST of dicts, not a mapping. Read it as the collector writes it.
        rows = [t for t in (getattr(sel, "tokens", None) or [])
                if isinstance(t, dict) and t.get("token")]
        if len(rows) >= 2:
            pairs = {str(r["token"]): str(r.get("outcome") or "")
                     for r in rows[:2]}
            if len(pairs) == 2:
                return str(getattr(sel, "market", "") or ""), pairs
    return "", {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--probe-seconds", type=float, default=20.0)
    ap.add_argument("--probe-only", action="store_true")
    ap.add_argument("--minutes", type=float, default=0.0,
                    help="0 or less = run forever once proven")
    args = ap.parse_args(argv)

    gate = boot_gate()
    print(json.dumps(gate, sort_keys=True), flush=True)
    if not gate["ok"]:
        print("BOOT GATE FAILED: %s -- refusing to start"
              % ",".join(gate["blocking_failures"]), flush=True)
        return 2

    root = os.environ.get(ENV_ROOT, "/state/l5-book-shadow")
    market = os.environ.get(ENV_MARKET, "")
    tokens = {}
    for env, outcome in ((ENV_TOKEN_UP, "UP"), (ENV_TOKEN_DOWN, "DOWN")):
        tid = os.environ.get(env, "")
        if tid:
            tokens[tid] = outcome
    if len(tokens) != 2 or not market:
        # Nothing was pinned, so discover a two-token market with the
        # collector's OWN discovery code rather than hardcoding ids that
        # would be stale within the hour. Read-only, GET-only, and it
        # reuses the same pacer ceiling.
        market, tokens = discover_two_token_market()
    if len(tokens) != 2 or not market:
        print("REFUSED: need a market with BOTH token ids; a single-token "
              "subscription cannot answer a two-token timing question",
              flush=True)
        return 2
    print("PROBE TARGET market=%s tokens=%s" % (market, tokens), flush=True)

    rep = probe(market, tokens, root, seconds=args.probe_seconds)
    print(json.dumps(rep, sort_keys=True)[:4000], flush=True)
    if not rep.get("both_tokens_streaming"):
        print("WS SUBSCRIPTION UNPROVEN -- not accumulating a tape whose "
              "gaps cannot be told from quiet books", flush=True)
        return 3
    if args.probe_only:
        return 0

    col = ClobWsCollector(root, market=market, tokens=tokens)
    import time
    deadline = None if args.minutes <= 0 else \
        time.monotonic() + args.minutes * 60.0
    while deadline is None or time.monotonic() < deadline:
        try:
            col.session(deadline=deadline)
        except Exception as exc:                               # noqa: BLE001
            col._down_since = col._down_since or time.time()
            col._emit_window("OPEN")
            col.reconnects += 1
            print("ws session ended: %s: %s"
                  % (type(exc).__name__, str(exc)[:200]), flush=True)
            time.sleep(min(2.0 * col.reconnects, 30.0))
    col.write_proof()
    return 0


if __name__ == "__main__":
    sys.exit(main())
