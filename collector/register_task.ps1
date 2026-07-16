# Регистрирует Задачу планировщика, запускающую импортёр периодически
# с наивысшими правами (без UAC-промпта). Запускать в PowerShell ОТ АДМИНА.
#
#   .\register_task.ps1                       # каждые 5 минут
#   .\register_task.ps1 -IntervalMinutes 10
#
param(
    [int]$IntervalMinutes = 5,
    [string]$TaskName = "WinlogCollectorImport"
)

$here = Split-Path -Parent $MyInvocation.MyCommand.Definition
$bat = Join-Path $here "run_importer.bat"

if (-not (Test-Path $bat)) {
    throw "Не найден $bat — положите run_importer.bat рядом с этим скриптом."
}

$action = New-ScheduledTaskAction -Execute $bat
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
# SYSTEM + Highest: уже «администратор», поэтому self-elevate в скрипте ничего
# не запрашивает. Можно заменить на доменную учётку при необходимости.
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force

Write-Host "Задача '$TaskName' зарегистрирована: каждые $IntervalMinutes мин."
