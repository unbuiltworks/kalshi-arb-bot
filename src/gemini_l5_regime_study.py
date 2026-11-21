"""Ex-ante regime study for the Gemini transfer blocker (OWNER 5555118026).

Question: longer rests raise strict first-leg fills but worsen adverse
selection on the retained Gemini tape. Is there an OBSERVABLE-BEFORE-ENTRY
regime (spread, pre-entry move, time-to-expiry, depth, trailing flow)
where strict transfer economics improve?

No hindsight: every feature is computed from tape rows and prints strictly
BEFORE the entry quote; the candidate rules are frozen in
`analysis/l5/gemini_regime_candidates.json` before any validation number is
read; the tape is split by DAY (train 260830-260831, validation
260901-260902); a rule is selected on TRAIN only (best strict net per
shell, minimum admitted windows) and then reported on VALIDATION. The L5
action relation is reported as a descriptive segment only. Nothing here
touches the live evaluation sample or the frozen transfer gate.

    python3 -m src.gemini_l5_regime_study
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Mapping, Optional

from src import gemini_l5_shadow_v2 as gs
from src import pmus_l5_fidelity_live as fid
from src import pmus_l5_persistence_v2 as pv2
from src.polymarket_l5 import transfer_backtest as tb

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "analysis/l5/gemini_regime_candidates.json"
OUT = ROOT / "analysis/l5/gemini_regime_study.json"
TRAIN_DAYS = ("260830", "260831")
VALID_DAYS = ("260901", "260902")
SHELLS = ("touch_cancel_30s", "rest_120s", "reprice_180s")
MIN_ADMITTED_TRAIN = 10
LABELS = ("THEORETICAL; strict fills; ex-ante features only; rules frozen "
          "before validation; selection on train days only")


def _day(symbol: str) -> str:
    return tb.event_of_symbol(symbol)[len("BTC05M"):][:6]


def entry_features(bbo_rows: list, prints: list, entry_t: float) -> dict:
    """Everything observable at the entry quote (rows/prints < entry_t
    plus the entry row itself)."""
    before = [r for r in bbo_rows if r["t"] <= entry_t and gs._two_sided(r)]
    entry = before[-1] if before else None
    if entry is None:
        return {}
    bid, ask = D(entry["bid"]), D(entry["ask"])
    mid = float((bid + ask) / 2)
    m60 = gs._mid_at(before, entry_t - 60.0)
    m120 = gs._mid_at(before, entry_t - 120.0)
    pre = [p for p in prints if p["timestampms"] / 1000.0 < entry_t
           and p["timestampms"] / 1000.0 >= entry_t - 120.0]
    sides = {p["type"] for p in pre}
    expiry = tb.ticker_expiry_epoch(tb.event_of_symbol(entry["symbol"]))
    return {
        "spread": float(ask - bid),
        "depth_min": float(min(D(entry["bid_sz"] or 0), D(entry["ask_sz"] or 0))),
        "move_60s": (abs(mid - float(m60)) if m60 is not None else None),
        "move_120s": (abs(mid - float(m120)) if m120 is not None else None),
        "trailing_prints_120s": len(pre),
        "trailing_two_sided_120s": sides == {"buy", "sell"},
        "tte_s": (expiry - entry_t) if expiry else None,
        "mid": mid,
    }


def rule_admits(rule: Mapping[str, Any], f: Mapping[str, Any]) -> Optional[bool]:
    """None when a required feature is unobservable (window excluded)."""
    for cond in rule["all"]:
        key, op, val = cond["feature"], cond["op"], cond["value"]
        x = f.get(key)
        if x is None:
            return None
        if op == "<=" and not x <= val:
            return False
        if op == ">=" and not x >= val:
            return False
        if op == "==" and not x == val:
            return False
    return True


def _arm_stats(rows: list) -> dict:
    ent = [r for r in rows if r.get("status") in ("MATCHED", "RESIDUAL_HOLD", "NO_FILL")]
    if not ent:
        return {"windows": 0}
    s = gs.summarize(ent)
    return {"windows": len(ent), "first_fills": s["first_leg_fills"],
            "fill_rate_pct": s["campaign_first_fill_rate"],
            "matched": s["two_sided_fill_campaigns"],
            "holds": s["one_sided_holds"],
            "net_strict": s["net_theoretical_total"],
            "net_per_window": round(float(s["net_theoretical_total"]) / len(ent), 4),
            "adverse_30s_pct": s["adverse_selection_30s"]["pct_adverse"],
            "adverse_n": s["adverse_selection_30s"]["fills_measured"]}


def _segment(rows: list, key, buckets) -> dict:
    """Descriptive: first-fill adverse share and hold outcome by bucket."""
    out: dict = {}
    for r in rows:
        f = r.get("features") or {}
        b = key(f, r)
        if b is None:
            continue
        d = out.setdefault(b, {"windows": 0, "first_fills": 0, "adverse": 0,
                               "measured": 0, "holds": 0, "net": D("0")})
        d["windows"] += 1
        d["net"] += D(r.get("pair_pnl_theoretical") or 0) + D(r.get("hold_pnl_theoretical") or 0)
        d["holds"] += int(r.get("status") == "RESIDUAL_HOLD")
        for fl in r.get("fills") or []:
            if fl["relation"] == "complete":
                continue
            d["first_fills"] += 1
            if fl.get("adverse") is not None:
                d["measured"] += 1
                d["adverse"] += int(bool(fl["adverse"]))
    return {b: {**{k: (str(v) if isinstance(v, D) else v) for k, v in d.items()},
                "adverse_pct": (round(100.0 * d["adverse"] / d["measured"], 1)
                                if d["measured"] else None)}
            for b, d in sorted(out.items(), key=lambda kv: str(kv[0]))}


def _bucket(x, edges):
    if x is None:
        return None
    for hi, label in edges:
        if x < hi:
            return label
    return edges[-1][1]


SPREAD_B = ((0.03, "spread<3c"), (0.06, "spread3-6c"), (0.12, "spread6-12c"), (9, "spread>=12c"))
MOVE_B = ((0.01, "move<1c"), (0.03, "move1-3c"), (0.06, "move3-6c"), (9, "move>=6c"))
DEPTH_B = ((100, "depth<100"), (300, "depth100-300"), (1e9, "depth>=300"))
TTE_B = ((150, "tte<150"), (240, "tte150-240"), (1e9, "tte>=240"))


def run(bbo_path: Path = gs.BBO_PATH, prints_path: Path = gs.PRINTS_PATH,
        candidates_path: Path = CANDIDATES) -> dict:
    cands = json.loads(candidates_path.read_text())
    model, art = pv2.load_v2(), fid.load()
    bbo, prints = tb.load_bbo(bbo_path), tb.load_prints(prints_path)
    symbols = sorted(s for s in bbo if s in prints)
    per_shell: dict[str, list] = {}
    for shell in SHELLS:
        rows = []
        for sym in symbols:
            r = gs.replay_window(sym, bbo[sym], prints[sym], model=model, art=art,
                                 policy="modal", mode="strict",
                                 shell={**gs.SHELLS[shell], "name": shell})
            if r.get("status") not in ("MATCHED", "RESIDUAL_HOLD", "NO_FILL"):
                continue
            r["features"] = entry_features(bbo[sym], prints[sym], r["entry_t"])
            r["day"] = _day(sym)
            r["first_relation"] = next((d["acted"] for d in r["decisions"]
                                        if d["acted"] in ("open", "extend", "complete")), None)
            rows.append(r)
        per_shell[shell] = rows
    result: dict[str, Any] = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "labels": LABELS, "candidates_sha": cands.get("_sha256_16"),
        "candidates_frozen_utc": cands.get("frozen_utc"),
        "split": {"train_days": TRAIN_DAYS, "validation_days": VALID_DAYS},
        "shells": {},
        "descriptive_segments": {},
    }
    for shell, rows in per_shell.items():
        train = [r for r in rows if r["day"] in TRAIN_DAYS]
        valid = [r for r in rows if r["day"] in VALID_DAYS]
        base = {"train": _arm_stats(train), "validation": _arm_stats(valid)}
        rules_out = {}
        best_name, best_net = None, None
        for rule in cands["rules"]:
            adm_t = [r for r in train if rule_admits(rule, r["features"]) is True]
            adm_v = [r for r in valid if rule_admits(rule, r["features"]) is True]
            st = _arm_stats(adm_t)
            rules_out[rule["name"]] = {"train": st, "validation": _arm_stats(adm_v),
                                       "train_admit_pct": round(100.0 * len(adm_t) / max(len(train), 1), 1)}
            if st["windows"] >= MIN_ADMITTED_TRAIN:
                net = float(st["net_strict"])
                if best_net is None or net > best_net:
                    best_name, best_net = rule["name"], net
        result["shells"][shell] = {
            "unconditional": base, "rules": rules_out,
            "selected_on_train": best_name,
            "selected_validation": (rules_out[best_name]["validation"] if best_name else None),
            "selected_train": (rules_out[best_name]["train"] if best_name else None),
            "improves_on_validation": (
                best_name is not None and rules_out[best_name]["validation"].get("windows", 0) > 0
                and float(rules_out[best_name]["validation"]["net_strict"])
                > float(base["validation"]["net_strict"] or 0)
                and float(rules_out[best_name]["validation"]["net_strict"]) >= 0),
        }
        result["descriptive_segments"][shell] = {
            "by_spread": _segment(rows, lambda f, r: _bucket(f.get("spread"), SPREAD_B), SPREAD_B),
            "by_move_60s": _segment(rows, lambda f, r: _bucket(f.get("move_60s"), MOVE_B), MOVE_B),
            "by_depth": _segment(rows, lambda f, r: _bucket(f.get("depth_min"), DEPTH_B), DEPTH_B),
            "by_tte": _segment(rows, lambda f, r: _bucket(f.get("tte_s"), TTE_B), TTE_B),
            "by_trailing_flow": _segment(rows, lambda f, r: ("two_sided" if f.get("trailing_two_sided_120s")
                                                             else "one_sided_or_none"), ()),
            "by_first_relation": _segment(rows, lambda f, r: r.get("first_relation"), ()),
        }
    return result


def main() -> int:
    res = run()
    OUT.write_text(json.dumps(res, indent=1, default=str, sort_keys=True))
    for shell, s in res["shells"].items():
        print(shell, "uncond", s["unconditional"], "selected", s["selected_on_train"],
              "valid", s["selected_validation"], "improves", s["improves_on_validation"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
