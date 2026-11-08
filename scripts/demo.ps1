# Start the OffsAid demo on this Windows laptop: local Postgres + the app, chat at http://127.0.0.1:8000/demo
#
#   .\scripts\demo.ps1            start (Postgres if needed, then the app)
#   .\scripts\demo.ps1 -Reset     also wipe the demo cases and reload the 10 simulated ones (do this before presenting)
#
# Uses the throwaway Postgres in $HOME\offsaid_pgtest (port 55432, database offsaid_dev). OFFSAID_HASH_KEY,
# ADMIN_PASSWORD and ANTHROPIC_API_KEY come from .env. KEFWA review page: http://127.0.0.1:8000/admin/review
param([switch]$Reset, [int]$Port = 8000)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$pgBin = Join-Path $HOME "offsaid_pgtest\env\Library\bin"
$pgData = Join-Path $HOME "offsaid_pgtest\data"
if (-not (Test-Path "$pgBin\pg_ctl.exe")) { throw "Postgres not found in $pgBin" }

& "$pgBin\pg_ctl.exe" -D $pgData status *> $null
if ($LASTEXITCODE -ne 0) {
    & "$pgBin\pg_ctl.exe" -D $pgData -o "-p 55432 -c listen_addresses=localhost" -l (Join-Path $HOME "offsaid_pgtest\pg.log") start
}
$env:DATABASE_URL = "postgresql+psycopg://postgres@localhost:55432/offsaid_dev"

if ($Reset) {
    & "$pgBin\psql.exe" -h localhost -p 55432 -U postgres -q -c "drop database if exists offsaid_dev with (force)" -c "create database offsaid_dev"
}
& .venv\Scripts\python.exe -W ignore -m scripts.migrate
if ($Reset) { & .venv\Scripts\python.exe -W ignore -m scripts.seed_linking data/linking_seed/seed_cases.json }

Write-Host ""
Write-Host "Chat demo:          http://127.0.0.1:$Port/demo"
Write-Host "KEFWA review page:  http://127.0.0.1:$Port/admin/review  (login from ADMIN_USERNAME / ADMIN_PASSWORD in .env)"
Write-Host ""
& .venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port $Port
