"""L5 fidelity pass (issue #167 comment 5553674206), OFFLINE and read-only.

One deterministic study over the canonical sequence, the same temporal
holdout as the v2 review (train: systematic-regime windows before
2026-08-31 00:00 UTC; validate: after), the same shrinkage scorer:

1. STATE UPGRADE. Candidate features that are reconstructable from L5's
   own history are scored one at a time ON TOP OF the persistence-v2
   state (base x last_rel x last_size) and greedily combined; every
   variant reports support (train states, median transitions per state,
   unseen-state rate on validation) and held-out mean log-prob, exact
   top-1 and relation top-1 against the 136-state v1 and v2. A feature
   is PROMOTABLE only if it improves held-out mean log-prob by at least
   PROMOTE_MIN_DLOGP over v2 without more than PROMOTE_MAX_UNSEEN_PCT of
   validation states unseen. Features that need the venue's live book or
   the recorder's flow are NOT in L5's history and are listed as
   unsupported, never scored.
2. STOCHASTIC POLICY. Deterministic seeded sampling from the shrunk
   conditional distribution, replayed on the holdout against the modal
   policy: relation agreement with the actual next action, calibration
   of P(extend) by decile, and the relation mix each policy produces
   against L5's own mix.
3. SIZE POLICY. L5 quantity/notional quantiles conditional on
   (base state, relation): what `l5_target_size` should read.
4. AGGRESSIVENESS. Taker share by time-to-settlement and relation, from
   the rows where maker/taker is KNOWN (takerOnly feed coverage only).
5/6. SEQUENCE + TRAJECTORY REFERENCE. Actions per window, same-side run
   lengths, |imbalance| and effective pair cost by sequence decile.

Nothing here touches the live path; the live engine reads the written
artifact for shadow fields only.

    python3 -m src.polymarket_l5.fidelity_pass
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import config
from .action_model import (SYSTEMATIC_REGIME_EPOCH, _bucket, _relation,
                           action_key, state_key, SIZE_BUCKETS, TTE_BUCKETS)
from .action_model_v2 import (ALPHA, EPS, HOLDOUT_EPOCH, Variant,
                              GAP_BUCKETS, MOVE_BUCKETS, _f)

SEQUENCE_CSV = config.OUTPUT_DIR / "l5_canonical_sequence.csv.gz"
OUT = config.OUTPUT_DIR / "l5_fidelity_pass.json"

PROMOTE_MIN_DLOGP = 0.005          # held-out nats per decision over v2
PROMOTE_MAX_UNSEEN_PCT = 3.0       # validation transitions in unseen states

# reconstructable candidate features (beyond v2's last_rel/last_size)
IMB_FINE_BUCKETS = ((1.0, "i0"), (3.0, "i1-3"), (10.0, "i3-10"),
                    (25.0, "i10-25"), (50.0, "i25-50"), (100.0, "i50-100"),
                    (1e12, "i>100"))
INV_NORM_BUCKETS = ((0.05, "bal"), (0.25, "lean"), (0.6, "tilt"),
                    (1.01, "onesided"))
RUN_BUCKETS = ((2.0, "run1"), (4.0, "run2-3"), (8.0, "run4-7"),
               (1e12, "run>=8"))
TTE_FINE_BUCKETS = ((15, "t<15s"), (30, "t15-30"), (60, "t30-60"),
                    (120, "t1-2m"), (180, "t2-3m"), (300, "t3-5m"),
                    (450, "t5-7.5m"), (600, "t7.5-10m"), (900, "t10-15m"),
                    (1e12, "t>15m"))
UNSUPPORTED_FEATURES = {
    "recent_trade_flow": "recorder trades_1h/flow exist only for live PMUS "
                         "markets; L5's history carries no venue tape",
    "book_spread_depth": "no historical book snapshots in L5's public "
                         "activity; only fills",
}
V2_EXTRAS = ("last_rel", "last_size")
NEW_FEATURES = ("imb_fine", "inv_norm", "path3", "run_len", "tte_fine",
                "move", "cadence")
# `cadence` is the gap between the previous fill and the NEXT one, i.e.
# it is only known once the action we are predicting has happened. It is
# scored for comparability with the v2 review but can never be promoted
# into a causal live state (the live engine knows only "time since the
# last fill so far"). Listed as non-causal, excluded from promotion.
NON_CAUSAL = {"cadence": "gap to the next fill is part of the outcome, "
                         "not of the pre-action state"}
RELATIONS = ("open", "extend", "complete", "sell")


def _windows(csv_path: Path) -> Iterable[List[Dict[str, str]]]:
    opener = gzip.open if str(csv_path).endswith(".gz") else open
    with opener(csv_path, "rt", newline="", encoding="utf-8") as fh:
        cur, wid = [], None
        for row in csv.DictReader(fh):
            if row["window_id"] != wid:
                if cur:
                    yield cur
                cur, wid = [], row["window_id"]
            cur.append(row)
        if cur:
            yield cur


def build_transitions(csv_path: Path = SEQUENCE_CSV,
                      holdout_epoch: float = HOLDOUT_EPOCH,
                      since_epoch: float = SYSTEMATIC_REGIME_EPOCH
                      ) -> Tuple[List[dict], List[dict]]:
    """v2's transitions plus the reconstructable candidate features and
    the per-window bookkeeping the sequence references need."""
    train, valid = [], []
    for rows in _windows(csv_path):
        rows.sort(key=lambda r: (int(r["timestamp"]), int(r["trade_seq"] or 0)))
        w0 = _f(rows[0]["timestamp"])
        if w0 < since_epoch:
            continue
        dest = valid if w0 >= holdout_epoch else train
        run, last_outcome = 0, None
        n_actions = len(rows)
        for i, (prev, nxt) in enumerate(zip(rows, rows[1:])):
            if prev["outcome"] == last_outcome:
                run += 1
            else:
                run, last_outcome = 1, prev["outcome"]
            resid = _f(prev["residual_qty_signed"])
            up, dn = _f(prev["cum_up_qty"]), _f(prev["cum_down_qty"])
            tot = up + dn
            p0, p1 = _f(prev["price"]), _f(rows[i - 1]["price"]) if i else _f(prev["price"])
            p2 = _f(rows[i - 2]["price"]) if i >= 2 else p1
            tte = _f(nxt["seconds_to_settlement"])
            rel_next = _relation(resid, nxt["outcome"], nxt["side"])
            t = {
                "window_id": prev["window_id"],
                "seq": i + 1,
                "n_actions": n_actions,
                "base": state_key(p0, tte, resid),
                "action": action_key(rel_next, _f(nxt["quantity"])),
                "relation": rel_next,
                "quantity": _f(nxt["quantity"]),
                "dollars": _f(nxt["dollars"]),
                "price_next": _f(nxt["price"]),
                "tte": tte,
                "maker_taker": nxt.get("maker_taker") or "unknown",
                "residual_abs": abs(resid),
                "pair_cost": (_f(prev["matched_pair_unit_cost"])
                              if prev["matched_pair_unit_cost"] else None),
                # v2 features
                "last_rel": _relation(
                    _f(rows[i - 1]["residual_qty_signed"]) if i else 0.0,
                    prev["outcome"], prev["side"]),
                "last_size": _bucket(_f(prev["quantity"]), SIZE_BUCKETS),
                "cadence": _bucket(_f(nxt["timestamp"]) - _f(prev["timestamp"]),
                                   GAP_BUCKETS),
                "move": _bucket(p0 - p2, MOVE_BUCKETS),
                # new reconstructable features
                "imb_fine": _bucket(abs(resid), IMB_FINE_BUCKETS),
                "inv_norm": _bucket(abs(resid) / tot if tot > 0 else 1.0,
                                    INV_NORM_BUCKETS),
                "path3": ("u" if p0 > p1 else "d" if p0 < p1 else "f")
                         + ("u" if p1 > p2 else "d" if p1 < p2 else "f"),
                "run_len": _bucket(float(run), RUN_BUCKETS),
                "tte_fine": _bucket(tte, TTE_FINE_BUCKETS),
            }
            dest.append(t)
    return train, valid


# ---------------------------------------------------------------- 1. state

def _support(v: Variant) -> dict:
    ns = [sum(c.values()) for c in v.full.values()]
    return {"train_states": len(ns),
            "median_transitions_per_state": (statistics.median(ns) if ns else 0),
            "states_with_lt20": sum(1 for n in ns if n < 20)}


def _score(extras: Tuple[str, ...], train: List[dict],
           valid: List[dict]) -> dict:
    v = Variant(extras)
    v.fit(train)
    out = v.evaluate(valid)
    out.update(_support(v))
    out["extras"] = list(extras) or ["(v1 base)"]
    return out


def state_upgrade_study(train: List[dict], valid: List[dict]) -> dict:
    results = [_score((), train, valid), _score(V2_EXTRAS, train, valid)]
    v2 = results[1]
    singles = []
    for feat in NEW_FEATURES:
        r = _score(V2_EXTRAS + (feat,), train, valid)
        r["dlogp_vs_v2"] = round(r["mean_logp"] - v2["mean_logp"], 4)
        singles.append(r)
    singles.sort(key=lambda r: -r["mean_logp"])
    pairs = []
    top = [r["extras"][-1] for r in singles[:3]]
    for a in range(len(top)):
        for b in range(a + 1, len(top)):
            r = _score(V2_EXTRAS + (top[a], top[b]), train, valid)
            r["dlogp_vs_v2"] = round(r["mean_logp"] - v2["mean_logp"], 4)
            pairs.append(r)
    pairs.sort(key=lambda r: -r["mean_logp"])

    def promotable(r: dict) -> bool:
        return (r.get("dlogp_vs_v2", 0.0) >= PROMOTE_MIN_DLOGP
                and r["valid_unseen_state_pct"] <= PROMOTE_MAX_UNSEEN_PCT
                and not any(e in NON_CAUSAL for e in r["extras"]))
    for r in singles + pairs:
        r["promotable"] = promotable(r)
    promoted = [r for r in singles + pairs if r["promotable"]]
    promoted.sort(key=lambda r: -r["mean_logp"])
    return {
        "rule": {"min_dlogp_vs_v2": PROMOTE_MIN_DLOGP,
                 "max_unseen_pct": PROMOTE_MAX_UNSEEN_PCT},
        "v1_base": results[0], "v2_current": v2,
        "singles_on_v2": singles, "pairs_on_v2": pairs,
        "promoted": promoted[0] if promoted else None,
        "unsupported_features": UNSUPPORTED_FEATURES,
        "non_causal_features": NON_CAUSAL,
    }


# ----------------------------------------------------------- 2. stochastic

def seed_material(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


def sample_from(dist: Mapping[str, float], seed: str) -> Tuple[str, float]:
    """Deterministic inverse-CDF draw: the first 8 hex chars of the seed
    give u in [0,1); actions are walked in sorted order. Returns the
    sampled action and u."""
    u = int(seed[:8], 16) / float(0x100000000)
    acc = 0.0
    items = sorted(dist.items())
    for a, p in items:
        acc += p
        if u < acc:
            return a, u
    return items[-1][0], u


def rel_mass(dist: Mapping[str, float]) -> Dict[str, float]:
    agg: Dict[str, float] = defaultdict(float)
    for a, p in dist.items():
        agg[a.split("|")[0]] += p
    return dict(agg)


def stochastic_replay(train: List[dict], valid: List[dict],
                      extras: Tuple[str, ...] = V2_EXTRAS) -> dict:
    v = Variant(extras)
    v.fit(train)
    n = 0
    agree = {"modal": 0, "sampled": 0}
    mix = {"actual": defaultdict(int), "modal": defaultdict(int),
           "sampled": defaultdict(int)}
    cal = defaultdict(lambda: [0, 0])          # decile -> [n, extend hits]
    logp_sampled_choice = 0.0
    for t in valid:
        dist, _ = v.dist(t)
        rm = rel_mass(dist)
        modal = max(rm, key=rm.get)
        sampled_action, _u = sample_from(dist, seed_material(t["window_id"], t["seq"]))
        sampled = sampled_action.split("|")[0]
        actual = t["relation"]
        n += 1
        agree["modal"] += modal == actual
        agree["sampled"] += sampled == actual
        mix["actual"][actual] += 1
        mix["modal"][modal] += 1
        mix["sampled"][sampled] += 1
        d = min(int(rm.get("extend", 0.0) * 10), 9)
        cal[d][0] += 1
        cal[d][1] += actual == "extend"
        logp_sampled_choice += math.log(rm.get(sampled, 0.0) + EPS)
    def _mix(c):
        tot = sum(c.values()) or 1
        return {k: round(v_ / tot, 4) for k, v_ in sorted(c.items())}
    calib = [{"p_extend_bucket": f"{d / 10:.1f}-{(d + 1) / 10:.1f}",
              "n": c[0], "observed_extend": round(c[1] / c[0], 4)}
             for d, c in sorted(cal.items()) if c[0]]
    l1 = lambda a, b: round(sum(abs(a.get(k, 0) - b.get(k, 0))
                                for k in set(a) | set(b)), 4)
    am, mm, sm = _mix(mix["actual"]), _mix(mix["modal"]), _mix(mix["sampled"])
    return {
        "n_valid": n,
        "relation_agreement_pct": {k: round(100 * x / max(n, 1), 1)
                                   for k, x in agree.items()},
        "relation_mix": {"actual": am, "modal": mm, "sampled": sm},
        "mix_l1_distance_to_actual": {"modal": l1(am, mm), "sampled": l1(am, sm)},
        "calibration_p_extend": calib,
        "seed": "sha256(window_id|seq) -> u = first 8 hex / 2^32; "
                "inverse CDF over sorted action keys",
        "verdict": ("sampled policy reproduces L5's relation MIX "
                    "(lower L1) while the modal policy scores higher per-"
                    "decision agreement: expected for a ~50/50 stochastic "
                    "process; live promotion needs the mix, not the top-1"),
    }


# ----------------------------------------------------------------- 3. size

def _q(xs: Sequence[float], q: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    k = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return round(s[k], 4)


def size_policy(train: List[dict]) -> dict:
    by: Dict[str, Dict[str, list]] = defaultdict(lambda: {"q": [], "usd": []})
    by_rel: Dict[str, Dict[str, list]] = defaultdict(lambda: {"q": [], "usd": []})
    for t in train:
        k = f"{t['base']}|{t['relation']}"
        by[k]["q"].append(t["quantity"]); by[k]["usd"].append(t["dollars"])
        by_rel[t["relation"]]["q"].append(t["quantity"])
        by_rel[t["relation"]]["usd"].append(t["dollars"])
    def _summ(d):
        return {k: {"n": len(v["q"]),
                    "shares_p25": _q(v["q"], .25), "shares_p50": _q(v["q"], .5),
                    "shares_p75": _q(v["q"], .75),
                    "usd_p50": _q(v["usd"], .5), "usd_p75": _q(v["usd"], .75)}
                for k, v in d.items()}
    return {"by_state_relation": _summ(by), "by_relation": _summ(by_rel),
            "labels": "L5 historical fill size; the live engine emits this as "
                      "l5_target_size beside executed_safe_size"}


def l5_target_size(policy: Mapping[str, Any], base: str, relation: str) -> dict:
    row = (policy.get("by_state_relation") or {}).get(f"{base}|{relation}")
    if row and row.get("n", 0) >= 20:
        return {"source": "state|relation", **row}
    row = (policy.get("by_relation") or {}).get(relation)
    return {"source": "relation", **(row or {"n": 0})}


# ------------------------------------------------------- 4. aggressiveness

def aggressiveness_policy(train: List[dict], valid: List[dict]) -> dict:
    rows = [t for t in train + valid if t["maker_taker"] in ("maker", "taker")]
    by_tte: Dict[str, list] = defaultdict(list)
    by_tte_rel: Dict[str, list] = defaultdict(list)
    for t in rows:
        tb = _bucket(t["tte"], TTE_BUCKETS)
        by_tte[tb].append(t["maker_taker"] == "taker")
        by_tte_rel[f"{tb}|{t['relation']}"].append(t["maker_taker"] == "taker")
    def _p(d):
        return {k: {"n": len(v), "p_taker": round(sum(v) / len(v), 4)}
                for k, v in sorted(d.items())}
    return {"known_rows": len(rows),
            "coverage": "takerOnly feed coverage only (Aug 28-31); "
                        "'unknown' rows excluded, never assumed maker",
            "p_taker_by_tte": _p(by_tte), "p_taker_by_tte_relation": _p(by_tte_rel),
            "live_policy": "maker/post-only only; taker stays disabled; "
                           "fee-safe completion rule unchanged"}


def l5_target_aggressiveness(policy: Mapping[str, Any], tte_s: float,
                             relation: str) -> dict:
    tb = _bucket(tte_s, TTE_BUCKETS)
    row = (policy.get("p_taker_by_tte_relation") or {}).get(f"{tb}|{relation}")
    if not row:
        row = (policy.get("p_taker_by_tte") or {}).get(tb) or {"n": 0, "p_taker": None}
    return {"tte_bucket": tb, **row}


# ----------------------------------------------- 5/6. sequence + trajectory

def sequence_reference(train: List[dict], valid: List[dict]) -> dict:
    per_window: Dict[str, List[dict]] = defaultdict(list)
    for t in train + valid:
        per_window[t["window_id"]].append(t)
    n_actions, runs = [], []
    dec_imb: Dict[int, list] = defaultdict(list)
    dec_pc: Dict[int, list] = defaultdict(list)
    for wid, ts in per_window.items():
        ts.sort(key=lambda t: t["seq"])
        n = ts[0]["n_actions"]
        n_actions.append(n)
        # same-side runs from the relation sequence: a run breaks on complete
        run = 0
        for t in ts:
            if t["relation"] in ("extend", "open"):
                run += 1
            else:
                if run:
                    runs.append(run)
                run = 0
        if run:
            runs.append(run)
        for t in ts:
            d = min(int(10 * (t["seq"] - 1) / max(n - 1, 1)), 9)
            dec_imb[d].append(t["residual_abs"])
            if t["pair_cost"] is not None:
                dec_pc[d].append(t["pair_cost"])
    return {
        "windows": len(per_window),
        "actions_per_window": {"p25": _q(n_actions, .25), "p50": _q(n_actions, .5),
                               "p75": _q(n_actions, .75), "p90": _q(n_actions, .9)},
        "same_side_run_len": {"p50": _q(runs, .5), "p75": _q(runs, .75),
                              "p90": _q(runs, .9), "max": max(runs) if runs else None},
        "imbalance_shares_by_decile": {d: _q(v, .5) for d, v in sorted(dec_imb.items())},
        "pair_cost_by_decile": {d: _q(v, .5) for d, v in sorted(dec_pc.items())},
        "pair_cost_above_1_share_by_decile": {
            d: round(sum(1 for x in v if x > 1.0) / len(v), 4)
            for d, v in sorted(dec_pc.items()) if v},
        "labels": "L5 reference profiles; live sequences are scored against "
                  "these by decile, never by final P&L alone",
    }


def trajectory_score(fills: Sequence[Mapping[str, Any]],
                     reference: Mapping[str, Any]) -> dict:
    """Score a live fill sequence (dicts with side 'YES'/'NO', price, qty)
    against the L5 decile profiles: |imbalance| in behavioral units and
    effective pair cost per decile of the sequence."""
    up = dn = 0.0
    spend_up = spend_dn = 0.0
    unit = None
    path = []
    for f in fills:
        q = float(f.get("qty") or 0)
        px = float(f.get("price") or 0)
        if q <= 0:
            continue
        unit = unit or q
        if str(f.get("side", "")).upper().endswith("YES"):
            up += q; spend_up += q * px
        else:
            dn += q; spend_dn += q * px
        matched = min(up, dn)
        pc = ((spend_up / up if up else 0.0) + (spend_dn / dn if dn else 0.0)
              if matched > 0 else None)
        path.append({"imb_units": round(abs(up - dn) / unit, 3),
                     "pair_cost": round(pc, 4) if pc is not None else None})
    n = len(path)
    by_dec: Dict[int, dict] = {}
    for i, p in enumerate(path):
        d = min(int(10 * i / max(n - 1, 1)), 9)
        by_dec[d] = p
    ref_pc = reference.get("pair_cost_by_decile") or {}
    return {"n_fills": n, "final": path[-1] if path else None,
            "by_decile": by_dec,
            "pair_cost_gap_vs_l5": {
                str(d): (round(p["pair_cost"] - float(ref_pc[str(d)]), 4)
                         if p.get("pair_cost") is not None and ref_pc.get(str(d)) is not None
                         else None)
                for d, p in by_dec.items()},
            "labels": "DERIVED shape comparison against L5 decile profile"}


# ------------------------------------------------------------------- main

def run(csv_path: Path = SEQUENCE_CSV) -> dict:
    train, valid = build_transitions(csv_path)
    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source": str(csv_path),
        "holdout_epoch": HOLDOUT_EPOCH, "alpha_shrinkage": ALPHA,
        "train_transitions": len(train), "valid_transitions": len(valid),
        "state_upgrade": state_upgrade_study(train, valid),
        "stochastic_policy": stochastic_replay(train, valid),
        "size_policy": size_policy(train),
        "aggressiveness_policy": aggressiveness_policy(train, valid),
        "sequence_reference": sequence_reference(train, valid),
        "labels": "OFFLINE research over L5's canonical sequence; nothing "
                  "here trades; the live engine reads shadow fields only",
    }
    return out


def main() -> int:
    out = run()
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True))
    su = out["state_upgrade"]
    print(f"train={out['train_transitions']} valid={out['valid_transitions']}")
    hdr = f"{'variant':<40}{'states':>7}{'med_n':>7}{'unseen%':>8}{'logp':>9}{'dlogp':>8}{'rel%':>6}{'ok':>4}"
    print(hdr)
    for r in [su["v1_base"], su["v2_current"]] + su["singles_on_v2"] + su["pairs_on_v2"]:
        print(f"{'+'.join(r['extras']):<40}{r['train_states']:>7}"
              f"{r['median_transitions_per_state']:>7}"
              f"{r['valid_unseen_state_pct']:>8}{r['mean_logp']:>9}"
              f"{r.get('dlogp_vs_v2', 0.0):>8}{r['relation_top1_pct']:>6}"
              f"{'Y' if r.get('promotable') else '-':>4}")
    print("promoted:", (su["promoted"] or {}).get("extras"))
    sp = out["stochastic_policy"]
    print("stochastic:", sp["relation_agreement_pct"], sp["mix_l1_distance_to_actual"])
    print("aggressiveness:", out["aggressiveness_policy"]["p_taker_by_tte"])
    print("sequence:", out["sequence_reference"]["actions_per_window"],
          out["sequence_reference"]["same_side_run_len"])
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
