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

## AWS Operations

### SSH Access

```bash
# Connect to AWS EC2 instance
ssh -i .ibkr-key.pem ec2-user@98.88.118.228

# Quick status check (from local machine)
ssh -i .ibkr-key.pem ec2-user@98.88.118.228 "curl -s http://localhost:8000/api/connection-status"
```

**SSH Keys:**
- `.ibkr-key.pem` - Primary SSH key for EC2 access (in project root, git-ignored)
- `~/.ssh/aws.pem` - Legacy AWS key (may not work with current instance)

**EC2 Details:**
- IP: `98.88.118.228`
- User: `ec2-user`
- Dashboard: http://98.88.118.228:8000 (if port open)

### Common Commands

```bash
# Check connection status
curl -s http://localhost:8000/api/connection-status | python3 -m json.tool

# View trade history
curl -s http://localhost:8000/api/trade-history | python3 -m json.tool

# View live orders
curl -s http://localhost:8000/api/live-orders | python3 -m json.tool

# View positions
curl -s http://localhost:8000/api/positions | python3 -m json.tool

# Check Docker containers
sudo docker-compose ps
sudo docker-compose logs -f trading-bot --tail=100

# Restart trading bot
sudo docker-compose restart trading-bot
```

### Deployment

Automatic via GitHub Actions on push to `main`. See `.github/workflows/deploy.yml`.
