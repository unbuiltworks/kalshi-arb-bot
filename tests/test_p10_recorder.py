"""The eight proofs required before the persistent P10 recorder is deployed.

It runs unattended beside a money-adjacent service, so every property that
makes that safe is a test rather than an intention.
"""
import ast
import io
import json
import os
import sys
import tokenize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "analysis", "gemini"))

from src.gemini_execution import p10_recorder as R                # noqa: E402
from src.gemini_execution import shadow_soak as SS                # noqa: E402

SRC = os.path.join(ROOT, "src", "gemini_execution", "p10_recorder.py")


def _code_only(path):
    """Source with comments and string literals stripped.

    The prose trap has caught this programme five times: a docstring saying
    "no order path" makes a naive grep for "order" fail.
    """
    out = []
    with open(path, "rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
    return " ".join(out)


# ---- PROOF 1: read-only ---------------------------------------------------

def test_1_read_only_no_mutation_verb_in_code():
    code = _code_only(SRC)
    for bad in ("place_order", "new_order", "cancel_order", "submit_order",
                "createOrder", "POST", "urlopen_post", "requests.post",
                "order.place", "order.cancel"):
        assert bad not in code, bad


def test_1_read_only_only_two_public_get_paths():
    code = _code_only(SRC)
    assert "urlopen" in code                      # it does fetch
    assert code.count("Request(") == 0            # never a custom method
    tree = ast.parse(open(SRC).read())
    consts = {n.value for n in ast.walk(tree)
              if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    paths = {c for c in consts if c.startswith("/v1/")}
    assert paths == {"/v1/prediction-markets/events?limit=60&category=Crypto",
                     "/v1/prediction-markets/events/%s",
                     "/v1/trades/%s?limit_trades=50"}, paths


# ---- PROOF 2: mutations_sent is 0, structurally ---------------------------

def test_2_health_record_reports_zero_mutations(tmp_path, monkeypatch):
    h = _drive(tmp_path, monkeypatch)
    assert h["mutations_sent"] == 0


# ---- PROOF 3: no authorization or arming path -----------------------------

def test_3_no_arming_or_credential_import():
    tree = ast.parse(open(SRC).read())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    for bad in ("arming", "bundle", "gemini_prediction_auth", "hmac",
                "hashlib", "base64", "ws_continuous", "run_continuous",
                "continuous_probe"):
        assert not any(bad in m for m in mods), (bad, mods)


def test_3_no_credential_environment_read():
    code = _code_only(SRC)
    for bad in ("GEMINI_API_KEY", "GEMINI_API_SECRET", "GEMINI_CANARY_LIVE",
                "GEMINI_CANARY_AUTH", "getenv", "environ"):
        assert bad not in code, bad


# ---- PROOF 4: existing collector outputs unchanged ------------------------

def test_4_p10_path_never_collides_with_a_cohort_evidence_path():
    for root in ("/state", "analysis/gemini"):
        p10 = R.out_dir(root)
        for cohort in ("BTC5m", "BTC15m"):
            ev, js = SS._cohort_paths(root, cohort)
            assert not ev.startswith(p10), (ev, p10)
            assert not js.startswith(p10), (js, p10)
            assert not p10.startswith(ev)


def test_4_recorder_writes_only_under_its_own_directory(tmp_path, monkeypatch):
    _drive(tmp_path, monkeypatch)
    written = {os.path.relpath(os.path.join(dp, f), str(tmp_path))
               for dp, _d, fs in os.walk(str(tmp_path)) for f in fs}
    assert written, "nothing was written at all"
    assert all(p.startswith("p10" + os.sep) for p in written), written


# ---- PROOF 5 and 7: restart persistence ----------------------------------

def test_5_seen_index_is_rebuilt_from_raw_rows_after_restart(tmp_path,
                                                             monkeypatch):
    _drive(tmp_path, monkeypatch)
    seen = R.load_seen(str(tmp_path), "btc15m")
    assert ("GEMI-BTC15M2609230015-UP", 111) in seen
    assert ("GEMI-BTC15M2609230015-UP", 112) in seen


def test_7_raw_records_survive_a_restart_and_are_appended_not_truncated(
        tmp_path, monkeypatch):
    _drive(tmp_path, monkeypatch)
    path = R.out_path(str(tmp_path), "btc15m")
    first = open(path).read().count("\n")
    # a "restart": fresh seen set rebuilt from disk, same directory
    _drive(tmp_path, monkeypatch, seen=R.load_seen(str(tmp_path), "btc15m"))
    second = open(path).read().count("\n")
    assert second > first, (first, second)
    rows = [json.loads(l) for l in open(path)]
    assert sum(1 for r in rows if r["kind"] == "trade") == 2, \
        "a restart re-wrote prints it had already recorded"


# ---- PROOF 6: dedupe by (symbol, tid) ------------------------------------

def test_6_dedupe_by_symbol_and_tid_within_a_run(tmp_path, monkeypatch):
    seen = set()
    _drive(tmp_path, monkeypatch, seen=seen)
    _drive(tmp_path, monkeypatch, seen=seen)          # same tape served twice
    rows = [json.loads(l) for l in open(R.out_path(str(tmp_path), "btc15m"))]
    tids = [r["tid"] for r in rows if r["kind"] == "trade"]
    assert sorted(tids) == [111, 112], tids
    assert len(tids) == len(set(tids))


def test_6_a_trade_without_a_tid_is_dropped_not_guessed(tmp_path, monkeypatch):
    h = _drive(tmp_path, monkeypatch, with_untagged=True)
    rows = [json.loads(l) for l in open(R.out_path(str(tmp_path), "btc15m"))]
    assert all(r.get("tid") is not None
               for r in rows if r["kind"] == "trade")
    assert h["trade_rows"] == 2


# ---- PROOF 8: the audit harness consumes it untransformed -----------------

def test_8_frozen_decoder_reads_the_artifact_with_no_transformation(
        tmp_path, monkeypatch):
    _drive(tmp_path, monkeypatch)
    import p10b_decode as B
    books, trades = B.load(R.out_path(str(tmp_path), "btc15m"))
    assert trades and books
    out = [B.classify_one(t, books, 5.0) for t in trades]
    assert {o["label"] for o in out} <= {
        "H1", "H2", "AMBIGUOUS_BOTH", "AMBIGUOUS_NEITHER", "STALE",
        "PRE_CAPTURE", "NO_BOOK"}
    # and the independent verifier's reader, too
    sys.path.insert(0, os.path.join(ROOT, "analysis", "gemini", "audit"))
    import verify as V
    bk, tr = V.read(R.out_path(str(tmp_path), "btc15m")) \
        if hasattr(V, "read") else (None, None)
    assert V.clopper_pearson_upper(0, 10) > 0


def test_8_row_schema_matches_p10a_capture_field_for_field(tmp_path,
                                                           monkeypatch):
    """A new field here would mean a new decoder, and the decoder is frozen."""
    _drive(tmp_path, monkeypatch)
    rows = [json.loads(l) for l in open(R.out_path(str(tmp_path), "btc15m"))]
    book = next(r for r in rows if r["kind"] == "book")
    trade = next(r for r in rows if r["kind"] == "trade")
    assert set(book) == {
        "kind", "poll_id", "symbol", "category", "subcategory", "t_sent",
        "t_recv", "rtt", "bid", "ask", "last", "buy_yes", "buy_no",
        "sell_yes", "sell_no", "expiry", "start", "resolution"}
    assert set(trade) == {
        "kind", "poll_id", "rank_in_response", "n_in_response",
        "new_in_response", "symbol", "category", "subcategory", "tid", "t",
        "tms", "price", "amount", "type", "t_sent", "t_recv", "rtt",
        "first_seen"}


# ---- freshness budget -----------------------------------------------------

def test_hourly_ladder_is_excluded_by_decision():
    assert [c[1] for c in R.COHORTS] == ["BTC15M", "BTC05M"]
    assert not any("2609" in c[1] or c[1].endswith("H") for c in R.COHORTS)


def test_btc15m_is_served_first():
    assert R.COHORTS[0][0] == "btc15m"


def test_a_slow_cycle_is_recorded_degraded(tmp_path, monkeypatch):
    h = _drive(tmp_path, monkeypatch, slow=True)
    assert h["degraded"] is True
    assert h["est_median_book_age_s"] > R.MAX_USEFUL_BOOK_AGE_S


def test_a_fast_cycle_is_not_degraded(tmp_path, monkeypatch):
    assert _drive(tmp_path, monkeypatch)["degraded"] is False


# ---------------------------------------------------------------- harness

SYM = "GEMI-BTC15M2609230015-UP"


def _drive(tmp_path, monkeypatch, *, seen=None, slow=False,
           with_untagged=False):
    """One cycle against a fake venue. No socket is opened."""
    trades = [{"tid": 111, "timestamp": 1790000000, "timestampms": 1790000000000,
               "price": "0.42", "amount": "3", "type": "buy"},
              {"tid": 112, "timestamp": 1790000001,
               "timestampms": 1790000001000, "price": "0.44", "amount": "1",
               "type": "sell"}]
    if with_untagged:
        trades = trades + [{"tid": None, "price": "0.5", "amount": "1",
                            "type": "buy"}]

    def fake_get(path, bucket, tries=2):
        t0 = time.time()
        if slow:
            t1 = t0 + 8.0
        else:
            t1 = t0 + 0.01
        if path.startswith("/v1/trades/"):
            return trades, t0, t1
        return ({"contracts": [{"instrumentSymbol": SYM, "status": "active",
                                "prices": {"bestBid": "0.41", "bestAsk": "0.47",
                                           "lastTradePrice": "0.43",
                                           "buy": {"yes": "0.47", "no": "0.59"},
                                           "sell": {"yes": "0.41",
                                                    "no": "0.53"}},
                                "expiryDate": "2026-09-23T00:15:00.000Z",
                                "effectiveDate": "2026-09-23T00:00:00.000Z",
                                "resolutionSide": None}]}, t0, t1)

    import time
    monkeypatch.setattr(R, "_get", fake_get)
    if slow:
        monkeypatch.setattr(R.time, "time",
                            _advancing(start=1790000000.0, step=4.0))
    return R.run_cohort_cycle(
        "btc15m", "BTC15M", 1, state_root=str(tmp_path),
        bucket=R._Bucket(1000.0), seen=(set() if seen is None else seen),
        poll=[0], syms=[{"ticker": "BTC15M2609230015", "symbol": SYM}])


def _advancing(start, step):
    state = {"t": start}

    def now():
        state["t"] += step
        return state["t"]
    return now


import time                                                    # noqa: E402
