' Hidden launcher for run_wake_cron.ps1 (2026-07-11, owner request: the 1-min
' task firings flashed a console window in the interactive session).
' Task Scheduler starts wscript.exe (GUI subsystem -> no console is created);
' this runs the PowerShell heartbeat with window style 0 (hidden) and WAITS,
' so the task's ExecutionTimeLimit and IgnoreNew semantics still cover the
' whole episode. Exit code is propagated unchanged.
' Revert: point the task action back to
'   powershell.exe -NoProfile -ExecutionPolicy Bypass -File run_wake_cron.ps1
Dim fso, sh, here, cmd, rc
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & here & "\run_wake_cron.ps1"""
rc = sh.Run(cmd, 0, True)
WScript.Quit rc
