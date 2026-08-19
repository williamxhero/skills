---
name: sleep-after-task
description: Schedule Windows sleep after work is fully finished. Use when the user asks for task completion followed by sleep, such as "完成任务后进入睡眠", "完成后睡眠", "完成后休眠", or equivalent requests that mean the machine should sleep after the final response.
---

# Sleep After Task

After all requested work is complete and the final response has been prepared, schedule "This Computer" Windows sleep with the exact command below. Use this skill only for requests that mean "finish the task, then let the machine sleep."

## Workflow

1. Finish all requested work first.
2. Send the final user-facing response.
3. Immediately after that, execute the exact command below through the shell tool.
4. Do not shorten the delay to 45 seconds. Use 180 seconds.

## Command

```powershell
Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @(
    '-NoProfile',
    '-Command',
    'Start-Sleep -Seconds 180; Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Application]::SetSuspendState([System.Windows.Forms.PowerState]::Suspend, $false, $true)'
)
```

## Rules

- Match these trigger phrases and close variants:
  - `完成任务后进入睡眠`
  - `完成后睡眠`
  - `完成后休眠`
  - `全部完成后睡眠`
- If the user gives a different explicit delay or a different power action, follow the user's latest explicit instruction instead of this default.
- If the user later cancels sleep, do not run the command.
- 只休眠本开发机（本机），不要休眠其他任何电脑。
