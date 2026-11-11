"""The preflight must never leak key material, must report NONE honestly,
and must not be mistaken for something that has actually run."""
from decimal import Decimal as D

from src import kalshi_account_preflight_readonly as P


# ------------------------------------------------------ secret discipline
def test_the_fingerprint_is_four_characters_and_nothing_more():
    assert P.key_id_fingerprint("abcdef0123456789") == "...6789"
    assert P.key_id_fingerprint("") is None
    assert P.key_id_fingerprint(None) is None


def test_credential_presence_never_returns_key_material(monkeypatch):
    secret_id = "SUPERSECRETKEYID0001"
    secret_pem = "-----BEGIN PRIVATE KEY-----abc-----END PRIVATE KEY-----"
    monkeypatch.setenv("KALSHI_API_KEY_ID", secret_id)
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PEM", secret_pem)
    blob = repr(P.credential_presence())
    assert secret_id not in blob
    assert secret_pem not in blob
    assert "BEGIN PRIVATE KEY" not in blob
    # only the last four characters may appear
    assert "...0001" in blob
    assert secret_id[:-4] not in blob


def test_the_whole_preflight_payload_is_free_of_key_material(monkeypatch):
    monkeypatch.setenv("KALSHI_API_KEY_ID", "LEAKMEPLEASE9999")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PEM", "-----BEGIN RSA-----zzz")
    blob = repr(P.preflight(balance_payload={"balance": 100}))
    assert "LEAKMEPLEASE" not in blob
    assert "BEGIN RSA" not in blob


# ------------------------------------------------------------- balance
def test_balance_is_cents_and_absence_is_not_zero():
    assert P.balance_usd({"balance": 341900}) == D("3419")
    assert P.balance_usd({}) is None          # unreadable != zero
    assert P.balance_usd(None) is None
    assert P.balance_usd({"balance": "junk"}) is None
    assert P.balance_usd({"balance": 0}) == D(0)


def test_the_owner_figure_is_checked_not_assumed():
    ok = P.preflight(balance_payload={"balance": 341900},
                     expected_cash_usd=D("3419"))
    assert ok["balance_matches_owner_figure"] is True
    bad = P.preflight(balance_payload={"balance": 1000},
                      expected_cash_usd=D("3419"))
    assert bad["balance_matches_owner_figure"] is False
    unknown = P.preflight(balance_payload=None, expected_cash_usd=D("3419"))
    assert unknown["balance_matches_owner_figure"] is None


# ------------------------------------------- the reward ledger that isn't
def test_the_absence_of_a_reward_ledger_is_recorded_with_its_method():
    assert "no dedicated reward-ledger endpoint" in P.NO_REWARD_LEDGER
    assert "31 plausible names return 404" in P.NO_REWARD_LEDGER
    for e in ("/portfolio/balance", "/portfolio/fills",
              "/portfolio/settlements"):
        assert e in P.ENDPOINTS_THAT_EXIST


def test_the_reward_must_be_inferred_from_a_balance_residue():
    r = P.REWARD_OBSERVATION_PATH
    assert "balance_delta - realised_fill_cash - settlement_cash" in r
    assert "otherwise idle account" in r
    assert "destroys the only instrument we have" in r


# ------------------------------------------------------- honest NONE
def test_no_prior_activity_reports_a_clean_none():
    out = P.scan_for_prior_incentivised_activity([], [])
    assert out["prior_activity"] is False
    assert out["verdict"].startswith("NONE")
    assert "no liquidity reward can ever have been credited" in out["verdict"]


def test_prior_activity_is_flagged_for_reconciliation_not_assumed_paid():
    fills = [{"ticker": "KXNATGAS15M-26SEP082015-15"},
             {"ticker": "KXCOPPER15M-26SEP082015-15"},
             {"ticker": "KXBTC15M-26SEP082030-T3"}]     # not incentivised
    out = P.scan_for_prior_incentivised_activity(fills, [])
    assert out["prior_activity"] is True
    assert out["fills_on_incentivised_series"] == 2
    assert out["by_series"] == {"KXNATGAS15M": 1, "KXCOPPER15M": 1}
    assert "reconcile the balance" in out["verdict"]
    assert "before assuming a reward was credited" in out["verdict"]


def test_it_never_infers_a_share_from_displayed_depth():
    """The prohibition the owner named explicitly. The same book supports a
    78% share or a 0.7% share depending on the unknown denominator."""
    # normalise the wrap: the claim must survive line breaks in the docstring
    d = " ".join(P.__doc__.split())
    assert "never infers a share from displayed depth" in d
    assert "78% share or a 0.7% share" in d


# --------------------------------------------- it has not actually run
def test_the_module_states_it_has_not_been_executed_live():
    b = P.BLOCKED_HERE
    assert "holds NO Kalshi credentials" in b
    assert "WRITTEN AND TESTED but has NOT been executed" in b
    assert "no balance, identity or reward figure in this repo is asserted" in b


def test_there_is_no_order_path():
    import inspect
    src = inspect.getsource(P)
    assert P.ORDER_ENTRY is False
    for forbidden in ("POST", "create_order", "place_order", "DELETE"):
        assert forbidden not in src, forbidden


# ------------------------------------------------- the fetcher is GET-only
def test_the_fetcher_has_no_order_path():
    """Checks EXECUTABLE CODE, not prose. The first version of this grepped
    the whole source and tripped on the docstring that describes the rule --
    a test that cannot tell a prohibition from a violation is not a test."""
    import ast
    import inspect
    from src import kalshi_account_preflight_fetch as F

    tree = ast.parse(inspect.getsource(F))
    calls = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute):
                calls.add(fn.attr)
            elif isinstance(fn, ast.Name):
                calls.add(fn.id)
    for forbidden in ("post", "delete", "put", "patch", "create_order",
                      "place_order", "cancel_order"):
        assert forbidden not in calls, forbidden
    # every HTTP verb it does use is a get
    assert "get" in calls
    assert F.READ_PATHS == ("/portfolio/balance", "/portfolio/fills",
                            "/portfolio/settlements")


def test_the_fetcher_order_path_check_would_catch_a_real_violation():
    """Prove the AST check works, rather than trusting it."""
    import ast
    tree = ast.parse("async def f(c):\n    await c.post('/portfolio/orders')\n")
    calls = {n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "post" in calls


def test_the_fetcher_suppresses_exception_messages():
    """A signing failure can echo key bytes in its message, so only the
    exception TYPE may be reported."""
    from src import kalshi_account_preflight_fetch as F
    import inspect
    src = inspect.getsource(F.main_async)
    assert 'type(exc).__name__' in src
    assert "the message is suppressed" in src


def test_the_workflow_never_echoes_a_secret_value():
    """The presence step prints booleans derived from -n tests, never the
    variables themselves."""
    from pathlib import Path
    wf = Path(".github/workflows/kalshi-account-preflight.yml").read_text()
    # presence is derived from -n, and no secret-bearing var is ever printed
    assert '[ -n "$1" ]' in wf
    for var in ("N1", "N2", "N4", "KALSHI_API_KEY_ID",
                "KALSHI_PRIVATE_KEY_PEM"):
        for form in (f'echo "${var}"', f'echo ${var}',
                     f'printf "${var}"', f'cat ${var}'):
            assert form not in wf, form
    assert "runs-on: ubuntu-latest" in wf       # not the AWS self-hosted box
    # push-on-path is the only trigger; nothing schedules or auto-repeats it
    assert "schedule:" not in wf
    assert "workflow_dispatch" not in wf
    assert "branches: [feature/l5-transfer-backtest]" in wf


def test_the_workflow_installs_what_settings_actually_imports():
    """config/settings.py imports dotenv at module load. Omitting it made
    the first run die with ModuleNotFoundError before touching the network."""
    from pathlib import Path
    wf = Path(".github/workflows/kalshi-account-preflight.yml").read_text()
    assert "python-dotenv" in wf
    # and a credential-free import check, whose traceback is safe to show
    assert "Import check (no credentials in scope)" in wf
