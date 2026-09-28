# Raw Railway `PMUS_TAPE_ROW` log slices — preservation only

Railway log retention destroys these rows. They are captured here verbatim so the
evidence survives independently of retention and of any ephemeral session disk.

- Source: Railway project `50fb7357-e2bc-4d04-a49e-71eb5d73b0d1`, environment
  `f3642d27-26dc-4e21-9170-bc76290c076f`, service `1f0c6aaf-657c-4c82-bb0d-592250c089b4`
  (`pmus-btc15m-tape`, read-only collector, no PMUS credentials).
- One gzipped JSONL per capture hour; each line is `{"timestamp", "message"}` exactly
  as Railway returned it. No transcription, no parsing, no filtering beyond
  `PMUS_TAPE_ROW`.

These files are NOT parsed into `data/pmus_btc15m_tape/pmus_btc15m_tape.jsonl`, are NOT
admitted to `USABLE`, and do NOT participate in any ledger, distribution or checkpoint
number. The frozen n=25 CHECKPOINT25 sample is untouched. Parsing them with
`scripts/scrape_pmus_tape.py` and running the completeness rule is a separate,
separately authorized step.
