"""Strict OOS counterfactual: frozen stochastic policy vs deterministic
modal on the retained Gemini BTC-15m tape (OWNER 5555609705 item 3).

Why this partition: the 15-minute tape (`gemini_bbo_btc15m.jsonl.gz`,
`gemini_prints_15m_tail.jsonl.gz`) was only ever used by the earlier v1
gate replay; no v2 shell, arm, threshold or hypothesis was chosen on it,
so it is untouched by the policy design. Caveats stated in the output:
different horizon (15 m windows; entry still at TTE 105-300 s), a print
TAIL only (46 symbols with prints, 29 with both quotes and prints), so
the sample is small and every number is THEORETICAL.

    python3 -m src.gemini_l5_counterfactual_15m
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src import gemini_l5_shadow_v2 as gs
from src import pmus_l5_fidelity_live as fid
from src import pmus_l5_persistence_v2 as pv2
from src.polymarket_l5 import transfer_backtest as tb

ROOT = Path(__file__).resolve().parents[1]
BBO = ROOT / "data/transfer_backtest/gemini_bbo_btc15m.jsonl.gz"
PRINTS = ROOT / "data/transfer_backtest/gemini_prints_15m_tail.jsonl.gz"
OUT = ROOT / "analysis/l5/gemini_counterfactual_15m.json"
ROWS_OUT = ROOT / "analysis/l5/gemini_counterfactual_15m_rows.jsonl"
SHELLS = ("touch_cancel_30s", "rest_120s", "reprice_180s")


def _trim(r: dict) -> dict:
    d = []
    for x in r.get("decisions") or []:
        d.append({"t": x.get("t"), "state": x.get("state"), "acted": x.get("acted"),
                  "refusal": x.get("refusal"), "v2_recommended": x.get("v2_recommended"),
                  "off_distribution": x.get("off_distribution"),
                  "p_complete": next((y["p"] for y in x.get("relation_ranked") or [] if y.get("relation") == "complete"), 0.0),
                  "p_extend": next((y["p"] for y in x.get("relation_ranked") or [] if y.get("relation") == "extend"), 0.0),
                  "sampled": ((x.get("shadow") or {}).get("stochastic") or {}).get("sampled"),
                  "seed": ((x.get("shadow") or {}).get("stochastic") or {}).get("seed"),
                  "residual_units": (x.get("inventory_before") or {}).get("behavioral_residual")})
    return {k: r.get(k) for k in ("symbol", "status", "policy", "shell", "entry_t", "entry_tte_s",
                                  "orders", "fills", "inventory", "pair_unit_cost",
                                  "pair_cost_after_fees", "fees_theoretical", "outcome_proxy",
                                  "pair_pnl_theoretical", "hold_pnl_theoretical", "flow")} | {"decisions": d}


def run() -> dict:
    model, art = pv2.load_v2(), fid.load()
    bbo, prints = tb.load_bbo(BBO), tb.load_prints(PRINTS)
    symbols = sorted(s for s in bbo if s in prints)
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(),
           "labels": "THEORETICAL strict counterfactual on the untouched 15m partition; "
                     "tiny print-tail sample; provenance per row in the .jsonl",
           "provenance": {"bbo": BBO.name, "prints": PRINTS.name, "symbols": len(symbols),
                          "model": model.get("_sha256_16"),
                          "stochastic_spec": json.loads((ROOT / "analysis/l5/gemini_research_arm_stochastic.json").read_text()).get("_sha256_16"),
                          "untouched_by": "every v2 shell/arm/threshold/hypothesis choice was made on the 5m tape or live windows"},
           "arms": {}}
    with ROWS_OUT.open("w", encoding="utf-8") as fh:
        for shell in SHELLS:
            for policy in gs.POLICIES:
                rows = [gs.replay_window(s, bbo[s], prints[s], model=model, art=art,
                                         policy=policy, mode="strict",
                                         shell={**gs.SHELLS[shell], "name": shell})
                        for s in symbols]
                ent = [r for r in rows if r.get("status") in ("MATCHED", "RESIDUAL_HOLD", "NO_FILL")]
                trimmed = [_trim(r) for r in ent]
                for tr in trimmed:
                    fh.write(json.dumps(tr, default=str) + "\n")
                s = gs.summarize(ent) if ent else {}
                sc = gs.action_scoring(trimmed)
                out["arms"][f"{shell}:{policy}"] = {
                    "windows": len(ent),
                    "economics": {k: s.get(k) for k in (
                        "orders_submitted", "first_leg_fills", "campaign_first_fill_rate",
                        "complement_orders", "complement_fills", "two_sided_fill_campaigns",
                        "matched_pairs_le_099", "median_pair_cost_after_fees",
                        "one_sided_holds", "holds_residual_won", "holds_residual_lost",
                        "holds_ambiguous_excluded", "fees_theoretical_total",
                        "pair_pnl_theoretical_total", "hold_pnl_theoretical_total",
                        "net_theoretical_total", "adverse_selection_by_horizon")},
                    "fidelity": {k: sc.get(k) for k in (
                        "scored_decisions", "expected_relation_freq_l5", "sampled_relation_freq",
                        "l1_sampled_vs_l5", "acted_relation_freq", "l1_acted_vs_l5",
                        "hold_share_of_decisions", "hold_refusals", "post_fill",
                        "off_distribution_rate_pct")},
                }
    return out


def main() -> int:
    res = run()
    OUT.write_text(json.dumps(res, indent=1, default=str, sort_keys=True))
    for arm, a in res["arms"].items():
        e, f = a["economics"], a["fidelity"]
        print(arm, "w", a["windows"], "fills", e.get("first_leg_fills"), "comp", e.get("complement_fills"),
              "matched", e.get("two_sided_fill_campaigns"), "holds", e.get("one_sided_holds"),
              "net", e.get("net_theoretical_total"), "| dec", f.get("scored_decisions"),
              "L1s", f.get("l1_sampled_vs_l5"), "L1a", f.get("l1_acted_vs_l5"),
              "post", (f.get("post_fill") or {}).get("decisions"),
              "gap_s", (f.get("post_fill") or {}).get("complete_freq_gap_pp_sampled"),
              "gap_a", (f.get("post_fill") or {}).get("complete_freq_gap_pp_acted"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
