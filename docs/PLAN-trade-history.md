# Plan: Trade History Feature

## Requirements

Add a simple trade history log showing all executed trades:
- Time of execution
- Action (SELL/BUY)
- Contract (symbol, strike, expiration)
- Quantity
- Fill price

This is **separate** from positions - it's just a record of what trades were executed.

## Display Format

| Time | Action | Contract | Qty | Price |
|------|--------|----------|-----|-------|
| 2026-01-15 14:38 | SELL | SPY 630P 04/17 | 1 | $5.89 |
| 2026-01-14 14:46 | SELL | SPY 625P 04/17 | 1 | $6.51 |
| 2026-01-13 16:10 | SELL | SPY 630P 04/17 | 1 | $6.03 |
| 2026-01-12 22:20 | SELL | SPY 630P 04/17 | 1 | $5.59 |

## Implementation

### 1. Database (database.py)
Add method to get trade history:
```python
def get_trade_history(self) -> list[dict]:
    """Get all executed trades as a simple history log."""
    # Query: time, action, symbol, strike, expiration, quantity, price
    # Order by time DESC (most recent first)
```

### 2. API (api.py)
Add endpoint:
```python
@app.get("/api/trade-history")
async def get_trade_history():
    """Get trade execution history."""
```

### 3. Dashboard (dashboard.html)
Add "Trade History" section:
- Simple table with columns: Time, Action, Contract, Qty, Price
- No expandable rows, no linked orders
- Just a log of executions

## Files to Modify

1. `src/ibkr_spy_puts/database.py` - Add `get_trade_history()`
2. `src/ibkr_spy_puts/api.py` - Add `/api/trade-history` endpoint
3. `src/ibkr_spy_puts/templates/dashboard.html` - Add Trade History section
