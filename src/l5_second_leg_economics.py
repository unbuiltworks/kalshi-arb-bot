"""Read-only counterfactual second-leg economics; never submits orders.

Each candidate is an executable *hypothesis*, not a fill. Prices, available
depth, fees and adverse-selection haircuts must come from timestamped venue
observations. Compare candidates at the SAME decision instant and inventory.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

D = Decimal


def dec(value):
    try:
        result = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("invalid numeric input")
    if not result.is_finite():
        raise ValueError("non-finite numeric input")
    return result


@dataclass(frozen=True)
class Inventory:
    up_qty: Decimal
    down_qty: Decimal
    total_cost: Decimal

    def __post_init__(self):
        if min(self.up_qty, self.down_qty, self.total_cost) < 0:
            raise ValueError("negative inventory or cost")

    def outcome_net(self):
        return self.up_qty - self.total_cost, self.down_qty - self.total_cost


@dataclass(frozen=True)
class Candidate:
    action: str
    side: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    depth: Decimal
    book_age_s: Decimal
    adverse_cost: Decimal = D("0")
    maker_fill_probability: Optional[Decimal] = None

    def __post_init__(self):
        if self.side not in ("UP", "DOWN"):
            raise ValueError("invalid side")
        if self.action not in ("MAKER", "REPRICE", "TAKER"):
            raise ValueError("invalid action")
        if self.quantity <= 0 or not (D("0") <= self.price <= D("1")):
            raise ValueError("invalid quantity or price")
        if min(self.fee, self.depth, self.book_age_s, self.adverse_cost) < 0:
            raise ValueError("negative cost, depth or book age")
        if self.maker_fill_probability is not None and not (
            D("0") <= self.maker_fill_probability <= D("1")
        ):
            raise ValueError("invalid probability")


def evaluate(inv: Inventory, candidate: Candidate, *, max_book_age_s=D("2"),
             cash_headroom=None, worst_case_loss_ceiling=None):
    """Return bounded hypothetical economics, not a trade instruction.

    For maker/reprice, a fill is NOT assumed. The output exposes both
    fill and no-fill branches, and refuses to rank them by expected value
    unless a validated fill probability was explicitly supplied.
    """
    u0, d0 = inv.outcome_net()
    hold_worst = min(u0, d0)
    result = {"action": candidate.action, "hold_up": str(u0),
              "hold_down": str(d0), "hold_worst": str(hold_worst),
              "eligible": False, "reason": None}
    if candidate.book_age_s > dec(max_book_age_s):
        result["reason"] = "STALE_BOOK"
        return result
    if candidate.depth < candidate.quantity:
        result["reason"] = "INSUFFICIENT_DEPTH"
        return result
    spend = candidate.quantity * candidate.price + candidate.fee + candidate.adverse_cost
    if cash_headroom is not None and spend > dec(cash_headroom):
        result["reason"] = "CASH_HEADROOM"
        return result
    u1 = u0 + (candidate.quantity if candidate.side == "UP" else D("0")) - spend
    d1 = d0 + (candidate.quantity if candidate.side == "DOWN" else D("0")) - spend
    worst = min(u1, d1)
    if worst_case_loss_ceiling is not None and worst < -dec(worst_case_loss_ceiling):
        result["reason"] = "WORST_CASE_LIMIT"
        return result
    result.update(eligible=True, spend=str(spend), fill_up=str(u1),
                  fill_down=str(d1), fill_worst=str(worst),
                  worst_improvement=str(worst - hold_worst),
                  no_fill_worst=str(hold_worst))
    if candidate.action == "TAKER":
        result["executable_worst"] = str(worst)
        result["execution_confirmed"] = False
    elif candidate.maker_fill_probability is not None:
        p = candidate.maker_fill_probability
        result["model_expected_worst"] = str(p * worst + (D("1") - p) * hold_worst)
        result["model_only"] = True
    else:
        result["eligible"] = False
        result["model_expected_worst"] = None
        result["reason"] = "FILL_PROBABILITY_UNVALIDATED"
    return result


#: tolerance on the joint-fill distribution summing to one. Calibrated
#: frequencies are rounded, so exact Decimal equality refuses inputs that are
#: correct to the precision they were measured at. A real mismatch still
#: refuses -- this only stops a rounding residue from looking like one.
PROB_SUM_TOLERANCE = D("0.0005")


def evaluate_paired_entry(*, up_price, down_price, quantity, up_fee="0",
                          down_fee="0", p_both=None, p_up_only=None,
                          p_down_only=None, p_neither=None,
                          p_up_settles_given_up_only=None,
                          p_up_settles_given_down_only=None, adverse_cost="0",
                          max_combined_cost=None, minimum_expected_net="0",
                          max_residual_probability=None,
                          max_single_leg_loss=None,
                          up_rebate="0", down_rebate="0", rebate_min_qty=None,
                          up_depth=None, down_depth=None,
                          book_age_s=None, max_book_age_s="2",
                          up_filled_qty=None, down_filled_qty=None):
    """Read-only joint-fill paired-entry model; NEVER an order instruction.

    Probabilities must be externally calibrated from time-aligned order events.
    A joint distribution is mandatory: assuming independent maker fills would
    conceal trend-driven adverse selection. Settlement probability is conditional on which leg filled alone,
    because fill-side adverse selection affects the settlement outcome. No estimated fills are represented as executions.
    Returns a refusal when the necessary evidence is unavailable.
    """
    q = dec(quantity)
    u, d = dec(up_price), dec(down_price)
    uf, df, adverse = dec(up_fee), dec(down_fee), dec(adverse_cost)
    if q <= 0 or not (D("0") <= u <= D("1") and D("0") <= d <= D("1")):
        raise ValueError("invalid quote price or quantity")
    if min(uf, df, adverse) < 0:
        raise ValueError("negative fee or adverse cost")

    # MAKER REBATES ARE A CREDIT, AND THEY HAVE A FLOOR.
    #
    # They are taken as their own non-negative inputs rather than as a
    # negative `fee`, because a negative fee would also admit a negative
    # ADVERSE cost -- an adverse-selection credit, which is not a thing.
    #
    # `rebate_min_qty` encodes the measured venue behaviour: the credited
    # maker rebate is 0.00727/fill but earns $0.00 below a confirmed floor
    # of 2.52 contracts, so at the deployed 1-contract clip it is ZERO.
    # A model that credited it anyway would make the deployed configuration
    # look better than it is, which is the specific error the programme has
    # been warned about.
    ur, dr = dec(up_rebate), dec(down_rebate)
    if min(ur, dr) < 0:
        raise ValueError("negative rebate")
    rebate_earned = True
    if rebate_min_qty is not None:
        floor = dec(rebate_min_qty)
        if floor < 0:
            raise ValueError("negative rebate floor")
        rebate_earned = q >= floor
    if not rebate_earned:
        ur = dr = D("0")
    uf_net, df_net = uf - ur, df - dr

    pair_cost = q * (u + d) + uf_net + df_net + adverse
    result = {"eligible": False, "pair_cost": str(pair_cost),
              "pair_net_if_both_fill": str(q - pair_cost),
              "rebate_credited": str(ur + dr),
              "rebate_earned": rebate_earned,
              "reason": "JOINT_FILL_OR_SETTLEMENT_UNVALIDATED"}

    # FRESHNESS, matching `evaluate`. The paired model had no book-age gate
    # at all, so a stale snapshot could price an entry with no refusal --
    # and this programme's own standard is a 2 s bound that 6.4-6.9 s
    # snapshots do not meet.
    if book_age_s is not None and dec(book_age_s) > dec(max_book_age_s):
        result["reason"] = "STALE_BOOK"
        return result

    # DEPTH, also matching `evaluate`. Quoting size the book cannot absorb
    # is not an executable hypothesis.
    for name, avail in (("up", up_depth), ("down", down_depth)):
        if avail is not None and dec(avail) < q:
            result["reason"] = "INSUFFICIENT_DEPTH"
            result["insufficient_side"] = name.upper()
            return result

    if max_combined_cost is not None and pair_cost > dec(max_combined_cost):
        result["reason"] = "PAIR_COST_LIMIT"
        return result
    probs = (p_both, p_up_only, p_down_only, p_neither,
             p_up_settles_given_up_only, p_up_settles_given_down_only)
    if any(p is None for p in probs):
        return result
    both, up_only, down_only, neither, settle_up_if_up, settle_up_if_down = map(dec, probs)
    if any(p < 0 or p > 1 for p in (both, up_only, down_only, neither, settle_up_if_up, settle_up_if_down)):
        raise ValueError("probability outside [0,1]")
    # A DISTRIBUTION THAT DOES NOT SUM TO ONE IS A REFUSAL, NOT A CRASH.
    # It used to raise, so a controller asking "should I enter?" got an
    # exception instead of "no". Fail closed, and say why.
    total = sum((both, up_only, down_only, neither))
    if abs(total - D("1")) > PROB_SUM_TOLERANCE:
        result.update(reason="JOINT_FILL_DISTRIBUTION_NOT_NORMALISED",
                      probability_sum=str(total), model_only=True)
        return result

    # PARTIAL FILLS. The joint distribution is all-or-nothing per leg, but
    # the live 2026-11-09 15:30Z window filled 0.7 of a 1-contract DOWN
    # order -- a partial -- so the single-leg paths take an optional filled
    # quantity. Fees scale pro rata with the filled fraction; the quoted
    # fee is for the quoted size.
    uq = q if up_filled_qty is None else dec(up_filled_qty)
    dq = q if down_filled_qty is None else dec(down_filled_qty)
    if uq <= 0 or dq <= 0 or uq > q or dq > q:
        raise ValueError("filled quantity must be in (0, quantity]")
    u_frac, d_frac = uq / q, dq / q

    # Charge the conservative adverse-cost allowance on every filled path.
    # Single-leg paths settle directionally; both-filled paths redeem q.
    up_only_net = uq * settle_up_if_up - (
        uq * u + uf_net * u_frac + adverse)
    down_only_net = dq * (1 - settle_up_if_down) - (
        dq * d + df_net * d_frac + adverse)
    both_net = q - pair_cost
    expected = both * both_net + up_only * up_only_net + down_only * down_only_net
    threshold = dec(minimum_expected_net)
    if threshold < 0:
        raise ValueError("negative minimum expected net")
    residual_probability = up_only + down_only
    # WORST CASE ON AN UNMATCHED LEG: the leg settles worthless and its cost
    # is lost. Computed on the FILLED quantity and net of any earned rebate,
    # so it tracks what is actually at risk.
    residual_worst = min(-(uq * u + uf_net * u_frac + adverse),
                         -(dq * d + df_net * d_frac + adverse))
    if max_residual_probability is not None:
        cap = dec(max_residual_probability)
        if not D("0") <= cap <= D("1"):
            raise ValueError("invalid residual probability limit")
        if residual_probability > cap:
            result.update(reason="RESIDUAL_PROBABILITY_LIMIT",
                          expected_net=str(expected),
                          p_any_residual=str(residual_probability),
                          model_only=True)
            return result
    if max_single_leg_loss is not None:
        limit = dec(max_single_leg_loss)
        if limit < 0:
            raise ValueError("negative single-leg loss limit")
        if residual_worst < -limit:
            result.update(reason="SINGLE_LEG_LOSS_LIMIT",
                          expected_net=str(expected),
                          residual_worst=str(residual_worst),
                          model_only=True)
            return result
    result.update(eligible=(expected > threshold),
                  reason=None if expected > threshold else "NONPOSITIVE_EXPECTED_NET",
                  model_only=True,
                  expected_net=str(expected),
                  p_any_residual=str(residual_probability),
                  residual_worst=str(residual_worst),
                  up_only_expected_net=str(up_only_net),
                  down_only_expected_net=str(down_only_net),
                  both_net=str(both_net),
                  up_filled_qty=str(uq), down_filled_qty=str(dq),
                  probability_sum=str(total),
                  no_fill_net="0")
    return result


#: The adverse-selection skew the entry gate assumes when none is measured.
#:
#: WHY NONZERO IS THE CONSERVATIVE DEFAULT. A resting two-sided quote fills
#: on the side the market runs through, so the leg that fills ALONE is the
#: adversely selected one: UP fills alone when the price falls through our
#: bid (and UP then tends to settle NO), DOWN fills alone when the price
#: rises through our ask (and UP then tends to settle YES). The skew is how
#: far those conditional settlement probabilities sit from 0.50.
#:
#: Zero would assert that a single fill carries NO directional information,
#: which the 2026-11-09 15:30Z window refutes: DOWN filled alone, the price
#: had risen, UP settled, and the DOWN leg paid nothing. That is the x=0.50
#: corner. One observation cannot estimate the skew, so this is set to a
#: modest value and the gate it feeds is a REFUSAL, not a licence.
DEFAULT_ADVERSE_SKEW = D("0.15")


def admissible_pair_ceiling(*, p_both, p_up_only, p_down_only,
                            adverse_skew=None, minimum_expected_net="0",
                            quantity="1"):
    """The dearest pair whose expected net still clears the threshold.

    WHY THIS EXISTS. The deployed entry gate tests `pair <= maker_pair_ceiling`
    with the ceiling a CONSTANT (0.96), derived from fee safety --
    `BREAKEVEN_PAIR` is 0.9532 -- and from nothing about adverse selection.
    Under any material skew that constant admits negative-expectancy
    entries, which is what the 15:30Z window bought: pair 0.95, inside the
    cap, expected net +0.0368 only if the skew is exactly zero, and
    NEGATIVE beyond a skew of 0.0871.

    So this inverts the paired-entry expectation into the same shape the
    existing gate already uses -- one ceiling, compared against one pair
    cost -- rather than adding a second mechanism beside it. It TIGHTENS
    entry and can never loosen it: callers take `min(existing, this)`.

    Returns the ceiling as a Decimal. Linearity in the pair cost makes the
    inversion exact: expected net falls by exactly `p_both * dq` for each
    unit the pair dearens, so there is no search and no tolerance.
    """
    q = dec(quantity)
    both, up_only, down_only = dec(p_both), dec(p_up_only), dec(p_down_only)
    if q <= 0:
        raise ValueError("invalid quantity")
    for name, v in (("p_both", both), ("p_up_only", up_only),
                    ("p_down_only", down_only)):
        if not (D("0") <= v <= D("1")):
            raise ValueError("%s outside [0,1]" % name)
    if both <= 0:
        # NO PAIRING, NO PRICE AT WHICH ENTRY IS JUSTIFIED. A pair that
        # never completes cannot be made attractive by being cheap.
        raise ValueError("p_both must be positive to admit any pair")
    skew = (DEFAULT_ADVERSE_SKEW if adverse_skew is None
            else dec(adverse_skew))
    if not (D("0") <= skew <= D("0.5")):
        raise ValueError("adverse skew outside [0,0.5]")
    threshold = dec(minimum_expected_net)
    if threshold < 0:
        raise ValueError("negative minimum expected net")

    # The single-leg branches do not depend on the pair cost: each is its
    # own leg's settlement less its own leg's cost. Only `both_net`
    # (= q - pair_cost) carries it, so solve
    #   both*(q - pair) + up_only*up_net + down_only*down_net = threshold
    # The adverse corner is priced on each single branch: UP alone settles
    # with probability 0.5 - skew, DOWN alone pays when UP does NOT, i.e.
    # with probability 1 - (0.5 + skew).
    a, b = D("0.5") - skew, D("0.5") + skew
    # Each single branch is valued at the SAME split of the pair that the
    # caller is testing, so the worst symmetric reading is used: each leg
    # costs half the pair. That keeps this a function of the pair alone,
    # which is what a one-number ceiling requires.
    #   up_net   = q*a       - half_pair
    #   down_net = q*(1 - b) - half_pair
    # Substituting half_pair = pair/2 and solving for `pair`:
    #   both*q + up_only*q*a + down_only*q*(1-b) - threshold
    #     = pair * (both + (up_only + down_only)/2)
    numer = (both * q + up_only * q * a + down_only * q * (D("1") - b)
             - threshold)
    denom = both + (up_only + down_only) / D("2")
    return numer / denom
