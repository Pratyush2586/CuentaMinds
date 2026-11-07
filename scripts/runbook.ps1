<#
.SYNOPSIS
  CuentaMinds bankrec runbook: runs the setup / reproduce / LIVE / FINAL procedure step by step.

.DESCRIPTION
  Every step shows what it does and the exact command, then waits for you:
     [Enter] run    [s] skip    [q] quit
  A failed step asks: [r] retry  [s] skip  [q] quit.  Nothing is uploaded without the checkpoint
  (LIVE: answer y; FINAL: type UPLOAD).  A transcript is written to runbook_<time>.log in the clone.

.EXAMPLE
  # from PS C:\Users\learn\Documents\prat\Hackathon>
  powershell -ExecutionPolicy Bypass -File .\nova7nov\scripts\runbook.ps1                    # setup + reproduce v16
  powershell -ExecutionPolicy Bypass -File .\nova7nov\scripts\runbook.ps1 -Phase live -Tag v17
  powershell -ExecutionPolicy Bypass -File .\nova7nov\scripts\runbook.ps1 -Phase final
  ... -From 7          start at step 7          ... -DryRun     only print the steps
#>
param(
    [ValidateSet("setup", "live", "final", "fixloop", "all")] [string]$Phase = "setup",
    [string]$Target = "C:\Users\learn\Documents\prat\Hackathon\CuentaMinds",
    [string]$Source = "C:\Users\learn\Documents\prat\Hackathon\nova7nov",
    [string]$Repo = "https://github.com/Pratyush2586/CuentaMinds.git",
    [string]$Ref = "live-v16",
    [string]$Tag = "v17",
    [string]$Engine = "main",          # LIVE / FINAL engine: main (latest kept) or a tag such as live-v16 (fallback)
    [int]$From = 1,
    [switch]$DryRun
)

$ErrorActionPreference = "Continue"
$Py312 = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
$Py = Join-Path $Target ".venv\Scripts\python.exe"
$script:n = 0

function Say([string]$text, [string]$color = "Gray") { Write-Host $text -ForegroundColor $color }

function Step([string]$title, [string]$why, [string]$cmd, [scriptblock]$action, [scriptblock]$ok = $null) {
    $script:n++
    if ($script:n -lt $From) { return }
    Write-Host ""
    Say ("=" * 78) "DarkCyan"
    Say (" STEP {0}: {1}" -f $script:n, $title) "Cyan"
    Say ("=" * 78) "DarkCyan"
    Say (" why : " + $why)
    Say (" cmd : " + $cmd) "Yellow"
    if ($DryRun) { return }
    while ($true) {
        $a = Read-Host " [Enter] run   [s] skip   [q] quit"
        if ($a -eq "q") { Say "Stopped. Resume later with -From $script:n" "Magenta"; try { Stop-Transcript | Out-Null } catch {}; exit 0 }
        if ($a -eq "s") { Say " skipped" "DarkYellow"; return }
        $global:LASTEXITCODE = 0
        $t0 = Get-Date
        try { & $action; $code = $LASTEXITCODE } catch { Say (" ERROR: " + $_) "Red"; $code = 1 }
        $good = ($code -eq 0)
        if ($good -and $ok) { $good = [bool](& $ok) }
        $secs = [int]((Get-Date) - $t0).TotalSeconds
        if ($good) { Say (" OK ({0}s)" -f $secs) "Green"; return }
        Say (" FAILED (exit {0}, {1}s)" -f $code, $secs) "Red"
        $b = Read-Host " [r] retry   [s] skip   [q] quit"
        if ($b -eq "q") { Say "Stopped. Resume later with -From $script:n" "Magenta"; try { Stop-Transcript | Out-Null } catch {}; exit 1 }
        if ($b -eq "s") { return }
    }
}

if (-not $DryRun -and (Test-Path $Target)) {
    Start-Transcript -Path (Join-Path $Target ("runbook_{0}.log" -f (Get-Date -Format "yyyyMMdd_HHmm"))) | Out-Null
}
Say "CuentaMinds runbook  phase=$Phase  target=$Target  source=$Source" "White"

# ----------------------------------------------------------------------------------------------- setup
if ($Phase -in @("setup", "all")) {
    Step "Clone the repository" "fresh copy of the code next to the working folder (skipped if it exists)" `
        "git clone $Repo $Target" {
        if (Test-Path (Join-Path $Target ".git")) { Say " already cloned: $Target" } else { git clone $Repo $Target }
        if (Test-Path $Target) { Start-Transcript -Path (Join-Path $Target ("runbook_{0}.log" -f (Get-Date -Format "yyyyMMdd_HHmm"))) -Append | Out-Null }
    }
    Step "Check out the engine of LIVE v16" "tag $Ref = engine 99f29e7, scored 92.59 on LIVE" `
        "git -C $Target fetch --tags; git -C $Target checkout $Ref" {
        git -C $Target fetch --tags -q; git -C $Target checkout -q $Ref; git -C $Target log --oneline -1
    }
    Step "Create the Python 3.12 virtual environment" "never use the system Python 3.11" `
        "$Py312 -m venv $Target\.venv" {
        if (Test-Path $Py) { Say " venv exists" } else { & $Py312 -m venv (Join-Path $Target ".venv") }
    }
    Step "Install the packages" "the project (editable) + pytest + requests" `
        "python -m pip install -e "".[dev]"" requests" {
        Push-Location $Target
        & $Py -m pip install -q --upgrade pip
        & $Py -m pip install -q -e ".[dev]" requests
        Pop-Location
    }
    Step "Verify the packages" "imports the engine and its libraries" `
        "python -c ""import bankrec, pdfplumber, anthropic, requests""" {
        & $Py -c "import bankrec, pdfplumber, anthropic, requests; print(' packages ok')"
    }
    Step "Copy the secrets (.env)" "API keys + team token; git-ignored, never printed" `
        "Copy-Item $Source\.env $Target\.env" {
        Copy-Item (Join-Path $Source ".env") (Join-Path $Target ".env") -Force
        & $Py -c "import pathlib; print(' keys present:', [l.split('=')[0] for l in pathlib.Path(r'$Target\.env').read_text().splitlines() if '=' in l])"
    }
    Step "Copy the data (with the model / PDF caches)" "same caches -> byte-identical results; can take a few minutes" `
        "robocopy $Source\data\valtermia $Target\data\valtermia /E /MT:16" {
        robocopy (Join-Path $Source "data\valtermia") (Join-Path $Target "data\valtermia") /E /MT:16 /NFL /NDL /NJH /NP
        if ($LASTEXITCODE -lt 8) { $global:LASTEXITCODE = 0 }   # robocopy: 0-7 = success
    }
    Step "Run the unit tests" "quick sanity of the install" "python -m pytest -q tests/bankrec" {
        Push-Location $Target; & $Py -m pytest -q tests/bankrec; Pop-Location
    }
    Step "Reproduce LIVE v16 (16-20 min)" "rebuild the file that scored 92.59" `
        "python -m bankrec.cli run --phase live --out submissions\repro_v16.csv.gz" {
        Push-Location $Target; & $Py -m bankrec.cli run --phase live --out submissions\repro_v16.csv.gz --note "repro v16"; Pop-Location
    }
    Step "Compare with the uploaded v16 file" "expect IDENTICAL" "pandas: repro_v16 == nova7nov live_v16" {
        & $Py -c "import pandas as pd,sys; a=pd.read_csv(r'$Target\submissions\repro_v16.csv.gz',dtype=str,keep_default_na=False); b=pd.read_csv(r'$Source\submissions\live_v16.csv.gz',dtype=str,keep_default_na=False); same=a.equals(b); print(' IDENTICAL' if same else ' DIFFERENT', len(a), len(b)); sys.exit(0 if same else 1)"
    }
    Step "Switch to the latest engine (main)" "next improvements live on main" `
        "git -C $Target checkout main; git -C $Target pull" {
        git -C $Target checkout -q main; git -C $Target pull -q; git -C $Target log --oneline -1
    }
}

# ------------------------------------------------------------------------------------------------ live
function SyncEngine {
    Step "Use the latest engine ($Engine)" "the clone must run exactly the engine evaluated in nova7nov; prints the commit" `
        "git -C $Target fetch --tags; git -C $Target checkout $Engine; git -C $Target pull" {
        git -C $Target fetch -q --tags; git -C $Target checkout -q $Engine
        if ($Engine -eq "main") { git -C $Target pull -q }
        git -C $Target log --oneline -1
        Push-Location $Target; & $Py -m pytest -q tests/bankrec; Pop-Location
    }
}

if ($Phase -in @("live", "all")) {
    SyncEngine
    Step "Show where we stand" "public leaderboard + our best LIVE score" "GET /api/leaderboard" {
        & $Py -c "import requests; d=requests.get('https://hackathon.usetalky.com/api/leaderboard',timeout=30).json(); [print('  %2d %-28s %s' % (r['rank'], r['team_name'], r['best_live'])) for r in d['rows'][:10]]"
    }
    Step "Build LIVE $Tag + checkpoint (y = upload, anything else = no)" `
        "builds, validates, shows the internal assessment and asks you before uploading (16-20 min)" `
        "python scripts\pipeline.py --tag $Tag --submit" {
        Push-Location $Target; & $Py scripts\pipeline.py --tag $Tag --submit; Pop-Location
    }
    Step "Per-segment report of the latest LIVE result" "where LIVE still loses lines vs TRAIN" `
        "python scripts\live_report.py" {
        Push-Location $Target; & $Py scripts\live_report.py; Pop-Location
    }
}

# ----------------------------------------------------------------------------------------------- final
if ($Phase -in @("final", "all")) {
    if ($Phase -eq "final") { SyncEngine }
    Step "Wait until FINAL is published" "polls config.json every 20 s and continues the moment it is available (unlock time is in UTC)" `
        "python scripts\wait_final.py" {
        Push-Location $Target; & $Py scripts\wait_final.py; Pop-Location
    }
    Step "FINAL: download, checksum, extract, sanity, run, gate, review, CHECKPOINT" `
        "ONE scored upload: only typing UPLOAD at the checkpoint sends it (run step ~20 min)" `
        "python scripts\final_run.py --download" {
        Push-Location $Target; & $Py scripts\final_run.py --download; Pop-Location
    }
}

# --------------------------------------------------------------------------------------------- fixloop
# Only after a FINAL run that was NOT uploaded and whose review / input profile shows a fixable gap.
# One loop = fix (Claude Code, in nova7nov) -> TRAIN eval -> hold-out -> FINAL rebuild -> checkpoint (~30 min).
if ($Phase -eq "fixloop") {
    Step "Show the agents' findings" "input profile (drift) + gate + release-manager review of the last FINAL build" `
        "type submissions\final_profile.md, submissions\final_v1.csv.gz.gate.txt" {
        Get-Content (Join-Path $Target "submissions\final_profile.md") -ErrorAction SilentlyContinue | Select-String "WARN|OK\]"
        Get-Content (Join-Path $Target "submissions\final_v1.csv.gz.gate.txt") -ErrorAction SilentlyContinue | Select-String "GATE:|^\s*\["
        Get-ChildItem (Join-Path $Target "autoresearch\analyst") -Filter "*final_review*" -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime | Select-Object -Last 1 | ForEach-Object { Get-Content $_.FullName -TotalCount 60 }
        $global:LASTEXITCODE = 0
    }
    Step "Delegate the fix" "paste the findings to Claude Code; it makes ONE change in nova7nov, simulates it, runs the TRAIN dev eval (keep / auto-revert) and pushes main only if kept" `
        "(Claude Code) -- press Enter here once it says the fix is pushed" { $global:LASTEXITCODE = 0 }
    SyncEngine
    Step "Audit: hold-out month, measured in your clone" "measure-only (no revert); must not fall below 93.18 on 2025-05 (~4 min)" `
        "python scripts\autoresearch.py holdout" {
        Push-Location $Target; & $Py scripts\autoresearch.py holdout; Pop-Location
    }
    Step "Rebuild FINAL + gate + review + CHECKPOINT" "same archive, new engine; only typing UPLOAD sends it (~20 min)" `
        "python scripts\final_run.py" {
        Push-Location $Target; & $Py scripts\final_run.py; Pop-Location
    }
}

Write-Host ""
Say "Runbook finished (phase=$Phase)." "Green"
if (-not $DryRun) { try { Stop-Transcript | Out-Null } catch {} }

