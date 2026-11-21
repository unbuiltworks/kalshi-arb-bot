"""What is the defensible sports analogue of L5's crypto clock, and does our
current mapping explain the R5F residual losses?

OWNER (#167 comment 5577894093): "what is the most defensible sports analogue
of L5's crypto `seconds_to_settlement` / window-phase state, and does our
current mapping materially explain R5F residual losses or missed
completions?"  Treated as an L5 TRANSFER / FIDELITY problem, not as licence to
invent a sports strategy.  Read-only research: nothing here trades, nothing
here is wired into the live probe, and no frozen artifact is modified.

DATA SOURCES (all named, all read-only)
  1. Railway probe log dumps ({"deploy":[{"timestamp","message"}]}) of the R5F
     campaign.  Parsing / de-duplication is IMPORTED from
     src.l5_r4_action_comparison.harvest and the per-decision ledger from
     src.l5_r5f_sequence_ledger.campaign_report -- this module does not
     re-implement either.  Pass the log directories with --logs.
  2. analysis/l5/l5_canonical_sequence.csv.gz  (L5's own crypto trade tape,
     71,984 rows / 2,216 windows; read only).
  3. analysis/l5/l5_action_model_v2.json  (the FROZEN action model, sha
     bbb55bf90638d2bd; read only -- its `base` counts give L5's canonical
     distribution over the live state key).
  4. src.polymarket_l5.action_model  (the bucket edges the live key uses) and
     src.pmus_event_progress.proximity (the venue-served game-position map --
     reused, not re-derived).
  5. The KEYLESS public gateway https://gateway.polymarket.us (GET only, no
     auth header): /v2/leagues, /v2/leagues/{slug}/events, /v1/market/slug/
     {slug}.  Probed only with --probe-gateway; --offline replays the
     snapshot already stored in the output JSON so a re-run is deterministic.

THE THREE CLOCKS (defined here, each computable causally at decision time)
  C1 event progress   venue-served game position (period / score / ended)
                      mapped by pmus_event_progress.proximity.  Attached to a
                      decision from the LATEST selection row at or before it.
  C2 market lifecycle seconds since the market first appeared in our own tape
                      and seconds since the venue's gameStartTime.  Remaining
                      tradeable life is NOT SERVED by the venue (endDate is a
                      fixed gameStartTime + 14 d contract expiry) and is
                      reported as missing, never manufactured.
  C3 local regime     seconds since the last MATERIAL move of the market's
                      yes-equivalent touch, where material means
                      |delta| >= K cents (default 2) between consecutive
                      decisions on that market, and a wall-clock gap of
                      >= GAP seconds (default 300) also opens a new episode.
                      Sensitivity to K is reported.

CAUSALITY.  Every clock is a function of (this decision, strictly earlier
decisions on the same market, venue fields published before the decision).
`causal_audit` re-derives every clock for decision i from the prefix rows[:i+1]
only and asserts equality with the full-pass value; the assertion is part of
the report and part of the test suite.  No feature uses settlement outcome or
any later state.

WHAT IS FROZEN.  FROZEN_MAPPING below is a definition, not a promotion: it is
hashed into the output JSON so the exploratory (in-sample) numbers and the
out-of-sample numbers refer to the same object.  Nothing here promotes it.

    python3 -m src.l5_sports_clock_study --logs DIR [DIR ...] --offline
"""
from __future__ import annotations

import argparse
import collections
import csv
import gzip
import hashlib
import json
import math
import os
import random
import ssl
import sys
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from src.l5_r4_action_comparison import harvest
from src.l5_r4_fidelity_decomposition import coverage, expand_logs
from src.l5_r5f_sequence_ledger import campaign_report
from src.polymarket_l5.action_model import (IMBALANCE_BUCKETS, PRICE_BANDS,
                                            TTE_BUCKETS, _bucket)

try:                                        # optional: only for C1 semantics
    from src.pmus_event_progress import proximity as venue_proximity
except Exception:                           # pragma: no cover - defensive
    venue_proximity = None                  # type: ignore

OUT_DIR = os.path.join("analysis", "l5")
JSON_NAME = "l5_sports_clock_study.json"
MD_NAME = "L5_SPORTS_CLOCK_STUDY.md"
CANONICAL = os.path.join(OUT_DIR, "l5_canonical_sequence.csv.gz")
MODEL_V2 = os.path.join(OUT_DIR, "l5_action_model_v2.json")
GATEWAY = "https://gateway.polymarket.us"
SHELL_PREFIX = "R5F/"

# The temporal split.  Day 1 of R5F ran 2026-09-07 19:21-20:58Z; the probe was
# idle until 2026-09-08 00:51Z, so any wall-clock boundary inside that gap
# gives the same split.  Everything at or after it is OUT OF SAMPLE.
IN_SAMPLE_END = "2026-09-07T23:00:00+00:00"

# C3 parameters.  Stated explicitly; sensitivity reported over K_SWEEP.
K_CENTS_DEFAULT = 2.0
GAP_S_DEFAULT = 300.0
K_SWEEP = (1.0, 2.0, 3.0, 5.0)

# C3 phase buckets (seconds since the episode opened).
PHASE_BUCKETS: Tuple[Tuple[float, str], ...] = (
    (15.0, "ep<15s"), (60.0, "ep15-60s"), (1e12, "ep>=60s"))

# Trailing-drift buckets for the mini-window test (cents of yes-equivalent
# touch moved over the trailing DRIFT_WINDOW_S, causal).
DRIFT_WINDOW_S = 120.0
DRIFT_BUCKETS: Tuple[Tuple[float, str], ...] = (
    (2.0, "drift<2c"), (5.0, "drift2-5c"), (1e12, "drift>=5c"))

BOOTSTRAP_B = 4000
BOOTSTRAP_SEED = 20260908

LABELS = {
    "realized": "the venue's own number (settlement rows only)",
    "derived": "arithmetic on the ledger's fills / venue rows",
    "measured": "read from the venue's keyless public gateway at probe time",
    "exploratory": "in-sample (R5F day 1); hypothesis generation only",
    "oos": "out-of-sample (2026-09-08 00:51Z onward); evaluated against the "
           "mapping frozen before it was looked at",
    "counterfactual": "did not happen; fill-assumed, queue and depth not "
                      "modelled",
}


# --------------------------------------------------------------------------
# The mapping under test.  FROZEN: hashed into the output so the in-sample and
# out-of-sample numbers refer to one object.  This is a DEFINITION, not a
# promotion -- nothing in the live path reads it.
# --------------------------------------------------------------------------
FROZEN_MAPPING: Dict[str, Any] = {
    "id": "L5_SPORTS_CLOCK_v1",
    "intent": ("A shadow-only annotation that replaces the degenerate "
               "tte_bucket with two causally-computable sports clocks. It is "
               "NOT a replacement state key: the live lookup stays on the "
               "frozen v1/v3 key until an out-of-sample gate is passed."),
    "state_key": "{price_bucket}|{event_phase}|{regime_phase}|{inventory_bucket}",
    "event_phase": {
        "source": "venue-served period/score/ended via "
                  "src.pmus_event_progress.proximity",
        "buckets": ["ev_unknown", "ev_early(<0.34)", "ev_mid(0.34-0.67)",
                    "ev_late(>=0.67)"],
        "missing_policy": "ev_unknown -- never extrapolated from a clock or "
                          "from endDate",
    },
    "regime_phase": {
        "source": "our own decision tape: seconds since the last material "
                  "move of the yes-equivalent touch",
        "material_move_cents": K_CENTS_DEFAULT,
        "episode_gap_s": GAP_S_DEFAULT,
        "buckets": [b for _, b in PHASE_BUCKETS],
    },
    "price_bucket": [b for _, b in PRICE_BANDS],
    "inventory_bucket": [b for _, b in IMBALANCE_BUCKETS],
    "causality": ("every component is a function of the current decision, "
                  "strictly earlier decisions on the same market, and venue "
                  "fields published before the decision; asserted by "
                  "causal_audit()"),
    "not_used": ["settlement outcome", "realized pnl", "any later decision",
                 "market endDate (a fixed gameStartTime + 14d contract "
                 "expiry, not a resolution time)"],
}


def mapping_hash(mapping: Mapping[str, Any] = FROZEN_MAPPING) -> str:
    blob = json.dumps(mapping, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ------------------------------- helpers ----------------------------------

def iso_epoch(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    t = str(s).replace("Z", "+00:00")
    # the log tool emits 9-digit fractions; datetime wants at most 6
    if "." in t:
        head, _, tail = t.partition(".")
        digits = "".join(c for c in tail if c.isdigit())[:6]
        rest = tail[len(digits):].lstrip("0123456789")
        t = f"{head}.{digits or '0'}{rest}"
    try:
        return datetime.fromisoformat(t).timestamp()
    except ValueError:
        return None


def _f(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def bucket_of(value: float, edges: Sequence[Tuple[float, str]]) -> str:
    for hi, label in edges:
        if value < hi:
            return label
    return edges[-1][1]


def phase_bucket(age_s: Optional[float]) -> str:
    """C3 phase.  `None` (no touch ever observed) is named, never guessed."""
    if age_s is None:
        return "ep_unknown"
    return bucket_of(age_s, PHASE_BUCKETS)


def event_phase_bucket(prox: Optional[float]) -> str:
    """C1 phase from the venue's own game position.  Missing stays missing."""
    if prox is None:
        return "ev_unknown"
    if prox < 0.34:
        return "ev_early(<0.34)"
    if prox < 0.67:
        return "ev_mid(0.34-0.67)"
    return "ev_late(>=0.67)"


def drift_bucket(cents: Optional[float]) -> str:
    if cents is None:
        return "drift_unknown"
    return bucket_of(abs(cents), DRIFT_BUCKETS)


# ------------------------- the live decision tape -------------------------

def input_manifest(files: Sequence[str]) -> dict:
    """Name and hash every log file the run consumed, so a re-run on the same
    inputs is verifiable (the log directories are shared and can grow)."""
    rows = []
    for f in sorted(files):
        try:
            with open(f, "rb") as fh:
                blob = fh.read()
        except OSError as exc:
            rows.append({"file": os.path.basename(f),
                         "error": f"{type(exc).__name__}"})
            continue
        rows.append({"file": os.path.basename(f), "bytes": len(blob),
                     "sha256_16": hashlib.sha256(blob).hexdigest()[:16]})
    joint = hashlib.sha256(
        json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return {"files": rows, "n_files": len(rows), "input_set_sha256_16": joint}


def build_ledger(log_paths: Sequence[str], shell_prefix: str = SHELL_PREFIX) -> dict:
    """The per-decision ledger, built with the EXISTING R5F parser."""
    files = expand_logs(list(log_paths))
    h = harvest(files, shell_prefix)
    cov = coverage(files, [d.get("decision_id") for d in h["decisions"]])
    cov = {k: v for k, v in cov.items() if k != "truncated"}
    rep = campaign_report(h, cov)
    rep["_selections"] = h.get("selections") or []
    rep["_input_manifest"] = input_manifest(files)
    return rep


def selection_progress(selections: Sequence[Mapping[str, Any]]) -> Dict[str, list]:
    """slug -> [(t_epoch, {period, proximity, basis, clock})] from the probe's
    own selection rows.  These are the ONLY retrospective record of the
    venue's live game state during R5F: the gateway serves the CURRENT state
    of an event and cannot be replayed."""
    out: Dict[str, list] = collections.defaultdict(list)
    for sel in selections:
        t = iso_epoch(sel.get("log_ts") or sel.get("status_t"))
        if t is None:
            continue
        for a in sel.get("admitted") or []:
            slug = a.get("slug")
            if not slug:
                continue
            period = a.get("game_period")
            prox = _f(a.get("game_proximity"))
            basis = "logged_game_proximity" if prox is not None else None
            if venue_proximity is not None:
                # reuse the live module's own token map so the basis (and any
                # fallback proximity) is the venue-served one, not a new guess
                p2, b2 = venue_proximity(period)
                basis = b2 if prox is None else f"{b2}|{basis}"
                if prox is None:
                    prox = p2
            out[slug].append((t, {
                "period": period, "proximity": prox, "basis": basis,
                "clock": a.get("game_clock"),
            }))
    for slug in out:
        out[slug].sort(key=lambda kv: kv[0])
    return dict(out)


def latest_at(series: Sequence[Tuple[float, Any]], t: float) -> Optional[Any]:
    """The most recent entry at or before t.  Causal by construction."""
    best = None
    for tt, v in series:
        if tt <= t:
            best = v
        else:
            break
    return best


def decision_tape(rep: Mapping[str, Any]) -> List[dict]:
    """One flat, time-ordered row per decision with the raw fields the clocks
    need.  No clock is computed here."""
    prog = selection_progress(rep.get("_selections") or [])
    rows: List[dict] = []
    for slug, m in (rep.get("per_market") or {}).items():
        for r in sorted(m.get("rows") or [], key=lambda x: x["t_utc"]):
            t = iso_epoch(r.get("t_utc"))
            if t is None:
                continue
            touch = r.get("touch") or {}
            order = r.get("order") or {}
            inv_a = r.get("inventory_after") or {}
            rows.append({
                "slug": slug,
                "idx": r.get("idx"),
                "t": t,
                "t_utc": r.get("t_utc"),
                "state": r.get("state"),
                "tte_bucket": (str(r.get("state") or "").split("|")[1]
                               if str(r.get("state") or "").count("|") == 2
                               else None),
                "price_bucket": (str(r.get("state") or "").split("|")[0]
                                 if str(r.get("state") or "").count("|") == 2
                                 else None),
                "inventory_bucket": (str(r.get("state") or "").split("|")[2]
                                     if str(r.get("state") or "").count("|") == 2
                                     else None),
                "acted": r.get("acted"),
                "l5_sampled": r.get("l5_sampled"),
                "refusal_class": r.get("refusal_class"),
                "outcome": r.get("outcome"),
                "yes_equiv": _f(touch.get("yes_equiv")),
                "relation": order.get("relation"),
                "px": _f(order.get("px")),
                "filled_qty": _f(order.get("filled_qty")) or 0.0,
                "residual_after": _f(inv_a.get("residual")),
                "placed_order": bool(order.get("order_id")),
                "progress_series": prog.get(slug) or [],
            })
    rows.sort(key=lambda r: (r["slug"], r["t"]))
    return rows


# ------------------------------ the clocks --------------------------------

def clocks_for_market(rows: Sequence[Mapping[str, Any]],
                      k_cents: float = K_CENTS_DEFAULT,
                      gap_s: float = GAP_S_DEFAULT,
                      drift_window_s: float = DRIFT_WINDOW_S) -> List[dict]:
    """C1/C2/C3 for one market's decisions, in time order.

    Strictly causal: at row i nothing later than row i is read.  The episode
    anchor only ever moves forward, and every trailing lookup scans the prefix.
    """
    k = k_cents / 100.0
    out: List[dict] = []
    anchor_px: Optional[float] = None
    anchor_t: Optional[float] = None
    last_obs_t: Optional[float] = None
    episode = -1
    first_t: Optional[float] = None
    hist: List[Tuple[float, float]] = []          # (t, yes_equiv), causal
    for r in sorted(rows, key=lambda x: x["t"]):
        t = r["t"]
        ye = r.get("yes_equiv")
        if first_t is None:
            first_t = t
        opened = None
        if ye is None:
            pass
        elif anchor_px is None:
            episode += 1
            anchor_px, anchor_t, opened = ye, t, "first_observation"
        elif last_obs_t is not None and (t - last_obs_t) >= gap_s:
            episode += 1
            anchor_px, anchor_t, opened = ye, t, "gap>=%gs" % gap_s
        elif abs(ye - anchor_px) >= k:
            episode += 1
            anchor_px, anchor_t, opened = ye, t, "move>=%gc" % k_cents
        if ye is not None:
            last_obs_t = t
        age = (t - anchor_t) if anchor_t is not None else None

        # C3 trailing drift over the window, from the causal prefix only
        drift = None
        if ye is not None:
            past = [y for tt, y in hist if tt <= t - drift_window_s]
            if past:
                drift = (ye - past[-1]) * 100.0

        # C1 from the latest selection row at or before t
        series = r.get("progress_series") or []
        prg = latest_at(series, t) or {}
        prox = prg.get("proximity")
        obs = [tt for tt, _ in series if tt <= t]
        c1_age = (t - obs[-1]) if obs else None

        out.append(dict(
            r,
            # C1
            c1_period=prg.get("period"),
            c1_proximity=prox,
            c1_basis=prg.get("basis"),
            c1_event_phase=event_phase_bucket(prox),
            c1_observation_age_s=(round(c1_age, 1) if c1_age is not None else None),
            # C2
            c2_since_first_decision_s=(t - first_t) if first_t is not None else None,
            c2_remaining_tradeable_life_s=None,
            c2_remaining_basis="not_served_by_venue:endDate_is_gameStart+14d",
            # C3
            c3_episode=episode if episode >= 0 else None,
            c3_episode_opened_by=opened,
            c3_regime_age_s=age,
            c3_regime_phase=phase_bucket(age),
            c3_trailing_drift_cents=drift,
            c3_drift_bucket=drift_bucket(drift),
        ))
        if ye is not None:
            hist.append((t, ye))
    return out


def attach_clocks(rows: Sequence[Mapping[str, Any]], **kw) -> List[dict]:
    by_slug: Dict[str, List[dict]] = collections.defaultdict(list)
    for r in rows:
        by_slug[r["slug"]].append(dict(r))
    out: List[dict] = []
    for slug in sorted(by_slug):
        out.extend(clocks_for_market(by_slug[slug], **kw))
    out.sort(key=lambda r: (r["t"], r["slug"]))
    return out


CLOCK_FIELDS = ("c1_event_phase", "c1_observation_age_s",
                "c2_since_first_decision_s", "c3_episode",
                "c3_regime_age_s", "c3_regime_phase",
                "c3_trailing_drift_cents", "c3_drift_bucket")


def clock_diagnostics(rows: Sequence[Mapping[str, Any]]) -> dict:
    """What each clock actually covers on this cohort, and how stale it is.
    Missing coverage is reported, never filled in."""
    ages = sorted(r["c1_observation_age_s"] for r in rows
                  if r.get("c1_observation_age_s") is not None)

    def pct(q: float) -> Optional[float]:
        return ages[min(len(ages) - 1, int(q * len(ages)))] if ages else None
    n = len(rows) or 1
    return {
        "c1_event_progress": {
            "decisions_with_a_venue_game_position": sum(
                1 for r in rows if r.get("c1_proximity") is not None),
            "decisions": len(rows),
            "share": round(sum(1 for r in rows
                               if r.get("c1_proximity") is not None) / n, 4),
            "by_basis": dict(sorted(collections.Counter(
                str(r.get("c1_basis")) for r in rows).items())),
            "by_period_token": dict(sorted(collections.Counter(
                str(r.get("c1_period")) for r in rows).items())),
            "observation_age_s_pcts": {"p0": pct(0.0), "p25": pct(0.25),
                                       "p50": pct(0.5), "p75": pct(0.75),
                                       "p90": pct(0.9), "max": (ages[-1] if ages else None)},
            "caveat": ("the venue serves the CURRENT state of an event and "
                       "cannot be replayed; the only retrospective record for "
                       "R5F is the probe's own selection rows, which are "
                       "written at market entry, so the attached game "
                       "position goes stale while a market is held. Effective "
                       "independent units are MARKETS, not decisions: each "
                       "market contributes essentially one event phase."),
        },
        "c2_market_lifecycle": {
            "since_first_decision_s_available": sum(
                1 for r in rows if r.get("c2_since_first_decision_s") is not None),
            "remaining_tradeable_life": "NOT SERVED by the venue",
            "reason": ("market.endDate on a live sports market is a fixed "
                       "gameStartTime + 14 d contract expiry; on a closed "
                       "market it is overwritten and can precede "
                       "gameStartTime. Neither is a resolution time."),
        },
        "c3_local_regime": {
            "decisions_with_a_touch": sum(
                1 for r in rows if r.get("yes_equiv") is not None),
            "episodes": len({(r["slug"], r["c3_episode"]) for r in rows
                             if r.get("c3_episode") is not None}),
            "phase_counts": dict(sorted(collections.Counter(
                str(r.get("c3_regime_phase")) for r in rows).items())),
            "drift_counts": dict(sorted(collections.Counter(
                str(r.get("c3_drift_bucket")) for r in rows).items())),
        },
    }


def market_level_event_phase(fills: Sequence[Mapping[str, Any]]) -> dict:
    """The honest unit for C1: one row per MARKET that created residual."""
    by: Dict[str, dict] = {}
    for f in fills:
        e = by.setdefault(f["slug"], {"slug": f["slug"], "phases": set(),
                                      "periods": set(), "fills": 0,
                                      "completed_fills": 0, "reduced_fills": 0,
                                      "cohort": f.get("cohort")})
        e["phases"].add(f.get("c1_event_phase"))
        e["periods"].add(str(f.get("c1_period")))
        e["fills"] += 1
        e["completed_fills"] += bool(f.get("completed"))
        e["reduced_fills"] += bool(f.get("residual_reduced"))
    rows = []
    for e in by.values():
        rows.append({"slug": e["slug"], "cohort": e["cohort"],
                     "event_phases": sorted(x for x in e["phases"] if x),
                     "periods": sorted(e["periods"]),
                     "residual_fills": e["fills"],
                     "completed_fills": e["completed_fills"],
                     "reduced_fills": e["reduced_fills"],
                     "all_completed": e["completed_fills"] == e["fills"]})
    rows.sort(key=lambda r: (r["event_phases"], r["slug"]))
    tab: Dict[str, List[int]] = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        key = r["event_phases"][0] if r["event_phases"] else "ev_unknown"
        tab[key][1] += 1
        tab[key][0] += bool(r["all_completed"])
    return {"markets": rows,
            "markets_fully_completed_by_event_phase":
                {k: {"markets_fully_completed": v[0], "markets": v[1]}
                 for k, v in sorted(tab.items())},
            "note": ("this is the sample the C1 effect actually rests on: a "
                     "handful of MARKETS, not tens of fills")}


def causal_audit(rows: Sequence[Mapping[str, Any]],
                 clock_fn: Optional[Callable[..., List[dict]]] = None,
                 fields: Sequence[str] = CLOCK_FIELDS, **kw) -> dict:
    """Re-derive every clock for decision i from the PREFIX rows[:i+1] only and
    assert it equals the full-pass value.  A feature that peeked at a later
    row would differ here.  Returns the audit; raises AssertionError on a
    mismatch so the test suite fails loudly."""
    fn = clock_fn or clocks_for_market
    by_slug: Dict[str, List[dict]] = collections.defaultdict(list)
    for r in rows:
        by_slug[r["slug"]].append(dict(r))
    checked = mismatches = 0
    detail: List[str] = []
    for slug, rs in sorted(by_slug.items()):
        rs.sort(key=lambda x: x["t"])
        full = fn(rs, **kw)
        for i in range(len(rs)):
            prefix = fn(rs[:i + 1], **kw)[-1]
            for f in fields:
                checked += 1
                a, b = full[i].get(f), prefix.get(f)
                if isinstance(a, float) and isinstance(b, float):
                    same = abs(a - b) < 1e-9
                else:
                    same = a == b
                if not same:
                    mismatches += 1
                    if len(detail) < 10:
                        detail.append(f"{slug}[{i}].{f}: full={a!r} prefix={b!r}")
    assert mismatches == 0, "non-causal clock field(s): " + "; ".join(detail)
    return {"fields_checked": checked, "mismatches": mismatches,
            "claim": ("every clock field for decision i is reproduced exactly "
                      "from rows[:i+1]; no field reads a later decision, a "
                      "settlement outcome, or any future venue state")}


# ------------------------- the degeneracy measurement ---------------------

def live_state_distribution(rows: Sequence[Mapping[str, Any]]) -> dict:
    out: Dict[str, collections.Counter] = {
        "tte_bucket": collections.Counter(),
        "price_bucket": collections.Counter(),
        "inventory_bucket": collections.Counter(),
    }
    for r in rows:
        for k in out:
            out[k][r.get(k) or "<none>"] += 1
    n = len(rows)
    return {k: {"n": n,
                "counts": dict(sorted(c.items())),
                "share": {kk: round(vv / n, 4) for kk, vv in sorted(c.items())}}
            for k, c in out.items()}


def canonical_state_distribution(model_path: str = MODEL_V2) -> dict:
    """L5's canonical distribution over the SAME key, from the frozen model's
    base transition counts (read only)."""
    with open(model_path, "rb") as fh:
        raw = fh.read()
    model = json.loads(raw)
    tte: collections.Counter = collections.Counter()
    price: collections.Counter = collections.Counter()
    inv: collections.Counter = collections.Counter()
    total = 0
    for key, acts in (model.get("base") or {}).items():
        parts = key.split("|")
        if len(parts) != 3:
            continue
        n = sum(acts.values())
        total += n
        price[parts[0]] += n
        tte[parts[1]] += n
        inv[parts[2]] += n
    def pack(c: collections.Counter) -> dict:
        return {"n": total, "counts": dict(sorted(c.items())),
                "share": {k: round(v / total, 4) for k, v in sorted(c.items())}}
    return {"model_sha256_16": hashlib.sha256(raw).hexdigest()[:16],
            "n_transitions": total,
            "tte_bucket": pack(tte), "price_bucket": pack(price),
            "inventory_bucket": pack(inv)}


def canonical_tte_support(csv_path: str = CANONICAL) -> dict:
    """What L5's tte buckets ACTUALLY contain, in seconds -- the fact that
    makes the shared label misleading."""
    per: Dict[str, Dict[str, Any]] = {}
    durations: collections.Counter = collections.Counter()
    windows: Dict[str, str] = {}
    n = 0
    with gzip.open(csv_path, "rt", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            s = _f(row.get("seconds_to_settlement"))
            if s is None:
                continue
            n += 1
            b = _bucket(s, TTE_BUCKETS)
            e = per.setdefault(b, {"n": 0, "min_s": s, "max_s": s,
                                   "durations": collections.Counter()})
            e["n"] += 1
            e["min_s"] = min(e["min_s"], s)
            e["max_s"] = max(e["max_s"], s)
            e["durations"][row.get("duration_minutes")] += 1
            durations[row.get("duration_minutes")] += 1
            windows[row.get("window_id")] = row.get("duration_minutes")
    for b in per:
        per[b]["durations"] = dict(sorted(per[b]["durations"].items()))
        per[b]["share"] = round(per[b]["n"] / n, 4)
    return {"trades": n, "windows": len(windows),
            "window_durations_minutes": dict(sorted(
                collections.Counter(windows.values()).items())),
            "by_tte_bucket": dict(sorted(per.items())),
            "note": ("L5's canonical tape contains ONLY 5- and 15-minute "
                     "windows, so its 'tte>10m' bucket is bounded above by "
                     "the 15-minute window length -- it means 'the opening "
                     "third of a 15-minute window', not 'more than ten "
                     "minutes from resolution'.")}


# --------------- does the clock carry information in L5's own domain? -----

def _relation(prev_residual: float, outcome: str, side: str) -> str:
    if side == "SELL":
        return "sell"
    if abs(prev_residual) < 1.0:
        return "open"
    surplus = "Up" if prev_residual > 0 else "Down"
    return "extend" if outcome == surplus else "complete"


def canonical_records(csv_path: str = CANONICAL,
                      k_cents: float = K_CENTS_DEFAULT) -> List[tuple]:
    """(price_b, tte_b, imb_b, regime_age_b, relation) per L5 decision, with the
    SAME local-regime clock definition applied to his crypto tape."""
    by_window: "collections.OrderedDict[str, list]" = collections.OrderedDict()
    with gzip.open(csv_path, "rt", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            by_window.setdefault(row["window_id"], []).append(row)
    k = k_cents / 100.0
    recs: List[tuple] = []
    for rows in by_window.values():
        rows.sort(key=lambda r: (int(float(r["timestamp"])),
                                 int(float(r["trade_seq"] or 0))))
        anchor_p = anchor_t = None
        for prev, nxt in zip(rows, rows[1:]):
            p = _f(prev.get("price"))
            t = _f(prev.get("timestamp"))
            res = _f(prev.get("residual_qty_signed")) or 0.0
            tte = _f(nxt.get("seconds_to_settlement"))
            if p is None or t is None or tte is None:
                continue
            if anchor_p is None or abs(p - anchor_p) >= k:
                anchor_p, anchor_t = p, t
            recs.append((
                _bucket(p, PRICE_BANDS),
                _bucket(tte, TTE_BUCKETS),
                _bucket(abs(res), IMBALANCE_BUCKETS),
                phase_bucket(t - anchor_t),
                _relation(res, nxt.get("outcome"), nxt.get("side")),
            ))
    return recs


def _entropy(counter: Mapping[str, int]) -> float:
    n = sum(counter.values())
    if not n:
        return 0.0
    return -sum((v / n) * math.log2(v / n) for v in counter.values() if v)


def information_test(recs: Sequence[tuple], folds: int = 5,
                     seed: int = BOOTSTRAP_SEED) -> dict:
    """How much does each state dimension tell L5 about his own next relation?
    In-sample conditional entropy plus a held-out log-loss, so a feature is
    not rewarded merely for splitting the data finer."""
    base = collections.Counter(r[4] for r in recs)
    H = _entropy(base)
    feats: Dict[str, Callable[[tuple], Any]] = {
        "price": lambda r: r[0],
        "tte": lambda r: r[1],
        "inventory": lambda r: r[2],
        "regime_age": lambda r: r[3],
        "price+tte+inventory (LIVE KEY)": lambda r: (r[0], r[1], r[2]),
        "price+inventory (time dropped)": lambda r: (r[0], r[2]),
        "price+regime_age+inventory (tte->regime)": lambda r: (r[0], r[3], r[2]),
        "price+tte+regime_age+inventory": lambda r: (r[0], r[1], r[3], r[2]),
    }
    rng = random.Random(seed)
    idx = list(range(len(recs)))
    rng.shuffle(idx)
    parts = [set(idx[i::folds]) for i in range(folds)]
    out: Dict[str, Any] = {}
    for name, f in feats.items():
        d: Dict[Any, collections.Counter] = collections.defaultdict(collections.Counter)
        for r in recs:
            d[f(r)][r[4]] += 1
        n = len(recs)
        cond = sum(sum(c.values()) / n * _entropy(c) for c in d.values())
        losses = []
        for held in parts:
            tr: Dict[Any, collections.Counter] = collections.defaultdict(collections.Counter)
            glob: collections.Counter = collections.Counter()
            for j, r in enumerate(recs):
                if j in held:
                    continue
                tr[f(r)][r[4]] += 1
                glob[r[4]] += 1
            gn = sum(glob.values()) or 1
            tot = 0.0
            for j in held:
                r = recs[j]
                c = tr.get(f(r))
                if c:
                    p = ((c[r[4]] + glob[r[4]] / gn) / (sum(c.values()) + 1.0))
                else:
                    p = glob[r[4]] / gn
                tot += -math.log2(max(p, 1e-12))
            losses.append(tot / max(len(held), 1))
        out[name] = {"states": len(d),
                     "H_rel_given_X_bits": round(cond, 4),
                     "information_gain_bits": round(H - cond, 4),
                     "held_out_log_loss_bits": round(sum(losses) / folds, 4)}
    return {"n": len(recs), "H_relation_bits": round(H, 4),
            "relation_marginal": {k: round(v / len(recs), 4)
                                  for k, v in base.most_common()},
            "by_feature": out,
            "reading": ("held-out log-loss is the honest column: lower is "
                        "better. A time dimension that helps must beat "
                        "'price+inventory (time dropped)'.")}


# ------------------------------ outcome linkage ---------------------------

def fill_sequences(rows: Sequence[Mapping[str, Any]]) -> List[dict]:
    """One record per fill that LEFT residual, with the clocks at that moment
    and whether/when the residual was later removed on that market.

    `completed` and `latency_s` are OUTCOMES, never features: no clock reads
    them."""
    by_slug: Dict[str, List[dict]] = collections.defaultdict(list)
    for r in rows:
        if (r.get("filled_qty") or 0) > 0:
            by_slug[r["slug"]].append(dict(r))
    out: List[dict] = []
    for slug, fs in sorted(by_slug.items()):
        fs.sort(key=lambda x: x["t"])
        for i, f in enumerate(fs):
            res = f.get("residual_after")
            if res is None or res <= 0.01:
                continue
            later = [g for g in fs[i + 1:]
                     if (g.get("residual_after") is not None
                         and g["residual_after"] <= 0.01)]
            reduced = [g for g in fs[i + 1:]
                       if (g.get("residual_after") is not None
                           and g["residual_after"] < res - 0.01)]
            out.append({
                "slug": slug, "t_utc": f["t_utc"], "t": f["t"],
                "relation": f.get("relation"), "px": f.get("px"),
                "qty": f.get("filled_qty"), "residual_after": res,
                "c1_event_phase": f.get("c1_event_phase"),
                "c1_period": f.get("c1_period"),
                "c3_regime_phase": f.get("c3_regime_phase"),
                "c3_drift_bucket": f.get("c3_drift_bucket"),
                "c3_trailing_drift_cents": f.get("c3_trailing_drift_cents"),
                "tte_bucket": f.get("tte_bucket"),
                "cohort": f.get("cohort"),
                "completed": bool(later),
                "latency_s": (round(later[0]["t"] - f["t"], 2) if later else None),
                # softer outcome: the residual was later PARTLY removed. A
                # market can pair most of its size and still never reach zero
                # (aec-setkameua-malden-stoole: 18.90 of 23.66 paired, 4.76
                # left), which `completed` alone would score as a total miss.
                "residual_reduced": bool(reduced),
                "reduction_latency_s": (round(reduced[0]["t"] - f["t"], 2)
                                        if reduced else None),
            })
    return out


def rate_table(rows: Sequence[Mapping[str, Any]], key: str,
               hit: Callable[[Mapping[str, Any]], bool],
               keep: Optional[Callable[[Mapping[str, Any]], bool]] = None) -> dict:
    d: Dict[str, List[int]] = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        if keep is not None and not keep(r):
            continue
        k = str(r.get(key))
        d[k][1] += 1
        if hit(r):
            d[k][0] += 1
    return {k: {"hits": v[0], "n": v[1],
                "rate": (round(v[0] / v[1], 4) if v[1] else None)}
            for k, v in sorted(d.items())}


def cluster_bootstrap(rows: Sequence[Mapping[str, Any]],
                      stat: Callable[[Sequence[Mapping[str, Any]]], Optional[float]],
                      cluster_key: str = "slug", b: int = BOOTSTRAP_B,
                      seed: int = BOOTSTRAP_SEED) -> dict:
    """Resample MARKETS, not decisions -- decisions inside one market are not
    independent.  Returns the point estimate and a percentile CI, with the
    number of resamples the statistic was defined on."""
    by: Dict[str, List[Mapping[str, Any]]] = collections.defaultdict(list)
    for r in rows:
        by[str(r.get(cluster_key))].append(r)
    keys = sorted(by)
    point = stat(list(rows))
    rng = random.Random(seed)
    draws: List[float] = []
    for _ in range(b):
        sample: List[Mapping[str, Any]] = []
        for _ in keys:
            sample.extend(by[keys[rng.randrange(len(keys))]])
        v = stat(sample)
        if v is not None:
            draws.append(v)
    draws.sort()
    if not draws:
        return {"point": point, "ci95": None, "B_defined": 0,
                "clusters": len(keys)}
    lo = draws[int(0.025 * len(draws))]
    hi = draws[min(len(draws) - 1, int(0.975 * len(draws)))]
    return {"point": (round(point, 4) if point is not None else None),
            "ci95": [round(lo, 4), round(hi, 4)],
            "B_defined": len(draws), "clusters": len(keys),
            "labels": "cluster bootstrap over markets; percentile CI"}


def _diff_rate(rows: Sequence[Mapping[str, Any]], key: str, a: Sequence[str],
               b: Sequence[str], hit: Callable[[Mapping[str, Any]], bool],
               min_n: int = 3) -> Optional[float]:
    ca = [0, 0]
    cb = [0, 0]
    for r in rows:
        k = str(r.get(key))
        if k in a:
            ca[1] += 1
            ca[0] += bool(hit(r))
        elif k in b:
            cb[1] += 1
            cb[0] += bool(hit(r))
    if ca[1] < min_n or cb[1] < min_n:
        return None
    return ca[0] / ca[1] - cb[0] / cb[1]


def outcome_linkage(rows: Sequence[Mapping[str, Any]],
                    fills: Sequence[Mapping[str, Any]]) -> dict:
    acted = [r for r in rows if r.get("acted") != "hold"]
    filled = lambda r: (r.get("filled_qty") or 0) > 0            # noqa: E731
    done = lambda r: bool(r.get("completed"))                    # noqa: E731
    res: Dict[str, Any] = {
        "decisions": len(rows),
        "order_placing_decisions": len(acted),
        "residual_creating_fills": len(fills),
        "fill_rate_by_tte_bucket": rate_table(acted, "tte_bucket", filled),
        "fill_rate_by_regime_phase": rate_table(acted, "c3_regime_phase", filled),
        "fill_rate_by_event_phase": rate_table(acted, "c1_event_phase", filled),
        "fill_rate_by_drift_bucket": rate_table(acted, "c3_drift_bucket", filled),
        "completion_by_tte_bucket": rate_table(fills, "tte_bucket", done),
        "completion_by_regime_phase": rate_table(fills, "c3_regime_phase", done),
        "completion_by_event_phase": rate_table(fills, "c1_event_phase", done),
        "completion_by_drift_bucket": rate_table(fills, "c3_drift_bucket", done),
        "residual_reduced_by_event_phase": rate_table(
            fills, "c1_event_phase", lambda r: bool(r.get("residual_reduced"))),
        "residual_reduced_by_regime_phase": rate_table(
            fills, "c3_regime_phase", lambda r: bool(r.get("residual_reduced"))),
    }
    lat: Dict[str, Any] = {}
    for key in ("c3_regime_phase", "c3_drift_bucket", "c1_event_phase"):
        d: Dict[str, List[float]] = collections.defaultdict(list)
        for f in fills:
            if f.get("latency_s") is not None:
                d[str(f.get(key))].append(float(f["latency_s"]))
        lat[key] = {k: {"n": len(v), "median_s": round(sorted(v)[len(v) // 2], 1)}
                    for k, v in sorted(d.items()) if v}
    res["completion_latency_median_s"] = lat
    res["effects"] = {
        "fill_rate(ep<15s) - fill_rate(ep>=60s)": cluster_bootstrap(
            acted, lambda s: _diff_rate(s, "c3_regime_phase", ("ep<15s",),
                                        ("ep>=60s",), filled)),
        "completion(quiet regime) - completion(drift>=5c)": cluster_bootstrap(
            fills, lambda s: _diff_rate(s, "c3_drift_bucket",
                                        ("drift<2c", "drift_unknown"),
                                        ("drift>=5c",), done)),
        "completion(ev_early|mid) - completion(ev_late)": cluster_bootstrap(
            fills, lambda s: _diff_rate(s, "c1_event_phase",
                                        ("ev_early(<0.34)", "ev_mid(0.34-0.67)"),
                                        ("ev_late(>=0.67)",), done)),
    }
    return res


def market_runs(rows: Sequence[Mapping[str, Any]],
                rep: Mapping[str, Any]) -> List[dict]:
    """Per market: the path the yes-equivalent touch took while we held it, and
    the settlement economics the ledger derived, side by side.  The path is
    descriptive (it uses the whole market history) and is labelled as such --
    it is NOT one of the causal features."""
    by: Dict[str, List[Mapping[str, Any]]] = collections.defaultdict(list)
    for r in rows:
        by[r["slug"]].append(r)
    out: List[dict] = []
    for slug, rs in sorted(by.items()):
        ys = [r["yes_equiv"] for r in sorted(rs, key=lambda x: x["t"])
              if r.get("yes_equiv") is not None]
        if not ys:
            continue
        m = (rep.get("per_market") or {}).get(slug) or {}
        e = m.get("economics") or {}
        d = e.get("derived") or {}
        out.append({
            "slug": slug, "decisions": len(rs),
            "fills": m.get("orders_filled"),
            "entry_yes_equiv": ys[0], "last_yes_equiv": ys[-1],
            "max_excursion_cents": round(max(abs(y - ys[0]) for y in ys) * 100, 1),
            "net_move_cents": round((ys[-1] - ys[0]) * 100, 1),
            "matched_pairs": d.get("matched_pairs"),
            "matched_pair_cost": d.get("matched_pair_cost"),
            "directional_residual": d.get("directional_residual"),
            "realized_usd_venue": e.get("realized_usd_venue"),
            "settlement_status": e.get("status"),
            "labels": "path fields are DESCRIPTIVE (whole-market), not features",
        })
    out.sort(key=lambda r: -(r["max_excursion_cents"] or 0))
    return out


# ----------------------------- venue coverage -----------------------------

DEFAULT_LEAGUES = ("mlb", "cfb", "nfl", "nba", "nhl", "atp", "wta", "itfm",
                   "itfw", "setkameua", "setkamemd", "setkamecz", "wtt",
                   "sea", "ekst", "arg2", "epl", "cs2", "dota2", "lol",
                   "valorant")


# What the venue's own published OpenAPI DECLARES per sport, as opposed to
# what a probe happens to catch live.  Used only to say "declared but not
# observed in this window" -- never to claim a field is served.
VENUE_DECLARED_SUBSTATES: Tuple[str, ...] = (
    "baseballState", "footballState", "soccerState", "tennisState",
    "cricketState", "ufcState",
)
VENUE_DECLARED_EVENT_FIELDS: Tuple[str, ...] = (
    "score", "period", "elapsed", "clock", "live", "ended", "status",
    "gameStartTime", "startTime", "startDate", "endDate", "gameId",
    "sportradarGameId", "currentInnings", "innings", "inningHalf", "outs",
)


def _get_json(path: str, timeout: float = 30.0) -> Any:
    ctx = ssl.create_default_context()
    ca = os.getenv("PMUS_CA_BUNDLE", "").strip()
    if ca:
        ctx = ssl.create_default_context(cafile=ca)
    req = urllib.request.Request(GATEWAY + path,
                                 headers={"User-Agent": "l5-sports-clock-study"})
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
        return json.loads(r.read())


def gateway_coverage(leagues: Sequence[str] = DEFAULT_LEAGUES,
                     get_json: Optional[Callable[[str], Any]] = None,
                     sample_market_slugs: Sequence[str] = ()) -> dict:
    """MEASURED coverage of venue-served sport state, per league, from the
    KEYLESS public gateway.  A league with no live event at probe time is
    reported as `not_observed` -- never as "no coverage"."""
    gj = get_json or _get_json
    rows: Dict[str, Any] = {}
    for slug in leagues:
        try:
            d = gj(f"/v2/leagues/{slug}/events?limit=50")
        except Exception as exc:                # pragma: no cover - network
            rows[slug] = {"error": f"{type(exc).__name__}:{str(exc)[:120]}"}
            continue
        evs = (d or {}).get("events") or []
        live = [e for e in evs if e.get("live")]
        fields: collections.Counter = collections.Counter()
        subs: collections.Counter = collections.Counter()
        samples: List[dict] = []
        for e in live:
            st = e.get("eventState") or {}
            for k, v in st.items():
                if isinstance(v, dict) and k.endswith("State"):
                    subs[k] += 1
                    for kk, vv in v.items():
                        if vv not in (None, "", [], {}):
                            fields[f"{k}.{kk}"] += 1
                elif v not in (None, "", [], {}):
                    fields[k] += 1
            if e.get("elapsed"):
                fields["elapsed"] += 1
            if len(samples) < 2:
                samples.append({"ticker": e.get("ticker"),
                                "period": e.get("period"),
                                "score": st.get("score"),
                                "substates": sorted(k for k, v in st.items()
                                                    if isinstance(v, dict))})
        rows[slug] = {
            "events_listed": len(evs), "live_at_probe": len(live),
            "observed": "yes" if live else "not_observed",
            "substates": dict(sorted(subs.items())),
            "populated_fields": dict(sorted(fields.items())),
            "samples": samples,
        }
    markets: Dict[str, Any] = {}
    for slug in sample_market_slugs:
        try:
            d = gj(f"/v1/market/slug/{slug}")
        except Exception as exc:                # pragma: no cover - network
            markets[slug] = {"error": f"{type(exc).__name__}:{str(exc)[:120]}"}
            continue
        mk = d.get("market") if isinstance(d, dict) and "market" in d else d
        if not isinstance(mk, dict):
            markets[slug] = {"error": "unexpected_shape"}
            continue
        gs, ed = mk.get("gameStartTime"), mk.get("endDate")
        markets[slug] = {
            "gameStartTime": gs, "endDate": ed, "startDate": mk.get("startDate"),
            "sportsMarketType": mk.get("sportsMarketType"),
            "endDate_before_gameStartTime": (
                bool(gs and ed and str(ed) < str(gs))),
            "endDate_minus_gameStart_days": (
                round((iso_epoch(ed) - iso_epoch(gs)) / 86400.0, 2)
                if iso_epoch(ed) is not None and iso_epoch(gs) is not None
                else None),
        }
    observed_subs = {k for r in rows.values()
                     for k in (r.get("substates") or {})}
    return {"probed_utc": datetime.now(timezone.utc).isoformat(),
            "gateway": GATEWAY, "auth": "none (keyless public gateway, GET)",
            "by_league": rows, "market_detail": markets,
            "venue_declared_substates": list(VENUE_DECLARED_SUBSTATES),
            "declared_but_not_observed_in_this_window": sorted(
                set(VENUE_DECLARED_SUBSTATES) - observed_subs),
            "venue_declared_event_fields": list(VENUE_DECLARED_EVENT_FIELDS),
            "declared_source": ("the venue's published OpenAPI for /v1/events "
                                "and /v2/sports; DECLARED is not the same as "
                                "served -- a league with no live event at "
                                "probe time is 'not_observed', which is "
                                "missing evidence, not evidence of absence"),
            "labels": LABELS["measured"]}


# --------------------------------- verdicts -------------------------------

OOS_GATE: Dict[str, Any] = {
    "id": "L5_SPORTS_CLOCK_v1_OOS_GATE",
    "applies_to": "any live use of the event-progress clock, in any form",
    "why_a_gate_at_all": (
        "the in-sample effect is null and the out-of-sample effect rests on "
        "5 markets; a gate is the only thing standing between a 13-market "
        "pattern and a live behaviour change"),
    "must_all_pass": [
        {"name": "sample", "requirement":
            ">= 60 residual-creating fills spread over >= 40 DISTINCT markets "
            "and >= 4 leagues, collected AFTER the mapping hash below was "
            "written, in shadow"},
        {"name": "effect", "requirement":
            "completion(ev_early|ev_mid) - completion(ev_late), cluster-"
            "bootstrapped over markets, has a 95% CI whose LOWER bound is "
            "> 0.10 (not merely excludes zero)"},
        {"name": "coherence", "requirement":
            "the substituted key must not lose action fidelity: agreement "
            "with the frozen v1/v3 relation draw, measured on the same rows, "
            "must not fall by more than 0.5 pp, and the held-out log-loss of "
            "price+event_phase+inventory must be <= that of "
            "price+tte+inventory on the SAME rows"},
        {"name": "coverage", "requirement":
            "a venue-served game position on >= 90% of decisions with an "
            "observation age p90 <= 120 s (i.e. the clock is polled per "
            "decision, not carried from market entry)"},
        {"name": "no_pnl_only_promotion", "requirement":
            "a P&L improvement alone does NOT satisfy this gate; the "
            "coherence criterion must pass on its own"},
    ],
    "if_it_fails": "record the shadow numbers and change nothing.",
}

SMALLEST_SHADOW_IMPLEMENTATION: Dict[str, Any] = {
    "id": "L5_SPORTS_CLOCK_v1_SHADOW",
    "one_line": ("attach the venue-served event phase and the local-regime "
                 "age to every decision row as ANNOTATION, and change "
                 "nothing else"),
    "why_this_and_not_more": (
        "the live probe already fetches the venue's live-event feed every "
        "cycle (src/pmus_event_progress.table, wired into "
        "src/pmus_l5_continuous_session for the ranker) and already writes "
        "game_period / game_proximity onto SELECTION rows. The only thing "
        "missing is that the annotation is not written per DECISION, so the "
        "clock goes stale the moment a market is held -- observation age p90 "
        "661 s in this cohort. Nothing new has to be fetched."),
    "changes": [
        "on each decision row, alongside the existing `state`, add "
        "`sports_clock`: {event_phase, game_period, game_proximity, "
        "proximity_basis, observation_age_s, regime_phase, regime_age_s, "
        "trailing_drift_cents, mapping_sha}",
        "no change to `state`, to the lookup key, to relation selection, to "
        "sizing, to pricing, to admission, or to any cap or gate",
        "the annotation must be written even when it is ev_unknown, so the "
        "coverage number in the gate is measurable",
    ],
    "explicitly_not_changed": [
        "the frozen action model (sha bbb55bf90638d2bd) and lookup v3",
        "the fidelity gate", "the safety shell", "the approvals file",
        "market admission / selection ranking",
    ],
    "how_it_gets_evaluated": ("re-run this study with --logs pointed at the "
                             "shadow logs; the OOS gate above is the pass "
                             "condition. Until it passes, the annotation is "
                             "measurement only."),
    "coherence_argument_not_pnl": (
        "the case for recording it is that the live key currently asserts a "
        "state ('tte>10m') that in L5's training data means 'the opening "
        "third of a 15-minute crypto window'. Recording the event phase makes "
        "the translation auditable: we can finally SAY which sports states we "
        "are mapping onto which crypto states, and show the mapping is not "
        "outcome-derived. That is worth doing even if the P&L effect is zero, "
        "and it is the only reason offered here."),
}


def loss_partition_audit(rep: Mapping[str, Any]) -> dict:
    """The ledger's per-market residual partition, split into the markets
    whose partition RECONCILES with their derived residual contribution and
    those that do not.  Only the reconciling total is quoted anywhere; a
    non-reconciling market is named, not silently summed."""
    ok: Dict[str, str] = {c: "0" for c in
                          ("faithful_l5_behavior", "execution_queue_failure",
                           "market_selection_transfer",
                           "remaining_shell_distortion")}
    ok_f = {k: 0.0 for k in ok}
    reconciling: List[str] = []
    failing: List[dict] = []
    for slug, m in (rep.get("per_market") or {}).items():
        lp = m.get("loss_partition")
        if not lp:
            continue
        chk = lp.get("partition_check") or {}
        part = lp.get("partition_of_residual_loss") or {}
        if chk.get("reconciles"):
            reconciling.append(slug)
            for k in ok_f:
                v = part.get(k)
                v = v.get("usd") if isinstance(v, Mapping) else v
                ok_f[k] += _f(v) or 0.0
        else:
            failing.append({"slug": slug,
                            "residual_contribution_usd":
                                chk.get("residual_contribution_usd"),
                            "partition_sum_usd": chk.get("sum_usd")})
    return {
        "reconciling_markets": sorted(reconciling),
        "reconciling_totals_usd": {k: round(v, 4) for k, v in ok_f.items()},
        "non_reconciling_markets": failing,
        "note": ("a non-reconciling market is EXCLUDED from the totals above. "
                 "src/l5_r5f_sequence_ledger's FIFO residual walk was built "
                 "for the day-1 cohort; on a market that opens, completes and "
                 "re-opens several times it attributes more dollars than the "
                 "market's residual contribution. Reported as a finding, not "
                 "patched here (the ledger is another session's file)."),
    }


def verdicts(rows, fills, in_rows, oos_rows, in_fills, oos_fills,
             rep: Mapping[str, Any]) -> dict:
    tte = collections.Counter(r.get("tte_bucket") for r in rows)
    n = len(rows) or 1
    dominant = tte.most_common(1)[0] if tte else (None, 0)
    filled = lambda r: (r.get("filled_qty") or 0) > 0            # noqa: E731
    return {
        "q1_is_the_time_dimension_degenerate": {
            "answer": "YES, completely",
            "evidence": (f"{dominant[1]} of {n} decisions "
                         f"({100.0 * dominant[1] / n:.1f}%) sit in the single "
                         f"bucket {dominant[0]!r}; the tte dimension takes one "
                         f"value across the whole live sample, in-sample and "
                         f"out-of-sample alike."),
            "sharper": ("the shared label is misleading in the other "
                        "direction too: in L5's canonical tape 'tte>10m' is "
                        "bounded at 896 s and comes entirely from 15-minute "
                        "windows, so it means 'the opening third of a "
                        "15-minute crypto window'. Our sports decisions sit "
                        "hours from resolution and key into it."),
        },
        "q2_does_the_mapping_explain_the_residual_losses": {
            "answer": "NO",
            "reason": ("a feature that takes one value cannot explain any "
                       "variation in outcome. The tte bucket is identical on "
                       "the markets that paired to zero residual and on the "
                       "markets that stranded residual, so it accounts for "
                       "0 of the loss."),
            "where_the_loss_actually_sits": loss_partition_audit(rep),
            "realized_usd_venue_sum": rep.get("realized_usd_venue_sum"),
            "corollary": ("the degeneracy is a COHERENCE defect -- we key a "
                          "crypto model on a state it never trained on -- not, "
                          "on this evidence, the cause of the losses."),
        },
        "q3_is_the_degeneracy_costing_action_fidelity": {
            "answer": "MUCH LESS THAN IT LOOKS",
            "evidence": ("on L5's OWN crypto tape the tte dimension carries "
                         "0.17% of the entropy of his next relation and buys "
                         "0.0018 bits held-out over price+inventory; the "
                         "inventory dimension carries ~7%. Losing the time "
                         "dimension loses very little of what the key does."),
            "caveat": ("this measures the RELATION distribution the key is "
                       "used for. It says nothing about the economics of "
                       "holding residual, which is a different question and "
                       "is where the sports/crypto difference actually bites."),
        },
        "q4_mini_window": {
            "answer": "EPISODES EXIST; EPISODE PHASE DOES NOT PREDICT OUR "
                      "OUTCOMES ON THIS SAMPLE",
            "episodes": len({(r["slug"], r["c3_episode"]) for r in rows
                             if r.get("c3_episode") is not None}),
            "fill_rate_effect": cluster_bootstrap(
                [r for r in rows if r.get("acted") != "hold"],
                lambda s: _diff_rate(s, "c3_regime_phase", ("ep<15s",),
                                     ("ep>=60s",), filled)),
            "reading": ("a local price/flow episode structure is clearly "
                        "present and is not degenerate, but within-episode "
                        "phase separates neither fills nor completions here."),
        },
        "q5_best_analogue": {
            "answer": ("the venue-served EVENT-PROGRESS clock (period / score "
                       "/ ended), NOT a wall clock and NOT the local-regime "
                       "clock"),
            "why": ("it is the only one of the three that is (a) served by "
                    "the venue for most leagues we trade, (b) monotone toward "
                    "resolution the way seconds_to_settlement is, and (c) "
                    "directionally associated with completion here. The "
                    "market-lifecycle clock has no served endpoint (endDate "
                    "is a contract expiry) and the local-regime clock is, on "
                    "L5's own tape, a WORSE time dimension than tte."),
            "status": "NOT ESTABLISHED -- see the sample and the robustness "
                      "check below",
            "sample_it_rests_on": market_level_event_phase(fills)[
                "markets_fully_completed_by_event_phase"],
            "robustness_check_FAILS": {
                "what": ("the C1 gradient survives only under the STRICT "
                         "outcome 'the residual later reached zero'. Under "
                         "the softer outcome 'the residual was later reduced "
                         "at all' the gradient disappears."),
                "strict_completion_by_event_phase": rate_table(
                    fills, "c1_event_phase", lambda r: bool(r.get("completed"))),
                "residual_reduced_by_event_phase": rate_table(
                    fills, "c1_event_phase",
                    lambda r: bool(r.get("residual_reduced"))),
                "meaning": ("late-game markets DO keep pairing, they just do "
                            "not finish. That is consistent with the crypto "
                            "intuition, and equally consistent with 'we ran "
                            "out of runway / cap on those markets'. On this "
                            "sample the two cannot be separated."),
            },
            "recommendation": ("do NOT substitute it into the live key. "
                               "Record it in shadow, per decision, and re-run "
                               "this study against the OOS gate."),
        },
    }


# --------------------------------- report ---------------------------------

def build_report(log_paths: Sequence[str], in_sample_end: str = IN_SAMPLE_END,
                 canonical: str = CANONICAL, model: str = MODEL_V2,
                 k_cents: float = K_CENTS_DEFAULT, gap_s: float = GAP_S_DEFAULT,
                 coverage_snapshot: Optional[dict] = None,
                 probe_gateway: bool = False,
                 skip_canonical: bool = False) -> dict:
    rep = build_ledger(log_paths)
    tape = decision_tape(rep)
    rows = attach_clocks(tape, k_cents=k_cents, gap_s=gap_s)
    split = iso_epoch(in_sample_end)
    for r in rows:
        r["cohort"] = "in_sample" if r["t"] < split else "oos"
    fills = fill_sequences(rows)
    audit = causal_audit(rows, k_cents=k_cents, gap_s=gap_s)

    in_rows = [r for r in rows if r["cohort"] == "in_sample"]
    oos_rows = [r for r in rows if r["cohort"] == "oos"]
    in_fills = [f for f in fills if f.get("cohort") == "in_sample"]
    oos_fills = [f for f in fills if f.get("cohort") == "oos"]

    sensitivity = {}
    for k in K_SWEEP:
        rr = attach_clocks(tape, k_cents=k, gap_s=gap_s)
        eps = len({(r["slug"], r["c3_episode"]) for r in rr
                   if r["c3_episode"] is not None})
        sensitivity[f"k={k}c"] = {
            "episodes": eps,
            "phase_share": {kk: round(vv / len(rr), 4) for kk, vv in
                            sorted(collections.Counter(
                                r["c3_regime_phase"] for r in rr).items())},
        }

    out: Dict[str, Any] = {
        "question": ("most defensible sports analogue of L5's crypto "
                     "seconds_to_settlement / window-phase state; and whether "
                     "the current mapping materially explains R5F residual "
                     "losses or missed completions"),
        "owner_ref": "#167 comment 5577894093",
        "labels": LABELS,
        "frozen_mapping": FROZEN_MAPPING,
        "frozen_mapping_sha256_16": mapping_hash(),
        "parameters": {"k_cents": k_cents, "gap_s": gap_s,
                       "drift_window_s": DRIFT_WINDOW_S,
                       "in_sample_end_utc": in_sample_end,
                       "bootstrap_B": BOOTSTRAP_B, "bootstrap_seed": BOOTSTRAP_SEED},
        "coverage_of_logs": rep.get("coverage"),
        "input_manifest": rep.get("_input_manifest"),
        "sample": {
            "markets": len(rep.get("markets") or []),
            "decisions": len(rows),
            "in_sample_decisions": len(in_rows),
            "oos_decisions": len(oos_rows),
            "in_sample_markets": len({r["slug"] for r in in_rows}),
            "oos_markets": len({r["slug"] for r in oos_rows}),
            "fills": sum(1 for r in rows if (r.get("filled_qty") or 0) > 0),
            "residual_creating_fills": len(fills),
            "in_sample_window_utc": [min((r["t_utc"] for r in in_rows), default=None),
                                     max((r["t_utc"] for r in in_rows), default=None)],
            "oos_window_utc": [min((r["t_utc"] for r in oos_rows), default=None),
                               max((r["t_utc"] for r in oos_rows), default=None)],
            "warning": ("SMALL. Every rate below is a handful of markets; the "
                        "cluster bootstrap CIs are the honest read, not the "
                        "point estimates."),
        },
        "causal_audit": audit,
        "clock_diagnostics": {
            "all": clock_diagnostics(rows),
            "in_sample": clock_diagnostics(in_rows),
            "oos": clock_diagnostics(oos_rows),
        },
        "event_phase_market_level": {
            "all": market_level_event_phase(fills),
            "in_sample_EXPLORATORY": market_level_event_phase(in_fills),
            "oos_VALIDATION": market_level_event_phase(oos_fills),
        },
        "tte_degeneracy": {
            "live_r5f": live_state_distribution(rows),
            "live_r5f_in_sample": live_state_distribution(in_rows),
            "live_r5f_oos": live_state_distribution(oos_rows),
            "why": ("src/pmus_l5_action_shadow.SPORTS_TTE_DEFAULT_S = 1e9 -- "
                    "an in-game sports market has no settlement clock on the "
                    "tape, so TTE collapses to the coarsest bucket. The "
                    "venue's only settlement-like field, market.endDate, is a "
                    "fixed gameStartTime + 14 d CONTRACT EXPIRY (see "
                    "src/pmus_event_progress docstring and market_detail "
                    "below), so wiring it in would not un-degenerate the key."),
        },
        "regime_clock_sensitivity": sensitivity,
        "outcome_linkage": {
            "all": outcome_linkage(rows, fills),
            "in_sample_EXPLORATORY": outcome_linkage(in_rows, in_fills),
            "oos_VALIDATION": outcome_linkage(oos_rows, oos_fills),
        },
        "verdicts": verdicts(rows, fills, in_rows, oos_rows, in_fills,
                             oos_fills, rep),
        "oos_gate": OOS_GATE,
        "smallest_shadow_only_implementation": SMALLEST_SHADOW_IMPLEMENTATION,
        "market_runs": market_runs(rows, rep),
        "loss_partition_from_ledger": loss_partition_audit(rep),
        "loss_partition_ledger_raw_totals_UNAUDITED":
            rep.get("loss_partition_totals_usd"),
        "realized_usd_venue_sum": rep.get("realized_usd_venue_sum"),
        "losing_markets": rep.get("losing_markets"),
    }
    if not skip_canonical:
        out["canonical_l5"] = canonical_state_distribution(model)
        out["canonical_tte_support"] = canonical_tte_support(canonical)
        recs = canonical_records(canonical, k_cents=k_cents)
        out["information_test_on_l5_own_tape"] = information_test(recs)
        live_tte = out["tte_degeneracy"]["live_r5f"]["tte_bucket"]["share"]
        occupied = {k for k, v in live_tte.items() if v > 0}
        can = out["canonical_l5"]["tte_bucket"]["share"]
        out["tte_degeneracy"]["share_of_l5_decisions_in_buckets_we_never_occupy"] = \
            round(sum(v for k, v in can.items() if k not in occupied), 4)
    if probe_gateway:
        out["venue_coverage"] = gateway_coverage(
            sample_market_slugs=sorted({r["slug"] for r in rows})[:8])
    elif coverage_snapshot is not None:
        out["venue_coverage"] = coverage_snapshot
    return out


# ---------------------------------- md ------------------------------------

def _pct(x: Optional[float]) -> str:
    return "-" if x is None else f"{100 * x:.1f}%"


def render_md(rep: Mapping[str, Any]) -> str:
    s = rep["sample"]
    L: List[str] = [
        "# L5 sports clock study: is there a defensible analogue of "
        "`seconds_to_settlement`?", "",
        f"Owner question ({rep['owner_ref']}): {rep['question']}", "",
        f"Sample: {s['decisions']} decisions over {s['markets']} markets "
        f"({s['in_sample_decisions']} in-sample / {s['oos_decisions']} "
        f"out-of-sample), {s['fills']} fills, "
        f"{s['residual_creating_fills']} residual-creating fills. "
        f"{s['warning']}", "",
        f"Re-run: `python3 -m src.l5_sports_clock_study --logs DIR "
        f"[DIR ...] [--probe-gateway|--offline]` over the input set "
        f"`{(rep.get('input_manifest') or {}).get('input_set_sha256_16')}` "
        f"({(rep.get('input_manifest') or {}).get('n_files')} probe log "
        f"dumps; every file name and hash is in the JSON). Log directories "
        f"are shared and can grow -- the manifest is what pins a run.", "",
        f"Frozen mapping `{rep['frozen_mapping']['id']}` sha "
        f"`{rep['frozen_mapping_sha256_16']}`. "
        f"Causal audit: {rep['causal_audit']['fields_checked']} clock-field "
        f"re-derivations from the causal prefix, "
        f"{rep['causal_audit']['mismatches']} mismatches.", "",
        "## 0. Verdicts", "",
    ]
    for k, v in (rep.get("verdicts") or {}).items():
        L.append(f"**{k}** -> {v.get('answer')}")
        for kk, vv in v.items():
            if kk == "answer":
                continue
            L.append(f"  - {kk}: {vv}")
        L.append("")
    gate = rep.get("oos_gate") or {}
    if gate:
        L += [f"### Out-of-sample gate `{gate.get('id')}`", "",
              f"Applies to: {gate.get('applies_to')}. "
              f"{gate.get('why_a_gate_at_all')}", ""]
        for c in gate.get("must_all_pass") or []:
            L.append(f"- **{c['name']}** -- {c['requirement']}")
        L += ["", f"If it fails: {gate.get('if_it_fails')}", ""]
    impl = rep.get("smallest_shadow_only_implementation") or {}
    if impl:
        L += [f"### Smallest shadow-only implementation `{impl.get('id')}`", "",
              impl.get("one_line", ""), "",
              f"Why only this: {impl.get('why_this_and_not_more')}", ""]
        for c in impl.get("changes") or []:
            L.append(f"- {c}")
        L += ["", "Explicitly NOT changed: "
              + "; ".join(impl.get("explicitly_not_changed") or []), "",
              f"Coherence argument (not P&L): {impl.get('coherence_argument_not_pnl')}",
              "", f"Evaluation: {impl.get('how_it_gets_evaluated')}", ""]
    L += [
        "## 1. Is the time dimension degenerate on sports?", "",
    ]
    live = rep["tte_degeneracy"]["live_r5f"]["tte_bucket"]
    L += ["| tte bucket | R5F decisions | R5F share | L5 canonical share |",
          "|---|---:|---:|---:|"]
    can = (rep.get("canonical_l5") or {}).get("tte_bucket", {}).get("share", {})
    for b in [x for _, x in TTE_BUCKETS]:
        L.append(f"| {b} | {live['counts'].get(b, 0)} | "
                 f"{_pct(live['share'].get(b, 0.0))} | {_pct(can.get(b))} |")
    never = rep["tte_degeneracy"].get(
        "share_of_l5_decisions_in_buckets_we_never_occupy")
    L += ["", f"Share of L5's canonical decisions in buckets we never occupy: "
              f"**{_pct(never)}**.", "",
          rep["tte_degeneracy"]["why"], ""]
    sup = rep.get("canonical_tte_support")
    if sup:
        L += [f"L5's canonical tape holds only "
              f"{sup['window_durations_minutes']}-minute windows "
              f"({sup['windows']} windows, {sup['trades']} prints). "
              f"{sup['note']}", "",
              "| tte bucket | L5 prints | seconds_to_settlement range |",
              "|---|---:|---|"]
        for b, e in sup["by_tte_bucket"].items():
            L.append(f"| {b} | {e['n']} | {e['min_s']:.0f}-{e['max_s']:.0f} s |")
        L.append("")
    inv = rep["tte_degeneracy"]["live_r5f"]["inventory_bucket"]["share"]
    caninv = (rep.get("canonical_l5") or {}).get(
        "inventory_bucket", {}).get("share", {})
    L += ["The inventory dimension is degenerate in the OTHER direction:", "",
          "| inventory bucket | R5F share | L5 canonical share |",
          "|---|---:|---:|"]
    for b in [x for _, x in IMBALANCE_BUCKETS]:
        L.append(f"| {b} | {_pct(inv.get(b, 0.0))} | {_pct(caninv.get(b))} |")
    L.append("")

    it = rep.get("information_test_on_l5_own_tape")
    if it:
        L += ["## 2. Does a clock carry information in L5's OWN domain?", "",
              f"N = {it['n']} L5 decisions, H(relation) = "
              f"{it['H_relation_bits']} bits. {it['reading']}", "",
              "| feature | states | IG (in-sample, bits) | held-out log-loss "
              "(bits) |", "|---|---:|---:|---:|"]
        for k, v in it["by_feature"].items():
            L.append(f"| {k} | {v['states']} | {v['information_gain_bits']} | "
                     f"{v['held_out_log_loss_bits']} |")
        L.append("")

    vc = rep.get("venue_coverage")
    if vc:
        L += ["## 3. What sport-native state does the venue actually serve?", "",
              f"Measured {vc['probed_utc']} on {vc['gateway']} ({vc['auth']}).",
              "", "| league | events | live at probe | sample period / score | "
              "per-sport substate | populated fields |",
              "|---|---:|---:|---|---|---|"]
        for lg, e in vc["by_league"].items():
            if "error" in e:
                L.append(f"| {lg} | - | - | - | ERROR | {e['error']} |")
                continue
            smp = (e.get("samples") or [{}])[0]
            shown = (f"`{smp.get('period')}` / `{smp.get('score')}`"
                     if smp.get("period") is not None else "-")
            L.append(f"| {lg} | {e['events_listed']} | {e['live_at_probe']} | "
                     f"{shown} | {', '.join(e['substates']) or '-'} | "
                     f"{', '.join(sorted(e['populated_fields'])) or 'not_observed'} |")
        L += ["", f"Venue-DECLARED per-sport substates: "
                  f"{', '.join(vc.get('venue_declared_substates') or [])}. "
                  f"Declared but NOT observed live in this probe window: "
                  f"{', '.join(vc.get('declared_but_not_observed_in_this_window') or []) or 'none'}. "
                  f"{vc.get('declared_source', '')}", "",
              "Market-detail probe (the settlement-clock question):", "",
              "| market | gameStartTime | endDate | endDate - gameStart (days) |",
              "|---|---|---|---:|"]
        for slug, e in (vc.get("market_detail") or {}).items():
            if "error" in e:
                L.append(f"| {slug} | - | - | {e['error']} |")
                continue
            L.append(f"| {slug} | {e['gameStartTime']} | {e['endDate']} | "
                     f"{e['endDate_minus_gameStart_days']} |")
        L.append("")

    cd = rep["clock_diagnostics"]["all"]
    L += ["## 3b. What each clock covers on our own decisions", "",
          f"- **C1 event progress**: a venue-served game position on "
          f"{cd['c1_event_progress']['decisions_with_a_venue_game_position']} "
          f"of {cd['c1_event_progress']['decisions']} decisions "
          f"({_pct(cd['c1_event_progress']['share'])}); observation age "
          f"p50 {cd['c1_event_progress']['observation_age_s_pcts']['p50']}s, "
          f"p90 {cd['c1_event_progress']['observation_age_s_pcts']['p90']}s, "
          f"max {cd['c1_event_progress']['observation_age_s_pcts']['max']}s. "
          f"{cd['c1_event_progress']['caveat']}",
          f"- period tokens seen: {cd['c1_event_progress']['by_period_token']}",
          f"- **C2 market lifecycle**: "
          f"{cd['c2_market_lifecycle']['remaining_tradeable_life']} -- "
          f"{cd['c2_market_lifecycle']['reason']}",
          f"- **C3 local regime**: "
          f"{cd['c3_local_regime']['decisions_with_a_touch']} decisions carry "
          f"a touch; {cd['c3_local_regime']['episodes']} episodes; phases "
          f"{cd['c3_local_regime']['phase_counts']}", "",
          "## 4. The mini-window test", "",
          f"Local-regime episodes are segmented on our own decision tape: a "
          f"new episode opens on a yes-equivalent touch move of "
          f">= {rep['parameters']['k_cents']}c or a wall-clock gap of "
          f">= {rep['parameters']['gap_s']}s. Sensitivity:", "",
          "| k | episodes | ep<15s | ep15-60s | ep>=60s |", "|---|---:|---:|---:|---:|"]
    for k, v in rep["regime_clock_sensitivity"].items():
        ps = v["phase_share"]
        L.append(f"| {k} | {v['episodes']} | {_pct(ps.get('ep<15s'))} | "
                 f"{_pct(ps.get('ep15-60s'))} | {_pct(ps.get('ep>=60s'))} |")
    L.append("")
    for name, block in (("ALL", "all"), ("IN-SAMPLE (exploratory)",
                                         "in_sample_EXPLORATORY"),
                        ("OUT-OF-SAMPLE (validation)", "oos_VALIDATION")):
        ol = rep["outcome_linkage"][block]
        L += [f"### {name}", "",
              f"- decisions {ol['decisions']}, order-placing "
              f"{ol['order_placing_decisions']}, residual-creating fills "
              f"{ol['residual_creating_fills']}",
              f"- fill rate by regime phase: "
              + "; ".join(f"{k} {v['hits']}/{v['n']}"
                          + (f" ({v['rate']})" if v['rate'] is not None else "")
                          for k, v in ol["fill_rate_by_regime_phase"].items()),
              f"- completion by drift bucket: "
              + "; ".join(f"{k} {v['hits']}/{v['n']}"
                          + (f" ({v['rate']})" if v['rate'] is not None else "")
                          for k, v in ol["completion_by_drift_bucket"].items()),
              f"- STRICT completion by event phase: "
              + "; ".join(f"{k} {v['hits']}/{v['n']}"
                          for k, v in ol["completion_by_event_phase"].items()),
              f"- SOFT 'residual later reduced' by event phase: "
              + "; ".join(f"{k} {v['hits']}/{v['n']}"
                          for k, v in ol["residual_reduced_by_event_phase"].items()),
              ""]
        for eff, v in ol["effects"].items():
            L.append(f"- effect `{eff}` = {v['point']} "
                     f"95% CI {v['ci95']} (clusters {v['clusters']}, "
                     f"B {v['B_defined']})")
        L.append("")

    ml = rep["event_phase_market_level"]["all"]
    L += ["### The C1 effect at its true unit (markets, not fills)", "",
          "| event phase at the residual fills | markets fully completed | "
          "markets |", "|---|---:|---:|"]
    for k, v in ml["markets_fully_completed_by_event_phase"].items():
        L.append(f"| {k} | {v['markets_fully_completed']} | {v['markets']} |")
    L += ["", "| market | cohort | event phase | periods | residual fills | "
              "fully completed | later reduced |", "|---|---|---|---|---:|---:|---:|"]
    for m in ml["markets"]:
        L.append(f"| {m['slug']} | {m['cohort']} | "
                 f"{', '.join(m['event_phases']) or '-'} | "
                 f"{', '.join(m['periods'])} | {m['residual_fills']} | "
                 f"{m['completed_fills']} | {m['reduced_fills']} |")
    L += ["", ml["note"], "",
          "## 5. Where the money actually went", "",
          f"Ledger loss partition (venue realized sum "
          f"{rep['realized_usd_venue_sum']}): "
          f"{rep['loss_partition_from_ledger']}",
          f"Losing markets: {rep['losing_markets']}", "",
          "| market | dec | fills | entry | last | max excursion (c) | net (c) "
          "| pairs | pair cost | residual | realized |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for m in rep["market_runs"]:
        L.append(f"| {m['slug']} | {m['decisions']} | {m['fills']} | "
                 f"{m['entry_yes_equiv']} | {m['last_yes_equiv']} | "
                 f"{m['max_excursion_cents']} | {m['net_move_cents']} | "
                 f"{m['matched_pairs']} | "
                 f"{str(m['matched_pair_cost'])[:7]} | "
                 f"{m['directional_residual']} | {m['realized_usd_venue']} |")
    L += ["", "Path columns are DESCRIPTIVE (whole-market) and are not used as "
              "features anywhere above.", "",
          "Labels: " + "; ".join(f"{k} = {v}" for k, v in rep["labels"].items()),
          ""]
    return "\n".join(L)


# ---------------------------------- cli -----------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="l5_sports_clock_study",
        description=("Measure the degeneracy of L5's time dimension on sports, "
                     "define and test three causal sports clocks, and report "
                     "whether the current mapping explains the R5F residual "
                     "losses. Read-only; nothing here trades."))
    ap.add_argument("--logs", nargs="+", required=True,
                    help="Railway probe log dumps or directories of them")
    ap.add_argument("--out", default=OUT_DIR, help="output directory")
    ap.add_argument("--canonical", default=CANONICAL,
                    help="L5 canonical crypto sequence csv.gz (read only)")
    ap.add_argument("--model", default=MODEL_V2,
                    help="frozen action model v2 json (read only)")
    ap.add_argument("--in-sample-end", default=IN_SAMPLE_END,
                    help="ISO instant; decisions at or after it are OOS")
    ap.add_argument("--k-cents", type=float, default=K_CENTS_DEFAULT,
                    help="material touch move that opens a new episode")
    ap.add_argument("--gap-s", type=float, default=GAP_S_DEFAULT,
                    help="wall-clock gap that also opens a new episode")
    ap.add_argument("--probe-gateway", action="store_true",
                    help="probe the KEYLESS public gateway for per-league "
                         "sport-state coverage (GET only, no auth)")
    ap.add_argument("--offline", action="store_true",
                    help="reuse the venue_coverage snapshot already in the "
                         "output JSON instead of probing (deterministic re-run)")
    ap.add_argument("--skip-canonical", action="store_true",
                    help="skip the canonical-crypto passes (fast smoke run)")
    a = ap.parse_args(argv)

    snapshot = None
    json_path = os.path.join(a.out, JSON_NAME)
    if a.offline and os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as fh:
                snapshot = json.load(fh).get("venue_coverage")
        except (OSError, ValueError):
            snapshot = None

    rep = build_report(a.logs, in_sample_end=a.in_sample_end,
                       canonical=a.canonical, model=a.model,
                       k_cents=a.k_cents, gap_s=a.gap_s,
                       coverage_snapshot=snapshot,
                       probe_gateway=a.probe_gateway,
                       skip_canonical=a.skip_canonical)
    os.makedirs(a.out, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1, sort_keys=True, default=str)
    with open(os.path.join(a.out, MD_NAME), "w", encoding="utf-8") as fh:
        fh.write(render_md(rep))
    print(json.dumps({
        "decisions": rep["sample"]["decisions"],
        "markets": rep["sample"]["markets"],
        "tte_share": rep["tte_degeneracy"]["live_r5f"]["tte_bucket"]["share"],
        "l5_share_in_buckets_we_never_occupy":
            rep["tte_degeneracy"].get(
                "share_of_l5_decisions_in_buckets_we_never_occupy"),
        "frozen_mapping_sha256_16": rep["frozen_mapping_sha256_16"],
        "causal_audit": rep["causal_audit"]["mismatches"],
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
