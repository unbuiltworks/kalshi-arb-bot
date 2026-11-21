"""Per-window coverage on the canonical Gemini 5m tape, and the eligibility
masks. STATE eligibility and EXECUTION eligibility are separate: a window can
support habitat/state analysis while its execution evidence stays UNKNOWN."""
import collections
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CANON = os.path.join(HERE, "gemini_5m_canonical.jsonl.gz")
DUR_S = 300.0
# the engine's own entry window, unchanged
ENTRY_LO, ENTRY_HI = 105.0, 300.0
# STATE eligibility: enough of the window observed, densely enough, to talk
# about state transitions at all. Deliberately looser than execution.
STATE_MIN_SPAN_S, STATE_MIN_OBS, STATE_MAX_GAP_S = 60.0, 8, 90.0


def q(v, p):
    v = sorted(x for x in v if x is not None)
    return v[min(len(v) - 1, int(p * (len(v) - 1)))] if v else None


def main():
    win = collections.defaultdict(lambda: {"bbo": [], "print": []})
    asset = {}
    for line in gzip.open(CANON, "rt"):
        r = json.loads(line)
        win[r["market"]][r["kind"]].append(r)
        asset[r["market"]] = r.get("asset")
    out = {}
    for m, d in win.items():
        b = sorted(d["bbo"], key=lambda r: r["t"])
        pr = d["print"]
        ts = [r["t"] for r in b]
        two = [r for r in b if r.get("bid") and r.get("ask")]
        inw = [r for r in b if r.get("tte_s") is not None
               and ENTRY_LO <= r["tte_s"] <= ENTRY_HI]
        inw_two = [r for r in inw if r.get("bid") and r.get("ask")]
        span = (ts[-1] - ts[0]) if len(ts) > 1 else 0.0
        gaps = [b2 - a for a, b2 in zip(ts, ts[1:])] if len(ts) > 1 else []
        # coverage of the 300 s window: seconds between first and last obs
        # that fall INSIDE the window, never extrapolated past them
        inwin = [r["t"] for r in b if r.get("tte_s") is not None
                 and 0 <= r["tte_s"] <= DUR_S]
        cov_s = (max(inwin) - min(inwin)) if len(inwin) > 1 else 0.0
        out[m] = {
            "asset": asset.get(m), "bbo": len(b), "two_sided": len(two),
            "prints": len(pr), "span_s": span,
            "obs_per_min": (len(b) / (span / 60.0)) if span > 0 else None,
            "max_gap_s": max(gaps) if gaps else None,
            "window_cov_s": cov_s,
            "in_entry_window": len(inw), "in_entry_two_sided": len(inw_two),
            # IN-WINDOW coverage is the requirement. A market observed
            # densely for an hour BEFORE it opens says nothing about the
            # states L5 would have traded, and an earlier version of this
            # mask counted exactly those: 285 "state eligible" markets whose
            # median in-window coverage was ZERO seconds.
            "state_eligible": (cov_s >= 120.0 and len(inw_two) >= 8),
            # execution evidence must ALSO be in-window: a print outside
            # the window cannot support a fill claim inside it
            "execution_strict": (cov_s >= 120.0 and len(inw_two) >= 8
                                 and len([x for x in pr
                                          if x.get("tte_s") is not None
                                          and 0 <= x["tte_s"] <= DUR_S]) >= 1),
            "execution_supported": (cov_s >= 120.0 and len(inw_two) >= 30),
            "no_quote_coverage": len(inw_two) == 0,
        }
    print(f"markets: {len(out)}\n")
    hdr = f"{'':22}{'BTC':>12}{'ETH':>12}{'BOTH':>12}"
    print(hdr); print("-" * len(hdr))
    groups = {"BTC": [v for v in out.values() if v["asset"] == "BTC"],
              "ETH": [v for v in out.values() if v["asset"] == "ETH"],
              "BOTH": list(out.values())}

    def row(label, fn):
        print(f"{label:22}" + "".join(f"{fn(groups[g]):>12}" for g in
                                      ("BTC", "ETH", "BOTH")))
    row("windows discovered", lambda v: len(v))
    row("complete 300s cov", lambda v: sum(1 for x in v if x["window_cov_s"] >= 290))
    row("cov>=240s", lambda v: sum(1 for x in v if x["window_cov_s"] >= 240))
    row("cov>=120s", lambda v: sum(1 for x in v if x["window_cov_s"] >= 120))
    row("median cov_s", lambda v: f"{q([x['window_cov_s'] for x in v], .5):.0f}")
    row("median obs/min", lambda v: f"{(q([x['obs_per_min'] for x in v], .5) or 0):.1f}")
    row("median max gap s", lambda v: f"{(q([x['max_gap_s'] for x in v], .5) or 0):.1f}")
    row("p90 max gap s", lambda v: f"{(q([x['max_gap_s'] for x in v], .9) or 0):.1f}")
    row("two-sided obs p50", lambda v: q([x["two_sided"] for x in v], .5))
    row("prints/window p50", lambda v: q([x["prints"] for x in v], .5))
    row("windows >=1 print", lambda v: sum(1 for x in v if x["prints"] >= 1))
    row("STATE_ELIGIBLE", lambda v: sum(1 for x in v if x["state_eligible"]))
    row("EXEC strict-elig", lambda v: sum(1 for x in v if x["execution_strict"]))
    row("EXEC supported-elig", lambda v: sum(1 for x in v if x["execution_supported"]))
    row("habitat-only", lambda v: sum(1 for x in v if x["state_eligible"]
                                      and not x["execution_strict"]))
    row("UNUSABLE", lambda v: sum(1 for x in v if not x["state_eligible"]))
    row("NO_QUOTE_COVERAGE", lambda v: sum(1 for x in v if x["no_quote_coverage"]))
    nq = sum(1 for x in out.values() if x["no_quote_coverage"])
    print(f"\nNO_QUOTE_COVERAGE on the COMPLETE tape: {nq}/{len(out)} = "
          f"{nq/len(out)*100:.1f}%   (prior curated-extract figure: 70.5%)")
    json.dump(out, open(os.path.join(HERE, "gemini_5m_coverage.json"), "w"),
              indent=1, default=str)


if __name__ == "__main__":
    main()
