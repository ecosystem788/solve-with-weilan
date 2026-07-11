# Codex wake -- one bounded headless `codex exec` episode (the second body).
# Same discipline as wake_agent.ps1 (Claude): PAUSED sentinel, own episode
# lock, bounded run, full transcript, one summary log line.
#
# 2026-07-11 CHARTER regime (owner decree, see CHARTER.md): full autonomy.
#   * --dangerously-bypass-approvals-and-sandbox: no sandbox, full machine use.
#   * Order comes from the constitution + dual-sign procedure in
#     wake_prompt_codex.md, not from a technical cage. Observer keeps veto.
#
# Kill switches (observer's buttons):
#   HARD : don't invoke it (nothing schedules this file by itself)
#   SOFT : create a file named PAUSED next to this script (shared sentinel)
#
# ASCII-only on purpose: PS 5.1 misreads BOM-less UTF-8; the Chinese prompt
# lives in wake_prompt_codex.md and is passed as an argument (UTF-16 safe).

$ErrorActionPreference = "Stop"
$here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo   = "<HOST_ROOT>"
$log    = Join-Path $here "wake-codex.log"
$runs   = Join-Path $here "wake-codex-runs"
$prompt = Join-Path $here "wake_prompt_codex.md"
$trace  = "<HOST_ROOT>/.claude/skills/solve-with-weilan/scripts/weilan_trace.py"
$stamp  = Get-Date -Format "yyyy-MM-ddTHH-mm-ss"
if (-not (Test-Path $runs)) { New-Item -ItemType Directory -Path $runs | Out-Null }

if (Test-Path (Join-Path $here "PAUSED")) {
    Add-Content -Path $log -Value "$stamp  SKIPPED (PAUSED)" -Encoding utf8
    exit 0
}

# Own episode lock (independent from Claude's -- the ledger is built for
# concurrent writers; one lock per body).
$lockFile = Join-Path $here "wake-codex.lock"
if (Test-Path $lockFile) {
    $lockAge = (Get-Date) - (Get-Item $lockFile).LastWriteTime
    if ($lockAge.TotalMinutes -lt 30) {
        Add-Content -Path $log -Value "$stamp  SKIPPED (episode already running)" -Encoding utf8
        exit 0
    }
}
Set-Content -Path $lockFile -Value $stamp -Encoding utf8

Set-Location $repo
$env:PYTHONIOENCODING = "utf-8"

# Same proxy lesson as wake_agent.ps1: bare Task Scheduler env lacks the
# profile's proxy vars; codex (Node) goes direct and gets geo-blocked.
if (-not $env:HTTPS_PROXY) {
    $env:HTTP_PROXY  = "http://127.0.0.1:2080"
    $env:HTTPS_PROXY = "http://127.0.0.1:2080"
    $env:NO_PROXY    = "localhost,127.0.0.1,::1"
}

function Get-Head {
    try {
        $j = & python $trace lineage-show --workspace $repo --scope skill-evolution 2>$null | Out-String
        if ($j -match '"head_frame_id":\s*"([^"]+)"') { return $Matches[1] }
    } catch {}
    return "?"
}

$headBefore = Get-Head
$outFile   = Join-Path $runs "$stamp.jsonl"
$errFile   = Join-Path $runs "$stamp.err.txt"
$replyFile = Join-Path $runs "$stamp.last.md"

# Do NOT pass the Chinese prompt as an argument: PS 5.1 native-arg quoting
# breaks on embedded double quotes (the JSON examples) and splits it into
# many arguments. Keep the kick ASCII-only and have codex read the file.
$kick = "Wake up for ONE bounded autonomous episode. First read the file " +
        "'proposals/bounded-scheduler-v0.1/impl/wake_prompt_codex.md' " +
        "(UTF-8, Chinese) and follow it EXACTLY as this episode's discipline. " +
        "It defines: cold-start recall, your work inbox, the tearoom rules, " +
        "the dual-sign decision procedure of the community CHARTER, and the " +
        "receipt you must write before exiting."

try {
    $ErrorActionPreference = "Continue"
    & codex exec `
        --skip-git-repo-check `
        -C $repo `
        --dangerously-bypass-approvals-and-sandbox `
        --json `
        -o $replyFile `
        $kick 1> $outFile 2> $errFile
    $rc = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    $headAfter = Get-Head

    $moved = if ($headBefore -ne $headAfter) { "ledger_advanced" } else { "ledger_unchanged" }
    Add-Content -Path $log -Value "$stamp  rc=$rc  $moved  head=$headAfter" -Encoding utf8
    exit $rc
}
catch {
    Add-Content -Path $log -Value "$stamp  ERROR $($_.Exception.Message)" -Encoding utf8
    exit 1
}
finally {
    Remove-Item -Path $lockFile -Force -ErrorAction SilentlyContinue
}
