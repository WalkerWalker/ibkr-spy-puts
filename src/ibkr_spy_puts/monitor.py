"""Order monitoring service for tracking bracket order fills.

This module provides:
1. Polling-based order status monitoring
2. Database synchronization when orders fill
3. Position snapshot updates with live greeks

Usage:
    # Run once to sync orders
    poetry run python -m ibkr_spy_puts.monitor --once

    # Run continuously (every 5 minutes during market hours)
    poetry run python -m ibkr_spy_puts.monitor --continuous
"""

import argparse
import time
from datetime import date, datetime
from decimal import Decimal

from ibkr_spy_puts.config import DatabaseSettings, TWSSettings
from ibkr_spy_puts.database import Database, Order, PositionSnapshot, Trade
from ibkr_spy_puts.ibkr_client import IBKRClient


class OrderMonitor:
    """Monitors IBKR orders and syncs with database."""

    def __init__(
        self,
        tws_settings: TWSSettings | None = None,
        db_settings: DatabaseSettings | None = None,
    ):
        """Initialize order monitor.

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

    def sync_orders(self) -> dict:
        """Sync order status from IBKR to database.

        Returns:
            Dict with sync statistics.
        """
        if not self.client or not self.db:
            raise RuntimeError("Not connected")

        stats = {
            "orders_checked": 0,
            "orders_updated": 0,
            "trades_closed": 0,
            "errors": 0,
        }

        # Get all trades (order status + execution info)
        ibkr_trades = self.client.ib.trades()
        print(f"Found {len(ibkr_trades)} trades in IBKR")

        # Get pending orders from database
        pending_orders = self.db.get_pending_orders()
        print(f"Found {len(pending_orders)} pending orders in database")

        # Create lookup by IBKR order ID
        db_orders_by_ibkr_id = {
            o["ibkr_order_id"]: o for o in pending_orders if o.get("ibkr_order_id")
        }

        # Check each IBKR trade against database
        for trade in ibkr_trades:
            stats["orders_checked"] += 1
            order_id = trade.order.orderId
            perm_id = trade.order.permId
            status = trade.orderStatus.status

            # Find matching database order
            db_order = db_orders_by_ibkr_id.get(order_id)
            if not db_order:
                continue  # Order not from our strategy

            # Check if status changed
            if status == "Filled" and db_order["status"] != "FILLED":
                try:
                    self._handle_fill(trade, db_order)
                    stats["orders_updated"] += 1

                    # If this was a bracket order fill, close the trade
                    if db_order["order_type"] in ("TAKE_PROFIT", "STOP_LOSS"):
                        self._close_trade(trade, db_order)
                        stats["trades_closed"] += 1

                except Exception as e:
                    print(f"ERROR updating order {order_id}: {e}")
                    stats["errors"] += 1

            elif status in ("Cancelled", "Inactive") and db_order["status"] not in (
                "CANCELLED",
                "INACTIVE",
            ):
                try:
                    self.db.update_order_status(
                        db_order["id"], status.upper()
                    )
                    stats["orders_updated"] += 1
                except Exception as e:
                    print(f"ERROR updating order {order_id}: {e}")
                    stats["errors"] += 1

        return stats

    def _handle_fill(self, trade, db_order: dict):
        """Handle an order fill.

        Args:
            trade: IBKR trade object.
            db_order: Database order record.
        """
        fill_price = Decimal(str(trade.orderStatus.avgFillPrice))
        fill_time = datetime.now()  # IBKR doesn't always provide exact fill time

        # Try to get actual fill time from executions
        if trade.fills:
            fill_time = trade.fills[-1].time

        print(
            f"Order {db_order['ibkr_order_id']} ({db_order['order_type']}) "
            f"filled at ${fill_price}"
        )

        self.db.update_order_fill(
            order_id=db_order["id"],
            fill_price=fill_price,
            fill_time=fill_time,
            filled_quantity=trade.orderStatus.filled,
        )

    def _close_trade(self, trade, db_order: dict):
        """Close a trade when bracket order fills.

        Args:
            trade: IBKR trade object.
            db_order: Database order record.
        """
        fill_price = Decimal(str(trade.orderStatus.avgFillPrice))
        fill_time = datetime.now()

        if trade.fills:
            fill_time = trade.fills[-1].time

        exit_reason = db_order["order_type"]  # TAKE_PROFIT or STOP_LOSS

        print(
            f"Closing trade {db_order['trade_id']} - {exit_reason} at ${fill_price}"
        )

        self.db.update_trade_exit(
            trade_id=db_order["trade_id"],
            exit_price=fill_price,
            exit_time=fill_time,
            exit_reason=exit_reason,
        )

        # Cancel the other bracket order in database
        # (IBKR OCO handles the actual cancellation)
        orders = self.db.get_orders_for_trade(db_order["trade_id"])
        for order in orders:
            if order.order_type != db_order["order_type"] and order.order_type != "PARENT":
                if order.status not in ("FILLED", "CANCELLED"):
                    self.db.update_order_status(order.id, "CANCELLED")
                    print(f"Marked {order.order_type} order as CANCELLED (OCO)")

    def update_position_snapshots(self) -> int:
        """Update position snapshots with current greeks.

        Returns:
            Number of positions updated.
        """
        if not self.client or not self.db:
            raise RuntimeError("Not connected")

        open_trades = self.db.get_open_trades()
        print(f"Updating snapshots for {len(open_trades)} open positions")

        updated = 0
        spy_price = self.client.get_spy_price()

        for trade in open_trades:
            try:
                # Get current option data
                # This requires qualifying the contract and getting greeks
                # For now, we'll create a basic snapshot
                snapshot = PositionSnapshot(
                    trade_id=trade.id,
                    snapshot_time=datetime.now(),
                    underlying_price=Decimal(str(spy_price)) if spy_price else None,
                    days_to_expiry=(trade.expiration - date.today()).days,
                )

                # Calculate unrealized P&L if we had current price
                # For now, just store the snapshot
                self.db.insert_snapshot(snapshot)
                updated += 1

            except Exception as e:
                print(f"ERROR updating snapshot for trade {trade.id}: {e}")

        return updated

    def run_once(self):
        """Run a single sync cycle."""
        print("=" * 60)
        print(f"Order Monitor - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)

        if not self.connect():
            return

        try:
            # Sync order status
            stats = self.sync_orders()
            print(f"\nSync complete:")
            print(f"  Orders checked: {stats['orders_checked']}")
            print(f"  Orders updated: {stats['orders_updated']}")
            print(f"  Trades closed: {stats['trades_closed']}")
            print(f"  Errors: {stats['errors']}")

            # Update position snapshots
            updated = self.update_position_snapshots()
            print(f"  Snapshots updated: {updated}")

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
    parser = argparse.ArgumentParser(description="Order monitoring service")
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
    monitor = OrderMonitor(tws_settings=tws_settings)

    if args.once:
        monitor.run_once()
    elif args.continuous:
        monitor.run_continuous(interval_minutes=args.interval)
    else:
        # Default: run once
        monitor.run_once()


if __name__ == "__main__":
    main()
