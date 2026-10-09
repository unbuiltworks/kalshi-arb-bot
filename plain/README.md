# Uncompressed views

For a reader that cannot decompress `.csv.gz` — which includes most AI
repository browsers. Same data, same columns, no gzip.

| file | rows | size | what |
|---|---|---|---|
| `l5_window_summary.csv` | 17,046 | 5.1 MB | **every** window in the corpus, one row each |
| `l5_fills_sample.csv` | 16,964 | 8.3 MB | individual fills — every 46th window, 370 whole windows |

`l5_window_summary.csv` is **complete**: all 17,046 windows, nothing
sampled away. Any population-level question about windows can be answered
from it exactly.

`l5_fills_sample.csv` is a **sample of whole windows**, never truncated
mid-window, so reading it sequentially is still meaningful. It spans the
full corpus period rather than a prefix.

The complete 760,817-fill table is **not** duplicated here: uncompressed
it is roughly 240 MB and no repository reader will ingest it. It lives in
`../dataset/fills/` as 14 gzipped parts, for file upload or local use. If
a conclusion needs the full table rather than this sample, say so — the
parts can be uploaded directly, and `../scripts/50_test_hypothesis.py`
runs against all 760,817 fills.

Column meanings: `../DATA_DICTIONARY.md`.
