param(
    [string]$TaskName = "MarketBrief Daily 09-05"
)

$ErrorActionPreference = "Stop"

try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Tarea eliminada: $TaskName"
} catch {
    Write-Host "No se pudo eliminar la tarea '$TaskName' o no existe."
    throw
}
