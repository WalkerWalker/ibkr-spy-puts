#!/usr/bin/env python3
"""Manually record a trade into the database.

Use this when you need to backfill a trade that was placed before
the database integration was complete.

Usage:
    poetry run python scripts/manual_record_trade.py
"""

import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Load environment variables
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from ibkr_spy_puts.config import DatabaseSettings
from ibkr_spy_puts.database import Database, Order, Trade


def main():
    # Connect to database
    db_settings = DatabaseSettings()
    db = Database(settings=db_settings)

    print("Connecting to database...")
    if not db.connect():
        print("ERROR: Failed to connect to database")
        sys.exit(1)
    print("Connected!")

    try:
        # Trade details from the order placed on 2026-01-12
        # SPY Apr17'26 $630 Put @ $5.59
        # Take Profit: $2.24 (60% profit = buy back at 40%)
        # Stop Loss: $16.77 (200% loss = buy back at 300%)

        trade = Trade(
            trade_date=date(2026, 1, 12),
            symbol="SPY",
            strike=Decimal("630.00"),
            expiration=date(2026, 4, 17),
            quantity=1,
            entry_price=Decimal("5.59"),
            entry_time=datetime(2026, 1, 12, 16, 0, 0),  # Approximate fill time
            expected_tp_price=Decimal("2.24"),  # 40% of 5.59
            expected_sl_price=Decimal("16.77"),  # 300% of 5.59
            status="OPEN",
            strategy_id="spy-put-selling",
        )

        # Check if already exists
        existing = db.get_open_trades()
        for t in existing:
            if t.strike == trade.strike and t.expiration == trade.expiration:
                print(f"Trade already exists: ID={t.id}")
                print(f"  Strike: ${t.strike}")
                print(f"  Expiration: {t.expiration}")
                print(f"  Entry: ${t.entry_price}")
                return

        # Insert trade
        trade_id = db.insert_trade(trade)
        print(f"\nCreated trade record: ID={trade_id}")
        print(f"  Symbol: {trade.symbol}")
        print(f"  Strike: ${trade.strike}")
        print(f"  Expiration: {trade.expiration}")
        print(f"  Entry Price: ${trade.entry_price}")
        print(f"  Take Profit Target: ${trade.expected_tp_price}")
        print(f"  Stop Loss Target: ${trade.expected_sl_price}")

        # Insert parent order (filled)
        parent = Order(
            trade_id=trade_id,
            ibkr_order_id=None,  # We don't have the ID from earlier
            ibkr_perm_id=1535817303,  # From the query above
            order_type="PARENT",
            action="SELL",
            order_class="LMT",
            limit_price=Decimal("5.59"),
            fill_price=Decimal("5.59"),
            fill_time=datetime(2026, 1, 12, 16, 0, 0),
            quantity=1,
            status="FILLED",
            algo_strategy="Adaptive",
            algo_priority="Normal",
        )
        parent_id = db.insert_order(parent)
        print(f"\nCreated parent order: ID={parent_id}")

        # Note: The bracket orders (TP/SL) don't appear to be active in IBKR
        # They may need to be re-placed manually
        print("\nWARNING: No bracket orders (TP/SL) found in IBKR.")
        print("You may need to manually place take profit and stop loss orders in TWS.")
        print(f"  Take Profit: BUY 1 SPY Apr17'26 $630 Put @ $2.24 limit")
        print(f"  Stop Loss: BUY 1 SPY Apr17'26 $630 Put @ $16.77 stop")

    finally:
        db.disconnect()
        print("\nDone.")


if __name__ == "__main__":
    main()
