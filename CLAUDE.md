# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
- Three settings classes: `TWSSettings`, `DatabaseSettings`, `StrategySettings`
- Loads from `.env` file automatically
- Environment variable prefixes: `TWS_`, `DB_` for respective settings

### IBKR Client (`src/ibkr_spy_puts/ibkr_client.py`)
- Wrapper around ib_insync's `IB` class
- Supports context manager protocol for automatic connect/disconnect
- Key methods: `connect()`, `disconnect()`, `get_spy_price()`, `get_account_summary()`

### TWS Connection
- Paper trading port: 7497
- Live trading port: 7496
- Tests automatically skip if TWS is not running

## Key Dependencies

- `ib_insync`: TWS API wrapper (not async despite name - uses event loop internally)
- `pydantic-settings`: Configuration from environment variables
- `pytest-asyncio`: Configured with `asyncio_mode = "auto"` in pyproject.toml
