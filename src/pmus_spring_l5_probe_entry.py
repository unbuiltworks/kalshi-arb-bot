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

# Only trade direction OBSERVED on the authenticated stream is admissible.
PROVENANCE_AUTHENTICATED = "authenticated"


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
    probe = evidence.get("probe_evidence")
    if not isinstance(probe, Mapping):
        return "probe_evidence_missing"
    if probe.get("status") != "READY_FOR_REVIEW":
        reasons = probe.get("reasons") or []
        suffix = ",".join(str(x) for x in reasons[:4]) or "not_ready"
        return f"probe_evidence_not_ready:{suffix}"
    # Category evidence bar (OWNER DECISION 5533476555): >=2 strict
    # completions within the rolling hour from >=2 DISTINCT same-category
    # markets. An isolated one-off market can never clear this alone.
    if evidence.get("spring_category_bar_met") is not True:
        return "spring_category_bar_not_met"
    cat = evidence.get("spring_category_evidence")
    if not isinstance(cat, Mapping):
        return "spring_category_evidence_missing"
    try:
        completions = int(cat.get("completions_60m"))
        distinct = int(cat.get("distinct_markets_60m"))
    except (TypeError, ValueError):
        return "spring_category_counts_missing"
    try:
        required = max(2, int(probe.get("min_completions_required") or 2))
    except (TypeError, ValueError):
        return "probe_min_completions_invalid"
    if completions < required:
        return (f"spring_category_completions_lt_required:"
                f"{completions}<{required}")
    if distinct < 2:
        return f"spring_category_distinct_markets_lt_2:{distinct}"
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
    try:
        from src import pmus_spring_evidence_transfer as xfer
    except ImportError as exc:      # image missing the composer
        return None, f"evidence_composer_unavailable:{exc.name}"
    try:
        status = xfer.load_recorder_status(Path(str(path)))
    except (OSError, ValueError) as exc:
        return None, f"status_unreadable:{type(exc).__name__}"
    now = time.time() if now is None else now
    ev = xfer.compose_evidence_from_recorder(status, slug, now)
    if ev is None:
        return None, f"slug_absent:{xfer.diagnose_missing_slug(status, slug)}"
    return ev, f"status_path:{path}"


def resolve_strict_evidence(slug: str,
                            now: Optional[float] = None) -> tuple:
    """The one place evidence enters the guard. A configured status path
    is authoritative and is never silently backfilled by the pasted env
    var -- a stale paste standing in for a broken feed is exactly the
    failure this replaces."""
    raw_path = os.getenv(EVIDENCE_STATUS_PATH_ENV, "").strip()
    if raw_path:
        return evidence_from_status_path(slug, raw_path, now)
    ev = parse_strict_evidence()
    return ev, ("operator_pasted_json" if ev is not None
                else "operator_pasted_json_absent")


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
    if not base.armed():
        return base.run_session(t=t, clock=clock, sleep=sleep, wall=wall)
    slug = os.getenv(base.SPRING_MARKET_ENV, "").strip()
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
