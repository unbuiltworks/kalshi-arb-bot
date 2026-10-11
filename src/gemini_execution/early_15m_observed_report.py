#!/usr/bin/env python3
"""EARLY_15M_OBSERVED over expired BTC15m contracts. Offline research.

`--bands` replays SEVERAL TTE intervals from ONE fetch of each contract's
tape, so the early band and the frozen [0, 300] control are measured over the
identical contracts with identical code and differ only in timing. Fetching
twice would let the slate move between runs and confound exactly the
comparison this exists to make.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone

from src.gemini_execution import backfill as BF
from src.gemini_execution import contract_cohort as CC
from src.gemini_execution import early_15m_observed as EO
from src.gemini_execution import metadata as MD
from src.gemini_execution import window_volume as WV


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", type=int, default=96)
    ap.add_argument("--bands", default="EARLY_15M_OBSERVED:300:900",
                    help="comma-separated label:lo:hi, replayed from one "
                         "fetch per contract")
    ap.add_argument("--out",
                    default="analysis/gemini/early_15m_observed.json",
                    help="written for the FIRST band; later bands get the "
                         "label appended")
    a = ap.parse_args(argv)
    bands = []
    for spec in a.bands.split(","):
        lbl, lo, hi = spec.split(":")
        bands.append((lbl, float(lo), float(hi)))
    tapes = []
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
        tapes.append((sym, t["prints"], exp))
        time.sleep(0.1)

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    stem, ext = os.path.splitext(a.out)
    for i, (lbl, lo, hi) in enumerate(bands):
        rows = [EO.replay_observed(sym, prints, expiry_ms=exp, lo=lo, hi=hi)
                for sym, prints, exp in tapes]
        rep = {"utc": datetime.now(timezone.utc).isoformat(),
               "census": EO.census(rows, label=lbl), "rows": rows}
        out = a.out if i == 0 else "%s_%s%s" % (stem, lbl.lower(), ext)
        json.dump(rep, open(out, "w", encoding="utf-8"), indent=2, default=str)
        _print(rep["census"])
    return 0


def _print(c) -> None:
    print("%s band=%s contracts=%s computable=%s first_fill=%s "
          "opp_flow=%s price_observable=%s par<=1=%s fee_positive=%s "
          "median_basis=%s distinct_bases=%s t2c=%ss reachable=%s" % (
              c["label"], c["band_tte_s"], c["contracts"],
              c["computable"], c["first_fill_states"],
              c["opposite_flow"], c["price_observable_complements"],
              c["pairs_at_or_below_1"], c["fee_positive_pairs"],
              c["median_actual_pair_basis"], c["distinct_bases"],
              c["median_seconds_to_complement"],
              c["maker_reachable_complements"]), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
