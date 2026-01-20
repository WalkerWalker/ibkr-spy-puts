#!/usr/bin/env python3
"""Test whatIfOrder API for margin calculation.

This simulates closing all SPY put positions to calculate
the margin that would be released.

Usage:
    # Paper trading (local TWS)
    poetry run python tests/scripts/test_whatif_margin.py --port 7497

    # Paper trading (local IB Gateway)
    poetry run python tests/scripts/test_whatif_margin.py --port 4002
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from ib_insync import IB, Option, MarketOrder


def calculate_margin_impact(host: str, port: int):
    """Calculate margin that would be released by closing all SPY put positions."""

    # Create event loop for ib_insync
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    ib = IB()

    print(f"Connecting to {host}:{port}...")
    try:
        ib.connect(host, port, clientId=50, readonly=True, timeout=15)
    except Exception as e:
        print(f"Connection failed: {e}")
        return None

    print(f"Connected. Account: {ib.managedAccounts()}")

    try:
        # Get all positions
        positions = ib.positions()
        print(f"\nFound {len(positions)} total positions")

        # Filter to SPY put options (short positions)
        spy_puts = []
        for pos in positions:
            c = pos.contract
            if (c.symbol == "SPY" and
                c.secType == "OPT" and
                getattr(c, "right", "") == "P" and
                pos.position < 0):  # Short position
                spy_puts.append(pos)
                print(f"  SPY Put: {c.strike} strike, exp {c.lastTradeDateOrContractMonth}, qty {pos.position}")

        if not spy_puts:
            print("\nNo SPY put positions found.")
            ib.disconnect()
            return None

        print(f"\nFound {len(spy_puts)} SPY put position(s) to analyze")

        # Calculate total margin impact by simulating closing each position
        total_maint_margin_change = 0.0
        total_init_margin_change = 0.0

        for pos in spy_puts:
            contract = pos.contract
            quantity = abs(int(pos.position))  # Positive quantity for BUY order

            # Qualify the contract
            qualified = ib.qualifyContracts(contract)
            if not qualified:
                print(f"  Could not qualify {contract.localSymbol}")
                continue

            # Create a market order to close (BUY to close short)
            order = MarketOrder("BUY", quantity)

            # Use whatIfOrder to simulate
            print(f"\nSimulating close of {quantity} x {contract.strike} put...")
            whatif = ib.whatIfOrder(qualified[0], order)

            if whatif:
                maint_change = float(whatif.maintMarginChange) if whatif.maintMarginChange else 0
                init_change = float(whatif.initMarginChange) if whatif.initMarginChange else 0

                print(f"  Maint margin change: ${maint_change:,.2f}")
                print(f"  Init margin change: ${init_change:,.2f}")

                total_maint_margin_change += maint_change
                total_init_margin_change += init_change
            else:
                print(f"  whatIfOrder returned None")

        print("\n" + "=" * 50)
        print("TOTAL MARGIN IMPACT (if all SPY puts closed):")
        print(f"  Maintenance margin change: ${total_maint_margin_change:,.2f}")
        print(f"  Initial margin change: ${total_init_margin_change:,.2f}")
        print("=" * 50)

        # Negative means margin would be released
        margin_used = -total_maint_margin_change if total_maint_margin_change < 0 else 0
        print(f"\nMargin used by SPY puts: ${margin_used:,.2f}")

        return {
            "maint_margin_change": total_maint_margin_change,
            "init_margin_change": total_init_margin_change,
            "margin_used_by_puts": margin_used,
        }

    finally:
        ib.disconnect()
        print("\nDisconnected.")


def main():
    parser = argparse.ArgumentParser(description="Test whatIfOrder margin calculation")
    parser.add_argument("--host", default="127.0.0.1", help="TWS/Gateway host")
    parser.add_argument("--port", type=int, default=7497, help="TWS/Gateway port (7497=paper TWS, 4002=paper gateway)")
    args = parser.parse_args()

    print("=" * 50)
    print("Testing whatIfOrder Margin Calculation")
    print("=" * 50)

    result = calculate_margin_impact(args.host, args.port)

    if result:
        print("\nResult:", result)


if __name__ == "__main__":
    main()
