"""The last N EXPIRED Gemini BTC5m symbols, newest first.

Settlement is only a question once a contract has actually expired, so a
settlement probe pointed at live windows would ask about instruments the venue
has not settled yet and read the absence as a finding.

Stdlib only, no network, no credential. Printed comma-separated for a shell.
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from typing import List, Optional, Sequence

WINDOW_S = 300


def recent_expiries(n: int = 6, now: Optional[float] = None,
                    asset: str = "BTC") -> List[str]:
    """Symbols whose expiry is strictly in the PAST, newest first."""
    t = int(now if now is not None else time.time())
    base = (t // WINDOW_S) * WINDOW_S          # the last boundary at/before now
    out: List[str] = []
    for k in range(1, max(1, n) + 1):
        e = base - (k - 1) * WINDOW_S
        if e >= t:                              # strictly past only
            e -= WINDOW_S
        dt = datetime.fromtimestamp(e, timezone.utc)
        out.append("GEMI-%s05M%s-UP" % (asset, dt.strftime("%y%m%d%H%M")))
    seen, uniq = set(), []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-n", type=int, default=6)
    ap.add_argument("--asset", default="BTC")
    a = ap.parse_args(argv)
    print(",".join(recent_expiries(a.n, asset=a.asset)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
