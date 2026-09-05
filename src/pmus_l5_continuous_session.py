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
from decimal import Decimal as D
from typing import Any, Mapping, Optional

from src import pmus_spring_l5_probe_entry as entry
from src import pmus_spring_l5_session as base
from src import pmus_l5_persistence_v2 as pv2
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
# not fatal; sustained failure inside the window stops the campaign closed.
ITERATION_ERROR_BACKOFF_S = 20.0
ITERATION_ERROR_WINDOW_S = 1800.0
MAX_ITERATION_ERRORS = 45            # ~15 min of continuous failure
RUNG_MARKETS_ENV = "PMUS_SPRING_RUNG_MARKETS"
DEFAULT_RUNG_MARKETS = 3
SPORTS_TTE_DEFAULT_S = base.SPORTS_TTE_DEFAULT_S
LABEL_SHADOW = "SHADOW/THEORETICAL; DISARMED; no venue network"


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
               "position_net": result.get("position_net"),
               "resolution": result.get("resolution"),
               "ledger_rows_written": written}
        if result.get("settled"):
            out["breakdown"] = settlement_breakdown(slug, result)
            if written:      # once, alongside the venue-confirmed rows
                base.q1.ledger_append({"kind": "settlement_breakdown",
                                       "spring": True, **out["breakdown"]})
        return out
    except Exception as exc:                       # noqa: BLE001
        return {"status": "AUDIT_UNAVAILABLE",
                "error": f"{type(exc).__name__}:{str(exc)[:80]}"}


SETTLE_AUDIT_EVERY_S = 60.0


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
    settled_here: set = set()
    iteration_errors: list = []          # clock() stamps of recent errors
    rung_markets = max(1, int(os.getenv(RUNG_MARKETS_ENV,
                                        str(DEFAULT_RUNG_MARKETS))))
    filled_markets: set = set()          # markets with a fill this campaign
    report["rung"] = {
        "markets_allowed": rung_markets,
        "per_market_cap_usd": str(base.SESSION_NOTIONAL_CAP),
        "worst_case_usd": str(base.SESSION_NOTIONAL_CAP * rung_markets),
        "daily_loss_stop_usd": str(base.q1.DAILY_LOSS_STOP),
        "concurrency": 1,
    }

    def _rung_gate() -> Optional[str]:
        """After the rung's markets are all flat/settled: stop at the
        ADVANCE GATE with what the next rung decision needs."""
        if len(filled_markets) < rung_markets:
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
        return "CAMPAIGN_RUNG_COMPLETE"
    # OWNER 5552841170 #4: throughput funnel, accumulated for the campaign
    # and printed per selection cycle
    funnel: dict[str, Any] = {"cycles": 0, "universe_rows": 0,
                              "in_distribution": 0, "admitted": 0,
                              "sessions": 0, "submitted": 0, "filled": 0,
                              "refusals": {}}
    report["funnel"] = funnel
    _log("campaign_start", mode=report["mode"], campaign_s=campaign_s,
         campaign_expires_at=wall() + campaign_s,
         max_sessions=MAX_SESSIONS_PER_CAMPAIGN,
         per_market_cap=str(base.SESSION_NOTIONAL_CAP))
    while clock() < stop_at and len(report["sessions"]) < \
            MAX_SESSIONS_PER_CAMPAIGN + resumes:
        try:
            now = wall()
            if armed:
                # OWNER 5552607804: a market we already hold is CONTINUED
                # (EXTEND / COMPLEMENT / HOLD from the model on the real
                # inventory) and no other market is started while it exists.
                held = held_market(t)
                if held == MULTI_HELD:
                    status = f"CAMPAIGN_STOP:{MULTI_HELD}"
                    break
                if held in settled_here:
                    # the venue already confirmed this market's resolution;
                    # a lingering positions row is not inventory to act on
                    held = None
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
                        report.setdefault("settlements", []).append(
                            {"slug": slug, **audit})
                        if audit.get("status") == "REALIZED":
                            _log("REALIZED", slug=slug,
                                 realized_usd=audit.get("realized_usd"),
                                 resolution=audit.get("resolution"))
                            report["realized_pnl"] = _sum_realized(report)
                if held:
                    filled_markets.add(held)
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
                         filled_actions=_new_fills(rep),
                         submitted=len(rep.get("orders") or []),
                         orders=(rep.get("orders") or [])[-8:],
                         decisions=(rep.get("decisions") or [])[-8:],
                         pair_economics=rep.get("pair_economics"))
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
                            if gate:
                                status = gate
                                break
                            continue
                    status = f"CAMPAIGN_RESUMING:{rep.get('status')}"
                    woke = await_state_change(t, held, clock, sleep)
                    _log("resume_wait", slug=held, **woke)
                    continue
            sel = select_candidate(now, model, cooldown)
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
            _log("campaign_iteration_error",
                 err=f"{type(exc).__name__}:{str(exc)[:160]}",
                 errors_in_window=len(iteration_errors),
                 max_in_window=MAX_ITERATION_ERRORS)
            if len(iteration_errors) >= MAX_ITERATION_ERRORS:
                status = "CAMPAIGN_STOP:VENUE_UNAVAILABLE"
                break
            sleep(ITERATION_ERROR_BACKOFF_S)
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
