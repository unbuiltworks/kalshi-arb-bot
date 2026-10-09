# External review round 1

| file | what |
|---|---|
| `GROK_PREDICTIONS_RESULT.json` | raw measurement output: every prediction, point estimate, n, 95% bootstrap CI, verdict |
| `GROK_ROUND1_RESULTS.md` | the same results written up, with the tally and a suggested round-2 message |

Produced by `../scripts/80_grok_predictions.py` against all 760,817 fills
and 17,046 windows. Re-runnable:

```bash
python3 scripts/80_grok_predictions.py --dataset dataset \
    --out external_review/GROK_PREDICTIONS_RESULT.json
```

Verdicts are CI-based: SUPPORTED only when the whole 95% interval clears
the stated threshold, NOT_SUPPORTED only when the whole interval is on the
wrong side, INDETERMINATE otherwise. No verdict rests on a p-value.

**Result: 6 SUPPORTED, 4 NOT_SUPPORTED, 2 not testable as written.**
