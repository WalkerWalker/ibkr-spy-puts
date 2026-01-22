# IBKR SPY Put Selling Bot - Project Plan

## Project Overview
Automated trading system that connects to Interactive Brokers TWS API to sell puts on SPY on a daily basis with configurable parameters (strike, expiration, quantity, etc.).

---

## Step 1: Language Decision - Java vs Python

### Python with ib_insync/ib_async

**Pros:**
- `ib_insync` is mature, well-documented, and widely used in the IBKR community
- Cleaner async/await syntax for handling TWS event-driven architecture
- Faster prototyping and iteration
- Excellent data libraries (pandas) for any analysis needs
- Simpler PostgreSQL integration (psycopg2, SQLAlchemy)
- Smaller codebase for same functionality
- Large community of algo traders using Python + IBKR

**Cons:**
- ib_insync is a wrapper, not native (but it's battle-tested)
- Runtime performance slightly slower (irrelevant for this use case)

### Java with Native TWS API

**Pros:**
- Native API support from IBKR
- Your current working language (no learning curve)
- Strong typing catches errors at compile time
- Better for complex enterprise systems

**Cons:**
- More verbose code for same functionality
- Event-driven callback pattern is more complex to manage
- More boilerplate for database operations
- Slower development cycle

### Recommendation: **Python**

For this project, Python is the better choice because:
1. **ib_insync is production-ready** - Used by many traders, well-maintained
2. **Simplicity matters** - Selling puts daily is not computationally intensive
3. **Faster iteration** - You can test and refine strategies quickly
4. **"Native" is overkill** - As you noted, for daily put selling, the native API complexity isn't justified
5. **Community resources** - More examples and help available for Python + IBKR

---

## Step 2: Project Structure

```
ibkr-spy-puts/
├── src/
│   ├── __init__.py
│   ├── main.py              # Entry point
│   ├── config.py            # Configuration management
│   ├── ibkr_client.py       # TWS connection and trading logic
│   ├── strategy.py          # Put selling strategy logic
│   ├── database.py          # PostgreSQL operations
│   └── scheduler.py         # Daily execution scheduling
├── sql/
│   └── schema.sql           # Database schema
├── tests/
│   └── ...
├── requirements.txt
├── .env.example             # Environment variables template
├── docker-compose.yml       # PostgreSQL container (optional)
└── README.md
```

---

## Step 3: Environment Setup

- [ ] Install Python 3.11+
- [ ] Create virtual environment
- [ ] Install dependencies:
  - `ib_insync` - TWS API wrapper
  - `psycopg2-binary` - PostgreSQL driver
  - `sqlalchemy` - ORM (optional, can use raw SQL)
  - `python-dotenv` - Environment configuration
  - `schedule` or `APScheduler` - Task scheduling
- [ ] Set up PostgreSQL (local or Docker)
- [ ] Configure TWS/IB Gateway for API connections

---

## Step 4: Database Schema Design

**Schema file:** `sql/schema.sql`

### Core Tables

1. **trades** - One record per strategy execution (daily trade)
   - Entry: trade_date, symbol, strike, expiration, entry_price, entry_time
   - Exit: exit_price, exit_time, exit_reason (TAKE_PROFIT, STOP_LOSS, MANUAL, EXPIRED_WORTHLESS, ASSIGNED)
   - P&L: realized_pnl, slippage (vs expected exit price)
   - Status: OPEN → CLOSED
   - Links to: strategy_id for filtering

2. **orders** - All orders placed (parent + bracket orders)
   - IBKR IDs: ibkr_order_id, ibkr_perm_id, ibkr_con_id
   - Type: PARENT, TAKE_PROFIT, STOP_LOSS
   - Status: PENDING → SUBMITTED → FILLED / CANCELLED
   - Algo: algo_strategy (Adaptive), algo_priority (Normal)

3. **position_snapshots** - Periodic snapshots with live greeks
   - Prices: current_bid, current_ask, current_mid, underlying_price
   - Greeks: delta, theta, gamma, vega, iv
   - P&L: unrealized_pnl at snapshot time

### Views for Frontend

- **open_positions** - Current positions with latest snapshot
- **strategy_summary** - Aggregate metrics (win rate, P&L totals)
- **risk_metrics** - Max loss, total delta/theta for all open positions
- **pending_orders** - Orders awaiting fill

### Key Design Decisions

1. **Separate trades from orders** - trades = strategy execution, orders = individual order lifecycle
2. **Position snapshots** - Store periodic greeks for historical tracking (not just latest)
3. **Strategy ID tagging** - Filter strategy trades from other account activity
4. **Slippage tracking** - Compare expected vs actual exit prices
5. **Auto P&L calculation** - Trigger calculates realized_pnl on trade close

---

## Step 5: Core Implementation

### 5.1 TWS Connection Module
- [x] Connect to TWS/IB Gateway
- [x] Handle connection lifecycle (connect, disconnect, reconnect)
- [ ] Implement heartbeat/health checks

### 5.2 Market Data & Option Selection
- [x] Fetch current SPY price
- [x] Get options chain for SPY
- [x] Find expiration closest to target DTE (default: 90 days)
- [x] Get option greeks (delta) for available strikes
- [x] Select strike closest to target delta (default: -0.15)

**Note**: Requires OPRA market data subscription ($1.50/month) for live options data.

### 5.3 Order Execution
- [x] Build option contract specification
- [x] Submit sell put order using Adaptive Algo (better fills than limit)
- [ ] Handle order status updates
- [ ] Log transactions to database

### 5.3.1 Execution Strategy (Adaptive Algo)
**Decision**: Use IBKR's Adaptive Algo instead of simple limit orders.

- **Priority**: "Normal" (balance between fill speed and price improvement)
- **Why Adaptive**: Seeks price improvement while still prioritizing getting filled
- **Implementation**: `LimitOrder` with `algoStrategy="Adaptive"` and `algoParams`

**Order Lifecycle:**
```
1. Create bracket order (parent + TP + SL)
2. Submit with Adaptive Algo on parent
3. Monitor order status
4. If not filled within timeout → Cancel and handle
5. If filled → Record to database, bracket orders become active
6. Monitor bracket orders for exit fills
7. On exit fill → Record P&L to database
```

### 5.3.2 Order Timeout and Retry Logic
**Configuration:**
- `ORDER_TIMEOUT_SECONDS=300` (5 minutes default)
- `ORDER_RETRY_ENABLED=false` (don't retry by default)

**Timeout Handling:**
- [ ] Start timer when order submitted
- [ ] Poll order status every 5 seconds
- [ ] If timeout reached and not filled:
  - Cancel the order
  - Log the cancellation with reason "TIMEOUT"
  - If retry enabled: wait 1 minute, try with Adaptive "Urgent" priority
  - If retry disabled: skip trading for this day, log and exit
- [ ] Handle partial fills:
  - If partially filled, keep remaining quantity active
  - Log partial fill to database

**Note on Adaptive Algo fills**: With "Normal" priority, Adaptive Algo usually fills within minutes for liquid options like SPY. Timeout is a safety net for unusual market conditions.

### 5.3.3 Post-Fill Database Update
**On Parent Order Fill:**
- [ ] Record entry transaction to database:
  - order_id, symbol, strike, expiration
  - fill_price, fill_time, quantity
  - action="SELL", status="FILLED"
  - bracket_type=NULL (parent order)
- [ ] Record bracket orders (pending):
  - Take profit: action="BUY", bracket_type="TAKE_PROFIT", status="PENDING"
  - Stop loss: action="BUY", bracket_type="STOP_LOSS", status="PENDING"
  - Link via parent_order_id

**On Bracket Order Fill:**
- [ ] Update bracket order status to "FILLED"
- [ ] Calculate realized P&L: (entry_price - exit_price) × quantity × 100
- [ ] Cancel the other bracket order (OCO)
- [ ] Mark cancelled order as "CANCELLED"

### 5.4 Exit Orders (Auto-Exit with TP/SL)

**Two-Step Process:**
1. Place sell order (with conflict handling)
2. After sell order fills, place exit orders (TP + SL) in OCA group

**Configuration (from ExitOrderSettings):**
- `EXIT_TAKE_PROFIT_PCT=60.0` → Buy back at 40% of premium (60% profit)
- `EXIT_STOP_LOSS_PCT=200.0` → Buy back at 300% of premium (200% loss)
- `EXIT_ENABLED=true` → Enable/disable exit orders

**Implementation Tasks:**
- [x] Implement `execute_trade()` for two-step process
- [x] Calculate take profit price: `sell_price * (1 - take_profit_pct / 100)`
- [x] Calculate stop loss price: `sell_price * (1 + stop_loss_pct / 100)`
- [x] Place exit orders in new OCA group after sell fills
- [ ] Log transactions to database (linked via sell_order_id)
- [ ] Handle partial fills and order adjustments

### 5.5 Position Management
- [ ] Track open positions
- [ ] Update position values daily
- [ ] Handle expiration (record if expired worthless or assigned)

### 5.6 Scheduling
- [ ] Schedule daily execution (e.g., 10:00 AM ET)
- [ ] Handle market holidays
- [ ] Implement retry logic for failures

---

## Step 6: Configuration Parameters

Create a `.env` file or config system for:

```
# TWS Connection
TWS_HOST=127.0.0.1
TWS_PORT=7496          # Live: 7496, Paper: 7497
TWS_CLIENT_ID=0        # 0 = master clientId (can manage orders from any clientId)

# Database
DB_HOST=localhost
DB_PORT=5432
DB_NAME=ibkr_puts
DB_USER=postgres
DB_PASSWORD=xxx

# Strategy Parameters (prefix: STRATEGY_)
STRATEGY_SYMBOL=SPY
STRATEGY_QUANTITY=1              # Number of contracts
STRATEGY_ORDER_TYPE=LMT          # LMT or MKT
STRATEGY_TARGET_DTE=90           # Target days to expiration (closest to this)
STRATEGY_TARGET_DELTA=-0.15      # Target delta (closest to this, negative for puts)

# Exit Order Parameters (prefix: EXIT_)
# Take profit: % of premium to capture before closing
# 60% means buy back at 40% of original premium (sold $1, buy back $0.40)
EXIT_TAKE_PROFIT_PCT=60.0
# Stop loss: % of premium loss before closing
# 200% means buy back at 300% of original (sold $1, buy back $3, losing $2)
EXIT_STOP_LOSS_PCT=200.0
EXIT_ENABLED=true

# Scheduling (prefix: SCHEDULE_)
SCHEDULE_TRADE_AT_OPEN=true      # Trade at market open
SCHEDULE_TRADE_TIME=09:30        # ET - market open time
SCHEDULE_TIMEZONE=America/New_York
```

---

## Step 7: Testing Strategy

### 7.1 Mock Data for Offline Development

**Problem:** Development is limited to market hours if we need live option chain data.

**Solution:** Capture real option chain data during market hours, save as fixtures for offline use.

**Fixture Data to Capture:**
- [ ] SPY price snapshot
- [ ] Option expirations list
- [ ] Full option chain with greeks (strikes, deltas, bids, asks)
- [ ] Sample account summary

**Implementation:**
- [ ] Create `scripts/capture_market_data.py` - Run during market hours to save fixtures
- [ ] Save to `tests/fixtures/` as JSON files (e.g., `spy_option_chain_2026-01-11.json`)
- [ ] Create `MockIBKRClient` class that loads from fixtures
- [ ] Unit tests use `MockIBKRClient` - no TWS required
- [ ] Integration tests use real `IBKRClient` - requires TWS

**Test Categories:**
```
tests/
├── fixtures/                    # Captured market data
│   ├── spy_price.json
│   ├── spy_expirations.json
│   └── spy_option_chain.json
├── unit/                        # Use MockIBKRClient, always run
│   ├── test_option_selection.py
│   ├── test_trade_execution.py
│   └── test_strategy.py
└── integration/                 # Use real IBKRClient, skip if no TWS
    ├── test_tws_connection.py
    └── test_order_submission.py
```

### 7.2 Test Checklist

- [ ] Unit tests for strategy calculations (offline, mock data)
- [ ] Unit tests for option selection logic (offline, mock data)
- [ ] Unit tests for exit price calculations (offline, mock data)
- [ ] Integration tests with TWS paper trading
- [ ] Test database operations
- [ ] Test order submission in paper account
- [ ] Run for 1-2 weeks in paper before live

---

## Step 8: Deployment (Docker + AWS EC2)

### Architecture
```
┌─────────────────────────────────────────┐
│  AWS EC2 Instance                       │
│  ┌─────────────────────────────────┐    │
│  │  Docker Container               │    │
│  │  ┌───────────┐  ┌────────────┐  │    │
│  │  │ IB Gateway│  │ Bot +      │  │    │
│  │  │ (headless)│  │ APScheduler│  │    │
│  │  │           │  │ + FastAPI  │  │    │
│  │  └───────────┘  └────────────┘  │    │
│  └─────────────────────────────────┘    │
└─────────────────────────────────────────┘
```

### 8.1 Scheduling (APScheduler)
- [ ] Add APScheduler dependency
- [ ] Create `scheduler.py` module
- [ ] Schedule daily trade at 9:31 AM ET (1 min after open for stability)
- [ ] Skip weekends automatically
- [ ] Skip US market holidays (NYSE calendar)
- [ ] Retry logic on connection failures
- [ ] Graceful shutdown handling

### 8.2 Docker Setup
- [ ] Create `Dockerfile` for the bot
- [ ] Create `docker-compose.yml` with IB Gateway
- [ ] Environment variable configuration
- [ ] Health check endpoint
- [ ] Log persistence (volume mount)

### 8.3 AWS Deployment
- [ ] EC2 instance setup (t3.small sufficient)
- [ ] Security group configuration
- [ ] Docker installation
- [ ] Container auto-restart on failure
- [ ] CloudWatch logging (optional)

---

## Step 9: Dashboard UI (Phase 2)

### 9.1 Technology Stack
- **Backend**: FastAPI (already in container, serves API + static files)
- **Frontend**: Simple HTML/JS or React (lightweight)
- **Database**: PostgreSQL for trade history

### 9.2 Portfolio Overview Dashboard
Display real-time aggregated metrics for all open positions:

**Position Summary Table:**
- [ ] All open put positions (symbol, strike, expiration, quantity)
- [ ] Current price vs entry price
- [ ] Individual P&L per position
- [ ] Days to expiration remaining

**Aggregated Greeks:**
- [ ] **Combined Delta**: Sum of (delta × quantity × 100) across all positions
- [ ] **Combined Theta**: Sum of (theta × quantity × 100) - daily time decay
- [ ] **Combined Gamma**: Portfolio gamma exposure
- [ ] **Combined Vega**: Portfolio vega exposure

**Risk Metrics:**
- [ ] **Maintenance Margin**: Current margin requirement from IBKR
- [ ] **Buying Power Used**: % of available buying power
- [ ] **Max Loss (Book-wide)**: Calculated from stop-loss levels
  - Formula: Σ (stop_loss_price - entry_price) × quantity × 100
- [ ] **Max Profit (Book-wide)**: Calculated from take-profit levels
  - Formula: Σ (entry_price - take_profit_price) × quantity × 100

**P&L Display:**
- [ ] **Realized P&L**: Sum of closed trades
- [ ] **Unrealized P&L**: Current mark-to-market
- [ ] **Total P&L**: Realized + Unrealized
- [ ] **P&L Chart**: Daily/weekly/monthly performance

### 9.3 Trade History
- [ ] Table of all executed trades
- [ ] Filter by date range, status
- [ ] Entry/exit prices, P&L per trade
- [ ] Export to CSV

### 9.4 Configuration Panel
- [ ] View/edit strategy parameters
- [ ] Enable/disable trading
- [ ] Manual trade trigger button
- [ ] View scheduled next trade time

### 9.5 API Endpoints (FastAPI)
```
GET  /api/positions          # Current open positions
GET  /api/positions/greeks   # Aggregated greeks
GET  /api/positions/risk     # Risk metrics (margin, max loss)
GET  /api/trades             # Trade history
GET  /api/pnl                # P&L summary
GET  /api/pnl/history        # Historical P&L data points
GET  /api/status             # Bot status, next scheduled trade
POST /api/trade/trigger      # Manual trade trigger
GET  /health                 # Health check for monitoring
```

---

## Implementation Order

1. ~~**Set up development environment**~~ ✅ (Python, Poetry, venv)
2. ~~**Implement TWS connection**~~ ✅ - tested with paper and live
3. ~~**Create mock client**~~ ✅ - offline development with fixtures
4. ~~**Implement put selection strategy**~~ ✅ - delta-based selection
5. ~~**Implement bracket orders**~~ ✅ - native IBKR OCO
6. ~~**Add Adaptive Algo execution**~~ ✅ - first live order filled!
7. **Create database schema** ← NEXT
8. **Add database logging** - record fills and P&L
9. **Add order timeout/retry logic** - safety net
10. **Add scheduling** - APScheduler with NYSE calendar
11. **Test in paper trading for 1-2 weeks**
12. **Deploy to Docker + EC2**

---

## Risk Considerations

- Always start with paper trading
- Use small position sizes initially
- Implement max daily loss limits
- Consider market circuit breakers
- Handle assignment scenarios (need capital for 100 shares per contract)
- Monitor for API disconnections

---

## Decision Log

| Decision | Choice | Date | Notes |
|----------|--------|------|-------|
| Language | Python | 2025-12-19 | ib_insync is mature, faster development |
| Package Manager | Poetry | 2025-12-19 | Still modern, user is familiar with it |
| Exit Orders | Two-step process | 2026-01-11 | Sell order first, then TP/SL in OCA group after fill |
| Offline Testing | Mock Fixtures | 2026-01-11 | Capture real option chain data during market hours, save as JSON fixtures for offline development and unit tests |
| Scheduling | APScheduler | 2026-01-11 | Internal Python scheduler; container runs 24/7 with IB Gateway; simpler than AWS ECS scheduled tasks |
| Deployment | Docker + EC2 | 2026-01-11 | Single container with IB Gateway + bot + scheduler + FastAPI dashboard |
| Order Execution | Adaptive Algo | 2026-01-12 | Use IBKR Adaptive Algo with "Normal" priority for better fills; timeout safety net at 5 min |
| First Live Trade | SUCCESS | 2026-01-12 | SPY Apr17'26 $630 Put order filled at $5.59 |

---

## Step 10: Project Scaffolding (Current Phase)

### 10.1 Poetry Setup
- [x] Initialize Poetry project in `/Users/jiamin/ibkr-spy-puts`
- [x] Configure Python version (3.11+)
- [x] Add dependencies:
  - `ib_insync` - TWS API wrapper
  - `psycopg2-binary` - PostgreSQL driver
  - `python-dotenv` - Environment configuration
  - `pydantic` - Settings/config validation
  - `pydantic-settings` - For env file loading
- [x] Add dev dependencies:
  - `pytest` - Testing framework
  - `pytest-asyncio` - Async test support (ib_insync uses asyncio)

### 10.2 Project Structure Creation
- [x] Create `src/ibkr_spy_puts/` package structure
- [x] Create `tests/` directory
- [x] Create `.env.example` template
- [x] Create `src/ibkr_spy_puts/config.py` - Configuration management
- [x] Create `src/ibkr_spy_puts/ibkr_client.py` - TWS connection module

### 10.3 Verification Tests
- [x] `tests/test_environment.py` - Verify Python env and imports work
- [x] `tests/test_tws_connection.py` - Verify TWS connection (skipped if TWS not running)

### 10.4 Verification Commands
After setup, run:
```bash
cd /Users/jiamin/ibkr-spy-puts
poetry install
poetry run pytest tests/test_environment.py -v  # Should pass immediately
poetry run pytest tests/test_tws_connection.py -v  # Requires TWS running
```
