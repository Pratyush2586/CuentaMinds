# closeops: Getting Started and Operations Manual

A step-by-step manual for a new engineer. It covers installing the project, starting and stopping it, running a close, looking at the results, and keeping it healthy.

There is no prior knowledge assumed beyond "I can open a terminal and type commands".

> Other docs, once you are set up:
> - `docs/USER_GUIDE.md`: what every output file means and how scoring works.
> - `RUNBOOK.md`: the hackathon-day checklist and settings.
> - `docs/HACKATHON_PLAYBOOK.md`: prompts to give Claude Code on the day.

---

## 0. What this project is (2-minute read)

`closeops` takes a company's raw accounting files and does the month-end "close" automatically. The files are invoices, purchase orders, bank statements, vendors, the general ledger and so on. The close produces:
- a list of **problems found** (duplicates, wrong tax, suspicious bank-detail changes, unmatched bank lines …)
- **proposed accounting entries** to fix them
- a **payment run** with risky payments blocked
- a **report** and a tamper-proof **audit log**

Every problem is put into one of three buckets:

| Bucket | Meaning |
|---|---|
| **AUTO** | The system is confident and the amount is small, so it acts on its own |
| **AUTO_NOTE** | It acts, but flags the item for a later look |
| **HOLD** | A human must decide (changed bank details, fraud, large amounts) |

There are two ways to use it:
- **Dashboard (web page):** click buttons and look at results. Best for demos and for exploring.
- **Command line (`closeops`):** type commands. Best for repeatable runs and scoring.

Optional **AI agents** can review the borderline findings and write a close memo. They use Gemini (free) or Claude (paid). Everything works without them.

---

## 1. Prerequisites (check once)

The project needs Windows 10 or 11, **Python 3.12**, and **Git**. Open **PowerShell** (Start menu → type `PowerShell` → Enter) and check each item:

```powershell
py -3.12 --version      # must print: Python 3.12.x
git --version           # any version is fine
```

- If `py -3.12` says it can't find Python 3.12, install it from <https://www.python.org/downloads/> and tick **"Add python.exe to PATH"** in the installer. Then close and reopen PowerShell.
- On this machine the plain `python` command is **3.11**. Never use it for this project; always use the project's own Python (step 2).

---

## 2. First-time install (about 5 minutes, once per machine)

### 2.1 Go to the project folder

```powershell
cd C:\Users\learn\Documents\prat\Hackathon\nova7nov
```

> **Every command in this manual is run from this folder.**
> - If a command says "file not found", first check you are here with `pwd`.
> - If you copied the project somewhere else, `cd` into that folder instead.

### 2.2 Create the project's private Python environment

```powershell
py -3.12 -m venv .venv
```

This creates a `.venv` folder: a private copy of Python just for this project.
- You only do this once.
- If `.venv` already exists, skip this step.

### 2.3 Install the project and its libraries

```powershell
.venv\Scripts\python -m pip install -e ".[dev]"
```

This takes 1–3 minutes and ends with `Successfully installed …`.

> **Why `.venv\Scripts\…` everywhere?**
> - Calling programs through `.venv\Scripts\` always uses the project's Python, so you never need to "activate" anything.
> - Activation is often blocked in PowerShell by the execution policy.

### 2.4 Prove the install works: run the tests

```powershell
.venv\Scripts\python -m pytest -q
```

Expected last line: **`301 passed`** (about 25 seconds).
- Any `failed` line means something is wrong. Stop here and see §11 Troubleshooting.
- The tests never call any paid or online service.

### 2.5 (Optional) API keys for the AI agents

Skip this step if you don't need the AI agents.

Create a file named `.env` in the project folder:

```powershell
notepad .env
```

Put these lines in it, save, and close Notepad:

```
GEMINI_API_KEY=paste-your-gemini-key-here
ANTHROPIC_API_KEY=paste-your-claude-key-here
```

- **Gemini** is the default and is free. Get a key at <https://aistudio.google.com/apikey>.
- **Claude** is only used if you switch the provider (§8.4), and it needs paid credit on the Anthropic account.
- `.env` is **git-ignored**, so it is never committed. **Never paste keys into any other file, chat or commit.**

Your install is done.

---

## 3. Start: the dashboard (the easiest way in)

### 3.1 Start it

```powershell
.venv\Scripts\streamlit run dashboard\app.py
```

After a few seconds you will see:

```
  Local URL: http://localhost:8501
```

- Your browser usually opens by itself. If not, open **<http://localhost:8501>**.
- **Leave this PowerShell window open.** Closing it stops the dashboard. Open a second PowerShell window for any other commands.

### 3.2 Your first close (2 clicks)

The **left sidebar** holds the controls:

| Field | Default | What it is |
|---|---|---|
| Raw data dir | `data/demo` | The folder of raw accounting files to process |
| Mapping | `config/mappings.synth.yaml` | Tells the system which column in the raw files means what |
| Policy | `config/policy.yaml` | All thresholds and settings |
| Results dir | `results` | Where each run's output folder is written |
| Agents | off | Turns the AI review on or off. Off means rules only, which is fast and free |

1. Click **Generate demo data** (first time only). This writes practice files into `data/demo`.
2. Click **Run close**. It takes about 2 seconds with Agents off.

### 3.3 What to look at (tabs)

1. **Overview:**
   - problems found per category
   - % of bank lines reconciled
   - how many items are on HOLD
   - audit log status: **verified ✓** is good
2. **Exceptions:**
   - Pick a category and a bucket, then click a finding.
   - You see its evidence, **the exact raw rows** it came from (file + row number), and the AI explanation if agents ran.
3. **Journal & Payments:** the proposed accounting entries, and the payment run with blocked payments in **red** and the reason why.
4. **Close report:** the summary report for the whole close.
5. **Human review:**
   - **Approve / Reject** each HOLD item.
   - Every click is written to the audit log with a timestamp.

### 3.4 Look at an older run

Type a different folder into **Results dir**, for example `results/smoke` or `results/demo`, and press **Enter**. The dashboard shows the **newest** run inside that folder.

---

## 4. Stop

### 4.1 Normal stop

Click into the PowerShell window running the dashboard and press **Ctrl + C**. Wait for the prompt to come back. The browser page will then show "connection error", which is expected.

### 4.2 You closed the window and the dashboard still answers, or "port 8501 is already in use"

Find and stop whatever is holding the port:

```powershell
Get-NetTCPConnection -LocalPort 8501 -State Listen |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Confirm:$false }
```

Check that it is gone. The following should print nothing, or an error:

```powershell
Get-NetTCPConnection -LocalPort 8501 -State Listen
```

### 4.3 Stop a long command-line run

Press **Ctrl + C** in its window.
- Runs with AI agents can take a few minutes.
- Without agents a run takes seconds, so there is normally nothing to stop.
- An interrupted run leaves a half-written folder in `results/`. Ignore it or delete it (§8.1).

---

## 5. Restart / run on another port

- **Restart:** stop it (§4.1), then start it again (§3.1). Restart after you change code. Changes to `config/*.yaml` are picked up on the next **Run close** without a restart.
- **Use another port** (for example if 8501 is taken):

  ```powershell
  .venv\Scripts\streamlit run dashboard\app.py --server.port 8502
  ```

  Then open <http://localhost:8502>.
- **Run without opening a browser** (for example on a server):

  ```powershell
  .venv\Scripts\streamlit run dashboard\app.py --server.headless true
  ```

- **Health check** (from a second window). This prints `ok` when the dashboard is up:

  ```powershell
  curl.exe -s http://localhost:8501/_stcore/health
  ```

---

## 6. Run from the command line (repeatable runs and scoring)

Open a PowerShell window in the project folder. The full cycle is **make data → look at it → run → score**.

### 6.1 Make practice data (with an answer key)

```powershell
.venv\Scripts\closeops synth data\try --seed 7
```

- This writes about 14 messy raw files into `data\try\`.
- It also writes `answer_key.json`: the list of the **90 problems planted on purpose**.
- Use another `--seed` number for a different company, `--n 1000` for more invoices, and `--hard` for harder traps.

### 6.2 Look at the raw files

```powershell
.venv\Scripts\closeops profile data\try > data\try_profile.md
```

Open `data\try_profile.md`. For every file it lists the columns, their types, empty values and sample rows. This is how you get to know new data before mapping it.

### 6.3 Run the close

```powershell
.venv\Scripts\closeops run data\try --mapping config\mappings.synth.yaml --out results
```

It prints something like:

```
results\20261106-120000
90 findings (AUTO=26, AUTO_NOTE=47, HOLD=17)
```

- The first line is **the run folder**, so note it down.
- Any `note:` lines are warnings: missing columns, values that could not be read, and so on. **Always read them.**
- Add `--agents` to let the AI review the borderline items. That is slower and uses API quota.

### 6.4 Score it against the answer key

```powershell
.venv\Scripts\closeops score results\20261106-120000 data\try\answer_key.json --note "my first run"
```

Replace the folder name with the one your run printed. You get a table:

| Column | Meaning |
|---|---|
| **tp** (true positive) | planted problems we found ✓ |
| **fp** (false positive) | things we flagged that were not real problems ✗ |
| **fn** (false negative) | planted problems we missed ✗ |
| **precision** | tp / (tp + fp): "when we flag something, how often are we right" |
| **recall** | tp / (tp + fn): "how many of the real problems did we catch" |

A perfect run is **90 tp / 0 fp / 0 fn**. Each score is also added as a line in `scoreboard.md`, so you keep a history.

> **Example:** a rules-only run scored 90/0/0. The same data with AI agents scored 89/0/1, because the AI wrongly dismissed one real fraud. That is why the rules-only run is the safe default, and agent runs are only used when they score at least as well.

### 6.5 Check the audit log was not tampered with

```powershell
.venv\Scripts\python -c "import sys; from closeops.evidence import EvidenceLog; print(EvidenceLog.status(sys.argv[1]))" results\20261106-120000\evidence.jsonl
```

The result is `verified` (good), `broken` (someone edited the file) or `missing`.

---

## 7. Where everything lives

| Path | What | In git? |
|---|---|---|
| `src\closeops\` | The program | yes |
| `dashboard\` | The web UI | yes |
| `config\policy.yaml` | **All settings and thresholds** | yes |
| `config\mappings.*.yaml` | Column mappings per dataset | yes |
| `tests\` | Automated tests | yes |
| `data\` | Raw input data (practice and real) | **no** (ignored) |
| `results\<timestamp>\` | One folder per run | **no**, except `results\demo` |
| `results\demo\` | Pre-recorded run used as the demo fallback | yes |
| `scoreboard.md` | History of scored runs | yes |
| `.env` | API keys | **no** (ignored, secret) |
| `.venv\` | Private Python | **no** |

**Inside a run folder, open in this order:**
1. `close_report.md`
2. `findings.csv` (opens in Excel)
3. `payment_proposals.csv`
4. `journal_entries.csv`
5. `close_memo.md` (only with agents)

`docs\USER_GUIDE.md` §4 explains every file.

---

## 8. Manage: day-to-day housekeeping

### 8.1 Clean up old runs

Every run creates a new folder, and they pile up. To delete the runs you don't need:

```powershell
Remove-Item -Recurse -Force results\smoke          # one sub-folder
Remove-Item -Recurse -Force results\2026*          # all top-level timestamped runs
```

> **Never delete `results\demo`.** It is the committed demo fallback. If you clicked Approve or Reject on it in the dashboard, reset it with `git checkout -- results/demo`.

### 8.2 Clean up practice data

```powershell
Remove-Item -Recurse -Force data\try
```

Practice data can always be regenerated with `closeops synth` (§6.1).

> **Never delete `data\raw\`.** On the hackathon day it holds the real event data, and that data is not in git.

### 8.3 Change settings

Edit `config\policy.yaml` in any text editor. Every line has a comment. Common knobs:

| Setting | Effect |
|---|---|
| `materiality_eur` | Amount above which items are never fully automatic |
| `tiers.auto_min_confidence` | How sure the rules must be for AUTO |
| `duplicates.*`, `tax.*`, `bankrec.*` | Tolerances for each check |
| `agents.max_residuals` | How many findings the AI may review per run. **Keep it low (about 10) to save free quota** |
| `agents.wall_clock_seconds` | Time limit for the AI per run |

After a change:
1. Re-run (§6.3).
2. Re-score with a `--note` that says what you changed (§6.4).
3. Compare with the previous line in `scoreboard.md`.

### 8.4 Switch the AI provider

In `config\policy.yaml`, under `agents:`:

```yaml
  provider: gemini        # free (needs GEMINI_API_KEY in .env)
  # provider: claude      # paid (needs ANTHROPIC_API_KEY and credit on the account)
```

**Free Gemini quota is per model, per day.** For example, `gemini-2.5-flash` allows only 20 requests a day. When the quota runs out:
- The run prints `agents unavailable: Gemini daily quota exhausted …`.
- It then finishes normally on the rules alone, so nothing breaks.
- To carry on, change `gemini_model` / `gemini_controller_model` to another model, or wait until tomorrow.

### 8.5 Back up important runs

A run you want to keep, such as a submitted one, should be copied outside `results\` or zipped:

```powershell
Compress-Archive results\20261106-120000 submissions\run-20261106-120000.zip
```

(`submissions\` is git-ignored.)

---

## 9. Update the code safely

### 9.1 See where you are

```powershell
git status          # what changed locally
git log --oneline -5
```

### 9.2 After pulling or receiving new code

1. Get the code: `git pull` if a remote is set up, or copy the files in.
2. Reinstall if `pyproject.toml` changed. It is harmless to run anyway:

   ```powershell
   .venv\Scripts\python -m pip install -e ".[dev]"
   ```

3. Run the tests. They must all pass:

   ```powershell
   .venv\Scripts\python -m pytest -q
   ```

4. Restart the dashboard if it is running (§5).

### 9.3 Making your own change

1. Run the tests before the change, so you know they passed to begin with.
2. Make the change, with a test first for any logic change.
3. Run the tests again.
4. Commit:

   ```powershell
   git add <files you changed>
   git commit -m "what and why"
   ```

Never commit `data\`, `.env` or `claudeapi.txt`. `.gitignore` already protects them, but check `git status` before committing.

---

## 10. Daily start / end checklist

**Start of day**

```powershell
cd C:\Users\learn\Documents\prat\Hackathon\nova7nov
git status                                  # clean? on the right branch (main)?
.venv\Scripts\python -m pytest -q           # all passed?
.venv\Scripts\streamlit run dashboard\app.py
```

**End of day**
1. Stop the dashboard with **Ctrl + C**.
2. Commit any work you want to keep.
3. Optionally clean old `results\` folders (§8.1).

---

## 11. Troubleshooting

| What you see | Why | Fix |
|---|---|---|
| `py -3.12` not found | Python 3.12 not installed | §1 |
| `'.venv\Scripts\closeops' is not recognized` | Not in the project folder, or install not done | `cd` to the project, then §2.3 |
| `ModuleNotFoundError: closeops` | Install step skipped or done with the wrong Python | Run §2.3 again |
| Tests fail right after install | Wrong Python, or a broken checkout | Check `.venv\Scripts\python --version` is 3.12; rerun §2.3 |
| `Port 8501 is already in use` | An old dashboard is still running | §4.2, or use another port (§5) |
| Browser shows "connection error" | Dashboard stopped | Start it (§3.1) |
| Dashboard shows an old run | It shows the newest folder in Results dir | Change Results dir, or click **Run close** |
| Lots of `note: … unparsed` | Dates or numbers in an unexpected format | Fix the mapping (`docs\USER_GUIDE.md` §6, `RUNBOOK.md` §3) |
| `agents unavailable: GEMINI_API_KEY not set` | No `.env`, or the key is missing | §2.5 |
| `agents unavailable: … daily quota exhausted` | Free Gemini quota used up for that model | §8.4: switch model or wait |
| `agents unavailable: … credit balance is too low` | Claude account has no credit | Add credit, or use `provider: gemini` |
| `agent error: … HTTP 429` on some items | Rate limit (too many requests per minute) | Lower `agents.max_workers` / `max_residuals` |
| Audit log `broken` | `evidence.jsonl` was edited after the run | Re-run to get a clean log |
| PowerShell: "running scripts is disabled" | You tried `Activate.ps1` | You don't need it; always call `.venv\Scripts\…` directly |

If you are stuck for more than 10 minutes, write down the exact command and error and ask.

---

## 12. Full reset / uninstall

**Reset to a clean state** (keeps the code):

```powershell
Remove-Item -Recurse -Force .venv            # private Python
Remove-Item -Recurse -Force results\2026*    # your runs (results\demo stays)
git checkout -- results/demo scoreboard.md   # undo changes to committed files
```

Then repeat §2.2–§2.4.

**Uninstall completely:** stop the dashboard, then delete the whole project folder. Nothing is installed anywhere else, apart from Python itself.

---

## 13. Glossary

| Term | Meaning |
|---|---|
| **Close** | The month-end process of checking and finalising the books |
| **Finding** | One problem the system found |
| **Rules** | Fixed, deterministic checks (no AI): fast, free, repeatable |
| **Agents** | Optional AI reviewers for borderline findings |
| **Residual** | A borderline finding the rules were not sure about; the only kind agents review |
| **Mapping** | The YAML file that says "column `Inv No` in this file is our `invoice_id`" |
| **Policy** | The YAML file of thresholds and settings |
| **Answer key** | The list of planted problems in practice data, used for scoring |
| **IBAN** | A bank account number. A changed IBAN right before a payment is a classic fraud sign, so such payments always go to HOLD |
| **Evidence log** | `evidence.jsonl`: every decision, chained by hashes so edits are detectable |
