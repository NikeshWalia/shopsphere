<#
.SYNOPSIS
  Start, stop or inspect ShopSphere running directly on this machine (no Docker).

.DESCRIPTION
  Docker Compose is the documented way to run ShopSphere and the one CI proves on
  every push. This script exists for the other case: running the services
  natively, which is faster to iterate on and is what you want when developing
  the test suites rather than demonstrating the stack.

  It starts four things in dependency order - PostgreSQL, the payment provider,
  the API, then the storefront - and waits for each to answer before starting
  the next, so there is nothing to sleep for.

.PARAMETER Action
  start | stop | status

.EXAMPLE
  .\scripts\local-stack.ps1 start
  .\scripts\local-stack.ps1 status
  .\scripts\local-stack.ps1 stop
#>
param(
  [ValidateSet('start', 'stop', 'status')]
  [string]$Action = 'start'
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot

# Where the database lives. SHOPSPHERE_DATA (or the two more specific
# variables below) overrides this. The default sits in your home directory
# rather than the repo or %TEMP%: data under %TEMP% will eventually be
# deleted by Windows, which is exactly the failure this script avoids.
$DataHome = if ($env:SHOPSPHERE_DATA) { $env:SHOPSPHERE_DATA } else { Join-Path $HOME 'shopsphere-data' }
$PgRoot = if ($env:SHOPSPHERE_PGROOT) { $env:SHOPSPHERE_PGROOT } else { Join-Path $DataHome 'pgroot\pgsql\bin' }
$PgData = if ($env:SHOPSPHERE_PGDATA) { $env:SHOPSPHERE_PGDATA } else { Join-Path $DataHome 'pgdata' }
$PgLog  = Join-Path (Split-Path -Parent $PgData) 'pg.log'
$PgPort = if ($env:SHOPSPHERE_PGPORT) { $env:SHOPSPHERE_PGPORT } else { '5433' }
$Py     = Join-Path $Root '.venv\Scripts\python.exe'

function Test-Port([int]$Port) {
  try {
    $c = New-Object Net.Sockets.TcpClient
    $c.Connect('127.0.0.1', $Port); $c.Close(); return $true
  } catch { return $false }
}

function Show-Status {
  $rows = @(
    @{ Name = 'PostgreSQL'; Port = [int]$PgPort; Url = "127.0.0.1:$PgPort" }
    @{ Name = 'Payment provider'; Port = 9100; Url = 'http://127.0.0.1:9100/docs' }
    @{ Name = 'API'; Port = 8000; Url = 'http://127.0.0.1:8000/docs' }
    @{ Name = 'Storefront'; Port = 5173; Url = 'http://127.0.0.1:5173' }
  )
  foreach ($r in $rows) {
    $up = if (Test-Port $r.Port) { 'UP  ' } else { 'down' }
    '{0}  {1,-18} {2}' -f $up, $r.Name, $r.Url | Write-Host
  }
}

switch ($Action) {

  'status' { Show-Status }

  'stop' {
    Write-Host 'Stopping application services...'
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='node.exe'" |
      Where-Object { $_.CommandLine -match 'uvicorn|vite' } |
      ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

    Write-Host 'Stopping PostgreSQL (clean shutdown, flushes to disk)...'
    & (Join-Path $PgRoot 'pg_ctl.exe') -D $PgData -m fast -w -t 120 stop
    Write-Host 'Stopped.'
  }

  'start' {
    if (-not (Test-Path $Py)) { throw "No virtualenv at $Py. Run: python -m venv .venv; make install-all" }

    if (Test-Port ([int]$PgPort)) {
      Write-Host "PostgreSQL already listening on $PgPort."
    } else {
      Write-Host "Starting PostgreSQL on port $PgPort..."
      & (Join-Path $PgRoot 'pg_ctl.exe') -D $PgData -o "-p $PgPort" -l $PgLog -w -t 300 start
    }

    # Each service gets its own window so its log stays readable and it can be
    # stopped individually.
    if (-not (Test-Port 9100)) {
      Write-Host 'Starting payment provider on 9100...'
      Start-Process -WindowStyle Minimized -FilePath $Py `
        -ArgumentList '-m', 'uvicorn', 'app.main:app', '--port', '9100' `
        -WorkingDirectory (Join-Path $Root 'payment-mock')
    }

    if (-not (Test-Port 8000)) {
      Write-Host 'Starting API on 8000...'
      $env:PYTHONPATH = '.'
      Start-Process -WindowStyle Minimized -FilePath $Py `
        -ArgumentList '-m', 'uvicorn', 'app.main:app', '--port', '8000' `
        -WorkingDirectory (Join-Path $Root 'backend')
    }

    if (-not (Test-Port 5173)) {
      Write-Host 'Starting storefront on 5173...'
      Start-Process -WindowStyle Minimized -FilePath 'cmd.exe' `
        -ArgumentList '/c', 'npm', 'run', 'dev' `
        -WorkingDirectory (Join-Path $Root 'frontend')
    }

    Write-Host ''
    & $Py (Join-Path $Root 'scripts\wait_for_stack.py') `
      --api http://127.0.0.1:8000 --payment http://127.0.0.1:9100 --ui http://127.0.0.1:5173
    Write-Host ''
    Show-Status
  }
}
