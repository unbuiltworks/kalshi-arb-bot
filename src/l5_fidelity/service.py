"""24/7 FIDELITY runner. READ ONLY -- public endpoints, no credentials, no order path.

ESTIMAND A ONLY. Arbitrary-state WAIT/ACT (estimand B) is deliberately NOT
served here: the activity feed's newest row is a median 28.5s old (7.6-55.2s,
measured 2026-09-15 over 12 samples), so a wall-clock freeze cannot observe its
own prediction window. B needs either a state feed without that lag or a freeze
deliberately held back behind it, and neither is built. It is reported NOT READY
rather than served badly.

THE STORE IS DURABLE AND IMMUTABLE. It lives on the mounted volume, not in the
repo: prospective rows are runtime research state, not source code. Seals are
content hashes, resolutions are first-writer-wins, and a tampered row is
excluded on load rather than scored.

FAIL VISIBLY. Every digest carries freeze/resolve counts, the last timestamps
and the feed's own lag. Silence is reported as stale accumulation and is never
interpreted as L5 inactivity -- that confusion is exactly what the feed lag
above produces if you let it.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from src.l5_fidelity import anchored, store
from src.l5_fidelity import model as M
from src.l5_fidelity import window as A
from src.l5_fidelity.model import COHORTS, HORIZONS_S, FidelityV0

ADDR = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
ACTIVITY = ("https://data-api.polymarket.com/activity"
            "?user={u}&limit=500&type=TRADE")
UA = {"User-Agent": "l5-fidelity/1.0 (read-only research)"}
FEE_RATE = 0.07
BEARING_CUT = FEE_RATE * 0.5
PRIMARY = "BTC15m"
DIGEST_S = 300.0
POLL_S = 10.0


def _get(url: str, timeout: float = 20.0) -> Optional[Any]:
    try:
        req = urllib.request.Request(url, headers=UA, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as fh:
            return json.loads(fh.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError, TimeoutError):
        return None


def fetch(cohort: str) -> Optional[List[dict]]:
    rows = _get(ACTIVITY.format(u=ADDR))
    if rows is None:
        return None
    out = []
    for x in rows:
        m = A.WINDOW_RE.match(str(x.get("slug") or ""))
        if not m:
            continue
        if f"{m.group(1).upper()}{m.group(2).lower()}" != cohort:
            continue
        if str(x.get("side") or "").upper() != "BUY":
            continue
        px = float(x.get("price") or 0)
        sz = float(x.get("size") or 0)
        usd = float(x.get("usdcSize") or 0)
        if not (px > 0 and sz > 0 and 0 < px < 1):
            continue
        den = px * (1 - px)
        out.append({"cid": str(x.get("conditionId")),
                    "t": int(x.get("timestamp") or 0),
                    "window_end": int(m.group(3)) + A.DURATION_S[m.group(2).lower()],
                    "outcome": str(x.get("outcome")), "price": px, "size": sz,
                    "fee_bearing": (((usd - px * sz) / sz) / den) > BEARING_CUT
                    if den > 0 else False})
    return sorted(out, key=lambda f: f["t"])


def lineage(out_dir: str) -> Dict[str, Any]:
    """Per-model-version accounting over the durable store.

    `seal_digest` is a hash over each lineage's prediction ids IN ORDER. The
    ids are themselves content hashes of the frozen bodies, so if a single byte
    of a single v0.1 row changed, that row's seal would stop matching (it would
    be reported tampered and dropped) and this digest would move. A stable
    digest across a model cutover is therefore evidence the older lineage's
    rows are byte-identical, not merely that they are still counted.
    """
    L = store.load(out_dir)
    per: Dict[str, Dict[str, Any]] = {}
    ids: Dict[str, List[str]] = collections.defaultdict(list)
    for pid, p in sorted(L["predictions"].items(),
                         key=lambda kv: kv[1]["prediction_utc"]):
        v = p["model_version"]
        d = per.setdefault(v, {"frozen": 0, "resolved": 0,
                               "first_freeze_utc": None,
                               "last_freeze_utc": None})
        d["frozen"] += 1
        if L["outcomes"].get(pid):
            d["resolved"] += 1
        t = p["prediction_utc"]
        if d["first_freeze_utc"] is None:
            d["first_freeze_utc"] = t
        d["last_freeze_utc"] = t
        ids[v].append(pid)
    for v, d in per.items():
        d["seal_digest"] = hashlib.sha256(
            "".join(ids[v]).encode()).hexdigest()[:16]
    return per


def self_check(model: FidelityV0) -> Dict[str, Any]:
    """Prove the model's identity and shape AT RUNTIME, in production, rather
    than trusting that what the tests asserted is what got deployed.

    Every check is a property that was actually broken at some point in this
    program's history, so each one is here because it failed once, not because
    it seemed prudent.
    """
    import copy
    out: Dict[str, Any] = {}

    # 1. the runtime model hashes to the version it claims. v0.1's hash covered
    #    the state-key schema and none of the numbers, so an artifact could be
    #    rebuilt or corrupted and keep its identity.
    recomputed = M.version_of(model.cohort, model.tables)
    out["content_hash_matches_artifact"] = (recomputed == model.version)
    out["runtime_content_hash"] = recomputed

    # 2. and changing a single parameter must move it.
    t = copy.deepcopy(model.tables)
    t["base_gaps"][0] = t["base_gaps"][0] + 1e-9
    out["param_change_changes_version"] = (
        M.version_of(model.cohort, t) != model.version)

    # 3. size is a distribution: normalised, and strictly positive so one
    #    surprise cannot score as log(0).
    bins = [model.tables["base_size_bins"]] + list(
        model.tables["size_bins"].values())
    out["size_bins_checked"] = len(bins)
    out["size_bins_sum_to_one"] = all(
        abs(sum(b.values()) - 1.0) < 1e-9 for b in bins)
    out["size_bins_all_positive"] = all(
        all(v > 0 for v in b.values()) for b in bins)

    # 4. true state n and the capped sample are separately represented. gaps
    #    and size are subsampled per state while side keeps full counts, so
    #    reporting len(gaps) understated the evidence behind a state.
    p = model.predict(tte=400, since=None, resid_rel=0.0,
                      last_outcome="Up", price=0.45)
    out["state_n_true"] = p["state_n"]
    out["state_n_sampled"] = p["state_n_sampled"]
    out["state_n_separately_represented"] = (
        p["state_n"] >= p["state_n_sampled"]
        and "predicted_size_p50" not in p
        and p.get("predicted_size_bins") is not None)
    out["all_pass"] = all(v for k, v in out.items()
                          if isinstance(v, bool))
    return out


def digest(out_dir: str, model: FidelityV0, *, feed_lag_s: Optional[float],
           last_freeze: Optional[float], last_resolve: Optional[float],
           feed_failures: int) -> Dict[str, Any]:
    L = store.load(out_dir)
    per_cohort = collections.Counter()
    per_h: Dict[str, Dict[str, int]] = {str(h): {"scored": 0, "acted": 0}
                                        for h in HORIZONS_S}
    est = collections.Counter()
    excluded = 0
    for pid, p in L["predictions"].items():
        per_cohort[p["cohort"]] += 1
        est[p["prediction"].get("estimand", "unknown")] += 1
        o = L["outcomes"].get(pid)
        if not o:
            continue
        if not o["actual"].get("prospective", True):
            excluded += 1
            continue
        for h in HORIZONS_S:
            a = o["disagreement"]["act"][str(h)]
            per_h[str(h)]["scored"] += 1
            per_h[str(h)]["acted"] += 1 if a["actual"] else 0
    now = time.time()
    stale = (last_freeze is not None and now - last_freeze > 900)
    return {
        "schema": "l5_fidelity_service/v1",
        "model_version": model.version,
        "predictions_frozen": L["n_predictions"],
        "predictions_resolved": L["n_resolved"],
        "unresolved": L["n_predictions"] - L["n_resolved"],
        "excluded_not_prospective": excluded,
        "tamper_failures": len(L["tampered"]),
        "last_freeze_at": last_freeze,
        "last_resolution_at": last_resolve,
        "seconds_since_last_freeze": (None if last_freeze is None else round(now - last_freeze, 1)),
        "per_cohort": dict(per_cohort),
        "per_model_version": lineage(out_dir),
        "per_horizon": per_h,
        "fill_anchored": est.get("fill_anchored", 0),
        "arbitrary_state": est.get("arbitrary_state", 0),
        "arbitrary_state_status": ("NOT READY: the activity feed is a median "
                                   "28.5s behind, so a wall-clock freeze cannot "
                                   "observe its own prediction window"),
        "feed_lag_s": feed_lag_s,
        "feed_consecutive_failures": feed_failures,
        "STALE_ACCUMULATION": stale,
        "labels": ("read-only; no credentials, no order path. Silence here is "
                   "reported as stale accumulation and is NEVER read as L5 "
                   "inactivity."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/state/l5-fidelity")
    ap.add_argument("--hours", type=float, default=0.0)
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)

    # load the FROZEN artifact rather than re-deriving from the raw tape:
    # the model is versioned and must not drift between boots.
    art = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "artifacts", "l5_fidelity",
        f"{PRIMARY}.json.gz")
    model = FidelityV0.load(art)
    print(f"L5_FIDELITY boot model={model.version} cohort={PRIMARY} "
          f"fills={model.tables['n_fills']} windows={model.tables['n_windows']}",
          flush=True)
    # PROVE IT IN PRODUCTION, don't assume the tests describe what shipped.
    print("L5_FIDELITY selfcheck " + json.dumps(self_check(model)), flush=True)
    print("L5_FIDELITY lineage " + json.dumps(lineage(a.out_dir)), flush=True)

    # RECOVER IN-FLIGHT PREDICTIONS ACROSS A RESTART. `pending` used to live
    # only in memory, so every prediction frozen within MAX_H + margin of a
    # restart was orphaned: frozen, never resolved, and counted forever as
    # unresolved. That is not a neutral leak -- it systematically discards
    # predictions near restarts, which is exactly when the runner is being
    # changed, so the dropped sample is not random with respect to the model.
    pending: List[tuple] = []
    seen_anchor: set = set()
    _L = store.load(a.out_dir)
    for _pid, _p in sorted(_L["predictions"].items(),
                           key=lambda kv: kv[1]["prediction_utc"]):
        if _L["outcomes"].get(_pid) or _p["model_version"] != model.version:
            continue
        _cid = (_p.get("state") or {}).get("cid")
        if not _cid:
            continue          # frozen before cid was recorded: unrecoverable
        pending.append((_p, _cid, _p["prediction_utc"]))
        seen_anchor.add((_cid, _p["state"]["anchor_t"]))
    print(f"L5_FIDELITY recovered_pending={len(pending)} "
          f"from a store of {_L['n_predictions']} predictions", flush=True)
    last_freeze = last_resolve = None
    last_digest = 0.0
    feed_failures = 0
    feed_lag = None
    deadline = time.time() + a.hours * 3600 if a.hours else None

    while deadline is None or time.time() < deadline:
        fills = fetch(PRIMARY)
        now = time.time()
        if fills is None:
            feed_failures += 1
        else:
            feed_failures = 0
            if fills:
                feed_lag = round(now - fills[-1]["t"], 1)
            live = [f for f in fills if f["window_end"] > now]
            if live:
                anchor = live[-1]
                key = (anchor["cid"], anchor["t"])
                if key not in seen_anchor:
                    seen_anchor.add(key)
                    wf = [f for f in live if f["cid"] == anchor["cid"]]
                    st = anchored.state_after_fill(cohort=PRIMARY, anchor=anchor,
                                                   window_fills=wf)
                    rec = anchored.freeze_anchored(a.out_dir, model, st, now=now)
                    pending.append((rec, anchor["cid"], now))
                    last_freeze = now
            ready = [x for x in pending
                     if now - x[2] > anchored.MAX_H + anchored.PROPAGATION_MARGIN_S]
            for rec, cid, t0 in ready:
                anchored.resolve_anchored(a.out_dir, rec,
                                          [f for f in fills if f["cid"] == cid],
                                          now=now)
                pending.remove((rec, cid, t0))
                last_resolve = now
        if now - last_digest > DIGEST_S:
            print("L5_FIDELITY " + json.dumps(
                digest(a.out_dir, model, feed_lag_s=feed_lag,
                       last_freeze=last_freeze, last_resolve=last_resolve,
                       feed_failures=feed_failures)), flush=True)
            last_digest = now
        if len(seen_anchor) > 20000:
            seen_anchor = set(list(seen_anchor)[-5000:])
        time.sleep(POLL_S)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
