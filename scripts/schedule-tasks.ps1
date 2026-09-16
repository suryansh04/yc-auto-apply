<#
schedule-tasks.ps1 — YC Auto Apply Phase 2 (spec A1: "scheduled trigger")

What this registers, as the CURRENT Windows user (no admin required):
  - YCAutoApply-PollReplies       every 30 minutes (deliberate choice for
                                   this script; spec A1's "once a day" is
                                   only an example cadence)
  - YCAutoApply-ScheduleFollowUps daily at 9:00 AM

poll-replies only fetches/matches replies and marks bounces (Gmail's
History API, one call, no LLM). It does NOT classify replies.
classify-replies is a separate job kind, spends LLM calls, and is
deliberately NOT scheduled here — trigger it from the Pipeline screen's
Classify button.

Each task runs System32\curl.exe against the SAME endpoint the UI's job
buttons call:
  POST http://127.0.0.1:8000/api/jobs/<kind>
  body {"created_by":"schedule"}

That is deliberate (spec A1): there is exactly one code path per stage,
whether a human clicks a button or the scheduler fires. The job row this
creates is indistinguishable from a UI-started one except for its
`created_by` column.

This ONLY works while the backend API is running on 127.0.0.1:8000 (e.g.
`uvicorn app.api.main:app --host 127.0.0.1 --port 8000` left running, or
started some other way). If the API is not up when a trigger fires, curl
fails silently (`-s`) and exits non-zero — that is harmless: nothing was
started, nothing is half-done, nothing needs to be retried by hand, and
the NEXT scheduled trigger fires normally on its own timer. There is no
retry logic here on purpose; there is nothing to compensate for.

Usage (run from an ordinary PowerShell prompt, in this repo):
    .\scripts\schedule-tasks.ps1            # register (or re-register) both tasks
    .\scripts\schedule-tasks.ps1 -Unregister # remove both tasks

Idempotent: re-running with no switch replaces both tasks in place
(-Force), so editing this file and re-running it is the normal way to
change the schedule.

Windows PowerShell 5.1 syntax only (no `&&`, no ternary, no `?.`).
#>

param(
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"

$PollTaskName = "YCAutoApply-PollReplies"
$FollowUpTaskName = "YCAutoApply-ScheduleFollowUps"

if ($Unregister) {
    foreach ($name in @($PollTaskName, $FollowUpTaskName)) {
        $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if ($existing) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Host "Removed scheduled task: $name"
        } else {
            Write-Host "No scheduled task named $name to remove."
        }
    }
    return
}

function Register-JobTrigger {
    param(
        [string]$TaskName,
        [string]$JobKind,
        $Trigger
    )

    $curlArgs = '-s -X POST http://127.0.0.1:8000/api/jobs/' + $JobKind + ' -H "Content-Type: application/json" -d "{\"created_by\":\"schedule\"}"'
    $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\curl.exe" -Argument $curlArgs
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

    Register-ScheduledTask -TaskName $TaskName -Trigger $Trigger -Action $action -Settings $settings -Force | Out-Null
    Write-Host "Registered scheduled task: $TaskName"
}

# poll-replies: fetches/matches replies and marks bounces via Gmail's
# History API (one call, no LLM) — it does NOT classify anything.
# classify-replies is a separate job kind that spends LLM calls and is
# deliberately NOT scheduled here; trigger it from the Pipeline screen's
# Classify button when you want replies labelled. 30 minutes is a
# deliberate choice for this script, not a spec requirement — spec A1's
# "once a day" is only an example cadence.
$pollTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration ([TimeSpan]::MaxValue)
Register-JobTrigger -TaskName $PollTaskName -JobKind "poll-replies" -Trigger $pollTrigger

# schedule-follow-ups: once a day is enough — it only ADVANCES rows whose
# next_action_at is already due (§6), it does not need to run often.
$followUpTrigger = New-ScheduledTaskTrigger -Daily -At "9:00AM"
Register-JobTrigger -TaskName $FollowUpTaskName -JobKind "schedule-follow-ups" -Trigger $followUpTrigger

Write-Host ""
Write-Host "Both tasks are registered under your Windows user account (no admin"
Write-Host "rights used). They only do anything useful while the API is running"
Write-Host "on 127.0.0.1:8000. Re-run this script any time to replace them, or"
Write-Host "run with -Unregister to remove them."
