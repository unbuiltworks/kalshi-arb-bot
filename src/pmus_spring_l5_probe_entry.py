"""Fail-closed deployment admission for the dedicated PMUS spring L5 probe.

This module contains NO strategy or order logic. It is a deployment safety
wrapper around ``src.pmus_spring_l5_session`` and delegates to that canonical
session only after two extra conditions are source-present:

1. fresh strict-replay ``READY_FOR_REVIEW`` evidence for the exact armed slug;
2. the spring state directory is a real writable mount point, so ledger and
   cumulative-notional state survive container replacement.

DISARMED remains network-free. Missing, stale, malformed, or weak evidence
fails closed. Evidence remains SHADOW/THEORETICAL: passing this guard only
permits a bounded live probe; it is never called a fill or realized profit.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Mapping, Optional

from src import pmus_spring_l5_session as base

STRICT_EVIDENCE_ENV = "PMUS_SPRING_STRICT_EVIDENCE_JSON"
STRICT_EVIDENCE_MAX_AGE_ENV = "PMUS_SPRING_STRICT_EVIDENCE_MAX_AGE_SECONDS"
DEFAULT_EVIDENCE_MAX_AGE_S = 120.0

# A status document the probe reads ITSELF at admission time, so no human
# stands inside a 120s freshness window. Set it and the pasted-JSON env is
# not consulted at all.
EVIDENCE_STATUS_PATH_ENV = "PMUS_SPRING_EVIDENCE_STATUS_PATH"
# Same document fetched from the recorder's read-only HTTP handoff over the
# private network (the two services do not share a volume). Takes
# precedence over the path; both take precedence over the pasted env.
EVIDENCE_STATUS_URL_ENV = "PMUS_SPRING_EVIDENCE_STATUS_URL"
STATUS_FETCH_TIMEOUT_S = 5.0

# Only trade direction OBSERVED on the authenticated stream is admissible.
PROVENANCE_AUTHENTICATED = "authenticated"

# The venue's authoritative open state on /v1/market/slug/{slug}. A
# resolved market reports MARKET_STATUS_RESOLVED with closed=True.
MARKET_STATUS_OPEN = "MARKET_STATUS_OPEN"

# Minimum touch depth to rest a q=1 behavioural unit against. The venue's
# minimumTradeQty is 0.01, so one unit of depth is already generous; the
# point is that ZERO depth is not a book.
MIN_TOUCH_DEPTH = D("1")


def _epoch(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        text = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError):
        return None


def market_open_reason(evidence: Mapping[str, Any],
                       now: Optional[float] = None) -> Optional[str]:
    """Why this market is not safe to OPEN into. None means it is.

    PMUS serves no `settlement_ts` for live sports, so none is invented
    and none is silently waived (OWNER 5551236878 item 4). The venue's
    own authoritative fields on `/v1/market/slug/{slug}` are used
    instead, verified live 2026-09-05:

        active     True        archived  False
        closed     False       status    MARKET_STATUS_OPEN
        endDate    2026-09-16T09:30:00Z

    and a resolved market really does report `closed=True`,
    `status=MARKET_STATUS_RESOLVED`, `ep3Status=EXPIRED`. Every one of
    these is REQUIRED: a payload that omits one fails closed naming the
    field, because "we could not check" must never read as "safe".
    """
    state = evidence.get("market_state")
    if not isinstance(state, Mapping):
        return ("strict_evidence_market_state_missing: needs "
                "/v1/market/slug/{slug} active,archived,closed,status,endDate")
    for field, want in (("active", True), ("archived", False),
                        ("closed", False)):
        got = state.get(field)
        if got is None:
            return f"strict_evidence_market_{field}_missing"
        if bool(got) is not want:
            return f"strict_evidence_market_{field}:{got!r}"
    status = str(state.get("status") or "")
    if not status:
        return "strict_evidence_market_status_missing"
    if status != MARKET_STATUS_OPEN:
        return f"strict_evidence_market_not_open:{status}"
    end = _epoch(state.get("endDate"))
    if end is None:
        return ("strict_evidence_market_end_missing: needs endDate from "
                "/v1/market/slug/{slug}")
    remaining = end - (time.time() if now is None else now)
    if remaining < base.STOP_BEFORE_SETTLEMENT_S:
        return f"strict_evidence_too_close_to_settlement:{round(remaining, 1)}s"
    return None


def strict_evidence_reason(slug: str, evidence: Optional[Mapping[str, Any]],
                           now: Optional[float] = None,
                           max_age_s: Optional[float] = None,
                           require_provenance: bool = True) -> Optional[str]:
    """Return why strict recorder evidence is insufficient; None means pass.

    `require_provenance` is True for ADMISSION, which must refuse an
    inferred payload outright. The readiness report passes False because
    it reports provenance in its own EVIDENCE_PROVENANCE layer, and
    folding it into the replication layer would make one red gate look
    like two unrelated failures. Nothing can reach a live probe through
    the False path -- only the report uses it.
    """
    if not isinstance(evidence, Mapping):
        return "strict_evidence_missing"
    if str(evidence.get("slug") or "") != slug:
        return "strict_evidence_slug_mismatch"
    # OWNER DECISION 5551236878: strict READY_FOR_REVIEW and the
    # >=2 completions / >=2 distinct markets category bar are COMPLETION
    # evidence and must NOT gate a first OPEN. Same circular prerequisite
    # removed from `preflight_candidate` in d784c1c8 -- proof of past
    # completions cannot be required before the first entry, because
    # nothing completes if nothing may open. Both remain measured and
    # reported by `completion_evidence_reason`; the safety and provenance
    # intent of 5533476555 is untouched.
    if not isinstance(evidence.get("probe_evidence"), Mapping):
        return "probe_evidence_missing"        # payload shape, not a bar
    # LIQUIDITY -- a book we can actually rest an order against. The bar
    # is the book itself, never its trading history. ABSENT IS A BLOCKER:
    # `if present: check` would let a payload that simply omits the field
    # walk past a safety gate, which is the opposite of fail-closed.
    if evidence.get("both_sides_served") is not True:
        return (f"strict_evidence_book_not_two_sided:"
                f"{evidence.get('both_sides_served')!r}")
    depth = evidence.get("bid_touch_qty")
    if depth is None:
        return "strict_evidence_touch_depth_missing"
    try:
        if D(str(depth)) < MIN_TOUCH_DEPTH:
            return f"strict_evidence_no_q1_touch_depth:{depth}"
    except Exception:
        return f"strict_evidence_touch_depth_unreadable:{depth!r}"
    # CADENCE -- an observation window with a hole in it is not a window.
    gaps = evidence.get("collector_gaps")
    if gaps is None:
        return "strict_evidence_cadence_missing"
    try:
        if int(gaps) > 0:
            return f"strict_evidence_cadence_gap:{gaps}"
    except (TypeError, ValueError):
        return f"strict_evidence_cadence_unreadable:{gaps!r}"
    # MARKET-OPEN SAFETY, from the venue's own authoritative fields.
    # PMUS serves no "settlement_ts" for live sports, so none is invented
    # and none is waived: `/v1/market/slug/{slug}` carries `active`,
    # `archived`, `closed`, `status` and `endDate`, and those are the
    # facts used. A payload without them fails closed and names the field.
    market_block = market_open_reason(evidence, now)
    if market_block:
        return market_block
    # THE MODEL -- the canonical L5 probe-entry recommendation, from
    # FLAT. Distribution alone is not enough: a state v2 has visited but
    # would not open in is not an entry.
    entry_action = evidence.get("probe_entry_action")
    if not isinstance(entry_action, Mapping):
        return "strict_evidence_probe_entry_action_missing"
    if entry_action.get("decision") != "quote":
        return (f"strict_evidence_model_no_open:"
                f"{entry_action.get('reason') or 'no_action'}")
    if entry_action.get("relation") != "open":
        return (f"strict_evidence_first_action_not_an_open:"
                f"{entry_action.get('relation')}")
    try:
        pair_target = D(str(evidence.get("spring_pair_target")))
    except Exception:
        return "spring_pair_target_missing"
    if pair_target > base.SPRING_PAIR_TARGET:
        return f"spring_pair_target_too_loose:{pair_target}"
    try:
        bid = D(str(evidence.get("bid")))
    except Exception:
        return "strict_evidence_bid_missing"
    if not (base.SPRING_BAND_LO <= bid <= base.SPRING_BAND_HI):
        return f"strict_evidence_outside_band:{bid}"
    try:
        trades = int(evidence.get("trades_1h"))
    except (TypeError, ValueError):
        return "strict_evidence_trades_missing"
    if trades < base.SPRING_MIN_TRADES_1H:
        return f"strict_evidence_thin_flow:{trades}"
    if evidence.get("two_sided_trades") is not True:
        return "strict_evidence_not_two_sided"
    if evidence.get("off_distribution") is not False:
        return "strict_evidence_off_distribution_or_unknown"
    # PROVENANCE, checked HERE and not only in the readiness report.
    # The readiness layers refuse inferred evidence, but this guard is
    # what actually admits a live probe -- and it accepted any payload
    # whose fields looked right, provenance unread. That was survivable
    # while a human pasted each payload. It is not survivable once a feed
    # composes payloads automatically, so the guard now reads provenance
    # itself. Missing or inferred fails CLOSED; only trade direction
    # OBSERVED on the authenticated stream qualifies.
    if require_provenance:
        prov = str(evidence.get("evidence_provenance") or "").strip().lower()
        if prov != PROVENANCE_AUTHENTICATED:
            return ("strict_evidence_provenance_not_authenticated:"
                    f"{prov or 'missing'}")
    ts = _epoch(evidence.get("evidence_t") or evidence.get("status_t")
                or evidence.get("t"))
    if ts is None:
        return "strict_evidence_timestamp_missing"
    now = time.time() if now is None else now
    max_age_s = (float(os.getenv(STRICT_EVIDENCE_MAX_AGE_ENV,
                                 str(DEFAULT_EVIDENCE_MAX_AGE_S)))
                 if max_age_s is None else max_age_s)
    age = now - ts
    if age < -5.0:
        return f"strict_evidence_from_future:{round(age, 3)}"
    if age > max_age_s:
        return f"strict_evidence_stale:{round(age, 3)}"
    return None


def evidence_from_status_path(slug: str, path: Optional[str] = None,
                             now: Optional[float] = None) -> tuple:
    """Compose strict evidence from a status document the probe reads.

    Returns ``(evidence, source)``. The manual path required a human to
    copy a payload into an env var and redeploy inside its own 120s
    freshness bound -- which the paste itself usually spent. Reading the
    status here removes the human from that window entirely; it does not
    relax the window, and every gate downstream is unchanged.

    Imported lazily: `pmus_spring_evidence_transfer` imports this module.
    """
    path = os.getenv(EVIDENCE_STATUS_PATH_ENV, "") if path is None else path
    if not str(path).strip():
        return None, "unset"
    status, source = load_status_document(path)
    if status is None:
        return None, source
    from src import pmus_spring_evidence_transfer as xfer
    now = time.time() if now is None else now
    ev = xfer.compose_evidence_from_recorder(status, slug, now)
    if ev is None:
        return None, f"slug_absent:{xfer.diagnose_missing_slug(status, slug)}"
    return ev, source


def load_status_document(path: Optional[str] = None) -> tuple:
    """Fetch the recorder's status document from a file path or from the
    recorder's read-only HTTP handoff. Returns ``(status, source)``;
    ``status`` is None with the fail-closed reason in ``source``.

    Shared by the one-shot guard and the continuous selector so both
    read the same bytes the same way.
    """
    if path is None:
        path = (os.getenv(EVIDENCE_STATUS_URL_ENV, "").strip()
                or os.getenv(EVIDENCE_STATUS_PATH_ENV, ""))
    path = str(path).strip()
    if not path:
        return None, "unset"
    try:
        from src import pmus_spring_evidence_transfer as xfer
    except ImportError as exc:      # image missing the composer
        return None, f"evidence_composer_unavailable:{exc.name}"
    is_url = path.startswith(("http://", "https://"))
    try:
        if is_url:
            import urllib.request
            with urllib.request.urlopen(          # noqa: S310 (scheme checked)
                    path, timeout=STATUS_FETCH_TIMEOUT_S) as resp:
                text = resp.read().decode("utf-8")
            status = xfer.parse_recorder_status(text, path)
        else:
            status = xfer.load_recorder_status(Path(path))
    except (OSError, ValueError) as exc:     # URLError/HTTPError are OSError
        return None, f"status_unreadable:{type(exc).__name__}"
    return status, f"{'status_url' if is_url else 'status_path'}:{path}"


def resolve_strict_evidence(slug: str,
                            now: Optional[float] = None) -> tuple:
    """The one place evidence enters the guard. A configured status path
    is authoritative and is never silently backfilled by the pasted env
    var -- a stale paste standing in for a broken feed is exactly the
    failure this replaces."""
    raw_path = (os.getenv(EVIDENCE_STATUS_URL_ENV, "").strip()
                or os.getenv(EVIDENCE_STATUS_PATH_ENV, "").strip())
    if raw_path:
        return evidence_from_status_path(slug, raw_path, now)
    ev = parse_strict_evidence()
    return ev, ("operator_pasted_json" if ev is not None
                else "operator_pasted_json_absent")


def completion_evidence_reason(evidence: Optional[Mapping[str, Any]]
                               ) -> Optional[str]:
    """Why this market lacks strict COMPLETION evidence; None means it
    has it. Reported, never used to admit or refuse an OPEN.

    Kept because the evidence is still worth knowing -- a market that has
    completed pairs recently is a better bet than one that has not -- but
    it now RANKS a candidate rather than gating one.
    """
    if not isinstance(evidence, Mapping):
        return "strict_evidence_missing"
    probe = evidence.get("probe_evidence")
    if not isinstance(probe, Mapping):
        return "probe_evidence_missing"
    if probe.get("status") != "READY_FOR_REVIEW":
        reasons = probe.get("reasons") or []
        return (f"probe_evidence_not_ready:"
                f"{','.join(str(x) for x in reasons[:4]) or 'not_ready'}")
    if evidence.get("spring_category_bar_met") is not True:
        return "spring_category_bar_not_met"
    cat = evidence.get("spring_category_evidence")
    if not isinstance(cat, Mapping):
        return "spring_category_evidence_missing"
    try:
        completions = int(cat.get("completions_60m"))
        distinct = int(cat.get("distinct_markets_60m"))
        required = max(2, int(probe.get("min_completions_required") or 2))
    except (TypeError, ValueError):
        return "spring_category_counts_missing"
    if completions < required:
        return (f"spring_category_completions_lt_required:"
                f"{completions}<{required}")
    if distinct < 2:
        return f"spring_category_distinct_markets_lt_2:{distinct}"
    return None


def parse_strict_evidence(raw: Optional[str] = None) -> Optional[dict]:
    raw = os.getenv(STRICT_EVIDENCE_ENV, "") if raw is None else raw
    if not str(raw).strip():
        return None
    try:
        obj = json.loads(str(raw))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def durable_state_reason(path: Optional[Path] = None,
                         mount_check=os.path.ismount) -> Optional[str]:
    """Require a writable mount point, not container-layer ephemeral state."""
    path = Path(path) if path is not None else base.q1.base.state_dir()
    try:
        if not path.exists() or not path.is_dir():
            return f"spring_state_dir_missing:{path}"
        if not mount_check(str(path)):
            return f"spring_state_dir_not_persistent_mount:{path}"
        probe = path / ".spring_durability_probe"
        with probe.open("a", encoding="utf-8") as f:
            f.write("probe\n")
            f.flush()
            os.fsync(f.fileno())
        probe.unlink(missing_ok=True)
    except OSError as exc:
        return f"spring_state_dir_not_writable:{type(exc).__name__}"
    return None


def evidence_from_status(status: Mapping[str, Any], slug: str) -> Optional[dict]:
    """Build the compact evidence payload used at arm time from recorder status."""
    for key in ("spring_watch", "eligible_markets", "top_markets"):
        for row in status.get(key) or []:
            if not isinstance(row, Mapping) or str(row.get("slug") or "") != slug:
                continue
            shadow = row.get("l5_shadow") or {}
            return {
                "slug": slug,
                "evidence_t": status.get("t"),
                "bid": row.get("bid"),
                "trades_1h": row.get("trades_1h"),
                "two_sided_trades": row.get("two_sided_trades"),
                "off_distribution": row.get("off_distribution",
                                                shadow.get("off_distribution")),
                "spring_pair_target": row.get("spring_pair_target"),
                "spring_matched_slots": row.get("spring_matched_slots"),
                "spring_repeated_completion": row.get(
                    "spring_repeated_completion"),
                "spring_category": row.get("spring_category"),
                "spring_category_evidence": row.get(
                    "spring_category_evidence"),
                "spring_category_bar_met": row.get(
                    "spring_category_bar_met"),
                # OPEN-relevant liquidity / cadence / market-state /
                # model fields (OWNER DECISION 5551236878). Absent stays
                # absent HERE and the guard fails closed on it there --
                # the composer never substitutes a default, because a
                # default is indistinguishable from a real measurement
                # once it is inside the payload.
                "both_sides_served": row.get("both_sides_served"),
                "bid_touch_qty": (row.get("bid_touch_qty")
                                  if row.get("bid_touch_qty") is not None
                                  else row.get("bid_qty")),
                "collector_gaps": row.get("collector_gaps"),
                "market_state": row.get("market_state"),
                "probe_entry_action": row.get("probe_entry_action"),
                "spring_slot_counts_theoretical": row.get(
                    "spring_slot_counts_theoretical"),
                "spring_evaluable_slots": row.get("spring_evaluable_slots"),
                "spring_matched_fraction": row.get("spring_matched_fraction"),
                "spring_pair_costs": row.get("spring_pair_costs") or [],
                "spring_fee_safe_completions": row.get(
                    "spring_fee_safe_completions"),
                "spring_evidence_missing": row.get("spring_evidence_missing") or [],
                "probe_evidence": row.get("probe_evidence"),
                "label": "SHADOW/THEORETICAL strict spring replay evidence",
            }
    return None


def run_session(t=None, clock=time.monotonic, sleep=time.sleep,
                wall=time.time) -> dict:
    """Guard the canonical session; no network is touched before guards pass."""
    slug = os.getenv(base.SPRING_MARKET_ENV, "").strip()
    if not base.armed():
        report = base.run_session(t=t, clock=clock, sleep=sleep, wall=wall)
        # DISARMED preview: report what the guard WOULD decide from the
        # configured evidence source, so the handoff can be verified from
        # the boot log before anyone holds the keys. Reads the recorder's
        # status only (file or private-network GET); the venue, the
        # credentials and the order path are never touched.
        if isinstance(report, dict) and (
                os.getenv(EVIDENCE_STATUS_URL_ENV, "").strip()
                or os.getenv(EVIDENCE_STATUS_PATH_ENV, "").strip()):
            now = wall()
            evidence, source = resolve_strict_evidence(slug, now=now)
            report["admission_preview"] = {
                "evidence_source": source,
                "evidence_age_s": evidence_age_s(evidence, now),
                "strict_evidence_reason": strict_evidence_reason(
                    slug, evidence, now=now),
                "completion_evidence_reason":
                    completion_evidence_reason(evidence),
                "label": "SHADOW/THEORETICAL; DISARMED; no venue network",
            }
        return report
    evidence, source = resolve_strict_evidence(slug, now=wall())
    reason = strict_evidence_reason(slug, evidence, now=wall())
    if reason:
        return {"session": "pmus-spring-l5-v1", "slug": slug,
                "status": "STRICT_GATE_NOT_CLEARED", "reason": reason,
                "evidence_source": source,
                "evidence_age_s": evidence_age_s(evidence, wall()),
                "realized_pnl": None,
                "labels": "SHADOW/THEORETICAL evidence prerequisite; no network"}
    reason = durable_state_reason()
    if reason:
        return {"session": "pmus-spring-l5-v1", "slug": slug,
                "status": "DURABLE_STATE_REQUIRED", "reason": reason,
                "realized_pnl": None,
                "labels": "live mutation blocked before network; durable ledger required"}
    report = base.run_session(t=t, clock=clock, sleep=sleep, wall=wall)
    if isinstance(report, dict):
        report.setdefault("evidence_source", source)
        report.setdefault("evidence_age_s",
                          evidence_age_s(evidence, wall()))
    return report


def evidence_age_s(evidence: Optional[Mapping[str, Any]],
                   now: float) -> Optional[float]:
    """Age of the evidence actually used, for the report. None when the
    payload carries no usable timestamp -- which the guard already
    refuses; this never invents one."""
    if not isinstance(evidence, Mapping):
        return None
    ts = _epoch(evidence.get("evidence_t") or evidence.get("status_t")
                or evidence.get("t"))
    return None if ts is None else round(now - ts, 3)


def main() -> int:
    report = run_session()
    print(json.dumps(report, indent=1, default=str), flush=True)
    if durable_state_reason() is None:
        base.q1.ledger_append({"kind": "session_report", "spring": True,
                               **report})
    safe = {"SPRING_MATCHED", "SPRING_RESIDUAL_HOLD", "SPRING_NO_FILL",
            "SPRING_SKIP_NOT_QUALIFYING", "SPRING_SAFE_REFUSAL",
            "DISARMED", "GATE_NOT_CLEARED", "NO_CREDENTIALS",
            "PREFLIGHT_FAILED", "DAILY_LOSS_STOP",
            "STRICT_GATE_NOT_CLEARED", "DURABLE_STATE_REQUIRED"}
    return 0 if report.get("status") in safe else 1


if __name__ == "__main__":
    raise SystemExit(main())
