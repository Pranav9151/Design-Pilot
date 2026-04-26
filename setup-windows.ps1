# DesignPilot MECH — Windows Setup Script (PowerShell)
# Run from the project root: .\setup-windows.ps1
# Requires: Python 3.12, Docker Desktop, Node.js 18+

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  DesignPilot MECH — Windows Setup" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# ── Check prerequisites ──────────────────────────────────────────────
Write-Host "Checking prerequisites..." -ForegroundColor Yellow

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "ERROR: Python not found. Install Python 3.12 from python.org" -ForegroundColor Red
    exit 1
}
$pyver = python --version 2>&1
Write-Host "  OK: $pyver" -ForegroundColor Green

$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) {
    Write-Host "ERROR: Docker not found. Install Docker Desktop from docker.com" -ForegroundColor Red
    exit 1
}
Write-Host "  OK: Docker found" -ForegroundColor Green

$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) {
    Write-Host "WARNING: Node.js not found. Frontend setup will be skipped." -ForegroundColor Yellow
    $hasNode = $false
} else {
    $nodever = node --version
    Write-Host "  OK: Node $nodever" -ForegroundColor Green
    $hasNode = $true
}

# ── Backend setup ────────────────────────────────────────────────────
Write-Host ""
Write-Host "Setting up backend..." -ForegroundColor Yellow

# Create venv
if (-not (Test-Path ".venv")) {
    python -m venv .venv
    Write-Host "  Created .venv" -ForegroundColor Green
} else {
    Write-Host "  .venv already exists" -ForegroundColor Green
}

# Install deps
& .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
& .venv\Scripts\pip.exe install --quiet -e ".[dev]"
Write-Host "  Dependencies installed" -ForegroundColor Green

# Copy .env
if (-not (Test-Path ".env")) {
    Copy-Item .env.example .env
    Write-Host "  Created .env from .env.example" -ForegroundColor Green
    Write-Host "  NOTE: Edit .env and set ANTHROPIC_API_KEY for real designs" -ForegroundColor Yellow
} else {
    Write-Host "  .env already exists" -ForegroundColor Green
}

# ── Database setup ───────────────────────────────────────────────────
Write-Host ""
Write-Host "Starting Docker services..." -ForegroundColor Yellow
docker compose up -d
Write-Host "  Waiting for Postgres to be ready..." -ForegroundColor Yellow
Start-Sleep 5

# Run migrations
Write-Host "  Running Alembic migrations..." -ForegroundColor Yellow
& .venv\Scripts\alembic.exe upgrade head
Write-Host "  Running seed..." -ForegroundColor Yellow
& .venv\Scripts\python.exe -m scripts.seed_materials

# ── Frontend setup ───────────────────────────────────────────────────
if ($hasNode) {
    Write-Host ""
    Write-Host "Setting up frontend..." -ForegroundColor Yellow
    Set-Location frontend
    if (-not (Test-Path ".env")) {
        Copy-Item .env.example .env
        Write-Host "  Created frontend/.env" -ForegroundColor Green
        Write-Host "  NOTE: Edit frontend/.env and set VITE_SUPABASE_URL + VITE_SUPABASE_ANON_KEY" -ForegroundColor Yellow
    }
    npm install --silent
    Write-Host "  Frontend dependencies installed" -ForegroundColor Green
    Set-Location ..
}

# ── Summary ─────────────────────────────────────────────────────────
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  Setup complete!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "To start the backend:" -ForegroundColor Cyan
Write-Host "  .venv\Scripts\uvicorn.exe app.main:app --reload --port 8000"
Write-Host ""
Write-Host "Backend URLs:" -ForegroundColor Cyan
Write-Host "  http://localhost:8000/health      <- liveness"
Write-Host "  http://localhost:8000/docs        <- Swagger UI (all endpoints)"
Write-Host "  http://localhost:8000/api/v1/health"
Write-Host ""
Write-Host "Mint a local dev token (no Supabase needed):" -ForegroundColor Cyan
Write-Host "  .venv\Scripts\python.exe -m scripts.mint_dev_token"
Write-Host ""
if ($hasNode) {
    Write-Host "To start the frontend:" -ForegroundColor Cyan
    Write-Host "  cd frontend && npm run dev"
    Write-Host "  -> http://localhost:5173"
    Write-Host ""
}
Write-Host "Run tests:" -ForegroundColor Cyan
Write-Host "  .venv\Scripts\pytest.exe tests\unit\ tests\engineering\ tests\security\ -q"
Write-Host ""
