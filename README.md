# IBKR SPY Put Selling Bot

Automated trading system that sells puts on SPY using Interactive Brokers TWS API.

## Quick Start

```bash
# Local development
poetry install
poetry run python -m ibkr_spy_puts.main --dry-run

# Docker deployment
cp .env.example .env
# Edit .env with your IBKR credentials
docker-compose up -d
```

## Architecture

- **Scheduler**: Runs daily at market open (configurable)
- **Strategy**: Selects puts by delta/DTE, places bracket orders
- **Dashboard**: FastAPI web UI at http://localhost:8000
- **Database**: PostgreSQL for trade tracking

## Configuration

See `.env.example` for all options.
