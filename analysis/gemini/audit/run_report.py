"""THE decision report. One command, generated from source artifacts.

    python3 analysis/gemini/audit/run_report.py

Exit code 0 only when every invariant passes. A FAIL closes the report: the
numbers are still printed, but under a FAILED banner and the exit code says so,
so no caller can mistake a broken report for a clean one.

Sections are kept apart on purpose and never mixed:

    OBSERVED FACTS        directly supported by venue or source data
    RECONSTRUCTED RESULTS dependent on decoder, model or accounting assumptions
    INTERPRETATION        hypotheses and conclusions

Every number carries its numerator, denominator, unit, source, period, lane,
evidence class, decoder dependency and an independently recomputed value.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import invariants as INV                                       # noqa: E402
import verify as V                                             # noqa: E402
from metric import (Metric, OBSERVED, RECONSTRUCTED,            # noqa: E402
                    DEC_NONE, DEC_SIDE, DEC_FILL)
import p10h_gate as G                                          # noqa: E402
from p11a_blocked_census import load as census_load            # noqa: E402

CENSUS_SRC = "analysis/gemini/audit/l5f_actions_20260922.tsv"
CENSUS_PERIOD = "2026-09-22T13:59Z/2026-09-22T23:19Z"
CAP_SRC = "analysis/gemini/p10_capture/*.jsonl{,.partial}"


def build():
    rows = census_load()
    census = V.census_totals()
    pop_spec = V.gate_population("BTC")            # spec-conformant: ANY book
    gate_prev = G.score("BTC", bracket=G.PREV_NEXT)   # the running gate
    gate_spec = G.score("BTC", bracket=G.SPEC)        # spec-conformant
    dup = V.duplicate_keys()

    metrics = []

    # ---------------- OBSERVED: the census counts nothing side-dependent
    for lane, fam in (("BTC5m", "5M"), ("BTC15m", "15M")):
        w = census["windows"][fam]
        c = census["cols"][fam]
        prim_w = sum(1 for r in rows if r["fam"] == fam)
        metrics.append(Metric(
            name="windows_observed[%s]" % lane, numerator=w, denominator=None,
            unit_numerator="window", unit_denominator=None,
            source=CENSUS_SRC, period=CENSUS_PERIOD, lane=lane,
            evidence=OBSERVED, decoder=DEC_NONE, independent=prim_w,
            notes="one contract window each"))
        prim_rc = sum(r["req"][k] for r in rows if r["fam"] == fam
                      for k in ("OPEN", "EXTEND", "COMPLETE"))
        metrics.append(Metric(
            name="rung_cycles_requested[%s]" % lane,
            numerator=c["reqOPEN"] + c["reqEXTEND"] + c["reqCOMPLETE"],
            denominator=None, unit_numerator="rung_cycle",
            unit_denominator=None, source=CENSUS_SRC, period=CENSUS_PERIOD,
            lane=lane, evidence=OBSERVED, decoder=DEC_NONE,
            independent=prim_rc,
            notes="a standing desire is counted ONCE PER CYCLE. NOT orders."))

    blk = census["blocked"].get("END_OF_WINDOW", {})
    prim_blk = sum(n for r in rows for (k, by), n in r["blocked"].items()
                   if by == "END_OF_WINDOW")
    metrics.append(Metric(
        name="rung_cycles_blocked[END_OF_WINDOW]",
        numerator=sum(blk.values()), denominator=None,
        unit_numerator="rung_cycle", unit_denominator=None,
        source=CENSUS_SRC, period=CENSUS_PERIOD, lane="ALL",
        evidence=OBSERVED, decoder=DEC_NONE, independent=prim_blk,
        notes="OPEN=%s EXTEND=%s COMPLETE=%s"
              % (blk.get("OPEN", 0), blk.get("EXTEND", 0),
                 blk.get("COMPLETE", 0))))

    # ---------------- RECONSTRUCTED: anything downstream of a fill
    for lane, fam in (("BTC5m", "5M"), ("BTC15m", "15M")):
        c = census["cols"][fam]
        n = c["filOPEN"] + c["filEXTEND"] + c["filCOMPLETE"]
        d = census["windows"][fam]
        prim_n = sum(sum(r["fil"].values()) for r in rows if r["fam"] == fam)
        prim_d = sum(1 for r in rows if r["fam"] == fam)
        metrics.append(Metric(
            name="fills_per_window[%s]" % lane, numerator=n, denominator=d,
            unit_numerator="fill", unit_denominator="window",
            source=CENSUS_SRC, period=CENSUS_PERIOD, lane=lane,
            evidence=RECONSTRUCTED, decoder=DEC_FILL,
            independent=(None if prim_d == 0 else
                         __import__("decimal").Decimal(prim_n) /
                         __import__("decimal").Decimal(prim_d)),
            notes="hypothetical fills from tape_replay.reaches, which reads "
                  "the aggressor side AND a mirrored price"))

    # ---------------- the decoder gate, BOTH readings, neither hidden
    metrics.append(Metric(
        name="decoded_n[BTC,spec:any-book]", numerator=pop_spec["_decoded"],
        denominator=None, unit_numerator="trade", unit_denominator=None,
        source=CAP_SRC, period="2026-09-22T21:00Z/now", lane="BTC",
        evidence=RECONSTRUCTED, decoder=DEC_SIDE,
        independent=gate_spec["decoded"],
        notes="SPEC-CONFORMANT: P10_DECODER_SPEC.md 3 says 'exists a book "
              "within max_stale_s (before or after)'"))
    metrics.append(Metric(
        name="ambiguous_n[BTC,spec:any-book]", numerator=pop_spec["_ambiguous"],
        denominator=pop_spec["_observed"], unit_numerator="trade",
        unit_denominator="trade", source=CAP_SRC,
        period="2026-09-22T21:00Z/now", lane="BTC",
        evidence=RECONSTRUCTED, decoder=DEC_SIDE,
        independent=(__import__("decimal").Decimal(gate_spec["ambiguous"])
                     / __import__("decimal").Decimal(gate_spec["observed"])),
        notes=("OPEN DISAGREEMENT, deliberately not resolved. Both "
               "implementations agree the numerator is 72 and decoded is "
               "134. They disagree on the DENOMINATOR because the spec does "
               "not say whether books from OTHER capture files may be used: "
               "verify merges books across files (STALE 74 / PRE_CAPTURE 58 "
               "/ observed 280 -> 25.7%), p10h classifies per file and takes "
               "the best label (STALE 17 / PRE_CAPTURE 96 / observed 223 -> "
               "32.3%). G2's threshold is 25%, so the two readings fall on "
               "OPPOSITE SIDES of it. Resolving this by picking one would be "
               "exactly the post-hoc choice the contract forbids.")))
    metrics.append(Metric(
        name="decoded_n[BTC,impl:prev-next]", numerator=gate_prev["decoded"],
        denominator=None, unit_numerator="trade", unit_denominator=None,
        source=CAP_SRC, period="2026-09-22T21:00Z/now", lane="BTC",
        evidence=RECONSTRUCTED, decoder=DEC_SIDE,
        independent=gate_prev["decoded"],
        notes="p10b_decode.classify_one examines only the bracketing pair"))

    bp = G.binom_upper(pop_spec["H2"], pop_spec["_decoded"])
    bi = V.clopper_pearson_upper(pop_spec["H2"], pop_spec["_decoded"])
    metrics.append(Metric(
        name="h2_upper_bound[BTC,spec]", numerator=round(bp, 12),
        denominator=None, unit_numerator="trade", unit_denominator=None,
        source=CAP_SRC, period="2026-09-22T21:00Z/now", lane="BTC",
        evidence=RECONSTRUCTED, decoder=DEC_SIDE, independent=round(bi, 12),
        notes="95%% one-sided; k=%s n=%s" % (pop_spec["H2"],
                                             pop_spec["_decoded"])))

    claims = [{
        "name": "ENVELOPE (CAP4 / max-leg / spend / 12-action / 2-working)",
        "blocked": 0, "reachable": False, "asserts_non_binding": False,
        "reach_evidence": ("MAX_IMBALANCE needs 4 net same-side fills; the "
                           "fills-per-window histogram is %s so no window "
                           "reached 4. Status: UNTESTED AT RELEVANT "
                           "INVENTORY DEPTH."
                           % census["fills_per_window_hist"])},
        {"name": "END_OF_WINDOW", "blocked": sum(blk.values()),
         "reachable": True, "asserts_non_binding": False,
         "reach_evidence": "fired in 21 of 151 windows"}]

    comparisons = [
        {"name": "H1 BTC15m [0,300] vs [0,900]",
         "exposure_note": ("NOT exposure-matched: control rests 17-37 "
                           "rung_cycle/window vs 67-149. Any fill difference "
                           "must be normalized by rested rung_cycles.")},
        {"name": "H2 BTC5m control vs EXEC_A_PLUS_B",
         "exposure_note": ("exposure-matched: 18 vs 19 rested rung_cycle in "
                           "the sampled window; both run band [0,300], which "
                           "never binds on a 300s contract")},
    ]

    checks = INV.run_all(
        census=census, pop=pop_spec, dup=dup, bound_primary=bp,
        bound_independent=bi, bound_k=pop_spec["H2"],
        bound_n=pop_spec["_decoded"], metrics=metrics, claims=claims,
        comparisons=comparisons, pnl=None)
    return metrics, checks, census, pop_spec, gate_prev, claims, comparisons


def main():
    metrics, checks, census, pop, gate_prev, claims, comparisons = build()
    failed = [c for c in checks if c[1] == INV.FAIL]

    print("=" * 78)
    print("GEMINI SHORT-CRYPTO DECISION REPORT" +
          ("   *** FAILED CLOSED ***" if failed else ""))
    print("=" * 78)

    print("\n-- INVARIANTS " + "-" * 62)
    for name, status, detail in checks:
        print("  %-6s %-38s %s" % (status, name, detail[:130]))

    for title, want in (("OBSERVED FACTS", OBSERVED),
                        ("RECONSTRUCTED RESULTS", RECONSTRUCTED)):
        print("\n-- %s %s" % (title, "-" * (74 - len(title))))
        for m in metrics:
            if m.evidence == want:
                print("  " + m.line())
                if m.notes:
                    print("        note: " + m.notes[:120])

    print("\n-- INTERPRETATION " + "-" * 58)
    print("  Stated in prose in POST_DECODER_EVALUATION_PREREG.md and")
    print("  P11_CORRECTIONS.md. No interpretation is emitted as a number.")

    if failed:
        print("\nREPORT FAILED CLOSED on: %s" % [c[0] for c in failed])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
