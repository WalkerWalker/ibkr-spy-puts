#!/bin/bash
set -e

# IBKR SPY Put Selling Bot - Container Entrypoint
# Runs both the trading scheduler and the FastAPI dashboard

echo "=========================================="
echo "IBKR SPY Put Selling Bot"
echo "=========================================="
echo "Mode: ${RUN_MODE:-all}"
echo "Dry Run: ${DRY_RUN:-false}"
echo "=========================================="

# Function to handle shutdown
shutdown() {
    echo "Shutting down..."
    kill -TERM "$SCHEDULER_PID" 2>/dev/null || true
    kill -TERM "$API_PID" 2>/dev/null || true
    wait
    exit 0
}

trap shutdown SIGTERM SIGINT

# Determine what to run based on RUN_MODE
case "${RUN_MODE:-all}" in
    scheduler)
        # Run only the scheduler
        echo "Starting scheduler only..."
        if [ "${DRY_RUN:-false}" = "true" ]; then
            exec python -m ibkr_spy_puts.main --scheduler --dry-run
        else
            exec python -m ibkr_spy_puts.main --scheduler
        fi
        ;;

    api)
        # Run only the API/dashboard
        echo "Starting API only..."
        exec uvicorn ibkr_spy_puts.api:app --host 0.0.0.0 --port 8000
        ;;

    all)
        # Run both scheduler and API
        echo "Starting scheduler and API..."

        # Start scheduler in background
        if [ "${DRY_RUN:-false}" = "true" ]; then
            python -m ibkr_spy_puts.main --scheduler --dry-run &
        else
            python -m ibkr_spy_puts.main --scheduler &
        fi
        SCHEDULER_PID=$!
        echo "Scheduler started (PID: $SCHEDULER_PID)"

        # Start API in foreground
        uvicorn ibkr_spy_puts.api:app --host 0.0.0.0 --port 8000 &
        API_PID=$!
        echo "API started (PID: $API_PID)"

        # Wait for either process to exit
        wait -n

        # If we get here, one process died - shut down the other
        shutdown
        ;;

    *)
        echo "Unknown RUN_MODE: ${RUN_MODE}"
        echo "Valid options: scheduler, api, all"
        exit 1
        ;;
esac
