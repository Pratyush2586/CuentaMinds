# CuentaMinds Runbook: VS Code + PowerShell, step by step

> Every command to run the bankrec application from a **new VS Code PowerShell terminal**: setup, data, runs,
> evaluation, inspection, LIVE uploads and the **FINAL at 18:00**, with the upload checkpoint that asks you before anything is sent.
> Architecture and background: `docs/BANKREC_GUIDE.md`. This file is the hands-on part.

**Conventions**
- Run every command from the repo root (§1). Lines starting with `#` are comments.
- The venv Python is always `.\.venv\Scripts\python.exe`. Never use the system `python` (3.11).
- Times are Madrid time. Today: FINAL data at **18:00**, submissions close **19:30**, projects close **19:45**.

---

## 0. Open the terminal

1. In VS Code: **File → Open Folder…** → `C:\Users\learn\Documents\prat\Hackathon\nova7nov`.
2. **Terminal → New Terminal**, and pick **PowerShell** from the `+ ▾` dropdown if it isn't the default.
3. If a script is ever blocked by the execution policy, allow it for this window only:
   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   ```

## 1. Go into the repo and check the state

```powershell
cd C:\Users\learn\Documents\prat\Hackathon\nova7nov
git status                     # should be clean (or only docs/SME_REVIEW_PACK.md untracked)
git checkout main
git pull                       # latest engine from GitHub
git log --oneline -5           # newest commit first
```

On another machine, start from the clone instead:

```powershell
git clone https://github.com/Pratyush2586/CuentaMinds.git
cd CuentaMinds
```

## 2. Python environment (first time on a machine only)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pip install pdfplumber openpyxl requests anthropic   # used by bankrec tooling
.\.venv\Scripts\python.exe --version            # Python 3.12.x
.\.venv\Scripts\bankrec.exe --help              # train-nonitem, run, score
.\.venv\Scripts\python.exe -m pytest -q         # all tests must pass (~300, ~25 s)
```

## 3. Secrets: API key and team token (never commit, never paste in chat)

Both live in the git-ignored file `.env` at the repo root. Open it with:

```powershell
notepad .env
```

It must contain these lines (values are yours):

```
ANTHROPIC_API_KEY=sk-ant-...      # Claude, used only by scripts/analyst.py (advisory)
HK_TOKEN=...                      # team token, used only by scripts/submit.py (uploads)
```

Check they're present without printing them:

```powershell
Select-String -Path .env -Pattern '^(ANTHROPIC_API_KEY|HK_TOKEN)=' | ForEach-Object { $_.Line.Split('=')[0] + '=<set>' }
git check-ignore .env claudeapi.txt          # both must be printed: they are ignored by git
```

## 4. Data: where it goes and how to add new data

All data lives under `data\` (git-ignored). The loader finds phase folders **by content**: any folder named `train`,
`live` or `final` that holds `bank_transactions.csv.gz`. Expected layout:

```
data\
  bankrec-docs-and-masters.zip   bankrec-train.tar.gz   bankrec-live.tar.gz   bankrec-final.tar.gz (18:00)
  valtermia\
    docs\     masters\ sample\ starter\ POLICIES.md ...
    train\train\    live\live\    final\final\  (+ final\final\masters_final\)
    cache\    (models, parsed advices, PDF facts, month caches: built automatically)
```

Adding an archive by hand (example: FINAL):

```powershell
New-Item -ItemType Directory -Force data\valtermia\final | Out-Null
tar -xzf data\bankrec-final.tar.gz -C data\valtermia\final
Get-ChildItem -Recurse data\valtermia\final -Filter bank_transactions.csv.gz | Select-Object FullName
Get-ChildItem -Recurse -Directory data\valtermia\final -Filter masters_final | Select-Object FullName
```

First-time model build (about 2 min, only if `data\valtermia\cache\nonitem.json` is missing):

```powershell
.\.venv\Scripts\bankrec.exe train-nonitem
```

## 5. Run the application

| what | command | time |
|---|---|---|
| sample month (smoke test, scored) | `.\.venv\Scripts\bankrec.exe run --phase sample --out submissions\sample_check.csv.gz --note "smoke"` | ~1 min |
| TRAIN month (LIVE-like, scored) | `.\.venv\Scripts\bankrec.exe run --month 2025-03 --out submissions\m2025-03_check.csv.gz --note "check"` | ~4 min |
| LIVE file (validated, not uploaded) | `.\.venv\Scripts\bankrec.exe run --phase live --out submissions\live_vNN.csv.gz --note "what changed"` | ~18 min |
| FINAL file (validated, not uploaded) | `.\.venv\Scripts\bankrec.exe run --phase final --out submissions\final_v1.csv.gz --note "FINAL"` | ~20–25 min |
| re-validate any file | `.\.venv\Scripts\bankrec.exe score submissions\live_vNN.csv.gz --phase live` | seconds |

Reading the output:
- `"ok": true` means the official validation passed. **Never upload without it.**
- `by stage {...}` shows how many lines each stage resolved.
- On LIVE/FINAL the printed score (20.0) means nothing, because there's no answer key.
- Each run also writes `<file>.stats.json`, used by the readiness gate.

## 6. Evaluate (internal scoring = our estimate of the platform score)

```powershell
# official score on the two dev months, keep/discard decision, logged to autoresearch\results.tsv
.\.venv\Scripts\python.exe scripts\autoresearch.py eval "short description of the change"
.\.venv\Scripts\python.exe scripts\autoresearch.py holdout        # untouched month 2025-05 (generalisation check)
.\.venv\Scripts\python.exe scripts\autoresearch.py status         # the experiment log
```

The predicted platform score is printed after each eval: `predicted LIVE ~ eval − gap`. The calibration lives in
`bankrec_calibration.tsv`; after the drift fix the gap stays between 0.02 and 0.08.

## 7. Inspect (why lines are wrong)

```powershell
$S = "autoresearch\runs\<commit>_2025-03.csv.gz"     # any month result, or submissions\m2025-03_check.csv.gz
.\.venv\Scripts\python.exe scripts\bankrec_pairs.py $S --month 2025-03            # lost pairs per segment
.\.venv\Scripts\python.exe scripts\bankrec_exc.py   $S --month 2025-03            # exception precision/recall/F1
.\.venv\Scripts\python.exe scripts\bankrec_diff.py  $S ar_retention 5 --month 2025-03   # wrong lines of one segment
$env:PYTHONHASHSEED = "0"; .\.venv\Scripts\python.exe scripts\bankrec_probe.py 2025-03 BT-2025-0061782   # engine internals
.\.venv\Scripts\python.exe scripts\live_report.py      # latest LIVE upload vs previous vs TRAIN, per segment
.\.venv\Scripts\python.exe scripts\analyst.py hypotheses        # Claude analyst: next experiments (~$0.2)
.\.venv\Scripts\python.exe scripts\analyst.py skeptic HEAD      # Claude skeptic: overfitting review of a commit
.\.venv\Scripts\python.exe scripts\overfit_guard.py main HEAD   # static check: no ids/dates/amount literals
```

## 8. Improve (the experiment loop)

```powershell
git checkout autoresearch/nov7                 # experiments live on this branch
# edit src\bankrec\engine.py (one change)
git commit -am "exp: <hypothesis in one line>"
.\.venv\Scripts\python.exe scripts\autoresearch.py eval "<hypothesis>"     # KEEP stays, DISCARD is reverted automatically
.\.venv\Scripts\python.exe scripts\overfit_guard.py main HEAD
# promote a kept change to main
git checkout main; git merge --ff-only autoresearch/nov7; git push; git checkout autoresearch/nov7
```

## 9. Submit to LIVE (with the checkpoint)

```powershell
git checkout main
.\.venv\Scripts\python.exe scripts\pipeline.py --tag v15 --note "what changed" --submit
```

The pipeline:
1. refuses uncommitted engine changes;
2. builds and validates the LIVE file;
3. shows the **UPLOAD CHECKPOINT** (internal evaluation, calibrated predicted score, best LIVE so far) and asks:
   ```
   Do you agree with this assessment and want to upload this file to the LIVE leaderboard? [y/N]:
   ```
   - **y**: it uploads through the organisers' API (≥ 3 min spacing, ≤ 20/day), waits for the score, saves
     `submissions\results\*.json` and prints the per-segment report.
   - **anything else**: nothing is sent, and it prints how to improve first (§7–8).

Upload an existing file (still with the checkpoint): `.\.venv\Scripts\python.exe scripts\pipeline.py --file submissions\live_v15.csv.gz --submit`.
After a scored upload, add the calibration row (engine commit, eval, LIVE score) to `bankrec_calibration.tsv`.

## 10. FINAL at 18:00, step by step

**17:30, preparation:**

```powershell
cd C:\Users\learn\Documents\prat\Hackathon\nova7nov
git checkout main; git pull; git status          # clean
git tag final-engine; git push --tags            # the exact engine used for FINAL
Get-Process python -ErrorAction SilentlyContinue # no experiment still running (they slow FINAL); stop them
```

Plug the laptop in. Close heavy applications.

**18:00, one command** (checksum → extract → sanity → run → gate → Claude review → checkpoint):

```powershell
.\.venv\Scripts\python.exe scripts\final_run.py --download
```

What to watch for at each step:

| step | you should see | if not |
|---|---|---|
| 1 checksum | `sha256 …` equal to `site …` | re-download (`Remove-Item data\bankrec-final.tar.gz`, run again) |
| 2 extract | `phase dir: [...\final\final]` | check the archive; extract by hand (§4) |
| 3 sanity | ~220k lines, dates 2026-01-01..2026-06-30, `masters …masters_final`, ZA01 among the entities, fx up to 2026-06 | `SANITY WARNINGS` → read §11 below before continuing |
| 4 run | `"ok": true` after ~20–25 min (first minutes quiet: parsing PDFs/advices) | traceback → §11 |
| 5 gate | `GATE: PASS` (or WARN with explained flags) | `FAIL` → §11 |
| 6 review | Claude release-manager note (go / no-go, expected range, risks) | advisory only |
| 7 checkpoint | predicted score, gate verdict, expected FINAL range, and the question | |

The checkpoint question:

```
Do you agree with this assessment and want to upload the FINAL file? Type UPLOAD to confirm:
```

- Type **UPLOAD** (exactly): the one scored FINAL submission is sent and the platform's status is shown.
- Anything else: **nothing is sent**. Improve (§7–8, then re-run step 4: `bankrec run --phase final …`), and come back
  to the checkpoint with:
  ```powershell
  .\.venv\Scripts\python.exe scripts\final_run.py --skip-run --file submissions\final_v1.csv.gz
  ```
- Gate FAIL: the checkpoint asks for **OVERRIDE** instead. Use it only if you understand and accept the flagged risk.

**Deadline:** a validated FINAL upload by **~18:45**. Submissions close at 19:30. If no valid file exists by **19:10**, use the fallback in §11.

After the upload:

```powershell
git add bankrec_scoreboard.md; git commit -m "chore: FINAL submitted"; git push
```

## 11. If something goes wrong (FINAL evening)

| symptom | action |
|---|---|
| `FINAL is not published yet` | the site's `config.json` still says unavailable: wait a minute and retry |
| checksum mismatch | delete the archive and re-run with `--download` |
| traceback in step 4 | read the last line; most likely a new value (ZA01, ZAR, a new column). Fix with a guard, re-run step 4. **19:05 without a fix → fallback** |
| `"ok": false` | the printed errors say what (missing lines / bad category / amount); fix, re-run `bankrec score … --phase final` |
| gate FAIL | look at the per-entity table in the gate output (which entity / stage collapsed), fix, re-run |
| **19:10 and still no valid file** | fallback: `.\.venv\Scripts\python.exe data\valtermia\docs\starter\starter.py --data data\valtermia\final --phase final --out submissions\final_starter.csv.gz` → `bankrec score … --phase final` → `scripts\submit.py FINAL submissions\final_starter.csv.gz --i-confirm-final-is-one-shot` (≈ 19 points, better than nothing) |
| browser upload crashes ("insertBefore…") | don't use the browser: the API pipeline above uploads directly |
| `git push` auth error | `& "C:\Program Files\GitHub CLI\gh.exe" auth setup-git` |

## 12. Project hand-in (before 19:45)

```powershell
& "C:\Program Files\GitHub CLI\gh.exe" repo edit Pratyush2586/CuentaMinds --visibility public --accept-visibility-change-consequences
```

Then on the site's Proyecto (Project) page:
- the repo URL, with "Repo access confirmed" ticked;
- the video link or upload;
- the write-up (template in `docs/BANKREC_GUIDE.md` §12);
- **Save**.

## 13. Command cheat sheet

```powershell
cd C:\Users\learn\Documents\prat\Hackathon\nova7nov
.\.venv\Scripts\bankrec.exe run --phase live --out submissions\live_vNN.csv.gz --note "..."
.\.venv\Scripts\python.exe scripts\autoresearch.py eval "..."
.\.venv\Scripts\python.exe scripts\pipeline.py --tag vNN --note "..." --submit        # LIVE, asks y/N
.\.venv\Scripts\python.exe scripts\checkpoint.py live submissions\live_vNN.csv.gz     # assessment only, no upload
.\.venv\Scripts\python.exe scripts\final_run.py --download                            # FINAL, asks UPLOAD
.\.venv\Scripts\python.exe scripts\final_run.py --skip-run --file submissions\final_v1.csv.gz   # back to the FINAL checkpoint
```

---

## 14. Fresh clone next to the working copy (reproduce LIVE v16, then run the latest)

From `PS C:\Users\learn\Documents\prat\Hackathon>`. The data is git-ignored, so it is copied from the working copy
(`nova7nov`), including `data\valtermia\cache` (category models + PDF facts), so the clone gives byte-identical results.

```powershell
# 1. code
git clone https://github.com/Pratyush2586/CuentaMinds.git CuentaMinds
cd CuentaMinds
git checkout live-v16                      # engine 99f29e7 = the file that scored 92.59 on LIVE

# 2. Python 3.12 venv + packages
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m venv .venv
.\.venv\Scripts\python -m pip install -q --upgrade pip
.\.venv\Scripts\python -m pip install -q -e ".[dev]" requests
.\.venv\Scripts\python -c "import bankrec, pdfplumber, anthropic, requests; print('packages ok')"

# 3. secrets + data (never committed)
Copy-Item ..\nova7nov\.env .\.env
robocopy ..\nova7nov\data\valtermia .\data\valtermia /E /MT:16 /NFL /NDL /NJH /NP
.\.venv\Scripts\python -c "import pathlib; print([l.split('=')[0] for l in pathlib.Path('.env').read_text().splitlines() if '=' in l])"

# 4. reproduce LIVE v16 (about 16-20 min) and compare with the uploaded file
.\.venv\Scripts\python -m bankrec.cli run --phase live --out submissions\repro_v16.csv.gz --note "repro v16"
.\.venv\Scripts\python -c "import pandas as pd; a=pd.read_csv('submissions/repro_v16.csv.gz',dtype=str,keep_default_na=False); b=pd.read_csv('../nova7nov/submissions/live_v16.csv.gz',dtype=str,keep_default_na=False); print('IDENTICAL' if a.equals(b) else 'DIFFERENT', len(a), len(b))"

# 5. move to the latest engine (main) for the next LIVE / FINAL
git checkout main
git pull
```
