"""Position monitoring service for detecting closed positions.

This module provides:
1. Detection of closed positions (TP/SL fills in IBKR)
2. Database synchronization when positions close

Since orders are live data from IBKR (not persisted), this monitor:
- Compares open positions in database with IBKR positions
- When a database position is no longer in IBKR, marks it as closed

Usage:
    # Run once to sync positions
    poetry run python -m ibkr_spy_puts.monitor --once

    # Run continuously (every 5 minutes during market hours)
    poetry run python -m ibkr_spy_puts.monitor --continuous
"""

import argparse
import time
from datetime import datetime
from decimal import Decimal

from ibkr_spy_puts.config import DatabaseSettings, TWSSettings
from ibkr_spy_puts.database import Database, Position, Trade
from ibkr_spy_puts.ibkr_client import IBKRClient


class PositionMonitor:
    """Monitors IBKR positions and syncs with database."""

    def __init__(
        self,
        tws_settings: TWSSettings | None = None,
        db_settings: DatabaseSettings | None = None,
    ):
        """Initialize position monitor.

        Args:
            tws_settings: TWS connection settings.
            db_settings: Database connection settings.
        """
        self.tws_settings = tws_settings or TWSSettings()
        self.db_settings = db_settings or DatabaseSettings()
        self.client: IBKRClient | None = None
        self.db: Database | None = None

    def connect(self) -> bool:
        """Connect to TWS and database.

        Returns:
            True if both connections successful.
        """
        # Connect to database
        self.db = Database(settings=self.db_settings)
        if not self.db.connect():
            print("ERROR: Failed to connect to database")
            return False
        print("Connected to database")

        # Connect to TWS
        self.client = IBKRClient(settings=self.tws_settings)
        if not self.client.connect():
            print("ERROR: Failed to connect to TWS")
            self.db.disconnect()
            return False
        print("Connected to TWS")

        return True

    def disconnect(self):
        """Disconnect from TWS and database."""
        if self.client:
            self.client.disconnect()
            print("Disconnected from TWS")
        if self.db:
            self.db.disconnect()
            print("Disconnected from database")

    def sync_positions(self) -> dict:
        """Sync position status between IBKR and database.

        Detects positions that have been closed (no longer in IBKR)
        and updates the database accordingly.

        Returns:
            Dict with sync statistics.
        """
        if not self.client or not self.db:
            raise RuntimeError("Not connected")

        stats = {
            "db_positions": 0,
            "ibkr_positions": 0,
            "positions_closed": 0,
            "errors": 0,
        }

        # Get open positions from database
        db_positions = self.db.get_open_positions()
        stats["db_positions"] = len(db_positions)
        print(f"Found {len(db_positions)} open positions in database")

        # Get positions from IBKR
        ibkr_positions = self.client.ib.positions()
        ibkr_option_positions = [
            p for p in ibkr_positions if p.contract.secType == "OPT"
        ]
        stats["ibkr_positions"] = len(ibkr_option_positions)
        print(f"Found {len(ibkr_option_positions)} option positions in IBKR")

        # Build lookup of IBKR positions by (symbol, strike, expiration)
        ibkr_lookup = {}
        for pos in ibkr_option_positions:
            c = pos.contract
            # Key: symbol_strike_expiration
            key = (c.symbol, int(c.strike), c.lastTradeDateOrContractMonth)
            ibkr_lookup[key] = pos

        # Check each database position
        for db_pos in db_positions:
            # Build key for lookup
            exp_str = db_pos.expiration.strftime("%Y%m%d")
            key = (db_pos.symbol, int(db_pos.strike), exp_str)

            if key not in ibkr_lookup:
                # Position is no longer in IBKR - it was closed
                try:
                    self._handle_closed_position(db_pos)
                    stats["positions_closed"] += 1
                except Exception as e:
                    print(f"ERROR closing position {db_pos.id}: {e}")
                    stats["errors"] += 1

        return stats

    def _handle_closed_position(self, db_pos: Position):
        """Handle a position that is no longer in IBKR.

        Args:
            db_pos: Database position that was closed.
        """
        print(f"Position closed: {db_pos.symbol} {db_pos.strike}P {db_pos.expiration}")

        # We don't know the exact exit price without checking fills
        # For now, mark as closed with a placeholder
        # In production, you might want to fetch the fill details from IBKR executions

        # Try to get the exit price from recent fills
        exit_price = None
        exit_time = datetime.now()

        # Check recent fills for this contract
        for fill in self.client.ib.fills():
            c = fill.contract
            if (
                c.secType == "OPT"
                and c.symbol == db_pos.symbol
                and int(c.strike) == int(db_pos.strike)
            ):
                exp_str = c.lastTradeDateOrContractMonth
                if exp_str == db_pos.expiration.strftime("%Y%m%d"):
                    # Found a fill for this contract
                    exit_price = Decimal(str(fill.execution.avgPrice))
                    exit_time = fill.execution.time
                    break

        if exit_price:
            # Also log to trades table
            trade = Trade(
                trade_date=exit_time.date(),
                symbol=db_pos.symbol,
                strike=db_pos.strike,
                expiration=db_pos.expiration,
                quantity=db_pos.quantity,
                action="BUY",  # Closing a short put
                price=exit_price,
                fill_time=exit_time,
            )
            self.db.insert_trade(trade)
            print(f"  Exit price: ${exit_price}")

            # Close the position
            self.db.close_position(db_pos.id, exit_price, exit_time)
        else:
            # No fill found - maybe expired worthless
            # Mark as closed without exit price
            print("  Exit price unknown (possibly expired worthless)")
            self.db.close_position(
                db_pos.id, Decimal("0"), exit_time
            )

    def run_once(self):
        """Run a single sync cycle."""
        print("=" * 60)
        print(f"Position Monitor - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)

        if not self.connect():
            return

        try:
            # Sync positions
            stats = self.sync_positions()
            print(f"\nSync complete:")
            print(f"  DB positions: {stats['db_positions']}")
            print(f"  IBKR positions: {stats['ibkr_positions']}")
            print(f"  Positions closed: {stats['positions_closed']}")
            print(f"  Errors: {stats['errors']}")

        finally:
            self.disconnect()

    def run_continuous(self, interval_minutes: int = 5):
        """Run continuous monitoring.

        Args:
            interval_minutes: Minutes between sync cycles.
        """
        print(f"Starting continuous monitoring (every {interval_minutes} min)")
        print("Press Ctrl+C to stop")

        while True:
            try:
                self.run_once()
                print(f"\nNext sync in {interval_minutes} minutes...")
                time.sleep(interval_minutes * 60)
            except KeyboardInterrupt:
                print("\nStopping monitor...")
                break
            except Exception as e:
                print(f"ERROR in monitor loop: {e}")
                print(f"Retrying in {interval_minutes} minutes...")
                time.sleep(interval_minutes * 60)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Position monitoring service")
    parser.add_argument(
        "--port", type=int, default=7496, help="TWS port (7496=live, 7497=paper)"
    )
    parser.add_argument(
        "--once", action="store_true", help="Run once and exit"
    )
    parser.add_argument(
        "--continuous", action="store_true", help="Run continuously"
    )
    parser.add_argument(
        "--interval", type=int, default=5, help="Minutes between syncs (default: 5)"
    )

    args = parser.parse_args()

    tws_settings = TWSSettings(port=args.port)
    monitor = PositionMonitor(tws_settings=tws_settings)

    if args.once:
        monitor.run_once()
    elif args.continuous:
        monitor.run_continuous(interval_minutes=args.interval)
    else:
        # Default: run once
        monitor.run_once()


if __name__ == "__main__":
    main()
