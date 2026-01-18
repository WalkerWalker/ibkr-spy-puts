#!/bin/bash
# =============================================================================
# Paper Trading Test Runner
# =============================================================================
# This script manages the paper trading environment and runs integration tests.
#
# Usage:
#   ./scripts/run_paper_tests.sh              # Run all tests
#   ./scripts/run_paper_tests.sh --start      # Start environment only
#   ./scripts/run_paper_tests.sh --stop       # Stop environment only
#   ./scripts/run_paper_tests.sh --conflict   # Run conflict scenario test
#   ./scripts/run_paper_tests.sh --logs       # View logs
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Docker compose command
COMPOSE_CMD="docker-compose -f docker-compose.yml -f docker-compose.paper.yml --env-file .env.paper"

# Functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

start_environment() {
    log_info "Starting paper trading environment..."

    # Check if .env.paper has credentials
    if ! grep -q "TWS_USERID" .env.paper 2>/dev/null; then
        log_error ".env.paper not found or missing credentials"
        log_info "Copy credentials from .env:"
        log_info "  grep 'TWS_USERID\\|TWS_PASSWORD' .env >> .env.paper"
        exit 1
    fi

    # Start services
    $COMPOSE_CMD up -d

    log_info "Waiting for services to be healthy..."

    # Wait for postgres
    log_info "Waiting for PostgreSQL..."
    timeout=60
    while ! docker exec ibkr-db-paper pg_isready -U ibkr -d ibkr_puts_paper >/dev/null 2>&1; do
        sleep 2
        timeout=$((timeout - 2))
        if [ $timeout -le 0 ]; then
            log_error "PostgreSQL did not become ready in time"
            exit 1
        fi
    done
    log_success "PostgreSQL is ready"

    # Wait for IB Gateway
    log_info "Waiting for IB Gateway (this may take 2-3 minutes for first login)..."
    timeout=180
    while ! docker exec ibkr-gateway-paper curl -sf http://localhost:5000 >/dev/null 2>&1; do
        sleep 5
        timeout=$((timeout - 5))
        if [ $timeout -le 0 ]; then
            log_warn "IB Gateway healthcheck timeout - checking logs..."
            docker logs ibkr-gateway-paper --tail 20
            break
        fi
        echo -n "."
    done
    echo

    # Give trading-bot time to initialize
    log_info "Waiting for trading bot to initialize..."
    sleep 10

    # Verify connection
    log_info "Verifying connection status..."
    if curl -sf http://localhost:8001/api/connection-status >/dev/null 2>&1; then
        CONNECTION=$(curl -s http://localhost:8001/api/connection-status)
        echo "$CONNECTION" | python3 -m json.tool
        log_success "Paper trading environment is ready!"
    else
        log_warn "Dashboard not responding yet. Check logs with: $0 --logs"
    fi
}

stop_environment() {
    log_info "Stopping paper trading environment..."
    $COMPOSE_CMD down
    log_success "Paper trading environment stopped"
}

view_logs() {
    log_info "Viewing logs (Ctrl+C to exit)..."
    $COMPOSE_CMD logs -f
}

run_integration_tests() {
    log_info "Running integration tests..."

    # Run pytest inside the container
    docker exec ibkr-bot-paper pytest /app/tests/integration/ -v --tb=short

    if [ $? -eq 0 ]; then
        log_success "Integration tests passed!"
    else
        log_error "Some integration tests failed"
        return 1
    fi
}

run_conflict_test() {
    log_info "Running conflict scenario test..."

    local strike=${1:-580}
    docker exec ibkr-bot-paper python3 /app/tests/scripts/test_conflict_scenario.py --strike "$strike"

    if [ $? -eq 0 ]; then
        log_success "Conflict scenario test passed!"
    else
        log_error "Conflict scenario test failed"
        return 1
    fi
}

verify_results() {
    log_info "Verifying results via API..."

    echo -e "\n${BLUE}Connection Status:${NC}"
    curl -s http://localhost:8001/api/connection-status | python3 -m json.tool

    echo -e "\n${BLUE}Live Orders:${NC}"
    curl -s http://localhost:8001/api/live-orders | python3 -m json.tool

    echo -e "\n${BLUE}Positions:${NC}"
    curl -s http://localhost:8001/api/positions | python3 -m json.tool

    echo -e "\n${BLUE}Trade History:${NC}"
    curl -s http://localhost:8001/api/trade-history | python3 -m json.tool
}

show_help() {
    echo "Paper Trading Test Runner"
    echo ""
    echo "Usage: $0 [command] [options]"
    echo ""
    echo "Commands:"
    echo "  (none)        Start environment, run all tests, verify results"
    echo "  --start       Start paper trading environment only"
    echo "  --stop        Stop paper trading environment"
    echo "  --logs        View container logs"
    echo "  --test        Run integration tests"
    echo "  --conflict    Run conflict scenario test (add strike: --conflict 500)"
    echo "  --verify      Verify results via API"
    echo "  --status      Show container status"
    echo "  --help        Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                    # Full test run"
    echo "  $0 --start            # Start environment for manual testing"
    echo "  $0 --conflict 500     # Test conflict on 500 strike"
    echo "  $0 --verify           # Check API results"
    echo ""
    echo "Port Mapping (Paper vs Live):"
    echo "  Dashboard API: 8001 (paper) / 8000 (live)"
    echo "  PostgreSQL:    5433 (paper) / 5432 (live)"
    echo "  VNC:           5901 (paper) / 5900 (live)"
}

show_status() {
    log_info "Container status:"
    $COMPOSE_CMD ps
}

# Main logic
case "${1:-}" in
    --start)
        start_environment
        ;;
    --stop)
        stop_environment
        ;;
    --logs)
        view_logs
        ;;
    --test)
        run_integration_tests
        ;;
    --conflict)
        run_conflict_test "${2:-580}"
        ;;
    --verify)
        verify_results
        ;;
    --status)
        show_status
        ;;
    --help|-h)
        show_help
        ;;
    "")
        # Full test run
        log_info "=== Paper Trading Full Test Run ==="

        start_environment

        log_info ""
        log_info "=== Running Tests ==="
        run_integration_tests || true

        log_info ""
        log_info "=== Running Conflict Scenario ==="
        run_conflict_test 580 || true

        log_info ""
        log_info "=== Verifying Results ==="
        verify_results

        log_info ""
        log_success "Test run complete!"
        log_info "Environment is still running. Stop with: $0 --stop"
        ;;
    *)
        log_error "Unknown command: $1"
        show_help
        exit 1
        ;;
esac
