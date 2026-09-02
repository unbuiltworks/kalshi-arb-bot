"""Tests: the v1 model, not the pair engine, controls campaign actions."""
from __future__ import annotations

from decimal import Decimal as D

from src import gemini_l5_v1_campaign as camp
from src import gemini_l5_v1_policy as v1
from src.gemini_l5_ladder_q1 import edge


# ---------- pure decision contract ----------

def test_flat_requires_in_distribution_open():
    assert camp.decide_flat({"allow": True, "modal_relation": "open"}) == "OPEN_ONE_SIDE"
    assert camp.decide_flat({"allow": False, "reason": "off_distribution"}) == "ABSTAIN"
    assert camp.decide_flat({"allow": True, "modal_relation": "complete"}) == "ABSTAIN"
    assert camp.decide_flat({"allow": True, "modal_relation": "sell"}) == "ABSTAIN"


def test_post_fill_complete_extend_abstain():
    assert camp.decide_after_fill({"allow": True, "modal_relation": "complete"}) == "COMPLETE"
    # extend is what v1 wants but the q=1 residual cap forbids it -> abstain-hold
    assert camp.decide_after_fill({"allow": True, "modal_relation": "extend"}) == "ABSTAIN_EXTEND_CAPPED"
    assert camp.decide_after_fill({"allow": False}) == "ABSTAIN"
    assert camp.decide_after_fill({"allow": True, "modal_relation": "sell"}) == "ABSTAIN"


def test_real_gate_drives_real_decisions():
    # flat in-distribution state -> the model's own verdict decides
    flat = v1.evaluate(0.50, 200.0, 0.0)
    assert camp.decide_flat(flat) in {"OPEN_ONE_SIDE", "ABSTAIN"}
    # after a 1-share fill the imbalance bucket moves off flat
    post = v1.evaluate(0.50, 180.0, 1.0)
    assert post["state"].endswith("imb1-10")
    assert camp.decide_after_fill(post) in {"COMPLETE", "ABSTAIN_EXTEND_CAPPED", "ABSTAIN"}
    # off-distribution always abstains at both stages
    hole = {"allow": False, "reason": "off_distribution"}
    assert camp.decide_flat(hole) == "ABSTAIN"
    assert camp.decide_after_fill(hole) == "ABSTAIN"


# ---------- risk adapter (economics never overrides the policy) ----------

def test_choose_open_side_is_fee_safe_and_queue_tiebroken():
    # both sides safe, deeper queue wins
    c = camp.choose_open_side(D("0.48"), D("500"), D("0.52"), D("100"))
    assert c == {"side": "YES", "price": D("0.48")}
    c = camp.choose_open_side(D("0.48"), D("100"), D("0.52"), D("500"))
    assert c["side"] == "NO" and c["price"] == D("0.48")
    # YES price outside 20-80c -> only the NO side is offered
    c = camp.choose_open_side(D("0.10"), D("999"), D("0.35"), D("1"))
    assert c == {"side": "NO", "price": D("0.65")}


def test_choose_open_side_none_when_neither_safe():
    assert camp.choose_open_side(D("0.05"), D("1"), D("0.97"), D("1")) is None


def test_complement_price_locks_min_edge_after_fees():
    px = camp.complement_price("YES", D("0.60"), D("0.01"))
    assert px is not None and D("0.20") <= px <= D("0.80")
    assert edge(D("0.60"), px) >= D("0.01")
    # first fill at 80c: complement must still lock 1c after both fees
    px2 = camp.complement_price("YES", D("0.80"), D("0.01"))
    assert px2 is None or edge(D("0.80"), px2) >= D("0.01")


def test_no_automatic_complement_semantics():
    """The campaign module has no code path that places the complement
    without a COMPLETE decision from v1: assert the only order.place for
    the complement is inside the decision == 'COMPLETE' branch."""
    import inspect
    src = inspect.getsource(camp)
    # exactly two order.place call sites: the v1 opening order and the
    # complement inside the COMPLETE branch
    assert src.count('order.place') == 2
    complete_branch = src.split('if decision == "COMPLETE"')[1]
    assert 'order.place' in complete_branch
    # and the pair engine's auto-rescue is not invoked anywhere
    assert '_dynamic_complement_rescue' not in src
