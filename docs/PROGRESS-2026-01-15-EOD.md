# Development Progress - 2026-01-15 (End of Day)

## Summary

Today's session focused on fixing the recurring "Cannot have orders on both sides" error and cleaning up database inconsistencies.

---

## Issues Encountered

### 1. Missed Scheduled Trade at 09:35 ET
- Container was running from yesterday but scheduler didn't fire at 09:35
- Root cause: Unknown - needs investigation
- **Status**: Not resolved, investigate tomorrow

### 2. Manual Trade Execution Failed with "Cannot have orders on both sides"
- Parent SELL order for 630P filled at $5.89
- TP/SL orders REJECTED because existing BUY orders (old TP/SL) were present
- Error: "Cannot have open orders on both sides of the same US Option contract"

### 3. Phantom Trades in Database
- Trades 20, 21, 22, 23 were test/phantom entries created at 10:24:16
- Deleted these - now have correct 4 positions

### 4. Dashboard Issues
- "Ready to trade" showing incorrectly (may have been cached)
- API correctly returns `ready_to_trade: false` when gateway not authenticated

---

## Fixes Applied

### 1. Rewrote `place_bracket_order()` with Two-Step Process

**File**: `src/ibkr_spy_puts/ibkr_client.py`

**Step 1: Place parent order (with conflict handling)**
```
1. Find conflicting BUY orders on same contract
2. Cancel them temporarily
3. Place parent SELL order
4. Wait for parent to FILL (up to 60s polling)
5. Re-place cancelled orders with ORIGINAL OCA groups (not combined)
```

**Step 2: Place TP/SL orders (no conflict possible)**
```
1. Parent already filled, no open SELL order
2. Place new TP in new OCA group
3. Place new SL in same new OCA group
```

**Key insight**: The conflict is NOT about OCA groups - it's about IBKR's rule that you cannot have BUY and SELL orders open simultaneously on the same US options contract.

### 2. Database Cleanup

Deleted phantom trades:
```sql
DELETE FROM trades WHERE id IN (20, 21, 22, 23);
```

Current correct positions:
| Trade ID | Date | Strike | Entry Price |
|----------|------|--------|-------------|
| 1 | 2026-01-12 | 630P | $5.59 |
| 17 | 2026-01-13 | 630P | $6.03 |
| 18 | 2026-01-14 | 625P | $6.51 |
| 19 | 2026-01-15 | 630P | $5.81 |

### 3. Added Unit Tests

**File**: `tests/unit/test_bracket_order.py`

8 tests covering:
- Conflict detection on same contract
- Wait for parent fill before placing TP/SL
- Re-place orders with original OCA groups (not combined)
- Different OCA groups stay separate
- Integration tests for full flow

---

## Current State (After Fixes)

### Positions (4 total) ✅
- 630P × 3 (trades 1, 17, 19)
- 625P × 1 (trade 18)
- All expiring 2026-04-17

### IBKR Connection ✅
- Gateway reconnected with 2FA at 15:05 ET
- All orders verified and fixed

### Orders in IBKR (Verified & Corrected) ✅
| Trade | Contract | Entry | TP Price | SL Price | OCA Group |
|-------|----------|-------|----------|----------|-----------|
| 1 | 630P | $5.59 | $2.24 | $16.77 | OCA_T1_* |
| 17 | 630P | $6.03 | $2.41 | $18.09 | OCA_T17_* |
| 18 | 625P | $6.51 | $2.59 | $19.44 | OCA_T18_* |
| 19 | 630P | $5.89 | $2.36 | $17.67 | OCA_T19_* |

**All 8 orders confirmed in IBKR with separate OCA groups per trade.**

### Trade 19 Price Correction
- Originally recorded: $5.81 (limit price used by mistake)
- Actual fill price: $5.89 (confirmed via IBKR executions)
- TP/SL recalculated: $2.36 / $17.67 (was $2.32 / $17.43)

---

## Tomorrow's Tasks

1. ~~**Reconnect IBKR Gateway**~~ ✅ Done

2. ~~**Verify Orders in IBKR**~~ ✅ Done - Fixed orders with separate OCA groups

3. **Investigate Scheduler Issue**
   - Why didn't the scheduled trade fire at 09:35?
   - Check scheduler logs and APScheduler configuration

4. **Test the Fix**
   - Next trading day, verify the two-step bracket order logic works
   - Confirm TP/SL orders are placed correctly after parent fills

---

## Files Modified Today

| File | Changes |
|------|---------|
| `src/ibkr_spy_puts/ibkr_client.py` | Rewrote `place_bracket_order()` with two-step process |
| `src/ibkr_spy_puts/database.py` | Fixed order query to filter by status, added `get_trade_history()` |
| `src/ibkr_spy_puts/api.py` | Added `/api/trade-history` endpoint, passed trade_history to dashboard |
| `src/ibkr_spy_puts/templates/dashboard.html` | Added Trade History section |
| `tests/unit/test_bracket_order.py` | New file with 8 unit tests |
| Database | Deleted phantom trades 20-23, synced orders with IBKR |

## Additional Fixes (Later Session)

### Dashboard Position-Order Display
- **Problem**: Each position showed 4 orders (including cancelled ones)
- **Fix**: Added status filter to query: `AND status IN ('SUBMITTED', 'PRESUBMITTED', 'PENDING')`
- **Result**: Each position now correctly shows only 2 active orders (TP + SL)

### Trade History Feature
- Added `get_trade_history()` method to database
- Added `/api/trade-history` API endpoint
- Added Trade History section to dashboard showing all executed trades:
  | Time | Action | Contract | Qty | Price |
  |------|--------|----------|-----|-------|
  | 2026-01-15 14:39 | SELL | SPY 630P 04/17 | 1 | $5.89 |
  | 2026-01-14 14:46 | SELL | SPY 625P 04/17 | 1 | $6.51 |
  | 2026-01-13 16:10 | SELL | SPY 630P 04/17 | 1 | $6.03 |
  | 2026-01-12 22:20 | SELL | SPY 630P 04/17 | 1 | $5.59 |

---

## Key Learnings

1. **IBKR Rule**: Cannot have open orders on both sides (BUY + SELL) of the same US options contract simultaneously

2. **OCA Groups**: Don't combine different trades' OCA groups - each trade's TP/SL should stay in its original OCA group with original prices

3. **Order Flow for Adding to Position**:
   - Cancel existing TP/SL (temporarily)
   - Place parent order
   - Wait for parent to FILL
   - Re-place old TP/SL with original OCA groups
   - Place new TP/SL with new OCA group
