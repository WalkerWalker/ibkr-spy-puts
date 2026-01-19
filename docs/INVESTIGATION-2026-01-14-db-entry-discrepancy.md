# Investigation: Database Entry Discrepancy (2026-01-14)

## Problem Summary

The database shows different TP/SL prices than what's live in IBKR for trade ID 17.

| Source | Entry/Limit | Take Profit | Stop Loss |
|--------|-------------|-------------|-----------|
| Database trades table | $6.03 | $2.35 | $17.64 |
| Database orders table | $5.88 | $2.35 | $17.64 |
| IBKR Live Orders | (fill $6.03) | $2.41 | $18.09 |

**Expected prices based on fill $6.03:**
- TP = 6.03 × 0.4 = **$2.41** (IBKR is correct)
- SL = 6.03 × 3.0 = **$18.09** (IBKR is correct)

**Expected prices based on limit $5.88:**
- TP = 5.88 × 0.4 = **$2.35** (Database is showing this)
- SL = 5.88 × 3.0 = **$17.64** (Database is showing this)

## Root Cause

**User confirmed**: The order group was entered **later after the original order was removed**.

### What Happened (Reconstructed Timeline)

1. **First attempt**: Bot placed bracket order with limit price ~$5.88
   - Orders were placed in IBKR
   - Database recorded: limit_price=$5.88, TP=$2.35, SL=$17.64

2. **First attempt failed**: Conflicting orders or other issue caused cancellation
   - The parent order and children were cancelled

3. **Second attempt** (possibly manual or re-run):
   - New limit price calculated based on current market: ~$6.01-$6.03
   - New bracket orders placed with TP=$2.41, SL=$18.09
   - These are the orders now live in IBKR

4. **Database not updated**: The database still has the first attempt's prices
   - The trade's `entry_price` was updated to $6.03 (fill price)
   - But `expected_tp_price` and `expected_sl_price` were NOT updated

### Evidence

1. **Timestamp discrepancy** in database:
   - Trade created: 2026-01-13 15:10:13
   - Trade updated: 2026-01-13 15:32:53 (22 minutes later!)

2. **OCA group name mismatch**:
   - Code generates: `BRACKET_{timestamp}` or `OCA_{timestamp}`
   - IBKR shows: `OCA_TODAY_xxx` (different format - suggests manual entry)

3. **Database orders table has `limit_price=$5.88` but trades table has `entry_price=$6.03`**
   - This proves the order was modified/replaced after initial recording

## Code Analysis

### Order Recording Logic (scheduler.py:300-365)

```python
# Trade is recorded with TradeOrder's calculated prices
db_trade = Trade(
    entry_price=Decimal(str(trade_order.limit_price)),  # From TradeOrder
    expected_tp_price=Decimal(str(trade_order.bracket_prices.take_profit_price)),
    expected_sl_price=Decimal(str(trade_order.bracket_prices.stop_loss_price)),
    ...
)

# Orders are also recorded with same prices
parent_order = Order(limit_price=Decimal(str(trade_order.limit_price)), ...)
tp_order = Order(limit_price=Decimal(str(trade_order.bracket_prices.take_profit_price)), ...)
sl_order = Order(stop_price=Decimal(str(trade_order.bracket_prices.stop_loss_price)), ...)
```

### Conflicting Order Handling (ibkr_client.py:424-615)

The code does handle conflicting orders:
1. Finds conflicting orders (opposite side on same contract)
2. Uses `globalCancel()` to cancel all open orders
3. Places new bracket order
4. Re-places cancelled orders as OCA group

**BUT**: If the strategy is re-run (e.g., manually or by scheduler retry), it creates a NEW trade record with different prices, OR updates the existing record inconsistently.

## Issues Identified

1. **No synchronization**: Database prices don't update when orders are modified/replaced in IBKR
2. **No fill-price update**: When parent order fills, TP/SL should recalculate based on actual fill price
3. **Multiple attempts create inconsistency**: Each strategy run uses current market prices

## Recommended Fixes

### Option A: Update on Fill (Recommended)

When the parent order fills, update the trade record with actual fill price and recalculate TP/SL:

```python
# When order fill is detected:
actual_fill_price = order_status.avgFillPrice
tp_price = actual_fill_price * (1 - take_profit_pct)  # 0.6
sl_price = actual_fill_price * stop_loss_multiplier   # 3.0

db.update_trade(trade_id, {
    "entry_price": actual_fill_price,
    "expected_tp_price": tp_price,
    "expected_sl_price": sl_price,
})
```

### Option B: Verify Against IBKR

Add a reconciliation step that compares database orders with IBKR live orders and updates discrepancies.

### Option C: Record After Fill Only

Don't record trade until parent order actually fills, ensuring we have the correct fill price.

## Immediate Action Items

1. [ ] Manually correct trade 17's TP/SL in database to match IBKR ($2.41, $18.09)
2. [ ] Add order fill monitoring to update database when fills occur
3. [ ] Add reconciliation check on dashboard showing DB vs IBKR price discrepancies
4. [ ] Consider adding idempotency check to prevent duplicate trade records on retry

## Related Files

- `src/ibkr_spy_puts/scheduler.py:300-365` - Trade recording logic
- `src/ibkr_spy_puts/ibkr_client.py:424-615` - Conflicting order handling
- `src/ibkr_spy_puts/strategy.py` - Trade order calculation
- `src/ibkr_spy_puts/database.py` - Database operations

## Status

**Investigation complete.** Root cause identified as multiple order attempts with different prices, and database recording prices from first attempt while IBKR has orders from later attempt.
