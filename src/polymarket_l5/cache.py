"""JSONL caches for raw API responses and normalized records.

Raw cache: one JSONL line per HTTP response, carrying the request that produced
it, so any downstream number can be traced back to a specific fetch. Analysis
runs entirely off these files -- re-running `build`/`analyze` never touches the
network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List

from .timeutil import now_iso


class RawCache:
    """Append-only JSONL sink for raw HTTP responses."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, tag: str) -> Path:
        return self.root / f"{tag}.jsonl"

    def reset(self, tag: str) -> None:
        path = self.path_for(tag)
        if path.exists():
            path.unlink()

    def append(
        self,
        tag: str,
        *,
        url: str,
        params: Dict[str, Any],
        status: int,
        payload: Any,
        error: str = "",
    ) -> None:
        record = {
            "fetched_at": now_iso(),
            "tag": tag,
            "url": url,
            "params": params,
            "status": status,
            "error": error,
            "payload": payload,
        }
        with self.path_for(tag).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")

    def records(self, tag: str) -> Iterator[Dict[str, Any]]:
        path = self.path_for(tag)
        if not path.exists():
            return iter(())
        return _iter_jsonl(path)

    def payload_batches(self, tag: str) -> List[List[Dict[str, Any]]]:
        """Return one list-of-items per successful cached response.

        Batch boundaries are preserved because dedup needs to distinguish
        "same fill returned twice by two overlapping pages" from "two genuinely
        identical fills inside one response".
        """
        batches: List[List[Dict[str, Any]]] = []
        for record in self.records(tag):
            if record.get("status") != 200:
                continue
            batches.append(coerce_items(record.get("payload")))
        return batches


def coerce_items(payload: Any) -> List[Dict[str, Any]]:
    """Normalize the several list shapes Polymarket endpoints return."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "results", "history", "activity", "positions", "trades"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [payload]
    return []


def _iter_jsonl(path: Path) -> Iterator[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
            count += 1
    return count


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    return list(_iter_jsonl(path))
