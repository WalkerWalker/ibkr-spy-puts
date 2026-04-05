#!/usr/bin/env python3
"""Run the theta engine backtest."""

import sys
from pathlib import Path

# Add backtest dir to path
sys.path.insert(0, str(Path(__file__).parent))

from thetadata_client import download_backtest_data
from backtester import run_backtest, print_summary


def main():
    # Date range - free tier has ~1 year rolling window
    # Use a recent range that we know has data
    start_date = "2025-03-01"
    end_date = "2026-02-28"

    # Download data (cached after first run)
    data = download_backtest_data(
        start_date=start_date,
        end_date=end_date,
        target_dte_range=(60, 120),  # download chains that are 60-120 DTE
    )

    # Run backtest
    results = run_backtest(data["spy_prices"], data["chains"])

    # Print results
    print_summary(results)

    # Save detailed trade log
    output_file = Path(__file__).parent / "data" / "backtest_results.csv"
    results.to_csv(output_file, index=False)
    print(f"\nDetailed trade log saved to: {output_file}")


if __name__ == "__main__":
    main()
