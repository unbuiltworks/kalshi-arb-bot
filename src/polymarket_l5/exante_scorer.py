"""TRUE EX-ANTE clone-vs-L5 scorer (issue #167).

The no-hindsight test the replay work cannot provide: the frozen action
model must predict L5's next fill BEFORE that fill exists, on live public
data, and be scored against what L5 then actually does.

No-hindsight guarantees (predeclared, enforced in code):
  1. FROZEN MODEL. The policy is the committed `l5_action_model.json`
     (systematic-regime training, Mar 27+). Its sha256 is recorded at
     session start and the file is loaded once, before any live fill is
     seen. Every scored fill has timestamp > session start, so no scored
     event can be in the training data.
  2. FRESH WINDOWS ONLY. Only windows whose settlement window STARTS after
     the session starts are scored — mid-flight windows are excluded so
     the clone's inventory state is built entirely from post-start fills.
  3. SEQUENTIAL PURE-FUNCTION PREDICTIONS. The prediction for fill n+1 is
     a pure function of the frozen model and the state after fill n; it is
     computed and appended to the tape (with its own wall-clock commit ts
     and poll-cycle id) before the outcome row is appended. For fills that
     arrive in the same poll batch the prediction is still hindsight-free
     (pure function of prior state + frozen model), but we additionally
     report the STRICT subset — transitions whose prediction was committed
     in an earlier poll cycle than the outcome's arrival — separately.
  4. APPEND-ONLY TAPE. data/l5/exante/exante_<UTC>.jsonl, flushed per row.

Predeclared comparison (what makes the sample informative):
  Three arms score every transition identically —
    conditional : P(action | state) from the frozen model
    marginal    : P(action) unconditional (frozen model's pooled mix)
    uniform     : 1/|action vocabulary|
  Claim under test: the state-CONDITIONAL model beats the MARGINAL on
  mean log-probability of L5's observed action, and on top-1 relation
  accuracy. If it does not, the coarse state carries no ex-ante
  information beyond L5's overall habit mix — a NO-GO for any clone that
  keys on this state alone (feeds the "richer state" review).

READ-ONLY public data. Nothing here trades, authenticates, or touches the
Railway collector; it runs wherever invoked with its own tape.

    python3 -m src.polymarket_l5.exante_scorer --minutes 240
    python3 -m src.polymarket_l5.exante_scorer --summarize data/l5/exante/exante_XXX.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config
from . import action_model as am
from .adapters import DEFAULT_ADAPTER

EXANTE_DIR = config.DATA_DIR / "exante"
MODEL_PATH = config.OUTPUT_DIR / "l5_action_model.json"
ACTIVITY_URL = ("https://data-api.polymarket.com/activity?user={wallet}"
                "&limit=500&sortBy=TIMESTAMP&sortDirection=DESC")
L5_WALLET = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
POLL_SECONDS = 10.0
EPS = 1e-6


class _State:
    __slots__ = ("up_qty", "dn_qty", "last_price", "n_fills")

    def __init__(self) -> None:
        self.up_qty = self.dn_qty = 0.0
        self.last_price = None
        self.n_fills = 0


def _load_frozen_model() -> Dict[str, Any]:
    raw = MODEL_PATH.read_bytes()
    model = json.loads(raw)
    model["_sha256_16"] = hashlib.sha256(raw).hexdigest()[:16]
    # marginal (unconditional) action mix, pooled over states weighted by n
    pooled: Dict[str, float] = defaultdict(float)
    total = 0.0
    for st in model["states"].values():
        for a, p in st["action_probs"].items():
            pooled[a] += st["n"] * p
        total += st["n"]
    model["_marginal"] = {a: v / total for a, v in pooled.items()}
    model["_vocab"] = sorted(pooled)
    return model


def _predict(model: Dict[str, Any], state_key: str) -> Dict[str, Any]:
    st = model["states"].get(state_key)
    return {
        "state": state_key,
        "conditional": st["action_probs"] if st else None,
        "state_n": st["n"] if st else 0,
        "modal": st["modal_action"] if st else None,
    }


def _score(pred: Dict[str, Any], model: Dict[str, Any],
           actual: str) -> Dict[str, Any]:
    marg = model["_marginal"]
    uni = 1.0 / max(len(model["_vocab"]), 1)
    cond = pred["conditional"]
    p_cond = cond.get(actual, 0.0) if cond else None
    rel = actual.split("|")[0]

    def rel_p(dist):
        return sum(p for a, p in dist.items() if a.split("|")[0] == rel)

    def rel_top(dist):
        agg: Dict[str, float] = defaultdict(float)
        for a, p in dist.items():
            agg[a.split("|")[0]] += p
        return max(agg, key=agg.get)

    return {
        "actual": actual,
        "off_distribution": cond is None,
        "p_conditional": round(p_cond, 5) if p_cond is not None else None,
        "p_marginal": round(marg.get(actual, 0.0), 5),
        "p_uniform": round(uni, 5),
        "exact_hit_conditional": (pred["modal"] == actual) if cond else None,
        "exact_hit_marginal": max(marg, key=marg.get) == actual,
        "relation_hit_conditional": (rel_top(cond) == rel) if cond else None,
        "relation_hit_marginal": rel_top(marg) == rel,
        "rel_p_conditional": round(rel_p(cond), 5) if cond else None,
        "rel_p_marginal": round(rel_p(marg), 5),
    }


def _fetch(wallet: str) -> List[dict]:
    req = urllib.request.Request(ACTIVITY_URL.format(wallet=wallet), headers={
        "User-Agent": "kalshi-arb-bot/l5-exante-scorer (read-only research)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            rows = json.loads(r.read())
        return [x for x in rows if isinstance(x, dict)] if isinstance(rows, list) else []
    except Exception:
        return []


def run(minutes: float, wallet: str = L5_WALLET) -> Path:
    model = _load_frozen_model()
    start_ts = time.time()
    EXANTE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tape = EXANTE_DIR / f"exante_{stamp}.jsonl"
    seen: set = set()
    states: Dict[str, _State] = {}
    pending: Dict[str, Dict[str, Any]] = {}   # window_id -> committed prediction
    cycle = 0
    with tape.open("a", encoding="utf-8") as sink:
        def emit(obj):
            sink.write(json.dumps(obj, separators=(",", ":")) + "\n")
            sink.flush()

        emit({"kind": "session_start", "utc": stamp, "start_ts": start_ts,
              "model_sha256_16": model["_sha256_16"],
              "model_since_epoch": model.get("since_epoch"),
              "model_decisions": model.get("n_decisions"),
              "wallet": wallet, "fresh_windows_only": True,
              "predeclared": ("conditional must beat marginal on mean "
                              "log-prob and relation top-1 accuracy")})
        deadline = start_ts + minutes * 60
        while time.time() < deadline:
            cycle += 1
            batch = _fetch(wallet)
            batch.sort(key=lambda r: float(r.get("timestamp") or 0))
            for raw in batch:
                if raw.get("type") != "TRADE":
                    continue
                win = DEFAULT_ADAPTER.parse_window(raw)
                fill = DEFAULT_ADAPTER.normalize_fill(raw)
                if win is None or fill is None:
                    continue
                if win.window_start < start_ts:
                    continue                      # fresh windows only
                key = (fill.transaction_id, fill.timestamp, fill.outcome,
                       fill.price, fill.quantity)
                if key in seen:
                    continue
                seen.add(key)
                st = states.setdefault(win.window_id, _State())
                # --- score the standing prediction against this fill ---
                actual_rel = am._relation(st.up_qty - st.dn_qty,
                                          fill.outcome, fill.side)
                actual = am.action_key(actual_rel, fill.quantity)
                pred = pending.get(win.window_id)
                if pred is not None:
                    row = _score(pred, model, actual)
                    row.update({"kind": "scored", "window_id": win.window_id,
                                "fill_ts": fill.timestamp,
                                "pred_cycle": pred["_cycle"], "score_cycle": cycle,
                                "strict_temporal": pred["_cycle"] < cycle,
                                "seq": st.n_fills + 1})
                    emit(row)
                # --- update state, then commit the next prediction ---
                sign = 1.0 if fill.side == "BUY" else -1.0
                if fill.outcome == "Up":
                    st.up_qty += sign * fill.quantity
                else:
                    st.dn_qty += sign * fill.quantity
                st.last_price = fill.price
                st.n_fills += 1
                tte = win.window_end - fill.timestamp
                sk = am.state_key(st.last_price, max(tte, 0.0),
                                  st.up_qty - st.dn_qty)
                nxt = _predict(model, sk)
                nxt["_cycle"] = cycle
                nxt["kind"] = "prediction"
                nxt["window_id"] = win.window_id
                nxt["committed_utc"] = datetime.now(timezone.utc).isoformat()
                pending[win.window_id] = nxt
                emit({k: v for k, v in nxt.items() if k != "conditional"})
            time.sleep(POLL_SECONDS)
    return tape


def summarize(tape: Path) -> Dict[str, Any]:
    scored: List[dict] = []
    header = None
    for line in tape.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("kind") == "session_start":
            header = row
        elif row.get("kind") == "scored":
            scored.append(row)

    def agg(rows: List[dict]) -> Dict[str, Any]:
        n = len(rows)
        if not n:
            return {"n": 0}
        on = [r for r in rows if not r["off_distribution"]]
        def mean_log(k):
            vals = [math.log((r[k] or 0.0) + EPS) for r in on]
            return round(sum(vals) / len(vals), 4) if vals else None
        def rate(rows_, k):
            vals = [r[k] for r in rows_ if r[k] is not None]
            return round(100 * sum(vals) / len(vals), 1) if vals else None
        return {
            "n_scored": n,
            "off_distribution": n - len(on),
            "mean_logp_conditional": mean_log("p_conditional"),
            "mean_logp_marginal": mean_log("p_marginal"),
            "mean_logp_uniform": mean_log("p_uniform"),
            "exact_top1_pct_conditional": rate(on, "exact_hit_conditional"),
            "exact_top1_pct_marginal": rate(rows, "exact_hit_marginal"),
            "relation_top1_pct_conditional": rate(on, "relation_hit_conditional"),
            "relation_top1_pct_marginal": rate(rows, "relation_hit_marginal"),
            "mean_rel_p_conditional": round(sum(
                r["rel_p_conditional"] for r in on) / len(on), 4) if on else None,
            "mean_rel_p_marginal": round(sum(
                r["rel_p_marginal"] for r in rows) / n, 4),
        }

    out = {
        "tape": str(tape),
        "session": {k: header.get(k) for k in
                    ("utc", "model_sha256_16", "model_decisions",
                     "predeclared")} if header else None,
        "windows": len({r["window_id"] for r in scored}),
        "all_transitions": agg(scored),
        "strict_temporal_subset": agg([r for r in scored if r["strict_temporal"]]),
        "verdict_hint": None,
    }
    a = out["all_transitions"]
    if a.get("n_scored", 0) >= 50 and a["mean_logp_conditional"] is not None:
        out["verdict_hint"] = (
            "conditional beats marginal"
            if a["mean_logp_conditional"] > a["mean_logp_marginal"]
            else "conditional does NOT beat marginal (state under-specified)")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--minutes", type=float, default=240.0)
    ap.add_argument("--summarize", default="")
    args = ap.parse_args()
    if args.summarize:
        print(json.dumps(summarize(Path(args.summarize)), indent=1))
        return 0
    tape = run(args.minutes)
    print(f"tape: {tape}")
    print(json.dumps(summarize(tape), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
