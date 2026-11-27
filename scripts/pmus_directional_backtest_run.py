"""Driver: baseline vs directional allocation on the recorded two-venue books
(21 Nov 2026, CFB/NBA/NHL). THEORETICAL; see src/pmus_directional_backtest.py.
usage: python3 scripts/pmus_directional_backtest_run.py [--offline]"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import pmus_directional_backtest as B  # noqa: E402

REC = ["analysis/l5/sports_wallets/cross_venue_20261121_2027_2217.jsonl.gz",
       "analysis/l5/sports_wallets/cross_venue_20261121_2218_0015_sizes.jsonl.gz"]
OUT = Path("analysis/l5/sports/directional")
META = OUT / "market_meta_20261121.json"

POLICIES = [
    ("baseline", {"directional": False}),
    ("directional_l0", {"directional": True, "cfg": {"risk_aversion": "0"}}),
    ("directional_l0.0025", {"directional": True,
                             "cfg": {"risk_aversion": "0.0025"}}),
    ("directional_l0.005", {"directional": True,
                            "cfg": {"risk_aversion": "0.005"}}),
    ("directional_l0.01", {"directional": True,
                           "cfg": {"risk_aversion": "0.01"}}),
    ("directional_l0.005_nodepth", {"directional": True,
                                    "cfg": {"risk_aversion": "0.005",
                                            "depth_unfavoured_ticks": 0}}),
    ("adds_only_l0.005", {"directional": True,
                          "cfg": {"risk_aversion": "0.005",
                                  "judge_openings": False}}),
    ("adds_only_l0.005_fl_lead", {"directional": True,
                                  "fill_loss_touch": "0.02",
                                  "fill_loss_behind": "0.01",
                                  "cfg": {"risk_aversion": "0.005",
                                          "judge_openings": False}}),
    ("adds_only_l0.01_fl_lead", {"directional": True,
                                 "fill_loss_touch": "0.02",
                                 "fill_loss_behind": "0.01",
                                 "cfg": {"risk_aversion": "0.01",
                                         "judge_openings": False}}),
    ("full_l0.005_fl_lead", {"directional": True,
                             "fill_loss_touch": "0.02",
                             "fill_loss_behind": "0.01",
                             "cfg": {"risk_aversion": "0.005"}}),
]
FILLS = [("touch", {"fill": "touch"}), ("through", {"fill": "through"})]


def main():
    frames = B.load_cross_venue(REC)
    if "--offline" in sys.argv and META.exists():
        meta = json.load(open(META))
    else:
        meta = B.fetch_meta(frames.keys(), str(META))
    for s, fr in frames.items():
        meta.setdefault(s, {})
        meta[s]["flip"] = None
        al = B.align_intl(fr)
        if al is not None:
            meta[s]["flip"] = not al
    res = {}
    lines = []
    for fname, fsim in FILLS:
        for pname, pol in POLICIES:
            r = B.run(frames, meta, pol, fsim)
            key = "%s/%s" % (fname, pname)
            res[key] = r
            lines.append("%-34s mkts %3d traded %3d fills %4d ctr %7s spend %8s "
                         "trading %8s (matched %7s, residual %8s) "
                         "per-mkt %+.3f se %s peak %7s ROC %s turnover %s" % (
                             key, r["markets"], r["markets_traded"], r["fills"],
                             r["contracts"], r["spend_usd"][:8],
                             r["1_settled_trading_pnl_after_fees_usd"][:8],
                             r["6_matched_pnl_usd"][:7],
                             r["6_residual_pnl_usd"][:8],
                             r["per_market_mean_usd"], r["per_market_se_usd"],
                             r["7_peak_capital_usd"][:7],
                             r["7_return_on_peak_capital"],
                             r["7_capital_turnover"]))
    sk = next(iter(res.values()))["skipped"]
    head = ["THEORETICAL backtest: recorded books, modelled fills, nothing "
            "realized. Recordings: " + ", ".join(REC),
            "markets in recordings: %d; skipped: %d %s" % (
                len(frames), len(sk),
                json.dumps({v: sum(1 for x in sk.values() if x == v)
                            for v in set(sk.values())}))]
    OUT.mkdir(parents=True, exist_ok=True)
    json.dump(res, open(OUT / "BACKTEST_RESULT_20261121.json", "w"),
              indent=1, default=str)
    (OUT / "BACKTEST_RESULT_20261121.txt").write_text(
        "\n".join(head + lines) + "\n")
    print("\n".join(head + lines))


if __name__ == "__main__":
    main()
