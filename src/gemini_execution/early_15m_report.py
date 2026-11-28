#!/usr/bin/env python3
"""EARLY_15M_ADAPTATION over expired BTC15m contracts. Offline research."""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone

from src.gemini_execution import backfill as BF
from src.gemini_execution import contract_cohort as CC
from src.gemini_execution import early_15m as EA
from src.gemini_execution import metadata as MD
from src.gemini_execution import window_volume as WV


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", type=int, default=48)
    ap.add_argument("--out", default="analysis/gemini/early_15m.json")
    a = ap.parse_args(argv)
    rows = []
    for tk in BF.tickers_back(CC.BTC15M, windows=a.windows):
        sym = CC.BTC15M.symbol_for(tk)
        t = WV.fetch_tape(sym)
        if not t.get("readable"):
            continue
        terms = MD.terms_for(sym) or {}
        raw = terms.get("expiry") or terms.get("expiryDate")
        if not raw:
            continue
        exp = datetime.fromisoformat(
            str(raw).replace("Z", "+00:00")).timestamp() * 1000
        rows.append(EA.replay_early(sym, t["prints"], expiry_ms=exp))
        time.sleep(0.1)
    rep = {"utc": datetime.now(timezone.utc).isoformat(),
           "census": EA.census(rows), "rows": rows}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(rep, open(a.out, "w", encoding="utf-8"), indent=2, default=str)
    c = rep["census"]
    print("EARLY_15M contracts=%s computable=%s first_fill=%s opp_flow=%s "
          "flow_reached_fee_safe=%s (%s) t2c=%ss basis(CONSTRUCTED)=%s" % (
              c["contracts_replayed"], c["computable"],
              c["with_a_first_fill"], c["with_opposite_flow_after_fill"],
              c["flow_reached_a_fee_safe_complement"], c["reach_rate"],
              c["median_seconds_to_complete"],
              c["constructed_basis_values"]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
