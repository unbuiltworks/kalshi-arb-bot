"""Sports candidate -> spring preflight reconciliation (issue #167
comment 5540937360): binary SPRING_READY_FOR_REVIEW with named
blockers, provenance separation, the durable-mount manifest, and the
one-sided-YES investigation fixes."""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path

from src import pmus_spring_evidence_transfer as xfer
from src import pmus_sports_l5_decision as l5d
from src import pmus_spring_shadow_replay as replay
from src import pmus_ws_evidence_recorder_enriched as enriched

SLUG = "aec-atp-yanmil-juaser-2026-09-04"
SIB = "aec-atp-pedrma-alecan-2026-09-04"


def _iso(ts): return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _obs(ts, slug=SLUG, bid="0.48", ask="0.50", cum="100", last=None):
    return {"kind": "bbo_book", "t": _iso(ts), "slug": slug,
            "league": "atp", "bid": bid, "ask": ask,
            "bid_touch_qty": "500", "ask_touch_qty": "400",
            "ask_book_missing": False, "last_trade_px": last,
            "shares_traded_cum": cum}


def _model():
    full = {}
    for band in ("p25-45c", "p45-55c", "p55-75c"):
        for imb in ("flat", "imb1-10"):
            for last in ("open", "extend", "complete"):
                full[f"{band}|tte>10m|{imb}|{last}|q<5"] = {
                    "extend|q<5": 8, "complete|q<5": 2}
    return {"full": full, "base": {},
            "marginal": {"extend|q<5": 5, "open|q<5": 2},
            "cadence_modal": {}, "_sha256_16": "xfertest00000000"}


def _flowing(base, slug=SLUG, bid="0.48", ask="0.50", n=26):
    rows, cum = [], 100
    for i in range(n):
        t, last = base + i * 35, None
        if i % 3 == 1:
            cum += 10; last = bid
        if i % 3 == 2:
            cum += 10; last = ask
        rows.append(_obs(t, slug=slug, bid=bid, ask=ask,
                         cum=str(cum), last=last))
    return rows


# ---- item 7: one-sided YES was an ADAPTER ARTIFACT, now fixed ----

def test_open_side_follows_cheaper_touch_not_hardcoded_yes():
    """The frozen model emits a relation, never a side. Opening was
    hardcoded YES, which structurally produced YES-heavy inventory."""
    cheap_no = {"bid": D("0.70"), "ask": D("0.72")}     # NO join 0.28
    cheap_yes = {"bid": D("0.28"), "ask": D("0.30")}    # YES join 0.28
    assert l5d.open_side_yes(cheap_no) is False
    assert l5d.open_side_yes(cheap_yes) is True
    assert l5d.open_side_yes({"bid": None, "ask": None}) is None


def test_shadow_opens_no_when_no_is_cheaper():
    base = 1_000_000.0
    rows = _flowing(base, bid="0.7000", ask="0.7200")
    r = l5d.decide_market(SLUG, rows, base + 26 * 35, _model())
    sides = {a.get("side") for a in r["actions_replayed"] if a.get("side")}
    assert sides == {"NO"}, sides
    assert r["inventory_final"]["yes"] == 0


def test_residual_cap_bounds_one_sided_accumulation():
    base = 1_000_000.0
    rows = _flowing(base, bid="0.7000", ask="0.7200")
    r = l5d.decide_market(SLUG, rows, base + 26 * 35, _model())
    assert abs(r["inventory_final"]["residual"]) <= l5d.MAX_RESIDUAL_UNITS
    caps = [a for a in r["actions_replayed"]
            if str(a.get("reason", "")).startswith("residual_cap")]
    assert caps, "cap must be visible in the audit trail, not silent"


def test_extend_joins_its_own_touch_and_only_complete_uses_pair_bound():
    """Latent mispricing found in the same pass: a NO-side action was
    priced through the pair bound even when it was an EXTEND."""
    inv = l5d.Inventory()
    book = {"bid": D("0.70"), "ask": D("0.72")}
    opened = l5d.plan_action("open", inv, book)
    assert opened["side"] == "NO" and opened["target_price"] == "0.28"
    inv.record(False, D("0.28"))
    ext = l5d.plan_action("extend", inv, book)
    assert ext["side"] == "NO" and ext["target_price"] == "0.28"
    comp = l5d.plan_action("complete", inv, book)
    assert comp["side"] == "YES"
    # completion is the only action bounded by >=1c-after-fees
    assert D(comp["target_price"]) <= l5d.hard_pair_bound() - D("0.28")


# ---- items 1-3: candidate is not readiness; provenance is explicit ----

def test_shadow_candidate_never_implies_spring_ready():
    base = 1_000_000.0
    by_slug = {SLUG: _flowing(base), SIB: _flowing(base, slug=SIB)}
    out = xfer.reconcile(by_slug, now=base + 26 * 35 + 30)
    assert out["kind"] == "PMUS_SPRING_PREFLIGHT_RECONCILE"
    assert out["SPRING_READY_FOR_REVIEW"] is False
    assert "does NOT mean" in out["note"]
    if out["SHADOW_PREFLIGHT_CANDIDATE"]:
        assert out["spring_readiness"]["blockers"]


def test_provenance_gate_blocks_inferred_sports_evidence():
    base = 1_000_000.0
    by_slug = {SLUG: _flowing(base), SIB: _flowing(base, slug=SIB)}
    r = xfer.spring_readiness(by_slug, SLUG, now=base + 26 * 35 + 30,
                              durable_reason=lambda: None)
    assert r["SPRING_READY_FOR_REVIEW"] is False
    prov = r["layers"][xfer.LAYER_PROVENANCE]
    assert prov["ok"] is False
    assert any("evidence_provenance_inferred" in b for b in prov["blockers"])
    assert any("authenticated recorder" in b for b in prov["blockers"])


def test_composed_evidence_preserves_provenance_age_category_and_sha():
    base = 1_000_000.0
    now = base + 26 * 35 + 30
    by_slug = {SLUG: _flowing(base), SIB: _flowing(base, slug=SIB)}
    ev = xfer.compose_evidence(SLUG, by_slug, now, model_sha="abc123")
    assert ev["slug"] == SLUG
    assert ev["evidence_provenance"] == xfer.PROVENANCE_INFERRED
    assert "/state/pmus-sports" in ev["evidence_source"]
    assert ev["action_model_sha"] == "abc123"
    assert ev["evidence_t"] is not None and ev["evidence_t"] <= now
    assert ev["spring_category"] == "atp"
    assert set(ev["spring_category_evidence"]) >= {
        "completions_60m", "distinct_markets_60m", "markets", "bar_met"}
    assert ev["probe_evidence"]["status"] in (
        "READY_FOR_REVIEW", "INSUFFICIENT_EVIDENCE")


def test_missing_slug_fails_closed_with_named_blocker():
    r = xfer.spring_readiness({}, "aec-atp-nope", now=1_000_000.0)
    assert r["SPRING_READY_FOR_REVIEW"] is False
    assert any("no_rows_for_slug" in b for b in r["blockers"])


def test_every_layer_is_reported_even_when_blocked():
    base = 1_000_000.0
    r = xfer.spring_readiness({SLUG: _flowing(base)}, SLUG,
                              now=base + 26 * 35 + 30)
    assert set(r["layers"]) == {
        xfer.LAYER_REPLICATION, xfer.LAYER_ECONOMICS,
        xfer.LAYER_PROVENANCE, xfer.LAYER_OPERATIONAL}
    assert r["required_mount"] == "/state/pmus-spring"


# ---- item 4: durable mount is unambiguous in repo state ----

def test_durable_spring_mount_is_declared_and_documented():
    root = Path(xfer.__file__).resolve().parents[1]
    docker = (root / "deploy/pmus_spring_l5/Dockerfile").read_text()
    assert "PMUS_Q1_STATE_DIR=/state/pmus-spring" in docker
    manifest = (root / "deploy/PROBE_MANIFESTS.md").read_text()
    assert "/state/pmus-spring" in manifest
    # the recorder's own volume must never be reused for the probe
    assert "/state/pmus-ws" in manifest
    assert xfer.SPRING_STATE_DIR == "/state/pmus-spring"


# ---- shared canonical definitions, no drift ----

def test_category_bar_has_one_canonical_definition():
    assert enriched.slug_category is replay.slug_category
    assert enriched.category_spring_evidence is replay.category_spring_evidence
    assert enriched.CATEGORY_BAR_COMPLETIONS == \
        replay.CATEGORY_BAR_COMPLETIONS == 2
    assert enriched.CATEGORY_BAR_DISTINCT_MARKETS == \
        replay.CATEGORY_BAR_DISTINCT_MARKETS == 2


def test_transfer_module_cannot_arm_or_order():
    src = inspect.getsource(xfer)
    for banned in ("urllib", "websockets", "requests", "Transport",
                   "/v1/orders", "X-PM-", "run_session", "submit",
                   "os.environ["):
        assert banned not in src, banned
    assert '"order_entry": False' in src


def test_one_shot_stays_out_of_the_keyless_collector_image():
    """This module imports the spring entry guard, which transitively
    imports the micro/q1 executors. That chain must never enter the
    keyless collector image, so the one-shot is an OPERATOR tool run
    off-service against a copy of the tape — deliberately, not by
    omission."""
    root = Path(xfer.__file__).resolve().parents[1]
    collector = (root / "deploy/pmus_sports_collector/Dockerfile").read_text()
    for banned in ("pmus_spring_evidence_transfer", "pmus_spring_l5_session",
                   "pmus_spring_l5_probe_entry", "pmus_micro_executor",
                   "pmus_q1_executor"):
        assert banned not in collector, banned
    # and the decision layer that DOES ship in that image stays clean
    assert "pmus_sports_l5_decision.py" in collector


def test_off_distribution_is_carried_from_the_decision_layer():
    """Unknown must fail closed, but a genuine on-distribution verdict
    has to reach the guard or the replication layer could never pass."""
    assert xfer.off_distribution_of(None) is None
    assert xfer.off_distribution_of(
        {"next_action": {"decision": "quote"}}) is False
    assert xfer.off_distribution_of(
        {"next_action": {"decision": "no_action",
                         "reason": "v2_off_distribution"}}) is True
    base = 1_000_000.0
    by_slug = {SLUG: _flowing(base), SIB: _flowing(base, slug=SIB)}
    ev = xfer.compose_evidence(SLUG, by_slug, base + 26 * 35 + 30,
                               off_distribution=False)
    assert ev["off_distribution"] is False


# ---- 5541176027: deploy currency, provenance bridge, probe entry ----

def test_spring_image_copies_every_module_it_imports_at_load_time():
    """The probe service has never had a successful build. A first build
    must not produce an image that imports cleanly in CI and then dies on
    Railway, so the COPY list is checked against the load-time import
    graph of the entry module. Deferred imports inside functions are
    excluded deliberately (the probe loads the prebuilt model artifact
    and never rebuilds it)."""
    import ast
    import re
    root = Path(xfer.__file__).resolve().parents[1]
    docker = (root / "deploy/pmus_spring_l5/Dockerfile").read_text()
    copied = set(re.findall(r"^COPY (src/[^\s]+)", docker, re.M))
    assert "RUN touch src/__init__.py" in docker      # package marker

    def top_level_src_imports(path: Path):
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.ImportFrom) and node.module \
                    and node.module.startswith("src"):
                yield node.module
                for a in node.names:
                    yield f"{node.module}.{a.name}"
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.startswith("src"):
                        yield a.name

    seen, stack, missing = set(), ["src/pmus_spring_l5_probe_entry.py"], []
    while stack:
        rel = stack.pop()
        if rel in seen:
            continue
        seen.add(rel)
        if not (root / rel).exists():
            continue
        for mod in top_level_src_imports(root / rel):
            for cand in (mod.replace(".", "/") + ".py",
                         mod.replace(".", "/") + "/__init__.py"):
                if (root / cand).exists():
                    if cand not in seen:
                        stack.append(cand)
                    if cand not in copied and cand != "src/__init__.py":
                        missing.append(f"{cand} (imported by {rel})")
                    break
    assert not missing, f"spring image would crash on import: {missing}"


def test_probe_entry_action_is_planned_from_flat_inventory():
    """A first bounded probe holds no position. The replayed inventory is
    a per-window artifact, so a market whose replay hit the residual cap
    must still be able to present a probe entry."""
    base = 1_000_000.0
    rows = _flowing(base, bid="0.7000", ask="0.7200")
    r = l5d.decide_market(SLUG, rows, base + 26 * 35, _model())
    assert abs(r["inventory_final"]["residual"]) == l5d.MAX_RESIDUAL_UNITS
    assert r["next_action"]["decision"] == "no_action"     # continuation
    probe = r["probe_entry_action"]
    assert probe["decision"] == "quote"                    # entry from flat
    assert probe["relation"] == "open" and probe["side"] == "NO"
    assert "flat" in probe["from_inventory"]


def test_pinned_slug_enters_authenticated_coverage_bounded(monkeypatch):
    """Provenance bridge: the operator names a corrected sports slug and
    the recorder starts OBSERVING it. Coverage is added; no gate moves."""
    import time as _t
    from tests.test_pmus_spring_evidence_pursuit import (
        _busy_state, _targets)
    now = _t.time()
    state = _busy_state(30, now)
    state["aec-atp-andned-johnik-2026-09-04"] = dict(state["busy-00"])
    monkeypatch.setenv(enriched.PIN_SLUGS_ENV,
                       "aec-atp-andned-johnik-2026-09-04")
    monkeypatch.setenv(enriched.PURSUIT_SEATS_ENV, "0")
    p = enriched.plan(_targets(state), state, 12, 0, {}, now=now)
    assert len(p["subscribe"]) == 12                        # cap unchanged
    pins = p["diagnostics"]["spring_pinned"]
    assert [x["slug"] for x in pins] == [
        "aec-atp-andned-johnik-2026-09-04"]
    if pins[0]["reason"] == "pinned_for_authenticated_provenance":
        assert "aec-atp-andned-johnik-2026-09-04" in p["subscribe"]
        assert "evicted_warm_seat" in pins[0]


def test_pinning_is_bounded_and_never_widens_the_cap(monkeypatch):
    import time as _t
    from tests.test_pmus_spring_evidence_pursuit import (
        _busy_state, _targets)
    now = _t.time()
    state = _busy_state(30, now)
    extra = [f"pin-{i}" for i in range(9)]
    for s in extra:
        state[s] = dict(state["busy-00"])
    monkeypatch.setenv(enriched.PIN_SLUGS_ENV, ",".join(extra))
    monkeypatch.setenv(enriched.PURSUIT_SEATS_ENV, "0")
    p = enriched.plan(_targets(state), state, 12, 0, {}, now=now)
    assert len(p["subscribe"]) == 12
    assert len(p["diagnostics"]["spring_pinned"]) <= enriched.PIN_SLUGS_MAX


def test_hardcoded_yes_cannot_reenter_either_call_site():
    """Directive item 8: the deleted bug derived the side from the sign
    of the residual for open/extend. Neither call site may reconstruct
    it — both must route through the shared planner."""
    src = inspect.getsource(l5d)
    assert 'rel in ("open", "extend") and inv.residual >= 0' not in src
    assert src.count("plan_action(") >= 3        # def + both call sites
    for fn in (l5d.decide_market,):
        body = inspect.getsource(fn)
        assert "side_yes = (" not in body


def test_dual_key_is_an_arm_time_step_not_a_readiness_blocker():
    """Readiness means 'an owner may now review and decide'. The dual key
    is what the owner supplies AFTER deciding, so counting it as a
    readiness blocker would make SPRING_READY_FOR_REVIEW unreachable in
    the DISARMED state the review must happen in. It is reported
    separately and still gates actual arming in the session/entry guard."""
    base = 1_000_000.0
    r = xfer.spring_readiness({SLUG: _flowing(base)}, SLUG,
                              now=base + 26 * 35 + 30,
                              durable_reason=lambda: None)
    assert any("isolated_dual_key" in a for a in r["arming_prerequisites"])
    assert not any("dual_key" in b for b in r["blockers"])
    # the session's own arming predicate is untouched
    assert xfer.session.armed() is False


# ---- provenance: the recorder path is the ONLY authenticated one ----

def _recorder_status(now, slug=SLUG, bid="0.48", trades=26,
                     completions=3, markets=2, bar_met=True):
    """The authenticated recorder's status shape, as the guard reads it."""
    return {
        "kind": "PMUS_WS_STATUS", "t": now,
        "spring_watch": [{
            "slug": slug, "bid": bid, "trades_1h": trades,
            "two_sided_trades": True, "off_distribution": False,
            "spring_pair_target": "0.98", "spring_matched_slots": 2,
            "spring_repeated_completion": True,
            "spring_slot_counts_theoretical": {"SPRING_MATCHED": 2},
            "spring_evaluable_slots": 3, "spring_matched_fraction": 0.67,
            "spring_pair_costs": ["0.9800", "0.9800"],
            "spring_fee_safe_completions": 2,
            "spring_evidence_missing": [],
            "spring_category": "atp",
            "spring_category_evidence": {
                "completions_60m": completions,
                "distinct_markets_60m": markets,
                "markets": [slug, SIB][:max(1, markets)],
                "bar_met": bar_met},
            "spring_category_bar_met": bar_met,
            "l5_shadow": {"off_distribution": False},
        }],
    }


def test_sports_tape_can_never_claim_authenticated_provenance():
    """The keyless collector infers direction, so no amount of market
    evidence may flip the provenance gate on that path."""
    now = 1_788_000_000.0
    by = {SLUG: _flowing(now - 900)}
    ev = xfer.compose_evidence(SLUG, by, now, off_distribution=False)
    assert ev["evidence_provenance"] == xfer.PROVENANCE_INFERRED
    assert ev["evidence_source"] == xfer.SOURCE_SPORTS
    out = xfer.spring_readiness(by, SLUG, now=now, off_distribution=False,
                                durable_reason=lambda: None)
    assert out["SPRING_READY_FOR_REVIEW"] is False
    assert any("authenticated_source_required" in b for b in out["blockers"])
    assert out["layers"][xfer.LAYER_PROVENANCE]["ok"] is False


def test_recorder_status_satisfies_the_provenance_gate():
    now = 1_788_000_000.0
    ev = xfer.compose_evidence_from_recorder(_recorder_status(now), SLUG, now)
    assert ev["evidence_provenance"] == xfer.PROVENANCE_AUTHENTICATED
    assert ev["evidence_source"] == xfer.SOURCE_RECORDER
    out = xfer.spring_readiness({}, SLUG, now=now, evidence=ev,
                                durable_reason=lambda: None)
    assert out["layers"][xfer.LAYER_PROVENANCE]["ok"] is True, out["blockers"]
    assert out["SPRING_READY_FOR_REVIEW"] is True, out["blockers"]


def test_recorder_path_still_enforces_every_other_gate():
    """Authenticated provenance is not a bypass: band, flow, category bar
    and off-distribution still fail closed on the recorder payload."""
    now = 1_788_000_000.0
    for kw, expect in (({"bid": "0.83"}, "outside_band"),
                       ({"trades": 2}, "thin_flow"),
                       ({"completions": 1, "markets": 1, "bar_met": False},
                        "category")):
        status = _recorder_status(now, **kw)
        ev = xfer.compose_evidence_from_recorder(status, SLUG, now)
        assert ev["evidence_provenance"] == xfer.PROVENANCE_AUTHENTICATED
        out = xfer.spring_readiness({}, SLUG, now=now, evidence=ev,
                                    durable_reason=lambda: None)
        assert out["SPRING_READY_FOR_REVIEW"] is False, (kw, out["blockers"])
        assert any(expect in b for b in out["blockers"]), (kw, out["blockers"])
    # a stale recorder payload is refused on freshness alone
    ev = xfer.compose_evidence_from_recorder(
        _recorder_status(now - 86_400), SLUG, now)
    out = xfer.spring_readiness({}, SLUG, now=now, evidence=ev,
                                durable_reason=lambda: None)
    assert out["SPRING_READY_FOR_REVIEW"] is False
    assert any("stale" in b for b in out["blockers"]), out["blockers"]


def test_recorder_status_absent_slug_fails_closed():
    now = 1_788_000_000.0
    assert xfer.compose_evidence_from_recorder(
        _recorder_status(now), "aec-atp-nobody-here-2026-09-04", now) is None
    out = xfer.spring_readiness({}, "aec-atp-nobody-here-2026-09-04",
                                now=now, evidence=None,
                                durable_reason=lambda: None)
    assert out["SPRING_READY_FOR_REVIEW"] is False


def test_load_recorder_status_reads_json_and_jsonl_tail(tmp_path):
    doc = tmp_path / "status.json"
    doc.write_text('{"kind": "PMUS_WS_STATUS", "t": 1}', encoding="utf-8")
    assert xfer.load_recorder_status(doc)["t"] == 1
    stream = tmp_path / "status.jsonl"
    stream.write_text('{"t": 1}\nnot json\n{"t": 2}\n', encoding="utf-8")
    assert xfer.load_recorder_status(stream)["t"] == 2
