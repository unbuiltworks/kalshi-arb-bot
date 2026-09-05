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
# operator-asserted STRICT replay evidence (issue #167 comment
# 5526683554): must EQUAL the slug, copied from a recorder status row
# whose spring_repeated_completion is true. Absent => admission fails
# closed with spring_completion_not_repeated.
SPRING_STRICT_ENV = "PMUS_SPRING_REPEATED_COMPLETION"
DEADLINE_ENV = "PMUS_SPRING_DEADLINE_SECONDS"      # default 900
SETTLEMENT_ENV = "PMUS_SPRING_SETTLEMENT_TS"       # optional epoch seconds
MAX_ACTIONS_ENV = "PMUS_SPRING_MAX_ACTIONS"        # default 6
STATE_DURABLE_ENV = "PMUS_SPRING_STATE_DURABLE"    # operator override "1"

# Spring gates — measured, from analysis/l5/l5_ramp_analysis.json
SPRING_BAND_LO, SPRING_BAND_HI = D("0.35"), D("0.65")   # OWNER 5533476555
SPRING_MIN_TRADES_1H = 10                # thin-flow floor (gate diagnosis)
# OBSERVATION/target cap per OWNER DECISION 5533476555. Execution stays
# fail-closed on q1.complement_bound (>=1c after known fees) — the
# spring_complement_price min() below never lets this cap admit a pair
# with <1c net edge.
SPRING_PAIR_TARGET = D("0.98")
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


def strict_completion_asserted(slug: str) -> bool:
    """Operator copies the strict-replay verdict — since OWNER DECISION
    5533476555 that means spring_category_bar_met true on the recorder
    status row for THIS slug — into the env; anything else fails closed
    downstream (issue #167 comment 5526683554)."""
    return bool(slug) and os.getenv(SPRING_STRICT_ENV, "").strip() == slug


def state_dir_block_reason() -> Optional[str]:
    """Live spring refuses to arm on ephemeral or unwritable state (issue
    #167 comment 5526791808): the ledger, session records, and the $0.05
    cumulative-notional cap live under PMUS_Q1_STATE_DIR; without a
    durable mount a restart would forget spent notional. Durable =
    the dir (or a parent below /) is an OS mountpoint, or the operator
    explicitly asserts PMUS_SPRING_STATE_DURABLE=1 for hosts where mount
    detection is unreliable. Fail closed on any doubt."""
    d = q1.state_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".durability_probe"
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        return f"state_dir_not_writable:{type(exc).__name__}"
    if os.getenv(STATE_DURABLE_ENV, "").strip() == "1":
        return None
    p = d.resolve()
    while True:
        if os.path.ismount(p):
            return None
        if p.parent == p:
            break
        p = p.parent
        if str(p) == "/":
            break
    return "state_dir_not_durable_no_mount"


class Inventory:
    """Exact-Decimal per-market session inventory.

    Venue quantities/notionals stay exact Decimal everywhere. For the
    BEHAVIORAL model only (issue #167 comment 5520710312), the signed
    residual is normalized to ACTION-UNIT equivalents — the first
    authoritative fill defines one behavioral unit, so a 0.01-contract
    penny fill reads as +/-1 to the historical share-based imbalance
    buckets instead of collapsing to 'flat'; partial fills map
    proportionally and the sign is always preserved. Venue inventory is
    never faked."""

    def __init__(self) -> None:
        self.up = self.dn = D("0")
        self.cost_up = self.cost_dn = D("0")
        self.last_rel: Optional[str] = None
        self.last_px: Optional[D] = None
        self.unit: Optional[D] = None          # first fill = 1 behavioral unit
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

    def behavioral_residual(self) -> float:
        """Signed residual in action-unit equivalents for the historical
        model; proportional for partials, sign-preserving, never int()."""
        if self.unit is None or self.unit == 0:
            return 0.0
        return float(self.residual / self.unit)

    def record(self, side_up: bool, px: D, qty: D, relation: str) -> None:
        if self.unit is None and qty > 0:
            self.unit = qty
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
    # behavioral action-unit residual, NOT raw penny contracts — a 0.01
    # first fill must not read as 'flat' (issue #167 comment 5520710312)
    return state_key(float(px), tte, inv.behavioral_residual())


def spring_cumulative_notional(slug: str) -> D:
    """Durable <=\\$0.05 cumulative SURPLUS-SPEND cap across invocations
    (issue #167 comment 5520710312 item 5): sums this market's submitted
    spring OPEN/EXTEND rows AND micro first-leg rows from the campaign
    ledger — completion legs are excluded, and an unparseable row counts
    as the full cap (fail closed)."""
    total = D("0")
    path = q1.base.state_dir() / "ledger.jsonl"
    if not path.exists():
        return total
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not (isinstance(row, dict) and row.get("kind") == "submitted"
                and row.get("slug") == slug):
            continue
        stage = str(row.get("stage", ""))
        if not (stage.startswith("spring_open")
                or stage.startswith("spring_extend")
                or stage == "micro_first_leg"):
            continue
        price, qty = micro._d(row.get("price")), micro._d(row.get("qty"))
        if price is None or qty is None:
            return SESSION_NOTIONAL_CAP
        total += price * qty
    return total


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
    # The CATEGORY evidence bar (>=2 strict completions from >=2 distinct
    # same-category markets in the rolling hour) used to refuse here.
    # OWNER DECISION 5551236878 removed it from OPEN admission: it is
    # COMPLETION evidence, and requiring proof of past completions before
    # a first entry is circular -- nothing completes if nothing may open.
    # It stays measured on the row and reported by the readiness layer;
    # the safety and provenance intent of 5533476555 is untouched, and
    # <=0.98 / >=1c still bind every complement below.
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
    # spring_watch (issue #167 comment 5521430953) surfaces band-fit
    # markets the small display lists can hide; advisory rows only — the
    # gates below stay authoritative for admission.
    _KEYS = ("eligible_markets", "top_markets", "spring_watch")
    slugs: list[str] = []
    for key in _KEYS:
        for row in status.get(key) or []:
            if isinstance(row, Mapping) and row.get("slug"):
                slugs.append(str(row["slug"]))
    slugs.extend(extra_slugs or [])
    by_slug: dict[str, Mapping] = {}
    for key in _KEYS:  # richer eligible/top rows win over watch rows
        for r in status.get(key) or []:
            if isinstance(r, Mapping) and r.get("slug"):
                by_slug.setdefault(str(r["slug"]), r)
    out = []
    for slug in dict.fromkeys(slugs):
        row = dict(micro.micro_eligible(t, slug))
        srow = by_slug.get(slug) or {}
        row.setdefault("both_sides_served", srow.get("both_sides_served",
                                                     True))
        row["trades_1h"] = srow.get("trades_1h")
        row["two_sided_trades"] = srow.get("two_sided_trades")
        # strict-replay evidence rides with the candidate (issue #167
        # comment 5526683554); a status row without it stays missing and
        # the gate below fails closed on it
        for key in ("spring_repeated_completion", "spring_matched_slots",
                    "spring_slot_counts_theoretical", "spring_pair_target",
                    "spring_category", "spring_category_evidence",
                    "spring_category_bar_met"):
            if key in srow:
                row[key] = srow[key]
        row["spring_skip"] = spring_candidate_gate(row, model)
        out.append(row)
    def _rank(r):
        return (r["spring_skip"] is not None,
                -int(r.get("trades_1h") or 0), str(r["slug"]))
    return sorted(out, key=_rank)


def spring_complement_price(avg_cost: D, opp_ask: D, tick: D) -> \
        tuple[Optional[D], dict]:
    """Maker-only completion price on the deficit side: the tighter of the
    spring pair target (observation cap, pair <= 0.98 per OWNER DECISION
    5533476555) and the hard >=1c-after-known-fees bound, floored to the
    venue tick and strictly below the opposite ask. The hard bound always
    participates in the min(), so the widened observation cap can never
    admit a pair with <1c net edge. The caller's report shows which
    regime was executable."""
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
                wall=time.time, slug: Optional[str] = None,
                gate_ok: bool = False,
                deadline_s: Optional[float] = None) -> dict:
    """One bounded sequential session on ONE market.

    ``slug`` / ``gate_ok`` / ``deadline_s`` exist for the continuous
    selector (OWNER 5552036210): it hands in the market whose fresh
    AUTHENTICATED evidence just passed the strict guard, which IS the
    gate evidence -- so the operator-pasted PMUS_SPRING_GATE_EVIDENCE is
    not required on that path. The dual key is required on every path;
    nothing here can arm.
    """
    slug = (slug if slug is not None
            else os.getenv(SPRING_MARKET_ENV, "")).strip()
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
    if not gate_ok and not gate_asserted(slug):
        return {**report, "status": "GATE_NOT_CLEARED"}
    sd_block = state_dir_block_reason()
    if sd_block:
        # issue #167 comment 5526791808: never run live on ephemeral state
        return {**report, "status": "STATE_NOT_DURABLE", "reason": sd_block,
                "required_action":
                "attach an isolated Railway volume mounted at the spring "
                "service's PMUS_Q1_STATE_DIR (/state/pmus-spring); do not "
                "share the recorder volume"}
    if q1.realized_today() <= q1.DAILY_LOSS_STOP:
        return {**report, "status": "DAILY_LOSS_STOP"}

    t = t or Transport()
    if deadline_s is None:
        deadline_s = float(os.getenv(DEADLINE_ENV, "900"))
    max_actions = int(os.getenv(MAX_ACTIONS_ENV, "6"))   # probe-1 bound
    # (default 6 per issue #167 comment 5525966568)
    # (issue #167 comment 5520665580 item 8: max 5 incremental actions
    # OR the $0.05 cumulative cap, whichever hits first)
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
        row = dict(micro.micro_eligible(t, slug))
        if strict_completion_asserted(slug):
            row["spring_category_bar_met"] = True
        skip = spring_candidate_gate(row, model)
        if skip:
            return {**report, "status": "SPRING_SKIP_NOT_QUALIFYING",
                    "reason": skip, "candidate_row": row}
        cum = spring_cumulative_notional(slug)
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
            # behavioral units, sign preserved — never int() a fractional
            # venue residual (issue #167 comment 5520710312)
            prop = pv2.proposal(model, skey, inv.last_rel or "open", "q<5",
                                residual=inv.behavioral_residual())
            relation = ("open" if inv.up + inv.dn == 0
                        else prop["recommended_relation"])
            off = bool(prop.get("off_distribution"))
            if off and inv.up + inv.dn > 0:
                # OWNER 5552036210 item 4: the policy is distributional; a
                # state the model never saw gets a LOGGED refusal and a
                # hold, never a substituted deterministic rule. Nothing is
                # flattened -- the residual holds to settlement.
                relation = "hold"
            # model probability and rank for EVERY decision (item 4)
            report.setdefault("decisions", []).append(
                {"state": skey, "last_rel": inv.last_rel,
                 "v2_recommended": prop["recommended_relation"],
                 "relation_ranked": prop.get("relation_ranked"),
                 "state_n": prop.get("state_n"),
                 "off_distribution": off,
                 "persist_extend_p": prop["persist_extend_p"],
                 "switch_complete_p": prop["switch_complete_p"],
                 "acted": relation,
                 "refusal": ("off_distribution_hold" if relation == "hold"
                             else None)})
            if relation == "hold":
                sleep(max(prop["next_reevaluation_s"], MIN_REEVAL_S))
                actions += 1
                continue

            if relation in ("open", "extend"):
                # spend caps: session + durable cumulative, before sizing
                if cum + inv.spent() + spec["entry_notional"] > \
                        SESSION_NOTIONAL_CAP:
                    relation = "complete" if inv.residual != 0 else "stop"
            if relation == "sell" or relation == "stop":
                break

            if relation in ("open", "extend"):
                if inv.residual == 0:
                    # the frozen model predicts a RELATION, never a side;
                    # the SHARED adapter (cheaper touch, ties -> YES) is
                    # what the recorder advertises as probe_entry_action,
                    # so the live open is the advertised open
                    from src import pmus_sports_l5_decision as l5d
                    side_up = l5d.open_side_yes(
                        {"bid": bbo["bid"], "ask": bbo["ask"]})
                    if side_up is None:
                        break
                else:
                    side_up = inv.residual > 0     # extend the surplus side
                px = bbo["bid"] if side_up else (q1.ONE - bbo["ask"])
                px = (px / spec["tick"]).to_integral_value(
                    rounding=ROUND_DOWN) * spec["tick"]
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
