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
    """No code path places the complement without a COMPLETE decision:
    all placement goes through place_order_durable (the single order.place
    site), and the complement call sits inside the COMPLETE branch."""
    import inspect
    src = inspect.getsource(camp)
    assert src.count('"order.place"') == 1        # the one request literal, in place_order_durable
    assert src.count("await place_order_durable(") == 2   # opening + complement
    complete_branch = src.split('if decision == "COMPLETE"')[1]
    assert "place_order_durable" in complete_branch
    assert "_dynamic_complement_rescue" not in src


# ---------- pre-GO review fixes ----------

def test_owner_recognizes_v1_terminal_statuses():
    from src import gemini_l5_dynamic_q1 as owner
    # successful v1 match is matched, not UNSAFE
    assert owner.classify_cycle_status("MATCHED_V1_COMPLETION", D("0")) == "MATCHED"
    # one-sided policy hold halts on the residual invariant (fail-closed)
    assert owner.classify_cycle_status("ONE_SIDED_RESIDUAL_POLICY_HOLD", D("1")) == "RESIDUAL_HALT"
    assert owner.classify_cycle_status("LATE_FILL_RESIDUAL_HOLD", D("1")) == "RESIDUAL_HALT"
    # v1 no-trade outcomes are benign
    assert owner.classify_cycle_status("V1_ABSTAIN_FLAT", D("0")) == "NO_TRADE"
    assert owner.classify_cycle_status("NO_FEE_SAFE_MAKER_PRICE", D("0")) == "NO_TRADE"
    # legacy statuses unchanged; junk still fails closed
    assert owner.classify_cycle_status("MATCHED_DYNAMIC_COMPLETION", D("0")) == "MATCHED"
    assert owner.classify_cycle_status("NO_FILL_TIMEOUT", D("0")) == "NO_TRADE"
    assert owner.classify_cycle_status("POST_REJECT", D("0")) == "UNSAFE"
    assert owner.classify_cycle_status("", D("0")) == "UNSAFE"


def test_authoritative_fill_price_chain_and_fail_closed():
    assert camp.authoritative_fill_price({"avgExecutionPrice": "0.49"}) == D("0.49")
    assert camp.authoritative_fill_price({"avg_execution_price": "0.47"}) == D("0.47")
    assert camp.authoritative_fill_price({"price": "0.50"}) == D("0.50")
    import pytest
    with pytest.raises(RuntimeError):
        camp.authoritative_fill_price({"filledQuantity": "1"})
    with pytest.raises(RuntimeError):
        camp.authoritative_fill_price(None)


def test_complement_economics_use_exec_price_not_limit():
    # fill executed at 0.47 though limit was 0.50: bound must key off 0.47
    px_exec = camp.complement_price("YES", D("0.47"), D("0.01"))
    px_limit = camp.complement_price("YES", D("0.50"), D("0.01"))
    assert px_exec is not None and px_limit is not None and px_exec > px_limit
    assert edge(D("0.47"), px_exec) >= D("0.01")


def test_durable_id_persisted_before_place_and_after_accept(tmp_path, monkeypatch):
    """Injected-exception coverage: the client ID must be in the durable
    cycle state BEFORE order.place (crash during placement) and remain
    there after acceptance (crash after complement acceptance) so exact-ID
    cleanup sees every order."""
    import asyncio, json
    from src import gemini_l5_pair_q1 as pair

    state_file = tmp_path / "cycle.json"
    monkeypatch.setenv(pair.STATE_ENV, str(state_file))

    class BoomMaker:
        async def request(self, *a, **k):
            raise RuntimeError("injected crash during placement")

    state = {"terminal": False, "orders": []}
    spec = {"side": "YES", "price": D("0.48"), "id": "cid-open-0", "row": None}
    import pytest
    with pytest.raises(RuntimeError):
        asyncio.run(camp.place_order_durable(BoomMaker(), state, spec, "SYM"))
    persisted = json.loads(state_file.read_text())
    assert [o["id"] for o in persisted["orders"]] == ["cid-open-0"]

    class AcceptMaker:
        async def request(self, *a, **k):
            return {"status": 200}

    async def fake_find_row(cid, symbol):
        return {"clientOrderId": cid, "filledQuantity": "0"}
    monkeypatch.setattr(camp.ladder.base, "_find_row", fake_find_row)

    comp = {"side": "NO", "price": D("0.50"), "id": "cid-comp-1", "row": None}
    ok = asyncio.run(camp.place_order_durable(AcceptMaker(), state, comp, "SYM"))
    assert ok is True
    persisted = json.loads(state_file.read_text())
    ids = [o["id"] for o in persisted["orders"]]
    assert ids == ["cid-open-0", "cid-comp-1"]        # BOTH exact IDs durable
    assert persisted["orders"][1]["accepted"] is True
    # an exception raised at this exact moment leaves cleanup a complete ID set


def test_accepted_but_undiscoverable_row_fails_closed(tmp_path, monkeypatch):
    import asyncio, pytest
    from src import gemini_l5_pair_q1 as pair
    monkeypatch.setenv(pair.STATE_ENV, str(tmp_path / "cycle.json"))

    class AcceptMaker:
        async def request(self, *a, **k):
            return {"status": 200}

    async def missing_row(cid, symbol):
        return None
    monkeypatch.setattr(camp.ladder.base, "_find_row", missing_row)
    spec = {"side": "YES", "price": D("0.48"), "id": "cid-x", "row": None}
    with pytest.raises(RuntimeError, match="not discoverable"):
        asyncio.run(camp.place_order_durable(AcceptMaker(), {"orders": []}, spec, "SYM"))


def test_initializer_is_labeled_not_validated():
    assert camp.INITIALIZER_LABEL == "TRANSFER_INITIALIZER"
    assert "NOT validated L5" in (camp.choose_open_side.__doc__ or "")
