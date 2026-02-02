#!/usr/bin/env python3
"""Main entry point for the IBKR SPY put selling bot.

Usage:
    # Execute trade immediately (one-shot mode)
    poetry run python -m ibkr_spy_puts.main

    # Dry run (no actual orders placed)
    poetry run python -m ibkr_spy_puts.main --dry-run

    # Dry run with mock data (no TWS needed)
    poetry run python -m ibkr_spy_puts.main --mock --dry-run

    # Run in scheduler mode (continuous, trades daily at scheduled time)
    poetry run python -m ibkr_spy_puts.main --scheduler

    # Scheduler mode with immediate first trade
    poetry run python -m ibkr_spy_puts.main --scheduler --run-now
"""

import argparse
import sys


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="IBKR SPY Put Selling Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Execute trade now (one-shot, writes to database)
  python -m ibkr_spy_puts.main

  # Dry run with mock data (no TWS needed)
  python -m ibkr_spy_puts.main --mock --dry-run

  # Run scheduler (trades daily at configured time)
  python -m ibkr_spy_puts.main --scheduler

  # Scheduler with immediate first trade
  python -m ibkr_spy_puts.main --scheduler --run-now
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
        help="TWS port override",
    )
    parser.add_argument(
        "--scheduler",
        action="store_true",
        help="Run in scheduler mode (continuous, trades daily at scheduled time)",
    )
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="Execute trade immediately (with --scheduler: also continue scheduling)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force run on non-trading days (weekends/holidays)",
    )

    args = parser.parse_args()

    from ibkr_spy_puts.scheduler import run_scheduler, create_trade_function

    if args.scheduler:
        # Scheduler mode: run continuously, execute at scheduled times
        run_scheduler(
            use_mock=args.mock,
            dry_run=args.dry_run,
            port=args.port,
            run_immediately=args.run_now,
            force_run=args.force,
        )
    else:
        # One-shot mode: execute trade immediately and exit
        # Uses the same trade function as scheduler (includes DB writes)
        print("=" * 60)
        print("IBKR SPY PUT SELLING BOT - One-shot Mode")
        print("=" * 60)
        print(f"Mode: {'MOCK' if args.mock else 'LIVE'}")
        print(f"Dry Run: {args.dry_run}")
        print("=" * 60)
        print()

        trade_func = create_trade_function(
            use_mock=args.mock,
            dry_run=args.dry_run,
            port=args.port,
        )

        # Execute the trade (same function scheduler uses)
        trade_func()

        print()
        print("Done.")


if __name__ == "__main__":
    main()
