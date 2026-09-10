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
from src import pmus_fill_ledger as fl
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
# OWNER GO #167 5589583477: COMPLETE may be worked past the opening budget.
#
# THE MEASUREMENT. The working loop already existed -- every iteration
# re-reads the book, recomputes the state key, re-asks the model and
# reprices. What did not exist was enough iterations: COMPLETE competed
# with OPEN and EXTEND for ONE budget of 6, so a session that opens at
# action 1 has five completion attempts. L5's FAST completions use a median
# of 7 distinct prices, his median completion takes 53.5 s, and only 34.8%
# of his completions land inside our old horizon. Live: 78 of 145
# order-placing sessions used exactly 6 actions and exited with no stop
# reason at all -- the budget, not a cap and not the clock.
#
# ONE COUNTER, ONE LIMIT. The first implementation used a second counter
# and a disjunctive loop condition; it produced an unbounded loop, a reject
# breaker that never fired, and a completion budget that was not enforced.
# `actions` still increments on every iteration and the LIMIT moves instead
# -- bounded by construction, with no interaction to get wrong.
#
# NOT A RISK INCREASE. A COMPLETE reduces unmatched exposure, and the
# unmatched-dollar cap never refuses an order that does. Per-action size,
# the per-market spend cap, concurrency 1, maker-only and the daily stop
# are untouched, so more completion attempts can only convert residual into
# pairs -- which is where the measured losses are.
MAX_COMPLETE_ACTIONS_ENV = "PMUS_SPRING_MAX_COMPLETE_ACTIONS"
# 12 extra attempts at the model-driven rest covers L5's p90 completion
# (166 s) and stays inside the 900 s session deadline.
DEFAULT_MAX_COMPLETE_ACTIONS = 12
# A REJECTED complete is not what the extended budget bought. #93's
# unexplained non-crossing rejections concentrate on COMPLETE, and a
# rejected complete at a non-crossing price is what REVOKES the complement
# proof -- the deadlock that took the lane down at 13:09 today. Patience is
# for orders that RESTED and did not fill; three consecutive refusals end
# the loop rather than retrying into them.
COMPLETE_REJECT_STREAK_MAX = 3
# OWNER probe stop conditions (2026-09-09), for the bounded COMPLETE-reprice
# probe. The owner explicitly declined a 21.7% bar: "that's a historical
# diagnostic, not a magical safety threshold." These are the agreed rules.
#
#   1. three consecutive COMPLETE rejections            -> halt (above)
#   2. >35% rejected once there are >=10 submissions    -> halt
#   3. an infrastructure/auth/schema/rate-limit class
#      rejection seen TWICE                             -> immediate halt
#
# The existing hard loss/exposure gates stay authoritative regardless; these
# only ever ADD a reason to stop.
COMPLETE_REJECT_RATE_MIN_N = 10
COMPLETE_REJECT_RATE_MAX = 0.35
INFRA_REJECT_MAX = 2
# Matched against the venue end_reason and our own rejection class, both
# lowercased. Deliberately inclusive: a false halt costs one bounded probe, a
# missed auth or rate-limit storm costs money and reputation with the venue.
INFRA_REJECT_TOKENS = (
    "unauthor", "unauthenticated", "forbidden", "invalid_api", "signature",
    "rate_limit", "ratelimit", "too_many", "429",
    "schema", "malformed", "invalid_request", "bad_request", "400",
    "internal", "unavailable", "timeout", "5xx", "500", "502", "503",
    "bbo_unreadable", "market_status_halted",
)


def is_infra_rejection(end_reason: Any, rejection_class: Any = None) -> bool:
    """Is this rejection our plumbing or the venue's, rather than pricing?

    A pricing rejection is information about the market. An auth, schema or
    rate-limit rejection is a fault, and repeating it is how an account gets
    throttled or banned -- so two of them end the probe outright rather than
    being averaged into a rate.
    """
    blob = f"{end_reason or ''} {rejection_class or ''}".lower()
    return any(tok in blob for tok in INFRA_REJECT_TOKENS)
# COMPLETE re-quote on a moved touch. The mechanism below already cancels and
# re-quotes when L5's touch moves off a resting order, and COMPLETE has always
# been excluded from it: 36 OPEN and 35 EXTEND orders exited via
# repriced:touch_moved in the R5F tape against 0 of 868 COMPLETEs. Following
# L5's own price when it moves is fidelity, not a new strategy -- the re-quote
# posts at the touch the model returns and never a tick beyond it.
#
# DEFAULT OFF. L5_COMPLETION_EXTENSION_GATE_v1 is frozen and accumulating, and
# a second change to the same surface would make neither attributable. This
# ships dark and is enabled only by an explicit owner decision, per arm.
COMPLETE_REQUOTE_ENV = "PMUS_SPRING_COMPLETE_REQUOTE"
DEFAULT_COMPLETE_REQUOTE = False


def complete_requote_enabled() -> bool:
    """Is the COMPLETE touch_moved re-quote armed for this process?

    Read at call time rather than at import so a test can flip it without
    reloading the module, and so the live default cannot be captured by an
    import that happened before the environment was set."""
    raw = os.environ.get(COMPLETE_REQUOTE_ENV)
    if raw is None:
        return DEFAULT_COMPLETE_REQUOTE
    return raw.strip().lower() in ("1", "true", "yes", "on")


def requote_relations() -> tuple:
    """Which relations may re-quote when the touch moves.

    OPEN and EXTEND always; COMPLETE only when explicitly armed."""
    return (("open", "extend", "complete") if complete_requote_enabled()
            else ("open", "extend"))
STATE_DURABLE_ENV = "PMUS_SPRING_STATE_DURABLE"    # operator override "1"

# Spring gates — measured, from analysis/l5/l5_ramp_analysis.json
# OWNER GO 2026-09-10 ("We need more trades"): 0.35-0.65 -> 0.25-0.75.
# The old window was set by OWNER 5533476555 before we could measure L5's
# own pre-fill coverage. Measured against the FROZEN model actually running
# (analysis/l5/l5_action_model_v2.json, sha256[:16] bbb55bf90638d2bd, the
# same model_sha stamped on every live decision row), L5 has pre-fill OPEN
# policy in ALL SEVEN price bands, and 0.35-0.65 admitted exactly ONE of
# them whole -- p45-55c, 119 observations, 18.5% of L5's 643 pre-fill opens.
# We had restricted ourselves to the THIRD-richest band and excluded the two
# richest: p25-45c (154) and p55-75c (143). 0.25-0.75 admits all three whole
# -> 416/643 = 64.7%, and every band added is better supported than the one
# we were already trading. Deliberately NOT 0.10-0.90 (90%): p<10c (29) and
# p>90c (35) are thin, and the extremes are where pair economics and
# residual risk diverge most. See analysis/l5/ENTRY_BAND_L5_COVERAGE.md.
# This moves NO money limit: per-action $2.50, per-leg $3.25, per-market
# $7.50, residual 6 units and the -$15.00 daily stop are untouched. Bands
# whose band|TTE cell has no L5 policy (p25-45c and p55-75c below 30 s)
# still fail closed on off_distribution_hold -- widening admission never
# widens the distribution.
SPRING_BAND_LO, SPRING_BAND_HI = D("0.25"), D("0.75")   # OWNER 5533476555 -> 2026-09-10
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
        # OWNER 5558859447 #5: canonical state inputs beside the v1 ones --
        # the outcome price of the last own fill (never mirrored) and the
        # model-scale (behavioral shares) inventory, so the state key is
        # independent of the safety-scaled executable size
        self.last_px_outcome: Optional[D] = None
        self.last_size_behavioral: Optional[str] = None
        self.up_shares_behavioral: float = 0.0
        self.dn_shares_behavioral: float = 0.0
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
        self.last_px_outcome = px
        self.actions.append({
            "t": datetime.now(timezone.utc).isoformat(),
            "relation": relation, "side": "YES" if side_up else "NO",
            "px": str(px), "qty": str(qty), "notional": str(px * qty),
            "inventory_after": self.snapshot(),
        })

    def record_behavioral_shares(self, side_up: bool, shares: float,
                                 size_bucket: Optional[str] = None) -> None:
        """Model-scale accounting of ONE fill: L5's target size (shares)
        for the state|relation the fill was made in, on the filled side."""
        if side_up:
            self.up_shares_behavioral += float(shares)
        else:
            self.dn_shares_behavioral += float(shares)
        if size_bucket:
            self.last_size_behavioral = size_bucket
        # LIVE 2026-09-07T08:13-08:15Z (R4, aec-atpcq-aleher-tundur): after a
        # completed pair the behavioral ledger kept NETTING across it -- two
        # OPEN targets (+10, +10) against two COMPLETE targets (+11.18,
        # +10.03) read -0.27, i.e. "flat", while the venue book held 5.10
        # YES ($2.447) unmatched. "flat" after an open is a key L5's history
        # never contains, so every decision fell to off_distribution_hold and
        # the leg R4 exists to complete sat untouched. A matched pair closes
        # a behavioral sequence: when the EXECUTABLE book is flat the next
        # open starts a fresh one, exactly as it does in a new window. This
        # is an accounting fix in the adapter; the frozen model, lookup and
        # gate are untouched, and it applies identically on resume replay.
        if self.up == self.dn:
            self.up_shares_behavioral = 0.0
            self.dn_shares_behavioral = 0.0

    def behavioral_residual_shares(self) -> Optional[float]:
        if self.up_shares_behavioral == 0.0 and self.dn_shares_behavioral == 0.0:
            return None
        return self.up_shares_behavioral - self.dn_shares_behavioral

    def snapshot(self) -> dict:
        return {"up": str(self.up), "dn": str(self.dn),
                "matched": str(self.matched),
                "residual": str(self.residual),
                "avg_up": str(self.avg_up()) if self.up else None,
                "avg_dn": str(self.avg_dn()) if self.dn else None,
                "spent": str(self.spent()),
                # OWNER 5567269295: the unmatched-DOLLAR path, on every
                # inventory snapshot, so the R4 report can show it per decision
                "unmatched_usd": str(unmatched_usd(self))}


def spring_state_key_v1(inv: Inventory, market_px: D,
                        settlement_t: Optional[float], now: float) -> str:
    """The pre-canonical key (YES-scale last price, unit residual), kept
    on every record for the before/after; never a lookup input now."""
    tte = (max(settlement_t - now, 0.0) if settlement_t
           else SPORTS_TTE_DEFAULT_S)
    px = inv.last_px if inv.last_px is not None else market_px
    return state_key(float(px), tte, inv.behavioral_residual())


def spring_state_key(inv: Inventory, market_px: D,
                     settlement_t: Optional[float], now: float,
                     ask: Optional[D] = None) -> str:
    """Canonical base-state key (OWNER 5558859447 #5, same rule as the
    Gemini shadow's lookup v3): post-fill the OUTCOME price of the last own
    fill (the frozen model is not mirror-symmetric); pre-fill the cheaper
    touch (the outcome the shared open convention buys); the imbalance in
    the same behavioral shares as last_size once fills carry their
    behavioral size, else the unit residual."""
    tte = (max(settlement_t - now, 0.0) if settlement_t
           else SPORTS_TTE_DEFAULT_S)
    if inv.last_px_outcome is not None:
        px = inv.last_px_outcome
    elif ask is not None:
        px = min(D(str(market_px)), q1.ONE - D(str(ask)))
    else:
        px = market_px
    shares = inv.behavioral_residual_shares()
    resid = shares if shares is not None else inv.behavioral_residual()
    return state_key(float(px), tte, resid)


def record_behavioral_fill(inv: Inventory, fid_art: Optional[Mapping[str, Any]],
                           base_state: str, relation: str, side_up: bool) -> None:
    """After a fill: the model-scale size (bucket + shares) the clone
    behaviorally made -- L5's p50 for this state|relation -- on the filled
    side. Unknown -> the executed bucket, zero shares (unit residual)."""
    from src.polymarket_l5.action_model import SIZE_BUCKETS, _bucket
    row = fid.l5_target_size(fid_art, base_state, relation) if fid_art else {}
    p50 = row.get("shares_p50") if isinstance(row, Mapping) else None
    try:
        p50 = float(p50) if p50 is not None else None
    except (TypeError, ValueError):
        p50 = None
    inv.record_behavioral_shares(side_up, p50 or 0.0,
                                 _bucket(p50, SIZE_BUCKETS) if p50 is not None else "q<5")


def spring_proposal(model: Mapping[str, Any], inv: Inventory, skey: str,
                    skey_v1: str) -> dict:
    """The frozen v2 policy looked up on the canonical key, with the v1
    lookup's off flag recorded beside it (OWNER 5558859447 #5): pre-fill
    the canonical history keys no first-in-window transition, so the only
    faithful relation is OPEN and off-distribution means the BASE state is
    absent; post-fill the behavioral last_size (never the executed q<5)."""
    pre_fill = (inv.up + inv.dn) == 0
    last_rel = inv.last_rel or "open"
    last_size_b = (inv.last_size_behavioral if not pre_fill and inv.last_size_behavioral
                   else "q<5")
    v1 = pv2.v2_dist(model, skey_v1, last_rel, "q<5")
    prop = pv2.proposal(model, skey, last_rel, last_size_b,
                        residual=inv.behavioral_residual())
    base = model.get("base") or {}
    if pre_fill:
        prop["recommended_relation"] = "open"
        prop["relation_ranked"] = [{"relation": "open", "p": 1.0}]
        prop["persist_extend_p"], prop["switch_complete_p"] = 0.0, 0.0
        prop["off_distribution"] = skey not in base
        prop["state_n"] = sum((base.get(skey) or {}).values())
        tier = ("pre_fill:first_action_is_open (canonical history keys no "
                "first-in-window transition; off = base state absent)")
    else:
        tier = "full->base->marginal on the canonical key"
    prop["lookup"] = {
        "lookup_version": pv2.CANONICAL_LOOKUP_VERSION,
        "state": skey, "state_v1": skey_v1, "last_rel": last_rel,
        "last_size_executed": "q<5", "last_size_behavioral": last_size_b,
        "pre_fill": pre_fill, "off_distribution_v1": bool(v1["backed_off"]),
        "full_key_v1": v1["state"], "lookup_tier": tier,
        "residual_units": inv.behavioral_residual(),
        "residual_shares_behavioral": inv.behavioral_residual_shares(),
        "price_convention": ("outcome price of the last own fill"
                             if inv.last_px_outcome is not None
                             else "cheaper touch (outcome the open convention buys)"),
        "model_sha": str(model.get("_sha256_16") or ""),
    }
    return prop


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
                return session_notional_cap()
            total += price * qty
    for oid, row in submitted.items():
        term = terminal.get(oid)
        if term is None:                       # unknown outcome: full spend
            price, qty = micro._d(row.get("price")), micro._d(row.get("qty"))
            if price is None or qty is None:
                return session_notional_cap()
            total += price * qty
            continue
        got = micro._d(term.get("filled_qty"))
        price = micro._d(term.get("price")) or micro._d(row.get("price"))
        if got is None or price is None:
            return session_notional_cap()
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


def mark_noop(dec: dict, reason: str, divergence: Optional[str] = None) -> dict:
    """OWNER 5558072959 #1 (LIVE 02:29-04:03Z: 160 of 240 decisions recorded
    as acted produced NO order -- unpriceable complements, band exits and
    spend-cap stops were logged as executed actions). A decision that
    submits nothing is recorded as HOLD with the named refusal; the
    relation the policy drew/attempted is kept in `attempted`."""
    if dec.get("acted") not in ("hold", None):
        dec["attempted"] = dec.get("acted")
    dec["acted"] = "hold"
    dec["refusal"] = reason
    dv = divergence or f"safety_adaptation:{reason}"
    if str(dec.get("divergence") or "none").startswith("none"):
        dec["divergence"] = dv
        dec["divergence_class"] = divergence_class(dv)
    return dec


def spring_complement_price(avg_cost: D, opp_ask: D, tick: D,
                            touch: Optional[D] = None) -> \
        tuple[Optional[D], dict]:
    """Maker-only completion price on the deficit side: the tighter of the
    spring pair target (observation cap, pair <= 0.98 per OWNER DECISION
    5533476555) and the hard >=1c-after-known-fees bound, floored to the
    venue tick and strictly below the opposite ask. The hard bound always
    participates in the min(), so the widened observation cap can never
    admit a pair with <1c net edge. The caller's report shows which
    regime was executable."""
    if complement_pricing() == "l5_touch" and touch is not None:
        # OWNER PROGRAM 5574156328 (R5F): "If he says COMPLETE at 1.02,
        # our replication target is 1.02 -- not an arbitrary 1.00". The
        # COMPLETE is priced at L5's own target -- the deficit-side touch
        # -- with NO pair ceiling: the pair it implies (above par included)
        # is reported on the decision, never gated. The only bounds left
        # are the venue's: legal price range and post-only (strictly below
        # the opposite ask, so a touch that already crosses is trimmed by
        # one tick and labelled as the execution adaptation it is).
        px = (D(str(touch)) / tick).to_integral_value(rounding=ROUND_DOWN) * tick
        trimmed = px >= D(str(opp_ask))
        if trimmed:
            px = D(str(opp_ask)) - tick
            px = (px / tick).to_integral_value(rounding=ROUND_DOWN) * tick
        pair = D(str(avg_cost)) + px
        hard = q1.complement_bound(avg_cost)
        info = {"regime": "l5_touch", "l5_touch_px": str(touch),
                "pair_cost_max": None, "observation_cap": str(SPRING_PAIR_TARGET),
                "pair_at_touch": str(D(str(avg_cost)) + D(str(touch))),
                "pair_at_chosen": str(pair), "above_par": bool(pair > q1.ONE),
                "fee_safe": bool(hard is not None and px <= D(str(hard))),
                "hard_bound_px": str(hard) if hard is not None else None,
                "post_only_trim": bool(trimmed),
                "labels": "L5 fidelity: touch pricing, pair reported not gated"}
        # LIVE 2026-09-07 19:59-20:00Z (aec-setkamecz-tomvoj-ticjir): L5's
        # COMPLETE at touch 0.87 -> 0.96 was refused three times because
        # this branch applied the executor's OWN entry range
        # (q1.MIN_PRICE..MAX_PRICE = 0.20..0.80) as if it were the venue's,
        # and the refusal was labelled a venue adaptation. Under l5_touch
        # the only price bounds left are the VENUE'S: one tick above zero,
        # one tick below one (a 1.00 contract cannot be bought below par
        # by a maker). The residual was held instead and lost -3.42; the
        # touch pair would have been 1.20-1.29. OWNER 5574762432: "above-par
        # pair cost is reported/accounted, not behaviorally gated".
        # OWNER 5577710815 approved removing this distortion: the
        # catastrophic shell (per-market 7.50, action sizing, residual
        # units, concurrency 1, maker-only, strict admission, deadman,
        # -15.00 daily stop) is untouched, and NO other band replaces it.
        if px < tick or px > q1.ONE - tick:
            return None, {**info, "regime": "outside_venue_price_range",
                          "venue_price_range": f"{tick}..{q1.ONE - tick}"}
        return px, {**info, "chosen_px": str(px),
                    "venue_price_range": f"{tick}..{q1.ONE - tick}"}
    # OWNER 5567269295 (R4): the completion ceiling follows the ACTIVE rung.
    # R1..R3L keep 0.98 with the >=1c hard bound exactly as before. A rung
    # stating pair_cost_max 1.00 lets a COMPLETE -- and only a COMPLETE,
    # this function prices nothing else -- go to par: the measured R3L
    # rebate (0.808% of filled dollars) pays for a pair up to ~1.003, and
    # par is the owner's HARD ceiling ("do not operationally spend rebate
    # beyond par"). pair_cost_max() itself never returns above 1.00.
    ceiling = pair_cost_max()
    hard = q1.complement_bound(
        avg_cost, min_edge=min(q1.MIN_EDGE, q1.ONE - ceiling))
    spring = ceiling - D(str(avg_cost))
    info = {"spring_pair_target": str(ceiling),
            "pair_cost_max": str(ceiling),
            "observation_cap": str(SPRING_PAIR_TARGET),
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
# OWNER 5560559108 #5: every live decision row is labelled with the safety
# shell it was produced under. Bump this string whenever a shell parameter
# (per-market cap, residual cap, concurrency, taker, fee-safe target, rest)
# changes so the frozen evaluator can split cohorts by shell.
SAFETY_SHELL_VERSION = "R1/cap0.05/residual3/conc1/maker-only/fee-safe0.98/rest30s"


# OWNER 5560851070: the per-market spend cap, the residual cap and the
# shell label follow the ACTIVE ramp rung (set by the campaign from its
# durable state / the consumed owner approval); the constants above are
# the R1 values and stay the defaults.
def session_notional_cap() -> D:
    return micro.per_market_first_leg_cap()


def max_residual_units() -> int:
    from src import pmus_l5_ramp_ladder as ladder
    return int(ladder.active().get("residual_units") or MAX_RESIDUAL_UNITS)


def max_unmatched_usd() -> Optional[D]:
    """OWNER 5567269295: the ACTIVE rung's cap on UNMATCHED dollars per
    market, None for a units-only rung."""
    from src import pmus_l5_ramp_ladder as ladder
    return ladder.max_unmatched_usd()


def pair_cost_max() -> D:
    """The ACTIVE rung's hard completion ceiling (0.98 unless the rung says
    otherwise; never above 1.00)."""
    from src import pmus_l5_ramp_ladder as ladder
    return ladder.pair_cost_max()


def complement_pricing() -> str:
    """OWNER PROGRAM 5574156328 (R5F): "fee_safe" (R1..R4) or "l5_touch"
    (a COMPLETE priced at L5's own touch, pair reported not gated)."""
    from src import pmus_l5_ramp_ladder as ladder
    return ladder.complement_pricing()


def rest_policy() -> str:
    from src import pmus_l5_ramp_ladder as ladder
    return ladder.rest_policy()


def maker_rest_max_s() -> float:
    """How long a post-only maker order may REST before the session
    cancels it: R1..R4 the fixed 30 s (SPORTS_MAKER_REST_MAX_S); under
    rest_policy "model" (R5F) the model's own next_reevaluation_s drives
    the rest and this is only its floor (rest_floor_s), so the order is
    cancelled the moment L5 would re-decide rather than 30 s later. The
    early-cancel checks (still_wanted / post-only) are unchanged."""
    from src import pmus_l5_ramp_ladder as ladder
    if ladder.rest_policy() == "model":
        return float(ladder.rest_floor_s())
    return SPORTS_MAKER_REST_MAX_S


def unmatched_usd(inv: "Inventory") -> D:
    """Dollars NOT protected by a matched pair: unmatched shares x their own
    average cost -- exactly what is lost if that side expires worthless.
    (R3L wisc-nd: 19.27 x 0.3262 = $6.2855, the realized loss to the cent.)
    A matched pair pays $1 whatever happens and carries none."""
    if inv.residual > 0:
        return (inv.residual * (inv.avg_up() or D(0))).quantize(D("0.0001"))
    if inv.residual < 0:
        return (-inv.residual * (inv.avg_dn() or D(0))).quantize(D("0.0001"))
    return D("0.0000")


def unmatched_usd_after(inv: "Inventory", side_up: bool, px: D, qty: D) -> D:
    """What unmatched_usd would read if an order of ``qty`` at outcome cost
    ``px`` on ``side_up`` filled in full. Pure arithmetic on the exact
    inventory; nothing is recorded."""
    up, dn = inv.up, inv.dn
    cost_up, cost_dn = inv.cost_up, inv.cost_dn
    if side_up:
        up, cost_up = up + qty, cost_up + px * qty
    else:
        dn, cost_dn = dn + qty, cost_dn + px * qty
    if up > dn:
        return ((up - dn) * (cost_up / up)).quantize(D("0.0001"))
    if dn > up:
        return ((dn - up) * (cost_dn / dn)).quantize(D("0.0001"))
    return D("0.0000")


def safety_shell_version() -> str:
    from src import pmus_l5_ramp_ladder as ladder
    return ladder.shell_version()


def daily_loss_stop() -> D:
    """The ACTIVE rung's daily realized-loss stop (R1/R2C: the absolute
    -$1.00; OWNER 5561465636 R3L: -$15.00), from the ladder."""
    from src import pmus_l5_ramp_ladder as ladder
    return ladder.daily_loss_stop()


# OWNER live decision 2026-09-06 (uptime): under a rung whose concurrency
# is above one the campaign may hold more than one market at a time. The
# campaign -- never this module and never the executor -- declares the
# markets it OWNS here before running a session; a position in one of
# them does not block a sibling market's first leg. Everything else is
# unchanged: an open order ANYWHERE still blocks, a position in a market
# the campaign does not own still blocks, one outstanding order per
# session, maker/post-only, fee-safe 0.98, HOLD to settlement. Empty (the
# default, and every concurrency-1 rung) is the original one-market lock.
_CONCURRENT_OWNED: set = set()


def set_concurrent_owned(slugs=()) -> frozenset:
    global _CONCURRENT_OWNED
    _CONCURRENT_OWNED = {str(s) for s in (slugs or ())}
    return frozenset(_CONCURRENT_OWNED)


def concurrent_owned() -> frozenset:
    return frozenset(_CONCURRENT_OWNED)
# OWNER 5554106892: prebuilt, OFF by default ("1" enables): a capped
# EXTEND falls to COMPLETE when COMPLETE carries at least this mass
CAP_FALLBACK_ENV = "PMUS_SPRING_CAP_FALLBACK_COMPLETE"
CAP_FALLBACK_MIN_P = 0.20
# OWNER 5556232035 (Phase 1): "modal" (argmax, default) or "stochastic"
# (execute the seeded draw from the frozen v2 masses -- L5's own
# conditional distribution). Neither changes the model, the gates or
# the caps; the choice is recorded on every decision (action_selection).
ACTION_SELECTION_ENV = "PMUS_SPRING_ACTION_SELECTION"
# OWNER 5556751941 #1: read-only 2 s cadence counterfactual while an
# order rests (synthetic decisions, logged separately, never acted on)
CF_2S_CADENCE_S = 2.0
CF_2S_MAX = 300                          # per session

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
    if not isinstance(doc, dict):
        return {"proven": False, "reason": "malformed"}
    return reconcile_later_acceptance(reconcile_complement_proof(doc))


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


# OWNER GO 5586502825 (Option A). The proof is an ACCOUNT-WIDE boolean, but
# until now it could only ever be re-established on a market we were already
# HOLDING: _prove_complement_acceptance builds the opposite-side order the
# RESIDUAL needs, and its first line is `deficit_up = inv.residual < 0`.
#
# That made the gate a one-way door. Measured live 2026-09-08: the proof was
# revoked at 13:09:40Z on aec-setkameua-zhyole-strole (CC7NPY37MR3X, YES 0.64
# against a fresh opposite ask of 0.67), the last residual then cleared, and
# for the next 55 minutes every one of 181 campaign cycles selected a market
# and died at complement_acceptance_unproven. Flat account -> no residual ->
# no proof order can be constructed -> no first leg -> no residual. A gate
# that fails closed with no path back to open is not a safety property, it is
# an outage.
#
# The owner authorised the narrowest possible re-arm: when FLAT, the same
# venue-minimum post-only probe may be built against the SELECTED market.
# What counts as proof is UNCHANGED -- the venue itself must show the order
# RESTING. Deliberately NOT changed: the L5 model, per-action sizing, the
# $7.50 per-market cap, concurrency 1, maker-only, and the -$15.00 daily
# stop. The probe is not an L5 decision and is never recorded as one.
FLAT_PROBE_STAGE = "complement_acceptance_flat"

# #93 control group (OWNER 5588925913: compare ACCEPTED orders against
# rejected ones). Post-hoc snapshots on accepted orders, bounded so the
# diagnostic can never become load on the recorder's status endpoint.
CONTROL_BOOK_MAX_PER_SESSION = 24
CONTROL_BOOK_MIN_GAP_S = 5.0
# ORDINARY-COMPLETE BOOK CAPTURE (owner P1, 2026-09-08). The authenticated
# book reached only 1.1% of accepted COMPLETEs, because it was attached to
# rejections and to the bounded #93 control group and nothing else. Without
# it "our price was above par" cannot be separated from "the far side was
# empty", which is the confound sitting under the strongest finding in the
# fill-predictor study.
#
# WHY THIS CANNOT MOVE ORDER LATENCY OR LIVE BEHAVIOUR:
#   * it runs in _harvest, AFTER the order has resolved, so nothing sits in
#     front of a submission;
#   * it reads the recorder's status document only. Unlike the #93 control
#     path it makes NO venue call, so it consumes no rate-limit budget;
#   * the read is already bounded by STATUS_FETCH_TIMEOUT_S and fails soft;
#   * one failure trips a per-session breaker, so a hung recorder costs one
#     timeout for the whole session rather than one per completion;
#   * it only ever writes diagnostic fields onto a row that has already been
#     reported, and never returns a value any decision reads.
COMPLETE_BOOK_MAX_PER_SESSION = 8
COMPLETE_BOOK_MIN_GAP_S = 2.0
# PER-PROCESS by accident until 2026-09-08: this dict is module-level, so
# CONTROL_BOOK_MAX_PER_SESSION capped the whole PROCESS at 24 snapshots
# rather than 24 per session, which is why the control group stalled at
# 72 rows across three deployments. run_session() now resets it, which is
# what its name and its docstring always claimed.
_control_state = {"n": 0, "last": 0.0}
_complete_book_state = {"n": 0, "last": 0.0, "broken": False}

# OWNER GO (#167, narrow, OPEN only): construct the first leg's touch from
# the book the MATCHING ENGINE uses, not from a gateway BBO that disagrees
# with it. Measured on 28 OPEN observations carrying both books: in 12
# (43%) our gateway book said the price was inside while the authenticated
# book showed it crossing. Accepted OPEN controls crossed 0 of 11; rejected
# OPEN controls crossed 14 of 17.
#
# An authenticated book older than this cannot speak to a submission being
# made now, so it is ignored rather than trusted -- the same bar the
# forensics module convicts on.
AUTH_TRIM_MAX_AGE_S = 10.0
# The read sits in front of a submission, which is the one place this
# codebase does not spend latency. Over budget, it fails OPEN (no trim)
# rather than delaying the order.
AUTH_TRIM_SLOW_MS = 750.0
FLAT_PROBE_LABEL = ("venue-acceptance evidence probe at the venue minimum; "
                    "NOT an L5 decision, NOT counted as an L5 action, and "
                    "never recorded in the behavioural fidelity artifact")


def flat_probe_plan(bbo: Mapping[str, Any], tick: D) -> dict:
    """Buy YES by JOINING THE BID, one venue-minimum lot, post-only.

    Joining the bid is non-crossing by construction whenever bid < ask, so a
    rejection here cannot be a cross and is unambiguous evidence about the
    venue's acceptance rules -- which is the only question the proof asks.
    It is also the price real maker orders use, so this tests the convention
    that actually matters rather than a safely-distant one nobody trades at.

    A locked or crossed book (bid >= ask) has no non-crossing join, so there
    is no evidence to be had and the probe declines rather than guessing."""
    bid, ask = bbo.get("bid"), bbo.get("ask")
    if bid is None or ask is None:
        return {"px": None, "why": "no_two_sided_book"}
    bid, ask = D(str(bid)), D(str(ask))
    if bid <= 0 or ask <= 0 or bid >= ask:
        return {"px": None, "why": f"not_two_sided:{bid}/{ask}"}
    if tick <= 0 or (bid / tick) % 1 != 0:
        return {"px": None, "why": f"bid_off_tick:{bid}/{tick}"}
    return {"px": bid, "side": "OUTCOME_SIDE_YES", "wire_px": str(bid),
            "opp_ask": str(ask), "regime": "flat_join_bid",
            "why": "joining the bid cannot cross"}


def prove_complement_when_flat(t, slug: str, *, clock=time.monotonic,
                               sleep=time.sleep, wall=time.time) -> dict:
    """One venue-minimum post-only probe on `slug`, to re-arm the gate when
    the account holds nothing. RESTING (or a fill) proves it; anything else
    leaves the gate exactly as closed as it was.

    Every failure mode writes evidence and returns; none raises into the
    campaign loop, because a probe that can halt the lane is worse than a
    lane that is merely blocked."""
    prior = load_complement_proof()
    proof: dict[str, Any] = {
        "proven": False, "slug": slug, "stage": FLAT_PROBE_STAGE,
        "attempted_utc": datetime.now(timezone.utc).isoformat(),
        "attempted_epoch": wall(), "residual": "0",
        "owner_ref": "#167 comment 5586502825 (Option A)"}

    def _skip(why: str) -> dict:
        # "no legal price at this book" is not venue evidence about
        # acceptance: say so, keep the prior state, and do NOT consume the
        # retry interval by writing attempted_epoch.
        print("PMUS_COMPLEMENT_ACCEPTANCE " + json.dumps(
            {"skipped": why, "stage": FLAT_PROBE_STAGE, "slug": slug,
             "prior_proven": prior.get("proven")},
            separators=(",", ":"), default=str), flush=True)
        return {**prior, "skipped": why}

    try:
        bbo = q1.outcome_bbo(t, slug, "OUTCOME_SIDE_YES")
        spec = micro.market_micro_spec(t, slug, bbo["bid"])
    except Exception as exc:                          # noqa: BLE001
        return _skip(f"book_or_spec_unreadable:{type(exc).__name__}")

    plan = flat_probe_plan(bbo, D(str(spec["tick"])))
    proof["plan"] = plan
    proof["book"] = {"yes_bid": str(bbo["bid"]), "yes_ask": str(bbo["ask"])}
    if plan["px"] is None:
        return _skip(f"no_non_crossing_join:{plan['why']}")

    px = D(str(plan["px"]))
    qty = D(str(spec["minimum_trade_qty"]))
    try:
        oid = micro.place_limit_buy(t, slug, plan["side"], px, qty,
                                    FLAT_PROBE_STAGE, post_only=True)
    except Halt as exc:
        proof["reason"] = f"preview_or_preflight:{exc}"
        save_complement_proof(proof)
        return proof
    proof["order_id"] = oid
    proof["submitted"] = {"side": plan["side"], "px": str(px),
                          "wire_px": plan["wire_px"], "qty": str(qty),
                          "post_only": True}

    start = clock()
    got, end_reason, observed = D("0"), "rest_bound", None
    try:
        while clock() < start + COMPLEMENT_PROOF_REST_MAX_S:
            order = q1._order_status(t, oid)
            observed = str(order.get("state") or order.get("status")
                           or "").upper()
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
        except Exception:                             # noqa: BLE001
            pass
        fill = micro._terminal_positive_fill(t, oid, qty)
        if fill is not None:
            got, end_reason = D(str(fill["qty"])), "filled_on_cancel"

    proven = bool(got > 0 or end_reason.startswith("resting"))
    proof.update({
        "proven": proven, "observed_state": observed,
        "end_reason": end_reason, "rest_s": round(clock() - start, 3),
        "filled_qty": str(got),
        "proven_utc": (datetime.now(timezone.utc).isoformat()
                       if proven else None),
        "labels": FLAT_PROBE_LABEL})
    if not proven:
        proof["reason"] = end_reason
    # A fill leaves a real, venue-minimum position. It is small but it is
    # NOT nothing, so it is written to the durable ledger with its probe
    # provenance intact rather than quietly dropped -- the settlement
    # auditor and the venue-vs-ledger reconciliation must both see it.
    if got > 0:
        q1.ledger_append({"kind": "order_filled", "spring": True,
                          "slug": slug, "order_id": oid,
                          "stage": FLAT_PROBE_STAGE, "side": plan["side"],
                          "px": str(px), "qty": str(got),
                          "relation": None, "l5_action": False,
                          "labels": FLAT_PROBE_LABEL})
    save_complement_proof(proof)
    return proof


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


# Proof records the CURRENT acceptance rule never writes (f465fbc9, OWNER
# 5556815940): "no legal price at this book" is not venue evidence, and a
# post-only rejection within ONE tick of the lagging fresh ask is a cross,
# not a revocation. A record of either class on disk was written by an
# earlier build under the retired rule.
RETIRED_PROOF_REASON_PREFIX = "no_fee_safe_complement:"
RETIRED_REVOCATION_PREFIX = "revoked:complete_rejected_at_fresh_non_crossing_price"
LEGACY_FAIL_CLOSED_TICK = D("0.005")   # finest PMUS sports tick
# end_reasons a COMPLETE order can only carry after the venue HELD it
# (the deadline / reprice loops poll a resting order; a rejection raises
# before either): venue acceptance of a post-only opposite-side order
ACCEPTED_REST_ENDS = ("resting:", "deadline", "repriced:")
# the on-disk record's own event time, whichever kind of write it was
PROOF_TIME_KEYS = ("attempted_utc", "revoked_utc", "reconciled_utc",
                   "proven_utc")


def _utc_key(s: Any) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def proof_record_time(doc: Mapping[str, Any]) -> Optional[datetime]:
    times = [t for t in (_utc_key(doc.get(k)) for k in PROOF_TIME_KEYS) if t]
    return max(times) if times else None


def reconcile_later_acceptance(doc: dict) -> dict:
    """A current-rule not-proven record (probe rejected / revoked) is
    superseded ONLY by a venue acceptance the ledger recorded strictly
    later than the record's own event: the last venue event wins, as in
    complement_proof_from_ledger, without ever letting an older acceptance
    outrank a newer rejection. Persisted once with the superseded record
    attached; no ledger evidence, or none later = the record stands."""
    if doc.get("proven") is True:
        return doc
    when = proof_record_time(doc)
    if when is None:
        return doc
    derived = complement_proof_from_ledger()
    last = derived.get("last_event") or {}
    if derived.get("proven") is not True:
        return doc
    t_last = _utc_key(last.get("t"))
    if t_last is None or t_last <= when:
        return doc
    out: dict[str, Any] = {
        "proven": True, "reason": None,
        "reconciled_utc": datetime.now(timezone.utc).isoformat(),
        "reconciled_from": "ledger",
        "superseded_by": "later_venue_acceptance",
        "slug": last.get("slug"), "order_id": last.get("order_id"),
        "end_reason": last.get("end_reason"), "proven_utc": last.get("t"),
        "superseded_record": dict(doc), "ledger_evidence": derived,
        "labels": "the venue held/filled a post-only opposite-side order "
                  "built by this code after the record's rejection; "
                  "re-derived from the durable ledger; no cap/gate change"}
    save_complement_proof(out)
    return out


def rejection_margin_ticks(order: Mapping[str, Any]) -> Optional[D]:
    """Ticks between a rejected post-only price and the FRESH opposite
    ask (the _classify_rejection margin). Rows written before the margin
    was recorded are measured at the finest sports tick: the fail-closed
    reading, since a coarser real tick can only make the margin smaller
    (fewer revocations). None = no fresh ask was recorded."""
    rec = order.get("rejection_margin_ticks")
    if rec is not None:
        try:
            return D(str(rec))
        except Exception:                             # noqa: BLE001
            return None
    ask, px = order.get("fresh_opp_ask"), order.get("px")
    if ask is None or px is None:
        return None
    try:
        tick = D(str(order.get("tick") or LEGACY_FAIL_CLOSED_TICK))
        return (D(str(ask)) - D(str(px))) / tick
    except Exception:                                 # noqa: BLE001
        return None


def retired_rule_write(proof: Mapping[str, Any]) -> Optional[str]:
    """Why an on-disk proof record could only have been written by a
    build running the retired acceptance rule (None = a current-rule
    record, taken as is)."""
    reason = str(proof.get("reason") or "")
    if reason.startswith(RETIRED_PROOF_REASON_PREFIX):
        return "no_legal_price_write_retired_by_f465fbc9"
    if reason.startswith(RETIRED_REVOCATION_PREFIX):
        margin = rejection_margin_ticks(proof.get("revoked_order") or {})
        if margin is not None and margin <= 1:
            return "one_tick_revocation_retired_by_f465fbc9"
    return None


def complement_proof_from_ledger() -> dict:
    """The complement-acceptance proof re-derived from the durable ledger
    under the CURRENT rule: the last venue event wins, in ledger order.
    FOR: a complement_acceptance probe order the venue held RESTING or
    filled. AGAINST: a complement_acceptance probe the venue rejected, or
    a COMPLETE order rejected >= 2 ticks from the fresh opposite ask (or
    with no fresh ask recorded -- fail closed). proven=None when the
    ledger holds no such event."""
    # One order = one venue event, joined across the row kinds that
    # recorded it: `submitted` (stage, since Sep 3) + `order_terminal`
    # (end_reason, since Sep 5 15:01Z) for every order; the session_report
    # order row (fresh ask, margin, tick -- since 02:19Z) refines it when
    # present. The 01:47Z/02:11Z resting probes predate session_report
    # rows and exist only as submitted + order_terminal.
    path = q1.base.state_dir() / "ledger.jsonl"
    orders: dict[str, dict] = {}
    if path.exists():
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            kind = row.get("kind")
            if kind == "submitted" and row.get("order_id"):
                o = orders.setdefault(str(row["order_id"]), {})
                o.update({"order_id": str(row["order_id"]), "t": row.get("t"),
                          "slug": row.get("slug"), "stage": row.get("stage"),
                          "px": row.get("price")})
            elif kind == "order_terminal" and row.get("order_id"):
                o = orders.setdefault(str(row["order_id"]), {})
                o.update({"order_id": str(row["order_id"]), "i": i,
                          "t": row.get("t"), "slug": row.get("slug"),
                          "end_reason": row.get("end_reason"),
                          "filled_qty": row.get("filled_qty"),
                          "px": o.get("px") or row.get("price")})
            elif kind == "session_report":
                for r in row.get("orders") or []:
                    if not isinstance(r, dict) or not r.get("order_id"):
                        continue
                    o = orders.setdefault(str(r["order_id"]), {})
                    o.update({k: v for k, v in r.items() if v is not None})
                    o.setdefault("i", i)
                    o.update({"t": o.get("t") or row.get("t"),
                              "slug": o.get("slug") or row.get("slug"),
                              "session_id": row.get("session_id")})
    events: list[dict] = []
    for o in sorted((o for o in orders.values() if "i" in o),
                    key=lambda o: o["i"]):
        stage = str(o.get("stage") or "")
        end = str(o.get("end_reason") or "")
        relation = o.get("relation") or (
            "complete" if stage.startswith("spring_complete") else None)
        ev = {k: o.get(k) for k in ("t", "slug", "session_id", "order_id",
                                    "stage", "px")}
        ev["end_reason"] = end
        try:
            filled = D(str(o.get("filled_qty") or "0")) > 0
        except Exception:                             # noqa: BLE001
            filled = False
        if stage == "complement_acceptance":
            if filled or end.startswith("resting:"):
                events.append({**ev, "proven": True})
            elif end.startswith("rejected"):
                events.append({**ev, "proven": False,
                               "why": "probe_rejected"})
        elif relation == "complete" and (filled or
                                         end.startswith(ACCEPTED_REST_ENDS)):
            # LIVE 16:33-16:40Z (mil-cin 9.5): the probe's own post-only
            # NO was rejected one tick from the ask and wrote proven=false;
            # the venue then held and FILLED three post-only NO completes
            # built by this code on the same market -- exactly the
            # acceptance the owner's rule (5556038561 #3) asks the venue to
            # show -- but only the probe stage counted, and with the market
            # matched out no held market remained to re-probe on, so NEW
            # first legs stayed closed on a flat account with admitted
            # candidates. A COMPLETE the venue held (rested to deadline /
            # reprice, or observed resting) or filled is the same venue
            # evidence as a held/filled probe.
            events.append({**ev, "proven": True, "why": "complete_accepted"})
        elif relation == "complete" and end.startswith("rejected"):
            margin = rejection_margin_ticks(o)
            if margin is None or margin >= 2:
                events.append({**ev, "proven": False,
                               "why": "complete_rejected_at_fresh_"
                                      "non_crossing_price",
                               "margin_ticks": (str(margin)
                                                if margin is not None
                                                else None),
                               "fresh_opp_ask": o.get("fresh_opp_ask")})
    if not events:
        return {"proven": None, "events": 0, "last_event": None}
    last = events[-1]
    return {"proven": bool(last["proven"]), "events": len(events),
            "last_event": last,
            "last_proven_event": next((e for e in reversed(events)
                                       if e["proven"]), None)}


def reconcile_complement_proof(doc: dict) -> dict:
    """LIVE 11:26Z: the campaign sat in CAMPAIGN_WAITING:COMPLEMENT_UNPROVEN
    with four admitted markets because the proof file still carried the
    03:54Z "no_legal_maker_price" write -- a record f465fbc9 stopped
    persisting, left on disk by the build before it -- while the proof
    can only be re-proven on a HELD market's resume path and nothing was
    held. A record the current rule never writes is re-derived from the
    durable venue evidence (the ledger) under the current rule, persisted
    once with the superseded record attached, and printed. No evidence in
    the ledger = still not proven (fail closed)."""
    why = retired_rule_write(doc)
    if why is None:
        return doc
    derived = complement_proof_from_ledger()
    out: dict[str, Any] = {
        "reconciled_utc": datetime.now(timezone.utc).isoformat(),
        "reconciled_from": "ledger", "retired_rule_write": why,
        "superseded_record": dict(doc), "ledger_evidence": derived}
    if derived.get("proven") is None:
        out.update({"proven": False, "reason": f"no_ledger_evidence:{why}"})
    else:
        last = derived["last_event"]
        out.update({"proven": derived["proven"], "slug": last.get("slug"),
                    "order_id": last.get("order_id"),
                    "end_reason": last.get("end_reason"),
                    "proven_utc": (last.get("t") if derived["proven"]
                                   else None),
                    "reason": (None if derived["proven"]
                               else f"ledger:{last.get('why')}"),
                    "labels": "re-derived from the durable ledger under "
                              "the current acceptance rule; no cap/gate "
                              "change"})
    save_complement_proof(out)
    return out


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
        if refusal.startswith("unmatched_usd_cap"):
            # OWNER 5567269295: the R4 dollar cap, classified on its own
            return "safety_adaptation:unmatched_usd_cap"
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
    "safety_adaptation:unmatched_usd_cap": "safety-cap override (R4 dollar residual cap)",
    "safety_adaptation:spend_cap": "safety-cap override",
    "policy_flag:cap_fallback_complete": "safety-cap override (L5-consistent fallback)",
    "safety_adaptation:off_distribution_hold": "genuinely off-distribution state",
    "safety_adaptation:complement_not_priceable": "venue/execution adaptation",
    "safety_adaptation:complement_preview_rejected": "venue/execution adaptation",
    "safety_adaptation:complement_acceptance_unproven": "venue/execution adaptation",
    "behavioral:modal_argmax_vs_stochastic_draw": "model deficiency (argmax execution of a stochastic policy)",
    "venue_execution_adaptation:complete_below_unit_granularity":
        "venue/execution adaptation (shares-scale imbalance below one venue unit)",
    # the same row as written by the pre-fd1eafd4 path (ledger row
    # ab06a5a293e589d893cb1b33); kept so a rebuild classifies it identically
    "implementation_bug_candidate:complete_without_residual":
        "venue/execution adaptation (shares-scale imbalance below one venue unit)",
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
                 clock, sleep, still_wanted=None, ledger=None) -> tuple:
    """One outstanding order: rest until filled, until the deadline, or
    until ``still_wanted()`` names a reason the action is no longer the
    current L5 action; then cancel-and-harvest.

    Returns ``(filled_qty, rest_s, end_reason)`` with the exact
    authoritative filled Decimal quantity (0 for none) and the measured
    rest time, so category fill hazard can be estimated later.

    LIVE 2026-09-08 (vervas-kohboh, OWNER GO 5584132560 item 2): this used
    to ask for a cancel, discard the answer, read the order's status ONCE
    and never look again. Order CC1E3Y7TPQ45 was never confirmed cancelled,
    stayed live on the venue and filled 4.34 units 5.7 s later, invisible
    to the ledger for good; the market then read flat while it was 4.34
    long. So the cancel's verdict is now honoured, an unconfirmed cancel is
    re-read after a settle delay, and the order stays OPEN in ``ledger``
    until the VENUE reports a terminal state.
    """
    start = clock()
    fill = None
    end_reason = "deadline"
    if ledger is not None:
        ledger.track(oid, meta={"requested_qty": str(qty)})
    try:
        while clock() < until:
            status = q1._order_status(t, oid)
            if ledger is not None:
                ledger.ingest(oid, status)
            fill = micro.authoritative_fill(status, qty)
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
        # rejection pattern is only diagnosable if the reason is logged.
        # A rejection is also TERMINAL, so close the order here rather than
        # leaving it tracked as open -- an order left open on every
        # post-only rejection pins may_assert_flat false for the life of
        # the market (LIVE 12:14Z, CC6WEM7CCR42 / CC6WXBGXJR42).
        if ledger is not None:
            try:
                ledger.close_on_venue_rejection(oid, q1._order_status(t, oid))
            except Exception:                           # noqa: BLE001
                pass                # unreadable stays OPEN, never "closed"
        return D("0"), round(clock() - start, 3), f"rejected:{str(exc)[:120]}"
    if fill is None:
        cancelled = False
        try:
            cancelled = bool(q1.cancel_exact(t, oid))
        except RejectedNoFill:
            return D("0"), round(clock() - start, 3), end_reason
        except Exception:
            cancelled = False
        fill = micro._terminal_positive_fill(t, oid, qty)
        if not cancelled:
            # the venue did not confirm the cancel, so the order may still
            # be live and may still be filling. Wait out the race and read
            # again; the larger cumulative quantity is the true one.
            sleep(fl.CANCEL_SETTLE_S)
            again = micro._terminal_positive_fill(t, oid, qty)
            if again is not None and (fill is None or
                                      D(str(again["qty"])) > D(str(fill["qty"]))):
                fill = again
                end_reason = "filled_in_cancel_race"
            elif fill is None:
                end_reason = "unconfirmed_cancel"
        if fill is not None and end_reason == "deadline":
            end_reason = "filled_on_cancel"
    if ledger is not None:
        try:
            ledger.ingest(oid, q1._order_status(t, oid))
            ledger.close_if_terminal(oid, q1._order_status(t, oid))
        except Exception:                               # noqa: BLE001
            pass                    # unreadable stays OPEN, never "finished"
    got = D(str(fill["qty"])) if fill else D("0")
    return got, round(clock() - start, 3), end_reason


def _reconcile_fills(t: Transport, slug: str, ledger, inv: Inventory) -> dict:
    """Re-read every order the venue has not declared terminal, book any
    quantity they gained after we stopped waiting, and compare the venue's
    own traded quantity for this market with our ledger's.

    Returns the audit row plus `may_assert_flat`: whether any calculation
    that ASSUMES flatness, a zero residual or a locked value is allowed to
    proceed. It is False on disagreement AND on missing venue evidence --
    unknown is not agreement."""
    audit: dict = {"reconciled": None, "recovered_qty": "0",
                   "venue_vs_ledger": fl.venue_vs_ledger(None, None),
                   "may_assert_flat": False}
    if t is None or not slug:
        audit["why"] = "no transport or slug; nothing can be proven"
        return audit
    try:
        audit["reconciled"] = ledger.reconcile(
            lambda oid: q1._order_status(t, oid))
        audit["recovered_qty"] = audit["reconciled"]["recovered_qty"]
    except Exception as exc:                            # noqa: BLE001
        audit["reconcile_error"] = f"{type(exc).__name__}:{str(exc)[:80]}"
    try:
        from src import pmus_settlement_auditor as settle_audit
        venue_qty = settle_audit.traded_qty_for_slug(slug, t)
    except Exception:                                   # noqa: BLE001
        venue_qty = None
    audit["venue_vs_ledger"] = fl.venue_vs_ledger(venue_qty, inv.up + inv.dn)
    audit["may_assert_flat"] = fl.may_assert_flat(audit["venue_vs_ledger"])
    if audit["reconciled"] and (audit["reconciled"]["still_open"]
                                or audit["reconciled"]["unreadable"]):
        # an order the venue has not closed can still fill; nothing that
        # assumes a settled book may proceed while one is outstanding
        audit["may_assert_flat"] = False
        audit["open_orders_block_flat_claim"] = True
    if getattr(ledger, "contradictions", None):
        audit["may_assert_flat"] = False
        audit["contradictions"] = ledger.contradictions
    audit["provenance"] = getattr(ledger, "provenance", [])[-16:]
    return audit


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
    art = fid.load()
    for _, side_up, px, qty, rel in sorted(events, key=lambda e: e[0]):
        # the state the fill was made in is not persisted on legacy ledger
        # rows: rebuild it from the fill's own outcome price, the sports
        # default TTE and the model-scale residual BEFORE the fill
        before = inv.behavioral_residual_shares()
        base_state = state_key(float(px), SPORTS_TTE_DEFAULT_S,
                               before if before is not None else inv.behavioral_residual())
        inv.record(side_up, px, qty, rel)
        record_behavioral_fill(inv, art, base_state, rel, side_up)
    return inv


def resume_block_reason(t: Transport, slug: str) -> Optional[str]:
    """Account preflight for RESUMING a held market: any open order
    anywhere and any position in ANY OTHER market still block (one
    market at a time); a position in this market is the point.

    Under a concurrency>1 rung the campaign's OWN other held markets are
    declared in set_concurrent_owned() and do not block either; anything
    else still does."""
    owned = concurrent_owned()
    # concurrency 1 (every rung but the owner's concurrency rung) calls the
    # lock exactly as before, with no extra argument
    block = (micro.account_block_reason(t, allow_slugs=owned) if owned
             else micro.account_block_reason(t))
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


def run_session(*args: Any, **kwargs: Any) -> dict:
    """Run a session, then reconcile its fills on EVERY exit path.

    LIVE 2026-09-08 12:07Z: the reconciliation lived at the end of the
    action loop, so a session that ended by refusal or halt -- exactly the
    shape of the one that abandoned order CC1E3Y7TPQ45 -- returned with
    `flat_verified: null` and never re-read its own orders. The sessions
    most likely to leave an order in an ambiguous state were the ones that
    skipped the check. Reconciliation now happens here, outside the body,
    where no return path can bypass it."""
    # Diagnostic budgets are PER SESSION, which is what their names say.
    # Leaving them module-global capped the whole process instead: the #93
    # control group stopped at 24 snapshots per deployment and stalled at
    # 72 rows. Resetting here, at the one boundary every path crosses,
    # cannot change any decision -- these counters gate diagnostics only.
    _control_state.update({"n": 0, "last": 0.0})
    _complete_book_state.update({"n": 0, "last": 0.0, "broken": False})
    report = _run_session_body(*args, **kwargs)
    ledger = report.pop("_fill_ledger", None)
    inv = report.pop("_inventory_obj", None)
    if ledger is None or inv is None:
        return report                 # never reached the order stage
    if report.get("fill_reconciliation") is not None:
        return report                 # the normal path already did it
    t = args[0] if args else kwargs.get("t")
    recon = _reconcile_fills(t, report.get("slug") or "", ledger, inv)
    report["fill_reconciliation"] = recon
    report["flat_verified"] = bool(recon["may_assert_flat"])
    if recon["venue_vs_ledger"].get("agrees") is False:
        print("PMUS_SPRING_ALARM " + json.dumps(
            {"alarm": "venue_ledger_quantity_mismatch",
             "slug": report.get("slug"), "exit": report.get("status"),
             **recon["venue_vs_ledger"]}), flush=True)
    return report


def _run_session_body(t: Optional[Transport] = None,
                clock=time.monotonic, sleep=time.sleep,
                wall=time.time, slug: Optional[str] = None,
                gate_ok: bool = False,
                deadline_s: Optional[float] = None,
                resume: bool = False,
                decision_seed_offset: int = 0) -> dict:
    """One bounded sequential session on ONE market.

    ``decision_seed_offset`` (OWNER 5556815940 / Phase 1 replication bug):
    the seeded draw is sha256(slug|decision_idx|model_sha). With the
    index restarting at 0 on every resumed session, EVERY session on a
    held market replayed the same 6 draws (LIVE 02:32-04:00Z: 40
    sessions, byte-identical 4 complete / 2 extend), so the executed
    frequency could never converge on the masses. The campaign passes
    the number of decisions already persisted for this market; the
    global index is recorded on every decision and the draw stays
    replayable from the records.

    ``slug`` / ``gate_ok`` / ``deadline_s`` exist for the continuous
    selector (OWNER 5552036210): it hands in the market whose fresh
    AUTHENTICATED evidence just passed the strict guard, which IS the
    gate evidence -- so the operator-pasted PMUS_SPRING_GATE_EVIDENCE is
    not required on that path. The dual key is required on every path;
    nothing here can arm.
    """
    slug = (slug if slug is not None
            else os.getenv(SPRING_MARKET_ENV, "")).strip()
    import uuid
    session_id = uuid.uuid4().hex[:16]
    report: dict[str, Any] = {
        "session": "pmus-spring-l5-v1", "slug": slug, "realized_pnl": None,
        "session_id": session_id,
        "decision_seed_offset": int(decision_seed_offset or 0),
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
    if q1.realized_today() <= daily_loss_stop():
        return {**report, "status": "DAILY_LOSS_STOP",
                "daily_loss_stop_usd": str(daily_loss_stop()),
                "realized_today_usd": str(q1.realized_today())}

    t = t or Transport()
    if deadline_s is None:
        deadline_s = float(os.getenv(DEADLINE_ENV, "900"))
    max_actions = int(os.getenv(MAX_ACTIONS_ENV, "6"))   # probe-1 bound
    max_complete_actions = int(os.getenv(
        MAX_COMPLETE_ACTIONS_ENV, str(DEFAULT_MAX_COMPLETE_ACTIONS)))
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
        _owned = concurrent_owned()
        block = (resume_block_reason(t, slug) if resume
                 else (micro.account_block_reason(t, allow_slugs=_owned)
                       if _owned else micro.account_block_reason(t)))
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
        # OWNER GO 5584132560 item 2: one idempotent, order-keyed fill
        # ledger for this session. Every order we submit is tracked in it
        # and stays OPEN until the VENUE reports a terminal state, so a
        # fill that lands after we stop waiting is still booked.
        fill_ledger = fl.FillLedger()
        # the wrapper reconciles on every exit path, including refusals and
        # halts, so it needs these regardless of how the body returns
        report["_fill_ledger"] = fill_ledger
        report["_inventory_obj"] = inv
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
            # the LIVE completion ceiling (rung) and the observation cap
            # (admission), named separately since R4 split them
            "pair_target": str(pair_cost_max()),
            "observation_cap": str(SPRING_PAIR_TARGET),
            "session_notional_cap": str(session_notional_cap()),
            "residual_cap_units": max_residual_units(),
            "max_unmatched_usd": (str(max_unmatched_usd())
                                  if max_unmatched_usd() is not None else None),
            "safety_shell_version": safety_shell_version(),
            "prior_cumulative_notional": str(cum),
            "cap_basis": "authoritative FILLED notional (partials included); "
                         "unconfirmed submissions count in full",
            "maker_rest_max_s": maker_rest_max_s(),
            # OWNER GO 5589583477: how much completion work this session was
            # allowed, so a change in patience is visible in the evidence
            "max_actions_open_extend": max_actions,
            "max_actions_complete_extension": max_complete_actions,
            # OWNER PROGRAM 5574156328 (R5F): the fidelity levers, from the
            # rung, so every session row says which regime priced/rested it
            "rest_policy": rest_policy(),
            "complement_pricing": complement_pricing(),
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
            # OWNER 5588925913: "compare accepted orders against rejected
            # ones rather than studying only failures". Until now the
            # authenticated book was attached ONLY on rejection, so #93 had
            # a sample of failures and no control group -- every margin
            # statistic was conditioned on the outcome being explained.
            # The same snapshot is taken for orders the venue ACCEPTED.
            #
            # POST-HOC BY CONSTRUCTION: this runs after the order has
            # already resolved, so it adds nothing to the path in front of
            # a submission. Bounded per session and swallowed on any error,
            # because a diagnostic must never be able to halt a session.
            if not end_reason.startswith("rejected"):
                try:
                    _control_book(side_up_, px_, report["orders"][-1])
                except Exception:                      # noqa: BLE001
                    pass
                # OWNER P1: the authenticated book on ORDINARY completes, so
                # pair-at-touch economics can be told apart from an empty far
                # side. Status-document read only -- no venue call, no
                # rate-limit budget -- and post-hoc like the control above.
                if relation_ == "complete":
                    try:
                        _complete_book(side_up_, report["orders"][-1])
                    except Exception:                  # noqa: BLE001
                        pass

        def _venue_market_status(slug_: str) -> dict:
            """The venue's own market flags at rejection time (read-only;
            never raises; a failed read is reported as unreadable)."""
            try:
                resp = t.request("GET", f"/v1/market/slug/{slug_}",
                                 base=q1.GATEWAY)
                m = resp.get("market") if isinstance(resp, Mapping) else None
                if not isinstance(m, Mapping):
                    return {"status": None, "unreadable": "market_unparseable"}
                out = {"status": m.get("status"), "active": m.get("active"),
                       "closed": m.get("closed"), "archived": m.get("archived"),
                       "ep3Status": m.get("ep3Status")}
                # any other status-like field the venue serves (halts,
                # suspensions) is carried verbatim, never interpreted
                for k, v in m.items():
                    lk = str(k).lower()
                    if k not in out and isinstance(v, (str, bool, int)) and (
                            "status" in lk or "halt" in lk or "suspend" in lk
                            or "trading" in lk or "paused" in lk):
                        out[str(k)] = v
                return out
            except Exception as exc:                  # noqa: BLE001
                return {"status": None,
                        "unreadable": f"{type(exc).__name__}:{str(exc)[:80]}"}

        def _recorder_book(slug_: str, side_up_: bool) -> dict:
            """The AUTHENTICATED top of book the recorder holds for this
            market, beside the public gateway BBO the order was priced
            from (issue #167, R5F 2026-09-08 01:09Z).

            LIVE EVIDENCE: NO completes at 0.71 on malden-stoole filled
            twice and were then rejected four times while BOTH the
            pre-submit guard and the post-rejection re-read of
            `/v1/markets/{slug}/bbo` reported the opposite ask at 0.73 --
            two ticks of room -- and the venue reported the market
            MARKET_STATUS_OPEN. Neither a halt nor a stale public read
            explains that. The remaining candidate is that the public
            gateway BBO is not the book the matching engine post-only
            checks against; the recorder's authenticated stream is, and
            it is already available to this process. Read-only, fail
            soft, never blocking: a book we cannot read is reported as
            unreadable and nothing downstream changes."""
            from src import pmus_spring_l5_probe_entry as entry   # lazy: cycle
            try:
                status, source = entry.load_status_document(None)
            except Exception as exc:                  # noqa: BLE001
                return {"unreadable": f"{type(exc).__name__}:{str(exc)[:80]}"}
            if not isinstance(status, Mapping):
                return {"unreadable": "status_document_unavailable"}
            row = None
            for key in ("spring_watch", "top_markets"):
                for r in (status.get(key) or []):
                    if isinstance(r, Mapping) and r.get("slug") == slug_:
                        row = r
                        break
                if row is not None:
                    break
            if row is None:
                return {"unreadable": "slug_not_in_status_document",
                        "source": source, "status_utc": status.get("t")}
            # the recorder quotes YES; express the side we were pricing
            yes_bid, yes_ask = row.get("bid"), row.get("ask")
            out = {"source": source, "status_utc": status.get("t"),
                   "yes_bid": yes_bid, "yes_ask": yes_ask,
                   "authenticated_age_s": row.get(
                       "last_authenticated_update_age_s"),
                   "both_sides_served": row.get("both_sides_served")}
            try:
                if side_up_:
                    out["opp_ask_authenticated"] = str(D(str(yes_ask)))
                else:
                    out["opp_ask_authenticated"] = str(q1.ONE - D(str(yes_bid)))
            except Exception:                          # noqa: BLE001
                out["opp_ask_authenticated"] = None
            return out

        def _authenticated_trim(px_: D, side_up_: bool, gateway_opp_ask: Any,
                                tick: D) -> dict:
            """Trim a FIRST LEG that the authenticated book says would cross.

            L5's rule is "join the touch". If the book we compute the touch
            from disagrees with the book the matching engine uses, we
            compute the WRONG touch and post-only refuses us -- 21.3% of
            OPEN orders, and 12 of 28 priced where our book said inside and
            the venue's said crossing. Reading the venue's own book makes
            the touch correct; it is a fidelity fix, not a new rule.

            STRICTLY ONE-DIRECTIONAL. The opposite ask used is the MINIMUM
            of the two books, so the price can only ever move DOWN, and the
            function asserts it. It is arithmetically incapable of making
            us more aggressive, which is what makes it safe to run in front
            of a submission at all.

            FAILS OPEN, ALWAYS. An unreadable book, a stale one, a slow
            read, a missing field or any exception leaves the price exactly
            as L5 chose it. A first leg is never blocked by this -- the
            worst case is the rejection we already get today."""
            info: dict = {"applied": False, "px_in": str(px_)}
            t0 = time.monotonic()
            try:
                book = _recorder_book(slug, side_up_)
                info["elapsed_ms"] = round(
                    (time.monotonic() - t0) * 1000.0, 1)
                if book.get("unreadable"):
                    info["why"] = f"unreadable:{book.get('unreadable')}"
                    return info
                if info["elapsed_ms"] > AUTH_TRIM_SLOW_MS:
                    info["why"] = "slow_read:failed_open"
                    return info
                age = book.get("authenticated_age_s")
                if age is None or float(age) > AUTH_TRIM_MAX_AGE_S:
                    info["why"] = f"stale:{age}"
                    return info
                auth = book.get("opp_ask_authenticated")
                if auth in (None, ""):
                    info["why"] = "no_authenticated_opposite_ask"
                    return info
                auth_ask = D(str(auth))
                gw = D(str(gateway_opp_ask))
                safe = min(gw, auth_ask)
                info.update({"gateway_opp_ask": str(gw),
                             "authenticated_opp_ask": str(auth_ask),
                             "safe_opp_ask": str(safe),
                             "authenticated_age_s": age})
                if px_ < safe:
                    info["why"] = "already_non_crossing_on_both_books"
                    return info
                new = ((safe - tick) / tick).to_integral_value(
                    rounding=ROUND_DOWN) * tick
                if new <= 0 or new >= q1.ONE:
                    info["why"] = f"trim_leaves_legal_range:{new}"
                    return info
                # the invariant that makes this safe, asserted not promised
                if new > px_:
                    info["why"] = "refused:would_raise_price"
                    return info
                info.update({"applied": True, "px_out": str(new),
                             "why": "gateway book disagreed with the venue's;"
                                    " touch recomputed from the venue's"})
                return info
            except Exception as exc:                   # noqa: BLE001
                info["why"] = f"error_failed_open:{type(exc).__name__}"
                return info

        def _complete_book(side_up_: bool, row: dict) -> None:
            """The authenticated book on an ORDINARY completion observation.

            NOT the #93 control group and never pooled with it: control rows
            carry acceptance_control and a fresh venue BBO, these carry
            book_capture="complete_observation" and no venue call at all.
            Three populations now exist -- rejection, #93 control, ordinary
            complete -- and each is labelled so none can be silently merged.

            Cheap and interruptible by construction: a status-document read
            with an existing timeout, bounded per session, and a breaker that
            gives up for the rest of the session on the FIRST failure, so an
            unreachable recorder costs one timeout rather than one per
            completion. It writes diagnostics onto an already-resolved row
            and returns nothing any decision reads."""
            if _complete_book_state["broken"]:
                return
            if row.get("recorder_book") is not None:
                return                      # control or rejection got there
            if _complete_book_state["n"] >= COMPLETE_BOOK_MAX_PER_SESSION:
                return
            now_ = time.monotonic()
            if now_ - _complete_book_state["last"] < COMPLETE_BOOK_MIN_GAP_S:
                return
            _complete_book_state["last"] = now_
            _complete_book_state["n"] += 1
            book = _recorder_book(slug, side_up_)
            if book.get("unreadable"):
                # one failure is enough: stop paying the timeout this session
                _complete_book_state["broken"] = True
            row["book_capture"] = "complete_observation"
            row["recorder_book"] = book

        def _control_book(side_up_: bool, px_: D, row: dict) -> None:
            """The CONTROL GROUP for #93: the same authenticated book and
            the same fresh opposite ask, on an order the venue accepted.

            Without this every margin figure in the investigation is
            conditioned on rejection, which cannot distinguish "the venue
            rejects orders N ticks inside" from "our orders are usually
            N ticks inside". Rows carry acceptance_control=True so the
            two populations can never be silently pooled.

            Bounded: at most CONTROL_BOOK_MAX_PER_SESSION snapshots and one
            per CONTROL_BOOK_MIN_GAP_S, so a busy session cannot turn this
            into a status-endpoint hammer."""
            now_ = time.monotonic()
            if _control_state["n"] >= CONTROL_BOOK_MAX_PER_SESSION:
                return
            if now_ - _control_state["last"] < CONTROL_BOOK_MIN_GAP_S:
                return
            _control_state["last"] = now_
            _control_state["n"] += 1
            row["acceptance_control"] = True
            row["recorder_book"] = _recorder_book(slug, side_up_)
            try:
                b = q1.outcome_bbo(t, slug, "OUTCOME_SIDE_YES" if side_up_
                                   else "OUTCOME_SIDE_NO")
                row["fresh_opp_ask"] = str(D(str(b["ask"])))
            except Exception as exc:                   # noqa: BLE001
                row["fresh_opp_ask"] = None
                row["control_bbo_error"] = f"{type(exc).__name__}"

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
            # LIVE 03:22:56Z: YES 0.225 rejected while the REST book read
            # ask 0.230 one second later, during a collapse from 0.30 to
            # 0.05 -- a REST snapshot lags a fast book by more than a
            # tick. Within ONE tick of the fresh ask the rejection is
            # a cross (ambiguous under lag); only a margin of >= 2 ticks
            # is evidence against this code's acceptance model.
            margin = (opp_ask - px_) / D(str(spec["tick"]))
            crossed = margin <= 1
            klass = ("venue/execution adaptation:"
                     "post_only_cross_after_book_move" if px_ >= opp_ask
                     else "venue/execution adaptation:"
                          "post_only_cross_within_1_tick_of_lagging_ask"
                     if crossed else "implementation bug candidate:"
                          "rejected_at_non_crossing_price")
            # R5F 2026-09-07 (issue #167 comment 5577507821): 36 of 72
            # classified rejections sat 2-12 ticks away from the fresh
            # REST ask, in bursts of identical orders seconds apart, with
            # no venue reason. The venue has been seen serving
            # MARKET_STATUS_HALTED (in-play / maintenance) while the BBO
            # still reads. Read the venue's own market status at rejection
            # time so the next such burst is named. OBSERVABILITY ONLY:
            # `crossed` (and everything that consumes it) is unchanged.
            status = _venue_market_status(slug)
            if status.get("status") not in (None, "MARKET_STATUS_OPEN"):
                klass += f";venue_market_status={status.get('status')}"
            # the authenticated book beside the public one the order was
            # priced from; when they disagree the class says so, because
            # that disagreement IS the finding
            book = _recorder_book(slug, side_up_)
            auth = book.get("opp_ask_authenticated")
            if auth is not None and not crossed:
                try:
                    if px_ >= D(str(auth)):
                        klass += ";authenticated_book_crossed"
                        book["disagrees_with_gateway"] = True
                except Exception:                      # noqa: BLE001
                    pass
            return {"class": klass, "crossed": crossed, "opp_ask": str(opp_ask),
                    "margin_ticks": str(margin), "venue_market": status,
                    "recorder_book": book}

        def _still_wanted(side_up_: bool, px_: D, relation_: str,
                          skey_: str):
            """Why the resting order is no longer the current L5 action
            (None = keep resting). Re-reads the live book each poll.

            Also hosts the READ-ONLY 2 s cadence counterfactual (OWNER
            5556751941 #1): every ~2 s of wall time while an order rests,
            the frozen model is asked what L5 would do at the CURRENT
            state and the seeded draw is logged as a SYNTHETIC decision
            (report["counterfactual_2s"]). Nothing is acted on and these
            never count as live evidence."""
            track: dict[str, Any] = {"last_cf": 0.0, "min_opp_ask": None,
                                     "max_touch": None, "polls": 0}

            def check() -> Optional[str]:
                try:
                    b = q1.outcome_bbo(t, slug, "OUTCOME_SIDE_YES")
                except Exception as exc:              # noqa: BLE001
                    return f"bbo_unreadable:{type(exc).__name__}"
                if b.get("bid") is None or b.get("ask") is None:
                    return "book_one_sided"
                track["polls"] += 1
                now_w = wall()
                if now_w - track["last_cf"] >= CF_2S_CADENCE_S and \
                        len(report.get("counterfactual_2s") or []) < CF_2S_MAX:
                    track["last_cf"] = now_w
                    try:
                        k2 = spring_state_key(inv, b["bid"], settlement_t, now_w,
                                              ask=b.get("ask"))
                        p2 = spring_proposal(
                            model, inv, k2,
                            spring_state_key_v1(inv, b["bid"], settlement_t, now_w))
                        idx2 = len(report.get("counterfactual_2s") or [])
                        d2 = fid.sample_relation(
                            p2.get("relation_ranked"),
                            fid.seed_material(f"{slug}|cf2s", idx2, model_sha))
                        report.setdefault("counterfactual_2s", []).append({
                            "t_utc": datetime.now(timezone.utc).isoformat(),
                            "state": k2, "v2_recommended": p2["recommended_relation"],
                            "relation_ranked": p2.get("relation_ranked"),
                            "sampled": d2.get("sampled"), "seed": d2.get("seed"),
                            "off_distribution": bool(p2.get("off_distribution")),
                            "live_order": {"relation": relation_, "px": str(px_),
                                           "side": "YES" if side_up_ else "NO"},
                            "book": {"yes_bid": str(b["bid"]), "yes_ask": str(b["ask"])},
                            "labels": "SYNTHETIC 2s-cadence counterfactual; "
                                      "not acted on; not live evidence"})
                    except Exception:                 # noqa: BLE001
                        pass
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
                # observable book path while resting (COMPLETE target-
                # price counterfactual, OWNER 5556751941 #2): how close the
                # opposite ask came to L5's touch price, and the best touch
                oa = D(str(opp_ask))
                track["min_opp_ask"] = (str(oa) if track["min_opp_ask"] is None
                                        else str(min(oa, D(track["min_opp_ask"]))))
                track["max_touch"] = (str(touch) if track["max_touch"] is None
                                      else str(max(touch, D(track["max_touch"]))))
                if px_ >= D(str(opp_ask)):
                    return f"would_cross:{opp_ask}"
                if relation_ in requote_relations() and touch != px_:
                    return f"touch_moved:{touch}"
                if inv.up + inv.dn > 0:
                    k = spring_state_key(inv, b["bid"], settlement_t, wall(),
                                         ask=b.get("ask"))
                    if k != skey_:
                        p = spring_proposal(
                            model, inv, k,
                            spring_state_key_v1(inv, b["bid"], settlement_t, wall()))
                        if p["recommended_relation"] != relation_:
                            return (f"l5_action_changed:"
                                    f"{p['recommended_relation']}")
                return None
            check.track = track                       # type: ignore[attr-defined]
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
                # LIVE 03:23-03:54Z: with the YES book at 0.05-0.18 no
                # fee-safe complement existed and the probe wrote
                # proven=false four times, which kept NEW first legs
                # closed for hours. "No legal price at this book" is not
                # venue evidence about acceptance: log it, keep the prior
                # proof state untouched, and do not consume the retry
                # interval (attempted_epoch stays as it was).
                prior = load_complement_proof()
                print("PMUS_COMPLEMENT_ACCEPTANCE " + json.dumps(
                    {"skipped": f"no_fee_safe_complement:{plan['regime']}",
                     "slug": slug, "plan": plan, "book": proof["book"],
                     "prior_proven": prior.get("proven")},
                    separators=(",", ":"), default=str), flush=True)
                return {**prior, "skipped": f"no_fee_safe_complement:{plan['regime']}"}
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
                record_behavioral_fill(inv, fid_art, last_skey.get("k") or "",
                                       "complete", deficit_up)
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
        complete_actions = 0          # REPORTING only, never control flow
        complete_reject_streak = 0
        # OWNER probe stop conditions 2 and 3
        complete_submissions = 0
        complete_rejections = 0
        infra_rejections = 0

        def action_budget() -> int:
            """One monotonic counter, one limit, and the LIMIT moves.

            While a residual is open the session may keep working it into a
            pair past the opening budget. `actions` counts every iteration,
            so this is bounded by construction however the relations fall."""
            return (max_actions + max_complete_actions if inv.residual != 0
                    else max_actions)

        while actions < action_budget() and not out_of_time():
            if complete_reject_streak >= COMPLETE_REJECT_STREAK_MAX:
                report["stop_reason"] = (
                    f"complete_rejections_consecutive:"
                    f"{complete_reject_streak}")
                break
            if infra_rejections >= INFRA_REJECT_MAX:
                report["stop_reason"] = (
                    f"complete_infra_rejections:{infra_rejections}")
                break
            if (complete_submissions >= COMPLETE_REJECT_RATE_MIN_N
                    and complete_rejections
                    > COMPLETE_REJECT_RATE_MAX * complete_submissions):
                report["stop_reason"] = (
                    f"complete_reject_rate:{complete_rejections}"
                    f"/{complete_submissions}")
                break
            bbo = q1.outcome_bbo(t, slug, "OUTCOME_SIDE_YES")
            spec = micro.market_micro_spec(t, slug, bbo["bid"])
            skey = spring_state_key(inv, bbo["bid"], settlement_t, wall(),
                                    ask=bbo.get("ask"))
            skey_v1 = spring_state_key_v1(inv, bbo["bid"], settlement_t, wall())
            last_skey["k"] = skey
            tte_s = (max(settlement_t - wall(), 0.0) if settlement_t
                     else SPORTS_TTE_DEFAULT_S)
            # behavioral units, sign preserved — never int() a fractional
            # venue residual (issue #167 comment 5520710312); canonical
            # lookup v3 with the v1 key recorded beside it (OWNER 5558859447 #5)
            prop = spring_proposal(model, inv, skey, skey_v1)
            # OWNER 5556232035 / Phase 1 largest mismatch: L5's policy is
            # a CONDITIONAL DISTRIBUTION over relations; executing its
            # argmax ("modal") can never match L5's action frequencies
            # (live acted L1 0.97 vs sampled 0.08 on 144 decisions).
            # PMUS_SPRING_ACTION_SELECTION=stochastic executes the SAME
            # seeded inverse-CDF draw that the shadow log already carries
            # (seed = sha256(slug|idx|model_sha); replayable). Every
            # safety gate below applies to the drawn relation unchanged.
            selection = os.getenv(ACTION_SELECTION_ENV, "modal").strip().lower()
            # GLOBAL per-market index (persisted decisions before this
            # session + local index): the seed never repeats across resumes
            decision_idx = int(decision_seed_offset or 0) + len(
                report.get("decisions") or [])
            model_sha = str(model.get("_sha256_16") or "")
            draw = fid.sample_relation(
                prop.get("relation_ranked"),
                fid.seed_material(slug, decision_idx, model_sha))
            import hashlib as _hl
            decision_id = _hl.sha256(
                f"{session_id}|{slug}|{decision_idx}|{model_sha}".encode()
            ).hexdigest()[:24]
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
                    inv.behavioral_residual()) >= max_residual_units():
                # bounded imbalance (same bound as the shared planner):
                # no further same-side EXTEND past the residual cap; a
                # COMPLETE is still allowed, and nothing is flattened
                relation, refusal = "hold", (
                    f"residual_cap:{abs(inv.behavioral_residual())}"
                    f">={max_residual_units()}")
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
                decision_idx=decision_idx,
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
                 # immutable provenance for the fidelity evaluator's
                 # dedupe (OWNER 5556815940 #2)
                 "session_id": session_id,
                 "decision_idx": decision_idx,
                 "decision_id": decision_id,
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
                 # OWNER 5559167134 #3: the per-decision counterfactual --
                 # the action the clone would take with NO safety cap
                 # (the seeded draw when a cap converted it), and which
                 # cap bound; the spend-cap conversion below updates it
                 "would_act_without_safety_cap": (
                     sampled if str(refusal or "").startswith("residual_cap")
                     and sampled else relation),
                 "cap_binding": (["residual_cap"]
                                 if str(refusal or "").startswith("residual_cap")
                                 else []),
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
                 "size_divergence": "venue_minimum_vs_l5_target",
                 # OWNER 5558167805 #1: post-mark_noop evidence row (the
                 # frozen evaluator scores this cohort separately from the
                 # pre-fix rows and never combines them)
                 "evidence_schema": 2,
                 # OWNER 5560559108 #5: the safety shell this row was
                 # produced under, so the behavioral layer stays comparable
                 # across any later shell change (concurrency, caps)
                 "safety_shell_version": safety_shell_version(),
                 # OWNER 5558859447 #5: which lookup produced this row --
                 # both state keys, both last_size values, both off flags
                 "lookup": prop.get("lookup"),
                 # OWNER 5558072959 #4: the behavioral size target and
                 # every transformation down to the executable size, so
                 # the size-cap override is never scored as L5 behavior
                 "size_transformations": [
                     {"step": "l5_target_p50_shares",
                      "value": (shadow.get("l5_target_size") or {}).get("shares_p50"),
                      "source": "frozen fidelity artifact (state|relation)"},
                     {"step": "behavioral_unit_contracts",
                      "value": str(inv.unit) if inv.unit else None,
                      "reason": "residual adapter: one L5 size unit = one venue-minimum contract"},
                     {"step": "executable_contracts",
                      "value": str(spec["qty"]),
                      "reason": f"owner size cap ${session_notional_cap()}/market + venue "
                                "minimum; declared safety override, not L5 behavior"}]})
            # The extension is for COMPLETING a residual, never for opening
            # or extending on it. Past the original budget only a complete
            # may act; anything else ends the session here, exactly as the
            # old single-budget condition did.
            if actions >= max_actions and relation != "complete":
                if complete_actions:
                    report["stop_reason"] = (
                        f"opening_budget_spent:{actions}/{max_actions}"
                        f":extension_is_completion_only_not_{relation}")
                break
            if relation == "hold":
                sleep(max(prop["next_reevaluation_s"], MIN_REEVAL_S))
                actions += 1
                continue

            if relation in ("open", "extend"):
                # OWNER GO (#167, reply to comment 5600883240): the entry band must
                # not end a session that is holding inventory.
                #
                # OWNER 5552841170 #1 set this band as OPEN admission: the
                # 0.35-0.65 window on a flat book, plus the venue's own legal
                # maker range. That part is unchanged and still refuses the
                # OPEN or EXTEND outright -- this is NOT permission to average
                # further into a collapsing held side.
                #
                # What changed is the EXIT. The refusal used to `break`, which
                # ends the whole session, so a market whose bid drifted out of
                # range while we held an unmatched leg stopped being worked at
                # all. Measured over the R5F campaign
                # (analysis/l5/L5_COMPLETION_FORENSICS.md): the band fired 42
                # times and 39 of those were while HOLDING unmatched
                # inventory, and the three markets it ended for 6, 9 and 12
                # consecutive cycles are the three largest stranded losses in
                # the settled cohort (-8.83 of -9.24). The band was never
                # adjudicating a COMPLETE directly; it was killing the session
                # that would have completed.
                #
                # So a refusal on held inventory now converts to
                # completion-only work, exactly as the spend cap below does,
                # and only a refusal while FLAT still stops the session. The
                # completion that follows remains bounded by the completion
                # budget, the rejection streak breaker, the clock and every
                # dollar cap; none of those move here.
                in_band = SPRING_BAND_LO <= bbo["bid"] <= SPRING_BAND_HI
                if not q1.entry_ok(bbo["bid"]) or \
                        (inv.up + inv.dn == 0 and not in_band):
                    relation = "complete" if inv.residual != 0 else "stop"
                    dec = report["decisions"][-1]
                    dec["divergence"] = \
                        f"safety_adaptation:entry_band_left->{relation}"
                    dec["divergence_class"] = \
                        divergence_class("safety_adaptation:entry_band_left")
                    dec["acted_draw"] = dec["acted"]
                    dec["would_act_without_safety_cap"] = dec["acted"]
                    dec["cap_binding"] = \
                        dec.get("cap_binding", []) + ["entry_band_left"]
                    dec["entry_band_left"] = {
                        "bid": str(bbo["bid"]),
                        "q1_entry_ok": q1.entry_ok(bbo["bid"]),
                        "in_observation_band": in_band,
                        "held_inventory": str(inv.up + inv.dn),
                        "outcome": ("completion_only" if relation == "complete"
                                    else "session_stopped_flat")}
                    dec["acted"] = ("hold" if relation == "stop" else relation)
                    if relation == "stop":
                        # Named, because a zero-order session must be
                        # distinguishable from an unfilled one in the log.
                        report["stop_reason"] = (
                            f"entry_band_left:bid={bbo['bid']}:"
                            f"q1_entry_ok={q1.entry_ok(bbo['bid'])}")
                        mark_noop(dec, report["stop_reason"],
                                  "safety_adaptation:entry_band_left")
                        break

            if relation in ("open", "extend"):
                # spend caps: session + durable cumulative, before sizing
                if cum + (inv.spent() - prior_spent) + \
                        spec["entry_notional"] > session_notional_cap():
                    relation = "complete" if inv.residual != 0 else "stop"
                    report["decisions"][-1]["divergence"] = \
                        f"safety_adaptation:spend_cap->{relation}"
                    report["decisions"][-1]["divergence_class"] = \
                        divergence_class("safety_adaptation:spend_cap")
                    # the EXECUTED relation is what the record says was
                    # acted; the draw the cap overrode is kept beside it
                    report["decisions"][-1]["acted_draw"] = \
                        report["decisions"][-1]["acted"]
                    report["decisions"][-1]["would_act_without_safety_cap"] = \
                        report["decisions"][-1]["acted"]
                    report["decisions"][-1]["cap_binding"] = \
                        report["decisions"][-1].get("cap_binding", []) + ["spend_cap"]
                    report["decisions"][-1]["acted"] = (
                        "hold" if relation == "stop" else relation)
            if relation == "sell" or relation == "stop":
                report["stop_reason"] = ("l5_sell_is_noop" if relation == "sell"
                                         else "spend_cap_reached")
                mark_noop(report["decisions"][-1], report["stop_reason"],
                          "safety_adaptation:spend_cap" if relation == "stop"
                          else "venue_execution_adaptation:l5_sell_is_noop")
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
                        mark_noop(report["decisions"][-1], "no_priceable_side",
                                  "venue_execution_adaptation:no_priceable_side")
                        break
                    # OWNER 5556751941 #3: side-mapping telemetry on every
                    # live OPEN -- the convention input, its output and
                    # the executed side, so the gate's side dimension is
                    # measured from records, not assumed
                    report["decisions"][-1]["open_side"] = {
                        "yes_bid": str(bbo["bid"]), "yes_ask": str(bbo["ask"]),
                        "yes_touch": str(bbo["bid"]),
                        "no_touch": str(q1.ONE - D(str(bbo["ask"]))),
                        "convention": "cheaper touch; ties -> YES",
                        "convention_side": "YES" if side_up else "NO",
                        "executed_side": "YES" if side_up else "NO"}
                else:
                    side_up = inv.residual > 0     # extend the surplus side
                px = bbo["bid"] if side_up else (q1.ONE - bbo["ask"])
                px = (px / spec["tick"]).to_integral_value(
                    rounding=ROUND_DOWN) * spec["tick"]
                # OWNER GO, OPEN ONLY: recompute the touch from the book the
                # matching engine actually uses when it disagrees with the
                # gateway. EXTEND is deliberately excluded -- its rejections
                # are still unexplained (#93) and the GO was narrow.
                if relation == "open":
                    _trim = _authenticated_trim(
                        px, side_up,
                        bbo["ask"] if side_up else (q1.ONE - D(str(bbo["bid"]))),
                        spec["tick"])
                    report["decisions"][-1]["authenticated_trim"] = _trim
                    if _trim.get("applied"):
                        px = D(str(_trim["px_out"]))
                qty = spec["qty"]
                # OWNER 5567269295 (R4): the residual cap in DOLLARS. An
                # order that would leave more than the rung's unmatched
                # cap standing is refused BEFORE submission; an order that
                # reduces unmatched exposure is never refused (a COMPLETE
                # is priced in its own branch below and this cannot touch
                # it). Classified under its own name so a refusal here is
                # never folded into residual_cap / spend_cap when the
                # owner asks whether the $2.50 cap blocked L5-consistent
                # actions.
                cap_usd = max_unmatched_usd()
                if cap_usd is not None:
                    before = unmatched_usd(inv)
                    after = unmatched_usd_after(inv, side_up, px, qty)
                    dec = report["decisions"][-1]
                    dec["unmatched_usd_before"] = str(before)
                    dec["unmatched_usd_after_if_filled"] = str(after)
                    dec["max_unmatched_usd"] = str(cap_usd)
                    if after > before and after > cap_usd:
                        dec["would_act_without_safety_cap"] = relation
                        dec["cap_binding"] = list(dec.get("cap_binding") or []) + \
                            ["unmatched_usd_cap"]
                        mark_noop(dec, f"unmatched_usd_cap:{after}>{cap_usd}",
                                  "safety_adaptation:unmatched_usd_cap")
                        sleep(max(prop["next_reevaluation_s"], MIN_REEVAL_S))
                        actions += 1
                        continue
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
                        mark_noop(report["decisions"][-1], report["stop_reason"])
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
                        mark_noop(report["decisions"][-1], report["stop_reason"])
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
                        maker_rest_max_s())),
                    clock, sleep,
                    still_wanted=_still_wanted(side_up, px, relation, skey),
                    ledger=fill_ledger)
                # every SUBMITTED order is reported, filled or not: the
                # ladder is submitted -> filled -> settled -> realized
                _harvest(oid, f"spring_{relation}_{actions + 1}", side_up,
                         px, qty, relation, got, rest_s, end_reason)
                if end_reason.startswith("rejected"):
                    rej = _classify_rejection(side_up, px)
                    report["orders"][-1]["rejection_class"] = rej["class"]
                    report["orders"][-1]["fresh_opp_ask"] = rej["opp_ask"]
                    report["orders"][-1]["rejection_margin_ticks"] = \
                        rej.get("margin_ticks")
                    report["orders"][-1]["tick"] = str(spec["tick"])
                    report["orders"][-1]["venue_market"] = rej.get("venue_market")
                    report["orders"][-1]["recorder_book"] = rej.get("recorder_book")
                    if rej["crossed"]:
                        sleep(max(prop["next_reevaluation_s"], MIN_REEVAL_S))
                if got > 0:
                    fill = micro._terminal_positive_fill(t, oid, qty) or \
                        {"price": px, "qty": got}
                    inv.record(side_up, D(str(fill["price"])), got, relation)
                    record_behavioral_fill(inv, fid_art, skey, relation, side_up)
            else:                               # complete: deficit side only
                if inv.residual == 0:
                    # LIVE 12:35:33Z (cs2 fokus-nemi): the canonical state
                    # is keyed in behavioral SHARES (v3) and read imb1-10
                    # (-1.38 shares) while the executable inventory in
                    # venue units was exactly flat; the model's COMPLETE
                    # has nothing below one venue unit to complete. A
                    # HOLD is the only faithful executable action: size
                    # granularity of the venue unit, not a defect.
                    mark_noop(report["decisions"][-1], "complete_without_residual",
                              "venue_execution_adaptation:complete_below_unit_granularity")
                    break
                deficit_up = inv.residual < 0
                avg = inv.avg_dn() if deficit_up else inv.avg_up()
                # completing YES-deficit means buying YES; price the pair
                # against the average cost of the SURPLUS side
                surplus_avg = inv.avg_up() if not deficit_up else inv.avg_dn()
                opp = q1.outcome_bbo(
                    t, slug,
                    "OUTCOME_SIDE_YES" if deficit_up else "OUTCOME_SIDE_NO")
                # OWNER 5556751941 #2: L5's own target for a COMPLETE is
                # the touch of the deficit side (join the bid). R1..R4: the
                # live order uses the fee-safe bound instead and the touch
                # is a READ-ONLY counterfactual. OWNER PROGRAM 5574156328
                # (R5F, complement_pricing "l5_touch"): the touch IS the
                # order price ("our replication target is 1.02 -- not an
                # arbitrary 1.00"); the pair it implies is reported, and
                # any remaining difference (a post-only trim) is labelled
                # as the execution adaptation it is.
                l5_touch = (D(str(opp["bid"])) / spec["tick"]).to_integral_value(
                    rounding=ROUND_DOWN) * spec["tick"]
                at_touch_regime = complement_pricing() == "l5_touch"
                px, econ = spring_complement_price(
                    surplus_avg, opp["ask"], spec["tick"],
                    touch=l5_touch if at_touch_regime else None)
                report["pair_economics"] = econ
                hard = q1.complement_bound(surplus_avg)
                pair_at_touch = D(str(surplus_avg)) + l5_touch
                report["decisions"][-1]["counterfactual_l5_price"] = {
                    "l5_touch_px": str(l5_touch), "executed_px": str(px),
                    "delta_ticks": str((l5_touch - px) / spec["tick"]) if px is not None else None,
                    "pair_at_touch": str(pair_at_touch),
                    "fee_safe_at_touch": bool(
                        hard is not None and l5_touch <= D(str(hard))
                        and pair_at_touch <= SPRING_PAIR_TARGET),
                    # OWNER 5567269295 report line: "complement opportunities
                    # blocked/allowed by old vs new bound", per decision
                    "within_old_bound_0_98": bool(pair_at_touch <= SPRING_PAIR_TARGET),
                    "within_rung_bound": (True if at_touch_regime
                                          else bool(pair_at_touch <= pair_cost_max())),
                    "rung_pair_cost_max": (None if at_touch_regime
                                           else str(pair_cost_max())),
                    "unlocked_by_rung_bound": bool(
                        SPRING_PAIR_TARGET < pair_at_touch <= pair_cost_max()),
                    "unmatched_usd_before": str(unmatched_usd(inv)),
                    "would_cross_at_touch": bool(l5_touch >= D(str(opp["ask"]))),
                    "complement_pricing": complement_pricing(),
                    "l5_consistent_above_par": bool(at_touch_regime
                                                    and pair_at_touch > q1.ONE),
                    "class": ("no_difference" if px is not None and l5_touch == px
                              else ("execution_adaptation:post_only_below_ask"
                                    if at_touch_regime and px is not None
                                    else "safety_override:fee_safe_complement_bound")),
                    "labels": ("LIVE price = L5 touch (R5F); pair reported, not gated"
                               if at_touch_regime else
                               "READ-ONLY counterfactual; live order unchanged")}
                if px is None:
                    # LIVE 02:29-04:03Z: 108 COMPLETE decisions with no
                    # fee-safe / legal maker price were recorded as acted
                    # -- they are HOLDs with the pricing regime named
                    mark_noop(report["decisions"][-1],
                              f"complement_not_priceable:{econ.get('regime')}",
                              # under l5_touch pricing the only way to be
                              # unpriceable is the venue's price range /
                              # post-only, never a safety bound of ours;
                              # the regime names which
                              (f"venue_execution_adaptation:{econ.get('regime')}"
                               if at_touch_regime else
                               "safety_adaptation:fee_safe_complement_bound"))
                    sleep(MIN_REEVAL_S)
                    actions += 1
                    continue
                qty = min(inv.residual.copy_abs(), spec["qty"])
                oid = micro.place_limit_buy(
                    t, slug, "OUTCOME_SIDE_YES" if deficit_up
                    else "OUTCOME_SIDE_NO", px, qty,
                    f"spring_complete_{actions + 1}", post_only=True)
                reached_order_stage = True
                sw = _still_wanted(deficit_up, px, "complete", skey)
                got, rest_s, end_reason = _await_order(
                    t, oid, qty,
                    min(stop_at, clock() + max(
                        prop["next_reevaluation_s"], MIN_REEVAL_S,
                        maker_rest_max_s())),
                    clock, sleep, still_wanted=sw, ledger=fill_ledger)
                _harvest(oid, f"spring_complete_{actions + 1}", deficit_up,
                         px, qty, "complete", got, rest_s, end_reason)
                report["orders"][-1]["rest_book"] = dict(sw.track)
                report["orders"][-1]["l5_touch_px"] = str(l5_touch)
                # honest observable only: did the opposite ask ever reach
                # L5's touch price while we rested (a resting bid there
                # COULD have traded; no fill is claimed)
                mo = sw.track.get("min_opp_ask")
                report["orders"][-1]["l5_touch_reached_by_opposite_ask"] = (
                    bool(D(mo) <= l5_touch) if mo is not None else None)
                # Patience is for orders that RESTED. A rested-and-unfilled
                # complete clears the streak; a refusal advances it, and the
                # loop head ends the session at three.
                complete_submissions += 1
                if end_reason.startswith("rejected"):
                    complete_reject_streak += 1
                    complete_rejections += 1
                    if is_infra_rejection(
                            end_reason,
                            report["orders"][-1].get("rejection_class")):
                        infra_rejections += 1
                    report["orders"][-1]["complete_reject_streak"] = \
                        complete_reject_streak
                    report["orders"][-1]["complete_reject_rate"] = (
                        f"{complete_rejections}/{complete_submissions}")
                    report["orders"][-1]["infra_rejections"] = infra_rejections
                else:
                    complete_reject_streak = 0
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
                    report["orders"][-1]["rejection_margin_ticks"] = \
                        rej.get("margin_ticks")
                    report["orders"][-1]["tick"] = str(spec["tick"])
                    report["orders"][-1]["venue_market"] = rej.get("venue_market")
                    report["orders"][-1]["recorder_book"] = rej.get("recorder_book")
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
                    record_behavioral_fill(inv, fid_art, skey, "complete", deficit_up)
            actions += 1
            if relation == "complete":
                complete_actions += 1

        # OWNER GO 5584132560 item 2: before this session claims anything
        # about what it holds, re-read every order the venue has not
        # declared terminal and book whatever they gained after we stopped
        # waiting. This is the read that would have caught the 4.34 units
        # on vervas-kohboh.
        report["fill_reconciliation"] = _reconcile_fills(
            t, slug, fill_ledger, inv)
        status = ("SPRING_MATCHED" if inv.matched > 0 and inv.residual == 0
                  else ("SPRING_RESIDUAL_HOLD" if inv.up + inv.dn > 0
                        else "SPRING_NO_FILL"))
        # The status describes OUR ledger and keeps its existing meaning --
        # strict-evidence admission counts these strings, so changing them
        # would change behaviour, which this fix may not do. What changes
        # is that the report now says whether the VENUE confirmed it.
        # `flat_verified` is False both when the venue disagrees and when
        # it could not be asked, and every downstream calculation that
        # assumes flatness or a locked value must read it before claiming
        # one. Nothing valued vervas-kohboh at +0.3318 would have passed
        # this flag.
        recon = report["fill_reconciliation"]
        report["flat_verified"] = bool(recon["may_assert_flat"])
        if recon["venue_vs_ledger"].get("agrees") is False:
            # the venue traded a quantity our ledger does not hold: a risk
            # event, not a rounding note. The resume guard already refuses
            # to add exposure on a market whose inventory cannot be
            # reconstructed exactly; this makes the cause loud.
            q1.ledger_append({"kind": "alarm", "spring": True, "slug": slug,
                              "alarm": "venue_ledger_quantity_mismatch",
                              **recon["venue_vs_ledger"]})
            print("PMUS_SPRING_ALARM " + json.dumps(
                {"alarm": "venue_ledger_quantity_mismatch", "slug": slug,
                 **recon["venue_vs_ledger"]}), flush=True)
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
