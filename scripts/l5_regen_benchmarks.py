#!/usr/bin/env python3
"""Regenerate the two prose-sourced L5 benchmarks as machine-readable artifacts.

HOUSEKEEPING ONLY. This does not touch Shadow S1, its candidate, its
fingerprint or its fill rules. It replaces two entries in
analysis/l5/shadow/L5_BENCHMARKS.json whose `source` pointed at a Markdown
narrative rather than a computed artifact:

  maker_fraction                 0.883  <- ZENODO_OVERLAP_FINDINGS.md prose
  two_outcome_burst_incidence_ge3 0.716 <- LADDER_V1.md prose

Both are recomputed here from primary data:

  * maker_fraction comes from the on-chain OrderFilled backfill over the
    Zenodo dataset's own 33 BTC15m windows, which carries an explicit
    per-fill `l5_role` field. It is a COUNT of logs where L5 is the maker
    over all L5 fills in the overlap -- no inference from book state.
  * the burst benchmark comes from the canonical activity corpus. A burst
    is a maximal run of legs <= BURST_GAP_S apart (the same definition
    l5_ladder_v1 uses, imported rather than restated). The headline number
    is conditioned on bursts of >= 3 legs, which is what the prose meant
    and which the existing machine entry `burst_both_outcomes_all` (0.309,
    unconditioned) does not say.

Each regenerated entry records its own inputs, so the number is auditable
without reading any prose.
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import l5_candidate_v2 as V          # noqa: E402  verified fill loader
import l5_ladder_v1 as L             # noqa: E402  verified burst definition

BENCH = "analysis/l5/shadow/L5_BENCHMARKS.json"
BACKFILL_COPY = "analysis/l5/historical_l2/l5_chain_backfill_33_windows.json"
SCRATCH = ("/tmp/claude-0/-home-user-kalshi-arb-bot/"
           "f2f630e2-70fa-534f-9e92-d1857b7df8ca/scratchpad/l2/"
           "l5_backfill.json")


def backfill_path() -> str:
    """Prefer the committed copy; fall back to the ephemeral scratchpad.

    The scratchpad is reclaimed with the container, so the first run that
    finds it there also commits a copy.
    """
    if os.path.exists(BACKFILL_COPY):
        return BACKFILL_COPY
    if os.path.exists(SCRATCH):
        return SCRATCH
    raise SystemExit("chain backfill not found; cannot recompute "
                     "maker_fraction from primary data")


def maker_fraction():
    path = backfill_path()
    raw = open(path, "rb").read()
    d = json.loads(raw)
    fills = [f for r in d["rows"] for f in r["fills"]]
    roles = collections.Counter(str(f.get("l5_role") or "MISSING")
                                for f in fills)
    missing = roles.get("MISSING", 0)
    if missing:
        # Never infer a role. An unlabelled log is reported, not bucketed.
        print("WARNING %d fills carry no l5_role" % missing)
    denom = sum(v for k, v in roles.items() if k != "MISSING")
    maker = roles.get("maker", 0)
    if path != BACKFILL_COPY:
        os.makedirs(os.path.dirname(BACKFILL_COPY), exist_ok=True)
        open(BACKFILL_COPY, "wb").write(raw)
        print("committed a durable copy of the chain backfill")
    holes = sum(int(r.get("chunk_holes") or 0) for r in d["rows"])
    return {
        "value": round(maker / denom, 4) if denom else None,
        "source": BACKFILL_COPY,
        "definition": ("count of on-chain OrderFilled logs in which L5 is "
                       "the maker party, over all L5 fills in the Zenodo "
                       "BTC15m overlap; l5_role is read from the log, never "
                       "inferred from book state"),
        "inputs": {
            "windows": len(d["rows"]),
            "windows_with_l5_fills": sum(1 for r in d["rows"]
                                         if r["fills"]),
            "l5_fills": len(fills),
            "role_counts": dict(sorted(roles.items())),
            "unlabelled_fills_excluded_from_denominator": missing,
            "chunk_holes": holes,
        },
        "caveats": [
            "BTC15m only, 33 windows, 12 of which carry L5 fills: this is "
            "not a corpus-wide maker share.",
            "A chunk hole would be a queried-range gap, not an absence of "
            "fills; holes are reported above and must be 0 for the count "
            "to be complete.",
        ],
        "sha256_of_source": hashlib.sha256(raw).hexdigest(),
    }


class _Any:
    """`in` is always true: load_fills filters by slug, we want every slug.

    Passing this instead of editing the verified loader keeps that loader
    byte-identical.
    """

    def __contains__(self, _):
        return True


def burst_stats(per):
    tot = collections.Counter()
    for slug, tr in per.items():
        legs = V.legs_for(tr)
        for bz in L.bursts(legs):
            n = len(bz)
            both = len({x["oi"] for x in bz}) == 2
            tot["bursts"] += 1
            if both:
                tot["both"] += 1
            if n >= 3:
                tot["bursts_ge3"] += 1
                if both:
                    tot["both_ge3"] += 1
    return tot


def burst_benchmark():
    per = V.load_fills(_Any())
    tot = burst_stats(per)
    # The prose figure was measured on the 27 L2-overlap markets only.
    # Reported alongside so a difference reads as a POPULATION change
    # rather than a contradiction.
    try:
        ser = V.load_books()
        overlap = sorted({s for (s, _oc) in ser})
        sub = burst_stats({s: tr for s, tr in per.items() if s in overlap})
    except Exception as exc:                                   # noqa: BLE001
        sub, overlap = None, []
        print("overlap subset unavailable: %s" % exc)
    ge3 = tot["bursts_ge3"]
    sub_val = (round(sub["both_ge3"] / sub["bursts_ge3"], 4)
               if sub and sub["bursts_ge3"] else None)
    return {
        "value": round(tot["both_ge3"] / ge3, 4) if ge3 else None,
        "source": "scripts/l5_regen_benchmarks.py over data/l5/raw",
        "definition": ("fraction of bursts of >= 3 legs that fill BOTH "
                       "outcomes, where a burst is a maximal run of legs "
                       "<= %gs apart (l5_ladder_v1.BURST_GAP_S, imported)"
                       % L.BURST_GAP_S),
        "inputs": {
            "markets": len(per),
            "bursts_all": tot["bursts"],
            "bursts_ge3": ge3,
            "both_outcomes_ge3": tot["both_ge3"],
            "both_outcomes_all": tot["both"],
            "burst_gap_s": L.BURST_GAP_S,
        },
        "l2_overlap_subset": {
            "note": ("the population the 0.716 prose figure was measured "
                     "on; a difference from the corpus-wide value above is "
                     "a population difference, not a correction"),
            "markets": len(overlap),
            "value_ge3": sub_val,
            "inputs": dict(sorted(sub.items())) if sub else None,
        },
        "caveats": [
            "This is a DESCRIPTION of the observed fill process, not "
            "evidence that the second leg was a decision: a burst filling "
            "both outcomes within seconds is consistent with two resting "
            "orders being taken, which is why Candidate V2 was killed.",
            "Conditioned on >= 3 legs. The unconditioned fraction is "
            "reported here as both_outcomes_all and is much lower.",
        ],
    }


def main() -> int:
    bench = json.load(open(BENCH, encoding="utf-8"))
    out = {}
    for key, fn in (("maker_fraction", maker_fraction),
                    ("two_outcome_burst_incidence_ge3", burst_benchmark)):
        old = bench.get(key, {})
        new = fn()
        out[key] = new
        ov, nv = old.get("value"), new["value"]
        agree = (ov is not None and nv is not None
                 and abs(ov - nv) <= 0.0005)
        print("%-34s prose %-8s recomputed %-8s %s"
              % (key, ov, nv, "AGREES" if agree else "DIFFERS"))
        if not agree:
            # A prose figure that reproduces EXACTLY on the population it
            # was measured on is not wrong; it is narrower than the entry
            # replacing it. Only mark SUPERSEDED when it reproduces on
            # NEITHER population.
            sub = (new.get("l2_overlap_subset") or {}).get("value_ge3")
            reproduced = (ov is not None and sub is not None
                          and abs(ov - sub) <= 0.0005)
            new["prior_entry"] = {
                "value": ov,
                "source": old.get("source"),
                "status": ("POPULATION_SCOPED" if reproduced
                           else "SUPERSEDED"),
                "note": (("reproduces to %.4f on the L2-overlap subset it "
                          "was measured on; the entry above is corpus-wide"
                          % sub) if reproduced else
                         "does not reproduce on any population measured "
                         "here"),
            }
            print("   prior entry %s"
                  % new["prior_entry"]["status"])
        bench[key] = new
    with open(BENCH, "w", encoding="utf-8") as fh:
        json.dump(bench, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("\nwrote %s" % BENCH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
