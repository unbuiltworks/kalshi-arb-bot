"""CP1 exists to let min(rung, mandate) REACH the authorized envelope -- never
to enlarge it.

Owner authorization 2026-09-16 "Option A": max_spend_per_market $5.00,
max_residual $3.00, max_total_spend $15.00, max_markets 1, max_total_markets 10
(widened from 3 by OWNER 2026-09-17, breadth only),
max_daily_realized_loss $6.00, 24h expiry, k 0.00619 unchanged.
"""
from __future__ import annotations

import ast
import json
from decimal import Decimal as D

import pytest

from src import pmus_l5_ramp_ladder as ladder
from src import pmus_live_mandate as M

pytestmark = pytest.mark.real_mandate_env

AUTHORIZED = {
    "authorization_id": "TEST", "authorized_at": "2026-09-16T18:00:00Z",
    "service": "l5-r1-probe", "strategy": "l5_continuous_inventory",
    "strategy_version": "v1.1", "max_markets": 1, "max_total_markets": 10,
    "max_spend_per_market": "5.00", "max_total_spend": "15.00",
    "max_residual": "3.00", "max_daily_realized_loss": "6.00",
    "expiry": "2026-09-17T18:00:00Z",
}


def _mandate(tmp_path, **over):
    f = tmp_path / "live_mandate.json"
    f.write_text(json.dumps({**AUTHORIZED, **over}))
    m, why = M.load_mandate({"PMUS_LIVE_MANDATE_FILE": str(f)})
    assert m is not None, why
    return m


def _effective(rung_id: str, mandate) -> D:
    """The production rule, reproduced exactly: min(rung, mandate)."""
    rung = D(str(ladder.rung(rung_id)["per_market_cap_usd"]))
    return min(rung, D(str(mandate.max_spend_per_market)))


# --------------------------------------------------------------------------
# 5. the effective cap is min($5 rung, $5 mandate) = $5

def test_effective_per_market_cap_is_five_dollars(tmp_path):
    assert _effective("CP1", _mandate(tmp_path)) == D("5.00")


# --------------------------------------------------------------------------
# 6. HOSTILE: a tighter MANDATE wins

@pytest.mark.parametrize("tighter", ["0.05", "0.50", "1.00", "4.99"])
def test_a_tighter_mandate_always_wins_over_cp1(tmp_path, tighter):
    m = _mandate(tmp_path, max_spend_per_market=tighter)
    assert _effective("CP1", m) == D(tighter)


# --------------------------------------------------------------------------
# 7. HOSTILE: a tighter RUNG wins

@pytest.mark.parametrize("rid,cap", [("R1", "0.05"), ("R2C", "0.15"),
                                     ("R2C2", "0.15")])
def test_a_tighter_rung_always_wins_over_the_mandate(tmp_path, rid, cap):
    assert _effective(rid, _mandate(tmp_path)) == D(cap)


# --------------------------------------------------------------------------
# 4. CP1 can never ENLARGE any mandate limit

def test_cp1_can_never_enlarge_the_per_market_cap(tmp_path):
    """Swept against every rung in the table: the effective cap is never above
    the mandate's, whatever rung is active."""
    m = _mandate(tmp_path)
    ceiling = D(str(m.max_spend_per_market))
    for r in ladder.ALL_RUNGS:
        assert _effective(r["id"], m) <= ceiling, r["id"]


def test_cp1_residual_units_cannot_exceed_the_mandate_dollar_ceiling():
    """A contract settles at $0 or $1, so its cost basis is at most $1.00.
    THREE unmatched contracts therefore carry at most $3.00 of economic
    exposure -- at every price, by the instrument's definition rather than by
    a price assumption. The count can only ever bind EARLIER than the $3.00
    transport ceiling, never later."""
    units = ladder.rung("CP1")["residual_units"]
    assert units == 3
    assert D(units) * D("1.00") <= D(AUTHORIZED["max_residual"])


def test_cp1_worst_case_and_what_actually_bounds_the_campaign(tmp_path):
    """OWNER 2026-09-17 widened breadth 3 -> 10 and held max_total_spend at
    $15.00, which SEPARATED two numbers that used to coincide.

    `worst_case` derives per_campaign as cap x markets, so the RUNG's own
    worst case is now $5.00 x 10 = $50.00 where it read $15.00 at three
    markets. That is a true statement about the rung and it is NOT the
    binding constraint: the immutable mandate caps campaign spend at $15.00
    and GuardedTransport enforces it at the wire. Asserting the min() rather
    than a single number is the point -- at 3 markets the two were equal and
    a test on either one looked identical."""
    wc = ladder.worst_case(ladder.rung("CP1"))
    assert wc["per_market_usd"] == "5.00"
    assert wc["per_campaign_usd"] == "50.00"          # the RUNG's own
    assert wc["max_concurrent_open_exposure_usd"] == "5.00"   # concurrency 1
    assert wc["daily_loss_stop_usd"] == "-6.00"
    # what actually bounds the campaign's dollars
    m = _mandate(tmp_path)
    assert min(D(wc["per_campaign_usd"]),
               D(str(m.max_total_spend))) == D("15.00")
    assert D(str(m.max_total_spend)) < D(wc["per_campaign_usd"]), (
        "the mandate must be the tighter of the two, or breadth would have "
        "bought dollars rather than observations")


def test_cp1_matches_every_authorized_limit():
    r = ladder.rung("CP1")
    assert r["per_market_cap_usd"] == "5.00"
    assert r["concurrency"] == 1                       # max_markets
    assert r["markets_per_campaign"] == 10             # max_total_markets
    assert r["daily_loss_stop_usd"] == "-6.00"
    assert r["max_actions_per_session"] == 6           # R1's, unchanged


# --------------------------------------------------------------------------
# the things the authorization forbade

def test_cp1_cannot_be_promoted_to_or_from():
    """No automatic rung progression is resurrected."""
    assert ladder.rung("CP1")["promote"] is None
    for r in ladder.ALL_RUNGS:
        crit = r.get("promote") or {}
        assert "CP1" not in json.dumps(crit)


def test_cp1_is_off_the_auto_ladder_and_leaves_its_lever_order_intact():
    assert "CP1" not in [r["id"] for r in ladder.RUNGS]
    assert "CP1" in [r["id"] for r in ladder.ALL_RUNGS]
    assert [r["lever"] for r in ladder.RUNGS] == [
        "baseline", "windows_per_day", "actions_per_window", "size", "concurrency"]


def test_no_seven_fifty_rung_was_selected():
    """Option B was explicitly rejected by the owner."""
    import re
    df = open("deploy/l5_r1_probe/Dockerfile", encoding="utf-8").read()
    # scan the ENV ASSIGNMENT, not prose -- the file also discusses the rung in
    # comments, and the first pass of this test matched one of those instead.
    env = [l.strip() for l in df.splitlines()
           if l.strip().startswith("PMUS_SPRING_RUNG=")]
    assert env == ["PMUS_SPRING_RUNG=CP1 \\"], env
    # Option B stays rejected. Checked on the ASSIGNED rung itself rather than
    # by scanning the file for rung ids -- the surrounding comments legitimately
    # mention R5F, and a substring scan reads that explanation as a violation.
    assigned = env[0].split("=", 1)[1].rstrip(" \\")
    assert assigned == "CP1"
    assert ladder.rung(assigned)["per_market_cap_usd"] == "5.00"
    seven_fifty = {r["id"] for r in ladder.ALL_RUNGS
                   if r["per_market_cap_usd"] == "7.50"}
    assert seven_fifty and assigned not in seven_fifty, seven_fifty


def test_all_three_market_allowances_agree(tmp_path):
    """THE ALLOWANCE IS A min() OF THREE INDEPENDENT NUMBERS, and any one of
    them left behind silently caps the authorization.

    `_rung_markets_of` returns min(PMUS_SPRING_RUNG_MARKETS, rung's
    markets_per_campaign), and the mandate's max_total_markets binds again at
    the wire. Widening one or two of the three would have produced a probe
    that quietly stopped early while every record claimed ten -- which is the
    failure this test exists to make impossible."""
    df = open("deploy/l5_r1_probe/Dockerfile", encoding="utf-8").read()
    env = [l.strip() for l in df.splitlines()
           if l.strip().startswith("PMUS_SPRING_RUNG_MARKETS=")]
    assert env == ["PMUS_SPRING_RUNG_MARKETS=10 \\"], env
    env_v = int(env[0].split("=", 1)[1].rstrip(" \\"))
    rung_v = ladder.rung("CP1")["markets_per_campaign"]
    mandate_v = _mandate(tmp_path).max_total_markets
    assert env_v == rung_v == mandate_v == 10, (env_v, rung_v, mandate_v)
    assert min(env_v, rung_v, mandate_v) == 10


def test_the_min_rule_itself_is_still_in_the_source():
    """min(rung, mandate) is the safety property; CP1 must not have weakened
    it into "the mandate wins"."""
    import re
    src = open("src/pmus_spring_l5_session.py", encoding="utf-8").read()
    body = src[src.index("def session_notional_cap"):]
    body = body[:body.index("\ndef ", 1)]
    code = re.sub(r'"""(?:.|\n)*?"""', "", body)
    assert "min(rung," in code.replace(" ", "") or "min(rung," in code


def test_guarded_transport_is_not_weakened_by_the_rung_work():
    """RESTATED 2026-09-16, and it still enforces the thing it was for.

    It asserted `git diff` on src/pmus_live_mandate.py was EMPTY. That was the
    right proof while the CP1 rung was the only change in flight: the rung must
    not touch the wire guard, and byte-identity proved it cheaply.

    The owner then authorized a change INSIDE that file -- the zero-exposure
    concurrency fix, after the live probe showed a zero-fill market holding
    max_concurrent_markets forever. Byte-identity would now fail for an
    authorized reason, which makes it the wrong instrument, not a failing one.

    So it asserts the PROPERTIES the authorization actually protects. These
    hold whatever else changes in the file, which is strictly more useful than
    a hash: enforcement stays on the wire, there is still no stage allow-list,
    the mandate is still immutable, and the RUNG work in particular still
    contributes nothing to this module.
    """
    from src import pmus_live_mandate as m
    src = open(m.__file__, encoding="utf-8").read()

    # enforcement is at the transport, on the order path
    assert 'ORDER_PATH = "/v1/orders"' in src
    assert "def guard(" in src and "MandateRefusal" in src

    # the mandate object cannot be edited at runtime
    assert "@dataclass(frozen=True)" in src

    # no stage allow-list came back: admission reads the BODY
    assert "def order_cash_effect(" in src

    # Everything below is scanned as CODE, via the AST, not as text. This
    # module's whole purpose is explaining the 2026-09-16 breach, so its
    # docstrings NAME the things it forbids -- `counts_against_market_cap()`
    # appears twice in prose precisely to say why it must never return. A bare
    # substring scan reads that explanation as the violation and fails green
    # code, which is the third time this session a text scan has done so.
    # What is actually dangerous is the SYMBOL: defined, called, imported, or
    # branched on. So look for that, and let the prose say what it likes.
    tree = ast.parse(src)

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))

    identifiers: set[str] = set()
    literals: list[str] = []
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            identifiers.add(node.name)
        elif isinstance(node, ast.arg):
            identifiers.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            identifiers.add(node.arg)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                literals.append(node.value)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")

    def _in_code(needle: str) -> str | None:
        for i in identifiers:
            if needle in i:
                return f"identifier {i!r}"
        for s in literals:
            if needle in s:
                return f"string literal {s!r}"
        return None

    # 1. the stage allow-list is gone as CODE, however often prose names it
    where = _in_code("counts_against_market_cap")
    assert where is None, f"the stage allow-list came back: {where}"

    # 2. and nothing replaced it: admission never branches on a stage name
    for s in literals:
        assert not s.startswith("spring_"), f"stage name {s!r} in executable code"

    # 3. the rung/sizing work contributes nothing here -- no symbol, and no
    #    import of the ladder or the sessions that drive it
    for leaked in ("tightest", "set_active_effective", "RISK_DIMENSIONS",
                   "target_notional", "max_leg_notional", "PMUS_SPRING_RUNG"):
        where = _in_code(leaked)
        assert where is None, f"{leaked} leaked into the mandate module: {where}"
    for mod in ("ramp_ladder", "spring_l5_session", "continuous_session"):
        assert not any(mod in i for i in imported), f"{mod} imported here"

    # 4. strongest form of the same claim: the wire guard depends on NOTHING
    #    in this repo. It cannot be loosened by a rung, a session or a sizing
    #    change, because it cannot see them.
    assert not any(i.startswith(("src", "pmus", "analysis")) for i in imported), \
        sorted(imported)

    # and the caps this rung is bounded by are still the ones enforced
    for cap in ("max_spend_per_market", "max_total_spend", "max_residual",
                "max_markets", "max_total_markets", "max_daily_realized_loss"):
        assert cap in src
