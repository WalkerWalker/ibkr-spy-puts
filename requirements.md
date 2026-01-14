# IBKR SPY Put Selling Bot - Requirements

## Functional Requirements

### FR1: Daily Put Selling
- **FR1.1**: Sell 1 SPY put contract daily at market open (09:30 ET)
- **FR1.2**: Select option with expiration closest to 90 DTE
- **FR1.3**: Select strike closest to -0.15 delta
- **FR1.4**: Support limit orders with configurable offset

### FR2: Automatic Exit (Bracket Orders)
- **FR2.1**: After put is sold, automatically place take-profit order
  - Default: Close position when 60% of premium is captured (buy back at 40% of original)
- **FR2.2**: After put is sold, automatically place stop-loss order
  - Default: Close position when loss reaches 200% of premium (buy back at 300% of original)
- **FR2.3**: Bracket orders should be OCO (One-Cancels-Other)
- **FR2.4**: All bracket parameters must be configurable

### FR3: Transaction Logging
- **FR3.1**: Store all trade transactions in PostgreSQL database
- **FR3.2**: Track order placement, fills, and cancellations
- **FR3.3**: Record entry price, exit price, and P&L for each trade
- **FR3.4**: Support querying historical transactions for analysis

### FR4: Configuration
- **FR4.1**: All parameters configurable via environment variables
- **FR4.2**: Support `.env` file for local configuration
- **FR4.3**: Configurable parameters:
  - TWS connection (host, port, client ID)
  - Database connection
  - Strategy (symbol, quantity, target DTE, target delta)
  - Bracket orders (take profit %, stop loss %, enabled/disabled)
  - Schedule (trade time, timezone)

### FR5: TWS Integration
- **FR5.1**: Connect to Interactive Brokers TWS or IB Gateway
- **FR5.2**: Support live trading port (7496) and paper trading port (7497)
- **FR5.3**: Fetch market data (requires OPRA subscription for live options data)
- **FR5.4**: Fetch options chain with greeks (delta required for strike selection)
- **FR5.5**: Submit and manage orders

### FR6: Order Execution Strategy
- **FR6.1**: Use IBKR Adaptive Algo for order execution
  - Priority: "Normal" (balance between fill speed and price improvement)
  - Better fills than simple limit orders in most cases
- **FR6.2**: Order timeout handling
  - If parent order not filled within configurable timeout (default: 5 minutes)
  - Cancel unfilled order
  - Optionally retry with adjusted parameters or wait until next day
- **FR6.3**: Order status monitoring
  - Poll order status until filled, cancelled, or timeout
  - Handle partial fills appropriately

### FR7: Post-Fill Workflow
- **FR7.1**: After parent order fills, record transaction to database
  - Store: order ID, symbol, strike, expiration, fill price, fill time
  - Store: bracket order IDs (take profit, stop loss)
- **FR7.2**: Monitor bracket orders for fills
  - When take profit or stop loss fills, record exit transaction
  - Calculate and store realized P&L
- **FR7.3**: Handle order modifications
  - Log any order amendments or cancellations

### FR8: Conflicting Order Handling
When placing a new bracket order, existing orders on the opposite side of the same contract may conflict.

- **FR8.1**: Before placing new orders, detect conflicting orders
  - Sync all open orders from IB (including from other client sessions)
  - Identify orders on the same contract with opposite action (e.g., BUY orders when we want to SELL)
- **FR8.2**: Temporarily remove conflicting orders
  - Save order details (type, price, quantity, OCA group, etc.)
  - Cancel conflicting orders using `globalCancel` if needed
  - Wait and verify cancellation succeeded before proceeding
- **FR8.3**: Execute new bracket order
  - Place parent order with bracket (take profit + stop loss)
  - All bracket child orders should be in an OCA group
- **FR8.4**: Restore cancelled orders
  - Re-place previously cancelled orders with same parameters
  - Maintain OCA grouping for related orders
  - Verify re-placed orders are active

### FR9: Dashboard Display
- **FR9.1**: Show open positions with:
  - Symbol, Strike, Expiration
  - Entry Date (when position was opened)
  - DIT (Days In Trade) - calculated from entry date
  - DTE (Days To Expiry)
  - Entry Price, Take Profit Target, Stop Loss Target
  - Status
- **FR9.2**: Show aggregate Greeks for all positions:
  - Total Delta, Total Theta, Total Gamma, Total Vega
  - Requires live market data or periodic snapshots
- **FR9.3**: Show P&L summary:
  - Realized P&L (from closed trades)
  - Unrealized P&L (requires live market data)
  - Max Profit (if all positions hit TP)
  - Max Loss (if all positions hit SL)
- **FR9.4**: Auto-refresh dashboard periodically

---

## Non-Functional Requirements

### NFR1: Reliability
- **NFR1.1**: Handle TWS disconnections gracefully
- **NFR1.2**: Retry failed operations with exponential backoff
- **NFR1.3**: Log all errors for debugging

### NFR2: Safety
- **NFR2.1**: Never place duplicate orders for the same day
- **NFR2.2**: Validate all order parameters before submission
- **NFR2.3**: Support read-only mode for testing

### NFR3: Observability
- **NFR3.1**: Log all trading activities
- **NFR3.2**: Store transactions for post-trade analysis
- **NFR3.3**: Clear error messages for troubleshooting

### NFR4: Maintainability
- **NFR4.1**: Clean, modular code structure
- **NFR4.2**: Unit tests for core logic
- **NFR4.3**: Integration tests with TWS paper trading

---

## Configuration Defaults

| Parameter | Default | Description |
|-----------|---------|-------------|
| `STRATEGY_SYMBOL` | SPY | Underlying symbol |
| `STRATEGY_QUANTITY` | 1 | Contracts per trade |
| `STRATEGY_TARGET_DTE` | 90 | Target days to expiration |
| `STRATEGY_TARGET_DELTA` | -0.15 | Target delta for strike selection |
| `BRACKET_ENABLED` | true | Enable bracket orders |
| `BRACKET_TAKE_PROFIT_PCT` | 60.0 | Take profit at 60% gain |
| `BRACKET_STOP_LOSS_PCT` | 200.0 | Stop loss at 200% loss |
| `SCHEDULE_TRADE_AT_OPEN` | true | Trade at market open |
| `SCHEDULE_TRADE_TIME` | 09:30 | Trade time (ET) |
| `TWS_PORT` | 7496 | TWS live trading port |
| `ORDER_TIMEOUT_SECONDS` | 300 | Cancel order if not filled (5 min) |
| `ORDER_RETRY_ENABLED` | false | Retry after timeout |
| `ORDER_USE_ADAPTIVE` | true | Use Adaptive Algo |
| `ORDER_ADAPTIVE_PRIORITY` | Normal | Adaptive priority (Urgent/Normal/Patient) |

---

## Database Requirements

### DR1: Strategy Trade Tracking
The database must track all trades executed by this strategy, separate from other account activity.

- **DR1.1**: Each strategy execution (daily) creates one "trade" record
- **DR1.2**: Trades are linked to their bracket orders (parent, TP, SL)
- **DR1.3**: Strategy trades are identifiable by a unique strategy tag/ID
- **DR1.4**: Support querying: "Show all trades from this strategy"

### DR2: Order Tracking
Track all orders placed by the strategy with their lifecycle.

- **DR2.1**: Store order details: symbol, strike, expiration, quantity, prices
- **DR2.2**: Track order status: PENDING → FILLED / CANCELLED / EXPIRED
- **DR2.3**: Record expected price vs actual fill price (slippage tracking)
- **DR2.4**: Link bracket orders to parent via relationship
- **DR2.5**: Store IBKR order IDs for reconciliation with live account

### DR3: Position Tracking
Track open positions from strategy trades.

- **DR3.1**: Position = trade that hasn't been closed yet
- **DR3.2**: Store entry details: price, time, quantity
- **DR3.3**: Store live greeks from IBKR: delta, theta, gamma, vega
- **DR3.4**: Calculate unrealized P&L: (entry_price - current_price) × quantity × 100
- **DR3.5**: Position closes when TP or SL fills (or manual close / expiration)

### DR4: P&L Tracking
Track realized and unrealized P&L.

- **DR4.1**: **Realized P&L** = (entry_price - exit_price) × quantity × 100
  - Positive when bought back cheaper than sold
- **DR4.2**: **Unrealized P&L** = (entry_price - current_mid) × quantity × 100
  - Requires live market data
- **DR4.3**: Track slippage: expected_exit_price vs actual_exit_price
  - TP slippage: expected 40% of premium, actual might differ
  - SL slippage: expected 300% of premium, actual might differ
- **DR4.4**: Store exit reason: TAKE_PROFIT, STOP_LOSS, MANUAL, EXPIRED_WORTHLESS, ASSIGNED

### DR5: Risk Metrics
Support calculating aggregate risk metrics.

- **DR5.1**: **Max Loss** = Σ (stop_loss_price - entry_price) × quantity × 100
  - For all open positions if all hit stop loss
- **DR5.2**: **Max Profit** = Σ (entry_price - take_profit_price) × quantity × 100
  - For all open positions if all hit take profit
- **DR5.3**: **Live Delta** = Σ (delta × quantity × 100) across open positions
- **DR5.4**: **Live Theta** = Σ (theta × quantity × 100) across open positions

### DR6: Frontend Query Support
The database must support these frontend queries efficiently:

- **DR6.1**: List all strategy trades (paginated, filterable by date)
- **DR6.2**: List open positions with current greeks and unrealized P&L
- **DR6.3**: List pending bracket orders
- **DR6.4**: Show realized P&L summary (daily, weekly, monthly, all-time)
- **DR6.5**: Show risk metrics dashboard (max loss, live delta/theta)
- **DR6.6**: Show trade details with entry, exit, slippage analysis

---

## Database Schema

### Table: trades
One record per strategy execution (typically one per trading day).

| Column | Type | Description |
|--------|------|-------------|
| id | serial | Primary key |
| trade_date | date | Date trade was executed |
| symbol | varchar(10) | Underlying symbol (SPY) |
| strike | decimal(10,2) | Strike price |
| expiration | date | Option expiration date |
| quantity | int | Number of contracts |
| entry_price | decimal(10,4) | Fill price when sold |
| entry_time | timestamp | When parent order filled |
| exit_price | decimal(10,4) | Fill price when closed (null if open) |
| exit_time | timestamp | When closed (null if open) |
| exit_reason | varchar(20) | TAKE_PROFIT, STOP_LOSS, MANUAL, EXPIRED, ASSIGNED |
| expected_tp_price | decimal(10,4) | Expected take profit price |
| expected_sl_price | decimal(10,4) | Expected stop loss price |
| realized_pnl | decimal(10,2) | P&L after close (null if open) |
| slippage | decimal(10,4) | Difference from expected exit price |
| status | varchar(20) | OPEN, CLOSED |
| strategy_id | varchar(50) | Identifier for this strategy |
| created_at | timestamp | Record creation time |
| updated_at | timestamp | Last update time |

### Table: orders
All orders placed by the strategy.

| Column | Type | Description |
|--------|------|-------------|
| id | serial | Primary key |
| trade_id | int | FK to trades.id |
| ibkr_order_id | int | IBKR order ID for reconciliation |
| ibkr_perm_id | int | IBKR permanent ID (survives restarts) |
| order_type | varchar(20) | PARENT, TAKE_PROFIT, STOP_LOSS |
| action | varchar(10) | SELL (parent) or BUY (exits) |
| limit_price | decimal(10,4) | Limit/stop price |
| fill_price | decimal(10,4) | Actual fill price (null if not filled) |
| fill_time | timestamp | When filled (null if not filled) |
| quantity | int | Number of contracts |
| status | varchar(20) | PENDING, SUBMITTED, FILLED, CANCELLED |
| algo_strategy | varchar(20) | Adaptive, etc. |
| created_at | timestamp | Record creation time |
| updated_at | timestamp | Last update time |

### Table: position_snapshots
Periodic snapshots of open positions with live greeks (for historical tracking).

| Column | Type | Description |
|--------|------|-------------|
| id | serial | Primary key |
| trade_id | int | FK to trades.id |
| snapshot_time | timestamp | When snapshot was taken |
| current_price | decimal(10,4) | Current mid price |
| unrealized_pnl | decimal(10,2) | P&L at snapshot time |
| delta | decimal(10,6) | Live delta |
| theta | decimal(10,6) | Live theta |
| gamma | decimal(10,6) | Live gamma |
| vega | decimal(10,6) | Live vega |
| iv | decimal(10,4) | Implied volatility |
| days_to_expiry | int | DTE at snapshot |

### View: open_positions
Convenience view for current open positions.

```sql
CREATE VIEW open_positions AS
SELECT t.*,
       (SELECT ps.* FROM position_snapshots ps
        WHERE ps.trade_id = t.id
        ORDER BY snapshot_time DESC LIMIT 1) as latest_snapshot
FROM trades t
WHERE t.status = 'OPEN';
```

### View: strategy_summary
Aggregate metrics for dashboard.

```sql
CREATE VIEW strategy_summary AS
SELECT
    COUNT(*) FILTER (WHERE status = 'OPEN') as open_positions,
    COUNT(*) FILTER (WHERE status = 'CLOSED') as closed_trades,
    SUM(realized_pnl) FILTER (WHERE status = 'CLOSED') as total_realized_pnl,
    SUM(CASE WHEN exit_reason = 'TAKE_PROFIT' THEN 1 ELSE 0 END) as tp_count,
    SUM(CASE WHEN exit_reason = 'STOP_LOSS' THEN 1 ELSE 0 END) as sl_count
FROM trades
WHERE strategy_id = 'spy-put-selling';
```

---

## Out of Scope (Phase 1)

- Multiple symbols (only SPY)
- Complex multi-leg strategies
- Email/SMS alerts
- Backtesting engine

## Phase 2 Features

- **Dashboard UI**: FastAPI + HTML/JS for portfolio visualization
- **Real-time P&L monitoring**: Live position and P&L updates
- **Aggregated Greeks**: Combined delta/theta/gamma/vega across positions
- **Risk Metrics**: Margin usage, max loss, max profit
- **Trade History**: Searchable transaction log with export

---

## Milestones Completed

| Milestone | Date | Notes |
|-----------|------|-------|
| Project setup | 2025-12-19 | Poetry, Python 3.11, initial structure |
| TWS connection | 2025-12-19 | Connect/disconnect, account summary |
| Mock client | 2026-01-12 | Offline development with fixture data |
| Strategy core | 2026-01-12 | Option selection, bracket price calc |
| Scheduler | 2026-01-12 | APScheduler with NYSE holiday calendar |
| First live order | 2026-01-12 | SPY Apr17'26 $630P bracket order filled! |
| Scheduler --run-now | 2026-01-13 | Added immediate execution flag |
| Conflicting order handling | 2026-01-13 | globalCancel + OCA group restore |
| Dashboard improvements | 2026-01-13 | Entry date, DIT column, removed ID |
| Second live order | 2026-01-13 | SPY Apr17'26 $630P @ $6.03 filled! |
