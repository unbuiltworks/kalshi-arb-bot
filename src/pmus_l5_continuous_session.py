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
from typing import Any, Mapping, Optional, Sequence

from src import pmus_spring_l5_probe_entry as entry
from src import pmus_migration_close_live as mclose
from src import pmus_residual_quarantine as rq
from src import pmus_risk_set_audit as rsa
from src import pmus_settlement_auditor as sa
from src import pmus_fill_ledger as fl
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
# OWNER GO 5588416241: log-only held-cycle ranking. While a market is held the
# campaign takes the resume_start branch and never reaches select_candidate,
# so 284 of 377 observed cycles (75.3%) carried no record of what a second
# slot could have used -- which made c2/c3 permanently unreconstructible
# rather than merely under-sampled. This ranks the same live universe and
# LOGS it without acting.
SHADOW_RANK_MIN_GAP_S = 20.0    # at most one shadow ranking per this gap
SHADOW_RANK_SLOW_MS = 2000.0    # a ranking slower than this is "materially
                                # delaying the live loop"
SHADOW_RANK_SLOW_STRIKES = 3    # ... and this many disables the measurement
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


# WHAT CODE IS THIS PROCESS ACTUALLY RUNNING. Four deployment surprises on
# 2026-09-09: two pushes to watched files were marked SKIPPED and silently did
# not deploy, and the probe was found still running a commit written BEFORE two
# of the three approved rejection stop conditions existed. GitHub and Railway
# both reported success. For a money-moving service that is not sufficient
# evidence -- the process itself has to say what it is executing.
#
# Stamped in _log() rather than at boot so EVERY row carries it: the boot
# report, each session_end, every rejection. A row can then never be attributed
# to the wrong build.
_COMMIT = (os.getenv("RAILWAY_GIT_COMMIT_SHA")
           or os.getenv("GIT_COMMIT_SHA") or "")[:12] or None


def _log(kind: str, **fields: Any) -> None:
    print("PMUS_L5_CAMPAIGN " + json.dumps({"kind": kind, "commit": _COMMIT,
                                            **fields},
                                           separators=(",", ":"),
                                           default=str), flush=True)


READ_HALT_MARK = "GET_RETRIES_EXHAUSTED"

# OWNER 5573574441: a probe-side alarm when selection keeps finding NO
# admissible candidate while the universe is non-empty (live events are
# being scored and every one is refused / none chosen). The recorder's
# stream alarm covers a dead feed; this covers a live feed the probe
# cannot use, which otherwise looks like a quiet campaign for hours.
# Fires once per window, on stdout as PMUS_SPRING_ALARM and in the
# campaign log; never changes a gate or a limit.
NO_CANDIDATE_ALARM_ENV = "PMUS_SPRING_NO_CANDIDATE_ALARM_SECONDS"
DEFAULT_NO_CANDIDATE_ALARM_S = 1800.0


class NoCandidateAlarm:
    def __init__(self, window_s: Optional[float] = None) -> None:
        self.window_s = float(window_s if window_s is not None else os.getenv(
            NO_CANDIDATE_ALARM_ENV, str(DEFAULT_NO_CANDIDATE_ALARM_S)))
        self.since: Optional[float] = None       # first no-candidate cycle
        self.last_alarm: Optional[float] = None
        self.cycles = 0
        self.rows_seen = 0
        self.refusals: dict = {}

    def note(self, now: float, chosen: Optional[str], n_rows: int,
             refusals: Mapping[str, int]) -> Optional[dict]:
        """Record one selection cycle; return the alarm row when due."""
        if chosen is not None or n_rows <= 0:
            # a pick, or an empty universe (no live events -- not this
            # alarm's case; the stream alarm / status cover that)
            self.since, self.last_alarm, self.cycles = None, None, 0
            self.rows_seen, self.refusals = 0, {}
            return None
        if self.since is None:
            self.since = now
        self.cycles += 1
        self.rows_seen = max(self.rows_seen, int(n_rows))
        for k, v in dict(refusals).items():
            self.refusals[k] = self.refusals.get(k, 0) + int(v)
        if now - self.since < self.window_s:
            return None
        if self.last_alarm is not None and now - self.last_alarm < self.window_s:
            return None
        self.last_alarm = now
        top = sorted(self.refusals.items(), key=lambda kv: -kv[1])[:5]
        return {"kind": "no_candidate", "since_utc": datetime.fromtimestamp(
                    self.since, timezone.utc).isoformat(),
                "no_candidate_s": round(now - self.since, 1),
                "window_s": self.window_s, "cycles": self.cycles,
                "max_universe_rows": self.rows_seen,
                "top_refusals": dict(top),
                "note": "live events scored, none admissible; gates unchanged"}


def _emit_alarm(row: Mapping[str, Any],
                kind: str = "no_candidate_alarm") -> None:
    """Both channels for one alarm row: the human PMUS_SPRING_ALARM line and
    the structured campaign row.

    LIVE 2026-09-11T15:10:15Z. This took a row whose OWN key was `alarm` --
    the risk-set detector's -- and passed it through `**row` while also
    passing `alarm=` explicitly, so every risk-set alarm for 45 minutes died
    as `TypeError: got multiple values for keyword argument 'alarm'`. The
    stdout line above had already printed, which is the only reason the
    transition was visible at all. So the label is now derived from whichever
    key carries it and BOTH keys are stripped from the forwarded fields: a
    row must never be able to collide with the wrapper that reports it."""
    print("PMUS_SPRING_ALARM " + json.dumps(dict(row), separators=(",", ":"),
                                            default=str), flush=True)
    label = row.get("kind") or row.get("alarm")
    _log(kind, **{k: v for k, v in row.items() if k not in ("kind", "alarm")},
         alarm=label)


def _read_halt(rep: Mapping[str, Any]) -> bool:
    """A session that HALTed because a READ-ONLY GET ran out of retries
    before it submitted anything (LIVE 08:21:02Z: 429 on /v1/orders/open in
    the resume preflight; 0 orders, no inventory rebuilt). That is the same
    venue read failure held_market() raises between sessions, not a trading
    halt, and gets the same bounded wait -- never a stopped process with a
    position held (restart is NEVER). A halt after an order was submitted,
    or with a fill in the session, still stops the campaign."""
    return (rep.get("status") == "HALT"
            and READ_HALT_MARK in str(rep.get("reason") or "")
            and not (rep.get("orders") or [])
            and _new_fills(rep) == 0)


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


_SHADOW_RANK = {"next_at": 0.0, "slow": 0, "disabled": False}


def shadow_rank_held_cycle(now: float, held_all, concurrency: int, model,
                           log, path: Optional[str] = None,
                           clock=time.monotonic) -> Optional[dict]:
    """Rank the live universe during a HOLD and return the evidence row.

    OWNER GO 5588416241, and every limit in it is enforced here rather than
    promised: no order path, no session, no reservation, no inventory, no cap
    or concurrency change, nothing that can touch the held market's L5
    OPEN/EXTEND/COMPLETE sequence. It reads the same status document the
    selector reads and ranks it; the caller logs the result and throws it
    away.

    Deliberately NOT select_candidate: that emits a `selection` row, which
    would be indistinguishable from a real one and would corrupt the very
    metrics this exists to feed. The row emitted here is `shadow_selection`
    and carries acted=False.

    "Do not carry stale rankings forward" is satisfied structurally -- a
    fresh status document is read every time and the row carries its own
    status_t and evidence_age_s, so a reader can always tell how old the
    evidence under a ranking was.

    OVERHEAD IS FAILED CLOSED ON THE MEASUREMENT, NEVER ON EXECUTION: a
    ranking slower than SHADOW_RANK_SLOW_MS counts a strike, and
    SHADOW_RANK_SLOW_STRIKES of them disable the instrumentation for the life
    of the process. The recorder starved once already this month; an
    evidence feature is never worth repeating that."""
    if _SHADOW_RANK["disabled"] or now < _SHADOW_RANK["next_at"]:
        return None
    _SHADOW_RANK["next_at"] = now + SHADOW_RANK_MIN_GAP_S
    t0 = clock()
    try:
        from src import pmus_spring_l5_probe_entry as entry
        status, source = entry.load_status_document(path)
        if status is None:
            return None
        ranking = rank_candidates(status, now, model, None,
                                  yield_by_slug=pmus_shadow_yield(now))
    except Exception as exc:                          # noqa: BLE001
        log("shadow_selection_error", error=repr(exc)[:200])
        return None
    elapsed_ms = round((clock() - t0) * 1000.0, 1)
    if elapsed_ms > SHADOW_RANK_SLOW_MS:
        _SHADOW_RANK["slow"] += 1
        if _SHADOW_RANK["slow"] >= SHADOW_RANK_SLOW_STRIKES:
            _SHADOW_RANK["disabled"] = True
            log("shadow_selection_disabled", elapsed_ms=elapsed_ms,
                strikes=_SHADOW_RANK["slow"],
                why=("the measurement was materially delaying the live loop; "
                     "disabling the measurement, never the execution"))
            return None
    admitted = ranking.get("admitted") or []
    return {
        "kind": "shadow_selection",
        "acted": False, "l5_action": False,
        "held": list(held_all), "concurrency": concurrency,
        "source": source, "status_t": ranking.get("status_t"),
        "evidence_age_s": (round(now - entry._epoch(ranking["status_t"]), 3)
                           if entry._epoch(ranking.get("status_t")) else None),
        "n_admitted": len(admitted),
        "n_refused": len(ranking.get("refused") or []),
        "elapsed_ms": elapsed_ms,
        "admitted": [{k: a.get(k) for k in
                      ("rank", "slug", "bid", "ask", "state", "state_n",
                       "pair_score", "pair_at_touch", "components",
                       "game_proximity", "hours_to_end")}
                     for a in admitted[:8]],
        "labels": ("OWNER GO 5588416241: ranked but NOT acted on. Evidence "
                   "for the concurrency counterfactual only; the held "
                   "market's L5 sequence is untouched"),
    }


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
        # OWNER re-arm 2026-09-10: "every session [must] preserve three
        # separate economic outcomes: matched-pair P&L | directional residual
        # P&L | incentives/rebates". The first two are above and are DERIVED;
        # this third is the venue's own credit and is never modelled. Kept
        # separate on purpose -- summing them is what would hide whether the
        # residual is structurally overwhelming the matched trading.
        "incentives_usd": (audit.get("incentives") or {}).get("net_to_us_usd"),
        "incentives": audit.get("incentives"),
        "three_streams": {
            "matched_pair_pnl_usd": str(matched_contrib),
            "directional_residual_pnl_usd":
                (str(Decimal(str(realized)) - matched_contrib)
                 if realized is not None else None),
            "incentives_usd": (audit.get("incentives") or {}).get("net_to_us_usd"),
            "labels": ("kept separate, never summed into one number: the "
                       "first is DERIVED arithmetic, the second is the "
                       "venue's realized less that arithmetic, the third is "
                       "the venue's own credit"),
        },
        # OWNER P1, 2026-09-13: the fields that made this row a COMPLETE
        # terminal reconciliation rather than an economics summary. Two live
        # markets -- legacy cricket and aec-atp-jayfri-maxbas-2026-09-13 --
        # resolved, left the scheduler's held set, and left a reader unable to
        # say from the durable record alone WHAT established terminal, WHEN
        # the venue said so, or which side won. Everything below is copied
        # from venue evidence; nothing is inferred from a book, a clock, a
        # count of empty quotes, or an outcomePrices array.
        "settlement_status": audit.get("status"),
        "settled_by": audit.get("settled_by"),
        "settlement_key": audit.get("settlement_key"),
        "residual_cost_basis": (str(inv.avg_up()) if residual > 0 else
                                (str(inv.avg_dn()) if residual < 0 else None)),
        "outcome_mapping": {
            "resolution_side": (audit.get("resolution") or {}).get("side"),
            "public_settlement_px": (audit.get("public") or {}).get("settlement_px"),
            "public_status": (audit.get("public") or {}).get("status"),
            "public_state": (audit.get("public") or {}).get("state"),
        },
        "venue_timestamps": {
            "resolution_update_time":
                (audit.get("resolution") or {}).get("updateTime"),
            "public_settlement_set_time":
                (audit.get("public") or {}).get("settlement_set_time"),
        },
        "provenance": {
            "source": "venue market state + authenticated account evidence",
            "position_net": audit.get("position_net"),
            "available_position": audit.get("available_position"),
            "venue_qty_total": audit.get("venue_qty_total"),
            "ledger_qty_total": audit.get("ledger_qty_total"),
            "realized_confirmed": bool(audit.get("realized_confirmed")),
            "realized_source": audit.get("realized_source"),
        },
        "reconciliation_version": TERMINAL_RECONCILIATION_VERSION,
        "reconciled_at": datetime.now(timezone.utc).isoformat(),
        "labels": "DERIVED accounting from ledger fills; realized_usd_venue "
                  "is the venue's own; nothing else is realized",
    }
    return out


def _backfill_venue_fills(audit) -> dict:
    """Indirection so tests can stub the repair; see the auditor."""
    from src import pmus_settlement_auditor as auditor
    return auditor.backfill_missing_venue_fills(audit)


def _ledger_qty(slug: str):
    from src import pmus_settlement_auditor as auditor
    return auditor.ledger_qty_total(slug)


def settle_held(t, slug: str, walk=None) -> dict:
    """Authoritative settlement for a held market: the venue's
    POSITION_RESOLUTION activity (read-only), recorded idempotently in
    the durable ledger. Realized is written ONLY from the venue's own
    afterPosition.realized; otherwise it stays unknown. Never raises."""
    try:
        from src import pmus_settlement_auditor as auditor
        result = auditor.audit_market(slug, t, walk=walk)
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
               # carried verbatim so the caller can repair a ledger that
               # under-records the venue (backfill_missing_venue_fills)
               "market": slug,
               "trade_summary": result.get("trade_summary"),
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
# P1: how many unsettled markets one sweep audits. The cursor moves by
# this much each pass, so the whole set is covered rather than the same
# five markets forever.
SETTLE_AUDIT_BATCH = 5
# P1: the settlement_breakdown row's schema version. Bumped when its fields
# change so a reader can tell a complete terminal record from an older one.
TERMINAL_RECONCILIATION_VERSION = "canonical-terminal/v1"
# OWNER 5561142758 #2: the account's own incentive economics, read-only,
# at boot and on this cadence (earnings endpoint, activity credits, trade
# fee fields, balances, published programs vs our markets).
# LIVE 2026-09-07: hourly, not 6-hourly. R3L is the first rung whose fills
# clear the 1.62-share rounding floor, so whether the venue credits a
# maker rebate is now an answerable question rather than a foregone
# $0.00 -- and a 6 h cadence would have missed tonight's fills entirely.
# Read-only GETs; it touches no cap, gate or order path.
INCENTIVE_AUDIT_EVERY_S = 3600.0
# LIVE 2026-09-07T08:20:23Z: the first hourly audit ran AT BOOT, so its
# program-list walk landed in the same seconds as the restore, the orphan
# sweep and the resume preflight -- the moment of the most authenticated
# reads -- and the preflight's /v1/orders/open answered 429 until the
# transport gave up (08:06:14 and again 08:21:02, the second one a HALT
# that stopped the campaign with a leg held). The first audit now waits
# this long after boot; the hourly cadence is unchanged.
INCENTIVE_AUDIT_BOOT_DELAY_S = 600.0


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
    """Markets this ledger has FILLED in whose economic result is NOT yet
    closed by a VENUE-CONFIRMED number.

    The venue drops a resolved market from positions, so a settled position is
    invisible to held_market(); the ledger remembers it.

    ONLY A `realized` ROW CLOSES A MARKET HERE, AND THAT IS A FIX.

    This used to close on `kind in ("settled", "realized")`. But
    pmus_settlement_auditor.append_authoritative_ledger writes `settled`
    ALWAYS and `realized` ONLY when the venue confirmed a number, while
    pmus_q1_executor.realized_today sums ONLY `realized`. So a settlement the
    venue would not put a number on -- SETTLED_REALIZED_UNCONFIRMED -- removed
    the market from the held set here AND contributed nothing to
    realized_today there. Its guaranteed-loss floor stopped being charged and
    no realized number replaced it: the dollars left BOTH terms of
    combined_loss and were in neither.

    That silently defeated the invariant combined_loss documents for itself --
    "a market that settles leaves the HELD set and its result enters
    realized_today in the same transition. Nothing is in both." The transition
    is implemented by two functions in two modules and they disagreed about
    what "settles" means.

    MEASURED 2026-09-15 against an independent reconstruction from immutable
    fills and confirmed cashflows: the executor reported combined_loss
    -23.2786 while the conservative figure was -31.0612, a -7.7826 gap made
    entirely of markets with no venue-confirmed terminal state that had
    stopped being charged. The conservative figure had already breached the
    unchanged -30.00 stop.

    A market being flat, matched, absent from risk_set, or operationally
    complete is NOT a terminal accounting state. Only the venue's own number
    is. Holding the slug here is what keeps its dollars represented, keeps the
    settlement sweep polling it (pmus_settlement_sweep.pending_slugs reads
    THIS), and keeps boundary_preflight's `settlements_current` honest.

    This fails CLOSED: a market the venue never confirms stays charged and
    keeps blocking new campaigns, rather than quietly vanishing from the
    budget. That is the intended direction."""
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
        elif kind == "realized":
            # the venue put a number on it; realized_today() now carries it
            settled.add(slug)
    return [s for s in filled if s not in settled]


def unsettled_detail() -> dict:
    """Why each unsettled market is still open, so "still charged" is legible
    rather than mysterious. A market with a bare `settled` row is resolved at
    the venue but carries no confirmed number, and that distinction is exactly
    what used to be lost."""
    path = base.q1.base.state_dir() / "ledger.jsonl"
    bare_settled: set = set()
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict) and row.get("kind") == "settled" \
                    and row.get("slug"):
                bare_settled.add(str(row["slug"]))
    out = {}
    for s in unsettled_slugs():
        out[s] = ("SETTLED_REALIZED_UNCONFIRMED" if s in bare_settled
                  else "NEVER_SETTLED")
    return out


def ledger_settled_backfill(filled, settled_here, unsettled) -> set:
    """The campaign's filled markets that the LEDGER reports settled and
    the process-local `settled_here` does not.

    The advance gate holds a campaign open until every market it filled is
    settled, and it read that from an in-process set. A market settled by
    an earlier process -- a redeploy, or the read-only settlement sweep --
    never enters that set, so the gate stalls on markets already settled
    and no owner approval can be consumed. `unsettled_slugs()` is the
    authoritative answer (filled with no `settled`/`realized` row), so a
    filled market absent from it is settled.

    Deliberately NOT a safety decision: the venue re-audit in
    continuation_preflight still runs over every filled market and still
    blocks the next campaign on the venue's own answer. This only decides
    whether the gate is allowed to ask the question."""
    unsettled = set(unsettled)
    return {s for s in set(filled) if s not in unsettled} - set(settled_here)


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


SHUTDOWN_IDLE_S = 300.0

# OWNER re-arm 2026-09-10: the nine conditions that must hold BEFORE the
# first submission of the re-armed probe. Named here so the boot row and the
# refusal both quote the same list.
REARM_GATE_ENV = "PMUS_SPRING_REARM_GATE"


def rearm_gate_enabled() -> bool:
    """The re-arm gate ships DARK and an owner arms it, like every other
    behavioural switch here.

    IT CANNOT BE ALWAYS-ON. The nine conditions describe THIS re-arm -- R5F,
    0.25-0.75, all three requote relations. A future campaign on another rung
    is not wrong, and a gate that refused it would be a bug wearing a safety
    label. Worse, an unconditional gate turned every existing armed test into
    a process that idles forever instead of returning: the first full-suite
    run after wiring it did not finish.

    Unrecognised values leave it OFF: an arming switch must never be armed by
    a typo."""
    return os.getenv(REARM_GATE_ENV, "").strip().lower() in (
        "1", "true", "yes", "on")


REARM_RUNG = "R5F"
REARM_BAND = (base.D("0.25"), base.D("0.75"))
REARM_REQUOTE_RELATIONS = ("open", "extend", "complete")
REARM_PAIR_CEILING = base.D("1.00")
REARM_REJECT_STREAK = 3


# DELIBERATELY NOT CACHED, and the reason is worth keeping. Eligibility asks
# the venue a question per held market (an open-order read plus an activity
# walk) and the selection loop turns every SELECT_POLL_S, so a short-lived
# cache is the obvious optimisation -- it was written, and then removed.
#
# It saved a handful of read GETs. It bought, in exchange, a window in which
# `venue_confirmed` could already be false while this still answered
# "dormant" -- which is the 774.62 shape, on the money path, in the one
# function whose job is establishing that a position is safe to stop
# watching. Fail-closed beats fast here, and the saving was never the
# bottleneck. If profiling ever says otherwise, the fix is to make the
# eligibility read cheaper, not to remember a stale answer.
def dormant_held(held, t=None) -> frozenset:
    """Which of `held` residual quarantine has established as DORMANT.

    THE ONE DEFINITION. Both halves of quarantine read it: the BOOT gate
    (via blocking_held, deciding whether to arm) and the RUNTIME scheduler
    (deciding whether the acquisition slot is free). This project has twice
    paid for writing one rule in two places -- the 300 s fill band, and the
    book reader -- so the rule lives here and the callers ask.

    Dormant means every condition in rq.eligibility holds: zero resting
    orders, inventory venue-confirmed against the ledger, real inventory
    (flat is not dormant, it should simply leave), still in `held`, still
    settlement-monitored. Anything that cannot be ESTABLISHED is not
    dormant: "we could not check" and "it holds" must never collapse.

    OFF BY DEFAULT: without PMUS_RESIDUAL_QUARANTINE this is always empty,
    and every caller is byte-identical to its pre-quarantine behaviour.

    Nothing here forgives a dollar. A dormant market stays in `held`, stays
    settlement-monitored, and its full conservative downside is still priced
    by combined_loss_above_stop. This changes WHEN we may acquire, never HOW
    MUCH we may lose."""
    held = list(held or [])
    if not held or not rq.quarantine_enabled():
        return frozenset()
    sweep = _fresh_open_orders(t)
    ours = sweep.get("ours")
    dormant: set = set()
    for slug in held:
        try:
            inv = base.held_inventory_from_ledger(slug)
            got = sa.traded_qty_with_evidence(slug, t)
            agrees = fl.venue_vs_ledger(
                got.get("qty"), str(inv.up + inv.dn)).get("agrees")
            elig = rq.eligibility({
                "slug": slug,
                # account-wide count: 0 open orders means 0 in this market.
                # Anything else is unknown here, and unknown must block.
                "resting_orders": 0 if ours == 0 else None,
                "venue_confirmed": agrees is True,
                "inventory": inv,
                "in_held": True,             # it stays held, by construction
                "settlement_monitored": slug in set(unsettled_slugs() or []),
            })
        except Exception:                                   # noqa: BLE001
            continue                     # could not establish -> not dormant
        if elig["eligible"]:
            dormant.add(slug)
    return frozenset(dormant)


def acquisition_state(held, dormant, concurrency: int,
                      allowance_spent: bool) -> dict:
    """Who occupies an ACQUISITION slot, given what is held and what is
    dormant. A pure function, because the three places that disagreed about
    this on 2026-09-11 disagreed precisely because each one recomputed it
    inline inside a 900-line loop where nothing could test it.

    `held` is untouched by every caller: settlement monitoring, combined_loss
    and the round-robin all read it, and a dormant market stays in all three.
    Quarantine frees a scheduling slot and frees nothing else.

    With `dormant` empty -- always, unless PMUS_RESIDUAL_QUARANTINE is on --
    every field below is what the loop computed before quarantine existed."""
    held = list(held or [])
    dormant = set(dormant or ())
    acquiring = [s for s in held if s not in dormant]
    return {
        "acquiring": acquiring,
        # one slot is one ACQUIRING market. Dormant markets are not acquiring.
        "capacity": len(acquiring) < concurrency and not allowance_spent,
        # the set the session layer must not treat as a foreign account
        # block, declared through the SAME audited allow_slugs channel the
        # concurrency>1 rung already uses. An open order anywhere still
        # refuses regardless of what is declared here.
        "declared_owned": (list(held) if concurrency > 1
                           else sorted(dormant)),
        # the rung-tightened drain. Counted over acquiring markets: without
        # that, freeing the slot opens a second market and the very next
        # cycle declares CAMPAIGN_DRAIN:OVER_CONCURRENCY, which stops opening
        # again -- quarantine would buy exactly one market and re-deadlock.
        # BREACH detection is deliberately NOT here; it stays over every held
        # market, in the loop, where a foreign position halts unconditionally.
        "drain": len(acquiring) > concurrency,
        "quarantined": sorted(dormant),
    }


def blocking_held(t=None) -> list:
    """Held markets that must stop the engine arming. Usually all of them.

    THE DEFECT THIS CLOSES. Residual quarantine frees the runtime acquisition
    slot, and by design a quarantined market stays in `held` right through to
    settlement. But the re-arm gate asked for `held_markets(t) == []`, so on
    every restart a quarantined residual refused to arm -- the engine stops,
    and every maker fill and every credited rebate stops with it. Quarantine
    was built for exactly the fixture that halted us tonight and could never
    have helped, because nothing taught the BOOT gate about it.

    MEASURED, 16 settled R5F markets: the 12 that paired to zero returned
    +6.3639 (mean +0.53); the 4 that stranded a residual returned -9.2384
    (mean -2.31, none positive). Residuals are where the money goes -- and
    a residual that also halts acquisition costs the whole schedule on top.

    OFF BY DEFAULT AND BYTE-IDENTICAL WHEN OFF: without
    PMUS_RESIDUAL_QUARANTINE, dormant_held is empty and this returns
    held_markets(t) unchanged."""
    held = list(held_markets(t) or [])
    dormant = dormant_held(held, t)
    return [s for s in held if s not in dormant]


def boot_assertions(t=None, sweep: Optional[Mapping[str, Any]] = None) -> dict:
    """The owner's nine re-arm conditions, checked before any order.

    "Clear PMUS_SPRING_SHUTDOWN and re-arm only after those boot assertions
    pass." Each check is a fact this process can establish about ITSELF or
    about the venue right now -- not a value copied from a deployment
    dashboard or remembered from a previous run. Every one of them failed
    silently at least once in this campaign's history, which is why they are
    a gate and not a log line.

    Returns {"ok": bool, "checks": [...]}. It never raises: an exception
    inside a check is that check FAILING, because "we could not establish
    it" and "it holds" must never collapse into the same answer."""
    checks: list = []

    def _check(name: str, fn, want=None) -> None:
        try:
            got = fn()
            ok = bool(got) if want is None else (got == want)
        except Exception as exc:                       # noqa: BLE001
            got, ok = f"ERROR:{type(exc).__name__}", False
        checks.append({"check": name, "ok": ok, "got": str(got),
                       **({"want": str(want)} if want is not None else {})})

    # 1. the running artifact contains the crash fix -- read from its source
    _check("crash_fix_51e384fb", base.crash_fix_present, True)
    # 2. fresh venue orphan sweep: zero open, and no foreign ambiguity
    _check("orphan_sweep_clean",
           lambda: (sweep is not None and not sweep.get("foreign")
                    and not sweep.get("errors")
                    and int(sweep.get("seen") or 0) == 0), True)
    _check("no_open_orders_fresh_read",
           lambda: _fresh_open_orders(t), {"ours": 0, "foreign": 0})
    # 3. flat: nothing held at the venue that BLOCKS arming. With quarantine
    #    off (the default) that is literally nothing held, byte-identical to
    #    the original check. With quarantine on, a dormant venue-confirmed
    #    residual does not block -- which is the whole reason quarantine
    #    exists, and check 4 below still charges its full downside.
    _check("flat_held_state", lambda: blocking_held(t), [])
    # 4. the combined budget is priced NOW and is strictly above the stop
    _check("combined_loss_above_stop", lambda: _combined_now()[1], True)
    # 5. rung and band
    _check("rung", lambda: str(_active_rung_id()), REARM_RUNG)
    _check("band", lambda: (base.SPRING_BAND_LO, base.SPRING_BAND_HI),
           REARM_BAND)
    # 6. dynamic COMPLETE armed for all three relations
    _check("requote_relations", lambda: tuple(base.requote_relations()),
           REARM_REQUOTE_RELATIONS)
    # 7/8. the completion ceiling AND the whole-position invariant, both 1.00
    _check("completion_pair_ceiling", base.completion_pair_ceiling,
           REARM_PAIR_CEILING)
    _check("matched_pair_invariant_enforced", _pair_invariant_enforced, True)
    # 9. rejection / infra stops unchanged
    _check("complete_reject_streak_max",
           lambda: base.COMPLETE_REJECT_STREAK_MAX, REARM_REJECT_STREAK)
    return {"ok": all(c["ok"] for c in checks), "checks": checks,
            "labels": ("OWNER re-arm gate: every condition established by "
                       "this process, from its own source or a fresh venue "
                       "read; a check that cannot be established FAILS")}


def _fresh_open_orders(t) -> dict:
    """A fresh /v1/orders/open, split ours vs foreign -- never the sweep's
    own return, for the same reason safe_shutdown refuses to trust it."""
    if t is None:
        raise RuntimeError("no_transport")
    opened = base.q1.base._get_with_retry(t, "/v1/orders/open")
    rows = opened.get("orders") if isinstance(opened, Mapping) else None
    ours_slugs = ledger_slugs()
    ours = foreign = 0
    for o in rows or []:
        if not isinstance(o, Mapping):
            continue
        if str(o.get("marketSlug") or "") in ours_slugs:
            ours += 1
        else:
            foreign += 1
    return {"ours": ours, "foreign": foreign}


def boot_transition_stop() -> dict:
    """The stop the BOOT RE-ARM PREFLIGHT prices the day against.

    LIVE 2026-09-11T21:52:43Z: the gate refused at combined_loss -15.1516
    vs R5F's -15.00 while the owner's approved successor R5FT carries
    -30.00 -- but an approval is consumed at the ADVANCE gate, downstream
    of the gate the old stop was blocking. This lets the preflight see the
    approved transition's stop BEFORE consuming it, and only then.

    Every refusal path returns the active rung's own stop, so no other
    rung's number moves and nothing widens without the exact approval."""
    from src import pmus_transition_stop as ts
    active_id = _active_rung_id()
    persisted = load_campaign_state() or {}
    consumed = [str(persisted.get("campaign_id") or "")] + [
        str(p.get("campaign_id") or "")
        for p in (persisted.get("previous_campaigns") or [])
        if isinstance(p, Mapping)]
    appr = approved_continuation(
        str(persisted.get("campaign_id") or INITIAL_CAMPAIGN_ID),
        [c for c in consumed[1:] if c])
    return ts.transition_stop(active_id, appr, consumed_ids=[c for c in consumed if c],
                              deployment=deployment_identity())


def _combined_now() -> tuple:
    """(combined_loss, strictly_above_the_stop). The stop is INCLUSIVE, so
    equality is already tripped and must not pass this gate.

    The stop is the ACTIVE rung's, except that the boot re-arm preflight
    may price an owner-approved pending transition against the approved
    rung's stop (OWNER 2026-09-11). Nothing else in the campaign reads it
    that way: once the approval is consumed the active rung IS the
    approved one, so this only covers the gap between the two gates."""
    realized = base.q1.realized_today()
    invs = [base.held_inventory_from_ledger(s) for s in unsettled_slugs()]
    combined = base.combined_loss(realized, invs)
    stop = base.daily_loss_stop()
    try:
        t_stop = boot_transition_stop()
        if t_stop.get("source") == "approved_transition":
            stop = D(str(t_stop["stop"]))
            _log("boot_transition_stop", **t_stop)
    except Exception as exc:                           # noqa: BLE001
        # a transition we cannot establish is not a transition
        _log("boot_transition_stop_error", error=repr(exc)[:200])
    return combined, combined > stop


def _active_rung_id() -> str:
    from src import pmus_l5_ramp_ladder as ladder
    return str((ladder.active() or {}).get("id") or "")


def _pair_invariant_enforced() -> bool:
    """The whole-position ceiling is a GATE, not a helper that exists. It is
    enforced only if the session refuses on it, so assert the refusal is
    wired -- `pair_invariant_ok` called in the submit path, and the ceiling
    it reads is the same 1.00.

    BOTH PATHS, and it used to check only one. `pair_invariant_ok` lives
    inside `if relation in ("open", "extend")`; a COMPLETE is priced in its
    own branch and capped there against the same ceiling. This assertion
    would have passed with the COMPLETE cap deleted -- and the COMPLETE is
    precisely the leg that produced the measured 1.2412 pair on
    asc-mlb-chc-mil-2026-09-09 before that cap existed. Ten markets settled
    above par (to 1.085714) in the days before 2026-09-10, when both caps
    landed. A gate that guards one of two doors is not a gate."""
    with open(base.__file__) as fh:
        src = fh.read()
    open_extend = ("if not pair_invariant_ok(inv, side_up, px, qty):" in src
                   and "matched_pair_ceiling" in src
                   and base.matched_pair_cost_after is not None)
    complete = ("econ_max = D(str(ceiling)) - D(str(avg_cost))" in src
                and "capped = px > econ_max" in src
                and "ceiling = completion_pair_ceiling()" in src)
    return open_extend and complete


def pending_qty_reconciliation(slugs, t=None, limit: int = 8,
                               walk=None) -> list:
    """For each still-pending market, the venue's traded quantity against BOTH
    ledger measures, with the per-trade evidence.

    WHY BOTH. The settled-cohort check compares the venue's gross traded
    quantity with the ledger's gross FILLED quantity and reconciled on 15 of
    16 markets. The live session path compares the same venue number with
    inv.up + inv.dn -- CURRENT HOLDINGS. For a buy-only probe those coincide,
    so if they disagree here the disagreement itself is the finding, and
    reporting only one of them is how we would miss it.

    LIVE 01:44Z: aec-county-not-ham-2026-09-08 read venue 774.6200 against
    45.01. That number gates may_assert_flat, so it blocks the flat boundary
    and it blocks sizing the residual for a migration close. The alarm that
    carries the evidence fires at session_end, and a halted probe never
    reaches session_end -- so it runs HERE, on the read-only pass that a
    halted probe does still run.

    Read-only. No order path, no ledger write, and it never raises."""
    from src import pmus_fill_ledger as fl
    from src import pmus_settlement_auditor as sa
    from src import pmus_spring_l5_session as sp
    rows: list = []
    for slug in list(slugs or [])[:limit]:
        row: dict = {"slug": slug}
        try:
            got = sa.traded_qty_with_evidence(slug, t, walk=walk)
            venue = got.get("qty")
            row["venue_qty_total"] = venue
            row["ledger_qty_filled_total"] = sa.ledger_qty_total(slug)
            row["vs_ledger_filled"] = fl.venue_vs_ledger(
                venue, row["ledger_qty_filled_total"])
            try:
                inv = sp.held_inventory_from_ledger(slug)
                held = inv.up + inv.dn
                row["ledger_qty_held"] = str(held)
                row["vs_ledger_held"] = fl.venue_vs_ledger(venue, held)
                row["residual"] = str(inv.up - inv.dn)
            except Exception as exc:                    # noqa: BLE001
                row["ledger_qty_held"] = None
                row["held_error"] = f"{type(exc).__name__}:{str(exc)[:60]}"
            row["qty_evidence"] = sp._qty_evidence_digest(
                {"qty_evidence": got.get("evidence")})
        except Exception as exc:                        # noqa: BLE001
            row["error"] = f"{type(exc).__name__}:{str(exc)[:80]}"
        rows.append(row)
    return rows


# --------------------------------------------------------------- settle batch
SETTLE_CURSOR_FILE = "settle_cursor.json"


def settle_batch(order: list, cursor: int, batch: int = SETTLE_AUDIT_BATCH
                 ) -> tuple[list, int]:
    """Pick the next `batch` markets round-robin, and return the new cursor.

    EXTRACTED FROM THE CAMPAIGN LOOP so both paths share ONE implementation.
    The shutdown path had no bound at all and walked EVERY pending market in a
    single pass; when the accounting fix returned ~38 markets to that set the
    first pass stopped completing, and a settlement loop that never finishes a
    cycle is a settlement loop that never reports anything.

    Modular indexing, never a concatenated slice: slicing audited the SAME
    market twice in one pass whenever fewer markets were pending than the
    batch size. That bug is preserved-as-fixed here rather than reintroduced.
    """
    n = len(order)
    if not n:
        return [], cursor
    i = cursor % n
    take = min(batch, n)
    return [order[(i + k) % n] for k in range(take)], cursor + take


def _cursor_path():
    return base.q1.base.state_dir() / SETTLE_CURSOR_FILE


def load_settle_cursor() -> int:
    """Durable round-robin position.

    In memory it resets to 0 on every restart, so a process that restarts more
    often than it can drain the set re-audits the same head forever and the
    tail is never reached. Progress has to outlive the process."""
    try:
        return int(json.loads(_cursor_path().read_text()).get("i", 0))
    except Exception:                                        # noqa: BLE001
        return 0


def save_settle_cursor(i: int) -> None:
    """Never raises: failing to persist progress must not stop the drain."""
    try:
        p = _cursor_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"i": int(i)}))
        os.replace(tmp, p)
    except Exception:                                        # noqa: BLE001
        pass


def shutdown_settle_pass(t=None) -> dict:
    """Reconcile pending settlements while HALTED, and re-price the budget.

    OWNER 2026-09-10, after the UnboundLocalError halt: "Restore/reconcile the
    17 pending settlements before calculating the current combined loss
    budget." The settlement auditor lived in the campaign loop, which the
    shutdown path deliberately never reaches -- so a halted probe accumulated
    pending markets forever and the combined budget stayed frozen at whatever
    tripped it. The budget would then refuse a lane on stale numbers.

    READ-ONLY AND IT MUST STAY THAT WAY. settle_held() reads the venue's
    POSITION_RESOLUTION activity and appends to OUR durable ledger. There is
    no order transport on this path, and the shutdown reachability test walks
    it. It never raises: a halted process that dies reporting is worse than
    one that reports an error.

    Realized is taken only from the venue's own afterPosition.realized, via
    settle_held. Nothing here marks to market, and nothing here re-arms."""
    out: dict[str, Any] = {
        "pending_before": [], "settled_now": [], "still_pending": [],
        "errors": [], "realized_today_usd": None,
        "unsettled_floor_usd": None, "combined_loss_usd": None,
        "combined_budget_usd": str(base.daily_loss_stop()),
        "budget_permits_normal_operation": None,
        "labels": "read-only; venue-confirmed realized only; no order path"}
    if t is None:                       # no transport, no venue read
        return {**out, "errors": ["no_transport"]}
    try:
        pending = unsettled_slugs()
    except Exception as exc:            # noqa: BLE001
        return {**out, "errors": [f"unsettled_slugs:{type(exc).__name__}"]}
    out["pending_before"] = list(pending)
    try:
        if not base.q1.has_credentials():
            return {**out, "errors": ["no_credentials"]}
    except Exception as exc:            # noqa: BLE001
        return {**out, "errors": [f"credentials:{type(exc).__name__}"]}
    # BOUNDED, ROUND-ROBIN, AND DURABLE. This loop used to walk EVERY pending
    # market in one pass. That was survivable while the set was ~1; when the
    # accounting fix correctly returned ~38 markets to it, the first pass
    # stopped completing and the loop reported nothing at all. One slow or
    # unreachable market could block every other market behind it forever.
    cursor = load_settle_cursor()
    batch, next_cursor = settle_batch(list(pending), cursor)
    save_settle_cursor(next_cursor)
    out["pending_total"] = len(pending)
    out["batch"] = list(batch)
    out["cursor"] = cursor
    out["next_cursor"] = next_cursor
    # ONE BOUNDED WALK PER CYCLE, REUSED BY EVERY MARKET IN THE BATCH.
    # /v1/portfolio/activities is ACCOUNT-scoped, so each market's audit was
    # re-reading the SAME feed: this loop called settle_held() with no `walk`,
    # so every market fetched its own. The continuation preflight has done it
    # the right way since the 429 storm -- `audit_market` already takes a
    # caller-supplied walk and uses it AS IS, never refetching -- and this
    # path simply never passed one. LIVE 2026-10-13/14: 173 consecutive
    # cycles, 5 markets each, 573 HTTP 429 and zero settlements.
    #
    # Bounded by the EARLIEST first-fill in the batch, so the one walk reaches
    # back at least as far as any single market's own `since`; a market the
    # walk does not cover still raises activity_walk_short_of_since inside
    # audit_market and stays PENDING, now at NO additional request cost.
    #
    # A failure here leaves shared_walk None, which restores the previous
    # per-market behaviour rather than skipping the audit: degrading to the
    # old cost is acceptable, silently auditing nothing is not.
    shared_walk = None
    try:
        from src import pmus_settlement_auditor as _auditor
        _sinces = [x for x in (_auditor.first_fill_time(s) for s in batch) if x]
        _since = min(_sinces) if _sinces else None
        # BOUNDED AT ONE MARKET'S OWN CAP, NOT THE DEEP PREFLIGHT CAP.
        # Measured: the per-market walks this replaces used the
        # ACTIVITY_MAX_PAGES default (25), so a 400-page shared walk would
        # cost MORE than the five 25-page walks it removes -- 400 against
        # 125 -- and the saving would be imaginary. Depth buys nothing here
        # either: the 96 markets in this set are dated 09-06..09-15 and 400
        # pages reaches only 09-26, so no reachable cap settles them. 25
        # pages serves any market that genuinely can settle, and one walk
        # then serves the whole batch: 25 against 125 per cycle, and one
        # walk against 96 across a full pass.
        shared_walk = _auditor.activity_pages(
            t, since=_since, max_pages=_auditor.ACTIVITY_MAX_PAGES)
        out["shared_walk"] = {
            "used": True, "since": _since,
            "pages": shared_walk.get("pages"),
            "rows": len(shared_walk.get("rows") or []),
            "ended": shared_walk.get("ended"),
            "oldest_time": shared_walk.get("oldest_time"),
            "truncated_by_page_cap": shared_walk.get("truncated_by_page_cap"),
            "truncated_by_error": shared_walk.get("truncated_by_error"),
            "labels": ("one walk served every market in this batch; "
                       "per-market reads are positions only"),
        }
    except Exception as exc:            # noqa: BLE001
        shared_walk = None
        out["shared_walk"] = {
            "used": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}",
            "labels": "fell back to one walk per market, as before"}
    for slug in batch:
        t0 = time.time()
        try:
            rep = settle_held(t, slug, walk=shared_walk)
        except Exception as exc:        # noqa: BLE001
            # ...but if it ever does, ONE market must not take the cycle down.
            # The market stays pending -- it is NOT dropped, because a failed
            # audit is not evidence that a market settled.
            out["errors"].append(f"{slug}:{type(exc).__name__}: {exc}")
            out["still_pending"].append(
                {"slug": slug, "status": "AUDIT_RAISED",
                 "error": f"{type(exc).__name__}: {exc}",
                 "elapsed_s": round(time.time() - t0, 2)})
            continue
        elapsed = round(time.time() - t0, 2)
        if rep.get("settled"):
            out["settled_now"].append(
                {"slug": slug,
                 "realized_confirmed": rep.get("realized_confirmed"),
                 "realized_usd": rep.get("realized_usd"),
                 "realized_source": rep.get("realized_source"),
                 "breakdown": rep.get("breakdown"),
                 "elapsed_s": elapsed})
        else:
            out["still_pending"].append(
                {"slug": slug, "status": rep.get("status"),
                 "elapsed_s": elapsed})
        if rep.get("status") == "AUDIT_UNAVAILABLE":
            out["errors"].append(f"{slug}:{rep.get('error')}")
    # Re-price the budget from what is NOW known, not from what tripped it.
    try:
        realized = base.q1.realized_today()
        # the FULL unsettled set, never the batch: the budget must price every
        # open obligation, not the five we happened to audit this cycle.
        _remaining = unsettled_slugs()
        invs = [base.held_inventory_from_ledger(s) for s in _remaining]
        out["pending_after"] = len(_remaining)
        out["realized_today_usd"] = str(realized)
        out["unsettled_floor_usd"] = str(base.unsettled_floor_total(invs))
        out["combined_loss_usd"] = str(base.combined_loss(realized, invs))
        out["budget_permits_normal_operation"] = not base.combined_budget_tripped(
            realized, invs)
    except Exception as exc:            # noqa: BLE001
        out["errors"].append(f"budget:{type(exc).__name__}")
    # A market that will not reconcile cannot be declared flat and cannot have
    # its residual sized, so a halted probe must keep asking rather than wait
    # for a session_end it will never reach.
    try:
        out["qty_reconciliation"] = pending_qty_reconciliation(
            [r["slug"] for r in out["still_pending"]], t, walk=shared_walk)
    except Exception as exc:            # noqa: BLE001
        out["errors"].append(f"qty_reconciliation:{type(exc).__name__}")
    return out


def shutdown_idle(sleep=time.sleep, report: Optional[Mapping[str, Any]] = None,
                  t=None, recheck=None):
    """Hold the halted state up. Never exit; return ONLY on a re-proved gate.

    LIVE 2026-09-10T00:57:19Z: the first shutdown ran, verified, and then the
    process EXITED. To the platform a long-running service that exits is a
    CRASH, and the platform's repair for a crash is to roll back to the
    PREVIOUS deployment. That is the exact opposite of halting -- and had the
    verdict been SHUTDOWN_NOT_VERIFIED it would have restored a build that may
    be ARMED, turning a failed safety check into a resumption of trading.

    So failing closed means STAYING UP in a halted state, not terminating.
    A halted process that keeps saying it is halted is legible; a dead one is
    indistinguishable from a crash and gets 'helpfully' replaced.

    OWNER 2026-09-13, P0.2 -- THE OTHER HALF OF THAT BUG. Staying up is
    correct; staying up FOREVER on a condition that has since stopped being
    true is not. LIVE 2026-09-13: flat_held_state failed at 03:35:01Z on a
    genuinely-open tennis position. That position settled at 05:42:59Z. The
    gate was right at 03:35 and wrong from 05:43 onward, and nothing here ever
    looked again -- this loop re-ran only the settlement drain and the
    migration close, never the assertions -- so the executor sat refused for
    hours over a position that no longer existed, waiting on a human restart.

    `recheck` closes that, and it is deliberately the ONLY way out:

      - it is the SAME unchanged gate, re-run against a FRESH venue read. No
        second, softer set of conditions exists to drift away from the first.
      - TIME IS NOT AN INPUT. Cycles do not accumulate credit; there is no
        attempt count, no grace period, no eventual give-up-and-arm. A
        thousand refusals and one refusal are the same state.
      - anything short of an unambiguous pass -- an exception, a malformed
        answer, no transport, no recheck at all -- is a REFUSAL, so the
        pre-P0.2 behaviour (loop forever) is exactly what every abnormal
        path still does.
      - it runs AFTER the settlement drain in the same cycle, because the
        drain is what makes a resolved position stop being held; checking
        first would always read the previous cycle's world.

    Returns the passing gate report when the account re-proves itself, so the
    caller can resume the campaign in this same process -- no human restart,
    and no second executor."""
    verdict = str((report or {}).get("verdict") or "SHUTDOWN_UNKNOWN")
    refusals = 0
    while True:
        # Read-only: drains pending settlements and re-prices the combined
        # budget while halted. Without it the halted probe never learns that
        # a market resolved, and the budget that refused a lane stays frozen
        # at the number that tripped it. Skipped entirely when there is no
        # transport, so this cannot reach the venue from a bare idle call.
        settlement = shutdown_settle_pass(t)
        migration = _idle_migration_close(t)
        gate = _rearm_recheck(recheck)
        if gate is not None and not gate["ok"]:
            refusals += 1
        _log("shutdown_idle", verdict=verdict,
             held_preserved=(report or {}).get("held_preserved"),
             settlement=settlement,
             # progress is the point of this line: a cycle that audits nothing
             # and a cycle that is stuck must not look the same.
             pending_total=(settlement or {}).get("pending_total"),
             pending_after=(settlement or {}).get("pending_after"),
             audited=(settlement or {}).get("batch"),
             cursor=(settlement or {}).get("cursor"),
             settled_now=len((settlement or {}).get("settled_now") or []),
             settle_errors=(settlement or {}).get("errors"),
             migration_close=migration,
             rearm_recheck=gate,
             refusals=refusals,
             evidence=("halted and holding the container up on purpose: "
                       "exiting here would be read as a crash and roll the "
                       "service back to the previous, possibly ARMED, build"))
        if gate is not None and gate["ok"]:
            _log("rearm_self_healed", verdict=verdict,
                 refusals_before=refusals,
                 checks=gate["checks"],
                 evidence=("the SAME re-arm gate that refused now passes on a "
                           "fresh venue read; resuming in this process"))
            return gate
        sleep(SHUTDOWN_IDLE_S)


def _rearm_recheck(recheck) -> Optional[dict]:
    """Run a re-arm recheck and collapse every non-pass into a refusal.

    "We could not establish it" and "it does not hold" must produce the same
    answer here for the same reason boot_assertions collapses them: the only
    safe reading of an unanswered question is no."""
    if recheck is None:
        return None
    try:
        out = recheck()
    except Exception as exc:                                # noqa: BLE001
        return {"ok": False, "checks": [],
                "failed": [f"recheck_error:{type(exc).__name__}"],
                "labels": "a recheck that raised is a refusal, never a pass"}
    if not isinstance(out, Mapping) or "ok" not in out:
        return {"ok": False, "checks": [], "failed": ["recheck_malformed"],
                "labels": "a recheck that answered nothing is a refusal"}
    checks = list(out.get("checks") or [])
    return {"ok": bool(out.get("ok")), "checks": checks,
            "failed": [str(c.get("check")) for c in checks
                       if isinstance(c, Mapping) and not c.get("ok")],
            "labels": out.get("labels")}


def rearm_recheck(t):
    """The re-arm gate as a callable, for shutdown_idle to re-run each cycle.

    It calls the SAME sweep_orphan_orders -> boot_assertions(t, sweep) pair,
    in the same order, that the boot path calls. Not a copy of the checks and
    not a subset of them: a divergent "is it safe yet" predicate is how a gate
    quietly stops meaning what its name says.

    No transport and no armed gate both answer NO, so a recheck can never be
    the thing that makes an unproved state look proved."""
    def _run() -> dict:
        if t is None:
            return {"ok": False, "checks": [
                {"check": "recheck_transport", "ok": False, "got": "None"}],
                "labels": "no venue to establish anything against"}
        if not rearm_gate_enabled():
            return {"ok": False, "checks": [
                {"check": "recheck_gate_enabled", "ok": False,
                 "got": f"{REARM_GATE_ENV} unset"}],
                "labels": "the gate that refused is no longer armed to re-prove"}
        sweep = sweep_orphan_orders(t)
        return boot_assertions(t, sweep)
    return _run


def _idle_migration_close(t):
    """Re-attempt the authorised one-shot close on the halted probe's cycle.

    At 02:43Z the boot attempt refused: the ask had moved 0.85 -> 0.87 and the
    locked loss would have been 0.6831 against the owner's 0.50 cap. That is
    the bound working. But a close attempted only at boot then never happens
    at all -- the probe halts and nothing tries again, so the residual that is
    blocking the boundary keeps blocking it until settlement.

    So the GATE is retried, never the order: every cycle re-reads the venue
    and re-runs all nine preconditions, and submits at most one order and only
    when every one passes. The price bound is not relaxed to make a refusal go
    away; the close simply waits for a book that satisfies it.

    Returns None when no close is requested, so this is invisible unless
    PMUS_MIGRATION_CLOSE_SLUG names a market."""
    if t is None:
        return None
    try:
        invs = [base.held_inventory_from_ledger(s) for s in unsettled_slugs()]
        sweep = _fresh_open_orders(t)
        return mclose.maybe_run(
            t, base.held_inventory_from_ledger,
            base.combined_loss(base.q1.realized_today(), invs),
            open_orders=sweep.get("ours"))
    except Exception as exc:                                # noqa: BLE001
        return {"error": f"{type(exc).__name__}:{str(exc)[:120]}"}


SHUTDOWN_ENV = "PMUS_SPRING_SHUTDOWN"


def shutdown_requested() -> bool:
    return os.getenv(SHUTDOWN_ENV, "").strip().upper() in ("1", "YES", "TRUE")


def safe_shutdown(t=None) -> dict:
    """OWNER 2026-09-10: "We should not design a live autonomous system whose
    safe shutdown procedure is 'Brett, open the app and cancel whatever is
    left.'"

    HALT -> cancel OUR outstanding orders -> VERIFY -> stop submitting ->
    preserve held inventory -> disarm. It runs regardless of armed(), because
    the whole failure it exists to fix is that sweep_orphan_orders lived
    INSIDE the armed branch: disarming was the one transition that could
    orphan an order, and the disarmed process that followed never swept.

    This path can never place an order. Held inventory is PRESERVED to
    settlement and never liquidated here -- a shutdown that dumps inventory
    to look tidy is a worse outcome than the orphan it was cleaning up.

    FAILS CLOSED. Verification is a FRESH read of /v1/orders/open, never the
    sweep's own return: a cancel the venue did not honour looks identical to
    one it did from the caller's side. A failed read, a sweep error, or one of
    ours still resting all return SHUTDOWN_NOT_VERIFIED -- which is the answer
    we report, rather than a reassuring guess that puts a human back in the
    loop with a phone."""
    out: dict[str, Any] = {
        "verdict": "SHUTDOWN_NOT_VERIFIED",
        "labels": ("cancel-only; cannot place; held inventory preserved to "
                   "settlement; verification is a fresh venue read")}
    if not base.q1.has_credentials():
        return {**out, "reason": "no_credentials"}
    if t is None:
        t = base.Transport()
    sweep = sweep_orphan_orders(t)
    out["sweep"] = sweep
    try:
        opened = base.q1.base._get_with_retry(t, "/v1/orders/open")
    except Exception as exc:                        # noqa: BLE001
        return {**out, "reason": f"verify_read_failed:{type(exc).__name__}"}
    rows = opened.get("orders") if isinstance(opened, Mapping) else None
    ours = ledger_slugs()
    still, foreign = [], []
    for o in rows or []:
        if not isinstance(o, Mapping):
            continue
        row = {"order_id": str(o.get("id") or o.get("orderId") or ""),
               "slug": str(o.get("marketSlug") or "")}
        (still if row["slug"] in ours else foreign).append(row)
    out["still_open_ours"], out["foreign_open"] = still, foreign
    try:
        out["held_preserved"] = held_markets(t)
    except Exception as exc:                        # noqa: BLE001
        out["held_preserved"] = None
        out["held_read_error"] = type(exc).__name__
    if sweep["errors"]:
        return {**out, "reason": "sweep_errors"}
    if still:
        return {**out, "reason": "ours_still_open"}
    return {**out, "verdict": "SHUTDOWN_VERIFIED"}


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


# Pacing between the preflight's per-market venue audits. The read-only
# sweep uses 1.5s; this path had none and took an HTTP 429 on all 93 markets
# at 2026-09-11T23:11:26Z. Every market is re-audited on every pass, by
# design: a preflight that re-reads is worth more than one that is fast.
PREFLIGHT_AUDIT_PACE_S = 1.5


def _preflight_sleep(seconds: float) -> None:
    """Indirected so tests can run the loop without real time passing."""
    import time as _t
    _t.sleep(seconds)


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
    # LIVE 23:11:26Z, the blocker this preflight actually had: all 93 markets
    # returned HTTP 429 Too Many Requests. The loop fired 93 authenticated
    # activity walks back to back with NO PACING, and the venue rate-limited
    # us -- self-inflicted, and then repeated on every retry of the waiting
    # loop, which kept the limiter hot and could never recover.
    #
    # The read-only settlement sweep already paces at PACE_S = 1.5 for
    # exactly this reason (src/pmus_settlement_sweep.py); this path never
    # learned it. Pacing is the whole fix: the 429 came from a BURST of 93
    # calls, not from sustained volume, and one call per 1.5s is an ordinary
    # rate the venue serves all day.
    #
    # I FIRST ALSO MEMOISED confirmed markets so retries would re-audit only
    # what was unconfirmed, and removed it. A test that re-stubs the audit
    # with a DIFFERENT ledger quantity to exercise ledger_reconciled caught
    # the cache answering with the old one -- which in production means a
    # ledger/venue divergence appearing after the first confirmation would
    # never be seen again. A preflight that re-reads is worth more than a
    # preflight that is fast, and the retry loop has time to spare.
    # ONE activity walk for the whole set. /v1/portfolio/activities is
    # ACCOUNT-scoped, so auditing 93 markets was re-reading one feed 93
    # times. Bounded by the EARLIEST first-fill across the set, so the
    # snapshot reaches back at least as far as any single market's own
    # `since`. A failure here is not fatal: `walk` stays None and each
    # market falls back to fetching its own, exactly as before.
    shared_walk = None
    walk_note: dict = {}
    try:
        from src import pmus_settlement_auditor as _auditor
        _sinces = [x for x in (_auditor.first_fill_time(s) for s in filled) if x]
        _since = min(_sinces) if _sinces else None
        # DEEP, because it is the only walk. LIVE 00:39:48Z the default
        # 25-page cap stopped it at 2026-09-08T03:11Z, short of the
        # 2026-09-07T19:25Z it was asked for, and 21 markets whose fills
        # predate that were reported PENDING_SETTLEMENT off a feed that had
        # never been read that far back. It stops the moment it passes
        # `since`, so the cap only bounds the pathological case.
        shared_walk = _auditor.activity_pages(
            t, since=_since,
            max_pages=_auditor.SHARED_ACTIVITY_MAX_PAGES) if t is not None else None
        if shared_walk is not None:
            walk_note = {"shared_walk": True, "since": _since,
                         "pages": shared_walk.get("pages"),
                         "rows": len(shared_walk.get("rows") or []),
                         "ended": shared_walk.get("ended"),
                         "oldest_time": shared_walk.get("oldest_time"),
                         "complete_to_since": shared_walk.get("complete_to_since"),
                         "truncated_by_page_cap": shared_walk.get("truncated_by_page_cap"),
                         "truncated_by_error": shared_walk.get("truncated_by_error"),
                         "errors": (shared_walk.get("errors") or [])[:3]}
    except Exception as exc:                            # noqa: BLE001
        shared_walk = None
        walk_note = {"shared_walk": False, "error": repr(exc)[:160],
                     "fallback": "each market fetches its own walk"}
    for i, slug in enumerate(sorted(filled)):
        if i:
            _preflight_sleep(PREFLIGHT_AUDIT_PACE_S)
        audit = settle_held(t, slug, walk=shared_walk)
        # THE LEDGER IS THE ONE THAT IS WRONG. On a venue-confirmed SETTLED
        # market, reconcile this ledger's share count to the venue's own and
        # re-read, so the check judges corrected numbers. It refuses when the
        # SESSIONS' own fills exceed the venue, and refuses on an unsettled
        # market; see backfill_missing_venue_fills.
        fix = None
        try:
            fix = _backfill_venue_fills(audit)
            # "0" means nothing was written, so there is nothing to re-read.
            # Any non-zero adjustment changes the ledger -- INCLUDING A
            # NEGATIVE ONE, which is how an earlier over-correction is undone.
            # Gating on a truthy "backfilled" list (the old shape) would make
            # this whole path a silent no-op.
            if fix and D(str(fix.get("adjustment") or "0")) != 0:
                audit = dict(audit)
                audit["ledger_qty_total"] = _ledger_qty(slug)
                _log("ledger_reconciled_to_venue", **fix,
                     ledger_qty_after=audit["ledger_qty_total"],
                     venue_qty=audit.get("venue_qty_total"))
        except Exception as exc:                        # noqa: BLE001
            fix = {"error": repr(exc)[:160]}
        real[slug] = {"status": audit.get("status"),
                      "ledger_reconcile": fix,
                      "settled": bool(audit.get("settled")),
                      "settled_by": audit.get("settled_by"),
                      "realized_confirmed": bool(audit.get("realized_confirmed")),
                      "realized_usd": audit.get("realized_usd"),
                      "venue_qty_total": audit.get("venue_qty_total"),
                      "venue_qty_unknown_reason": audit.get("venue_qty_unknown_reason"),
                      "ledger_qty_total": audit.get("ledger_qty_total"),
                      # LIVE 22:45:07Z: all 93 markets came back
                      # AUDIT_UNAVAILABLE with every other field null, and
                      # the preflight row could not say WHY -- settle_held
                      # puts the exception in `error` and this dict dropped
                      # it. 93 identical undiagnosable failures is not 93
                      # facts about markets, it is one fact about the read.
                      "error": audit.get("error")}
    # LIVE 15:46Z (OWNER P0 5560183912): the venue confirms settlement
    # either by a POSITION_RESOLUTION activity or by having carried no
    # position at a public resolution; for the latter it publishes NO
    # realized number (the trades carry none), so realized stays
    # unconfirmed and is REPORTED, never blocking: no position, no order,
    # no unsettled market is the exposure safety, and it is unchanged.
    # When EVERY market fails the same way, the venue read is broken, not the
    # markets. Name that once instead of leaving it to be inferred from 93
    # identical rows -- which is exactly what cost a deploy at 22:45:07Z.
    _errs = sorted({str(v.get("error")) for v in real.values() if v.get("error")})
    _unav = [s for s, v in real.items() if v.get("status") == "AUDIT_UNAVAILABLE"]
    checks["venue_settled"] = {
        "ok": bool(real) and all(v["settled"] for v in real.values()),
        "activity_walk": walk_note,
        "audit_unavailable": len(_unav),
        "audit_error_kinds": _errs[:5],
        "uniform_failure": bool(real) and len(_unav) == len(real),
        "diagnosis": ("every market failed the same read: this is a venue/"
                      "transport fault, not a settlement fact"
                      if real and len(_unav) == len(real) else None),
        "markets": real,
        "realized_unconfirmed": sorted(s for s, v in real.items()
                                       if not v["realized_confirmed"]),
        # named, not inferred: a market the walk never reached cannot be
        # reconciled, and must not be silently counted as agreeing either
        "venue_qty_unknown": sorted(s for s, v in real.items()
                                    if v.get("venue_qty_unknown_reason")),
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


_REALIZED_SCOPE = ("THIS PROCESS only, not the day: a redeploy restarts this "
                   "accumulator. For a daily figure sum the venue-confirmed "
                   "settlement rows across processes.")


def _sum_realized(report: Mapping[str, Any]) -> Optional[str]:
    """VENUE-confirmed realized over THIS PROCESS's settlements only.

    NOT a daily figure, and the difference cost an hour of accounting on
    2026-09-11. campaign_end reported realized_pnl -6.1848 while the day's
    venue-confirmed realized was -9.3736: this process booted at 14:57:23Z
    and nickja had realized at 14:00:46Z under the previous one, so its
    -3.1888 was outside the report this sums. A redeploy silently restarts
    the accumulator, which means any restart makes this UNDER-state the day
    -- and under-stating a loss is the dangerous direction.

    The value is correct for what it is. `realized_scope` beside it in the
    campaign_end row is what stops it being read as something else."""
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
    if shutdown_requested():
        # before armed() is even consulted: shutdown is not a mode of
        # trading, it is the absence of it
        if t is None:
            # materialise it once so the idle loop can keep reconciling
            # settlements read-only; safe_shutdown builds its own otherwise
            # and drops it, leaving the halted process unable to see a
            # market resolve.
            try:
                if base.q1.has_credentials():
                    t = base.Transport()
            except Exception:                      # noqa: BLE001
                t = None
        rep = safe_shutdown(t)
        _log("safe_shutdown", **rep)
        shutdown_idle(sleep, rep, t)       # never returns in production
        return {"campaign": "pmus-l5-continuous-v1", "mode": "SHUTDOWN",
                "status": rep["verdict"], "shutdown": rep,
                "realized_pnl": None, "sessions": [], "selections": 0}
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
            # OWNER 2026-09-11, one-shot migration close. It runs BEFORE the
            # re-arm gate on purpose: the gate refuses to arm unless the
            # account is flat, and a legacy residual is precisely what stops
            # it being flat. Closing first lets one boot go
            # residual -> flat -> armed instead of deadlocking on itself.
            #
            # Inert unless PMUS_MIGRATION_CLOSE_SLUG names a market, so the
            # ordinary boot path is unchanged by its presence. Reduce-only and
            # capped at abs(residual), so it cannot open, extend or overshoot
            # -- the flatness gate is not bypassed here, it is satisfied.
            try:
                _mc_invs = [base.held_inventory_from_ledger(x)
                            for x in unsettled_slugs()]
                closed = mclose.maybe_run(
                    t, base.held_inventory_from_ledger,
                    base.combined_loss(base.q1.realized_today(), _mc_invs),
                    # the SAME fresh venue sweep boot_assertions uses; probing
                    # for a q1 helper that does not exist recorded None and
                    # failed closed on a book with no orders at all
                    open_orders=sweep.get("ours"))
                if closed is not None:
                    report["migration_close"] = closed
            except Exception as exc:                        # noqa: BLE001
                _log("migration_close_error",
                     err=f"{type(exc).__name__}:{str(exc)[:120]}")
            # OWNER re-arm 2026-09-10: the nine conditions, established by
            # this process before the first submission. A failure HOLDS the
            # container up rather than exiting -- exiting reads as a crash
            # and the platform's repair is a rollback to the previous,
            # possibly still-crashing, build.
            boot = (boot_assertions(t, sweep) if rearm_gate_enabled()
                    else {"ok": True, "checks": [],
                          "labels": f"gate OFF ({REARM_GATE_ENV} unset)"})
            _log("boot_assertions", **boot)
            report["boot_assertions"] = boot
            if not boot["ok"]:
                failed = [c["check"] for c in boot["checks"] if not c["ok"]]
                _log("rearm_refused", failed=failed,
                     evidence=("a re-arm condition could not be established; "
                               "refusing to submit and holding halted"))
                # OWNER 2026-09-13 P0.2: hold halted, and keep re-running THIS
                # gate. It returns only when the same nine conditions pass on
                # a fresh venue read -- the refusal below is still what any
                # other exit from the idle loop means.
                healed = shutdown_idle(sleep, {"verdict": "REARM_REFUSED",
                                               "failed": failed,
                                               "held_preserved": []}, t,
                                       recheck=rearm_recheck(t))
                if not (healed or {}).get("ok"):
                    return {**report, "status": "CAMPAIGN_STOP:REARM_REFUSED",
                            "failed_checks": failed}
                # the refusal is KEPT: report["boot_assertions"] stays the
                # gate that failed, and the pass that released it is recorded
                # beside it. A self-heal that erased its own cause would make
                # the next investigation start from nothing.
                report["rearm_self_healed"] = {"first_failed": failed,
                                               "checks": healed["checks"]}
                _log("rearm_resumed", first_failed=failed,
                     evidence=("the account re-proved every re-arm condition "
                               "while halted; continuing in this process"))
    start = clock()
    stop_at = start + campaign_s
    cooldown: dict[str, float] = {}
    sessions_on: dict[str, int] = {}
    status = "CAMPAIGN_NO_CANDIDATE"
    resumes = 0
    last_settle_audit = float("-inf")
    _settle_rr = {"i": 0}          # P1: rotate, never a fixed prefix
    last_incentive_audit = (start - INCENTIVE_AUDIT_EVERY_S
                            + INCENTIVE_AUDIT_BOOT_DELAY_S)
    settled_here: set = set()
    iteration_errors: list = []          # clock() stamps of recent errors
    # OWNER 5554127702 #4: the prebuilt ladder names the active rung
    # (PMUS_SPRING_RUNG, default R1 = the authorized 3-market rung). Only
    # the THROUGHPUT levers are wired here (markets per campaign, actions
    # per session); the per-market cap and concurrency stay the session's
    # hard constants until the owner changes them by decision.
    active_rung = ladder.set_active(None)        # the env rung (default R1)

    def _rung_markets_of(r: dict) -> Optional[int]:
        """The campaign's market allowance: the env override, else the
        rung's markets_per_campaign. OWNER PROGRAM 5574156328 (R5F): None
        = CONTINUOUS -- the campaign never stops at a market count, only
        at the daily stop / the horizon (env "none", "continuous" or "0"
        reads the same way)."""
        raw = os.getenv(RUNG_MARKETS_ENV)
        if raw is not None:
            if raw.strip().lower() in ("none", "continuous", "0", ""):
                return None
            return max(1, int(raw))
        return ladder.markets_per_campaign(r)
    rung_markets = _rung_markets_of(active_rung)
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
        rung_markets = _rung_markets_of(active_rung)
        os.environ[base.MAX_ACTIONS_ENV] = str(active_rung["max_actions_per_session"])
        return active_rung
    filled_markets: set = set()          # markets with a fill this campaign
    no_candidate_alarm = NoCandidateAlarm()   # OWNER 5573574441
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
            "markets_allowed": ((rung_markets if rung_markets is not None else
                                 "continuous (daily stop / horizon)")
                                if stop_rule == STOP_RULE_RUNG
                                else "until the frozen LIVE evidence minimums are met"),
            "per_market_cap_usd": str(base.session_notional_cap()),
            "residual_cap_units": base.max_residual_units(),
            # OWNER 5567269295: the R4 levers, from the rung, never literals
            "max_unmatched_usd": (str(base.max_unmatched_usd())
                                  if base.max_unmatched_usd() is not None else None),
            "pair_cost_max": str(base.pair_cost_max()),
            "safety_shell_version": base.safety_shell_version(),
            "worst_case_usd": (str(base.session_notional_cap() * rung_markets)
                               if stop_rule == STOP_RULE_RUNG and rung_markets is not None
                               else f"{base.session_notional_cap()} per filled market, "
                                    f"bounded by the daily-loss stop"),
            "daily_loss_stop_usd": str(base.daily_loss_stop()),
            # OWNER PROGRAM 5574156328 (R5F): the fidelity levers, from the
            # rung, never literals
            "complement_pricing": ladder.complement_pricing(),
            "rest_policy": ladder.rest_policy(),
            "maker_rest_max_s": base.maker_rest_max_s(),
            "max_sessions_per_campaign": ladder.max_sessions_per_campaign(
                default=MAX_SESSIONS_PER_CAMPAIGN),
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
        # OWNER 2026-09-11: the advance gate asks "is every filled market
        # of this campaign settled?" and answered it from a PROCESS-LOCAL
        # accumulator. A market settled by an EARLIER process -- or by the
        # read-only sweep -- is absent from that set, so the gate stalls
        # forever on markets the LEDGER already reports settled. Not
        # hypothetical: the R5F state saved at 14:42:40Z carries 92 filled
        # markets and 80 in settled_here while unsettled_slugs() is EMPTY,
        # so the gate could never fire and no owner approval could ever be
        # consumed.
        #
        # The ledger is the authoritative settlement record (its rows are
        # written only by the settlement audit), so seed from it. This
        # WEAKENS NOTHING: continuation_preflight still re-audits every
        # filled market against the VENUE and blocks the next campaign
        # unless the venue itself says settled. All this decides is
        # whether the gate gets to ASK.
        try:
            _ledger_settled = ledger_settled_backfill(
                filled_markets, settled_here, unsettled_slugs())
            settled_here |= _ledger_settled
        except Exception as exc:                       # noqa: BLE001
            _ledger_settled = set()
            _log("campaign_settled_seed_error", error=repr(exc)[:200])
        if _ledger_settled:
            _log("campaign_settled_seeded_from_ledger",
                 added=sorted(_ledger_settled),
                 persisted_settled_here=len(persisted.get("settled_here") or []),
                 filled_markets=len(filled_markets),
                 settled_here=len(settled_here),
                 note="ledger-confirmed settlement the previous process did "
                      "not record; the venue re-audit in "
                      "continuation_preflight is unchanged and still blocking")
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
    # slugs whose quarantine has already been announced, so the row appears
    # when the set CHANGES rather than on every one of the loop's cycles
    _quarantine_logged: set = set()
    # previous risk-set snapshot and scheduled set, for the held->absent
    # detector. Seeded empty: the first cycle establishes a baseline and
    # cannot alarm, because "we have not looked yet" is not a transition.
    _risk_prev: dict = {"snapshot": None, "held": []}

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
        if rung_markets is None:
            # OWNER PROGRAM 5574156328 (R5F): a continuous campaign has no
            # market allowance to spend; it ends at the daily stop, the
            # horizon or the owner's hand, never at a market count
            return False
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
    # OWNER: the process itself must prove the arm, not the operator's memory
    # of which variables were set -- SHA, mode, rung, and the ONE behavioural
    # flag this campaign exists to test. Without this line an absent COMPLETE
    # re-quote is unattributable: a flag that never got set and a market that
    # never moved its touch produce identical logs.
    _log("campaign_start", mode=report["mode"], campaign_s=campaign_s,
         campaign_expires_at=wall() + campaign_s,
         max_sessions=ladder.max_sessions_per_campaign(
             default=MAX_SESSIONS_PER_CAMPAIGN),
         per_market_cap=str(base.SESSION_NOTIONAL_CAP),
         complete_requote=base.complete_requote_enabled(),
         requote_relations=list(base.requote_relations()),
         # OWNER 2026-09-10: the economic envelope must be provable from the
         # boot row like the flag is. rung.pair_cost_max is the LADDER's value
         # (0.98) and is NOT the rule that now binds; these two are.
         completion_pair_ceiling=str(base.completion_pair_ceiling()),
         matched_pair_ceiling=str(base.completion_pair_ceiling()),
         combined_loss_budget_usd=str(base.daily_loss_stop()),
         rung=report["rung"])
    _log("ramp_ladder", active=active_rung["id"],
         rungs=[{"id": r["id"], "lever": r["lever"],
                 "worst_case": r["worst_case"]} for r in report["ramp_ladder"]])
    # OWNER 5557786528 #7: reconstruct the evidence from the immutable raw
    # ledger at boot (read-only) so the corrected UNIQUE minimums are
    # published even while the venue is unavailable; re-run on the frozen
    # cadence below and forced after every session that adds evidence
    fidelity_eval(wall(), force=True)
    # the session bound follows the ACTIVE rung (R5F: continuous, 10,080 =
    # one per minute for the 7-day horizon); the R1..R4 default is 48
    _hb = {"t": 0.0, "boot": wall()}
    while clock() < stop_at and len(report["sessions"]) - sessions_base < \
            ladder.max_sessions_per_campaign(default=MAX_SESSIONS_PER_CAMPAIGN) + resumes:
        try:
            now = wall()
            if now - _hb["t"] >= HEARTBEAT_S:
                # the heartbeat reads only values this cycle already has --
                # it must never consume the campaign clock, or observing
                # the loop would change it
                _hb["t"] = now
                _log("heartbeat", campaign_id=campaign_id,
                     uptime_s=round(now - _hb["boot"], 1),
                     sessions=len(report["sessions"]),
                     held=len(held_all), status=status,
                     rss_mb=_rss_now_mb(), rss_peak_mb=_rss_peak_mb(),
                     evidence=("this line is the liveness signal: a missing "
                               "heartbeat means the process is gone, which "
                               "quiet logs alone can never tell you"))
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
                # RUNTIME HALF OF RESIDUAL QUARANTINE. The boot gate already
                # knew what dormant meant; the scheduler did not, so on
                # 2026-09-11 the campaign armed and then sat at
                # CAMPAIGN_HOLD:POSITION_HELD indefinitely -- quarantine
                # freed the arm and nothing else. dormant_held is the same
                # single definition the boot gate reads, and it is empty
                # whenever quarantine is off, which leaves every line below
                # identical to its pre-quarantine form.
                dormant = dormant_held(held_all, t)
                slots = acquisition_state(held_all, dormant, concurrency,
                                          _allowance_spent())
                acquiring = slots["acquiring"]
                if set(dormant) != _quarantine_logged:
                    _quarantine_logged.clear()
                    _quarantine_logged.update(dormant)
                    if dormant:
                        _log("quarantine_slot", quarantined=sorted(dormant),
                             held=list(held_all), acquiring=acquiring,
                             concurrency=concurrency,
                             evidence=(
                                 "dormant: zero resting orders, inventory "
                                 "venue-confirmed against the ledger, still "
                                 "held and still settlement-monitored. Its "
                                 "full min(up,dn)-spent downside stays in "
                                 "combined_loss and it is still worked for "
                                 "COMPLETE; it stops occupying the one "
                                 "acquisition slot."))
                base.set_concurrent_owned(slots["declared_owned"])
                capacity = slots["capacity"]
                # RISK-SET AUDIT (owner 2026-09-11), read-only. The running
                # probe never emitted what it was CARRYING -- `unsettled`
                # appears only in rung_markets_reached, which a continuous
                # campaign never reaches, and in the shutdown_idle re-price,
                # which needs a halt. So when cricket left `held` with no
                # settlement row there was no row to diagnose it from. This
                # emits the risk-bearing set beside the scheduled one, and
                # alarms when a slug leaves the scheduler while still
                # carried in the loss budget.
                #
                # Nothing here decides anything. Risk is priced over
                # unsettled_slugs() at every call site, so a scheduler
                # lifecycle bug cannot erase exposure; this makes the
                # divergence legible rather than guarding against it.
                try:
                    _unsettled = unsettled_slugs() or []
                    # OWNER 2026-09-13: the exposure the slot count is NOT.
                    # Priced over the risk-bearing set, never the scheduled
                    # one, so a quarantined or resolved-but-unaudited market
                    # still shows its dollars.
                    _m = _n = D("0")
                    for _s in _unsettled:
                        try:
                            _iv = base.held_inventory_from_ledger(_s)
                        except Exception:               # noqa: BLE001
                            continue
                        _au, _ad = _iv.avg_up(), _iv.avg_dn()
                        if _iv.matched > 0 and _au is not None and _ad is not None:
                            _m += _iv.matched * (_au + _ad)
                        _n += base.unmatched_usd(_iv)
                    _rs = rsa.snapshot(
                        _unsettled, held_all,
                        combined_loss_usd=str(_combined_now()[0]),
                        daily_loss_stop_usd=str(base.daily_loss_stop()),
                        acquisition_slots_in_use=len(acquiring),
                        total_matched_capital_usd=str(_m),
                        total_unmatched_usd=str(_n),
                        # every residual worthless, matched pairs still pay
                        portfolio_worst_case_usd=str(_n),
                        daily_realized_pnl_usd=str(base.q1.realized_today()))
                    for _a in rsa.detect(_risk_prev["held"], held_all,
                                         _unsettled):
                        _emit_alarm(_a, "risk_set_alarm")
                    if rsa.changed(_risk_prev["snapshot"], _rs):
                        _log("risk_set", **_rs)
                    _risk_prev["snapshot"] = _rs
                except Exception as exc:                    # noqa: BLE001
                    # an audit that cannot run must never stop the campaign
                    _log("risk_set_error", error=repr(exc)[:200])
                finally:
                    # ALWAYS advance the baseline, even when the audit threw.
                    # LIVE 15:10:15Z: the emission raised, this assignment
                    # never ran, and ONE transition re-alarmed on every cycle
                    # for 45 minutes while the snapshot stayed frozen at its
                    # 14:59 values -- so the row that would have shown the
                    # loss budget crossing its stop never appeared. A
                    # watchdog that repeats forever is a broken watchdog.
                    # The cost of advancing after a failure is one missed
                    # EDGE; the standing condition it describes is still
                    # visible every cycle in the snapshot's
                    # `charged_not_scheduled`, which is level, not edge.
                    _risk_prev["held"] = list(held_all)
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
                    # BREACH detection stays over every held market. A
                    # position this campaign did not open must halt whether
                    # or not something else is quarantined, so held_all --
                    # not `acquiring` -- is what is asked. (A foreign market
                    # could not be dormant anyway: it has no ledger inventory
                    # and is not settlement-monitored. Relying on that
                    # implicitly is exactly how a safety branch rots.)
                    if over_concurrency_action(held_all, filled_markets,
                                               concurrency) == "halt":
                        status = f"CAMPAIGN_STOP:{MULTI_HELD}"
                        _log("concurrency_breach", held=held_all, foreign=foreign,
                             concurrency=concurrency, rung=active_rung["id"])
                        break
                    # DRAIN is the rung-tightened case and is counted over
                    # ACQUIRING markets (acquisition_state decides; see the
                    # note there on why quarantine would otherwise buy
                    # exactly one market and then re-deadlock).
                    if slots["drain"]:
                        if status != "CAMPAIGN_DRAIN:OVER_CONCURRENCY":
                            _log("concurrency_drain", held=held_all,
                                 acquiring=acquiring, concurrency=concurrency,
                                 rung=active_rung["id"],
                                 reading="rung tightened under open "
                                         "inventory; holding every market to "
                                         "settlement and opening none")
                        status = "CAMPAIGN_DRAIN:OVER_CONCURRENCY"
                if t is not None and clock() - last_incentive_audit >= INCENTIVE_AUDIT_EVERY_S:
                    last_incentive_audit = clock()
                    report["incentive_audit"] = run_incentive_audit(t, _log)
                if not held and t is not None and \
                        clock() - last_settle_audit >= SETTLE_AUDIT_EVERY_S:
                    # flat, but the ledger remembers fills the venue has since
                    # resolved out of positions: record their authoritative
                    # settlement (realized only from the venue's own number).
                    #
                    # OWNER P1, 2026-09-13 -- WHY THIS MISSED jayfri. It took
                    # unsettled_slugs()[:5]: a FIXED PREFIX of a list in ledger
                    # first-fill order. Markets 6..n were never audited at all
                    # while markets 1..5 were re-audited every minute, so a
                    # market's reconciliation latency depended on how early it
                    # first filled rather than on whether it had resolved.
                    # jayfri filled at 13:01, resolved, left held at 13:57 and
                    # sat unaudited with its full downside still charged.
                    #
                    # So ROTATE across the whole unsettled set -- every market
                    # is reached within ceil(n/BATCH) passes -- and take the
                    # ones the risk set is charging but the scheduler is NOT
                    # working first, which is the exact shape of a market that
                    # resolved and left.
                    #
                    # The `not held` gate STAYS, but NOT for the reason the
                    # P1 commit gave. That commit said "the loop already
                    # alternates (open_cycle), so `held` is None every other
                    # pass" -- and that is FALSE at this rung. The alternation
                    # is `capacity and (i % 2 == 0)`, and capacity is
                    # `len(acquiring) < concurrency`; at concurrency 1 with one
                    # NON-dormant held market acquiring has one entry, capacity
                    # is False, open_cycle is never True and this sweep NEVER
                    # RUNS. Not less often -- never.
                    #
                    # WHAT ACTUALLY KEEPS IT ALIVE IS RESIDUAL QUARANTINE: a
                    # held market that goes quiescent becomes dormant, leaves
                    # `acquiring`, frees the slot, and capacity returns. LIVE
                    # 2026-09-13, jayfri reconciled at 14:01:57Z while cov-bha
                    # was still held, which at concurrency 1 is only possible
                    # with cov-bha dormant.
                    #
                    # So timely settlement reconciliation at concurrency 1
                    # DEPENDS ON PMUS_RESIDUAL_QUARANTINE being enabled.
                    # Turning quarantine off looks like an acquisition-
                    # scheduling change and would silently starve settlement
                    # auditing whenever a position is held.
                    # tests/test_settlement_sweep_liveness.py pins this.
                    last_settle_audit = clock()
                    _uns = unsettled_slugs()
                    _absent = [s for s in _uns if s not in set(held_all)]
                    _rest = [s for s in _uns if s in set(held_all)]
                    _order = _absent + _rest
                    if _order:
                        # the SAME bounded round-robin the shutdown path uses.
                        # Extracted to settle_batch() so the two cannot drift;
                        # the modular-indexing fix (never a concatenated slice,
                        # which audited one market twice per pass when fewer
                        # markets were pending than the batch) lives there now.
                        _order, _settle_rr["i"] = settle_batch(
                            _order, _settle_rr["i"])
                    for slug in _order:
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
                            report["realized_scope"] = _REALIZED_SCOPE
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
                    # OWNER GO 5588416241. Placed AFTER the session returns on
                    # purpose: ranking before it would put a status fetch in
                    # front of live order placement, and the approval was for
                    # evidence, not for latency on the money path. The hold
                    # persists across resume iterations, so sampling here is
                    # still inside the window a second slot would have used.
                    try:
                        shadow = shadow_rank_held_cycle(
                            wall(), held_all, concurrency, model, _log)
                    except Exception as exc:          # noqa: BLE001
                        shadow = None
                        _log("shadow_selection_error", error=repr(exc)[:200])
                    if shadow:
                        _log(**shadow)
                    _log("session_end", slug=held, resumed=True,
                         status=rep.get("status"), reason=rep.get("reason"),
                         stop_reason=rep.get("stop_reason"),
                         resumed_inventory=rep.get("resumed_inventory"),
                         inventory=rep.get("inventory"),
                         spring_gates=rep.get("spring_gates"),
                         reconcile=rep.get("reconcile"),
                         # OWNER 5584807147 item 3: the venue-vs-ledger proof
                         # has to be readable in the log, not only persisted
                         # in the session report nobody can grep
                         flat_verified=rep.get("flat_verified"),
                         fill_reconciliation=rep.get("fill_reconciliation"),
                         filled_actions=_new_fills(rep),
                         submitted=len(rep.get("orders") or []),
                         orders=(rep.get("orders") or [])[-8:],
                         decisions=(rep.get("decisions") or [])[-8:],
                         pair_economics=rep.get("pair_economics"))
                    if rep.get("decisions"):
                        record_session_report(rep, held, True)
                        fidelity_eval(wall(), force=True)
                    if _read_halt(rep):
                        _log("session_read_halt", slug=held, resumed=True,
                             reason=rep.get("reason"),
                             treatment="venue read failure before any order:"
                                       " bounded wait, campaign continues")
                        raise base.q1.base.Halt(str(rep.get("reason")))
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
                                report["realized_scope"] = _REALIZED_SCOPE
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
            cycle_refusals: dict = {}
            for x in sel["refused"]:
                key = str(x["reason"]).split(":")[0]
                cycle_refusals[key] = cycle_refusals.get(key, 0) + 1
            alarm_row = no_candidate_alarm.note(
                now, chosen["slug"] if chosen else None,
                len(sel["admitted"]) + len(sel["refused"]), cycle_refusals)
            if alarm_row:
                _emit_alarm(alarm_row)
                report["alarms"] = list(report.get("alarms") or []) + [alarm_row]
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
                # OWNER GO 5586502825 (Option A): the gate could only ever be
                # re-armed from a HELD market's residual, so once the account
                # went flat it stayed shut -- 181 cycles and 55 minutes with
                # no order placed on 2026-09-08. When nothing is held, run the
                # same venue-minimum post-only probe against the market just
                # selected. Rate-limited to one probe order per
                # COMPLEMENT_PROOF_RETRY_S exactly as the held-market path is,
                # and what counts as proof is unchanged: the VENUE must show
                # it resting. If it proves, fall through and start the normal
                # L5 session on this cycle rather than waiting for the next.
                last_try = float(base.load_complement_proof().get(
                    "attempted_epoch") or 0)
                if now - last_try >= base.COMPLEMENT_PROOF_RETRY_S:
                    probe = base.prove_complement_when_flat(t, chosen["slug"])
                    _log("complement_flat_probe", chosen=chosen["slug"],
                         proven=bool(probe.get("proven")),
                         end_reason=probe.get("end_reason"),
                         skipped=probe.get("skipped"),
                         order_id=probe.get("order_id"),
                         filled_qty=probe.get("filled_qty"),
                         l5_action=False)
                    if D(str(probe.get("filled_qty") or 0)) > 0:
                        # The probe is post-only and JOINS THE BID, so it can
                        # be lifted -- at 16:44:07Z on 2026-09-08 it was, for
                        # 0.01 on aec-setkameua-beldmy-dobvol. That is a real
                        # position, and a fresh OPEN preflight requires a flat
                        # account, so falling through here starts a session
                        # that is GUARANTEED to fail preflight on the very
                        # market the probe just unblocked. Stand down for this
                        # cycle instead and let the residual settle under the
                        # ordinary HOLD policy. The fill is NOT promoted into
                        # an L5 opening leg: it keeps relation=None and
                        # l5_action=False in the ledger and stays that way.
                        _not_started("complement_probe_left_residual")
                        _log("complement_probe_residual",
                             chosen=chosen["slug"],
                             filled_qty=probe.get("filled_qty"),
                             treatment="held to settlement as a non-L5 "
                                       "residual; no OPEN attempted on it",
                             l5_action=False)
                        status = "CAMPAIGN_HOLD:POSITION_HELD"
                        break
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
                 flat_verified=rep.get("flat_verified"),
                 fill_reconciliation=rep.get("fill_reconciliation"),
                 filled_actions=_new_fills(rep),
                 submitted=len(rep.get("orders") or []),
                 orders=(rep.get("orders") or [])[-8:],
                 decisions=(rep.get("decisions") or [])[-8:],
                 pair_economics=rep.get("pair_economics"))
            if rep.get("decisions"):
                record_session_report(rep, chosen["slug"], False)
                fidelity_eval(wall(), force=True)
            if _read_halt(rep):
                _log("session_read_halt", slug=chosen["slug"], resumed=False,
                     reason=rep.get("reason"),
                     treatment="venue read failure before any order:"
                               " bounded wait, campaign continues")
                raise base.q1.base.Halt(str(rep.get("reason")))
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
    if clock() >= stop_at and not str(status).startswith("CAMPAIGN_STOP"):
        # LIVE 2026-09-07 14:48Z: the R4 campaign ended at its wall-clock
        # horizon (campaign_s) with nothing resting and the last cycle's
        # status as the only trace; restart is NEVER, so the lane was
        # silently down. A horizon exit is now named in the log and the
        # report (the status keeps the last cycle's value; the process
        # still exits 0 and a redeploy resumes the persisted rung state).
        report["exit_reason"] = {
            "reason": "HORIZON", "campaign_s": campaign_s,
            "last_status": status, "ended_utc": _utc(wall),
            "sessions_this_campaign": len(report["sessions"]) - sessions_base,
            "resume": "redeploy resumes the persisted rung state; "
                      "no approval is re-run"}
        _log("campaign_horizon_reached", **report["exit_reason"])
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


# ---------------------------------------------------------------------------
# OWNER GO 5584132560 items 1 and 3: a clean end must not leave the lane
# down, and it must not destroy the evidence of why it ended.
# ---------------------------------------------------------------------------

# A campaign that ends for one of these reasons has NOT been told to stop:
# the object ended, the shell still permits operating, and leaving the lane
# down until a human notices is the outage we are fixing. Anything not
# listed here -- a loss stop, a halt, an un-durable state, missing
# credentials, foreign open orders, and every owner decision point such as
# CAMPAIGN_RUNG_COMPLETE or a CAMPAIGN_WAITING approval gate -- is NOT
# auto-restarted. Restarting through an approval gate would consume an
# approval the owner has not given.
RECOVERABLE_PREFIXES = ("CAMPAIGN_SETTLED", "CAMPAIGN_RESUMING")
RECOVERABLE_EXACT = (
    "CAMPAIGN_HORIZON",
    # a quiet board is not a reason to end the lane: the campaign simply
    # found nothing to work and should look again
    "CAMPAIGN_NO_CANDIDATE",
    # a venue outage is transient and is exactly the case that must come
    # back by itself. Every preflight and admission gate still runs, and
    # still fails closed, on each attempt
    "CAMPAIGN_STOP:VENUE_UNAVAILABLE",
    # an account-wide flat lock held by a residual is the most ordinary
    # state this lane has: the residual settles by itself, and the resume
    # path exists precisely to continue it. Ending the PROCESS here leaves
    # the lane down until a human notices. The anti-spin guard still fails
    # closed WITH AN ALARM when this status repeats immediately with no new
    # settlement, so a residual that never clears is reported rather than
    # looped on -- which is the behaviour a stuck 0.01 probe fill needs.
    "CAMPAIGN_HOLD:POSITION_HELD",
)

# A campaign that ends almost immediately is spinning; one that ran for a
# while and ended the same way is just a quiet board. Only the first is
# stopped, or a dead overnight slate would put the lane back down -- which
# is the outage this whole change exists to prevent.
MIN_HEALTHY_RUN_S = 120.0

SUPERVISOR_MAX_RESTARTS_ENV = "PMUS_SPRING_MAX_RESTARTS_PER_HOUR"
DEFAULT_MAX_RESTARTS_PER_HOUR = 12
SUPERVISOR_BACKOFF_S = (5.0, 15.0, 30.0, 60.0, 120.0)

# Liveness is POSITIVE evidence that the process is running, not the
# absence of log lines. The 06:30-10:40 outage looked exactly like a quiet
# market from the logs alone; a missing heartbeat cannot.
HEARTBEAT_S = 300.0


def _rss_peak_mb() -> Optional[float]:
    """The process high-water mark. NOT current memory: ru_maxrss is a peak
    and reporting it as live RSS overstates it (the first heartbeat read
    1056.6 while the platform measured 278 MB)."""
    try:
        import resource
        # Linux reports kilobytes
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    except Exception:                                   # noqa: BLE001
        return None


def _rss_now_mb() -> Optional[float]:
    """Current resident set, read from /proc. None where /proc is absent
    rather than a guess."""
    try:
        with open("/proc/self/statm", "r", encoding="ascii") as fh:
            pages = int(fh.read().split()[1])
        return round(pages * os.sysconf("SC_PAGE_SIZE") / (1024.0 ** 2), 1)
    except Exception:                                   # noqa: BLE001
        return None


def is_recoverable(status: str) -> bool:
    s = str(status or "")
    return s.startswith(RECOVERABLE_PREFIXES) or s in RECOVERABLE_EXACT


def compact_end_row(report: Mapping[str, Any], *, restart: str) -> dict:
    """ONE structured line that survives Railway's rate limit, carrying the
    terminal cause the full report used to bury under 100k pretty-printed
    lines (the 06:30Z exit dropped 117,262 messages, its own `status`
    among them). The full report is persisted durably; this is what the
    log has to keep."""
    sessions = report.get("sessions") or []
    settlements = report.get("settlements") or []
    exit_reason = report.get("exit_reason") or {}
    recon = [s.get("fill_reconciliation") for s in sessions
             if isinstance(s, Mapping) and s.get("fill_reconciliation")]
    return {
        "kind": "campaign_end",
        "status": report.get("status"),
        "reason": exit_reason.get("reason") or report.get("reason"),
        "campaign_id": report.get("campaign_id"),
        "sessions": len(sessions),
        "filled_markets": len(report.get("filled_markets") or []),
        "settled": len(settlements),
        "pending": sum(1 for s in settlements
                       if str((s or {}).get("status") or "").startswith("PENDING")),
        "realized_pnl": report.get("realized_pnl"),
        "realized_confirmed_by_venue": bool(report.get("realized_pnl")),
        "daily_stop": report.get("daily_stop_state"),
        "fill_reconciliation": {
            "sessions_with_audit": len(recon),
            "may_assert_flat_all": all(bool(r.get("may_assert_flat"))
                                       for r in recon) if recon else None,
            "recovered_qty": [r.get("recovered_qty") for r in recon
                              if r.get("recovered_qty") not in (None, "0")],
            "contradictions": sum(len(r.get("contradictions") or [])
                                  for r in recon),
        },
        "next": restart,
        "full_report": "persisted to the durable ledger, not to stdout",
    }


def _persist_full_report(report: Mapping[str, Any]) -> Optional[str]:
    """The whole document, durably, exactly once. Printing it is what
    destroyed the terminal evidence; the ledger keeps it instead."""
    try:
        if base.armed() and entry.durable_state_reason() is None:
            base.q1.ledger_append({"kind": "campaign_report", "spring": True,
                                   **report})
            return "ledger"
    except Exception as exc:                            # noqa: BLE001
        _log("campaign_report_persist_failed",
             err=f"{type(exc).__name__}:{str(exc)[:80]}")
    return None


def _run_once() -> dict:
    try:
        return run_campaign()
    except KeyboardInterrupt:
        _log("campaign_interrupted", reason="SIGTERM_or_interrupt")
        return {"campaign": "pmus-l5-continuous-v1",
                "status": "CAMPAIGN_STOP:INTERRUPTED", "realized_pnl": None}


def main(argv: Optional[Sequence[str]] = None,
         *, sleep=time.sleep, clock=time.time) -> int:
    """Supervise the campaign IN THIS PROCESS. One process means a restart
    can never race a still-running one into duplicate orders, and the
    durable campaign state is simply still there.

    The loop refuses to spin: the same recoverable status twice in a row
    with no new settlement between them is treated as stuck and fails
    closed, and restarts are capped per hour."""
    _install_term_handler()
    max_restarts = int(os.getenv(SUPERVISOR_MAX_RESTARTS_ENV,
                                 str(DEFAULT_MAX_RESTARTS_PER_HOUR)))
    started = clock()
    restarts = 0
    last_status = None
    last_settled = -1
    report: dict = {}
    while True:
        cycle_started = clock()
        report = _run_once()
        ran_s = clock() - cycle_started
        status = str(report.get("status", ""))
        settled = len(report.get("settlements") or [])
        _persist_full_report(report)

        if not is_recoverable(status):
            _log(**compact_end_row(
                report, restart="none: this status is an owner decision or a "
                                "stop, and is never auto-restarted"))
            break
        if restarts >= max_restarts and (clock() - started) < 3600:
            _log(**compact_end_row(
                report, restart=f"none: {max_restarts} restarts in the last "
                                f"hour, failing closed rather than spinning"))
            _log("PMUS_SPRING_ALARM", alarm="restart_budget_exhausted",
                 restarts=restarts, status=status)
            break
        if status == last_status and settled == last_settled and \
                ran_s < MIN_HEALTHY_RUN_S:
            _log(**compact_end_row(
                report, restart=(f"none: the same status repeated after "
                                 f"{round(ran_s, 1)}s with no new settlement, "
                                 f"so restarting would spin")))
            _log("PMUS_SPRING_ALARM", alarm="campaign_stuck", status=status)
            break

        wait = SUPERVISOR_BACKOFF_S[min(restarts, len(SUPERVISOR_BACKOFF_S) - 1)]
        _log(**compact_end_row(
            report, restart=f"same process, in {wait}s (restart "
                            f"{restarts + 1} of {max_restarts} this hour)"))
        last_status, last_settled = status, settled
        restarts += 1
        if (clock() - started) >= 3600:
            started, restarts = clock(), 1
        sleep(wait)

    safe_prefix = ("SHADOW_", "CAMPAIGN_")
    ok = str(report.get("status", "")).startswith(safe_prefix) or \
        report.get("status") in ("NO_CREDENTIALS", "DURABLE_STATE_REQUIRED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
