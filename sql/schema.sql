-- IBKR SPY Put Selling Bot - Database Schema
-- PostgreSQL 14+

-- Enable UUID extension for potential future use
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- TABLES
-- ============================================================================

-- trades: One record per strategy execution (typically one per trading day)
CREATE TABLE trades (
    id SERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    symbol VARCHAR(10) NOT NULL DEFAULT 'SPY',
    strike DECIMAL(10,2) NOT NULL,
    expiration DATE NOT NULL,
    quantity INT NOT NULL DEFAULT 1,

    -- Entry details
    entry_price DECIMAL(10,4) NOT NULL,
    entry_time TIMESTAMP WITH TIME ZONE NOT NULL,

    -- Exit details (null while position is open)
    exit_price DECIMAL(10,4),
    exit_time TIMESTAMP WITH TIME ZONE,
    exit_reason VARCHAR(20), -- TAKE_PROFIT, STOP_LOSS, MANUAL, EXPIRED_WORTHLESS, ASSIGNED

    -- Bracket order prices (for calculating slippage)
    expected_tp_price DECIMAL(10,4) NOT NULL, -- Expected take profit price (40% of entry)
    expected_sl_price DECIMAL(10,4) NOT NULL, -- Expected stop loss price (300% of entry)

    -- P&L tracking
    realized_pnl DECIMAL(10,2), -- Calculated on close: (entry - exit) * qty * 100
    slippage DECIMAL(10,4), -- Difference from expected exit price

    -- Status
    status VARCHAR(20) NOT NULL DEFAULT 'OPEN', -- OPEN, CLOSED
    strategy_id VARCHAR(50) NOT NULL DEFAULT 'spy-put-selling',

    -- Metadata
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    -- Constraints
    CONSTRAINT valid_status CHECK (status IN ('OPEN', 'CLOSED')),
    CONSTRAINT valid_exit_reason CHECK (
        exit_reason IS NULL OR
        exit_reason IN ('TAKE_PROFIT', 'STOP_LOSS', 'MANUAL', 'EXPIRED_WORTHLESS', 'ASSIGNED')
    )
);

-- Index for common queries
CREATE INDEX idx_trades_status ON trades(status);
CREATE INDEX idx_trades_trade_date ON trades(trade_date);
CREATE INDEX idx_trades_strategy_id ON trades(strategy_id);
CREATE INDEX idx_trades_expiration ON trades(expiration);

-- orders: All orders placed by the strategy (parent, take profit, stop loss)
CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    trade_id INT NOT NULL REFERENCES trades(id) ON DELETE CASCADE,

    -- IBKR identifiers for reconciliation with live account
    ibkr_order_id INT, -- Order ID (may change on restart)
    ibkr_perm_id INT,  -- Permanent ID (survives restarts)
    ibkr_con_id INT,   -- Contract ID

    -- Order details
    order_type VARCHAR(20) NOT NULL, -- PARENT, TAKE_PROFIT, STOP_LOSS
    action VARCHAR(10) NOT NULL,     -- SELL (parent) or BUY (exits)
    order_class VARCHAR(20) NOT NULL DEFAULT 'LMT', -- LMT, STP, etc.
    limit_price DECIMAL(10,4),       -- Limit price (for LMT orders)
    stop_price DECIMAL(10,4),        -- Stop price (for STP orders)

    -- Fill details
    fill_price DECIMAL(10,4),        -- Actual fill price
    fill_time TIMESTAMP WITH TIME ZONE,
    filled_quantity INT DEFAULT 0,

    -- Status
    quantity INT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING', -- PENDING, SUBMITTED, FILLED, CANCELLED, REJECTED

    -- Execution details
    algo_strategy VARCHAR(20),       -- Adaptive, etc.
    algo_priority VARCHAR(20),       -- Urgent, Normal, Patient

    -- Metadata
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    -- Constraints
    CONSTRAINT valid_order_type CHECK (order_type IN ('PARENT', 'TAKE_PROFIT', 'STOP_LOSS')),
    CONSTRAINT valid_action CHECK (action IN ('BUY', 'SELL')),
    CONSTRAINT valid_order_status CHECK (
        status IN ('PENDING', 'SUBMITTED', 'PRESUBMITTED', 'FILLED', 'CANCELLED', 'REJECTED', 'INACTIVE')
    )
);

-- Index for common queries
CREATE INDEX idx_orders_trade_id ON orders(trade_id);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_ibkr_order_id ON orders(ibkr_order_id);
CREATE INDEX idx_orders_ibkr_perm_id ON orders(ibkr_perm_id);

-- position_snapshots: Periodic snapshots of open positions with live greeks
-- Used for historical tracking and dashboard display
CREATE TABLE position_snapshots (
    id SERIAL PRIMARY KEY,
    trade_id INT NOT NULL REFERENCES trades(id) ON DELETE CASCADE,

    -- Snapshot timing
    snapshot_time TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    -- Price data
    current_bid DECIMAL(10,4),
    current_ask DECIMAL(10,4),
    current_mid DECIMAL(10,4),
    underlying_price DECIMAL(10,4), -- SPY price at snapshot

    -- P&L at snapshot time
    unrealized_pnl DECIMAL(10,2),

    -- Greeks (for short put, delta is positive exposure)
    delta DECIMAL(10,6),
    theta DECIMAL(10,6),  -- Positive for short options (time decay profit)
    gamma DECIMAL(10,6),
    vega DECIMAL(10,6),

    -- Other metrics
    iv DECIMAL(10,4),     -- Implied volatility
    days_to_expiry INT,

    -- Metadata
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- Index for fetching latest snapshot per trade
CREATE INDEX idx_snapshots_trade_time ON position_snapshots(trade_id, snapshot_time DESC);

-- ============================================================================
-- VIEWS
-- ============================================================================

-- open_positions: Current open positions with latest snapshot data
CREATE VIEW open_positions AS
SELECT
    t.id,
    t.trade_date,
    t.symbol,
    t.strike,
    t.expiration,
    t.quantity,
    t.entry_price,
    t.entry_time,
    t.expected_tp_price,
    t.expected_sl_price,
    t.strategy_id,
    -- Days to expiry
    (t.expiration - CURRENT_DATE) as days_to_expiry,
    -- Latest snapshot data
    ps.snapshot_time as last_updated,
    ps.current_mid,
    ps.underlying_price,
    ps.unrealized_pnl,
    ps.delta,
    ps.theta,
    ps.gamma,
    ps.vega,
    ps.iv
FROM trades t
LEFT JOIN LATERAL (
    SELECT *
    FROM position_snapshots ps
    WHERE ps.trade_id = t.id
    ORDER BY ps.snapshot_time DESC
    LIMIT 1
) ps ON true
WHERE t.status = 'OPEN';

-- strategy_summary: Aggregate metrics for dashboard
CREATE VIEW strategy_summary AS
SELECT
    COUNT(*) FILTER (WHERE status = 'OPEN') as open_positions,
    COUNT(*) FILTER (WHERE status = 'CLOSED') as closed_trades,
    COALESCE(SUM(realized_pnl) FILTER (WHERE status = 'CLOSED'), 0) as total_realized_pnl,
    COUNT(*) FILTER (WHERE exit_reason = 'TAKE_PROFIT') as take_profit_count,
    COUNT(*) FILTER (WHERE exit_reason = 'STOP_LOSS') as stop_loss_count,
    COUNT(*) FILTER (WHERE exit_reason = 'EXPIRED_WORTHLESS') as expired_worthless_count,
    COUNT(*) FILTER (WHERE exit_reason = 'ASSIGNED') as assigned_count,
    COUNT(*) FILTER (WHERE exit_reason = 'MANUAL') as manual_close_count,
    -- Win rate (TP + expired worthless = wins)
    CASE
        WHEN COUNT(*) FILTER (WHERE status = 'CLOSED') > 0 THEN
            ROUND(
                100.0 * COUNT(*) FILTER (WHERE exit_reason IN ('TAKE_PROFIT', 'EXPIRED_WORTHLESS'))
                / COUNT(*) FILTER (WHERE status = 'CLOSED'),
                1
            )
        ELSE 0
    END as win_rate_pct
FROM trades
WHERE strategy_id = 'spy-put-selling';

-- risk_metrics: Aggregate risk for all open positions
CREATE VIEW risk_metrics AS
SELECT
    COUNT(*) as open_position_count,
    SUM(op.quantity) as total_contracts,
    -- Max loss if all hit stop loss
    SUM((op.expected_sl_price - op.entry_price) * op.quantity * 100) as max_loss,
    -- Max profit if all hit take profit
    SUM((op.entry_price - op.expected_tp_price) * op.quantity * 100) as max_profit,
    -- Aggregate Greeks (for short puts: delta exposure, theta profit)
    SUM(op.delta * op.quantity * 100) as total_delta,
    SUM(op.theta * op.quantity * 100) as total_theta,
    SUM(op.gamma * op.quantity * 100) as total_gamma,
    SUM(op.vega * op.quantity * 100) as total_vega,
    -- Unrealized P&L
    SUM(op.unrealized_pnl) as total_unrealized_pnl
FROM open_positions op;

-- pending_orders: All orders that are not yet filled
CREATE VIEW pending_orders AS
SELECT
    o.*,
    t.symbol,
    t.strike,
    t.expiration,
    t.trade_date
FROM orders o
JOIN trades t ON o.trade_id = t.id
WHERE o.status IN ('PENDING', 'SUBMITTED', 'INACTIVE')
ORDER BY o.created_at DESC;

-- ============================================================================
-- FUNCTIONS
-- ============================================================================

-- Function to update the updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Triggers for auto-updating updated_at
CREATE TRIGGER update_trades_updated_at
    BEFORE UPDATE ON trades
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_orders_updated_at
    BEFORE UPDATE ON orders
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Function to calculate and update realized P&L on trade close
CREATE OR REPLACE FUNCTION calculate_realized_pnl()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.status = 'CLOSED' AND NEW.exit_price IS NOT NULL THEN
        -- For short puts: profit = (entry - exit) * quantity * 100
        NEW.realized_pnl = (NEW.entry_price - NEW.exit_price) * NEW.quantity * 100;

        -- Calculate slippage based on exit reason
        IF NEW.exit_reason = 'TAKE_PROFIT' THEN
            NEW.slippage = NEW.exit_price - NEW.expected_tp_price;
        ELSIF NEW.exit_reason = 'STOP_LOSS' THEN
            NEW.slippage = NEW.exit_price - NEW.expected_sl_price;
        ELSE
            NEW.slippage = 0;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER calculate_pnl_on_close
    BEFORE UPDATE ON trades
    FOR EACH ROW
    WHEN (OLD.status = 'OPEN' AND NEW.status = 'CLOSED')
    EXECUTE FUNCTION calculate_realized_pnl();

-- ============================================================================
-- SAMPLE QUERIES FOR FRONTEND
-- ============================================================================

-- Get all open positions with current data
-- SELECT * FROM open_positions ORDER BY expiration;

-- Get strategy summary
-- SELECT * FROM strategy_summary;

-- Get risk metrics
-- SELECT * FROM risk_metrics;

-- Get pending orders
-- SELECT * FROM pending_orders;

-- Get trade history with P&L
-- SELECT * FROM trades WHERE status = 'CLOSED' ORDER BY exit_time DESC;

-- Get P&L by month
-- SELECT
--     DATE_TRUNC('month', exit_time) as month,
--     SUM(realized_pnl) as monthly_pnl,
--     COUNT(*) as trade_count
-- FROM trades
-- WHERE status = 'CLOSED'
-- GROUP BY DATE_TRUNC('month', exit_time)
-- ORDER BY month DESC;
