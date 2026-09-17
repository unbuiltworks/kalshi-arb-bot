"""The MLB cohort / settlement-horizon eligibility gate.

L5_ADAPTED venue/market SELECTION. These tests pin two things: that the gate
refuses everything whose horizon the venue has not positively established, and
that it cannot reach any trading behaviour while doing so.
"""
import os
import ast
import pytest

from src import pmus_l5_continuous_session as sess
from src import pmus_event_progress as prog

MLB_LIVE = {"league": "mlb", "ended": False, "game_proximity": 0.1111,
            "proximity_basis": "inning/baseball", "sport_type": "baseball"}


@pytest.fixture(autouse=True)
def _no_ambient_cohort(monkeypatch):
    monkeypatch.delenv(sess.COHORT_ENV, raising=False)


# ----------------------------------------------- default is unchanged

def test_unset_cohort_refuses_nothing():
    """THE compatibility property: committing this gate must not change the
    behaviour of a probe that does not opt into it."""
    assert sess.cohort_league() == ""
    for p in (None, {}, MLB_LIVE, {**MLB_LIVE, "league": "wta"},
              {**MLB_LIVE, "game_proximity": None}, {**MLB_LIVE, "ended": True}):
        assert sess.cohort_reason(p, "") is None


def test_the_cohort_comes_from_the_environment_and_is_case_folded(monkeypatch):
    monkeypatch.setenv(sess.COHORT_ENV, "  MLB  ")
    assert sess.cohort_league() == "mlb"


# ----------------------------------------------- what is eligible

def test_a_live_mlb_game_with_a_recognised_inning_is_eligible():
    assert sess.cohort_reason(MLB_LIVE, "mlb") is None


def test_every_inning_the_venue_serves_is_eligible():
    """A game is eligible from the 1st inning to the 9th: the gate selects on
    whether the game is UNDER WAY, not on how far through it is. Refusing an
    early inning would be a duration estimate, which we do not have."""
    for token in ("Top 1st", "Bot 1st", "Mid 5th", "End 7th", "Bot 9th"):
        prx, basis = prog.proximity(token, "baseball", False)
        assert prx is not None, token
        assert sess.cohort_reason(
            {**MLB_LIVE, "game_proximity": prx, "proximity_basis": basis},
            "mlb") is None, token


# ----------------------------------------------- fail closed

@pytest.mark.parametrize("progress,expect", [
    (None,                                          "cohort:not_in_live_feed"),
    ({},                                            "cohort:not_in_live_feed"),
    ({**MLB_LIVE, "ended": True},                   "cohort:event_ended"),
    ({**MLB_LIVE, "league": "wta"},                 "cohort:not_mlb:wta"),
    ({**MLB_LIVE, "league": "cfb"},                 "cohort:not_mlb:cfb"),
    ({**MLB_LIVE, "league": None},                  "cohort:not_mlb:unknown"),
    ({**MLB_LIVE, "league": ""},                    "cohort:not_mlb:unknown"),
])
def test_unknown_or_wrong_horizon_is_refused(progress, expect):
    assert sess.cohort_reason(progress, "mlb") == expect


def test_an_unrecognised_period_token_is_refused_not_guessed():
    """An esports "Map 2" yields no proximity because the venue does not serve
    series length. That must refuse, never fall through to admitted."""
    prx, basis = prog.proximity("Map 2", "esports", False)
    assert prx is None and basis == "unknown_series_length"
    r = sess.cohort_reason({**MLB_LIVE, "game_proximity": None,
                            "proximity_basis": basis}, "mlb")
    assert r == "cohort:unknown_progress:unknown_series_length"


def test_a_not_yet_started_game_is_refused():
    """A market whose game has not started is absent from the venue's
    live-only feed, so its progress lookup is None. That is the
    aec-cfb-lsu-miss-2026-09-19 case: kickoff 69.6 h away, admitted in all six
    measured cycles before this gate existed."""
    assert sess.cohort_reason(None, "mlb") == "cohort:not_in_live_feed"


def test_baseball_that_is_not_mlb_is_refused():
    """sport_type is too coarse to carry the cohort: NPB and KBO both serve
    "baseball". The league key is what the restriction is keyed on."""
    for other in ("npb", "kbo"):
        assert sess.cohort_reason({**MLB_LIVE, "league": other}, "mlb") == \
            f"cohort:not_mlb:{other}"


def test_a_live_feed_outage_refuses_everything_rather_than_admitting_it():
    """prog.table() returns an empty table on any failure, so every lookup is
    None. The gate must then refuse the whole board -- idle, not blind."""
    assert all(sess.cohort_reason(None, "mlb") is not None for _ in range(3))


# ----------------------------------------------- it cannot reach behaviour

def _code_tokens(name: str) -> set:
    """Every identifier, attribute, keyword and string LITERAL the function
    actually executes -- with its docstring removed.

    Scanning raw source would fail here for the wrong reason: this docstring
    names endDate and the trading parameters precisely in order to record that
    they are NOT consulted. A prose mention is not a reference.
    """
    import textwrap
    fn = ast.parse(textwrap.dedent(_fn_source(name))).body[0]
    first = fn.body[0] if fn.body else None
    has_doc = (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
               and isinstance(first.value.value, str))
    body = fn.body[1:] if has_doc else fn.body
    toks = {a.arg for a in fn.args.args}
    for stmt in body:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Name):
                toks.add(n.id)
            elif isinstance(n, ast.Attribute):
                toks.add(n.attr)
            elif isinstance(n, ast.keyword) and n.arg:
                toks.add(n.arg)
            elif isinstance(n, ast.Constant) and isinstance(n.value, str):
                toks.add(n.value)
    return {t.lower() for t in toks}


def test_the_code_token_scan_actually_catches_a_reference():
    """Guards the two tests below: if _code_tokens stopped seeing real
    references they would pass vacuously and prove nothing."""
    toks = _code_tokens("cohort_reason")
    assert "game_proximity" in toks and "league" in toks and "ended" in toks


def test_the_gate_never_consults_endDate():
    """endDate is a fixed +14 d contract expiry carrying no settlement
    information. If the gate ever reads one, it is wrong."""
    toks = _code_tokens("cohort_reason")
    for bad in ("enddate", "end_date", "hours_to_end", "horizon_basis",
                "market_state"):
        assert not any(bad in t for t in toks), f"cohort_reason consults {bad}"


def test_the_gate_touches_no_trading_parameter():
    """SELECTION only. Nothing about price, size, rest, pair ceiling, residual
    or ACT/WAIT behaviour may be reachable from the cohort decision."""
    toks = _code_tokens("cohort_reason")
    for bad in ("price", "qty", "size", "rest", "pair", "residual", "notional",
                "post_only", "aggress", "tick", "spread", "submit", "order",
                "cap", "spring_live", "spring_arm", "mandate"):
        assert not any(bad in t for t in toks), f"cohort_reason mentions {bad}"
    # "live" alone is deliberately NOT forbidden: the refusal string
    # "cohort:not_in_live_feed" names the venue's live-events FEED, which is
    # the gate's whole input. What must never appear is the ARMING flag.
    assert "cohort:not_in_live_feed" in toks


def test_the_gate_can_only_remove_candidates():
    """It returns None or a string. There is no branch that produces a
    candidate, so it can never widen the admitted set."""
    tree = ast.parse(_fn_source("cohort_reason"))
    fn = tree.body[0]
    for node in ast.walk(fn):
        if isinstance(node, ast.Return):
            v = node.value
            assert v is None or isinstance(v, (ast.Constant, ast.BinOp)), \
                ast.dump(v)


def _fn_source(name: str) -> str:
    import inspect
    return inspect.getsource(getattr(sess, name))


# ----------------------------------------------- ordering of reasons

def test_an_evidence_refusal_keeps_its_own_reason():
    """The cohort clause is evaluated only when the evidence gate already
    admitted, so a market refused on evidence is still reported for that --
    otherwise the refusal log stops telling us why markets really fail."""
    src = _fn_source("rank_candidates")
    i_evidence = src.index("strict_evidence_reason")
    i_cohort = src.index("cohort_reason(")
    assert i_evidence < i_cohort
    # and it is guarded by `reason is None`
    seg = src[src.index("until = exclude.get(slug)"):i_cohort]
    assert "if reason is None:" in seg


def test_the_refusals_are_counted_so_the_cost_is_measurable():
    """A gate whose removed opportunity is not measurable cannot be reviewed."""
    src = _fn_source("rank_candidates")
    assert "cohort_refused" in src and "cohort_refusal_reasons" in src


# ----------------------------------------------- the league key itself

def test_league_is_read_from_the_venues_primaryTag():
    assert prog._league_of({"primaryTag": {"slug": "mlb"}}) == "mlb"
    assert prog._league_of({"primaryTag": "mlb"}) == "mlb"


def test_a_missing_or_empty_primaryTag_is_unknown_never_a_match():
    for e in ({}, {"primaryTag": None}, {"primaryTag": {}},
              {"primaryTag": {"slug": ""}}, {"primaryTag": ""}):
        assert prog._league_of(e) is None
        assert sess.cohort_reason({**MLB_LIVE, "league": prog._league_of(e)},
                                  "mlb") == "cohort:not_mlb:unknown"


def test_progress_table_carries_the_league_through():
    rows = [{"id": 1, "slug": "ev", "primaryTag": {"slug": "mlb"},
             "period": "Bot 3rd", "eventState": {"type": "baseball"},
             "markets": [{"slug": "aec-mlb-x-y-2026-09-16"}]}]
    tbl = prog.progress_table(rows, now=0.0)
    got = tbl["aec-mlb-x-y-2026-09-16"]
    assert got["league"] == "mlb"
    assert got["game_proximity"] is not None
    assert sess.cohort_reason(got, "mlb") is None
