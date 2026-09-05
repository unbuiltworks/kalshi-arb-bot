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
from typing import Any, Mapping, Optional

from src import pmus_spring_l5_probe_entry as entry
from src import pmus_spring_l5_session as base
from src import pmus_l5_persistence_v2 as pv2
from src.polymarket_l5.action_model import state_key

CAMPAIGN_SECONDS_ENV = "PMUS_SPRING_CAMPAIGN_SECONDS"   # default 3600
DEFAULT_CAMPAIGN_S = 3600.0
SELECT_POLL_S = 15.0            # recorder status cadence is ~20s
SESSION_DEADLINE_S = 900.0      # per-market bound, as the one-shot probe
RETRY_COOLDOWN_S = 120.0        # a market that just refused/no-filled waits
MAX_SESSIONS_PER_CAMPAIGN = 8   # bounded reselection, not a runaway
MAX_SESSIONS_PER_SLUG = 3       # attempt cap per market (x6 orders each)
SPORTS_TTE_DEFAULT_S = base.SPORTS_TTE_DEFAULT_S
LABEL_SHADOW = "SHADOW/THEORETICAL; DISARMED; no venue network"


def _log(kind: str, **fields: Any) -> None:
    print("PMUS_L5_CAMPAIGN " + json.dumps({"kind": kind, **fields},
                                           separators=(",", ":"),
                                           default=str), flush=True)


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


def rank_candidates(status: Mapping[str, Any], now: float,
                    model: Optional[Mapping[str, Any]] = None,
                    exclude: Optional[Mapping[str, float]] = None) -> dict:
    """Every spring_watch row through the strict guard; admitted rows
    ranked. Returns {"admitted": [...], "refused": [...]}."""
    from src import pmus_spring_evidence_transfer as xfer
    exclude = exclude or {}
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
                **mv}
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
    admitted.sort(key=lambda x: (x["off_distribution"], -x["state_n"],
                                 -x["p_buy"], _gap(x), -_touch(x), x["slug"]))
    # OWNER 5552475548 item 4: prioritise live CFB when its books are
    # active, unless another market has MATERIALLY better L5-state fit and
    # executable flow (twice the model evidence AND at least the depth).
    cfb = [x for x in admitted if _is_cfb(x["slug"])]
    if cfb and admitted[0] is not cfb[0]:
        best, top_cfb = admitted[0], cfb[0]
        materially_better = (
            best["off_distribution"] is False
            and best["state_n"] >= 2 * max(top_cfb["state_n"], 1)
            and _touch(best) >= _touch(top_cfb))
        if not materially_better:
            admitted.remove(top_cfb)
            admitted.insert(0, top_cfb)
    for i, x in enumerate(admitted, 1):
        x["rank"] = i
        x["cfb"] = _is_cfb(x["slug"])
    return {"admitted": admitted, "refused": refused,
            "status_t": status.get("t")}


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
    ranking = rank_candidates(status, now, model, exclude)
    chosen = ranking["admitted"][0] if ranking["admitted"] else None
    _log("selection", source=source, status_t=ranking["status_t"],
         evidence_age_s=(round(now - entry._epoch(ranking["status_t"]), 3)
                         if entry._epoch(ranking["status_t"]) else None),
         n_rows=len(ranking["admitted"]) + len(ranking["refused"]),
         chosen=(chosen["slug"] if chosen else None),
         admitted=[{k: x.get(k) for k in ("rank", "slug", "bid", "ask",
                                          "bid_touch_qty", "state",
                                          "state_n", "p_buy",
                                          "relation_ranked",
                                          "probe_entry_action")}
                   for x in ranking["admitted"]],
         refused=[{"slug": x["slug"], "bid": x["bid"],
                   "reason": x["reason"], "state_n": x["state_n"]}
                  for x in ranking["refused"]])
    return {"chosen": chosen, "source": source, **ranking}


RESUME_REEVAL_S = 30.0          # between resume sessions on a held market
MULTI_HELD = "MULTIPLE_POSITIONS"


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
    start = clock()
    stop_at = start + campaign_s
    cooldown: dict[str, float] = {}
    sessions_on: dict[str, int] = {}
    status = "CAMPAIGN_NO_CANDIDATE"
    resumes = 0
    while clock() < stop_at and len(report["sessions"]) < \
            MAX_SESSIONS_PER_CAMPAIGN + resumes:
        now = wall()
        if armed:
            # OWNER 5552607804: a market we already hold is CONTINUED
            # (EXTEND / COMPLEMENT / HOLD from the model on the real
            # inventory) and no other market is started while it exists.
            held = held_market(t)
            if held == MULTI_HELD:
                status = f"CAMPAIGN_STOP:{MULTI_HELD}"
                break
            if held:
                _log("resume_start", slug=held)
                rep = dict(session_runner(
                    t, clock=clock, sleep=sleep, wall=wall, slug=held,
                    gate_ok=True,
                    deadline_s=min(SESSION_DEADLINE_S, stop_at - clock()),
                    resume=True) or {})
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
                     filled_actions=len(rep.get("actions") or []),
                     submitted=len(rep.get("orders") or []),
                     orders=(rep.get("orders") or [])[-8:],
                     decisions=(rep.get("decisions") or [])[-8:],
                     pair_economics=rep.get("pair_economics"))
                if rep.get("status") in ("DAILY_LOSS_STOP", "HALT",
                                         "STATE_NOT_DURABLE",
                                         "NO_CREDENTIALS", "DISARMED"):
                    status = f"CAMPAIGN_STOP:{rep.get('status')}"
                    break
                status = f"CAMPAIGN_RESUMING:{rep.get('status')}"
                sleep(RESUME_REEVAL_S)
                continue
        sel = select_candidate(now, model, cooldown)
        report["selections"] += 1
        chosen = sel["chosen"]
        if chosen is None:
            sleep(SELECT_POLL_S)
            continue
        if not armed:
            report["last_shadow"] = {"chosen": chosen,
                                     "label": LABEL_SHADOW}
            status = "SHADOW_SELECTED"
            sleep(SELECT_POLL_S)
            continue
        remaining = stop_at - clock()
        _log("session_start", slug=chosen["slug"], rank=chosen["rank"],
             state=chosen["state"], state_n=chosen["state_n"],
             p_buy=chosen["p_buy"],
             probe_entry_action=chosen.get("probe_entry_action"))
        rep = session_runner(t, clock=clock, sleep=sleep, wall=wall,
                             slug=chosen["slug"], gate_ok=True,
                             deadline_s=min(SESSION_DEADLINE_S, remaining))
        rep = dict(rep or {})
        rep["selected"] = {k: chosen.get(k) for k in
                           ("rank", "state", "state_n", "p_buy",
                            "relation_ranked", "bid", "ask",
                            "bid_touch_qty")}
        report["sessions"].append(rep)
        _log("session_end", slug=chosen["slug"], status=rep.get("status"),
             reason=rep.get("reason"), stop_reason=rep.get("stop_reason"),
             inventory=rep.get("inventory"),
             spring_gates=rep.get("spring_gates"),
             filled_actions=len(rep.get("actions") or []),
             submitted=len(rep.get("orders") or []),
             orders=(rep.get("orders") or [])[-8:],
             decisions=(rep.get("decisions") or [])[-8:],
             pair_economics=rep.get("pair_economics"))
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
            # one market at a time; residual holds to settlement
            status = f"CAMPAIGN_HOLD:{rep.get('status')}"
            break
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
    return {**report, "status": status}


def main() -> int:
    report = run_campaign()
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
