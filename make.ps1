# ТехОценка — аналог Makefile для Windows PowerShell: .\make.ps1 up | test | ...
param([Parameter(Position = 0)][string]$Target = "help")

# Не "Stop": docker/npm пишут прогресс в stderr, PowerShell 5.1 счёл бы это ошибкой.
# Успех нативных команд определяется по $LASTEXITCODE в Invoke-Step.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

function Invoke-Step([string]$cmd) {
    Write-Host "> $cmd" -ForegroundColor DarkGray
    Invoke-Expression $cmd
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Ensure-Env { if (-not (Test-Path .env)) { Copy-Item .env.example .env } }

$apiRun = "docker compose run --rm api"  # нужен Postgres для тестовой БД
$webRun = "docker compose run --rm --no-deps web"

switch ($Target) {
    "up"               { Ensure-Env; Invoke-Step "docker compose up -d --build --wait" }
    "down"             { Invoke-Step "docker compose down" }
    "logs"             { Invoke-Step "docker compose logs -f --tail=200" }
    "ps"               { Invoke-Step "docker compose ps" }
    "migrate"          { Invoke-Step "docker compose exec api alembic upgrade head" }
    "seed"             { Invoke-Step "docker compose exec api python -m app.seed" }
    "test"             { Ensure-Env
                         Invoke-Step "$apiRun sh -c 'ruff check . && pytest'"
                         Invoke-Step "$webRun sh -c 'npm run lint && npm run typecheck'" }
    "test-backend"     { Ensure-Env; Invoke-Step "$apiRun sh -c 'ruff check . && pytest'" }
    "test-frontend"    { Ensure-Env; Invoke-Step "$webRun sh -c 'npm run lint && npm run typecheck'" }
    "test-integration" { Invoke-Step "docker compose exec -e RUN_INTEGRATION=1 api pytest tests/integration" }
    "e2e"              { Push-Location frontend; try { Invoke-Step "npx playwright test" } finally { Pop-Location } }
    "fmt"              { Invoke-Step "$apiRun sh -c 'ruff format . && ruff check --fix .'" }
    "health"           { (Invoke-WebRequest -UseBasicParsing http://localhost:8000/health).Content }
    default            { Write-Host "up | down | logs | ps | migrate | seed | test | test-integration | e2e | fmt | health" }
}
