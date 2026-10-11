"""Bridge the two-sided BTC15m recorder's settlement schema to the one the
canonical shadow engine reads.

READ-ONLY. No venue call, no order path, no signer.

WHY THIS EXISTS
---------------
Two BTC15m tape producers exist and their settlement rows disagree:

  ie_collector (analysis/gemini/info_edge/ie_collector.py)
      {"kind":"settlement","ticker":...,"resolution_side":"yes"|"no",
       "settlement_value":...,"strike":...}

  btc15m two-sided research recorder (analysis/gemini/btc15m_rec/*)
      {"kind":"settlement","ticker":...,"settlement":{"value":...},
       "raw_event":{...}}                      <-- no resolution_side, no strike

`btc15m_shadow_policy.shadow_window` reads `settled["resolution_side"]` and,
when it is absent, emits NO `settlement` block at all -- not an error, just a
missing key. Every economic column then prints "-" and the cohort total prints
0, which is indistinguishable from "the policy made no money". That is a
fail-OPEN settlement reader, and it is why the richer recorder tape (the only
BTC15m tape with a derived DOWN leg and a venue print tape) has never been
scored.

This module derives the missing field from evidence that IS on the recorder
tape -- the authoritative post-`availableAt` strike on the window rows and the
venue's own settlement value -- and fails CLOSED: a window whose strike or
settlement value is missing or unparseable is returned as UNRESOLVED and is
never silently given a side.

Settlement rule, from the venue's own contract text carried in `raw_event`:
"If the price of Bitcoin (BTC) is greater than or equal to the price specified
in the title ... this market will resolve to Yes."  So UP wins on
`settlement_value >= strike`, ties to UP.
"""
from __future__ import annotations

import glob
import json
import os
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Tuple

TICKER_PREFIX = "BTC15M"


def _dec(x: Any) -> Optional[Decimal]:
    if x is None:
        return None
    try:
        return Decimal(str(x))
    except (InvalidOperation, ValueError):
        return None


def scan(tape_dir: str) -> Dict[str, Dict[str, Any]]:
    """Collect strike + settlement value per BTC15m ticker from a tape dir.

    Accepts both `*.jsonl` and the recorder's own `*.jsonl.gz`.
    """
    import gzip

    out: Dict[str, Dict[str, Any]] = {}
    files = sorted(glob.glob(os.path.join(tape_dir, "*.jsonl"))) + \
        sorted(glob.glob(os.path.join(tape_dir, "*.jsonl.gz")))
    for path in files:
        op = gzip.open if path.endswith(".gz") else open
        with op(path, "rt") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                k = r.get("kind")
                tk = r.get("ticker") or ""
                if not tk.startswith(TICKER_PREFIX):
                    continue
                e = out.setdefault(tk, {"ticker": tk, "strike": None,
                                        "settlement_value": None,
                                        "expiry": None,
                                        "strike_sources": set(),
                                        "already_sided": None})
                if k in ("window", "window_poll", "strike_resolved"):
                    s = _dec(r.get("strike"))
                    if s is not None:
                        # a later non-null strike supersedes an earlier null;
                        # disagreement between non-null strikes is recorded.
                        e["strike_sources"].add(str(s))
                        e["strike"] = s
                    if r.get("expiry"):
                        e["expiry"] = r["expiry"]
                elif k == "settlement":
                    # recorder schema
                    sv = _dec((r.get("settlement") or {}).get("value")
                              if isinstance(r.get("settlement"), dict) else None)
                    if sv is None:
                        # collector schema
                        sv = _dec(r.get("settlement_value"))
                    if sv is not None:
                        e["settlement_value"] = sv
                    if r.get("resolution_side") in ("yes", "no"):
                        e["already_sided"] = r["resolution_side"]
                    if _dec(r.get("strike")) is not None:
                        e["strike"] = _dec(r.get("strike"))
    return out


def resolve(entry: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """Return (resolution_side, provenance). Fails CLOSED on missing evidence."""
    if entry.get("already_sided") in ("yes", "no"):
        return entry["already_sided"], "VENUE_FIELD"
    strike = entry.get("strike")
    value = entry.get("settlement_value")
    if strike is None and value is None:
        return None, "UNRESOLVED_NO_STRIKE_NO_VALUE"
    if strike is None:
        return None, "UNRESOLVED_NO_STRIKE"
    if value is None:
        return None, "UNRESOLVED_NO_SETTLEMENT_VALUE"
    if len(entry.get("strike_sources") or ()) > 1:
        return None, "UNRESOLVED_STRIKE_DISAGREEMENT:" + \
            ",".join(sorted(entry["strike_sources"]))
    return ("yes" if value >= strike else "no"), "DERIVED_VALUE_VS_STRIKE"


def table(tape_dir: str) -> Dict[str, Dict[str, Any]]:
    rows = {}
    for tk, e in sorted(scan(tape_dir).items()):
        side, prov = resolve(e)
        rows[tk] = {
            "ticker": tk,
            "strike": None if e["strike"] is None else str(e["strike"]),
            "settlement_value": (None if e["settlement_value"] is None
                                 else str(e["settlement_value"])),
            "expiry": e["expiry"],
            "resolution_side": side,
            "provenance": prov,
        }
    return rows


def main() -> None:
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else "data/gemini_infoedge_15m"
    rows = table(d)
    resolved = sum(1 for r in rows.values() if r["resolution_side"])
    print(f"tape_dir={d}  tickers={len(rows)}  resolved={resolved}  "
          f"unresolved={len(rows) - resolved}")
    for r in rows.values():
        print(f"  {r['ticker']}  strike={r['strike']}  "
              f"value={r['settlement_value']}  -> {r['resolution_side']}  "
              f"({r['provenance']})")
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
