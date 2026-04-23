#!/usr/bin/env bash
# DesignPilot MECH — Windows Git Bash Setup Script
# Usage: bash setup-windows.sh
# Run from project root.

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

echo ""
echo -e "${CYAN}============================================${NC}"
echo -e "${CYAN}  DesignPilot MECH — Windows Git Bash Setup${NC}"
echo -e "${CYAN}============================================${NC}"
echo ""

# ── Detect Windows venv path ──────────────────────────────────────────
ACTIVATE=""
if [ -f ".venv/Scripts/activate" ]; then
    ACTIVATE=".venv/Scripts/activate"
elif [ -f ".venv/bin/activate" ]; then
    ACTIVATE=".venv/bin/activate"
fi

# ── Create venv if needed ─────────────────────────────────────────────
if [ ! -d ".venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    python -m venv .venv
fi

ACTIVATE=""
if [ -f ".venv/Scripts/activate" ]; then
    ACTIVATE=".venv/Scripts/activate"
elif [ -f ".venv/bin/activate" ]; then
    ACTIVATE=".venv/bin/activate"
fi

if [ -z "$ACTIVATE" ]; then
    echo -e "${RED}ERROR: Could not find venv activate script${NC}"
    exit 1
fi

echo -e "${GREEN}  Activating: $ACTIVATE${NC}"
# shellcheck disable=SC1090
source "$ACTIVATE"

# ── Install deps ──────────────────────────────────────────────────────
echo -e "${YELLOW}Installing Python dependencies...${NC}"
pip install --quiet --upgrade pip
pip install --quiet -e ".[dev]"
echo -e "${GREEN}  Done${NC}"

# ── Copy .env ─────────────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo -e "${GREEN}  Created .env${NC}"
    echo -e "${YELLOW}  NOTE: Edit .env — set ANTHROPIC_API_KEY for real designs${NC}"
fi

# ── Start Docker ──────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}Starting Docker services (Postgres + Redis)...${NC}"
docker compose up -d
echo "  Waiting for Postgres..."
sleep 6

# ── Migrate + seed ────────────────────────────────────────────────────
echo -e "${YELLOW}Running migrations...${NC}"
alembic upgrade head
echo -e "${YELLOW}Seeding materials...${NC}"
python -m scripts.seed_materials

# ── Frontend ──────────────────────────────────────────────────────────
if command -v node &> /dev/null; then
    echo ""
    echo -e "${YELLOW}Setting up frontend...${NC}"
    cd frontend
    if [ ! -f ".env" ]; then
        cp .env.example .env
        echo -e "${GREEN}  Created frontend/.env${NC}"
        echo -e "${YELLOW}  NOTE: Edit frontend/.env — set VITE_SUPABASE_URL + VITE_SUPABASE_ANON_KEY${NC}"
    fi
    npm install --silent
    echo -e "${GREEN}  Frontend ready${NC}"
    cd ..
fi

# ── Done ──────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  Setup complete!${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo -e "${CYAN}Start backend:${NC}"
echo "  source $ACTIVATE && uvicorn app.main:app --reload --port 8000"
echo ""
echo -e "${CYAN}Backend URLs:${NC}"
echo "  http://localhost:8000/health      <- liveness check"
echo "  http://localhost:8000/docs        <- Swagger UI"
echo ""
echo -e "${CYAN}Get a local dev token (no Supabase needed):${NC}"
echo "  python -m scripts.mint_dev_token"
echo ""
echo -e "${CYAN}Test a protected endpoint:${NC}"
echo "  TOKEN=\$(python -m scripts.mint_dev_token --hours 1 | head -1)"
echo '  curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/materials'
echo ""
echo -e "${CYAN}Start frontend:${NC}"
echo "  cd frontend && npm run dev  ->  http://localhost:5173"
echo ""
echo -e "${CYAN}Run tests:${NC}"
echo "  pytest tests/unit/ tests/engineering/ tests/security/ -q"
echo ""
