# Gemini L5 probe — DISARMED config (OWNER 5554263833 item 6)

Prepared only so nothing has to be designed later. **Not deployed, not
armed, no Railway service, no AWS unit.** The shadow/replay evidence
(`docs/L5_GEMINI_TRANSFER_PREP.md`, verdict TRANSFER-UNPROVEN) does not
support a live probe; this file stays a config sketch until it does and
the owner authorizes a Gemini-only money cap.

| control | value |
|---|---|
| arming | dual key, owner-held: `GEMINI_L5_LIVE=YES` + `GEMINI_L5_SESSION_ID` + `GEMINI_L5_ARM` (same pattern as PMUS); absent = DISARMED |
| policy | v2 modal + logged seeded draw, same shell as PMUS (`gemini_l5_shadow_v2` semantics) |
| size | q=1 contract; money cap = OWNER DECISION (a unit costs $0.20–0.80; the PMUS $0.05 cap is not raised or reused) |
| concurrency | one window at a time; allocator GEMINI cap 0 until authorized |
| orders | maker-only, post-only; complement ≤0.98 pair and ≥1c after the ceil-cent maker fee; no taker |
| rest / cancel | ≤30s, cancel on touch move (measured binding constraint — any change is an owner decision) |
| residual | cap 3 units, HOLD to settlement; realized only from venue settlement rows |
| deadman | cancel-all on exit / stale book / fetch error; restart policy NEVER |
| daily loss | −$1.00 venue stop; shares the −$1.00 TOTAL stop with PMUS |
| health gate | closes on HTTP 5xx, stale book >10s, or no prints in the window |
| secrets | Gemini keys never printed, copied, committed or shared with PMUS |
