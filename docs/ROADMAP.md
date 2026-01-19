# Roadmap & Future Requirements

## Completed (2026-01-19)

### Database Schema Simplification
- [x] Removed `orders` table - orders are live data from IBKR, not persisted
- [x] Removed `position_snapshots` table - Greeks are fetched live from IBKR
- [x] Simplified `trades` table to pure execution log (SELL/BUY entries)
- [x] Created `positions` table (the book) for tracking open/closed positions

## Future Requirements

### Daily Book Snapshots
**Priority:** Medium
**Status:** Planned

At end of each trading day, capture a snapshot of the entire book:
- Total delta exposure
- Total theta
- Total gamma
- Total vega
- Margin requirement (if available from IBKR)
- Total unrealized P&L

**Table design (tentative):**
```sql
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
    total_gamma DECIMAL(10,4),
    total_vega DECIMAL(10,4),

    -- P&L
    unrealized_pnl DECIMAL(10,2),

    -- Risk metrics
    margin_used DECIMAL(12,2),
    buying_power DECIMAL(12,2),

    -- SPY reference
    spy_price DECIMAL(10,2),

    UNIQUE(snapshot_date)
);
```

**Use cases:**
- Track risk exposure over time
- Analyze theta decay effectiveness
- Monitor margin utilization
- Historical performance analysis

### Position Monitoring Improvements
**Priority:** Low
**Status:** Planned

- Better detection of expired options (check expiration date)
- Fetch exit price from IBKR execution history
- Alert notifications when positions close
