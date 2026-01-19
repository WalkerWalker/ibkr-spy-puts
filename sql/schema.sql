-- IBKR SPY Put Selling Bot - Database Schema
-- PostgreSQL 14+

-- Enable UUID extension for potential future use
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- TABLES
-- ============================================================================

-- trades: Pure execution log - one record per filled order
-- Every SELL (open position) and BUY (close position) is recorded here.
-- This is historical data only - no status tracking.
CREATE TABLE trades (
    id SERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    symbol VARCHAR(10) NOT NULL DEFAULT 'SPY',
    strike DECIMAL(10,2) NOT NULL,
    expiration DATE NOT NULL,
    quantity INT NOT NULL DEFAULT 1,

    -- Execution details
    action VARCHAR(10) NOT NULL,  -- SELL (open) or BUY (close)
    price DECIMAL(10,4) NOT NULL,
    fill_time TIMESTAMP WITH TIME ZONE NOT NULL,
    commission DECIMAL(10,4) DEFAULT 0,  -- IBKR commission

    -- Strategy tracking
    strategy_id VARCHAR(50) NOT NULL DEFAULT 'spy-put-selling',

    -- Metadata
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    CONSTRAINT valid_trade_action CHECK (action IN ('BUY', 'SELL'))
);

-- Indexes for common queries
CREATE INDEX idx_trades_trade_date ON trades(trade_date);
CREATE INDEX idx_trades_strategy_id ON trades(strategy_id);
CREATE INDEX idx_trades_symbol_strike_exp ON trades(symbol, strike, expiration);

-- positions: The book - tracks all positions with open/closed status
-- Each position represents a short put that was opened.
-- When closed, exit details are populated.
CREATE TABLE positions (
    id SERIAL PRIMARY KEY,

    -- Contract details
    symbol VARCHAR(10) NOT NULL DEFAULT 'SPY',
    strike DECIMAL(10,2) NOT NULL,
    expiration DATE NOT NULL,
    quantity INT NOT NULL DEFAULT 1,

    -- Entry details
    entry_price DECIMAL(10,4) NOT NULL,
    entry_time TIMESTAMP WITH TIME ZONE NOT NULL,

    -- Exit details (null while open)
    exit_price DECIMAL(10,4),
    exit_time TIMESTAMP WITH TIME ZONE,

    -- Expected bracket order prices (for verification against IBKR)
    expected_tp_price DECIMAL(10,4) NOT NULL,  -- Take profit price
    expected_sl_price DECIMAL(10,4) NOT NULL,  -- Stop loss price

    -- Status
    status VARCHAR(10) NOT NULL DEFAULT 'OPEN',
    strategy_id VARCHAR(50) NOT NULL DEFAULT 'spy-put-selling',

    -- Metadata
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    CONSTRAINT valid_position_status CHECK (status IN ('OPEN', 'CLOSED'))
);

-- Indexes for common queries
CREATE INDEX idx_positions_status ON positions(status);
CREATE INDEX idx_positions_expiration ON positions(expiration);
CREATE INDEX idx_positions_strategy ON positions(strategy_id);

-- book_snapshots: Daily snapshot of portfolio metrics
-- Captured at end of each trading day for historical tracking.
CREATE TABLE book_snapshots (
    id SERIAL PRIMARY KEY,
    snapshot_date DATE NOT NULL,
    snapshot_time TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    -- Position counts
    open_positions INT NOT NULL,
    total_contracts INT NOT NULL,

    -- Greeks (aggregated across all positions)
    total_delta DECIMAL(10,4),
    total_theta DECIMAL(10,4),
    total_gamma DECIMAL(10,6),
    total_vega DECIMAL(10,4),

    -- P&L
    unrealized_pnl DECIMAL(12,2),

    -- Risk metrics
    maintenance_margin DECIMAL(12,2),

    -- SPY reference
    spy_price DECIMAL(10,2),

    -- One snapshot per day
    UNIQUE(snapshot_date)
);

-- Index for date queries
CREATE INDEX idx_book_snapshots_date ON book_snapshots(snapshot_date);

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

-- Trigger for auto-updating updated_at on positions
CREATE TRIGGER update_positions_updated_at
    BEFORE UPDATE ON positions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- VIEWS (convenience queries)
-- ============================================================================

-- Open positions view
CREATE VIEW open_positions AS
SELECT
    id,
    symbol,
    strike,
    expiration,
    quantity,
    entry_price,
    entry_time,
    expected_tp_price,
    expected_sl_price,
    (expiration - CURRENT_DATE) as days_to_expiry,
    strategy_id,
    created_at
FROM positions
WHERE status = 'OPEN'
ORDER BY expiration, strike;

-- Strategy summary view
CREATE VIEW strategy_summary AS
SELECT
    COUNT(*) FILTER (WHERE status = 'OPEN') as open_positions,
    COUNT(*) FILTER (WHERE status = 'CLOSED') as closed_positions,
    COALESCE(SUM(entry_price * quantity * 100) FILTER (WHERE status = 'OPEN'), 0) as open_premium,
    COALESCE(SUM((entry_price - exit_price) * quantity * 100) FILTER (WHERE status = 'CLOSED'), 0) as realized_pnl
FROM positions
WHERE strategy_id = 'spy-put-selling';
