"""Fast continuous launcher for the read-only all-category overlap audit.

The base auditor intentionally supports a very broad inventory. For a continuous
5-minute production loop we care first about liquid/actionable contracts, so this
launcher bounds the Polymarket inventory to the top 20k active markets by reported
volume and refuses to run expensive fuzzy scoring unless a candidate shares at
least two rare identity tokens with a Kalshi contract.

Candidates remain discovery-only and are never executable.
"""

from __future__ import annotations

from collections import Counter

from src import universe_overlap_audit as base

# Continuous tier: broad enough to cover a large live universe without pinning a
# Railway CPU core indefinitely. A separate one-shot exhaustive audit can still use
# the base module's larger safety cap when needed.
base.POLY_MAX_RECORDS = 20_000
base.MAX_SCORE_POOL = 60
base.INDEX_MAX_POSTING = 1_500


def _fast_candidate_pool(p, postings, dfs):
    compatible = base._compatible_categories(p.category)
    ranked: list[tuple[int, str]] = []
    for token in p.tokens:
        df = sum(dfs.get(cat, Counter()).get(token, 0) for cat in compatible)
        if 0 < df <= base.INDEX_MAX_POSTING:
            ranked.append((df, token))
    ranked.sort()
    chosen = ranked[:10]
    if not chosen:
        return [], 0

    shared_counts: Counter[str] = Counter()
    by_ticker = {}
    for _, token in chosen:
        for cat in compatible:
            for k in postings.get(cat, {}).get(token, []):
                if not base._compatible(p.category, k.category):
                    continue
                by_ticker[k.ticker] = k
                shared_counts[k.ticker] += 1

    # The scorer itself requires two shared tokens. Filter here so we never spend
    # SequenceMatcher CPU on candidates that cannot pass that first gate anyway.
    eligible = [k for ticker, k in by_ticker.items() if shared_counts[ticker] >= 2]
    eligible.sort(
        key=lambda k: (shared_counts[k.ticker], k.volume),
        reverse=True,
    )
    return eligible[: base.MAX_SCORE_POOL], len(eligible)


base._candidate_pool = _fast_candidate_pool


def main() -> None:
    # Keep httpx's per-request lines out of Railway logs; audit summaries/candidates
    # remain at INFO and are what we actually need overnight.
    import logging

    logging.getLogger("httpx").setLevel(logging.WARNING)
    base.main()


if __name__ == "__main__":
    main()
