# Starts the backend (:8008) and frontend (:3008) in their own windows, with settings from .env.
#   powershell -ExecutionPolicy Bypass -File .\start-dev.ps1
$root = $PSScriptRoot
$envFile = Join-Path $root '.env'
if (-not (Test-Path $envFile)) { Write-Error "Missing .env - copy .env.example to .env and set PRIME_DB_PASSWORD."; exit 1 }
Get-Content $envFile | Where-Object { $_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$' } | ForEach-Object {
    [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim(), 'Process')
}
if (-not $env:PRIME_DB_ENGINE) { $env:PRIME_DB_ENGINE = 'mysql' }

# Child windows inherit this process's environment.
Start-Process powershell -WorkingDirectory (Join-Path $root 'backend') -ArgumentList '-NoExit', '-Command', '.venv\Scripts\python manage.py runserver 8008'
Start-Process powershell -WorkingDirectory (Join-Path $root 'frontend') -ArgumentList '-NoExit', '-Command', 'npm run dev'
Write-Host 'Backend: http://localhost:8008   Frontend: http://localhost:3008'
