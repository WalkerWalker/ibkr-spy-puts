# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Session Startup

**At the start of each session, read these files to understand context:**

1. **`journal/` directory** - Read the most recent journal entry (sorted by date)
   - Provides summary of previous day's work
   - Lists what was completed and what's pending
   - Contains deployment notes and verification steps

2. **`plan.md`** - Project implementation plan
   - Current phase and next steps
   - Decision log with rationale
   - Completed milestones

3. **`requirements.md`** - Functional and non-functional requirements
   - FR1-FR9: Functional requirements
   - NFR1-NFR4: Non-functional requirements
   - Database requirements (DR1-DR6)

**After completing work, update:**
- Create/update today's journal entry in `journal/YYYY-MM-DD.md`
- Update `plan.md` with completed items and decisions
- Update `requirements.md` if requirements change

## Project Overview

Automated trading system that connects to Interactive Brokers TWS API to sell puts on SPY. Uses Python with ib_insync library for TWS communication.

## Commands

```bash
# Install dependencies
poetry install

# Run all tests
poetry run pytest

# Test environment setup (no TWS required)
poetry run pytest tests/test_environment.py -v

# Test TWS connection (requires TWS/IB Gateway running)
poetry run pytest tests/test_tws_connection.py -v

# Run a single test
poetry run pytest tests/test_environment.py::TestPythonEnvironment::test_python_version -v
```

## Architecture

### Configuration (`src/ibkr_spy_puts/config.py`)
- Uses Pydantic Settings for configuration management
- Settings classes: `TWSSettings`, `DatabaseSettings`, `StrategySettings`, `ExitOrderSettings`
- Loads from `.env` file automatically
- Environment variable prefixes: `TWS_`, `DB_`, `STRATEGY_`, `EXIT_` for respective settings

### IBKR Client (`src/ibkr_spy_puts/ibkr_client.py`)
- Wrapper around ib_insync's `IB` class
- Supports context manager protocol for automatic connect/disconnect
- Key methods: `connect()`, `disconnect()`, `get_spy_price()`, `get_account_summary()`

### TWS Connection
- Paper trading port: 7497 (local) or 4002 (Docker)
- Live trading port: 7496 (local) or 4001 (Docker)
- Tests automatically skip if TWS is not running

### Database Separation
- `TRADING_MODE=paper` -> uses `ibkr_puts_paper` database
- `TRADING_MODE=live` -> uses `ibkr_puts` database
- Set via `.env` file or environment variable
- Docker containers inherit from `.env`

## Key Dependencies

- `ib_insync`: TWS API wrapper (not async despite name - uses event loop internally)
- `pydantic-settings`: Configuration from environment variables
- `pytest-asyncio`: Configured with `asyncio_mode = "auto"` in pyproject.toml

## Post-Execution Verification

After any trade execution or order placement, ALWAYS verify via dashboard APIs:

```bash
# 1. Connection status - must be connected, logged in, ready
curl -s http://localhost:8000/api/connection-status | python3 -m json.tool

# 2. Orders in IBKR - should match expected count (2 per position: TP + SL)
curl -s http://localhost:8000/api/live-orders | python3 -m json.tool

# 3. Positions in database - should match IBKR positions
curl -s http://localhost:8000/api/positions | python3 -m json.tool

# 4. Trade history - chronological log of all executions
curl -s http://localhost:8000/api/trade-history | python3 -m json.tool
```

**Checklist:**
- [ ] Connection: `connected=true`, `logged_in=true`, `ready_to_trade=true`
- [ ] Orders: Count = (number of positions) × 2
- [ ] Positions: Each has `expected_tp_price` and `expected_sl_price`
- [ ] Trade history: All entries have correct action, price, and timestamp

## Trading Logic

### Two-Step Order Process
1. **Step 1: Place sell order**
   - Check for conflicting BUY orders on the same contract
   - If conflicts exist: cancel them temporarily
   - Place SELL order using Adaptive algo
   - Wait for FILL
   - Re-place cancelled orders with their ORIGINAL OCA groups

2. **Step 2: Place exit orders (TP/SL)**
   - Only after sell order is FILLED
   - Create new OCA group for this trade's exit orders
   - Place limit order for take profit
   - Place stop order for stop loss

### Key Rules
- IBKR does not allow BUY and SELL orders simultaneously on the same US options contract
- Each trade's TP/SL must be in its own OCA group (never combine)
- The trades table is a pure trade log (entries and exits)
- TP/SL prices are derived from the orders table, not stored in trades

## Testing with Paper Trading

To test the conflict handling logic before live trading:

```bash
# 1. Switch to paper trading mode
# Set TRADING_MODE=paper in .env and restart ib-gateway

# 2. Manually create a position with TP/SL orders
docker exec ibkr-bot python3 /app/tests/scripts/create_test_position.py

# 3. Trigger the scheduler to place a new conflicting order
SCHEDULE_TRADE_TIME=HH:MM docker-compose up -d trading-bot

# 4. Verify the conflict was handled correctly
curl -s http://localhost:8000/api/live-orders | python3 -m json.tool
```

See `tests/integration/test_conflict_handling.py` for automated tests.
