"""SPRING-L5 one-market session layer — DISARMED BY DEFAULT.

Issue #167 comment 5520504343 over `L5_OPERATING_MODEL.md`: run the
SPRING-era L5 policy (the measured Apr 6-10 patience discipline from
`analysis/l5/l5_ramp_analysis.json`, ramp commit ef1da90a) at penny
scale on one PMUS sports/props market — persistent incremental
inventory, NOT the August/rebate-era behavior.

One invocation == one bounded session on ONE market:

    maker OPEN (~$0.01) -> reassess on persistence-v2 cadence ->
    optional same-side EXTEND (~$0.01, maker) -> reassess ->
    patient fee-safe COMPLETE on the deficit side only ->
    settle/audit (realized stays null until authoritative settlement)

Spring constraints (constants, not knobs — from the ramp analysis):
* ONE market; no concurrency; no auto-restart or second campaign; the
  account-wide flat/open-order gate runs before the first mutation.
* Mid-band spring entry only: the session refuses a market whose YES bid
  is outside the ~45-55c modal spring band, has one-sided/thin recent
  flow, or whose persistence-v2 state is off-distribution. No qualifying
  candidate -> SKIP; gates are never lowered to manufacture a trade.
* Penny actions through the canonical micro executor primitives: venue
  minimumTradeQty/tick read live, ~$0.01 target per action, <=$0.02
  worst-case either-leg per action, exact Decimal quantities end-to-end.
* MAKER ONLY. There is no taker code path in spring mode at all.
* Pair economics expose BOTH bounds: `spring_pair_target` (completed
  pair <= 0.96, the measured pre-rebate spring regime) and the hard
  safety bound (>=1c locked after fees). Completion prices at the
  tighter of the two when executable; the report shows when only the
  hard bound was achievable. Rebates/rewards are never counted.
* Patience: no forced completion at an arbitrary 300s mark — the session
  reevaluates on the persistence-v2 cadence and book changes, tolerates
  bounded imbalance, and stops before settlement when a settlement clock
  is provided (and at the session deadline regardless).
* Hard caps: cumulative first-leg/EXTEND notional <= $0.05 per market
  per session AND across invocations via the durable ledger; ONE
  outstanding order at a time; exact-ID cancel + market-scoped deadman;
  mutations never replayed.

Arming is ISOLATED dual-key — none of the q1, v4, or micro keys can arm
this layer and this layer's keys arm nothing else:
  PMUS_SPRING_LIVE=YES
  PMUS_SPRING_SESSION_ID=<fresh id> and PMUS_SPRING_ARM=<same id>
  PMUS_SPRING_MARKET_SLUG=<slug> and PMUS_SPRING_GATE_EVIDENCE=<same>
Optional: PMUS_SPRING_DEADLINE_SECONDS (default 900),
PMUS_SPRING_SETTLEMENT_TS (epoch; stop STOP_BEFORE_SETTLEMENT_S ahead),
PMUS_SPRING_MAX_ACTIONS (default 6).

Candidate selection stays READ-ONLY and operator-reviewed:
`rank_spring_candidates(t, status)` scans the recorder's full-slate
status (eligible + top rows + any extra slugs) through `micro_eligible`
and the spring gates and returns a ranked list; the ARMED run then takes
the operator-chosen slug via env, exactly like every prior probe.

Disarmed dry-run (safe anywhere, no network):
    python3 -m src.pmus_spring_l5_session
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal as D
from typing import Any, Mapping, Optional

from src import pmus_micro_executor as micro
from src import pmus_l5_persistence_v2 as pv2
from src.polymarket_l5.action_model import state_key

q1 = micro.q1
Transport = micro.Transport
Halt = micro.Halt
RejectedNoFill = micro.RejectedNoFill

SPRING_LIVE_ENV = "PMUS_SPRING_LIVE"
SPRING_SID_ENV = "PMUS_SPRING_SESSION_ID"
SPRING_ARM_ENV = "PMUS_SPRING_ARM"
SPRING_MARKET_ENV = "PMUS_SPRING_MARKET_SLUG"
SPRING_GATE_ENV = "PMUS_SPRING_GATE_EVIDENCE"
DEADLINE_ENV = "PMUS_SPRING_DEADLINE_SECONDS"      # default 900
SETTLEMENT_ENV = "PMUS_SPRING_SETTLEMENT_TS"       # optional epoch seconds
MAX_ACTIONS_ENV = "PMUS_SPRING_MAX_ACTIONS"        # default 6

# Spring gates — measured, from analysis/l5/l5_ramp_analysis.json
SPRING_BAND_LO, SPRING_BAND_HI = D("0.45"), D("0.55")   # modal spring entry
SPRING_MIN_TRADES_1H = 10                # thin-flow floor (gate diagnosis)
SPRING_PAIR_TARGET = D("0.96")           # pre-rebate spring pair regime
SESSION_NOTIONAL_CAP = micro.PER_MARKET_FIRST_LEG_CAP    # $0.05
STOP_BEFORE_SETTLEMENT_S = 120.0
SPORTS_TTE_DEFAULT_S = 1e9               # no settlement clock -> coarsest
MIN_REEVAL_S = 2.0


def armed() -> bool:
    """Isolated dual-key: q1/v4/micro keys can never arm spring and the
    spring keys arm nothing else."""
    sid = os.getenv(SPRING_SID_ENV, "").strip()
    return (os.getenv(SPRING_LIVE_ENV, "").strip().upper() == "YES"
            and bool(sid) and os.getenv(SPRING_ARM_ENV, "").strip() == sid)


def gate_asserted(slug: str) -> bool:
    return bool(slug) and os.getenv(SPRING_GATE_ENV, "").strip() == slug


class Inventory:
    """Exact-Decimal per-market session inventory."""

    def __init__(self) -> None:
        self.up = self.dn = D("0")
        self.cost_up = self.cost_dn = D("0")
        self.last_rel: Optional[str] = None
        self.last_px: Optional[D] = None
        self.actions: list[dict] = []

    @property
    def residual(self) -> D:
        return self.up - self.dn

    @property
    def matched(self) -> D:
        return min(self.up, self.dn)

    def avg_up(self) -> Optional[D]:
        return (self.cost_up / self.up) if self.up else None

    def avg_dn(self) -> Optional[D]:
        return (self.cost_dn / self.dn) if self.dn else None

    def spent(self) -> D:
        return self.cost_up + self.cost_dn

    def record(self, side_up: bool, px: D, qty: D, relation: str) -> None:
        if side_up:
            self.up += qty
            self.cost_up += px * qty
        else:
            self.dn += qty
            self.cost_dn += px * qty
        self.last_rel = relation
        self.last_px = px if side_up else (q1.ONE - px)
        self.actions.append({
            "t": datetime.now(timezone.utc).isoformat(),
            "relation": relation, "side": "YES" if side_up else "NO",
            "px": str(px), "qty": str(qty), "notional": str(px * qty),
            "inventory_after": self.snapshot(),
        })

    def snapshot(self) -> dict:
        return {"up": str(self.up), "dn": str(self.dn),
                "matched": str(self.matched),
                "residual": str(self.residual),
                "avg_up": str(self.avg_up()) if self.up else None,
                "avg_dn": str(self.avg_dn()) if self.dn else None,
                "spent": str(self.spent())}


def spring_state_key(inv: Inventory, market_px: D,
                     settlement_t: Optional[float], now: float) -> str:
    tte = (max(settlement_t - now, 0.0) if settlement_t
           else SPORTS_TTE_DEFAULT_S)
    px = inv.last_px if inv.last_px is not None else market_px
    return state_key(float(px), tte, float(inv.residual))


def spring_candidate_gate(row: Mapping[str, Any],
                          model: Optional[Mapping[str, Any]] = None) -> \
        Optional[str]:
    """Why a market does NOT qualify for a spring session (None = fits).
    Gates are constants; they are never lowered to manufacture a trade."""
    if row.get("skip_reason"):
        return f"micro:{row['skip_reason']}"
    bid = row.get("bid")
    if bid is None:
        return "no_bid"
    b = D(str(bid))
    if not (SPRING_BAND_LO <= b <= SPRING_BAND_HI):
        return f"outside_spring_band:{b}"
    if not row.get("both_sides_served", True):
        return "one_sided_book"
    trades = row.get("trades_1h")
    if trades is not None and int(trades) < SPRING_MIN_TRADES_1H:
        return f"thin_flow:{trades}"
    if row.get("two_sided_trades") is False:
        return "one_directional_flow"
    if model is not None:
        d = pv2.v2_dist(model, state_key(float(b), SPORTS_TTE_DEFAULT_S,
                                         0.0), "open", "q<5")
        if d["backed_off"]:
            return "v2_off_distribution"
    return None


def rank_spring_candidates(t: Transport,
                           status: Mapping[str, Any],
                           extra_slugs: Optional[list[str]] = None) -> list[dict]:
    """READ-ONLY full-universe ranking: every market the recorder status
    carries (eligible + display rows) plus any extra slugs, through
    micro_eligible and the spring gates. GET calls only."""
    model = None
    try:
        model = pv2.load_v2()
    except Exception:
        pass
    slugs: list[str] = []
    for key in ("eligible_markets", "top_markets"):
        for row in status.get(key) or []:
            if isinstance(row, Mapping) and row.get("slug"):
                slugs.append(str(row["slug"]))
    slugs.extend(extra_slugs or [])
    by_slug = {r["slug"]: r for key in ("eligible_markets", "top_markets")
               for r in (status.get(key) or []) if isinstance(r, Mapping)}
    out = []
    for slug in dict.fromkeys(slugs):
        row = dict(micro.micro_eligible(t, slug))
        srow = by_slug.get(slug) or {}
        row.setdefault("both_sides_served", srow.get("both_sides_served",
                                                     True))
        row["trades_1h"] = srow.get("trades_1h")
        row["two_sided_trades"] = srow.get("two_sided_trades")
        row["spring_skip"] = spring_candidate_gate(row, model)
        out.append(row)
    def _rank(r):
        return (r["spring_skip"] is not None,
                -int(r.get("trades_1h") or 0), str(r["slug"]))
    return sorted(out, key=_rank)


def spring_complement_price(avg_cost: D, opp_ask: D, tick: D) -> \
        tuple[Optional[D], dict]:
    """Maker-only completion price on the deficit side: the tighter of the
    spring pair target (pair <= 0.96) and the hard >=1c-after-fee bound,
    floored to the venue tick and strictly below the opposite ask. The
    caller's report shows which regime was executable."""
    hard = q1.complement_bound(avg_cost)
    spring = SPRING_PAIR_TARGET - D(str(avg_cost))
    info = {"spring_pair_target": str(SPRING_PAIR_TARGET),
            "spring_bound_px": str(spring),
            "hard_bound_px": str(hard) if hard is not None else None}
    if hard is None:
        return None, {**info, "regime": "no_fee_safe_complement"}
    px = min(spring, D(str(hard)), D(str(opp_ask)) - tick)
    px = (px / tick).to_integral_value(rounding=ROUND_DOWN) * tick
    if px < q1.MIN_PRICE or px > q1.MAX_PRICE or px <= 0:
        return None, {**info, "regime": "no_legal_maker_price"}
    regime = "spring_target" if px <= spring else "hard_bound_only"
    return px, {**info, "regime": regime, "chosen_px": str(px)}


def _await_order(t: Transport, oid: str, qty: D, until: float,
                 clock, sleep) -> D:
    """One outstanding order: wait, then cancel-and-harvest. Returns the
    exact authoritative filled Decimal quantity (0 for none)."""
    fill = None
    try:
        fill = micro.wait_for_fill(t, oid, qty, until, clock, sleep)
    except RejectedNoFill:
        return D("0")
    if fill is None:
        try:
            q1.cancel_exact(t, oid)
        except RejectedNoFill:
            return D("0")
        except Exception:
            pass
        fill = micro._terminal_positive_fill(t, oid, qty)
    return D(str(fill["qty"])) if fill else D("0")


def run_session(t: Optional[Transport] = None,
                clock=time.monotonic, sleep=time.sleep,
                wall=time.time) -> dict:
    slug = os.getenv(SPRING_MARKET_ENV, "").strip()
    report: dict[str, Any] = {
        "session": "pmus-spring-l5-v1", "slug": slug, "realized_pnl": None,
        "labels": ("SPRING discipline (ramp ef1da90a): maker-only, penny "
                   "actions, pre-rebate economics; submitted->filled->"
                   "settled->realized; rebates never counted"),
    }
    if not armed():
        return {**report, "status": "DISARMED",
                "note": "isolated PMUS_SPRING_* dual-key absent/mismatched; "
                        "no network activity (q1/micro keys never arm this)"}
    if not q1.has_credentials():
        return {**report, "status": "NO_CREDENTIALS"}
    if not gate_asserted(slug):
        return {**report, "status": "GATE_NOT_CLEARED"}
    if q1.realized_today() <= q1.DAILY_LOSS_STOP:
        return {**report, "status": "DAILY_LOSS_STOP"}

    t = t or Transport()
    deadline_s = float(os.getenv(DEADLINE_ENV, "900"))
    max_actions = int(os.getenv(MAX_ACTIONS_ENV, "6"))
    settle_ts = os.getenv(SETTLEMENT_ENV, "").strip()
    settlement_t = float(settle_ts) if settle_ts else None
    start = clock()
    stop_at = start + deadline_s
    inv = Inventory()
    reached_order_stage = False

    def out_of_time() -> bool:
        if clock() >= stop_at:
            return True
        return bool(settlement_t and
                    wall() >= settlement_t - STOP_BEFORE_SETTLEMENT_S)

    try:
        model = pv2.load_v2()
        block = micro.account_block_reason(t)
        if block:
            return {**report, "status": "PREFLIGHT_FAILED", "reason": block}
        row = micro.micro_eligible(t, slug)
        skip = spring_candidate_gate(row, model)
        if skip:
            return {**report, "status": "SPRING_SKIP_NOT_QUALIFYING",
                    "reason": skip, "candidate_row": row}
        cum = micro.cumulative_first_leg_notional(slug)
        report["spring_gates"] = {
            "band": f"{SPRING_BAND_LO}-{SPRING_BAND_HI}",
            "pair_target": str(SPRING_PAIR_TARGET),
            "session_notional_cap": str(SESSION_NOTIONAL_CAP),
            "prior_cumulative_notional": str(cum),
        }

        actions = 0
        while actions < max_actions and not out_of_time():
            bbo = q1.outcome_bbo(t, slug, "OUTCOME_SIDE_YES")
            spec = micro.market_micro_spec(t, slug, bbo["bid"])
            skey = spring_state_key(inv, bbo["bid"], settlement_t, wall())
            prop = pv2.proposal(model, skey, inv.last_rel or "open", "q<5",
                                residual=int(inv.residual.copy_abs()
                                             .to_integral_value()) *
                                (1 if inv.residual >= 0 else -1))
            relation = ("open" if inv.up + inv.dn == 0
                        else prop["recommended_relation"])
            report.setdefault("decisions", []).append(
                {"state": skey, "last_rel": inv.last_rel,
                 "v2_recommended": prop["recommended_relation"],
                 "persist_extend_p": prop["persist_extend_p"],
                 "switch_complete_p": prop["switch_complete_p"],
                 "acted": relation})

            if relation in ("open", "extend"):
                # spend caps: session + durable cumulative, before sizing
                if cum + inv.spent() + spec["entry_notional"] > \
                        SESSION_NOTIONAL_CAP:
                    relation = "complete" if inv.residual != 0 else "stop"
            if relation == "sell" or relation == "stop":
                break

            if relation in ("open", "extend"):
                side_up = inv.residual >= 0
                px = bbo["bid"] if side_up else (q1.ONE - bbo["ask"])
                if not q1.entry_ok(bbo["bid"]) or \
                        not (SPRING_BAND_LO <= bbo["bid"] <= SPRING_BAND_HI):
                    break                       # band left us: stop extending
                qty = spec["qty"]
                oid = micro.place_limit_buy(
                    t, slug, "OUTCOME_SIDE_YES" if side_up
                    else "OUTCOME_SIDE_NO", px, qty,
                    f"spring_{relation}_{actions + 1}", post_only=True)
                reached_order_stage = True
                got = _await_order(
                    t, oid, qty,
                    min(stop_at, clock() + max(
                        prop["next_reevaluation_s"], MIN_REEVAL_S)),
                    clock, sleep)
                if got > 0:
                    fill = micro._terminal_positive_fill(t, oid, qty) or \
                        {"price": px, "qty": got}
                    inv.record(side_up, D(str(fill["price"])), got, relation)
            else:                               # complete: deficit side only
                if inv.residual == 0:
                    break
                deficit_up = inv.residual < 0
                avg = inv.avg_dn() if deficit_up else inv.avg_up()
                # completing YES-deficit means buying YES; price the pair
                # against the average cost of the SURPLUS side
                surplus_avg = inv.avg_up() if not deficit_up else inv.avg_dn()
                opp = q1.outcome_bbo(
                    t, slug,
                    "OUTCOME_SIDE_YES" if deficit_up else "OUTCOME_SIDE_NO")
                px, econ = spring_complement_price(
                    surplus_avg, opp["ask"], spec["tick"])
                report["pair_economics"] = econ
                if px is None:
                    sleep(MIN_REEVAL_S)
                    actions += 1
                    continue
                qty = min(inv.residual.copy_abs(), spec["qty"])
                oid = micro.place_limit_buy(
                    t, slug, "OUTCOME_SIDE_YES" if deficit_up
                    else "OUTCOME_SIDE_NO", px, qty,
                    f"spring_complete_{actions + 1}", post_only=True)
                reached_order_stage = True
                got = _await_order(
                    t, oid, qty,
                    min(stop_at, clock() + max(
                        prop["next_reevaluation_s"], MIN_REEVAL_S)),
                    clock, sleep)
                if got > 0:
                    fill = micro._terminal_positive_fill(t, oid, qty) or \
                        {"price": px, "qty": got}
                    inv.record(deficit_up, D(str(fill["price"])), got,
                               "complete")
            actions += 1

        status = ("SPRING_MATCHED" if inv.matched > 0 and inv.residual == 0
                  else ("SPRING_RESIDUAL_HOLD" if inv.up + inv.dn > 0
                        else "SPRING_NO_FILL"))
        pair = (str(inv.avg_up() + inv.avg_dn())
                if inv.matched > 0 and inv.up and inv.dn else None)
        return {**report, "status": status, "actions": inv.actions,
                "inventory": inv.snapshot(),
                "pair_unit_cost": pair,
                "settlement": "PENDING — realized only after authoritative "
                              "settlement reconcile"}
    except Halt as halt:
        return {**report, "status": "SPRING_SAFE_REFUSAL",
                "reason": str(halt), "inventory": inv.snapshot(),
                "actions": inv.actions}
    except Exception as exc:
        q1.ledger_append({"kind": "halt", "spring": True,
                          "reason": f"{type(exc).__name__}:{str(exc)[:120]}"})
        return {**report, "status": "HALT",
                "reason": f"{type(exc).__name__}:{str(exc)[:120]}",
                "inventory": inv.snapshot(), "actions": inv.actions}
    finally:
        if t is not None and slug and reached_order_stage:
            q1.cancel_all_deadman(t, slug)


def main() -> int:
    report = run_session()
    print(json.dumps(report, indent=1, default=str))
    q1.ledger_append({"kind": "session_report", "spring": True, **report})
    safe = {"SPRING_MATCHED", "SPRING_RESIDUAL_HOLD", "SPRING_NO_FILL",
            "SPRING_SKIP_NOT_QUALIFYING", "SPRING_SAFE_REFUSAL",
            "DISARMED", "GATE_NOT_CLEARED", "NO_CREDENTIALS",
            "PREFLIGHT_FAILED", "DAILY_LOSS_STOP"}
    return 0 if report.get("status") in safe else 1


if __name__ == "__main__":
    raise SystemExit(main())
