"""Why does the MODAL policy never complete a pair while the seeded
STOCHASTIC draw does? (OWNER 5555437740, READ-ONLY, retained tape only.)

For every post-first-fill decision in the retained-tape replay (strict,
reprice_180s and rest_120s shells) we record the exact state key, the v2
relation masses (COMPLETE vs EXTEND), what modal did, what the draw did,
whether a fee-safe complement was even executable against the opposite
touch, TTE and the pair-cost bound. The same states are then looked up in
the frozen v2 artifact (historical L5 counts) to answer: is zero modal
completion the arithmetic consequence of argmax over a genuinely
stochastic policy (P(extend) > P(complete) in every visited state), or a
state/adapter mismatch (states L5 rarely visited, backed-off, or a TTE
bucket that PMUS never sees)?

    python3 -m src.gemini_l5_modal_decomposition
"""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Any

from src import gemini_l5_shadow_v2 as gs
from src import pmus_l5_fidelity_live as fid
from src import pmus_l5_persistence_v2 as pv2
from src.polymarket_l5 import transfer_backtest as tb

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "analysis/l5/gemini_modal_vs_stochastic.json"
SHELLS = ("reprice_180s", "rest_120s")
LABELS = ("READ-ONLY decomposition on the retained Gemini tape; THEORETICAL; "
          "nothing here changes live PMUS or Gemini execution")


def _post_fill_decisions(rows: list) -> list:
    out = []
    for r in rows:
        fills = [f for f in r.get("fills") or []]
        if not fills:
            continue
        first_t = min(f["t"] for f in fills)
        for d in r.get("decisions") or []:
            if d["t"] <= first_t:
                continue
            inv = d.get("inventory_before") or {}
            try:
                res = float(inv.get("residual") or 0)
            except (TypeError, ValueError):
                res = 0.0
            if res == 0:
                continue
            ranked = {x["relation"]: x["p"] for x in d.get("relation_ranked") or []}
            out.append({"symbol": r["symbol"], "policy": r["policy"], "shell": r["shell"],
                        "t": d["t"], "state": d["state"], "state_n": d.get("state_n"),
                        "off": d.get("off_distribution"),
                        "p_complete": ranked.get("complete", 0.0),
                        "p_extend": ranked.get("extend", 0.0),
                        "v2": d.get("v2_recommended"), "acted": d.get("acted"),
                        "sampled": ((d.get("shadow") or {}).get("stochastic") or {}).get("sampled"),
                        "refusal": d.get("refusal"),
                        "pair_econ": (d.get("pair_economics") or {}).get("regime"),
                        "tte_s": (d.get("book") or {}).get("tte_s"),
                        "residual_units": res})
    return out


def _hist_state(model: dict, state: str) -> dict:
    """Historical L5 counts for the exact full state and its base state."""
    base = "|".join(state.split("|")[:3])
    full = model["full"].get(state) or {}
    bcount = model["base"].get(base) or {}

    def mass(c):
        tot = sum(c.values()) or 1
        agg: dict = defaultdict(int)
        for a, n in c.items():
            agg[a.split("|")[0]] += n
        return {k: round(v / tot, 4) for k, v in agg.items()}, tot
    fm, fn = mass(full)
    bm, bn = mass(bcount)
    return {"full_n": fn if full else 0, "full_mass": fm, "base_n": bn if bcount else 0,
            "base_mass": bm}


def run() -> dict:
    model, art = pv2.load_v2(), fid.load()
    bbo, prints = tb.load_bbo(gs.BBO_PATH), tb.load_prints(gs.PRINTS_PATH)
    symbols = sorted(s for s in bbo if s in prints)
    out: dict[str, Any] = {"generated_utc": datetime.now(timezone.utc).isoformat(),
                           "labels": LABELS, "model": model.get("_sha256_16"), "shells": {}}
    for shell in SHELLS:
        per_policy = {}
        for policy in gs.POLICIES:
            rows = [gs.replay_window(s, bbo[s], prints[s], model=model, art=art,
                                     policy=policy, mode="strict",
                                     shell={**gs.SHELLS[shell], "name": shell})
                    for s in symbols]
            decs = _post_fill_decisions(rows)
            states = Counter(d["state"] for d in decs)
            # the state key is base|last_rel|last_size as the model sees it
            keyed = Counter(f"{d['state']}|{'extend' if d['residual_units'] and d['acted'] != 'open' else 'open'}" for d in decs)
            hist = {}
            for d in decs:
                k = f"{d['state']}|{d['v2'] and 'x'}"
            # exact model keys the engine looked up: base | last_rel | q<5
            exact = Counter()
            for d in decs:
                last_rel = "open" if d["residual_units"] in (1.0, -1.0) and abs(d["residual_units"]) == 1 else "extend"
                exact[f"{d['state']}|{last_rel}|q<5"] += 1
            for key in exact:
                base_state, last_rel, last_size = key.rsplit("|", 2)
                dist = pv2.v2_dist(model, base_state, last_rel, last_size)
                rel = pv2.relation_mass(dist["dist"])
                hist[key] = {"visits": exact[key], "model_n": dist["n"],
                             "backed_off": dist["backed_off"],
                             "p_complete": round(rel.get("complete", 0), 4),
                             "p_extend": round(rel.get("extend", 0), 4),
                             "argmax": max(rel, key=rel.get),
                             "historical": _hist_state(model, key)}
            comp_p = [d["p_complete"] for d in decs]
            ext_p = [d["p_extend"] for d in decs]
            per_policy[policy] = {
                "windows_with_first_fill": len({d["symbol"] for d in decs}),
                "post_fill_decisions": len(decs),
                "acted": dict(Counter(d["acted"] for d in decs)),
                "v2_recommended": dict(Counter(d["v2"] for d in decs)),
                "sampled": dict(Counter(str(d["sampled"]) for d in decs)),
                "refusals": dict(Counter(str(d["refusal"]).split(":")[0] for d in decs if d["refusal"])),
                "states": dict(states.most_common(12)),
                "p_complete_p25_p50_p75": gs._quartiles(comp_p),
                "p_extend_p25_p50_p75": gs._quartiles(ext_p),
                "decisions_where_complete_is_argmax": sum(1 for d in decs if d["p_complete"] > d["p_extend"]),
                "off_distribution_decisions": sum(1 for d in decs if d["off"]),
                "tte_s_p50_at_decision": gs._median([d["tte_s"] for d in decs]),
                "complete_attempts": sum(1 for d in decs if d["acted"] == "complete"),
                "complement_regimes": dict(Counter(str(d["pair_econ"]) for d in decs if d["acted"] == "complete")),
                "model_states": hist,
            }
        out["shells"][shell] = per_policy
    # size-bucket sensitivity: our q=1 unit keys every lookup on last_size
    # "q<5", while L5's own size in these states was mostly q5-15/q15-40.
    # Same base states, other size buckets: does COMPLETE ever become argmax?
    visited_bases = sorted({k.rsplit("|", 2)[0] for sh in out["shells"].values()
                            for pol in sh.values() for k in pol["model_states"]})
    sens = {}
    for base_state in visited_bases:
        row = {}
        for last_rel in ("open", "extend"):
            for last_size in ("q<5", "q5-15", "q15-40", "q>40"):
                dist = pv2.v2_dist(model, base_state, last_rel, last_size)
                rel = pv2.relation_mass(dist["dist"])
                row[f"{last_rel}|{last_size}"] = {
                    "n": dist["n"], "p_complete": round(rel.get("complete", 0), 4),
                    "p_extend": round(rel.get("extend", 0), 4),
                    "argmax": max(rel, key=rel.get)}
        sens[base_state] = row
    out["size_bucket_sensitivity"] = sens
    out["complete_is_argmax_anywhere"] = sorted(
        f"{b}|{k}" for b, row in sens.items() for k, v in row.items()
        if v["argmax"] == "complete" and v["n"] >= 5)
    # historical L5 completion by TTE bucket for imb1-10 states (the ones visited)
    by_tte: dict = {}
    for base_state, counts in model["base"].items():
        band, tte, imb = base_state.split("|")
        if imb != "imb1-10":
            continue
        agg = defaultdict(int)
        for a, n in counts.items():
            agg[a.split("|")[0]] += n
        tot = sum(agg.values())
        d = by_tte.setdefault(tte, {"n": 0, "complete": 0, "extend": 0})
        d["n"] += tot
        d["complete"] += agg.get("complete", 0)
        d["extend"] += agg.get("extend", 0)
    out["historical_l5_imb1_10_by_tte"] = {
        k: {"n": v["n"], "p_complete": round(v["complete"] / v["n"], 4),
            "p_extend": round(v["extend"] / v["n"], 4)} for k, v in by_tte.items()}
    return out


def main() -> int:
    res = run()
    OUT.write_text(json.dumps(res, indent=1, default=str, sort_keys=True))
    for shell, pp in res["shells"].items():
        for policy, s in pp.items():
            print(shell, policy, {k: s[k] for k in ("windows_with_first_fill", "post_fill_decisions", "acted", "sampled", "p_complete_p25_p50_p75", "decisions_where_complete_is_argmax", "off_distribution_decisions", "complete_attempts", "complement_regimes", "tte_s_p50_at_decision")})
    print(json.dumps(res["historical_l5_imb1_10_by_tte"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
