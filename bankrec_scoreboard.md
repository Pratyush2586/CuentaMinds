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
| 2026-11-06 20:28 | live | submissions\live_v2.csv.gz | valid (no golden: upload to score) | LIVE v2 = sample v17 engine (88.71) |
| 2026-11-06 20:34 | live | submissions\live_v3.csv.gz | valid (no golden: upload to score) | LIVE v3 = sample v20 engine (90.78) |
| 2026-11-06 20:53 | live | submissions\live_v4.csv.gz | valid (no golden: upload to score) | LIVE v4 = sample v21 engine (91.38) |
| 2026-11-07 10:05 | train 2025-03 | submissions\m2025-03_v21.csv.gz | 80.25 | TRAIN 2025-03 held-out, v21 engine |
| 2026-11-07 10:10 | sample | submissions\sample_v22.csv.gz | 91.49 | v22 acct ccy from master, near-set subset search, exact-name tie |
| 2026-11-07 10:13 | train 2025-03 | submissions\m2025-03_v22.csv.gz | 83.54 | v22 acct ccy from master, near-set subset search, exact-name tie |
| 2026-11-07 10:17 | sample | submissions\sample_v24.csv.gz | 91.43 | v24 run-id join + bitset last resort |
| 2026-11-07 10:19 | train 2025-03 | submissions\m2025-03_v23.csv.gz | 86.06 | v23 payment runs joined by quoted e2e/run id (FX transfers, mixed-currency runs) |
| 2026-11-07 10:21 | train 2025-03 | submissions\m2025-03_v24.csv.gz | 85.56 | v24 + exact bitset subset-sum as last resort for known payers |
| 2026-11-07 10:23 | sample | submissions\sample_v23.csv.gz | 91.44 | v23 run-id join for payment runs |
| 2026-11-07 10:25 | train 2025-03 | submissions\m2025-03_v23b.csv.gz | 86.02 | v23 re-check after revert |
| 2026-11-07 10:26 | sample | submissions\sample_v23s1.csv.gz | 91.47 | v23 seed0 a |
| 2026-11-07 10:27 | sample | submissions\sample_v23s2.csv.gz | 91.47 | v23 seed0 b |
| 2026-11-07 10:30 | sample | submissions\sample_v25.csv.gz | 91.75 | v25 advice deductions explained by policy (discount/withholding/charges) are not disputes |
| 2026-11-07 10:32 | train 2025-03 | submissions\m2025-03_v23s.csv.gz | 85.98 | v23 seeded baseline |
| 2026-11-07 10:33 | train 2025-03 | submissions\m2025-03_v25.csv.gz | 89.33 | v25 advice deductions explained by policy are not disputes |
| 2026-11-07 10:41 | train 2024-10 | submissions\m2024-10_v25.csv.gz | 87.12 | v25 on second held-out month 2024-10 |
| 2026-11-07 10:57 | live | submissions\live_v6.csv.gz | valid (no golden: upload to score) | LIVE v6 = v25 (TRAIN-month 89.33, sample 91.75) |
| 2026-11-07 11:44 | live | submissions\live_v7.csv.gz | valid (no golden: upload to score) | LIVE v7 = v25 + confirming by maturity (eval 90.27) |
| 2026-11-07 12:06 | live | submissions\live_v8.csv.gz | valid (no golden: upload to score) | LIVE v8 = + coverage rounding + referenced-first ordering (eval 90.55) |
| 2026-11-07 12:27 | live | submissions\live_v9.csv.gz | valid (no golden: upload to score) | LIVE v9 = eval 90.55 engine + Sept-2025 advice layout drift fix |
| 2026-11-07 12:29 | live UPLOAD | submissions/live_v9.csv.gz | 90.32 (platform) | c30a802 v9 = eval 90.55 + Sept-2025 advice layout drift fix |
| 2026-11-07 13:03 | live | submissions\live_v10.csv.gz | valid (no golden: upload to score) | LIVE v10 = dc15838 instalments + partial shares (eval 90.77) |
| 2026-11-07 13:03 | live UPLOAD | submissions/live_v10.csv.gz | 90.69 (platform) | dc15838 instalments + partial shares (eval 90.77) |
| 2026-11-07 13:25 | live | submissions\live_v11.csv.gz | valid (no golden: upload to score) | LIVE v11 = 4755d1e one partial among quoted invoices (eval 90.88) |
| 2026-11-07 13:26 | live UPLOAD | submissions/live_v11.csv.gz | 90.81 (platform) | 4755d1e one partial among quoted invoices (eval 90.88) |
| 2026-11-07 14:00 | live | submissions\live_v12.csv.gz | valid (no golden: upload to score) | LIVE v12 = 3d67c36 advances/returns/chargebacks/dup refunds (eval 91.16) |
| 2026-11-07 14:01 | live UPLOAD | submissions/live_v12.csv.gz | 91.11 (platform) | 3d67c36 advances/returns/chargebacks/dup refunds (eval 91.16) |
| 2026-11-07 14:25 | live | submissions\live_v13.csv.gz | valid (no golden: upload to score) | LIVE v13 = 5925988 skeptic-tightened flows + cheque short stubs (eval 91.225) |
| 2026-11-07 14:25 | live UPLOAD | submissions/live_v13.csv.gz | 91.16 (platform) | 5925988 skeptic-tightened flows + cheque short stubs (eval 91.225) |
| 2026-11-07 15:32 | live | submissions\live_v14.csv.gz | valid (no golden: upload to score) | LIVE v14 = 9f72a96 PDF facts (eval 91.555) |
| 2026-11-07 15:32 | live UPLOAD | submissions/live_v14.csv.gz | 91.47 (platform) | 9f72a96 PDF facts (eval 91.555) |
| 2026-11-07 16:19 | live | submissions\live_v16.csv.gz | valid (no golden: upload to score) | LIVE v16 = d659638 99f29e7: UNIDENTIFIED rule + payment runs per account + window-minus-hole + discounts |
| 2026-11-07 16:24 | live UPLOAD | submissions/live_v16.csv.gz | 92.59 (platform) | d659638 99f29e7: UNIDENTIFIED rule + payment runs per account + window-minus-hole + discounts (user approved at checkpoint) |
