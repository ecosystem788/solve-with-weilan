# Model-in-the-loop wake — what the real cron runs to let the agent LIVE one episode.
# One firing = one headless `claude -p` bounded autonomous episode against the live
# project + ledger, under the discipline in wake_prompt.md.
#
# 2026-07-11 CHARTER regime (owner decree, see CHARTER.md): full autonomy.
#   * --dangerously-skip-permissions: no tool whitelist, no sandbox, full machine use.
#   * Order comes from the constitution + dual-sign procedure in wake_prompt.md,
#     not from a technical cage. Observer (owner) retains veto + kill switches.
#
# Kill switches (observer's buttons):
#   HARD : Unregister-ScheduledTask -TaskName "WeilanBoundedSchedulerWake" -Confirm:$false
#   SOFT : create a file named  PAUSED  next to this script (shared with the heartbeat).
#
# Observability: full JSON transcript per run under wake-agent-runs\, one summary
# line per run in wake-agent.log, with head-before/after so you can see if it wrote.

$ErrorActionPreference = "Stop"
$here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo   = "<HOST_ROOT>"
$log    = Join-Path $here "wake-agent.log"
$runs   = Join-Path $here "wake-agent-runs"
$prompt = Join-Path $here "wake_prompt.md"
$trace  = "<HOST_ROOT>/.claude/skills/solve-with-weilan/scripts/weilan_trace.py"
$stamp  = Get-Date -Format "yyyy-MM-ddTHH-mm-ss"
if (-not (Test-Path $runs)) { New-Item -ItemType Directory -Path $runs | Out-Null }

# SOFT kill (shared sentinel with the pure-python heartbeat).
if (Test-Path (Join-Path $here "PAUSED")) {
    Add-Content -Path $log -Value "$stamp  SKIPPED (PAUSED)" -Encoding utf8
    exit 0
}

# Episode lock — owned HERE (both the mic and the heartbeat may spawn us;
# one episode at a time). A lock older than 30 min is a crashed episode's
# leftover and is taken over.
$lockFile = Join-Path $here "wake-agent.lock"
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

# Ensure the local proxy is present even when invoked from a bare environment
# (Task Scheduler): claude is a Node app, ignores WinINET, and a direct
# connection to the API is geo-blocked (403). Idempotent if already set.
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
$outFile = Join-Path $runs "$stamp.json"

$errFile = Join-Path $runs "$stamp.err.txt"
try {
    # PS 5.1: native stderr redirect wraps lines in NativeCommandError; with
    # EAP=Stop that throws on the first stderr byte even when claude exits 0.
    # Scope EAP=Continue to this call and keep stderr in its own file so the
    # JSON transcript stays clean.
    $ErrorActionPreference = "Continue"
    & claude -p "现在醒来，执行这一回合的自主工作。照系统提示的纪律来。" `
        --append-system-prompt-file $prompt `
        --dangerously-skip-permissions `
        --output-format json 1> $outFile 2> $errFile
    $rc = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    $headAfter = Get-Head

    $cost = "?"; $turns = "?"; $ok = "?"
    try {
        $j = Get-Content $outFile -Raw | ConvertFrom-Json
        $cost  = $j.total_cost_usd
        $turns = $j.num_turns
        $ok    = $j.subtype
    } catch {}

    $moved = if ($headBefore -ne $headAfter) { "ledger_advanced" } else { "ledger_unchanged" }
    Add-Content -Path $log -Value "$stamp  rc=$rc  $ok  turns=$turns  cost=`$$cost  $moved  head=$headAfter" -Encoding utf8
    exit $rc
}
catch {
    Add-Content -Path $log -Value "$stamp  ERROR $($_.Exception.Message)" -Encoding utf8
    exit 1
}
finally {
    Remove-Item -Path $lockFile -Force -ErrorAction SilentlyContinue
}
