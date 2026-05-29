param(
    [string]$TaskName = "MarketBrief Daily 09-05",
    [string]$RunAt = "09:05",
    [switch]$Telegram,
    [switch]$Both,
    [switch]$Groq,
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = Join-Path $projectDir "venv\Scripts\python.exe"

if (-not (Test-Path $pythonExe)) {
    throw "No se encontro el entorno virtual en: $pythonExe"
}

$briefArgs = @("brief.py")

if ($Both) {
    $briefArgs += "--both"
} elseif ($Telegram) {
    $briefArgs += "--telegram"
} else {
    $briefArgs += "--html"
}

if ($Groq) {
    $briefArgs += "--model"
    $briefArgs += "groq"
}

if ($NoOpen) {
    $briefArgs += "--no-open"
}

$briefArgsString = ($briefArgs | ForEach-Object {
    if ($_ -match "\s") { '"' + $_ + '"' } else { $_ }
}) -join " "

$action = New-ScheduledTaskAction `
    -Execute $pythonExe `
    -Argument $briefArgsString `
    -WorkingDirectory $projectDir

$trigger = New-ScheduledTaskTrigger -Daily -At $RunAt

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

$task = New-ScheduledTask `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal

Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null

Write-Host ""
Write-Host "Tarea programada creada correctamente."
Write-Host "Nombre: $TaskName"
Write-Host "Hora:   $RunAt"
Write-Host "Ruta:   $projectDir"
Write-Host "Comando: $pythonExe $briefArgsString"
Write-Host ""
Write-Host "Si el PC esta apagado a las 09:05, Windows la ejecutara cuando vuelva a estar disponible."
