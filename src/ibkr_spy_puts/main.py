#!/usr/bin/env python3
"""Main entry point for the IBKR SPY put selling bot.

Usage:
    # Dry run with mock data (no TWS needed)
    poetry run python -m ibkr_spy_puts.main --mock --dry-run

    # Dry run with live TWS connection
    poetry run python -m ibkr_spy_puts.main --dry-run

    # Execute trade (requires TWS)
    poetry run python -m ibkr_spy_puts.main

    # Use paper trading port
    poetry run python -m ibkr_spy_puts.main --port 7497 --dry-run

    # Run in scheduler mode (continuous, trades daily at market open)
    poetry run python -m ibkr_spy_puts.main --scheduler --port 7497

    # Scheduler mode with dry run
    poetry run python -m ibkr_spy_puts.main --scheduler --dry-run --mock
"""

import argparse
import sys
from pathlib import Path

from ibkr_spy_puts.config import (
    ExitOrderSettings,
    StrategySettings,
    TWSSettings,
    get_settings,
)
from ibkr_spy_puts.strategy import PutSellingStrategy


def create_client(use_mock: bool = False, port: int | None = None):
    """Create the appropriate client.

    Args:
        use_mock: Use mock client with fixture data.
        port: TWS port override.

    Returns:
        IBKRClient or MockIBKRClient instance.
    """
    if use_mock:
        from ibkr_spy_puts.mock_client import MockIBKRClient
        return MockIBKRClient()
    else:
        from ibkr_spy_puts.ibkr_client import IBKRClient
        settings = TWSSettings()
        if port:
            settings = TWSSettings(port=port)
        return IBKRClient(settings=settings)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="IBKR SPY Put Selling Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run with mock data (no TWS needed)
  python -m ibkr_spy_puts.main --mock --dry-run

  # Dry run with live TWS (paper trading)
  python -m ibkr_spy_puts.main --port 7497 --dry-run

  # Execute trade on paper account
  python -m ibkr_spy_puts.main --port 7497
        """,
    )

    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock client with fixture data (no TWS needed)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't actually place orders, just show what would be done",
    )
    parser.add_argument(
        "--port",
        type=int,
        help="TWS port (7496=live, 7497=paper)",
    )
    parser.add_argument(
        "--scheduler",
        action="store_true",
        help="Run in scheduler mode (continuous, trades daily at market open)",
    )
    parser.add_argument(
        "--quantity",
        type=int,
        help="Number of contracts to trade",
    )
    parser.add_argument(
        "--target-dte",
        type=int,
        help="Target days to expiration",
    )
    parser.add_argument(
        "--target-delta",
        type=float,
        help="Target delta (negative for puts, e.g., -0.15)",
    )
    parser.add_argument(
        "--no-exit-orders",
        action="store_true",
        help="Disable exit orders (take profit / stop loss)",
    )
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="Execute trade immediately (use with --scheduler to also continue scheduling)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force run on non-trading days (weekends/holidays) for testing",
    )

    args = parser.parse_args()

    # Scheduler mode
    if args.scheduler:
        from ibkr_spy_puts.scheduler import run_scheduler
        run_scheduler(
            use_mock=args.mock,
            dry_run=args.dry_run,
            port=args.port,
            run_immediately=args.run_now,
            force_run=args.force,
        )
        return

    # Load settings
    settings = get_settings()

    # Override settings from command line
    strategy_settings = StrategySettings(
        quantity=args.quantity or settings.strategy.quantity,
        target_dte=args.target_dte or settings.strategy.target_dte,
        target_delta=args.target_delta or settings.strategy.target_delta,
    )

    exit_settings = ExitOrderSettings(
        enabled=not args.no_exit_orders and settings.exit_orders.enabled,
        take_profit_pct=settings.exit_orders.take_profit_pct,
        stop_loss_pct=settings.exit_orders.stop_loss_pct,
    )

    # Print configuration
    print("=" * 60)
    print("IBKR SPY PUT SELLING BOT")
    print("=" * 60)
    print(f"Mode: {'MOCK' if args.mock else 'LIVE'}")
    print(f"Dry Run: {args.dry_run}")
    if not args.mock:
        port = args.port or settings.tws.port
        print(f"TWS Port: {port} ({'Paper' if port == 7497 else 'Live'})")
    print()
    print("Strategy Settings:")
    print(f"  Symbol: {strategy_settings.symbol}")
    print(f"  Quantity: {strategy_settings.quantity}")
    print(f"  Target DTE: {strategy_settings.target_dte}")
    print(f"  Target Delta: {strategy_settings.target_delta}")
    print()
    print("Exit Order Settings:")
    print(f"  Enabled: {exit_settings.enabled}")
    if exit_settings.enabled:
        print(f"  Take Profit: {exit_settings.take_profit_pct}%")
        print(f"  Stop Loss: {exit_settings.stop_loss_pct}%")
    print("=" * 60)
    print()

    # Create client
    client = create_client(use_mock=args.mock, port=args.port)

    # Connect
    print("Connecting...")
    if not client.connect():
        print("ERROR: Failed to connect. Is TWS running?")
        sys.exit(1)
    print("Connected!")
    print()

    try:
        # Create and run strategy
        strategy = PutSellingStrategy(
            client=client,
            strategy_settings=strategy_settings,
            exit_settings=exit_settings,
        )

        # Create trade order
        order = strategy.create_trade_order()
        if order is None:
            print("ERROR: No suitable option found")
            sys.exit(1)

        # Show trade details
        print(strategy.describe_trade(order))
        print()

        # Execute or dry run
        if args.dry_run:
            print("DRY RUN - Order not placed")
            order, result = strategy.run(dry_run=True)
        else:
            # Confirm before placing real order
            if not args.mock:
                confirm = input("Place this order? (yes/no): ")
                if confirm.lower() != "yes":
                    print("Order cancelled by user")
                    sys.exit(0)

            order, result = strategy.run(dry_run=False)

        # Show result
        print()
        print("Result:")
        print(f"  Success: {result.success}")
        print(f"  Message: {result.message}")
        if result.sell_order_id:
            print(f"  Sell Order ID: {result.sell_order_id}")
        if result.take_profit_order_id:
            print(f"  Take Profit Order ID: {result.take_profit_order_id}")
        if result.stop_loss_order_id:
            print(f"  Stop Loss Order ID: {result.stop_loss_order_id}")

    finally:
        client.disconnect()
        print()
        print("Disconnected.")


if __name__ == "__main__":
    main()
