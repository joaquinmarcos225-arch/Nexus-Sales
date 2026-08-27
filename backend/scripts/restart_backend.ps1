# Reinicia un unico backend en 8002 (Windows)
$port = 8002

function Stop-BackendPython {
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object {
      $cmd = $_.CommandLine
      ($cmd -like "*uvicorn*" -and $cmd -like "*$port*") -or
      ($cmd -like "*multiprocessing.spawn*" -and $cmd -like "*spawn_main*")
    } |
    ForEach-Object {
      Write-Host "Stopping PID $($_.ProcessId)"
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

Stop-BackendPython
Start-Sleep -Seconds 2
# Segunda pasada: workers huérfanos que siguen escuchando en el puerto
Stop-BackendPython
Start-Sleep -Seconds 1
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
Write-Host "Starting backend on $port..."
Start-Process -NoNewWindow -WorkingDirectory $root -FilePath python -ArgumentList @(
  "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$port", "--reload"
)
