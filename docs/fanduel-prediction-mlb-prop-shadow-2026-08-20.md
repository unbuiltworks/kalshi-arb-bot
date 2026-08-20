# FanDuel vs prediction MLB prop shadow validation — 2026-08-20

Read-only regression gate for exact FanDuel half-point player-prop normalization and K/G/P counterpart joins. Tests canonical Over/Under side mapping, same-key identity, opposite-side economics, and fail-closed game-start mismatch. No alerts or order entry.

The regression gate passed 4/4. This source-trigger checkpoint launches the one-game live shadow join against the nearest future MLB event using only Hits, H+R+RBI, Total Bases, and Pitcher Strikeouts markets already confirmed available from FanDuel. All positives remain non-executable pending complete lifecycle certification.
