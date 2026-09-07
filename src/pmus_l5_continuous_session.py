"""Continuous L5 sports campaign: select -> sequential session -> reselect.

OWNER DIRECTIVES 5552036210 / 5552039820 (issue #167): the one-shot
spring gate is not the strategy. This module is the loop:

    every cycle, read the recorder's FRESH authenticated status
      -> run EVERY spring_watch row through the unchanged strict guard
      -> rank the admitted rows by how well the frozen L5 model knows
         their state (in-distribution first, then evidence mass, then
         buy-mass, then touch depth), logging model probability / rank
         for every candidate and the exact refusal for every other row
      -> hand the best one to the canonical bounded session
         (`pmus_spring_l5_session.run_session`: state + inventory ->
         model distribution -> OPEN / EXTEND / COMPLETE / HOLD -> fill
         -> re-evaluate -> repeat)
      -> when that session ends with NO inventory (skip, no fill,
         preflight), pick the next admitted market instead of stopping
      -> when it ends WITH inventory, the campaign holds: one market at
         a time, residual to settlement (OWNER 5547834091)

Market selection is separate from the L5 policy: this file never
decides a side, price or size -- the session and the shared planner do.

Safety envelope is INHERITED, not re-implemented: isolated dual key
(`armed()`), credentials, durable state, daily-loss stop, account-wide
flat/open-order preflight, maker/post-only only, one outstanding order,
<=$0.05 cumulative OPEN/EXTEND notional, bounded actions/residual,
<=0.98 and >=1c-after-known-fees completion, cancel + deadman, restart
NEVER. DISARMED, the loop runs as a SHADOW: it selects and reports the
planned first action every cycle and never touches the venue.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from decimal import Decimal as D
from typing import Any, Mapping, Optional

from src import pmus_spring_l5_probe_entry as entry
from src import pmus_incentive_audit as incentives
from src import pmus_spring_l5_session as base
from src import pmus_l5_persistence_v2 as pv2
from src import pmus_l5_ramp_ladder as ladder
from src.polymarket_l5.action_model import state_key

CAMPAIGN_SECONDS_ENV = "PMUS_SPRING_CAMPAIGN_SECONDS"
# OWNER 5552841170 #3: the approved bounded campaign keeps operating
# without an operator redeploy per market. The arm still EXPIRES (this
# bound, logged at boot as campaign_expires_at) and restart is NEVER;
# the money limits are untouched: <=$0.05 filled OPEN/EXTEND per
# market, one market at a time, daily-loss stop, action/attempt caps.
DEFAULT_CAMPAIGN_S = 6 * 3600.0
SELECT_POLL_S = 15.0            # recorder status cadence is ~20s
SESSION_DEADLINE_S = 900.0      # per-market bound, as the one-shot probe
RETRY_COOLDOWN_S = 120.0        # a market that just refused/no-filled waits
MAX_SESSIONS_PER_CAMPAIGN = 48  # bounded reselection, not a runaway
MAX_SESSIONS_PER_SLUG = 3       # attempt cap per market (x6 orders each)
RESUME_HEARTBEAT_S = 120.0      # re-evaluate a held market at least this often
RESUME_BOOK_POLL_S = 5.0        # ...and sooner when the book actually moves
# OWNER 5553189142: the scaling rung is THROUGHPUT first. A campaign may
# work up to this many FILLED markets sequentially (each after the prior
# is flat/settled, concurrency 1), then stops at the ADVANCE GATE with a
# reconciliation report. Per-market money caps are unchanged, so the
# worst case for the rung is RUNG_MARKETS x SESSION_NOTIONAL_CAP of
# filled OPEN/EXTEND notional, inside the daily-loss stop.
# A venue read error between sessions (HTTP 5xx on a GET) is waited out,
# never fatal: the loop backs off 20s, then 60s once the outage is
# sustained, and keeps polling until the campaign's own expiry. Waiting
# IS the fail-closed state (no order can be placed while the account view
# is unreadable); exiting would leave the held position without its
# settlement audit and the campaign dead until an operator redeploy
# (restart is NEVER). LIVE 17:40-18:2xZ: /v1/orders/open answered 500
# for 40+ minutes while every public endpoint was healthy.
ITERATION_ERROR_BACKOFF_S = 20.0
ITERATION_ERROR_SLOW_BACKOFF_S = 60.0
ITERATION_ERROR_SLOW_AFTER = 10      # consecutive errors before slowing
ITERATION_ERROR_WINDOW_S = 1800.0
MAX_ITERATION_ERRORS = 10 ** 9       # no stop inside the campaign
RUNG_MARKETS_ENV = "PMUS_SPRING_RUNG_MARKETS"
DEFAULT_RUNG_MARKETS = 3
# OWNER (2026-09-06 ~14:45Z, after 5559915772): a fresh R1 EVIDENCE
# continuation after the previous campaign is complete and the account is
# verified flat. Approvals live in the repo (never in an env var); a
# campaign starts from an approval only at the advance gate of the
# previous campaign, after the continuation preflight. Stop rules:
#   rung_markets            -- the rung's markets_per_campaign allowance
#   frozen_evidence_minimums -- selection stops when the frozen gate says
#                              every LIVE minimum is met (200 / 5 / 50 / 50)
CAMPAIGN_APPROVALS_FILE = Path("analysis/l5/l5_campaign_approvals.json")
STOP_RULE_RUNG = "rung_markets"
STOP_RULE_EVIDENCE = "frozen_evidence_minimums"
INITIAL_CAMPAIGN_ID = "R1-INITIAL"
EVIDENCE_MINIMUM_KEYS = ("live_scored_decisions", "live_markets",
                         "live_post_fill_decisions", "live_timed_intervals")
SPORTS_TTE_DEFAULT_S = base.SPORTS_TTE_DEFAULT_S
LABEL_SHADOW = "SHADOW/THEORETICAL; DISARMED; no venue network"


FIDELITY_EVAL_S = 600.0         # frozen L5 fidelity gate re-run cadence
# Gemini shadow service's published rolling report (Railway private
# networking), e.g. http://gemini-l5-shadow.railway.internal:8080/rolling
SHADOW_REPORT_URL_ENV = "L5_SHADOW_REPORT_URL"
_fidelity_last = {"t": 0.0}
_outage_state: dict = {"outage": None}     # the campaign's live outage record


def ledger_decision_count(slug: str) -> int:
    """Decisions already persisted for this market (session_report rows):
    the seed offset for the next session on it (OWNER 5556815940)."""
    path = base.q1.base.state_dir() / "ledger.jsonl"
    if not path.exists():
        return 0
    n = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("kind") == "session_report" and row.get("slug") == slug:
            n += len(row.get("decisions") or [])
    return n


def record_session_report(rep: Mapping[str, Any], slug: str,
                          resumed: bool) -> None:
    """Durable per-session fidelity evidence (OWNER Phase 1 item 8):
    every decision and order of every session goes to the ledger so the
    frozen gate can be re-run across restarts."""
    base.q1.ledger_append({
        "kind": "session_report", "spring": True, "slug": slug,
        "session_id": rep.get("session_id"),
        "decision_seed_offset": rep.get("decision_seed_offset"),
        "resumed": bool(resumed), "status": rep.get("status"),
        "decisions": rep.get("decisions") or [],
        "orders": rep.get("orders") or [],
        # SYNTHETIC 2 s cadence counterfactual (never live evidence)
        "counterfactual_2s": rep.get("counterfactual_2s") or [],
        "inventory": rep.get("inventory")})


def _compact_shadow_diagnostics(rolling: Optional[Mapping[str, Any]]) -> Optional[dict]:
    """The shadow service's read-only diagnostics, compacted for the log:
    off-distribution causes / cross-tab / top states and the
    sampled -> acted funnel (OWNER 5558167805 #2-#4)."""
    if not rolling:
        return None
    attr = rolling.get("off_distribution_attribution") or {}
    fun = rolling.get("sampled_to_acted_funnel") or {}
    if not attr and not fun:
        return None
    top = list((attr.get("by_state") or {}).items())[:6]
    return {
        "off_distribution": {
            k: attr.get(k) for k in ("scored_decisions", "off_distribution_decisions",
                                     "off_distribution_rate_pct", "off_states",
                                     "causes", "cross_tab", "status", "err")
            if attr.get(k) is not None},
        "off_top_states": [{"state": s, "n": e.get("n"), "cause": e.get("cause"),
                            "absent": e.get("absent_components"),
                            "refusals": e.get("refusals"), "post_fill": e.get("post_fill")}
                           for s, e in top],
        # canonical-lookup rows only (the only ones that reach the gate)
        "off_states_v2": [{"state": s, **{k: e.get(k) for k in (
                               "n", "tier", "state_v1", "refusals", "post_fill",
                               "last_rel", "last_size_behavioral", "inputs_example")}}
                          for s, e in list((attr.get("by_state_v2") or {}).items())[:8]],
        "before_after": attr.get("before_after_canonical_lookup"),
        "funnel": {k: fun.get(k) for k in ("scored_decisions", "acted_as_sampled_share",
                                           "held_share", "converted_share",
                                           "held_by_reason", "per_sampled_relation")
                   if fun.get(k) is not None},
    }


PMUS_SHADOW_URL_ENV = "L5_PMUS_SHADOW_URL"


def pmus_shadow_url() -> str:
    """The recorder's /l5_shadow report: explicit env, else derived from
    the recorder status URL the probe already reads."""
    explicit = os.getenv(PMUS_SHADOW_URL_ENV, "").strip()
    if explicit:
        return explicit
    status_url = os.getenv(entry.EVIDENCE_STATUS_URL_ENV, "").strip()
    if status_url.startswith("http") and "/status" in status_url:
        return status_url.split("/status", 1)[0] + "/l5_shadow"
    return ""


def _compact_pmus_shadow(ps: Optional[Mapping[str, Any]],
                         source: Mapping[str, Any]) -> Optional[dict]:
    if not ps:
        return {"source": dict(source)}
    keep = ("status", "decisions", "markets", "markets_shadowed", "post_fill_decisions",
            "theoretical_fills", "counts_toward_frozen_minimums")
    dims = {k: {"status": v.get("status"), "n": v.get("n"), "value": v.get("value")}
            for k, v in ps.items() if isinstance(v, Mapping) and "status" in v
            and k not in ("executable_layer",)}
    ex = ps.get("executable_layer") or {}
    return {**{k: ps.get(k) for k in keep}, **dims,
            "funnel": ps.get("funnel"),
            "executable": {k: ex.get(k) for k in ("execution_eligible", "eligible_share",
                                                  "refusals_by_reason",
                                                  "complete_counterfactual")},
            "lookup_provenance": ps.get("lookup_provenance"),
            "source": dict(source)}


def fidelity_eval(now: float, force: bool = False) -> Optional[dict]:
    """Run the frozen L5 fidelity gate over the ledger's session reports
    (items 8/9): progress toward EVERY minimum, the largest mismatch,
    the divergence classes, and the full verdict the moment every
    minimum is met. Logged as PMUS_L5_CAMPAIGN kind=fidelity_gate."""
    if not force and now - _fidelity_last["t"] < FIDELITY_EVAL_S:
        return None
    _fidelity_last["t"] = now
    rolling, shadow_source = None, {"url": os.getenv(SHADOW_REPORT_URL_ENV, "").strip()}
    if shadow_source["url"]:
        # OWNER 5557209522 #5: the deduplicated stochastic OOS window
        # evidence lives on the Gemini shadow service; it publishes its
        # latest rolling report over Railway private networking and the
        # frozen evaluator reads it here (never restored/pre-freeze rows:
        # the service counts unique ENTERED windows since the freeze)
        try:
            import urllib.request
            with urllib.request.urlopen(shadow_source["url"], timeout=8) as resp:
                rolling = json.loads(resp.read().decode("utf-8"))
            shadow_source["fetched_t"] = rolling.get("t")
        except Exception as exc:                      # noqa: BLE001
            shadow_source["error"] = f"{type(exc).__name__}:{str(exc)[:120]}"
    # OWNER (maximize legitimate evidence turnover): the recorder's READ-ONLY
    # multi-market L5 shadow report, served beside its status document
    pmus_shadow, pmus_source = None, {"url": pmus_shadow_url()}
    if pmus_source["url"]:
        try:
            import urllib.request
            with urllib.request.urlopen(pmus_source["url"], timeout=8) as resp:
                pmus_shadow = json.loads(resp.read().decode("utf-8"))
            pmus_source["fetched_t"] = pmus_shadow.get("t")
        except Exception as exc:                      # noqa: BLE001
            pmus_source["error"] = f"{type(exc).__name__}:{str(exc)[:120]}"
    try:
        from src import l5_fidelity_gate as fgate
        path = base.q1.base.state_dir() / "ledger.jsonl"
        # OWNER 5557786528 #2: raw session rows only; summary snapshots
        # (this very fidelity_gate line included) are rejected by kind and
        # the rejection is counted in the accounting block
        ingest = (fgate.read_reports_accounted(path) if path.exists()
                  else {"reports": [], "rows_total": 0, "summary_rows_ignored": 0,
                        "other_rows_ignored": 0, "raw_decision_rows": 0,
                        "source": str(path)})
        model = pv2.load_v2()
        now_iso = datetime.fromtimestamp(float(now), timezone.utc).isoformat()
        out = fgate.evaluate(ingest["reports"], rolling=rolling, model=model,
                             ingest=ingest, pmus_shadow=pmus_shadow,
                             maintenance=effective_maintenance_windows(
                                 load_maintenance_windows(), _outage_state["outage"],
                                 now_iso))
        out["shadow_source"] = shadow_source
        # the frozen gate's own reading of the LIVE minimums: the stop
        # rule of an evidence-continuation campaign reads it from here
        _fidelity_last["minimums"] = dict(
            (out.get("evidence_progress") or {}).get("minimums") or {})
        _fidelity_last["minimums_t"] = now
    except Exception as exc:                          # noqa: BLE001
        _log("fidelity_gate_error", err=f"{type(exc).__name__}:{str(exc)[:160]}")
        return None
    compact = {
        "label": out["verdict"]["label"],
        "phase_1_met": out["verdict"]["phase_1_met"],
        "evidence_progress": out["evidence_progress"],
        "largest_mismatch": out["largest_mismatch"],
        "divergence_classes": out["divergence_classes"],
        "divergence_unclassified_samples": out.get("divergence_unclassified_samples"),
        "safety_shell_versions": out.get("safety_shell_versions"),
        "failing": out["verdict"]["failing"],
        "insufficient_sample": out["verdict"]["insufficient_sample"],
        "unmeasured": out["verdict"]["unmeasured"],
        "live": {k: {"status": v.get("status"), "n": v.get("n"),
                     "value": v.get("value")}
                 for k, v in out["live"].items() if isinstance(v, Mapping)},
        "shadow": {k: {"status": v.get("status"), "n": v.get("n"),
                       "value": v.get("value")}
                   for k, v in out["shadow"].items() if isinstance(v, Mapping)},
        "shadow_windows": out["shadow"].get("windows_since_freeze"),
        "shadow_windows_v2": out["shadow"].get("windows_entered_unique_v2"),
        "shadow_lookup_status": out["shadow"].get("lookup_status"),
        "shadow_off_distribution_corrected_gate": out["shadow"].get("off_distribution_corrected_gate"),
        "shadow_off_distribution_v1_diagnostic": out["shadow"].get("off_distribution_v1_diagnostic"),
        "shadow_source": out.get("shadow_source"),
        # OWNER 5558167805 #1/#2/#3/#7: cohort split, the shadow service's
        # off-distribution attribution + sampled->acted funnel (compact),
        # and the post-fix COMPLETE price sample
        "cohort": (out["live"] or {}).get("cohort"),
        "live_legacy": ({k: {"status": v.get("status"), "n": v.get("n"),
                             "value": v.get("value")}
                         for k, v in (out.get("live_legacy") or {}).items()
                         if isinstance(v, Mapping)}
                        if out.get("live_legacy") else None),
        "complete_price_counterfactual": out.get("complete_price_counterfactual"),
        "shadow_diagnostics": _compact_shadow_diagnostics(rolling),
        # OWNER: two readings of the same frozen dimensions, and the
        # recorder's read-only multi-market shadow beside the minimums
        "layers": {layer: {k: {"status": v.get("status"), "n": v.get("n"),
                               "value": v.get("value")}
                           for k, v in (block or {}).items() if isinstance(v, Mapping)}
                   for layer, block in (out.get("layers") or {}).items()
                   if layer in ("behavioral", "executable")},
        "pmus_shadow": _compact_pmus_shadow(out.get("pmus_shadow"), pmus_source),
        # OWNER 5559167134 #2/#3: frozen-gate feasibility under the CURRENT
        # safety shell + cap-fallback attribution (read-only)
        "feasibility": out.get("feasibility"),
    }
    _log("fidelity_gate", **compact)
    if out["evidence_progress"]["all_met"]:
        _log("fidelity_verdict", **out["verdict"])
    return out


def _log(kind: str, **fields: Any) -> None:
    print("PMUS_L5_CAMPAIGN " + json.dumps({"kind": kind, **fields},
                                           separators=(",", ":"),
                                           default=str), flush=True)


def _new_fills(rep: Mapping[str, Any]) -> int:
    """Fills that happened IN this session: reported orders with a positive
    filled quantity. `rep["actions"]` is the inventory's whole fill history
    (a resumed market re-carries every earlier fill), so counting it once
    per cycle inflated the funnel's `filled` (90 for 6 real fills)."""
    n = 0
    for o in (rep.get("orders") or []):
        try:
            if D(str(o.get("filled_qty") or "0")) > 0:
                n += 1
        except Exception:
            continue
    return n


def _model_view(model: Optional[Mapping[str, Any]], bid: Any) -> dict:
    """How well the frozen model knows this OPEN state, for ranking and
    for the log. Never decides an action."""
    try:
        b = float(bid)
    except (TypeError, ValueError):
        return {"state": None, "state_n": 0, "off_distribution": True,
                "p_buy": 0.0, "relation_ranked": []}
    if model is None:
        return {"state": None, "state_n": 0, "off_distribution": True,
                "p_buy": 0.0, "relation_ranked": []}
    st = state_key(b, SPORTS_TTE_DEFAULT_S, 0.0)
    d = pv2.v2_dist(model, st, "open", "q<5")
    rels = pv2.relation_mass(d["dist"])
    ranked = sorted(rels.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"state": d["state"], "state_n": int(d["n"]),
            "off_distribution": bool(d["backed_off"]),
            "p_buy": round(rels.get("open", 0.0) + rels.get("extend", 0.0), 4),
            "relation_ranked": [{"relation": r, "p": round(p, 4)}
                                for r, p in ranked]}


def _f(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


PAIR_SCORE_WEIGHTS = {
    # OWNER 5556038561 #6: rank for CLEAN COMPLETED PAIRS, not first-leg
    # fill rate. Every component is a measured recorder/model fact.
    "two_sided_flow": 2.0,        # prints on BOTH sides in the hour
    "flow": 2.0,                  # trades_1h, saturating at 50
    "complement_availability": 2.0,  # opposite side displayed & sized
    "fee_headroom": 1.5,          # pair at the touches vs the 0.98 target
    "on_distribution": 2.0,       # model evidence for the OPEN state
    "depth": 1.0,                 # min(displayed bid, ask) size, sat. 100
    "residual_duration": 0.5,     # settlement proximity (see pair_score)
    "feed_quality": 0.5,          # recorder cadence gaps
    # OWNER 5566810119 #3: incentive eligibility is one of the five factors
    # the ranker must weigh. Measured on R3L: $0.2800 credited on $34.6402
    # of filled notional, payable only where feeCoefficient > 0.
    "rebate_eligible": 1.0,
}


def pair_score(row: Mapping[str, Any], mv: Mapping[str, Any],
               now: float, progress: Optional[Mapping[str, Any]] = None) -> dict:
    """0..1 composite for the ranking (higher = better pair prospects),
    with every component reported so the selection log shows WHY."""
    bid, ask = _f(row.get("bid")), _f(row.get("ask"))
    bq = _f(row.get("bid_qty"), _f(row.get("bid_touch_qty")))
    aq = row.get("ask_qty")
    aq = _f(aq) if aq is not None else None
    depth = min(bq, aq) if aq is not None else bq
    pair_at_touch = (bid + (1.0 - ask)) if (bid and ask) else None
    if pair_at_touch is None:
        headroom = 0.0
    else:
        headroom = (1.0 if pair_at_touch <= 0.98
                    else max(0.0, 1.0 - (pair_at_touch - 0.98) / 0.02))
    end = (row.get("market_state") or {}).get("endDate")
    hours = None
    if end:
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
            hours = max(0.0, (dt.timestamp() - now) / 3600.0)
        except (ValueError, TypeError):
            hours = None
    # residual_duration wants "how soon does this position resolve". endDate
    # cannot answer it: measured live 2026-09-07T02:47Z, all three CFB
    # markets the campaign traded served gameStartTime 2026-09-06T23:30Z and
    # endDate 2026-09-20T23:30Z -- a fixed +14d CONTRACT EXPIRY, identical
    # across markets, which fed through 1-hours/24 is 0.0 for every live
    # sports market. The component was a constant. The venue's own live-event
    # feed serves game position (period/clock/score), so prefer it and keep
    # endDate only as the fallback it always was.
    prox = (progress or {}).get("game_proximity")
    if prox is not None:
        residual_duration = max(0.0, min(1.0, float(prox)))
        horizon_basis = (progress or {}).get("proximity_basis")
    elif hours is not None:
        residual_duration = max(0.0, 1.0 - hours / 24.0)
        horizon_basis = "market_endDate"
    else:
        residual_duration = 0.5
        horizon_basis = "none"
    comp = {
        "two_sided_flow": 1.0 if row.get("two_sided_trades") else 0.0,
        "flow": min(_f(row.get("trades_1h")), 50.0) / 50.0,
        "complement_availability": (1.0 if (ask and (aq is None or aq > 0))
                                    else 0.0),
        "fee_headroom": headroom,
        "on_distribution": (0.0 if mv.get("off_distribution")
                            else min(_f(mv.get("state_n")), 300.0) / 300.0),
        "depth": min(depth, 100.0) / 100.0,
        "residual_duration": residual_duration,
        "feed_quality": (1.0 if row.get("collector_gaps") in (0, None, False)
                         else 0.5),
        # unknown eligibility scores neutral: absence of the venue's
        # feeCoefficient is missing data, never a claim of ineligibility
        "rebate_eligible": (0.5 if (progress or {}).get("rebate_eligible") is None
                            else (1.0 if progress["rebate_eligible"] else 0.0)),
    }
    total_w = sum(PAIR_SCORE_WEIGHTS.values())
    score = sum(PAIR_SCORE_WEIGHTS[k] * v for k, v in comp.items()) / total_w
    return {"pair_score": round(score, 4),
            "pair_at_touch": (round(pair_at_touch, 4)
                              if pair_at_touch is not None else None),
            "hours_to_end": (round(hours, 2) if hours is not None else None),
            "horizon_basis": horizon_basis,
            "rebate_eligible": (progress or {}).get("rebate_eligible"),
            "game_proximity": prox,
            "game_period": (progress or {}).get("period"),
            "game_clock": (progress or {}).get("clock"),
            "components": {k: round(v, 3) for k, v in comp.items()}}


YIELD_CACHE_S = 60.0
_yield_cache: dict = {"t": 0.0, "by_slug": {}, "source": None}


def pmus_shadow_yield(now: Optional[float] = None) -> dict:
    """OWNER 5560404270 #4: the recorder shadow's per-market evidence
    yield (independent in-distribution decisions over the last hour),
    read from /l5_shadow at most every YIELD_CACHE_S. Empty when the
    report is unreachable -- ranking then falls back to state fit."""
    now = time.time() if now is None else now
    if now - _yield_cache["t"] < YIELD_CACHE_S:
        return _yield_cache["by_slug"]
    _yield_cache["t"] = now
    url = pmus_shadow_url()
    by_slug: dict = {}
    if url:
        try:
            import urllib.request
            with urllib.request.urlopen(url, timeout=8) as resp:
                rep = json.loads(resp.read().decode("utf-8"))
            for r in ((rep.get("market_yield") or {}).get("markets") or []):
                if isinstance(r, Mapping) and r.get("slug"):
                    by_slug[str(r["slug"])] = dict(r)
            _yield_cache["source"] = {"url": url, "t": rep.get("t"), "markets": len(by_slug)}
        except Exception as exc:                      # noqa: BLE001
            _yield_cache["source"] = {"url": url, "error": f"{type(exc).__name__}:{str(exc)[:80]}"}
    _yield_cache["by_slug"] = by_slug
    return by_slug


def rank_candidates(status: Mapping[str, Any], now: float,
                    model: Optional[Mapping[str, Any]] = None,
                    exclude: Optional[Mapping[str, float]] = None,
                    yield_by_slug: Optional[Mapping[str, Any]] = None) -> dict:
    """Every spring_watch row through the strict guard; admitted rows
    ranked. Returns {"admitted": [...], "refused": [...]}."""
    from src import pmus_spring_evidence_transfer as xfer
    exclude = exclude or {}
    yield_by_slug = yield_by_slug or {}
    # Venue-served game position for every live sports market, one bounded
    # cached GET. REPORTED and fed to the (diagnostic) pair_score only: the
    # candidate ORDER below is unchanged L5-state fit per OWNER 5556232035
    # #3, and ordering by settlement proximity is a selection change that is
    # the owner's to make, not one to slip in here.
    try:
        from src import pmus_event_progress as prog
        progress_by_slug, progress_diag = prog.table(now=now)
    except Exception as exc:                     # pragma: no cover
        progress_by_slug, progress_diag = {}, {
            "error": f"{type(exc).__name__}:{str(exc)[:80]}"}
    admitted, refused = [], []
    for row in status.get("spring_watch") or []:
        if not isinstance(row, Mapping) or not row.get("slug"):
            continue
        slug = str(row["slug"])
        ev = xfer.compose_evidence_from_recorder(status, slug, now)
        reason = (entry.strict_evidence_reason(slug, ev, now=now)
                  if ev is not None else "compose_failed")
        mv = _model_view(model, row.get("bid"))
        item = {"slug": slug, "bid": row.get("bid"), "ask": row.get("ask"),
                "bid_touch_qty": row.get("bid_touch_qty"),
                "collector_gaps": row.get("collector_gaps"),
                "market_status": (row.get("market_state") or {}).get("status"),
                "probe_entry_action": row.get("probe_entry_action"),
                "trades_1h": row.get("trades_1h"),
                "two_sided_trades": row.get("two_sided_trades"),
                **mv, **pair_score(row, mv, now,
                                   progress_by_slug.get(slug))}
        y = yield_by_slug.get(slug) or {}
        item["evidence_yield"] = ({k: y.get(k) for k in ("decisions", "in_distribution",
                                                         "eligible", "post_fill",
                                                         "legit_decisions_per_h")}
                                  if y else None)
        item["yield_rate"] = float(y.get("legit_decisions_per_h") or 0.0) if y else 0.0
        until = exclude.get(slug)
        if reason is None and until is not None and now < until:
            reason = f"cooldown_until:{round(until - now, 1)}s"
        if reason is None:
            admitted.append(item)
        else:
            refused.append({**item, "reason": reason})

    def _touch(x: Mapping[str, Any]) -> float:
        try:
            return float(x.get("bid_touch_qty") or 0)
        except (TypeError, ValueError):
            return 0.0

    def _gap(x: Mapping[str, Any]) -> int:
        # OWNER 5552475548 item 2: recorder cadence is a QUALITY feature
        # here (a clean feed ranks above a gappy one), never a veto
        g = x.get("collector_gaps")
        return 1 if g is None else int(bool(g))
    # OWNER 5566810119 #3: "Fix ranking horizon blindness ... Rank the broad
    # sports universe for L5-transfer suitability using executable two-sided
    # flow + on-distribution state + spread/depth + rebate/incentive
    # eligibility + actual game/settlement proximity." That is exactly
    # pair_score's component set once residual_duration reads the venue's
    # served game position instead of the dead +14d contract expiry, so
    # pair_score now ORDERS candidates rather than only being logged.
    #
    # This supersedes, for R4 onward and on the owner's own instruction,
    # "pair_score is LOGGED ... and does not order candidates" (OWNER
    # 5556232035 #3) and the yield-first ordering of OWNER 5560404270 #2/#4,
    # whose purpose -- reach the frozen evidence minimums -- is met (752
    # clean scored decisions across 27 markets, every minimum at 100%).
    # Evidence yield stays as the tiebreaker directly beneath it.
    #
    # What does NOT change: off_distribution is still the FIRST key, so a
    # market the reconstructed L5 policy does not know can never outrank one
    # it does, whatever its score; there is no directional or sports view
    # anywhere in this ordering; and execution stays one market at a time.
    admitted.sort(key=lambda x: (x["off_distribution"], -x["pair_score"],
                                 -x["yield_rate"], -x["state_n"], -x["p_buy"],
                                 _gap(x), -_touch(x), x["slug"]))
    # OWNER 5552475548 item 4 prioritised live CFB while the goal was
    # evidence yield. OWNER live decision 2026-09-06 (uptime) RETIRES that
    # override: under HOLD-to-settlement a college-football game is the
    # longest-settling market on the board, and preferring it is what put
    # the campaign into a 97-minute idle hold on
    # tsc-cfb-txs-pvam-2026-09-06-total-57pt5 (19:51Z-21:28Z). Ranking is
    # now purely the L5-state fit / measured-yield order above; CFB still
    # competes on that order like any other league and the `cfb` flag is
    # still reported per row.
    for i, x in enumerate(admitted, 1):
        x["rank"] = i
        x["cfb"] = _is_cfb(x["slug"])
    return {"admitted": admitted, "refused": refused,
            "status_t": status.get("t"), "progress_diag": progress_diag}


CFB_LEAGUE_TOKENS = frozenset({"cfb", "ncaaf", "ncaafb"})


def _is_cfb(slug: str) -> bool:
    parts = str(slug).lower().split("-")
    return len(parts) > 1 and parts[1] in CFB_LEAGUE_TOKENS


def select_candidate(now: Optional[float] = None,
                     model: Optional[Mapping[str, Any]] = None,
                     exclude: Optional[Mapping[str, float]] = None,
                     path: Optional[str] = None) -> dict:
    """One selection cycle: fetch, gate, rank, log. Read-only."""
    now = time.time() if now is None else now
    status, source = entry.load_status_document(path)
    if status is None:
        _log("status_unavailable", source=source)
        return {"chosen": None, "source": source, "admitted": [],
                "refused": []}
    ranking = rank_candidates(status, now, model, exclude,
                              yield_by_slug=pmus_shadow_yield(now))
    chosen = ranking["admitted"][0] if ranking["admitted"] else None
    _log("selection", source=source, status_t=ranking["status_t"],
         evidence_age_s=(round(now - entry._epoch(ranking["status_t"]), 3)
                         if entry._epoch(ranking["status_t"]) else None),
         n_rows=len(ranking["admitted"]) + len(ranking["refused"]),
         chosen=(chosen["slug"] if chosen else None),
         game_progress=ranking.get("progress_diag"),
         yield_source=_yield_cache.get("source"),
         admitted=[{k: x.get(k) for k in ("rank", "slug", "bid", "ask",
                                          "bid_touch_qty", "state",
                                          "state_n", "p_buy",
                                          "relation_ranked",
                                          "probe_entry_action",
                                          "pair_score", "pair_at_touch",
                                          "hours_to_end", "horizon_basis",
                                          "game_proximity", "game_period",
                                          "game_clock", "components",
                                          "evidence_yield", "yield_rate")}
                   for x in ranking["admitted"]],
         refused=[{"slug": x["slug"], "bid": x["bid"],
                   "reason": x["reason"], "state_n": x["state_n"]}
                  for x in ranking["refused"]])
    return {"chosen": chosen, "source": source, **ranking}


RESUME_REEVAL_S = 30.0          # legacy fixed pause (tests); see heartbeat
MULTI_HELD = "MULTIPLE_POSITIONS"


def await_state_change(t, slug: str, clock, sleep,
                       max_wait_s: float = RESUME_HEARTBEAT_S,
                       poll_s: float = RESUME_BOOK_POLL_S) -> dict:
    """OWNER 5552841170 #2: between resume sessions, wait for a MEANINGFUL
    fresh book change (best bid or ask moved) with a heartbeat fallback,
    instead of a blind fixed pause. Read-only BBO polls; never raises."""
    start = clock()

    def _bbo():
        try:
            b = base.q1.outcome_bbo(t, slug, "OUTCOME_SIDE_YES")
            return (str(b.get("bid")), str(b.get("ask")))
        except Exception as exc:                   # noqa: BLE001
            return ("err", type(exc).__name__)
    first = _bbo()
    while clock() - start < max_wait_s:
        sleep(poll_s)
        cur = _bbo()
        if cur != first:
            return {"reason": "book_changed", "from": first, "to": cur,
                    "waited_s": round(clock() - start, 1)}
    return {"reason": "heartbeat", "book": first,
            "waited_s": round(clock() - start, 1)}


def over_concurrency_action(held: list, filled, concurrency: int) -> Optional[str]:
    """What to do when the venue shows more open markets than the rung
    allows. None = within the cap. "halt" = at least one held market is
    NOT one this campaign opened, which is a genuine breach and stops
    everything. "drain" = they are all ours and the rung simply tightened
    under open inventory (OWNER DECISION 5564173746 cut R3L to
    concurrency 1 mid-campaign); halting there would strand real money,
    so the campaign holds each to settlement and opens nothing new."""
    if len(held) <= concurrency:
        return None
    return "halt" if [s for s in held if s not in set(filled)] else "drain"


def held_markets(t) -> list:
    """Every market the account holds inventory in, from the venue's own
    positions (authoritative, not the ledger). OWNER live decision
    2026-09-06 (uptime): a rung whose concurrency is above one may hold
    more than one at a time; the campaign works them round-robin, one
    session at a time, and stops if the venue ever shows more positions
    than the rung allows."""
    one = held_market(t)          # same authority, same failure semantics
    if one is None:
        return []
    if one != MULTI_HELD:
        return [one]
    if t is None:
        return []
    block = base.micro.account_block_reason(t)
    if not block or not block.startswith("nonzero_positions:"):
        return []
    return sorted({p.split("=", 1)[0] for p in block.split(":", 1)[1].split(",")
                   if p})


def held_market(t) -> Optional[str]:
    """OWNER 5552607804: the market the account already holds inventory
    in, from the venue's own positions (authoritative, not the ledger).
    None when flat; MULTI_HELD when more than one market is held, which
    the one-market campaign never continues into."""
    if t is None:
        return None
    block = base.micro.account_block_reason(t)
    if not block or not block.startswith("nonzero_positions:"):
        return None
    slugs = [p.split("=", 1)[0] for p in block.split(":", 1)[1].split(",")
             if p]
    return slugs[0] if len(slugs) == 1 else MULTI_HELD


def _market_gone(rep: Mapping[str, Any]) -> bool:
    """A resumed session that could not read a book or found the venue
    reporting the market not open -- the signals a held market has
    resolved (or is between points; the audit decides, never this)."""
    reason = str(rep.get("reason") or "")
    return (reason.startswith("resume:bbo")
            or reason.startswith("live_market")
            or reason.startswith("resume:metadata"))


def settlement_breakdown(slug: str, audit: Mapping[str, Any]) -> dict:
    """OWNER 5553037966 #1: what the settlement cash was made of.

    From the durable ledger's authoritative fills: contracts bought on
    each side, cost basis, MATCHED pairs (min of the two sides) and the
    DIRECTIONAL residual. From the venue: the realized amount. The
    matched contribution is arithmetic ($1 per matched pair less the
    pair's cost); the residual contribution is what remains of the
    venue's realized number. Fees: the venue's trade rows carry
    `isAggressor`; every fill here is a maker fill and no fee field is
    served, so fees are reported as venue-unreported, never assumed.
    Everything here is labeled derived; only realized_usd is the venue's.
    """
    from decimal import Decimal
    inv = base.held_inventory_from_ledger(slug)
    up, dn = inv.up, inv.dn
    matched = min(up, dn)
    residual = up - dn
    pair_cost = ((inv.avg_up() or Decimal("0")) + (inv.avg_dn() or Decimal("0"))
                 if matched > 0 else None)
    matched_contrib = (matched * (Decimal("1") - pair_cost)
                       if pair_cost is not None else Decimal("0"))
    realized = audit.get("realized_usd")
    trades = (audit.get("trade_summary") or {}).get("trades") or []
    out = {
        "slug": slug,
        "fills": len(inv.actions),
        "bought_yes": str(up), "bought_no": str(dn),
        "avg_yes": str(inv.avg_up()) if up else None,
        "avg_no": str(inv.avg_dn()) if dn else None,
        "cost_basis_usd": str(inv.spent()),
        "matched_pairs": str(matched),
        "matched_pair_cost": str(pair_cost) if pair_cost is not None else None,
        "matched_contribution_usd": str(matched_contrib),
        "directional_residual": str(residual),
        "resolution_side": (audit.get("resolution") or {}).get("side"),
        "realized_usd_venue": realized,
        "residual_contribution_usd": (str(Decimal(str(realized)) - matched_contrib)
                                      if realized is not None else None),
        "maker_fills": sum(1 for tr in trades if tr.get("isAggressor") is False),
        "taker_fills": sum(1 for tr in trades if tr.get("isAggressor") is True),
        "fees_usd": "venue_unreported",
        "labels": "DERIVED accounting from ledger fills; realized_usd_venue "
                  "is the venue's own; nothing else is realized",
    }
    return out


def settle_held(t, slug: str) -> dict:
    """Authoritative settlement for a held market: the venue's
    POSITION_RESOLUTION activity (read-only), recorded idempotently in
    the durable ledger. Realized is written ONLY from the venue's own
    afterPosition.realized; otherwise it stays unknown. Never raises."""
    try:
        from src import pmus_settlement_auditor as auditor
        result = auditor.audit_market(slug, t)
        written = auditor.append_authoritative_ledger(result)
        out = {"status": result.get("status"),
               "settled": result.get("settled"),
               "realized_confirmed": result.get("realized_confirmed"),
               "realized_usd": result.get("realized_usd"),
               "realized_source": result.get("realized_source"),
               "settled_by": result.get("settled_by", "position_resolution_activity"
                                        if result.get("settled") else None),
               "position_net": result.get("position_net"),
               "available_position": result.get("available_position"),
               "venue_qty_total": result.get("venue_qty_total"),
               "ledger_qty_total": result.get("ledger_qty_total"),
               "activity_walk": result.get("activity_walk"),
               "trades": {**{k: (result.get("trade_summary") or {}).get(k)
                             for k in ("busted", "uncleared", "realized_pnl_rows",
                                       "realized_pnl_sum")},
                          **_trade_shape((result.get("trade_summary") or {}).get("trades") or [])},
               "resolution": result.get("resolution"),
               "ledger_rows_written": written}
        if result.get("settled"):
            out["breakdown"] = settlement_breakdown(slug, result)
            if written:      # once, alongside the venue-confirmed rows
                base.q1.ledger_append({"kind": "settlement_breakdown",
                                       "spring": True, **out["breakdown"]})
        elif result.get("status") == "PENDING_SETTLEMENT":
            # OWNER P0 5560183912: a PENDING market is diagnosed against the
            # venue's PUBLIC status (read-only, throttled) so a stale
            # detector is seen from evidence, never guessed
            out["public"] = _pending_diagnostic(slug, t, auditor)
        return out
    except Exception as exc:                       # noqa: BLE001
        return {"status": "AUDIT_UNAVAILABLE",
                "error": f"{type(exc).__name__}:{str(exc)[:80]}"}


def _trade_shape(trades: list) -> dict:
    """LIVE 15:41Z: the venue's trade rows for the market -- state
    histogram, the field names, side/outcome fields and qty per row -- so
    the clearing state that blocks settlement is read, not guessed."""
    states: dict[str, int] = {}
    for tr in trades:
        s = str(tr.get("state"))
        states[s] = states.get(s, 0) + 1
    return {"n": len(trades), "states": states,
            "keys": trades[0].get("keys") if trades else None,
            "rows": [{"t": tr.get("createTime"), "px": tr.get("price"),
                      "qty": tr.get("qty"), "state": tr.get("state"),
                      "aggr": tr.get("isAggressor"), "rpnl": tr.get("realizedPnl"),
                      **(tr.get("side_fields") or {})} for tr in trades[:16]]}


PENDING_DIAGNOSTIC_EVERY_S = 600.0
_pending_diag_last: dict[str, float] = {}


def _pending_diagnostic(slug: str, t, auditor) -> Optional[dict]:
    """Public market view on every audit; the full account-shape
    diagnostic at most every PENDING_DIAGNOSTIC_EVERY_S per market."""
    try:
        public = auditor.public_market_view(slug, t)
    except Exception as exc:                       # noqa: BLE001
        return {"error": f"{type(exc).__name__}:{str(exc)[:80]}"}
    now = time.monotonic()
    if public.get("public_resolved") and \
            now - _pending_diag_last.get(slug, float("-inf")) >= PENDING_DIAGNOSTIC_EVERY_S:
        _pending_diag_last[slug] = now
        try:
            _log("settlement_diagnostic", **auditor.diagnose_pending(slug, t))
        except Exception as exc:                   # noqa: BLE001
            _log("settlement_diagnostic", slug=slug,
                 error=f"{type(exc).__name__}:{str(exc)[:80]}")
    return public


SETTLE_AUDIT_EVERY_S = 60.0
# OWNER 5561142758 #2: the account's own incentive economics, read-only,
# at boot and on this cadence (earnings endpoint, activity credits, trade
# fee fields, balances, published programs vs our markets).
# LIVE 2026-09-07: hourly, not 6-hourly. R3L is the first rung whose fills
# clear the 1.62-share rounding floor, so whether the venue credits a
# maker rebate is now an answerable question rather than a foregone
# $0.00 -- and a 6 h cadence would have missed tonight's fills entirely.
# Read-only GETs; it touches no cap, gate or order path.
INCENTIVE_AUDIT_EVERY_S = 3600.0


def run_incentive_audit(t, log) -> Optional[dict]:
    if t is None:
        return None
    try:
        out = incentives.incentive_audit(t)
    except Exception as exc:                          # noqa: BLE001
        out = {"ok": False, "error": str(exc)[:200]}
    log("incentive_audit", **out)
    return out


def unsettled_slugs() -> list:
    """Markets this ledger has FILLED in that carry no `settled` row yet.
    The venue drops a resolved market from positions, so a settled
    position is invisible to held_market(); the ledger remembers it."""
    path = base.q1.base.state_dir() / "ledger.jsonl"
    if not path.exists():
        return []
    filled: dict[str, int] = {}
    settled: set = set()
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict) or not row.get("slug"):
            continue
        slug, kind = str(row["slug"]), row.get("kind")
        if kind == "filled" and str(row.get("stage", "")).startswith("spring_"):
            filled[slug] = filled.get(slug, 0) + 1
        elif kind == "order_terminal":
            try:
                if float(row.get("filled_qty") or 0) > 0:
                    filled[slug] = filled.get(slug, 0) + 1
            except (TypeError, ValueError):
                pass
        elif kind in ("settled", "realized"):
            settled.add(slug)
    return [s for s in filled if s not in settled]


def ledger_slugs() -> set:
    """Every market this ledger has ever submitted to (attribution)."""
    path = base.q1.base.state_dir() / "ledger.jsonl"
    out: set = set()
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict) and row.get("kind") == "submitted" and \
                row.get("slug"):
            out.add(str(row["slug"]))
    return out


def sweep_orphan_orders(t) -> dict:
    """A redeploy can kill the campaign while a maker order rests; the
    session's deadman never runs and the order stays open on the venue,
    refusing every later preflight with open_orders:N. Cancel (exact id)
    every open order in a market THIS ledger submitted to; never touch an
    order in a market it did not -- that is a stop, reported, not a
    cancel. Cancelling is the safe direction; nothing here can place."""
    out = {"seen": 0, "cancelled": [], "foreign": [], "errors": []}
    try:
        opened = base.q1.base._get_with_retry(t, "/v1/orders/open")
    except Exception as exc:                       # noqa: BLE001
        out["errors"].append(f"list:{type(exc).__name__}")
        return out
    orders = opened.get("orders") if isinstance(opened, Mapping) else None
    ours = ledger_slugs()
    for o in orders or []:
        if not isinstance(o, Mapping):
            continue
        out["seen"] += 1
        oid = str(o.get("id") or o.get("orderId") or "")
        slug = str(o.get("marketSlug") or "")
        if slug not in ours or not oid:
            out["foreign"].append({"order_id": oid, "slug": slug})
            continue
        try:
            ok = base.q1.cancel_exact(t, oid)
            out["cancelled"].append({"order_id": oid, "slug": slug,
                                     "confirmed": bool(ok)})
            base.q1.ledger_append({"kind": "orphan_cancelled", "spring": True,
                                   "slug": slug, "order_id": oid,
                                   "confirmed": bool(ok)})
        except Exception as exc:                   # noqa: BLE001
            out["errors"].append(f"{oid}:{type(exc).__name__}")
    return out


MAINTENANCE_FILE = Path("analysis/l5/venue_maintenance_windows.json")


def load_maintenance_windows(path: Path = MAINTENANCE_FILE) -> list:
    """OWNER 5557988072 #6: declared venue maintenance intervals (read-only
    diagnostics input; never a gate, cap or policy input)."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for w in doc.get("windows") or []:
        try:
            s = datetime.fromisoformat(str(w["start_utc"]).replace("Z", "+00:00"))
            e = datetime.fromisoformat(str(w["end_utc"]).replace("Z", "+00:00"))
        except (KeyError, ValueError, TypeError):
            continue
        # OWNER 5558646019 #1: a venue may extend its own maintenance; every
        # revision is kept with its provenance, the effective declared end
        # is the latest one, the original end_utc is never edited
        revised_end, revised_by = None, None
        for rev in w.get("revisions") or []:
            try:
                r = datetime.fromisoformat(str(rev["end_utc_revised"]).replace("Z", "+00:00"))
            except (KeyError, ValueError, TypeError):
                continue
            if revised_end is None or r > revised_end:
                revised_end, revised_by = r, rev.get("revised_by")
        eff = max(e, revised_end) if revised_end else e
        out.append({"id": w.get("id"), "venue": w.get("venue"),
                    "start": s.timestamp(), "end": eff.timestamp(),
                    "start_utc": w.get("start_utc"), "end_utc": w.get("end_utc"),
                    "end_utc_revised": (revised_end.isoformat() if revised_end else None),
                    "revised_by": revised_by,
                    "declared_by": w.get("declared_by")})
    return out


def maintenance_window_at(t_utc: Optional[str], windows: Optional[list] = None,
                          slack_s: float = 120.0) -> Optional[dict]:
    """The declared window containing the instant `t_utc` (ISO), allowing
    `slack_s` before its start for the first error to land slightly ahead
    of the declared start. None = not inside any declared window."""
    if not t_utc:
        return None
    try:
        ts = datetime.fromisoformat(str(t_utc).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
    for w in (load_maintenance_windows() if windows is None else windows):
        if w["start"] - slack_s <= ts <= w["end"]:
            return w
    return None


def _ts(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def classify_outage(outage: Optional[dict], windows: Optional[list] = None,
                    now_utc: Optional[str] = None) -> Optional[dict]:
    """Tag an outage record with its class: `scheduled_maintenance` when
    its first error falls inside a declared window (planned venue outage,
    excluded from fidelity/strategy diagnostics); OWNER 5558646019 #1:
    `scheduled_maintenance_overrun` once the reference instant (the first
    healthy account read when known, else `now_utc`) is past the declared
    end -- still linked to the window and still excluded until the actual
    `venue_recovered` boundary; else `unplanned`."""
    if not outage:
        return outage
    w = maintenance_window_at(outage.get("start_utc"), windows)
    outage = dict(outage)
    if not w:
        outage["class"], outage["maintenance"] = "unplanned", None
        return outage
    recovered = outage.get("account_recovered_utc")
    ref = _ts(recovered) if recovered else _ts(now_utc)
    outage["maintenance"] = {k: w.get(k) for k in ("id", "venue", "start_utc",
                                                   "end_utc", "declared_by",
                                                   "end_utc_revised", "revised_by")}
    outage["maintenance"]["declared_end_utc"] = w.get("end_utc_revised") or w["end_utc"]
    if ref is not None and ref > w["end"]:
        outage["class"] = "scheduled_maintenance_overrun"
        outage["overrun_s"] = round(ref - w["end"], 1)
        outage["recovery_boundary"] = ("venue_recovered" if recovered
                                       else "open (no healthy account read yet)")
    else:
        outage["class"] = "scheduled_maintenance"
        # a revised (later) declared end can move a record back inside the
        # window: never carry stale overrun fields from an earlier pass
        outage.pop("overrun_s", None)
        outage["recovery_boundary"] = ("venue_recovered" if recovered
                                       else "inside declared window")
    return outage


RECOVERIES_FILE = "maintenance_recoveries.jsonl"


def record_observed_recovery(outage: Mapping[str, Any]) -> None:
    """Durable provenance of the ACTUAL recovery boundary of a planned
    outage (OWNER 5558646019 #1): appended once when public prints resume;
    read back by every later evaluator run so the overrun interval stays
    excluded after the in-memory outage record is cleared."""
    try:
        path = base.q1.base.state_dir() / RECOVERIES_FILE
        row = {k: outage.get(k) for k in ("start_utc", "first_error", "class",
                                          "account_recovered_utc",
                                          "trades_resumed_utc", "overrun_s")}
        row["maintenance_id"] = (outage.get("maintenance") or {}).get("id")
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
    except Exception:                                    # noqa: BLE001
        pass


def load_observed_recoveries() -> list:
    try:
        path = base.q1.base.state_dir() / RECOVERIES_FILE
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
    except Exception:                                    # noqa: BLE001
        return []


def effective_maintenance_windows(windows: list, outage: Optional[Mapping[str, Any]],
                                  now_utc: Optional[str],
                                  recoveries: Optional[list] = None) -> list:
    """Declared windows extended to their ACTUAL recovery boundary: the
    first healthy account read of an outage that started inside the window
    (from the live outage record or the durable recoveries file), or
    open-ended at `now_utc` while the venue has not recovered."""
    recs = list(recoveries if recoveries is not None else load_observed_recoveries())
    if outage:
        recs.append(dict(outage))
    out = []
    for w in windows:
        w2 = dict(w)
        best = None
        for r in recs:
            st = _ts(r.get("start_utc"))
            if st is None or not (w["start"] - 120.0 <= st <= w["end"]):
                continue
            rec_utc = r.get("account_recovered_utc")
            ref = _ts(rec_utc) if rec_utc else _ts(now_utc)
            if ref is not None and ref > w["end"] and (best is None or ref > best[0]):
                best = (ref, rec_utc)
        if best:
            w2["end"] = best[0]
            w2["effective_end_utc"] = best[1] or now_utc
            w2["overrun_s"] = round(best[0] - w["end"], 1)
            w2["boundary"] = ("venue_recovered" if best[1]
                              else "open (no healthy account read yet)")
        else:
            w2["effective_end_utc"], w2["boundary"] = w["end_utc"], "declared_end"
        out.append(w2)
    return out


def _utc(wall) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(float(wall()), tz=timezone.utc).isoformat()


def liveness_sample(t, slugs) -> dict:
    """OWNER 5554127702 #1: public, read-only `sharesTraded` per market
    from the venue's own market data -- the signal that trades are
    printing again. None when unreadable; never raises."""
    out: dict = {}
    for slug in list(dict.fromkeys(s for s in slugs if s))[:6]:
        try:
            md = t.request("GET", f"/v1/markets/{slug}/bbo",
                           base=base.q1.GATEWAY)
            md = md.get("marketData") if isinstance(md, Mapping) else None
            raw = (md or {}).get("sharesTraded")
            out[slug] = float(raw) if raw not in (None, "") else None
        except Exception:                          # noqa: BLE001
            out[slug] = None
    return out


def liveness_slugs() -> list:
    """Markets to watch for prints: the ledger's unsettled (held) markets
    plus the deepest rows of the recorder's latest status."""
    slugs = list(unsettled_slugs())
    try:
        status, _ = entry.load_status_document(None)
        rows = [r for r in (status or {}).get("spring_watch") or []
                if isinstance(r, Mapping) and r.get("slug")]
        rows.sort(key=lambda r: -float(r.get("bid_touch_qty") or 0))
        slugs += [str(r["slug"]) for r in rows[:3]]
    except Exception:                              # noqa: BLE001
        pass
    return slugs


def status_has_trades() -> bool:
    """The recorder's own tape: any watch row with trades in the hour."""
    try:
        status, _ = entry.load_status_document(None)
        return any(int(r.get("trades_1h") or 0) > 0
                   for r in (status or {}).get("spring_watch") or []
                   if isinstance(r, Mapping))
    except Exception:                              # noqa: BLE001
        return False


CAMPAIGN_STATE_FILE = "l5_campaign_state.json"   # NOT the q1 campaign_state.json


def campaign_state_path() -> Path:
    return base.q1.base.state_dir() / CAMPAIGN_STATE_FILE


def load_campaign_state() -> dict:
    """OWNER 5554553793 A: the outage baseline, the recovery gate, the
    recovery-burst milestones already reported, and the rung's filled /
    settled markets survive a restart or redeploy. Missing or unreadable
    state reads as empty (fail closed: the boot gate below still runs)."""
    try:
        raw = json.loads(campaign_state_path().read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:                              # noqa: BLE001
        return {}


def save_campaign_state(state: Mapping[str, Any]) -> bool:
    """Atomic write on the durable mount; never raises."""
    try:
        path = campaign_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, default=str, sort_keys=True),
                       encoding="utf-8")
        tmp.replace(path)
        return True
    except Exception:                              # noqa: BLE001
        return False


def load_campaign_approvals(path: Optional[Path] = None) -> list:
    """OWNER campaign approvals from the repo file; unreadable = none."""
    try:
        raw = json.loads((path or CAMPAIGN_APPROVALS_FILE).read_text(encoding="utf-8"))
        return [dict(a) for a in (raw.get("approvals") or [])
                if isinstance(a, Mapping) and a.get("campaign_id")]
    except Exception:                              # noqa: BLE001
        return []


def approved_continuation(current_id: str, previous_ids: list,
                          approvals: Optional[list] = None) -> Optional[dict]:
    """The first owner approval this durable state has not run yet, if
    any. An approval is consumed by running it (its id is persisted as
    the campaign id and later in previous_campaigns), so a redeploy can
    never re-run one."""
    done = {current_id, *previous_ids}
    for a in (load_campaign_approvals() if approvals is None else approvals):
        if a["campaign_id"] in done:
            continue
        if a.get("stop_rule") not in (STOP_RULE_RUNG, STOP_RULE_EVIDENCE):
            continue
        return dict(a)
    return None


def evidence_minimums_met() -> tuple:
    """(met, view) from the frozen gate's latest evidence_progress: every
    LIVE minimum met. No gate reading yet = not met (fail closed)."""
    mins = _fidelity_last.get("minimums") or {}
    view = {k: {kk: (mins.get(k) or {}).get(kk) for kk in ("have", "need", "met")}
            for k in EVIDENCE_MINIMUM_KEYS}
    met = bool(mins) and all(bool((mins.get(k) or {}).get("met"))
                             for k in EVIDENCE_MINIMUM_KEYS)
    return met, view


def deployment_identity() -> dict:
    """The exact active deployment / commit, from the platform's own
    environment (Railway injects both); never inferred from the repo."""
    return {"commit": os.getenv("RAILWAY_GIT_COMMIT_SHA") or None,
            "deployment_id": os.getenv("RAILWAY_DEPLOYMENT_ID") or None,
            "service": os.getenv("RAILWAY_SERVICE_NAME") or None}


def continuation_preflight(t, filled: set, settled: set,
                           rung: Optional[Mapping[str, Any]] = None) -> dict:
    """OWNER preconditions for an evidence continuation, all from the
    venue / the durable ledger, none inferred: every filled market of the
    previous campaign settled with realized_confirmed (POSITION_RESOLUTION,
    idempotent re-audit), zero open orders (the orphan sweep sees none),
    positions flat, and the ledger carrying no filled-but-unsettled
    market. Any failure blocks the start; the caller re-checks later.

    OWNER 5561465636 #2 (a SIZED rung, i.e. one stating a per-action
    target): additionally fail closed unless the venue's buying power
    covers the rung's whole per-campaign worst case, every settled
    market's ledger quantity equals the venue's, and the exact active
    deployment / commit is known; the cancel path is the orphan sweep
    having run without error."""
    checks: dict[str, Any] = {}
    sized = bool(rung and rung.get("target_notional_usd"))
    unsettled = sorted(set(filled) - set(settled))
    checks["all_filled_settled"] = {"ok": not unsettled, "filled": sorted(filled),
                                    "unsettled": unsettled}
    real: dict[str, Any] = {}
    for slug in sorted(filled):
        audit = settle_held(t, slug)
        real[slug] = {"status": audit.get("status"),
                      "settled": bool(audit.get("settled")),
                      "settled_by": audit.get("settled_by"),
                      "realized_confirmed": bool(audit.get("realized_confirmed")),
                      "realized_usd": audit.get("realized_usd"),
                      "venue_qty_total": audit.get("venue_qty_total"),
                      "ledger_qty_total": audit.get("ledger_qty_total")}
    # LIVE 15:46Z (OWNER P0 5560183912): the venue confirms settlement
    # either by a POSITION_RESOLUTION activity or by having carried no
    # position at a public resolution; for the latter it publishes NO
    # realized number (the trades carry none), so realized stays
    # unconfirmed and is REPORTED, never blocking: no position, no order,
    # no unsettled market is the exposure safety, and it is unchanged.
    checks["venue_settled"] = {
        "ok": bool(real) and all(v["settled"] for v in real.values()),
        "markets": real,
        "realized_unconfirmed": sorted(s for s, v in real.items()
                                       if not v["realized_confirmed"]),
        "ledger_vs_venue_qty_mismatch": sorted(
            s for s, v in real.items()
            if v["venue_qty_total"] is not None and v["ledger_qty_total"] is not None
            and D(str(v["venue_qty_total"])) != D(str(v["ledger_qty_total"])))}
    sweep = sweep_orphan_orders(t)
    checks["open_orders"] = {"ok": sweep["seen"] == 0 and not sweep["errors"]
                             and not sweep["foreign"], "sweep": sweep}
    held = held_market(t)
    checks["positions_flat"] = {"ok": held is None, "held": held}
    ledger_unsettled = unsettled_slugs()
    checks["ledger_unsettled"] = {"ok": not ledger_unsettled,
                                  "unsettled": ledger_unsettled}
    # OWNER 5561465636 #2: the sized-rung checks; recorded for every
    # rung, blocking only for a sized one
    mismatch = checks["venue_settled"]["ledger_vs_venue_qty_mismatch"]
    checks["ledger_reconciled"] = {"ok": (not mismatch) or not sized,
                                   "mismatch": mismatch, "blocking": sized}
    checks["cancel_path"] = {"ok": (not sweep["errors"]) or not sized,
                             "orphan_sweep_errors": sweep["errors"],
                             "blocking": sized}
    worst = ladder.worst_case(rung) if rung else None
    need = D(str(worst["per_campaign_usd"])) if worst else D("0")
    bal = incentives.balances(t) if t is not None else {"ok": False, "error": "no transport"}
    power = None
    try:
        rows = ((bal.get("raw") or {}).get("balances") or []) if bal.get("ok") else []
        if rows and rows[0].get("buyingPower") is not None:
            power = D(str(rows[0]["buyingPower"]))
    except Exception:                               # noqa: BLE001
        power = None
    checks["buying_power"] = {"ok": (power is not None and power >= need) or not sized,
                              "buying_power_usd": (str(power) if power is not None else None),
                              "required_per_campaign_worst_case_usd": str(need),
                              "blocking": sized,
                              "error": None if bal.get("ok") else bal.get("error")}
    ident = deployment_identity()
    checks["deployment_identity"] = {"ok": bool(ident["commit"] and ident["deployment_id"])
                                     or not sized, **ident, "blocking": sized}
    checks["rung"] = {"ok": True, "id": (rung or {}).get("id"), "sized": sized,
                      "worst_case": worst}
    failed = [k for k, v in checks.items() if not v["ok"]]
    return {"ok": not failed, "failed": failed, "checks": checks}


def burst_report(report: Mapping[str, Any], funnel: Mapping[str, Any],
                 burst: Mapping[str, Any], minutes: int) -> dict:
    """OWNER 5554127702 #5: what happened in the first N minutes after
    the venue resumed -- eligible/selected/decisions/submitted/rested/
    filled/cancelled, shadow vs executed, inventory trajectory, realized
    settlements, and the exact refusal reasons, all as deltas from the
    moment of recovery."""
    f0 = burst["funnel0"]
    sessions = (report.get("sessions") or [])[burst["sessions0"]:]
    orders = [o for s in sessions for o in (s.get("orders") or [])]
    decisions = [d for s in sessions for d in (s.get("decisions") or [])]
    filled = [o for o in orders if str(o.get("outcome")) == "filled"]
    rested = [o for o in orders if str(o.get("end_reason")) == "deadline"]
    rejected = [o for o in orders
                if str(o.get("end_reason", "")).startswith("rejected")]
    refusals = {k: int(v) - int(f0.get("refusals", {}).get(k, 0))
                for k, v in (funnel.get("refusals") or {}).items()}
    settles = (report.get("settlements") or [])[burst["settlements0"]:]
    return {
        "minutes": minutes,
        "eligible_admitted": int(funnel.get("admitted", 0)) - int(f0.get("admitted", 0)),
        "universe_rows": int(funnel.get("universe_rows", 0)) - int(f0.get("universe_rows", 0)),
        "selected_markets": sorted({str(s.get("slug")) for s in sessions if s.get("slug")}),
        "sessions": len(sessions),
        "decisions": len(decisions),
        "decisions_by_action": {a: sum(1 for d in decisions if d.get("acted") == a)
                                for a in ("open", "extend", "complete", "hold")},
        "shadow_differs_from_executed": sum(
            1 for d in decisions if d.get("shadow_differs_from_executed")),
        "submitted": len(orders), "filled": len(filled),
        "rested_to_deadline": len(rested), "rejected": len(rejected),
        "cancelled": len(orders) - len(filled),
        "inventory_trajectory": [{"slug": s.get("slug"),
                                  "inventory": s.get("inventory")}
                                 for s in sessions if s.get("inventory")],
        "settlements": [{"slug": s.get("slug"), "status": s.get("status"),
                         "realized_usd": s.get("realized_usd")} for s in settles],
        "refusal_reasons_delta": {k: v for k, v in refusals.items() if v},
    }


def _sum_realized(report: Mapping[str, Any]) -> Optional[str]:
    """Campaign realized = the sum of VENUE-confirmed realized amounts
    over this campaign's settlements; None until the first one."""
    from decimal import Decimal
    vals = [s.get("realized_usd") for s in (report.get("settlements") or [])
            if s.get("status") == "REALIZED" and s.get("realized_usd")]
    if not vals:
        return None
    return str(sum(Decimal(str(v)) for v in vals))


def _has_inventory(report: Mapping[str, Any]) -> bool:
    inv = report.get("inventory") or {}
    try:
        return (float(inv.get("up") or 0) + float(inv.get("dn") or 0)) > 0
    except (TypeError, ValueError):
        return bool(report.get("actions"))


def run_campaign(t=None, clock=time.monotonic, sleep=time.sleep,
                 wall=time.time, campaign_s: Optional[float] = None,
                 session_runner=None) -> dict:
    """The loop. DISARMED -> shadow selection only; ARMED -> the canonical
    session on the selected market, reselecting after any session that
    ends without inventory."""
    if campaign_s is None:
        campaign_s = float(os.getenv(CAMPAIGN_SECONDS_ENV,
                                     str(DEFAULT_CAMPAIGN_S)))
    session_runner = session_runner or base.run_session
    armed = base.armed()
    try:
        model = pv2.load_v2()
    except Exception as exc:      # noqa: BLE001 - reported, never guessed
        model = None
        _log("model_unavailable", err=f"{type(exc).__name__}")
    report: dict[str, Any] = {
        "campaign": "pmus-l5-continuous-v1",
        "mode": "ARMED" if armed else "SHADOW",
        "realized_pnl": None,
        "sessions": [], "selections": 0,
        "labels": ("continuous select -> sequential L5 session -> reselect; "
                   "one market at a time; residual HOLD to settlement; "
                   "submitted->filled->settled->realized"),
    }
    if not armed:
        report["note"] = ("isolated PMUS_SPRING_* dual key absent/mismatched; "
                          "shadow selection only, no venue network")
    else:
        if not base.q1.has_credentials():
            return {**report, "status": "NO_CREDENTIALS"}
        reason = entry.durable_state_reason()
        if reason:
            return {**report, "status": "DURABLE_STATE_REQUIRED",
                    "reason": reason}
        # the real transport only when the real session runs; a scripted
        # session runner (tests) gets whatever it was handed
        if t is None and session_runner is base.run_session:
            t = base.Transport()
        if t is not None:
            sweep = sweep_orphan_orders(t)
            _log("orphan_sweep", **sweep)
            report["orphan_sweep"] = sweep
            if sweep["foreign"]:
                # an open order this strategy did not place: not ours to
                # cancel, and not a state to trade into -- stop, report
                return {**report, "status": "CAMPAIGN_STOP:FOREIGN_OPEN_ORDERS"}
    start = clock()
    stop_at = start + campaign_s
    cooldown: dict[str, float] = {}
    sessions_on: dict[str, int] = {}
    status = "CAMPAIGN_NO_CANDIDATE"
    resumes = 0
    last_settle_audit = float("-inf")
    last_incentive_audit = float("-inf")
    settled_here: set = set()
    iteration_errors: list = []          # clock() stamps of recent errors
    # OWNER 5554127702 #4: the prebuilt ladder names the active rung
    # (PMUS_SPRING_RUNG, default R1 = the authorized 3-market rung). Only
    # the THROUGHPUT levers are wired here (markets per campaign, actions
    # per session); the per-market cap and concurrency stay the session's
    # hard constants until the owner changes them by decision.
    active_rung = ladder.set_active(None)        # the env rung (default R1)
    rung_markets = max(1, int(os.getenv(
        RUNG_MARKETS_ENV, str(active_rung["markets_per_campaign"]))))
    os.environ.setdefault(base.MAX_ACTIONS_ENV,
                          str(active_rung["max_actions_per_session"]))

    def _apply_rung(rung_id: Optional[str]) -> dict:
        """OWNER 5560851070: the caps follow the campaign's rung -- from
        durable state at boot, from the consumed approval at the gate.
        Sets the ACTIVE rung for the session/executor cap readers and the
        rung-level throughput levers; caps outside the ladder never
        apply (unknown id -> the authorized rung)."""
        nonlocal active_rung, rung_markets
        active_rung = ladder.set_active(rung_id)
        rung_markets = max(1, int(os.getenv(
            RUNG_MARKETS_ENV, str(active_rung["markets_per_campaign"]))))
        os.environ[base.MAX_ACTIONS_ENV] = str(active_rung["max_actions_per_session"])
        return active_rung
    filled_markets: set = set()          # markets with a fill this campaign
    # OWNER evidence continuation: the campaign identity and its stop rule
    # are durable state; an approval is consumed at the advance gate
    campaign_id = INITIAL_CAMPAIGN_ID
    stop_rule = STOP_RULE_RUNG
    previous_campaigns: list = []
    sessions_base = 0                    # sessions before this campaign began

    def _rung_report() -> dict:
        return {
            "campaign_id": campaign_id,
            "stop_rule": stop_rule,
            "markets_allowed": (rung_markets if stop_rule == STOP_RULE_RUNG
                                else "until the frozen LIVE evidence minimums are met"),
            "per_market_cap_usd": str(base.session_notional_cap()),
            "residual_cap_units": base.max_residual_units(),
            # OWNER 5567269295: the R4 levers, from the rung, never literals
            "max_unmatched_usd": (str(base.max_unmatched_usd())
                                  if base.max_unmatched_usd() is not None else None),
            "pair_cost_max": str(base.pair_cost_max()),
            "safety_shell_version": base.safety_shell_version(),
            "worst_case_usd": (str(base.session_notional_cap() * rung_markets)
                               if stop_rule == STOP_RULE_RUNG else
                               f"{base.session_notional_cap()} per filled market, "
                               f"bounded by the daily-loss stop"),
            "daily_loss_stop_usd": str(base.daily_loss_stop()),
            "per_action_target_usd": str(ladder.target_notional()),
            "per_leg_max_usd": str(ladder.max_leg_notional()),
            # the rung's own value, never a literal: the loop enforces
            # active_rung["concurrency"], so the evidence record must say
            # the same number (LIVE 23:32Z: this read 1 under R2C2/conc2)
            "concurrency": int(active_rung.get("concurrency") or 1),
            "ladder_rung": active_rung["id"],
            "deployment": deployment_identity(),
            "max_actions_per_session": os.environ.get(base.MAX_ACTIONS_ENV),
            "previous_campaigns": [p.get("campaign_id") for p in previous_campaigns],
        }
    report["rung"] = _rung_report()
    report["ramp_ladder"] = ladder.ladder()
    # OWNER 5554127702 #1/#5: outage bookkeeping for the postmortem, the
    # recovery gate (account reads healthy AND public prints moving), and
    # the recovery-burst milestones
    outage: Optional[dict] = None
    liveness_baseline: dict = {}
    recovery_gate: Optional[dict] = None
    burst: Optional[dict] = None
    gate_logs = 0
    # OWNER 5554553793 A: durable campaign state. A restart/redeploy in
    # the middle of an outage keeps the print baseline, the pending gate,
    # the rung's filled/settled markets and the burst milestones already
    # reported (no double count). Burst timing is kept in WALL time so a
    # restart cannot reset the 30/60/120-minute clock.
    persisted = load_campaign_state() if armed and t is not None else {}
    if persisted:
        filled_markets |= set(persisted.get("filled_markets") or [])
        settled_here |= set(persisted.get("settled_here") or [])
        for s_ in settled_here:
            cooldown[s_] = float("inf")
        campaign_id = str(persisted.get("campaign_id") or INITIAL_CAMPAIGN_ID)
        stop_rule = str(persisted.get("stop_rule") or STOP_RULE_RUNG)
        if stop_rule not in (STOP_RULE_RUNG, STOP_RULE_EVIDENCE):
            stop_rule = STOP_RULE_RUNG
        if persisted.get("rung"):
            # the campaign's own rung survives a restart: a held R1 market
            # keeps R1 caps; a campaign started under an owner rung keeps it
            _apply_rung(str(persisted["rung"]))
        previous_campaigns = [dict(p) for p in (persisted.get("previous_campaigns") or [])
                              if isinstance(p, Mapping)]
        for p in previous_campaigns:
            # a market of an earlier campaign is never re-entered
            for s_ in (p.get("filled_markets") or []) + (p.get("settled_here") or []):
                cooldown[str(s_)] = float("inf")
        report["rung"] = _rung_report()
        if persisted.get("outage"):
            # re-classified on restore: state saved by an older build (or
            # before a window was declared) carries no class
            outage = classify_outage(dict(persisted["outage"]))
            outage["start_clock"] = clock() - float(
                outage.get("elapsed_s") or 0.0)
            liveness_baseline = dict(persisted.get("liveness_baseline") or {})
            if persisted.get("recovery_gate"):
                recovery_gate = dict(persisted["recovery_gate"])
            else:
                iteration_errors.append(outage["start_clock"])
        if persisted.get("burst"):
            burst = dict(persisted["burst"])
            burst.update({"restarted": True, "funnel0": {}, "sessions0": 0,
                          "settlements0": 0})
        report["restored_state"] = {
            k: (classify_outage(persisted.get(k)) if k == "outage"
                else persisted.get(k))
            for k in ("saved_utc", "outage", "recovery_gate", "burst",
                      "filled_markets", "settled_here")}
        _log("campaign_state_restored", **report["restored_state"])
    elif armed and t is not None and not status_has_trades():
        # BOOT GATE: no persisted outage but the recorder tape shows no
        # trades in the hour -- treat the boot as inside an outage: sample
        # the print baseline now and wait for prints before any session
        # (account endpoints healthy before tape -> no trade).
        liveness_baseline = liveness_sample(t, liveness_slugs())
        outage = classify_outage(
            {"start_clock": clock(), "start_utc": _utc(wall),
             "first_error": "boot_without_public_prints",
             "account_recovered_utc": _utc(wall)})
        recovery_gate = {"pending": True, "checks": 0, "boot": True}
        _log("boot_gate_waiting", liveness_baseline=liveness_baseline)

    def _persist() -> None:
        if not (armed and t is not None):
            return
        snap = {
            "saved_utc": _utc(wall), "rung": active_rung["id"],
            "campaign_id": campaign_id, "stop_rule": stop_rule,
            "previous_campaigns": previous_campaigns,
            "filled_markets": sorted(filled_markets),
            "settled_here": sorted(settled_here),
            "outage": ({**{k: v for k, v in classify_outage(
                               outage, now_utc=_utc(wall)).items()
                           if k != "start_clock"},
                        "elapsed_s": round(clock() - outage["start_clock"], 1)}
                       if outage else None),
            "liveness_baseline": liveness_baseline,
            "recovery_gate": recovery_gate,
            "burst": burst,
        }
        save_campaign_state(snap)
        _outage_state["outage"] = (classify_outage(outage, now_utc=_utc(wall))
                                   if outage else None)
    _persist()

    # OWNER live decision 2026-09-06 (uptime): round-robin cursor over the
    # markets held at once under a concurrency>1 rung, and the alternation
    # that keeps a held market worked while capacity is free for another
    _hold_rr = {"i": 0}

    held_all: list = []          # markets held right now; [] while disarmed

    def _next_held(slugs: list) -> Optional[str]:
        if not slugs:
            return None
        _hold_rr["i"] = (_hold_rr["i"] + 1) % len(slugs)
        return slugs[_hold_rr["i"]]

    def _allowance_spent() -> bool:
        """rung_markets: fills on markets_allowed distinct markets this
        campaign. frozen_evidence_minimums: the frozen gate's latest
        reading says every LIVE minimum is met (fail closed until it has
        read once)."""
        if stop_rule == STOP_RULE_EVIDENCE:
            return evidence_minimums_met()[0]
        return len(filled_markets) >= rung_markets

    def _rung_gate() -> Optional[str]:
        """After the rung's markets are all flat/settled: stop at the
        ADVANCE GATE with what the next rung decision needs."""
        if not _allowance_spent():
            return None
        if any(s not in settled_here for s in filled_markets):
            return None                       # something still held
        clean = {
            "orphan_cancels": len((report.get("orphan_sweep") or {})
                                  .get("cancelled") or []),
            "unresolved_reconcile": sum(
                int((s.get("reconcile") or {}).get("unresolved") or 0)
                for s in report["sessions"]),
            "inventory_mismatches": sum(
                1 for s in report["sessions"]
                if str(s.get("reason") or "").startswith("inventory_mismatch")),
            "halts": sum(1 for s in report["sessions"]
                         if s.get("status") == "HALT"),
            "settlements": len(report.get("settlements") or []),
            "realized_usd": _sum_realized(report),
        }
        clean["all_clean"] = (clean["orphan_cancels"] == 0
                              and clean["unresolved_reconcile"] == 0
                              and clean["inventory_mismatches"] == 0
                              and clean["halts"] == 0)
        report["advance_gate"] = {"filled_markets": sorted(filled_markets),
                                  **clean}
        _log("advance_gate", **report["advance_gate"])
        # the ladder's verdict on THIS rung from venue-confirmed evidence;
        # informational -- promotion is the owner's env change
        report["ramp_evaluation"] = ladder.evaluate(
            active_rung["id"], ladder.evidence_from_campaign(report))
        _log("ramp_evaluation", **report["ramp_evaluation"])
        return "CAMPAIGN_RUNG_COMPLETE"

    def _after_gate(gate: str) -> bool:
        """The advance gate fired. Without an unconsumed owner approval the
        campaign ends here (True = break). With one, run the continuation
        preflight: on failure wait (re-checked on the next audit, never a
        start); on success archive the completed campaign, consume the
        approval, and continue selecting under its stop rule."""
        nonlocal status, campaign_id, stop_rule, previous_campaigns
        nonlocal stop_at, sessions_base
        if gate != "CAMPAIGN_RUNG_COMPLETE":
            status = gate
            return True
        appr = approved_continuation(campaign_id,
                                     [p.get("campaign_id") for p in previous_campaigns])
        if not appr:
            status = gate
            return True
        pre = continuation_preflight(
            t, filled_markets, settled_here,
            rung=ladder.rung(str(appr["rung"])) if appr.get("rung") else None)
        if not pre["ok"]:
            if status != "CAMPAIGN_WAITING:CONTINUATION_PREFLIGHT":
                _log("campaign_continuation_blocked", approval=appr["campaign_id"],
                     failed=pre["failed"], checks=pre["checks"])
            status = "CAMPAIGN_WAITING:CONTINUATION_PREFLIGHT"
            return False
        previous_campaigns.append({
            "campaign_id": campaign_id, "stop_rule": stop_rule,
            "filled_markets": sorted(filled_markets),
            "settled_here": sorted(settled_here),
            "realized_usd": _sum_realized(report),
            "advance_gate": report.get("advance_gate"),
            "ended_utc": _utc(wall)})
        for s_ in filled_markets | settled_here:
            cooldown[s_] = float("inf")
        filled_markets.clear()
        settled_here.clear()
        campaign_id = str(appr["campaign_id"])
        stop_rule = str(appr.get("stop_rule") or STOP_RULE_RUNG)
        if appr.get("rung"):
            # OWNER 5560851070: the approval names the rung the new
            # campaign runs under (caps, residual units, markets); the
            # shell label on every decision row changes with it
            _apply_rung(str(appr["rung"]))
        stop_at = clock() + campaign_s
        sessions_base = len(report["sessions"])
        report["rung"] = _rung_report()
        report.setdefault("continuations", []).append(
            {"campaign_id": campaign_id, "approval": appr, "preflight": pre,
             "started_utc": _utc(wall)})
        _persist()
        fidelity_eval(wall(), force=True)
        met, mins = evidence_minimums_met()
        _log("campaign_continuation_start", campaign_id=campaign_id,
             stop_rule=stop_rule, approval=appr, preflight=pre,
             rung=report["rung"], evidence_minimums=mins,
             evidence_minimums_met=met,
             previous_campaign=previous_campaigns[-1])
        status = f"CAMPAIGN_CONTINUATION:{campaign_id}"
        return False
    # OWNER 5552841170 #4: throughput funnel, accumulated for the campaign
    # and printed per selection cycle
    funnel: dict[str, Any] = {"cycles": 0, "universe_rows": 0,
                              "in_distribution": 0, "admitted": 0,
                              "sessions": 0, "submitted": 0, "filled": 0,
                              "refusals": {},
                              # OWNER (admitted -> session is P0): every
                              # cycle that admitted a market and started
                              # no session is counted by REASON CODE, and
                              # every resume iteration that skipped
                              # selection because a market is held is
                              # counted too, so the admitted -> session
                              # drop is never anonymous
                              "not_started": {},
                              "selection_skipped_position_held": 0,
                              "selection_skipped_rung_reached": 0}
    report["funnel"] = funnel

    def _not_started(reason: str) -> None:
        funnel["not_started"][reason] = funnel["not_started"].get(reason, 0) + 1
    _log("campaign_start", mode=report["mode"], campaign_s=campaign_s,
         campaign_expires_at=wall() + campaign_s,
         max_sessions=MAX_SESSIONS_PER_CAMPAIGN,
         per_market_cap=str(base.SESSION_NOTIONAL_CAP),
         rung=report["rung"])
    _log("ramp_ladder", active=active_rung["id"],
         rungs=[{"id": r["id"], "lever": r["lever"],
                 "worst_case": r["worst_case"]} for r in report["ramp_ladder"]])
    # OWNER 5557786528 #7: reconstruct the evidence from the immutable raw
    # ledger at boot (read-only) so the corrected UNIQUE minimums are
    # published even while the venue is unavailable; re-run on the frozen
    # cadence below and forced after every session that adds evidence
    fidelity_eval(wall(), force=True)
    while clock() < stop_at and len(report["sessions"]) - sessions_base < \
            MAX_SESSIONS_PER_CAMPAIGN + resumes:
        try:
            now = wall()
            fidelity_eval(now)
            if armed:
                # OWNER 5552607804: a market we already hold is CONTINUED
                # (EXTEND / COMPLEMENT / HOLD from the model on the real
                # inventory). Under a concurrency-1 rung no other market is
                # started while it exists; OWNER live decision 2026-09-06
                # (uptime) allows the rung's concurrency to be higher, in
                # which case the campaign holds up to that many markets and
                # works them ONE SESSION AT A TIME, round-robin -- never two
                # sessions at once, never two outstanding orders.
                nonlocal_held = [s for s in held_markets(t)
                                 if s not in settled_here]
                held_all[:] = nonlocal_held
                concurrency = max(1, int(active_rung.get("concurrency") or 1))
                # the sibling positions the session layer must not treat as
                # a foreign block; empty under every concurrency-1 rung
                base.set_concurrent_owned(held_all if concurrency > 1 else ())
                capacity = (len(held_all) < concurrency
                            and not _allowance_spent())
                # with capacity free, alternate: one iteration looks for a
                # new market, the next works a held one, so neither starves
                open_cycle = capacity and (_hold_rr["i"] % 2 == 0)
                if capacity:
                    _hold_rr["i"] += 1
                held = None if (open_cycle or not held_all) \
                    else _next_held(held_all)
                if iteration_errors:
                    # OWNER 5553997094 #2: the first successful account read
                    # after an outage is REPORTED, and open orders are
                    # reconciled again BEFORE any submission -- the boot
                    # sweep may itself have failed inside the outage.
                    outage_s = round(clock() - iteration_errors[0], 1)
                    n_err = len(iteration_errors)
                    iteration_errors.clear()
                    sweep = sweep_orphan_orders(t)
                    report.setdefault("recoveries", []).append(
                        {"outage_s": outage_s, "errors": n_err,
                         "held": held, "sweep": sweep})
                    _log("venue_recovered", outage_s=outage_s,
                         error_count=n_err, held=held, sweep=sweep,
                         outage_class=(classify_outage(outage, now_utc=_utc(wall)) or {}).get("class"),
                         maintenance=(classify_outage(outage, now_utc=_utc(wall)) or {}).get("maintenance"),
                         recovery_boundary_utc=_utc(wall))
                    if sweep["foreign"]:
                        status = "CAMPAIGN_STOP:FOREIGN_OPEN_ORDERS"
                        break
                    if outage is None:
                        outage = {"start_clock": clock() - outage_s,
                                  "start_utc": None}
                    outage["account_recovered_utc"] = _utc(wall)
                    outage["errors"] = n_err
                    outage["sweep"] = sweep
                    # OWNER 5554127702 #1: account reads healthy is not
                    # enough -- public prints must move again before any
                    # session runs; until then every iteration waits here
                    recovery_gate = {"pending": True, "checks": 0}
                    _persist()
                if recovery_gate and recovery_gate.get("pending"):
                    live = liveness_sample(t, list(liveness_baseline)
                                           or liveness_slugs())
                    moved = [s for s, v in live.items()
                             if v is not None
                             and liveness_baseline.get(s) is not None
                             and v > liveness_baseline[s]]
                    tape = status_has_trades()
                    recovery_gate["checks"] += 1
                    if moved or tape:
                        recovery_gate["pending"] = False
                        outage = classify_outage(outage, now_utc=_utc(wall))
                        outage["trades_resumed_utc"] = _utc(wall)
                        if outage.get("maintenance"):
                            record_observed_recovery(outage)
                        outage["trades_resumed_by"] = (
                            {"markets_moved": moved} if moved
                            else {"recorder_tape": True})
                        report.setdefault("outages", []).append(outage)
                        _log("venue_trading_resumed", **outage)
                        burst = {"start_wall": float(wall()),
                                 "funnel0": json.loads(json.dumps(funnel)),
                                 "sessions0": len(report["sessions"]),
                                 "settlements0": len(report.get("settlements") or []),
                                 "milestones": [30, 60, 120], "done": []}
                        outage, liveness_baseline = None, {}
                        recovery_gate = None
                        _persist()
                    else:
                        gate_logs += 1
                        if gate_logs % 4 == 1:
                            _log("recovery_gate_waiting",
                                 checks=recovery_gate["checks"],
                                 baseline=liveness_baseline, now=live,
                                 recorder_tape=tape)
                        status = "CAMPAIGN_WAITING:VENUE_PRINTS"
                        if recovery_gate["checks"] % 4 == 1:
                            _persist()
                        sleep(SELECT_POLL_S)
                        continue
                if burst:
                    elapsed_min = (float(wall()) - burst["start_wall"]) / 60.0
                    for m in burst["milestones"]:
                        if m not in burst["done"] and elapsed_min >= m:
                            burst["done"].append(m)
                            rb = burst_report(report, funnel, burst, m)
                            if burst.get("restarted"):
                                rb["labels"] = ("after a restart: deltas count "
                                                "only this process's sessions")
                            report.setdefault("recovery_bursts", []).append(rb)
                            _log("recovery_burst", **rb)
                            _persist()
                    if len(burst["done"]) == len(burst["milestones"]):
                        burst = None
                        _persist()
                if len(held_all) > concurrency:
                    # More open positions than the rung authorises. A
                    # position the campaign did NOT open is a genuine
                    # breach and stops everything. Positions it DID open
                    # are simply over the cap because the rung tightened
                    # under them (OWNER DECISION 5564173746 cut R3L back
                    # to concurrency 1 mid-campaign): halting there would
                    # strand real inventory and delay the running
                    # campaign, so they are DRAINED instead -- held to
                    # settlement under the unchanged rules, with no new
                    # market opened, because `capacity` is already False.
                    foreign = [s for s in held_all if s not in filled_markets]
                    if over_concurrency_action(held_all, filled_markets,
                                               concurrency) == "halt":
                        status = f"CAMPAIGN_STOP:{MULTI_HELD}"
                        _log("concurrency_breach", held=held_all, foreign=foreign,
                             concurrency=concurrency, rung=active_rung["id"])
                        break
                    if status != "CAMPAIGN_DRAIN:OVER_CONCURRENCY":
                        _log("concurrency_drain", held=held_all,
                             concurrency=concurrency, rung=active_rung["id"],
                             reading="rung tightened under open inventory; "
                                     "holding every market to settlement and "
                                     "opening none")
                    status = "CAMPAIGN_DRAIN:OVER_CONCURRENCY"
                if t is not None and clock() - last_incentive_audit >= INCENTIVE_AUDIT_EVERY_S:
                    last_incentive_audit = clock()
                    report["incentive_audit"] = run_incentive_audit(t, _log)
                if not held and t is not None and \
                        clock() - last_settle_audit >= SETTLE_AUDIT_EVERY_S:
                    # flat, but the ledger remembers fills the venue has since
                    # resolved out of positions: record their authoritative
                    # settlement (realized only from the venue's own number)
                    last_settle_audit = clock()
                    for slug in unsettled_slugs()[:5]:
                        audit = settle_held(t, slug)
                        _log("settlement", slug=slug, **audit)
                        if audit.get("settled"):
                            settled_here.add(slug)
                            filled_markets.add(slug)
                            cooldown[slug] = float("inf")   # never re-enter
                            _persist()
                        report.setdefault("settlements", []).append(
                            {"slug": slug, **audit})
                        if audit.get("status") == "REALIZED":
                            _log("REALIZED", slug=slug,
                                 realized_usd=audit.get("realized_usd"),
                                 resolution=audit.get("resolution"))
                            report["realized_pnl"] = _sum_realized(report)
                    # LIVE 12:31Z: the ATP residual settled on THIS path
                    # (periodic audit, market already out of positions)
                    # and the rung gate was only consulted on the
                    # market-gone path, so the campaign went straight to
                    # selection and opened a 4th market on a 3-market rung.
                    # The advance gate is checked after every settlement.
                    gate = _rung_gate()
                    if gate and _after_gate(gate):
                        break
                if held:
                    if held not in filled_markets:
                        filled_markets.add(held)
                        _persist()
                    funnel["selection_skipped_position_held"] += 1
                    _log("resume_start", slug=held,
                         held=list(held_all), concurrency=concurrency,
                         selection_skipped=(
                             "CAMPAIGN_HOLD:POSITION_HELD "
                             f"({len(held_all)}/{concurrency} markets held, "
                             "worked one session at a time; residual held "
                             "to settlement)"),
                         funnel={k: funnel[k] for k in
                                 ("admitted", "sessions", "not_started",
                                  "selection_skipped_position_held")})
                    rep = dict(session_runner(
                        t, clock=clock, sleep=sleep, wall=wall, slug=held,
                        gate_ok=True,
                        deadline_s=min(SESSION_DEADLINE_S, stop_at - clock()),
                        resume=True,
                        decision_seed_offset=ledger_decision_count(held)) or {})
                    rep["resumed"] = True
                    report["sessions"].append(rep)
                    resumes += 1
                    _log("session_end", slug=held, resumed=True,
                         status=rep.get("status"), reason=rep.get("reason"),
                         stop_reason=rep.get("stop_reason"),
                         resumed_inventory=rep.get("resumed_inventory"),
                         inventory=rep.get("inventory"),
                         spring_gates=rep.get("spring_gates"),
                         reconcile=rep.get("reconcile"),
                         filled_actions=_new_fills(rep),
                         submitted=len(rep.get("orders") or []),
                         orders=(rep.get("orders") or [])[-8:],
                         decisions=(rep.get("decisions") or [])[-8:],
                         pair_economics=rep.get("pair_economics"))
                    if rep.get("decisions"):
                        record_session_report(rep, held, True)
                        fidelity_eval(wall(), force=True)
                    if rep.get("status") in ("DAILY_LOSS_STOP", "HALT",
                                             "STATE_NOT_DURABLE",
                                             "NO_CREDENTIALS", "DISARMED"):
                        status = f"CAMPAIGN_STOP:{rep.get('status')}"
                        break
                    funnel["submitted"] += len(rep.get("orders") or [])
                    funnel["filled"] += _new_fills(rep)
                    if _market_gone(rep):
                        # no book / venue says not open: the held market may
                        # have RESOLVED. Ask the venue's own activities for
                        # POSITION_RESOLUTION; realized only when it says so.
                        audit = settle_held(t, held)
                        _log("settlement", slug=held, **audit)
                        if audit.get("status") in ("REALIZED", "SETTLED",
                                                   "SETTLED_WITH_BUSTED_TRADE_REVIEW"):
                            settled_here.add(held)
                            cooldown[held] = float("inf")   # never re-enter
                            _persist()
                            report.setdefault("settlements", []).append(
                                {"slug": held, **audit})
                            if audit.get("status") == "REALIZED":
                                _log("REALIZED", slug=held,
                                     realized_usd=audit.get("realized_usd"))
                                report["realized_pnl"] = _sum_realized(report)
                            # OWNER 5552841170 #3: flat again -> straight back
                            # to full-sports selection, no redeploy
                            status = f"CAMPAIGN_SETTLED:{audit.get('status')}"
                            gate = _rung_gate()
                            if gate and _after_gate(gate):
                                break
                            continue
                    status = f"CAMPAIGN_RESUMING:{rep.get('status')}"
                    woke = await_state_change(t, held, clock, sleep)
                    _log("resume_wait", slug=held, **woke)
                    continue
            if _allowance_spent():
                # the campaign's allowance is spent (rung: fills on
                # markets_allowed distinct markets, restored across
                # restarts; evidence continuation: the frozen gate says
                # every LIVE minimum is met): NO new market is ever
                # selected; the filled markets are continued by the
                # resume/settlement paths until the advance gate fires.
                # LIVE 12:31Z: without this check a 4th market opened
                # after the 3rd settled.
                funnel["selection_skipped_rung_reached"] += 1
                if status == "CAMPAIGN_WAITING:CONTINUATION_PREFLIGHT":
                    # an approved continuation is blocked by its preflight;
                    # the audit path re-checks it, the status says why
                    sleep(SELECT_POLL_S)
                    continue
                if status != "CAMPAIGN_WAITING:RUNG_MARKETS_REACHED":
                    _log("rung_markets_reached",
                         campaign_id=campaign_id, stop_rule=stop_rule,
                         filled_markets=sorted(filled_markets),
                         markets_allowed=report["rung"]["markets_allowed"],
                         evidence_minimums=evidence_minimums_met()[1],
                         unsettled=sorted(s for s in filled_markets
                                          if s not in settled_here),
                         waiting_for="settlement of every filled market -> "
                                     "CAMPAIGN_RUNG_COMPLETE (owner decides "
                                     "the next campaign / rung)")
                status = "CAMPAIGN_WAITING:RUNG_MARKETS_REACHED"
                sleep(SELECT_POLL_S)
                continue
            # never "select" a market the account already holds: it is
            # continued by the resume path, not opened again
            sel = select_candidate(
                now, model,
                {**cooldown, **{s: float("inf") for s in held_all}})
            report["selections"] += 1
            funnel["cycles"] += 1
            funnel["universe_rows"] += len(sel["admitted"]) + len(sel["refused"])
            funnel["in_distribution"] += sum(
                1 for x in sel["admitted"] + sel["refused"]
                if x.get("off_distribution") is False)
            funnel["admitted"] += len(sel["admitted"])
            for x in sel["refused"]:
                key = str(x["reason"]).split(":")[0]
                funnel["refusals"][key] = funnel["refusals"].get(key, 0) + 1
            _log("funnel", **{k: v for k, v in funnel.items()})
            chosen = sel["chosen"]
            if chosen is None:
                if sel["admitted"]:
                    _not_started("admitted_but_no_choice")
                else:
                    _not_started("no_candidate")
                if status.startswith("CAMPAIGN_WAITING"):
                    status = "CAMPAIGN_NO_CANDIDATE"   # venue back, no pick
                sleep(SELECT_POLL_S)
                continue
            if not armed:
                _not_started("not_armed:shadow_only")
                report["last_shadow"] = {"chosen": chosen,
                                         "label": LABEL_SHADOW}
                status = "SHADOW_SELECTED"
                sleep(SELECT_POLL_S)
                continue
            if not base.complement_acceptance_proven():
                _not_started("complement_acceptance_unproven")
                # OWNER 5556038561 #3: NEW first legs fail closed until
                # the venue has accepted an opposite-side order built by
                # this code (proven on the held market's resume path);
                # gated HERE so the per-slug attempt allowance is not
                # spent on the same account-wide refusal every cycle
                proof = base.load_complement_proof()
                if proof != report.get("complement_gate"):
                    report["complement_gate"] = proof
                    _log("complement_gate", chosen=chosen["slug"],
                         proof=proof)
                status = "CAMPAIGN_WAITING:COMPLEMENT_UNPROVEN"
                sleep(SELECT_POLL_S)
                continue
            remaining = stop_at - clock()
            _log("session_start", slug=chosen["slug"], rank=chosen["rank"],
                 state=chosen["state"], state_n=chosen["state_n"],
                 p_buy=chosen["p_buy"],
                 probe_entry_action=chosen.get("probe_entry_action"))
            rep = session_runner(t, clock=clock, sleep=sleep, wall=wall,
                                 slug=chosen["slug"], gate_ok=True,
                                 deadline_s=min(SESSION_DEADLINE_S, remaining),
                                 decision_seed_offset=ledger_decision_count(
                                     chosen["slug"]))
            rep = dict(rep or {})
            funnel["sessions"] += 1
            funnel["submitted"] += len(rep.get("orders") or [])
            funnel["filled"] += _new_fills(rep)
            rep["selected"] = {k: chosen.get(k) for k in
                               ("rank", "state", "state_n", "p_buy",
                                "relation_ranked", "bid", "ask",
                                "bid_touch_qty")}
            report["sessions"].append(rep)
            _log("session_end", slug=chosen["slug"], status=rep.get("status"),
                 reason=rep.get("reason"), stop_reason=rep.get("stop_reason"),
                 inventory=rep.get("inventory"),
                 spring_gates=rep.get("spring_gates"),
                 filled_actions=_new_fills(rep),
                 submitted=len(rep.get("orders") or []),
                 orders=(rep.get("orders") or [])[-8:],
                 decisions=(rep.get("decisions") or [])[-8:],
                 pair_economics=rep.get("pair_economics"))
            if rep.get("decisions"):
                record_session_report(rep, chosen["slug"], False)
                fidelity_eval(wall(), force=True)
            if rep.get("status") in ("DAILY_LOSS_STOP", "HALT",
                                     "STATE_NOT_DURABLE", "NO_CREDENTIALS",
                                     "DISARMED"):
                status = f"CAMPAIGN_STOP:{rep.get('status')}"
                break
            if rep.get("status") == "PREFLIGHT_FAILED" and str(
                    rep.get("reason") or "").startswith("nonzero_positions"):
                # the account already holds a residual (from an earlier
                # session): one market at a time means the campaign holds
                # too, rather than spending its session allowance on the
                # same account-wide refusal every cycle
                status = "CAMPAIGN_HOLD:POSITION_HELD"
                break
            if _has_inventory(rep):
                # one market at a time; the held market is continued by the
                # resume path above (OWNER 5552607804) until flat/settled
                filled_markets.add(chosen["slug"])
                _persist()
                status = f"CAMPAIGN_HOLD:{rep.get('status')}"
                if t is None:                 # no venue view: nothing to resume
                    break
                continue
            sessions_on[chosen["slug"]] = sessions_on.get(chosen["slug"], 0) + 1
            if rep.get("stop_reason") == "spend_cap_reached" or \
                    sessions_on[chosen["slug"]] >= MAX_SESSIONS_PER_SLUG:
                # the durable per-market cap is spent, or this slug has had
                # its attempt allowance (OWNER 5552475548 item 3: attempts are
                # capped separately from exposure so zero-fill cancellations
                # can never become an infinite repost loop)
                cooldown[chosen["slug"]] = float("inf")
            else:
                cooldown[chosen["slug"]] = wall() + RETRY_COOLDOWN_S
            status = "CAMPAIGN_RESELECTING"
        except Exception as exc:                   # noqa: BLE001
            # LIVE 17:40:52Z: a venue HTTP 500 on the read-only open-orders
            # poll escaped held_market() as a Halt and killed the campaign
            # with a position held (restart is NEVER). A transient venue
            # error between sessions is a logged wait, bounded in a window;
            # only sustained failure stops the campaign, fail closed. Any
            # order a session had resting was already cancelled by that
            # session's own deadman before the exception reached here.
            now_c = clock()
            iteration_errors[:] = [e for e in iteration_errors
                                   if now_c - e < ITERATION_ERROR_WINDOW_S]
            iteration_errors.append(now_c)
            if outage is None:
                # postmortem timestamps + the public-print baseline the
                # recovery gate compares against (read-only, may be empty)
                # OWNER 5557988072 #6: classified against the declared
                # maintenance windows (planned venue outage vs unplanned)
                outage = classify_outage(
                    {"start_clock": now_c, "start_utc": _utc(wall),
                     "first_error": f"{type(exc).__name__}:{str(exc)[:120]}"})
                if t is not None:
                    liveness_baseline = liveness_sample(t, liveness_slugs())
                _log("venue_outage_start", **outage,
                     liveness_baseline=liveness_baseline)
                _persist()
            _log("campaign_iteration_error",
                 err=f"{type(exc).__name__}:{str(exc)[:160]}",
                 errors_in_window=len(iteration_errors),
                 max_in_window=MAX_ITERATION_ERRORS,
                 outage_class=(classify_outage(outage, now_utc=_utc(wall)) or {}).get("class"),
                 overrun_s=(classify_outage(outage, now_utc=_utc(wall)) or {}).get("overrun_s"))
            if len(iteration_errors) >= MAX_ITERATION_ERRORS:
                status = "CAMPAIGN_STOP:VENUE_UNAVAILABLE"
                break
            status = "CAMPAIGN_WAITING:VENUE_UNAVAILABLE"
            sleep(ITERATION_ERROR_SLOW_BACKOFF_S
                  if len(iteration_errors) > ITERATION_ERROR_SLOW_AFTER
                  else ITERATION_ERROR_BACKOFF_S)
            continue
    return {**report, "status": status}


def _install_term_handler() -> None:
    """Railway stops a replaced container with SIGTERM. Python's default
    dies without running `finally`, so a resting maker order would stay
    open on the venue. Turn SIGTERM into KeyboardInterrupt so the
    session's deadman cancel runs on the way out."""
    import signal

    def _term(*_: Any) -> None:
        raise KeyboardInterrupt("SIGTERM")
    try:
        signal.signal(signal.SIGTERM, _term)
    except (ValueError, OSError):
        pass


def main() -> int:
    _install_term_handler()
    try:
        report = run_campaign()
    except KeyboardInterrupt:
        _log("campaign_interrupted", reason="SIGTERM_or_interrupt")
        report = {"campaign": "pmus-l5-continuous-v1",
                  "status": "CAMPAIGN_STOP:INTERRUPTED", "realized_pnl": None}
    print(json.dumps(report, indent=1, default=str), flush=True)
    if base.armed() and entry.durable_state_reason() is None:
        base.q1.ledger_append({"kind": "campaign_report", "spring": True,
                               **report})
    safe_prefix = ("SHADOW_", "CAMPAIGN_")
    ok = str(report.get("status", "")).startswith(safe_prefix) or \
        report.get("status") in ("NO_CREDENTIALS", "DURABLE_STATE_REQUIRED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
