| time | phase | file | local score (validation only if no golden) | note |
|---|---|---|---|---|
| 2026-11-06 19:39 | sample | submissions\sample_v1.csv.gz | 58.79 | v1 batches+refs+party+nonitem |
| 2026-11-06 19:41 | sample | submissions\sample_v2.csv.gz | 59.69 | v2 batch fallbacks (mandate/name/suffix) |
| 2026-11-06 19:43 | sample | submissions\sample_v3.csv.gz | 60.34 | v3 lookback batch files, confirming subsets, payrun PARTIAL/FX |
| 2026-11-06 19:45 | sample | submissions\sample_v4.csv.gz | 62.08 | v4 sample opening balances from train golden |
| 2026-11-06 19:48 | sample | submissions\sample_v5.csv.gz | 61.94 | v5 faster subset-sum |
| 2026-11-06 19:51 | sample | submissions\sample_v6.csv.gz | 73.82 | v6 remittance advices |
| 2026-11-06 19:52 | sample | submissions\sample_v7.csv.gz | 77.12 | v7 ranked parties (idf, truncated words), party-scoped refs/PO |
| 2026-11-06 19:54 | sample | submissions\sample_v8.csv.gz | 80.82 | v8 unified per-payer resolution, gap fill, overpay cap |
| 2026-11-06 19:57 | sample | submissions\sample_v9.csv.gz | 80.68 | v9 speed (lists, cached name weights, subset pruning) |
| 2026-11-06 20:00 | sample | submissions\sample_v10.csv.gz | 84.03 | v10 explained single, credit refunds, UNIDENTIFIED only for unknown payers |
| 2026-11-06 20:01 | sample | submissions\sample_v11.csv.gz | 84.41 | v11 stricter known-payer for UNIDENTIFIED |
| 2026-11-06 20:02 | sample | submissions\sample_v12.csv.gz | 85.19 | v12 FX advices by original amount |
| 2026-11-06 20:05 | sample | submissions\sample_v13.csv.gz | 85.12 | v13 advice lines gross -deduction without paid column |
| 2026-11-06 20:06 | sample | submissions\sample_v14.csv.gz | 85.21 | v14 due-date prefix, zero-stripped suffixes |
| 2026-11-06 20:08 | sample | submissions\sample_v15.csv.gz | 85.32 | v15 no future-dated candidates, complement subset |
| 2026-11-06 20:09 | sample | submissions\sample_v16.csv.gz | 86.55 | v16 returns via DD batch mandate rows, DEVOL. |
| 2026-11-06 20:10 | sample | submissions\sample_v17.csv.gz | 88.71 | v17 same-name party tie-breaks, keep all tied |
| 2026-11-06 20:10 | live | submissions\live_v1.csv.gz | valid (no golden: upload to score) | LIVE v1 = sample v9 engine |
| 2026-11-06 20:12 | sample | submissions\sample_v18.csv.gz | 88.7 | v18 service month for same-amount invoices |
| 2026-11-06 20:13 | sample | submissions\sample_v18.csv.gz | 88.84 | v18 service month for same-amount invoices |
| 2026-11-06 20:14 | sample | submissions\sample_v19.csv.gz | 88.9 | v19 overpaid single, multi-invoice withholding |
| 2026-11-06 20:15 | sample | submissions\sample_v20.csv.gz | 90.78 | v20 overpayment signature (round / 1% / 10%) |
| 2026-11-06 20:27 | sample | submissions\sample_v21.csv.gz | 91.38 | v21 duplicate payments of settled invoices |
