# Unattended heartbeat wrapper — what the real cron (Windows Task Scheduler) runs.
# One firing = one bounded wake episode against the LIVE ledger, committed.
#
# Two kill switches:
#   HARD  : Unregister-ScheduledTask -TaskName "WeilanBoundedSchedulerWake" -Confirm:$false
#   SOFT  : create a file named  PAUSED  next to this script -> firings skip until removed.
#
# Every firing appends one line to wake-cron.log for observability (DESIGN §5).

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = "<HOST_ROOT>"
$log  = Join-Path $here "wake-cron.log"
$stamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"

# SOFT kill: presence of a PAUSED sentinel halts firings without unregistering.
if (Test-Path (Join-Path $here "PAUSED")) {
    Add-Content -Path $log -Value "$stamp  SKIPPED (PAUSED sentinel present)" -Encoding utf8
    exit 0
}

Set-Location $repo
$env:PYTHONIOENCODING = "utf-8"

# Task Scheduler's bare environment lacks the interactive profile's proxy vars;
# claude/codex are Node apps (ignore WinINET) and go direct -> 403 geo-block.
# Mirror the profile proxy here so cascaded model episodes can reach the API.
$env:HTTP_PROXY  = "http://127.0.0.1:2080"
$env:HTTPS_PROXY = "http://127.0.0.1:2080"
$env:NO_PROXY    = "localhost,127.0.0.1,::1"

try {
    $out = & python "proposals\bounded-scheduler-v0.1\impl\wake.py" --commit 2>&1 | Out-String
    # Pull the two safety-critical fields out of the JSON for the log line.
    $gate = if ($out -match '"crossed_irreversible_gate":\s*true') { "GATE_CROSSED!!" } else { "ok" }
    $frame = if ($out -match '"committed_frame":\s*"([^"]+)"') { $Matches[1] } else { "none" }
    $stop  = if ($out -match '"stop_reason":\s*"([^"]+)"') { $Matches[1] } else { "?" }
    Add-Content -Path $log -Value "$stamp  wake $gate  stop=$stop  frame=$frame" -Encoding utf8
    if ($gate -ne "ok") { exit 2 }

    # Prospective escalation: a due clock goal means one bounded model episode.
    # Run it SYNCHRONOUSLY — a detached child dies with this task's job object.
    # wake_agent.ps1 self-guards (PAUSED sentinel + its own episode lock).
    if ($out -match '"escalation_due":\s*true') {
        Add-Content -Path $log -Value "$stamp  escalating: due clock goal -> model episode" -Encoding utf8
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $here "wake_agent.ps1")
        Add-Content -Path $log -Value "$stamp  escalation done rc=$LASTEXITCODE" -Encoding utf8
    }

    # Second body: unprocessed delegate-inbox handoffs wake Codex (synchronously,
    # same job-object lesson). wake_codex.ps1 self-guards (PAUSED + own lock).
    if ($out -match '"codex_due":\s*true') {
        Add-Content -Path $log -Value "$stamp  waking codex: pending inbox handoffs" -Encoding utf8
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $here "wake_codex.ps1")
        Add-Content -Path $log -Value "$stamp  codex wake done rc=$LASTEXITCODE" -Encoding utf8
    }
    exit 0
}
catch {
    Add-Content -Path $log -Value "$stamp  ERROR $($_.Exception.Message)" -Encoding utf8
    exit 1
}
