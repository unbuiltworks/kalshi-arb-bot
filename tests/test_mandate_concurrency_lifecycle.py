"""OWNER AUTHORIZATION 2026-09-16: concurrency counts EXPOSURE, not history.

THE LIVE FINDING. The CP1 probe armed at 21:13:02Z, submitted six maker orders
on one market at qty 0.22, filled NOTHING, cancelled all six, and then could
not open a second market: `markets_open` was "committed AND not settled", and a
market with nothing in it never settles, so market 1 counted against
max_concurrent_markets=1 forever. The campaign stopped.

THE INVARIANT: a market is concurrently OPEN while it has a live or unresolved
order, or unsettled fills. Zero live orders AND zero fills AND no exposure
releases CONCURRENCY -- and nothing else.

THE COUNTER THAT MUST NOT MOVE: `markets_touched`. A zero-fill attempt still
consumes one of max_total_markets. Releasing it from both counters would turn a
3-market authorization into unlimited market scanning, which is a worse failure
than the one being fixed.
"""
import json
from decimal import Decimal as D

import pytest

from src import pmus_live_mandate as M

AUTH = "TEST-AUTH"


# --------------------------------------------------------------------------
# a tiny ledger builder, so each case states its lifecycle literally

class Ledger:
    def __init__(self, tmp_path):
        self.d = tmp_path
        self.rows = []
        self._n = 0

    def commit(self, slug, cash="0.10", qty="0.22", order_id=None):
        """An admitted order: the guard's own commitment row, plus the link to
        a venue order id when submission returned one."""
        self._n += 1
        cid = f"{AUTH}:{slug}:{self._n}"
        self.rows.append({"kind": M.COMMIT_KIND, "authorization_id": AUTH,
                          "commit_id": cid, "slug": slug,
                          "cash_at_risk": cash, "quantity": qty})
        if order_id is not None:
            self.rows.append({"kind": M.LINK_KIND, "authorization_id": AUTH,
                              "commit_id": cid, "order_id": order_id})
        return cid

    def terminal(self, order_id, filled_qty="0", slug=None):
        self.rows.append({"kind": "order_terminal", "order_id": order_id,
                          "slug": slug, "filled_qty": str(filled_qty)})

    def fill(self, slug, qty, side="YES"):
        self.rows.append({"kind": "filled", "slug": slug,
                          "qty": str(qty), "side": side})

    def settlement(self, slug):
        self.rows.append({"kind": "settlement", "slug": slug})

    def write(self):
        p = self.d / "ledger.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in self.rows),
                     encoding="utf-8")
        return self.d

    def read(self):
        self.write()
        return M.consumption(self.d, AUTH)


def _open(c):
    return set(c["markets_open"])


def _touched(c):
    return set(c["markets_touched"])


# --------------------------------------------------------------------------
# 1-4: the lifecycle of a single market

def test_1_admitted_with_a_live_order_counts_open(tmp_path):
    """No terminal row: the order may still be resting. Fail closed."""
    L = Ledger(tmp_path)
    L.commit("mkt-1", order_id="ORD1")
    c = L.read()
    assert _open(c) == {"mkt-1"} and c["n_markets_open"] == 1


def test_2_partial_fill_with_cancelled_remainder_counts_open(tmp_path):
    """The cancel resolved the ORDER; the inventory it left is still exposure."""
    L = Ledger(tmp_path)
    L.commit("mkt-1", qty="0.22", order_id="ORD1")
    L.terminal("ORD1", filled_qty="0.10", slug="mkt-1")
    L.fill("mkt-1", "0.10")
    c = L.read()
    assert _open(c) == {"mkt-1"}
    assert D(c["residual_usd"]["mkt-1"]) > 0


def test_3_full_fill_counts_open(tmp_path):
    L = Ledger(tmp_path)
    L.commit("mkt-1", qty="0.22", order_id="ORD1")
    L.terminal("ORD1", filled_qty="0.22", slug="mkt-1")
    L.fill("mkt-1", "0.22")
    c = L.read()
    assert _open(c) == {"mkt-1"}


def test_4_admitted_cancelled_zero_fill_does_not_count_open(tmp_path):
    """THE DEFECT, stated as a test. This is the exact live shape: six
    admitted orders, all terminal, zero fills, no inventory."""
    L = Ledger(tmp_path)
    for i in range(6):
        oid = f"ORD{i}"
        L.commit("mkt-1", order_id=oid)
        L.terminal(oid, filled_qty="0", slug="mkt-1")
    c = L.read()
    assert _open(c) == set(), "a market with nothing in it still counts open"
    assert c["n_markets_open"] == 0
    # ...but it is NOT forgotten
    assert _touched(c) == {"mkt-1"} and c["n_markets_touched"] == 1


def test_a_matched_position_counts_open_even_though_residual_is_zero(tmp_path):
    """Guards an easy wrong fix: keying concurrency off RESIDUAL would read a
    fully matched position as flat. A matched pair still carries money to
    settlement, so ANY unsettled fill counts."""
    L = Ledger(tmp_path)
    L.commit("mkt-1", order_id="ORD1")
    L.terminal("ORD1", filled_qty="0.22", slug="mkt-1")
    L.fill("mkt-1", "0.22", side="YES")
    L.fill("mkt-1", "0.22", side="NO")
    c = L.read()
    assert D(c["residual_usd"]["mkt-1"]) == 0     # perfectly matched
    assert _open(c) == {"mkt-1"}, "matched inventory is still exposure"


def test_settlement_is_what_finally_closes_a_filled_market(tmp_path):
    L = Ledger(tmp_path)
    L.commit("mkt-1", order_id="ORD1")
    L.terminal("ORD1", filled_qty="0.22", slug="mkt-1")
    L.fill("mkt-1", "0.22")
    assert _open(L.read()) == {"mkt-1"}
    L.settlement("mkt-1")
    c = L.read()
    assert _open(c) == set()
    assert _touched(c) == {"mkt-1"}               # still counted forever


# --------------------------------------------------------------------------
# 5-6: what concurrency admission actually does with that

def test_5_a_zero_fill_market_releases_concurrency_for_a_second_market(tmp_path):
    L = Ledger(tmp_path)
    L.commit("mkt-1", order_id="ORD1")
    L.terminal("ORD1", filled_qty="0", slug="mkt-1")
    c = L.read()
    assert c["n_markets_open"] == 0
    # the admission arithmetic the wire guard performs for a NEW market
    assert c["n_markets_open"] + 1 <= 1, "a second market must now be admissible"
    assert c["n_markets_touched"] + 1 <= 3, "and it is the 2nd of 3 allowed"


def test_6_a_market_holding_inventory_cannot_be_bypassed(tmp_path):
    L = Ledger(tmp_path)
    L.commit("mkt-1", order_id="ORD1")
    L.terminal("ORD1", filled_qty="0.22", slug="mkt-1")
    L.fill("mkt-1", "0.22")
    c = L.read()
    assert c["n_markets_open"] == 1
    assert not (c["n_markets_open"] + 1 <= 1), "a second market must be refused"


# --------------------------------------------------------------------------
# 7-8: restart. The ledger is durable, so these must hold across a fresh read.

def test_7_restart_does_not_resurrect_a_zero_fill_market_as_open(tmp_path):
    L = Ledger(tmp_path)
    L.commit("mkt-1", order_id="ORD1")
    L.terminal("ORD1", filled_qty="0", slug="mkt-1")
    L.write()
    for _ in range(3):                       # three fresh processes
        c = M.consumption(tmp_path, AUTH)
        assert c["n_markets_open"] == 0
        assert c["n_markets_touched"] == 1


def test_8_restart_does_restore_a_market_with_residual_inventory(tmp_path):
    L = Ledger(tmp_path)
    L.commit("mkt-1", order_id="ORD1")
    L.terminal("ORD1", filled_qty="0.22", slug="mkt-1")
    L.fill("mkt-1", "0.22")
    L.write()
    for _ in range(3):
        c = M.consumption(tmp_path, AUTH)
        assert c["markets_open"] == ["mkt-1"]


# --------------------------------------------------------------------------
# 9-10: the total counter is NOT released. This is the anti-scanning guard.

def test_9_three_zero_fill_markets_still_exhaust_max_total_markets(tmp_path):
    L = Ledger(tmp_path)
    for i in (1, 2, 3):
        oid = f"ORD{i}"
        L.commit(f"mkt-{i}", order_id=oid)
        L.terminal(oid, filled_qty="0", slug=f"mkt-{i}")
    c = L.read()
    assert c["n_markets_open"] == 0, "none holds exposure"
    assert c["n_markets_touched"] == 3, "but all three were spent"


def test_10_a_fourth_market_is_refused_after_three_zero_fill_attempts(tmp_path):
    L = Ledger(tmp_path)
    for i in (1, 2, 3):
        oid = f"ORD{i}"
        L.commit(f"mkt-{i}", order_id=oid)
        L.terminal(oid, filled_qty="0", slug=f"mkt-{i}")
    L.write()
    m = _mandate()
    v = M.admit(_body(), state_dir=tmp_path, mandate=m, slug="mkt-4", realized_today=D("0"))
    assert v["admits"] is False
    assert v["reason"] == "max_total_markets_exhausted", v


def test_the_fourth_refusal_is_the_total_counter_not_the_concurrency_one(tmp_path):
    """Names WHICH limit fired, because the whole point of this change is that
    the two counters are different."""
    L = Ledger(tmp_path)
    for i in (1, 2, 3):
        oid = f"ORD{i}"
        L.commit(f"mkt-{i}", order_id=oid)
        L.terminal(oid, filled_qty="0", slug=f"mkt-{i}")
    L.write()
    c = M.consumption(tmp_path, AUTH)
    assert c["n_markets_open"] == 0          # concurrency is free
    assert c["n_markets_touched"] == 3       # the total is what is exhausted


# --------------------------------------------------------------------------
# fail-closed: unresolved is never free

def test_an_order_with_no_terminal_row_holds_concurrency(tmp_path):
    L = Ledger(tmp_path)
    L.commit("mkt-1", order_id="ORD1")
    L.commit("mkt-1", order_id="ORD2")
    L.terminal("ORD2", filled_qty="0", slug="mkt-1")   # only one resolved
    assert _open(L.read()) == {"mkt-1"}, "one unresolved order still holds it"


def test_a_commit_that_never_got_an_order_id_holds_concurrency(tmp_path):
    """We never learned what happened to it. 'We never found out' must never
    free concurrency -- the same rule the charge path uses."""
    L = Ledger(tmp_path)
    L.commit("mkt-1", order_id=None)
    assert _open(L.read()) == {"mkt-1"}


def test_an_unreadable_ledger_still_refuses(tmp_path):
    (tmp_path / "ledger.jsonl").write_text("{not json\n", encoding="utf-8")
    v = M.admit(_body(), state_dir=tmp_path, mandate=_mandate(), slug="mkt-1",
                realized_today=D("0"))
    assert v["admits"] is False


# --------------------------------------------------------------------------
# 11: nothing else weakened

def _mandate():
    m, why = M.parse_mandate({
        "authorization_id": AUTH, "authorized_at": "2026-09-16T00:00:00Z",
        "service": "l5-r1-probe", "strategy": "s",
        "strategy_version": "CP1",
        "max_markets": 1, "max_total_markets": 3,
        "max_spend_per_market": "5.00", "max_total_spend": "15.00",
        "max_residual": "3.00", "max_daily_realized_loss": "6.00",
        "expiry": "2099-01-01T00:00:00Z"})
    assert m is not None, why
    return m


def _body(price="0.45", qty="0.22"):
    """The real wire shape: outcomeSide + action, priced by economic effect.
    `price.value` is always the LONG side's price on this venue."""
    return {"marketSlug": "mkt-x", "action": "ACTION_BUY",
            "outcomeSide": "OUTCOME_SIDE_YES",
            "price": {"value": price}, "quantity": qty}


def test_11_the_charge_arithmetic_is_unchanged(tmp_path):
    """A fill is permanent; a cancelled remainder is released. Concurrency
    moved, money did not."""
    L = Ledger(tmp_path)
    L.commit("mkt-1", cash="1.00", qty="1.0", order_id="ORD1")
    L.terminal("ORD1", filled_qty="0.5", slug="mkt-1")
    c = L.read()
    assert D(c["per_market"]["mkt-1"]) == D("0.5"), "half filled, half released"
    # and a zero-fill cancel releases all of its own reservation
    L2 = Ledger(tmp_path)
    L2.commit("mkt-2", cash="1.00", qty="1.0", order_id="ORD2")
    L2.terminal("ORD2", filled_qty="0", slug="mkt-2")
    assert D(L2.read()["per_market"]["mkt-2"]) == 0


def test_11_an_unconfirmed_submission_still_charges_in_full(tmp_path):
    L = Ledger(tmp_path)
    L.commit("mkt-1", cash="1.00", qty="1.0", order_id="ORD1")   # no terminal
    assert D(L.read()["per_market"]["mkt-1"]) == D("1.00")


def test_11_the_spend_caps_still_bind(tmp_path):
    L = Ledger(tmp_path)
    L.commit("mkt-1", cash="4.99", qty="1.0", order_id="ORD1")
    L.write()
    v = M.admit(_body(), state_dir=tmp_path, mandate=_mandate(), slug="mkt-1",
                realized_today=D("0"))
    assert v["admits"] is False
    assert v["reason"] == "max_spend_per_market_exhausted", v


def test_11_the_residual_cap_still_binds(tmp_path):
    L = Ledger(tmp_path)
    L.commit("mkt-1", order_id="ORD1")
    L.terminal("ORD1", filled_qty="4", slug="mkt-1")
    L.fill("mkt-1", "4", side="YES")          # 4 unmatched -> $4.00 > $3.00
    L.write()
    v = M.admit(_body(), state_dir=tmp_path, mandate=_mandate(), slug="mkt-1",
                realized_today=D("0"))
    assert v["admits"] is False
    assert v["reason"] == "max_residual_exceeded", v


def test_11_the_daily_loss_stop_still_binds(tmp_path):
    Ledger(tmp_path).write()
    v = M.admit(_body(), state_dir=tmp_path, mandate=_mandate(), slug="mkt-1",
                realized_today=D("-6.00"))
    assert v["admits"] is False
    assert v["reason"] == "daily_realized_loss_stop", v


def test_11_the_ledger_is_never_rewritten_by_reading_it(tmp_path):
    """No audit trail is deleted to make concurrency work. The fix is a
    different READING of the same rows."""
    L = Ledger(tmp_path)
    L.commit("mkt-1", order_id="ORD1")
    L.terminal("ORD1", filled_qty="0", slug="mkt-1")
    p = L.write() / "ledger.jsonl"
    before = p.read_bytes()
    for _ in range(3):
        M.consumption(tmp_path, AUTH)
    assert p.read_bytes() == before
    rows = [json.loads(x) for x in before.decode().splitlines() if x.strip()]
    assert sum(1 for r in rows if r["kind"] == M.COMMIT_KIND) == 1
    assert sum(1 for r in rows if r["kind"] == "order_terminal") == 1


def test_11_consumption_does_not_write_anything_at_all():
    """Source-level: a read that can write is a read that can corrupt.

    Scans for the WRITE CALLS, not for substrings -- the first version of this
    forbade "open(" and matched `open_markets` and `_concurrently_open(`, which
    is the same class of false positive as a credential scan hitting prose.
    """
    import ast
    src = open(M.__file__, encoding="utf-8").read()
    fn = next(n for n in ast.parse(src).body
              if isinstance(n, ast.FunctionDef) and n.name == "consumption")
    WRITES = {"_append", "write_text", "write_bytes", "unlink", "mkdir",
              "rename", "touch", "ledger_append"}
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            name = getattr(f, "id", None) or getattr(f, "attr", None)
            assert name not in WRITES, f"consumption() calls {name}()"
            # the builtin open() in write mode would also be a write
            if name == "open":
                raise AssertionError("consumption() calls open()")


def test_11_the_wire_is_still_the_only_admission_point():
    src = open(M.__file__, encoding="utf-8").read()
    assert 'ORDER_PATH = "/v1/orders"' in src
    assert "def guard(" in src


# --------------------------------------------------------------------------
# the live shape, end to end

def test_the_exact_live_failure_now_admits_a_second_market(tmp_path):
    """Replays 21:13Z: six admitted orders at qty 0.22, all terminal with zero
    fills, then a second market. Before this change that refused with
    max_concurrent_markets_exhausted and the campaign stopped."""
    L = Ledger(tmp_path)
    for i in range(6):
        oid = f"CHKFE{i}"
        L.commit("aec-dota2-yes-tm-2026-09-16", cash="0.078",
                 qty="0.22", order_id=oid)
        L.terminal(oid, filled_qty="0", slug="aec-dota2-yes-tm-2026-09-16")
    L.write()
    c = M.consumption(tmp_path, AUTH)
    assert c["n_markets_open"] == 0
    assert c["n_markets_touched"] == 1
    assert D(c["total"]) == 0, "six cancelled orders spent nothing"
    v = M.admit(_body(), state_dir=tmp_path, mandate=_mandate(),
                slug="aec-setkameua-shcdmy-voryar-2026-09-16",
                realized_today=D("0"))
    assert v["admits"] is True, v


# --------------------------------------------------------------------------
# THE SEPARATE FINDING, investigated and PINNED rather than changed.
#
# The owner asked why `MandateRefusal:max_concurrent_markets_exhausted`
# surfaces as `CAMPAIGN_STOP:HALT`. The chain is:
#
#   1. GuardedTransport.request() raises MandateRefusal (an Exception).
#   2. pmus_spring_l5_session.run_session() catches Halt -> SPRING_SAFE_REFUSAL,
#      but MandateRefusal is NOT a Halt, so it lands in the generic
#      `except Exception` -> status "HALT", reason "MandateRefusal:<why>".
#   3. The campaign's stop-set contains "HALT" -> CAMPAIGN_STOP:HALT.
#
# So it is the generic unexpected-exception path, not a purpose-built mapping:
# the label says "something went wrong" where the truth is "the mandate
# correctly declined". The label is imprecise. The BEHAVIOUR is right, and the
# only reclassification the existing semantics offer is strictly less safe --
# SPRING_SAFE_REFUSAL is in no stop-set, so it CONTINUES the campaign, which
# would let max_total_markets_exhausted and max_total_spend_exhausted be
# retried forever. That is the 2026-09-16 breach shape: authorization to start
# read as authorization to continue.
#
# These tests pin the chain so the classification cannot drift quietly.

def test_a_mandate_refusal_is_not_a_halt_and_cannot_take_the_continue_path():
    """If MandateRefusal ever became a Halt subclass, every mandate refusal
    would silently become SPRING_SAFE_REFUSAL and the campaign would carry on
    against an exhausted envelope."""
    from src.pmus_q1_executor import Halt
    assert not issubclass(M.MandateRefusal, Halt)
    assert issubclass(M.MandateRefusal, Exception)


def test_a_mandate_refusal_never_qualifies_as_a_bounded_wait_read_halt():
    """`_read_halt` lets ONE kind of HALT continue the campaign: a read-only
    GET that ran out of retries before anything was submitted. A mandate
    refusal must never be mistaken for it."""
    from src import pmus_l5_continuous_session as cont
    refusal = {"status": "HALT", "orders": [], "actions": [],
               "reason": "MandateRefusal:max_concurrent_markets_exhausted"}
    assert cont._read_halt(refusal) is False
    # ... and the thing it IS for still qualifies, so this is not vacuous
    assert cont._read_halt({**refusal, "reason": cont.READ_HALT_MARK}) is True


def test_the_campaign_stops_on_halt_and_would_not_stop_on_a_safe_refusal():
    """The reason the label stays imprecise: the alternative is unsafe.

    Extracted from the AST, not by scanning text -- the campaign discusses
    both statuses in prose, and a substring scan cannot tell a stop-set from
    a comment about one."""
    import ast
    from src import pmus_l5_continuous_session as cont
    tree = ast.parse(open(cont.__file__, encoding="utf-8").read())

    stop_sets = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        if not isinstance(node.ops[0], ast.In):
            continue
        comp = node.comparators[0]
        if not isinstance(comp, (ast.Tuple, ast.List, ast.Set)):
            continue
        members = [e.value for e in comp.elts
                   if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if len(members) == len(comp.elts) and "HALT" in members:
            stop_sets.append(members)

    assert stop_sets, "no status stop-set found; this test has gone stale"
    for members in stop_sets:
        assert "HALT" in members
        assert "SPRING_SAFE_REFUSAL" not in members, (
            "a safe refusal would stop the campaign now -- the tradeoff this "
            "finding rests on has changed, so re-open the classification")
