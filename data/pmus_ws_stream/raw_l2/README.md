# Raw full-L2 slice, exported read-only from the running recorder's volume

Source: `/state/pmus-ws/raw/pmus_ws_20260930_13.jsonl` on Railway volume
`18bd5702-…` mounted at `/state/pmus-ws` by service `pmus-ws-evidence-recorder`
(`71b6ea99-…`). Exported via Railway's supported read-only container-file API
(`listContainerFiles` / `readContainerFile`, the API equivalents of
`railway service files list|download`).

**The recorder was never restarted, redeployed, or modified.** `_level()`
untouched, telemetry caps untouched, no variable, branch or config change.

Longest contiguous byte run recovered: offsets 61,400,000 → 68,500,000
(7.1 MB), stitched with partial-line discipline at every chunk boundary.
2,663 complete JSON lines, 0 unparsable.

Verified contents: raw verbatim WS messages (`{kind, t, msg}`), full ladders
(bids median 27 levels / max 37, offers median 20 / max 36), 1,763 book
messages, 900 trade messages each carrying a venue-unique `id`.
