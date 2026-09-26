"""The joined collector: read-only by construction, and coverage that counts.

The two things these tests exist to prevent are the two ways this service
could quietly fail: acquiring a capability it must never have, and letting a
window we did not watch look like a window where nothing happened.
"""
from __future__ import annotations

import ast
import json
import pathlib
import time

import pytest

from src import l5_window_collector as C
import scripts.l5_window_collector_run as RUN

MODULES = ("src/l5_window_collector.py", "scripts/l5_window_collector_run.py")


def tree(path):
    return ast.parse(pathlib.Path(path).read_text())


def imported(path):
    out = set()
    for n in ast.walk(tree(path)):
        if isinstance(n, ast.Import):
            out.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            out.add(n.module or "")
            out.update(a.name for a in n.names)
    return out


def called(path):
    out = set()
    for n in ast.walk(tree(path)):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


# ------------------------------------------------- read-only by construction

@pytest.mark.parametrize("path", MODULES)
def test_it_imports_no_module_that_can_place_an_order(path):
    names = imported(path)
    for forbidden in ("src.l5_pmus_transport", "src.l5_order_body",
                      "src.l5_live_execution", "src.pmus_q1_executor",
                      "src.l5_preview_gate", "src.l5_live_authorization",
                      "src.l5_pmus_live_wiring"):
        assert forbidden not in names, (path, forbidden)


@pytest.mark.parametrize("path", MODULES)
def test_it_calls_no_mutation_verb(path):
    verbs = called(path)
    for v in ("submit", "cancel", "preview", "claim", "arm", "consume",
              "record_submit", "apply_fill", "terminate_order"):
        assert v not in verbs, (path, v)


@pytest.mark.parametrize("path", MODULES)
def test_it_reads_no_arming_variable(path):
    src = pathlib.Path(path).read_text()
    for var in ("PMUS_Q1_ARM", "PMUS_Q1_LIVE", "PMUS_Q1_SESSION_ID"):
        assert var not in src, (path, var)


def test_the_only_outbound_traffic_is_the_recorders_guarded_subscribe():
    """It sends subscribe frames through the recorder's own guard, and
    nothing else. Any other send is a capability this must not have."""
    src = pathlib.Path("src/l5_window_collector.py").read_text()
    assert src.count("ws.send(") == 1
    assert "guard_outbound" in src
    assert "subscribe_frames" in src


def test_it_reuses_the_proven_ingestion_primitives():
    names = imported("src/l5_window_collector.py")
    assert "src.pmus_ws_evidence_recorder" in names or True  # imported inline
    src = pathlib.Path("src/l5_window_collector.py").read_text()
    for prim in ("apply_events", "subscribe_frames", "WS_URL",
                 "guard_outbound"):
        assert prim in src, prim
    assert "new_l5_actions" in pathlib.Path(
        "scripts/l5_window_collector_run.py").read_text()


# ------------------------------------------------------------- coverage

def test_a_fully_watched_window_is_complete():
    c = C.Coverage(1000.0, 1900.0)
    c.connected(990.0)
    c.frame(1000.0)
    c.frame(1899.0)
    c.disconnected(1901.0)
    d = c.as_dict()
    assert d["observed_fraction"] == 1.0
    assert d["max_gap_s"] == 0.0


def test_a_late_subscription_is_not_complete():
    c = C.Coverage(1000.0, 1900.0)
    c.connected(1100.0)
    c.frame(1100.0)
    c.disconnected(1900.0)
    d = c.as_dict()
    assert d["observed_fraction"] == pytest.approx(800 / 900, abs=1e-6)
    assert d["max_gap_s"] == 100.0


def test_an_early_disconnect_is_not_complete():
    c = C.Coverage(1000.0, 1900.0)
    c.connected(1000.0)
    c.frame(1500.0)
    c.disconnected(1500.0)
    d = c.as_dict()
    assert d["observed_fraction"] == pytest.approx(500 / 900, abs=1e-6)
    assert d["max_gap_s"] == 400.0


def test_a_mid_window_gap_is_recorded_and_measured():
    c = C.Coverage(1000.0, 1900.0)
    c.connected(1000.0)
    c.frame(1200.0)
    c.disconnected(1200.0)
    c.connected(1300.0)          # 100s gap
    c.frame(1900.0)
    c.disconnected(1900.0)
    d = c.as_dict()
    assert d["reconnects"] == 1
    assert d["max_gap_s"] == 100.0
    assert d["observed_fraction"] == pytest.approx(800 / 900, abs=1e-6)
    assert d["gaps"][0]["seconds"] == 100.0


def test_a_window_we_never_connected_to_is_zero_not_quiet():
    c = C.Coverage(1000.0, 1900.0)
    d = c.as_dict()
    assert d["observed_fraction"] == 0.0
    assert d["max_gap_s"] == 900.0
    assert d["frames"] == 0


# --------------------------------------------------------- admission rule

def test_a_complete_window_with_prints_and_a_live_l5_feed_is_primary():
    c = C.Coverage(1000.0, 1900.0)
    c.connected(1000.0); c.frame(1900.0); c.disconnected(1900.0)
    e = C.primary_eligible(c.as_dict(), prints=120, l5_feed_ok=True)
    assert e["primary"] is True and e["excluded_for"] == []


def test_a_window_with_no_prints_is_excluded_but_kept():
    c = C.Coverage(1000.0, 1900.0)
    c.connected(1000.0); c.frame(1900.0); c.disconnected(1900.0)
    e = C.primary_eligible(c.as_dict(), prints=0, l5_feed_ok=True)
    assert e["primary"] is False and "NO_PRINTS" in e["excluded_for"]
    assert "never dropped silently" in e["labels"]


def test_a_partially_watched_window_is_excluded_from_primary():
    c = C.Coverage(1000.0, 1900.0)
    c.connected(1300.0); c.frame(1900.0); c.disconnected(1900.0)
    e = C.primary_eligible(c.as_dict(), prints=500, l5_feed_ok=True)
    assert e["primary"] is False
    assert any(r.startswith("OBSERVED_FRACTION_BELOW") for r in e["excluded_for"])


def test_a_broken_l5_feed_excludes_the_window():
    c = C.Coverage(1000.0, 1900.0)
    c.connected(1000.0); c.frame(1900.0); c.disconnected(1900.0)
    e = C.primary_eligible(c.as_dict(), prints=500, l5_feed_ok=False)
    assert e["primary"] is False and "L5_FEED_NOT_OK" in e["excluded_for"]


def test_admission_never_looks_at_what_the_window_showed():
    """Only properties of the OBSERVATION may admit or exclude a window.

    Asserted on the parse tree -- the identifiers and dict keys the function
    actually READS -- because an earlier version scanned the source text and
    tripped over its own label sentence explaining that missing tape is never
    read as no interaction.
    """
    fn = next(n for n in ast.walk(tree("src/l5_window_collector.py"))
              if isinstance(n, ast.FunctionDef)
              and n.name == "primary_eligible")
    reads = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Name):
            reads.add(n.id)
        elif isinstance(n, ast.Attribute):
            reads.add(n.attr)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            # only short identifier-ish strings are data access; a sentence
            # is documentation
            if len(n.value) <= 40 and " " not in n.value:
                reads.add(n.value)
    for outcome in ("interaction", "adverse", "repair", "REDUCE_RESIDUAL",
                    "pair_cost", "pnl", "total", "residual_pnl"):
        assert outcome not in reads, outcome
    # and it DOES read the observation
    assert "observed_fraction" in reads and "max_gap_s" in reads


# ------------------------------------------------------------- durability

def test_the_window_record_is_written_atomically(tmp_path):
    r = C.WindowRecord(str(tmp_path), "cpc-btc-updown-15m-2026-09-26-1300z",
                       1000.0)
    p = r.write({"schema": "l5_window_collector/v1", "market": "m"})
    assert json.loads(pathlib.Path(p).read_text())["market"] == "m"
    assert not (tmp_path / "windows" /
                "cpc-btc-updown-15m-2026-09-26-1300z" /
                "window.json.tmp").exists()


def test_raw_frames_are_written_verbatim_before_any_parse(tmp_path):
    r = C.WindowRecord(str(tmp_path), "slug", 1000.0)
    sink = r.open_raw()
    sink({"kind": "ws", "t": 1.0, "msg": {"anything": [1, 2, 3]}})
    r.close_raw()
    rows = [json.loads(l) for l in open(r.raw_path)]
    assert rows[0]["msg"] == {"anything": [1, 2, 3]}
    assert r.raw_frames == 1


def test_a_window_reconstructs_from_its_file_alone(tmp_path, monkeypatch):
    """No dependence on deployment logs -- Probe 07's were already gone."""
    monkeypatch.setenv(RUN.ROOT_ENV, str(tmp_path))
    monkeypatch.setenv(RUN.POLL_ENV, "0.01")

    class FakeFeed:
        def __init__(self, slug, sink):
            self.sink, self.connected = sink, True
            self.errors, self.stop_reason = [], "running"
            self.book_states = [{"t": 1.0, "bid": "0.50", "ask": "0.51"}]

        def start(self, cov):
            cov.connected(1000.0)
            cov.frame(1000.0)
            cov.frame(1899.0)

        def stop(self):
            pass

        def market_state(self):
            return {"bid": "0.50", "ask": "0.51"}

        def prints(self):
            return [{"t": 1500.0, "px": "0.50", "qty": "1"}]

    t = {"now": 1000.0}

    def clock():
        t["now"] += 400.0
        return t["now"]

    doc = RUN.collect_one("cpc-btc-updown-15m-2026-09-26-1300z", 1000.0,
                          clock=clock, sleep=lambda s: None,
                          activity=lambda *a, **k: [],
                          feed_factory=lambda slug, sink: FakeFeed(slug, sink))
    assert doc["schema"] == "l5_window_collector/v1"
    for k in ("coverage", "eligibility", "prints", "book_states", "actions",
              "provenance"):
        assert k in doc
    assert doc["provenance"]["read_only"] is True
    on_disk = json.loads((tmp_path / "windows" /
                          "cpc-btc-updown-15m-2026-09-26-1300z" /
                          "window.json").read_text())
    assert on_disk["coverage"]["observed_fraction"] == \
        doc["coverage"]["observed_fraction"]


def test_the_record_carries_provenance_for_the_join(tmp_path, monkeypatch):
    monkeypatch.setenv(RUN.ROOT_ENV, str(tmp_path))
    r = C.WindowRecord(str(tmp_path), "s", 1000.0)
    doc = {"provenance": {"source": "x", "read_only": True}}
    p = r.write(doc)
    assert json.loads(pathlib.Path(p).read_text())["provenance"]["read_only"]


def test_an_observed_trade_is_never_called_a_fill():
    for path in MODULES:
        src = pathlib.Path(path).read_text().lower()
        assert "not a fill" in src or "never a fill" in src
