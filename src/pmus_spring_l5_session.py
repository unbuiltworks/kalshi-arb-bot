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
from src import pmus_l5_fidelity_live as fid
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
    # OWNER 5552475548 item 3: exposure is what FILLED, not what was
    # submitted. A submission counts only until it has a terminal row
    # (authoritative filled quantity, 0 included); a submission the venue
    # never confirmed keeps counting at its full notional -- fail closed.
    total = D("0")
    path = q1.base.state_dir() / "ledger.jsonl"
    if not path.exists():
        return total
    submitted: dict[str, dict] = {}
    terminal: dict[str, dict] = {}
    for i, line in enumerate(path.read_text().splitlines()):
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not (isinstance(row, dict) and row.get("slug") == slug):
            continue
        kind = row.get("kind")
        stage = str(row.get("stage", ""))
        if kind == "submitted" and (stage.startswith("spring_open")
                                    or stage.startswith("spring_extend")
                                    or stage == "micro_first_leg"):
            # a submission without an order id can never be reconciled:
            # it keeps its own key and counts in full, fail closed
            submitted[str(row.get("order_id") or f"row{i}")] = row
        elif kind == "order_terminal":
            terminal[str(row.get("order_id"))] = row
        elif kind == "filled" and stage == "micro_first_leg":
            price, qty = micro._d(row.get("price")), micro._d(row.get("qty"))
            if price is None or qty is None:
                return SESSION_NOTIONAL_CAP
            total += price * qty
    for oid, row in submitted.items():
        term = terminal.get(oid)
        if term is None:                       # unknown outcome: full spend
            price, qty = micro._d(row.get("price")), micro._d(row.get("qty"))
            if price is None or qty is None:
                return SESSION_NOTIONAL_CAP
            total += price * qty
            continue
        got = micro._d(term.get("filled_qty"))
        price = micro._d(term.get("price")) or micro._d(row.get("price"))
        if got is None or price is None:
            return SESSION_NOTIONAL_CAP
        total += price * got
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


# OWNER 5552475548 item 1: on sports a post-only maker order may REST up
# to this long. The L5 DECISION cadence is untouched -- the model still
# chooses every action -- but the venue is not a 5-minute BTC book, and a
# touch-joining quote cancelled after 2s never gets hit. While resting,
# the order is re-checked every poll: it is cancelled early the moment
# the book/state moves so that the chosen action is no longer the
# current L5 action or would breach post-only. Maker only; no taker.
SPORTS_MAKER_REST_MAX_S = 30.0
# bounded imbalance in behavioral units, the shared planner's bound
# (pmus_sports_l5_decision.MAX_RESIDUAL_UNITS); EXTEND stops here,
# COMPLETE does not
MAX_RESIDUAL_UNITS = 3
# OWNER 5554106892: prebuilt, OFF by default ("1" enables): a capped
# EXTEND falls to COMPLETE when COMPLETE carries at least this mass
CAP_FALLBACK_ENV = "PMUS_SPRING_CAP_FALLBACK_COMPLETE"
CAP_FALLBACK_MIN_P = 0.20
# OWNER 5556232035 (Phase 1): "modal" (argmax, default) or "stochastic"
# (execute the seeded draw from the frozen v2 masses -- L5's own
# conditional distribution). Neither changes the model, the gates or
# the caps; the choice is recorded on every decision (action_selection).
ACTION_SELECTION_ENV = "PMUS_SPRING_ACTION_SELECTION"

# OWNER 5556038561 #3/#4: the first leg filled while every complement /
# extend was venue-rejected (LIVE 2026-09-06 00:57-01:20Z), which turns a
# pair-seeking strategy into stranded directional exposure. Until the
# venue has ACCEPTED (rested) a post-only opposite-side order built by
# this code, NEW first legs fail closed; a held residual is still managed
# under the existing bounded rules. The proof is durable (state dir),
# carries the venue evidence, and is REVOKED by any later complement
# rejection at a price the fresh book said was non-crossing.
COMPLEMENT_PROOF_FILE = "complement_acceptance.json"
COMPLEMENT_PROOF_REST_MAX_S = 3.0     # rest-and-cancel bound for the probe
COMPLEMENT_PROOF_RETRY_S = 600.0      # at most one probe order per 10 min
RESTING_STATES = ("OPEN", "NEW", "PARTIALLY_FILLED", "PENDING_NEW",
                  "PENDING_RISK")


def complement_proof_path():
    return q1.base.state_dir() / COMPLEMENT_PROOF_FILE


def load_complement_proof() -> dict:
    path = complement_proof_path()
    if not path.exists():
        return {"proven": False, "reason": "never_attempted"}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"proven": False, "reason": "unreadable"}
    return doc if isinstance(doc, dict) else {"proven": False,
                                              "reason": "malformed"}


def save_complement_proof(proof: Mapping[str, Any]) -> None:
    path = complement_proof_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(proof), indent=1, default=str),
                   encoding="utf-8")
    os.replace(tmp, path)
    print("PMUS_COMPLEMENT_ACCEPTANCE " + json.dumps(
        dict(proof), separators=(",", ":"), default=str), flush=True)


def complement_acceptance_proven() -> bool:
    return load_complement_proof().get("proven") is True


def revoke_complement_proof(slug: str, order: Mapping[str, Any],
                            why: str) -> None:
    """A complement/extend rejection at a price the fresh book said was
    non-crossing means the venue's acceptance rules are not what this
    code proved; NEW first legs fail closed again until re-proven."""
    prior = load_complement_proof()
    save_complement_proof({
        "proven": False, "reason": f"revoked:{why}", "slug": slug,
        "revoked_utc": datetime.now(timezone.utc).isoformat(),
        "revoked_order": dict(order), "prior_proof": prior})


def divergence_reason(refusal: Optional[str], v2_relation: str,
                      acted: str, sampled: Optional[str]) -> str:
    """OWNER 5556038561 #7: WHY the safe executed action differs from the
    L5 shadow, so a safety adaptation is never mistaken for a behavioral
    mismatch. 'none' when the executed action IS the model's action and
    the seeded draw agrees."""
    if refusal:
        if refusal.startswith("off_distribution"):
            return "safety_adaptation:off_distribution_hold"
        if refusal.startswith("residual_cap:fallback_complete"):
            return "policy_flag:cap_fallback_complete"
        if refusal.startswith("residual_cap"):
            return "safety_adaptation:residual_cap"
        return f"safety_adaptation:{refusal.split(':')[0]}"
    if sampled is not None and acted == sampled and acted != v2_relation:
        return "none:stochastic_draw_executed"      # L5's own distribution
    if acted != v2_relation:
        return f"safety_adaptation:{acted}_for_{v2_relation}"
    if sampled is not None and sampled != acted:
        return "behavioral:modal_argmax_vs_stochastic_draw"
    return "none"


# OWNER Phase 1 directive (2026-09-06, item 5): every stochastic-vs-
# executed disagreement is classified into exactly one of five classes:
# implementation bug / venue-execution adaptation / safety-cap override /
# model deficiency / genuinely off-distribution state.
DIVERGENCE_CLASSES = {
    "safety_adaptation:residual_cap": "safety-cap override",
    "safety_adaptation:spend_cap": "safety-cap override",
    "policy_flag:cap_fallback_complete": "safety-cap override (L5-consistent fallback)",
    "safety_adaptation:off_distribution_hold": "genuinely off-distribution state",
    "safety_adaptation:complement_not_priceable": "venue/execution adaptation",
    "safety_adaptation:complement_preview_rejected": "venue/execution adaptation",
    "safety_adaptation:complement_acceptance_unproven": "venue/execution adaptation",
    "behavioral:modal_argmax_vs_stochastic_draw": "model deficiency (argmax execution of a stochastic policy)",
    "none": "no disagreement",
    "none:stochastic_draw_executed": "no disagreement",
}


def divergence_class(divergence: str) -> str:
    if divergence in DIVERGENCE_CLASSES:
        return DIVERGENCE_CLASSES[divergence]
    head = divergence.split("->")[0]
    if head in DIVERGENCE_CLASSES:
        return DIVERGENCE_CLASSES[head]
    if divergence.startswith("safety_adaptation:"):
        return "safety-cap override"
    return "implementation bug (unclassified divergence -- investigate)"


def complement_plan(first_side_up: bool, first_px: D,
                    yes_bbo: Mapping[str, Any], tick: D) -> dict:
    """The exact opposite-side order a first leg at ``first_px`` would
    need: side, fee-safe maker price (pair <= 0.98 and >= 1c after known
    fees, strictly below the opposite ask) and the venue long-side wire
    price. ``px`` is None when no legal fee-safe complement exists at the
    current book -- that first leg would be a residual by construction."""
    yb, ya = D(str(yes_bbo["bid"])), D(str(yes_bbo["ask"]))
    if first_side_up:
        side, opp_ask = "OUTCOME_SIDE_NO", q1.ONE - yb      # NO ask
    else:
        side, opp_ask = "OUTCOME_SIDE_YES", ya
    px, econ = spring_complement_price(D(str(first_px)), opp_ask, tick)
    return {"side": side, "px": px, "opp_ask": str(opp_ask),
            "wire_px": (str(micro.venue_price(side, px))
                        if px is not None else None), **econ}


def _relation_mass(prop: Mapping[str, Any], relation: str) -> float:
    for r in prop.get("relation_ranked") or []:
        if isinstance(r, Mapping) and r.get("relation") == relation:
            try:
                return float(r.get("p") or 0.0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _await_order(t: Transport, oid: str, qty: D, until: float,
                 clock, sleep, still_wanted=None) -> tuple:
    """One outstanding order: rest until filled, until the deadline, or
    until ``still_wanted()`` names a reason the action is no longer the
    current L5 action; then cancel-and-harvest.

    Returns ``(filled_qty, rest_s, end_reason)`` with the exact
    authoritative filled Decimal quantity (0 for none) and the measured
    rest time, so category fill hazard can be estimated later.
    """
    start = clock()
    fill = None
    end_reason = "deadline"
    try:
        while clock() < until:
            fill = micro.authoritative_fill(q1._order_status(t, oid), qty)
            if fill is not None:
                end_reason = "filled"
                break
            if still_wanted is not None:
                why = still_wanted()
                if why:
                    end_reason = f"repriced:{why}"
                    break
            sleep(q1.POLL_S)
    except RejectedNoFill as exc:
        # the venue's verdict, with whatever reason it attached: a NO-side
        # rejection pattern is only diagnosable if the reason is logged
        return D("0"), round(clock() - start, 3), f"rejected:{str(exc)[:120]}"
    if fill is None:
        try:
            q1.cancel_exact(t, oid)
        except RejectedNoFill:
            return D("0"), round(clock() - start, 3), end_reason
        except Exception:
            pass
        fill = micro._terminal_positive_fill(t, oid, qty)
        if fill is not None:
            end_reason = "filled_on_cancel"
    got = D(str(fill["qty"])) if fill else D("0")
    return got, round(clock() - start, 3), end_reason


def held_inventory_from_ledger(slug: str) -> Inventory:
    """OWNER 5552607804: rebuild this market's REAL inventory from the
    durable ledger so a later session continues the position L5-style
    instead of freezing it. Fills come from `filled` rows and, for orders
    submitted by an earlier build that wrote no `filled` row, from a
    terminal row with a positive filled quantity joined to its
    submission. Ledger order is preserved so avg cost, the behavioral
    unit and last_rel are the real ones."""
    inv = Inventory()
    path = q1.base.state_dir() / "ledger.jsonl"
    if not path.exists():
        return inv
    rows: list[tuple[int, dict]] = []
    for i, line in enumerate(path.read_text().splitlines()):
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict) and row.get("slug") == slug:
            rows.append((i, row))
    # pass 1: every order with a `filled` row is accounted by that row
    # ALONE. The live session writes the terminal row before the filled
    # row for the same order, so a single pass that trusted whichever
    # came first counted one fill twice (ledger 0.04 vs venue 0.03).
    submitted: dict[str, dict] = {}
    filled_ids: set[str] = set()
    events: list[tuple[int, bool, D, D, str]] = []
    for i, row in rows:
        kind, stage = row.get("kind"), str(row.get("stage", ""))
        oid = str(row.get("order_id") or "")
        if kind == "submitted" and stage.startswith("spring_"):
            submitted[oid] = row
        elif kind == "filled" and stage.startswith("spring_"):
            px, qty = micro._d(row.get("price")), micro._d(row.get("qty"))
            if px is None or qty is None or qty <= 0:
                continue
            filled_ids.add(oid)
            events.append((i, str(row.get("side")).upper() != "NO", px, qty,
                           stage.split("_")[1] if "_" in stage else "open"))
    # pass 2: orders an earlier build left with only a terminal row
    for i, row in rows:
        oid = str(row.get("order_id") or "")
        if row.get("kind") != "order_terminal" or oid not in submitted or \
                oid in filled_ids:
            continue
        got = micro._d(row.get("filled_qty"))
        px = micro._d(row.get("price")) or \
            micro._d(submitted[oid].get("price"))
        if got is None or px is None or got <= 0:
            continue
        filled_ids.add(oid)
        sub_stage = str(submitted[oid].get("stage", ""))
        events.append((i, str(submitted[oid].get("side")).upper()
                       != "OUTCOME_SIDE_NO", px, got,
                       sub_stage.split("_")[1] if "_" in sub_stage
                       else "open"))
    for _, side_up, px, qty, rel in sorted(events, key=lambda e: e[0]):
        inv.record(side_up, px, qty, rel)
    return inv


def resume_block_reason(t: Transport, slug: str) -> Optional[str]:
    """Account preflight for RESUMING a held market: any open order
    anywhere and any position in ANY OTHER market still block (one
    market at a time); a position in this market is the point."""
    block = micro.account_block_reason(t)
    if block is None or not block.startswith("nonzero_positions:"):
        return block
    others = [p for p in block.split(":", 1)[1].split(",")
              if p and not p.startswith(slug + "=")]
    return ("nonzero_positions_elsewhere:" + ",".join(others)) if others \
        else None


def live_market_open_reason(t: Transport, slug: str,
                            now: Optional[float] = None) -> Optional[str]:
    """Execution-freshness rule (OWNER 5552475548 item 2): immediately
    before the first submission, the venue's own market document is
    re-read and the same fail-closed open-state check the guard applies
    to recorder evidence is applied to the LIVE document."""
    from src import pmus_spring_l5_probe_entry as entry   # lazy: cycle
    try:
        doc = t.request("GET", f"/v1/market/slug/{slug}")
    except Exception as exc:                       # noqa: BLE001
        return f"live_market_unreadable:{type(exc).__name__}"
    market = doc.get("market") if isinstance(doc, Mapping) else None
    if not isinstance(market, Mapping):
        return "live_market_document_missing"
    reason = entry.market_open_reason({"market_state": market}, now)
    return None if reason is None else reason.replace("strict_evidence_",
                                                        "live_", 1)


def reconcile_submissions(t: Transport, slug: str, limit: int = 50) -> dict:
    """OWNER 5552475548 item 3: every spring submission for this slug that
    has no terminal row is reconciled against the venue's authoritative
    order status and given one, so a cancelled zero-fill attempt from an
    earlier build can no longer count as spend. An order still open is
    cancelled first (it should not exist: the deadman ran). Anything the
    venue cannot confirm stays unreconciled and keeps counting."""
    path = q1.base.state_dir() / "ledger.jsonl"
    if not path.exists():
        return {"checked": 0, "reconciled": 0, "unresolved": 0}
    submitted: dict[str, dict] = {}
    terminal: set[str] = set()
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict) or row.get("slug") != slug:
            continue
        if row.get("kind") == "submitted" and \
                str(row.get("stage", "")).startswith("spring_"):
            submitted[str(row.get("order_id"))] = row
        elif row.get("kind") == "order_terminal":
            terminal.add(str(row.get("order_id")))
    pending = [oid for oid in submitted if oid not in terminal][:limit]
    out: dict[str, Any] = {"checked": len(pending), "reconciled": 0,
                           "unresolved": 0, "unresolved_reasons": []}
    for oid in pending:
        try:
            try:
                order = q1._order_status(t, oid)
            except RejectedNoFill:
                # the venue's own verdict: rejected, zero filled
                order = {"state": "ORDER_STATE_REJECTED", "cumQuantity": "0"}
            state = str(order.get("state") or order.get("status") or "").upper()
            if not any(x in state for x in ("CANCEL", "EXPIRED", "REJECT",
                                            "FILLED")):
                q1.cancel_exact(t, oid)
                order = q1._order_status(t, oid)
            req = micro._d(submitted[oid].get("qty")) or D("0")
            fill = micro.authoritative_fill(order, req, accept_partial=True) \
                if req > 0 else None
            q1.ledger_append({"kind": "order_terminal", "spring": True,
                              "slug": slug, "order_id": oid,
                              "filled_qty": str(fill["qty"]) if fill else "0",
                              "price": (str(fill["price"]) if fill
                                        else submitted[oid].get("price")),
                              "source": "reconcile"})
            out["reconciled"] += 1
        except Exception as exc:                   # noqa: BLE001
            out["unresolved"] += 1                 # keeps counting as spend
            if len(out["unresolved_reasons"]) < 5:
                out["unresolved_reasons"].append(
                    f"{oid}:{type(exc).__name__}:{str(exc)[:60]}")
    return out


def run_session(t: Optional[Transport] = None,
                clock=time.monotonic, sleep=time.sleep,
                wall=time.time, slug: Optional[str] = None,
                gate_ok: bool = False,
                deadline_s: Optional[float] = None,
                resume: bool = False) -> dict:
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
        fid_art = fid.load()              # shadow fields only; None is fine
        last_skey: dict = {}
        block = (resume_block_reason(t, slug) if resume
                 else micro.account_block_reason(t))
        if block:
            return {**report, "status": "PREFLIGHT_FAILED", "reason": block}
        row = dict(micro.micro_eligible(t, slug))
        if strict_completion_asserted(slug):
            row["spring_category_bar_met"] = True
        if resume:
            # OWNER 5552607804: a HELD market is continued L5-style
            # (EXTEND / COMPLEMENT / HOLD from the model on the real
            # inventory), so the OPEN-admission band gate does not apply;
            # a readable two-sided book still does
            if row.get("skip_reason") and not str(
                    row["skip_reason"]).startswith("entry_out_of_band"):
                return {**report, "status": "SPRING_SKIP_NOT_QUALIFYING",
                        "reason": f"resume:{row['skip_reason']}",
                        "candidate_row": row}
            # the fill may have been recorded by an earlier build as a
            # bare submission: give every submission its authoritative
            # terminal row FIRST, then rebuild inventory from the ledger
            report["reconcile"] = reconcile_submissions(t, slug)
            inv = held_inventory_from_ledger(slug)
            venue_pos = q1._position_for_slug(t, slug)
            if inv.up + inv.dn == 0 or \
                    inv.residual.copy_abs() != venue_pos.copy_abs():
                # the ledger and the venue disagree about what we hold:
                # never act on inventory we cannot reconstruct exactly
                return {**report, "status": "SPRING_SAFE_REFUSAL",
                        "reason": (f"inventory_mismatch:ledger_residual="
                                   f"{inv.residual}:venue={venue_pos}"),
                        "ledger_inventory": inv.snapshot()}
            report["resumed_inventory"] = inv.snapshot()
        else:
            skip = spring_candidate_gate(row, model)
            if skip:
                return {**report, "status": "SPRING_SKIP_NOT_QUALIFYING",
                        "reason": skip, "candidate_row": row}
            if not complement_acceptance_proven():
                # OWNER 5556038561 #3: no NEW first leg until the venue
                # has accepted an opposite-side order built by this code
                return {**report, "status": "SPRING_SAFE_REFUSAL",
                        "reason": "complement_acceptance_unproven",
                        "complement_proof": load_complement_proof()}
        # execution-freshness rule (OWNER 5552475548 item 2): the venue's
        # LIVE market document, immediately before any submission
        live_block = live_market_open_reason(t, slug, wall())
        if live_block:
            return {**report, "status": "SPRING_SAFE_REFUSAL",
                    "reason": live_block}
        # item 3: give every earlier submission its authoritative terminal
        # row before the cap is computed from FILLED notional
        if "reconcile" not in report:          # resume reconciled earlier
            report["reconcile"] = reconcile_submissions(t, slug)
        cum = spring_cumulative_notional(slug)
        # on a resume the durable cap already contains this session's
        # starting inventory; only NEW spend adds to it
        prior_spent = inv.spent()
        report["spring_gates"] = {
            "band": f"{SPRING_BAND_LO}-{SPRING_BAND_HI}",
            "pair_target": str(SPRING_PAIR_TARGET),
            "session_notional_cap": str(SESSION_NOTIONAL_CAP),
            "prior_cumulative_notional": str(cum),
            "cap_basis": "authoritative FILLED notional (partials included); "
                         "unconfirmed submissions count in full",
            "maker_rest_max_s": SPORTS_MAKER_REST_MAX_S,
        }

        def _harvest(oid: str, stage: str, side_up_: bool, px_: D,
                     qty_: D, relation_: str, got_: D, rest_s: float,
                     end_reason: str) -> None:
            """Report + durable rows for one submitted order: terminal
            (always) and filled (when it filled)."""
            q1.ledger_append({"kind": "order_terminal", "spring": True,
                              "slug": slug, "order_id": oid,
                              "filled_qty": str(got_), "price": str(px_),
                              "rest_s": rest_s, "end_reason": end_reason})
            if got_ > 0:
                q1.ledger_append({"kind": "filled", "spring": True,
                                  "stage": stage, "slug": slug,
                                  "side": "YES" if side_up_ else "NO",
                                  "price": str(px_), "qty": str(got_),
                                  "order_id": oid})
            report.setdefault("orders", []).append(
                {"stage": stage, "order_id": oid,
                 "side": "YES" if side_up_ else "NO",
                 "px": str(px_), "qty": str(qty_), "filled_qty": str(got_),
                 "relation": relation_, "rest_s": rest_s,
                 "end_reason": end_reason,
                 "outcome": "filled" if got_ > 0 else "no_fill_cancelled",
                 # fidelity-gate inputs: OPEN/EXTEND join the touch of
                 # their side (L5's aggressiveness); COMPLETE rests at the
                 # fee-safe bound; OPEN side is the shared convention side
                 "touch_join": relation_ in ("open", "extend"),
                 "side_convention_ok": True if relation_ == "open" else None,
                 # OWNER 5553674206 #3: the scale mismatch is MEASURED --
                 # L5's historical size for this state/relation beside the
                 # venue-minimum bounded size we actually executed
                 "executed_safe_size": {
                     "contracts": str(qty_),
                     "usd": str((px_ * qty_).quantize(D("0.0001")))},
                 "l5_target_size": fid.l5_target_size(
                     fid_art, last_skey.get("k") or "", relation_)})

        def _classify_rejection(side_up_: bool, px_: D) -> dict:
            """Owner item 5 classes for a post-only rejection, from the
            FRESH book: crossed = venue/execution adaptation (the book
            moved through our price); not crossed = unexplained by this
            code's acceptance model (implementation bug candidate)."""
            try:
                b = q1.outcome_bbo(t, slug, "OUTCOME_SIDE_YES" if side_up_
                                   else "OUTCOME_SIDE_NO")
                opp_ask = D(str(b["ask"]))
            except Exception as exc:                  # noqa: BLE001
                return {"class": f"unknown:bbo_unreadable:{type(exc).__name__}",
                        "crossed": False, "opp_ask": None}
            crossed = px_ >= opp_ask
            return {"class": ("venue/execution adaptation:"
                              "post_only_cross_after_book_move" if crossed
                              else "implementation bug candidate:"
                                   "rejected_at_non_crossing_price"),
                    "crossed": crossed, "opp_ask": str(opp_ask)}

        def _still_wanted(side_up_: bool, px_: D, relation_: str,
                          skey_: str):
            """Why the resting order is no longer the current L5 action
            (None = keep resting). Re-reads the live book each poll."""
            def check() -> Optional[str]:
                try:
                    b = q1.outcome_bbo(t, slug, "OUTCOME_SIDE_YES")
                except Exception as exc:              # noqa: BLE001
                    return f"bbo_unreadable:{type(exc).__name__}"
                if b.get("bid") is None or b.get("ask") is None:
                    return "book_one_sided"
                if relation_ in ("open", "extend") and not q1.entry_ok(
                        b["bid"]):
                    return f"price_bound_left:{b['bid']}"
                if relation_ == "open" and not (
                        SPRING_BAND_LO <= b["bid"] <= SPRING_BAND_HI):
                    return f"band_left:{b['bid']}"
                # our price must still be the touch on our side; a moved
                # touch means L5 would re-quote, and a price at/through
                # the opposite ask would breach post-only
                if side_up_:
                    touch, opp_ask = b["bid"], b["ask"]
                else:
                    touch, opp_ask = q1.ONE - b["ask"], q1.ONE - b["bid"]
                touch = (D(str(touch)) / spec["tick"]).to_integral_value(
                    rounding=ROUND_DOWN) * spec["tick"]
                if px_ >= D(str(opp_ask)):
                    return f"would_cross:{opp_ask}"
                if relation_ in ("open", "extend") and touch != px_:
                    return f"touch_moved:{touch}"
                if inv.up + inv.dn > 0:
                    k = spring_state_key(inv, b["bid"], settlement_t, wall())
                    if k != skey_:
                        p = pv2.proposal(model, k, inv.last_rel or "open",
                                         "q<5",
                                         residual=inv.behavioral_residual())
                        if p["recommended_relation"] != relation_:
                            return (f"l5_action_changed:"
                                    f"{p['recommended_relation']}")
                return None
            return check

        def _prove_complement_acceptance(bbo: Mapping[str, Any],
                                         spec: Mapping[str, Any]) -> dict:
            """OWNER 5556038561 #4/#5 (dry acceptance): on the HELD market
            build the exact opposite-side order the residual needs (fee-
            safe price, post-only, venue-minimum size), preview it, submit
            it, and treat a RESTING venue state as the proof; cancel as
            soon as the state is observed. A fill in that window is a
            legitimate fee-safe complement and is accounted like any
            other. Rejection = not proven, with the venue's evidence."""
            deficit_up = inv.residual < 0
            surplus_avg = inv.avg_dn() if deficit_up else inv.avg_up()
            plan = complement_plan(not deficit_up, surplus_avg, bbo,
                                   spec["tick"])
            proof: dict[str, Any] = {
                "proven": False, "slug": slug,
                "attempted_utc": datetime.now(timezone.utc).isoformat(),
                "attempted_epoch": wall(),
                "residual": str(inv.residual), "plan": plan,
                "book": {"yes_bid": str(bbo["bid"]),
                         "yes_ask": str(bbo["ask"])}}
            if plan["px"] is None:
                proof["reason"] = f"no_fee_safe_complement:{plan['regime']}"
                save_complement_proof(proof)
                return proof
            px = D(str(plan["px"]))
            qty = min(inv.residual.copy_abs(),
                      D(str(spec["minimum_trade_qty"])))
            try:
                oid = micro.place_limit_buy(t, slug, plan["side"], px, qty,
                                            "complement_acceptance",
                                            post_only=True)
            except Halt as exc:
                proof["reason"] = f"preview_or_preflight:{exc}"
                save_complement_proof(proof)
                return proof
            proof["order_id"] = oid
            proof["submitted"] = {"side": plan["side"], "px": str(px),
                                  "wire_px": plan["wire_px"],
                                  "qty": str(qty), "post_only": True}
            start = clock()
            got, end_reason, observed = D("0"), "rest_bound", None
            try:
                while clock() < start + COMPLEMENT_PROOF_REST_MAX_S:
                    order = q1._order_status(t, oid)
                    observed = str(order.get("state") or
                                   order.get("status") or "").upper()
                    fill = micro.authoritative_fill(order, qty)
                    if fill is not None:
                        got, end_reason = D(str(fill["qty"])), "filled"
                        break
                    if any(x in observed for x in RESTING_STATES):
                        end_reason = f"resting:{observed}"
                        break
                    sleep(q1.POLL_S)
            except RejectedNoFill as exc:
                end_reason = f"rejected:{str(exc)[:160]}"
            if got == 0 and not end_reason.startswith("rejected"):
                try:
                    q1.cancel_exact(t, oid)
                except Exception:                     # noqa: BLE001
                    pass
                fill = micro._terminal_positive_fill(t, oid, qty)
                if fill is not None:
                    got, end_reason = D(str(fill["qty"])), "filled_on_cancel"
            rest_s = round(clock() - start, 3)
            _harvest(oid, "complement_acceptance", deficit_up, px, qty,
                     "complete", got, rest_s, end_reason)
            if got > 0:
                fill = micro._terminal_positive_fill(t, oid, qty) or \
                    {"price": px, "qty": got}
                inv.record(deficit_up, D(str(fill["price"])), got,
                           "complete")
            proven = bool(got > 0 or end_reason.startswith("resting"))
            proof.update({
                "proven": proven, "observed_state": observed,
                "end_reason": end_reason, "rest_s": rest_s,
                "filled_qty": str(got),
                "proven_utc": (datetime.now(timezone.utc).isoformat()
                               if proven else None),
                "labels": "venue accepted a post-only opposite-side order at "
                          "the long-side price convention; no cap/gate "
                          "change; a fill here is a fee-safe complement"})
            if not proven:
                proof["reason"] = end_reason
            save_complement_proof(proof)
            return proof

        if resume and inv.residual != 0 and \
                not complement_acceptance_proven() and \
                wall() - float(load_complement_proof().get(
                    "attempted_epoch") or 0) >= COMPLEMENT_PROOF_RETRY_S:
            # bounded: one probe order per retry interval, not one per
            # resume iteration (resumes run every 15-120 s on book moves)
            bbo0 = q1.outcome_bbo(t, slug, "OUTCOME_SIDE_YES")
            spec0 = micro.market_micro_spec(t, slug, bbo0["bid"])
            report["complement_acceptance"] = _prove_complement_acceptance(
                bbo0, spec0)
            reached_order_stage = reached_order_stage or bool(
                report["complement_acceptance"].get("order_id"))

        actions = 0
        while actions < max_actions and not out_of_time():
            bbo = q1.outcome_bbo(t, slug, "OUTCOME_SIDE_YES")
            spec = micro.market_micro_spec(t, slug, bbo["bid"])
            skey = spring_state_key(inv, bbo["bid"], settlement_t, wall())
            last_skey["k"] = skey
            tte_s = (max(settlement_t - wall(), 0.0) if settlement_t
                     else SPORTS_TTE_DEFAULT_S)
            # behavioral units, sign preserved — never int() a fractional
            # venue residual (issue #167 comment 5520710312)
            prop = pv2.proposal(model, skey, inv.last_rel or "open", "q<5",
                                residual=inv.behavioral_residual())
            # OWNER 5556232035 / Phase 1 largest mismatch: L5's policy is
            # a CONDITIONAL DISTRIBUTION over relations; executing its
            # argmax ("modal") can never match L5's action frequencies
            # (live acted L1 0.97 vs sampled 0.08 on 144 decisions).
            # PMUS_SPRING_ACTION_SELECTION=stochastic executes the SAME
            # seeded inverse-CDF draw that the shadow log already carries
            # (seed = sha256(slug|idx|model_sha); replayable). Every
            # safety gate below applies to the drawn relation unchanged.
            selection = os.getenv(ACTION_SELECTION_ENV, "modal").strip().lower()
            decision_idx = len(report.get("decisions") or [])
            draw = fid.sample_relation(
                prop.get("relation_ranked"),
                fid.seed_material(slug, decision_idx,
                                  str(model.get("_sha256_16") or "")))
            if inv.up + inv.dn == 0:
                relation = "open"
            elif selection == "stochastic" and draw.get("sampled"):
                relation = str(draw["sampled"])
            else:
                relation = prop["recommended_relation"]
            off = bool(prop.get("off_distribution"))
            refusal = None
            if off and inv.up + inv.dn > 0:
                # OWNER 5552036210 item 4: the policy is distributional; a
                # state the model never saw gets a LOGGED refusal and a
                # hold, never a substituted deterministic rule. Nothing is
                # flattened -- the residual holds to settlement.
                relation, refusal = "hold", "off_distribution_hold"
            elif relation == "extend" and abs(
                    inv.behavioral_residual()) >= MAX_RESIDUAL_UNITS:
                # bounded imbalance (same bound as the shared planner):
                # no further same-side EXTEND past the residual cap; a
                # COMPLETE is still allowed, and nothing is flattened
                relation, refusal = "hold", (
                    f"residual_cap:{abs(inv.behavioral_residual())}"
                    f">={MAX_RESIDUAL_UNITS}")
                # OWNER 5554106892 (next behavioral mismatch, prebuilt,
                # OFF by default): at the cap the modal policy HOLDs 100%
                # of the time, while L5 in the same imb states COMPLETEs
                # 43-48% (v2 base: p55-75c|imb1-10 n=316 complete 0.434;
                # p45-55c n=281 0.452; p25-45c n=366 0.484). With the
                # flag, a capped EXTEND falls to the SECOND-ranked
                # relation when it is COMPLETE with mass >= the floor;
                # the complement still has to clear <=0.98 and >=1c after
                # known fees or it stays a HOLD. Pairs only ever REDUCE
                # directional exposure; no cap is touched.
                comp_p = _relation_mass(prop, "complete")
                if os.getenv(CAP_FALLBACK_ENV, "").strip() == "1" and \
                        comp_p >= CAP_FALLBACK_MIN_P:
                    relation = "complete"
                    refusal = f"residual_cap:fallback_complete:p={comp_p:.3f}"
            # OWNER 5553674206 #2/#3/#4: SHADOW fields only -- the seeded
            # draw from this same distribution, L5's size and taker share
            # for this state; nothing here is acted on
            shadow = fid.shadow_fields(
                fid_art, slug=slug,
                decision_idx=len(report.get("decisions") or []),
                model_sha=str(model.get("_sha256_16") or ""),
                base_state=skey, relation=prop["recommended_relation"],
                relation_ranked=prop.get("relation_ranked"), tte_s=tte_s)
            sampled = (shadow.get("stochastic") or {}).get("sampled")
            # model probability and rank for EVERY decision (item 4);
            # OWNER 5553997094 #4: state, executed safe action, shadow
            # action with probability/rank, L5 target size/aggressiveness,
            # planned size, inventory before, refusal if different
            report.setdefault("decisions", []).append(
                {"state": skey, "last_rel": inv.last_rel,
                 # L5 fidelity gate inputs (OWNER 5556232035 #5): cadence,
                 # market, behavioral unit for the imbalance dimension
                 "t_utc": datetime.now(timezone.utc).isoformat(),
                 "slug": slug,
                 "action_selection": selection,
                 "next_reevaluation_s": prop.get("next_reevaluation_s"),
                 "behavioral_unit": str(inv.unit) if inv.unit else None,
                 "v2_recommended": prop["recommended_relation"],
                 "relation_ranked": prop.get("relation_ranked"),
                 "state_n": prop.get("state_n"),
                 "off_distribution": off,
                 "persist_extend_p": prop["persist_extend_p"],
                 "switch_complete_p": prop["switch_complete_p"],
                 "acted": relation,
                 "refusal": refusal,
                 "inventory_before": inv.snapshot(),
                 "planned_safe_size": {
                     "contracts": str(spec["qty"]),
                     "usd": str(spec["entry_notional"])},
                 "shadow": shadow,
                 "shadow_differs_from_executed": bool(
                     sampled is not None and sampled != relation),
                 # OWNER 5556038561 #7: safety adaptation vs behavioral
                 # mismatch, named per decision
                 "divergence": divergence_reason(
                     refusal, prop["recommended_relation"], relation,
                     sampled),
                 "divergence_class": divergence_class(divergence_reason(
                     refusal, prop["recommended_relation"], relation,
                     sampled)),
                 "size_divergence": "venue_minimum_vs_l5_target"})
            if relation == "hold":
                sleep(max(prop["next_reevaluation_s"], MIN_REEVAL_S))
                actions += 1
                continue

            if relation in ("open", "extend"):
                # spend caps: session + durable cumulative, before sizing
                if cum + (inv.spent() - prior_spent) + \
                        spec["entry_notional"] > SESSION_NOTIONAL_CAP:
                    relation = "complete" if inv.residual != 0 else "stop"
                    report["decisions"][-1]["divergence"] = \
                        f"safety_adaptation:spend_cap->{relation}"
                    report["decisions"][-1]["divergence_class"] = \
                        divergence_class("safety_adaptation:spend_cap")
            if relation == "sell" or relation == "stop":
                report["stop_reason"] = ("l5_sell_is_noop" if relation == "sell"
                                         else "spend_cap_reached")
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
                        report["stop_reason"] = "no_priceable_side"
                        break
                else:
                    side_up = inv.residual > 0     # extend the surplus side
                px = bbo["bid"] if side_up else (q1.ONE - bbo["ask"])
                px = (px / spec["tick"]).to_integral_value(
                    rounding=ROUND_DOWN) * spec["tick"]
                # OWNER 5552841170 #1: the 0.35-0.65 band is OPEN
                # admission. A CONTINUATION (EXTEND on existing inventory)
                # is governed by the model's in-distribution state at the
                # CURRENT price -- an off-distribution state already HOLDs
                # above -- plus the venue price bounds and every cap.
                in_band = SPRING_BAND_LO <= bbo["bid"] <= SPRING_BAND_HI
                if not q1.entry_ok(bbo["bid"]) or \
                        (inv.up + inv.dn == 0 and not in_band):
                    # Named, because a zero-order session must be
                    # distinguishable from an unfilled one in the log.
                    report["stop_reason"] = (
                        f"entry_band_left:bid={bbo['bid']}:"
                        f"q1_entry_ok={q1.entry_ok(bbo['bid'])}")
                    break
                qty = spec["qty"]
                if inv.up + inv.dn == 0:
                    # OWNER 5556038561 #4: the opposite-side order this
                    # first leg would need must be priceable (fee-safe,
                    # non-crossing) and PREVIEW-accepted by the venue
                    # before the first leg is allowed; otherwise the first
                    # leg would be a residual by construction
                    plan = complement_plan(side_up, px, bbo, spec["tick"])
                    report["decisions"][-1]["complement_preflight"] = plan
                    if plan["px"] is None:
                        report["stop_reason"] = (
                            f"complement_not_priceable:{plan['regime']}")
                        report["decisions"][-1]["divergence"] = (
                            "safety_adaptation:complement_not_priceable")
                        break
                    cbody = micro._body(slug, plan["side"],
                                        D(str(plan["px"])), qty, True)
                    try:
                        prev = t.request("POST", "/v1/order/preview",
                                         {"request": cbody})
                        ok = isinstance(prev, Mapping) and isinstance(
                            prev.get("order"), Mapping)
                    except Exception as exc:              # noqa: BLE001
                        prev, ok = {"error": f"{type(exc).__name__}:"
                                             f"{str(exc)[:160]}"}, False
                    plan["preview_ok"] = bool(ok)
                    print("PMUS_COMPLEMENT_PREFLIGHT " + json.dumps(
                        {"slug": slug, "first_leg": {
                            "side": "YES" if side_up else "NO",
                            "px": str(px), "qty": str(qty)},
                         "complement": {k: plan.get(k) for k in
                                        ("side", "px", "wire_px", "opp_ask",
                                         "regime", "preview_ok")},
                         "preview": (q1._scalar_fields(prev["order"], 300)
                                     if ok else str(prev)[:300])},
                        separators=(",", ":"), default=str), flush=True)
                    if not ok:
                        report["stop_reason"] = "complement_preview_rejected"
                        report["decisions"][-1]["divergence"] = (
                            "safety_adaptation:complement_preview_rejected")
                        break
                oid = micro.place_limit_buy(
                    t, slug, "OUTCOME_SIDE_YES" if side_up
                    else "OUTCOME_SIDE_NO", px, qty,
                    f"spring_{relation}_{actions + 1}", post_only=True)
                reached_order_stage = True
                got, rest_s, end_reason = _await_order(
                    t, oid, qty,
                    min(stop_at, clock() + max(
                        prop["next_reevaluation_s"], MIN_REEVAL_S,
                        SPORTS_MAKER_REST_MAX_S)),
                    clock, sleep,
                    still_wanted=_still_wanted(side_up, px, relation, skey))
                # every SUBMITTED order is reported, filled or not: the
                # ladder is submitted -> filled -> settled -> realized
                _harvest(oid, f"spring_{relation}_{actions + 1}", side_up,
                         px, qty, relation, got, rest_s, end_reason)
                if end_reason.startswith("rejected"):
                    rej = _classify_rejection(side_up, px)
                    report["orders"][-1]["rejection_class"] = rej["class"]
                    report["orders"][-1]["fresh_opp_ask"] = rej["opp_ask"]
                    if rej["crossed"]:
                        sleep(max(prop["next_reevaluation_s"], MIN_REEVAL_S))
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
                got, rest_s, end_reason = _await_order(
                    t, oid, qty,
                    min(stop_at, clock() + max(
                        prop["next_reevaluation_s"], MIN_REEVAL_S,
                        SPORTS_MAKER_REST_MAX_S)),
                    clock, sleep,
                    still_wanted=_still_wanted(deficit_up, px, "complete",
                                               skey))
                _harvest(oid, f"spring_complete_{actions + 1}", deficit_up,
                         px, qty, "complete", got, rest_s, end_reason)
                if end_reason.startswith("rejected"):
                    # LIVE 02:10:53-58Z: four NO completes at 0.625 were
                    # rejected within 1 s each right after a fill AT that
                    # price -- the book had moved through our price
                    # between the bbo read and the submit (a genuine
                    # post-only cross), not a venue rule this code does
                    # not model. Classify by the FRESH book: crossed ->
                    # venue/execution adaptation, wait for the next book
                    # move; not crossed -> the proof is revoked.
                    rej = _classify_rejection(deficit_up, px)
                    report["orders"][-1]["rejection_class"] = rej["class"]
                    report["orders"][-1]["fresh_opp_ask"] = rej["opp_ask"]
                    if rej["crossed"]:
                        sleep(max(prop["next_reevaluation_s"], MIN_REEVAL_S))
                    else:
                        revoke_complement_proof(
                            slug, {"order_id": oid,
                                   "side": "YES" if deficit_up else "NO",
                                   "px": str(px), "qty": str(qty),
                                   "end_reason": end_reason,
                                   "fresh_opp_ask": rej["opp_ask"]},
                            "complete_rejected_at_fresh_non_crossing_price")
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
