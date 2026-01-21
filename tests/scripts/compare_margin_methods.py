#!/usr/bin/env python3
"""Compare margin calculation methods.

This script compares two approaches:
1. Sum of individual whatIfOrder for each position
2. Single whatIfOrder to close all positions at once (basket order)

Run: python tests/scripts/compare_margin_methods.py
"""

import asyncio
import os
import sys
from decimal import Decimal

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from dotenv import load_dotenv
load_dotenv()

from ib_insync import IB, MarketOrder, Option


def main():
    """Compare margin calculation methods."""
    from ibkr_spy_puts.config import TWSSettings

    tws = TWSSettings()
    ib = IB()

    print(f"Connecting to {tws.host}:{tws.port}...")
    ib.connect(tws.host, tws.port, clientId=95, readonly=True, timeout=15)
    print(f"Connected. Account: {ib.managedAccounts()}")

    # Get all SPY put positions (short)
    positions = ib.positions()
    spy_puts = []

    for pos in positions:
        c = pos.contract
        if (c.symbol == "SPY" and
            c.secType == "OPT" and
            getattr(c, "right", "") == "P" and
            pos.position < 0):  # Short position
            spy_puts.append(pos)

    if not spy_puts:
        print("No SPY put positions found.")
        ib.disconnect()
        return

    print(f"\nFound {len(spy_puts)} SPY put position(s):")
    for pos in spy_puts:
        c = pos.contract
        print(f"  {c.strike} strike, exp {c.lastTradeDateOrContractMonth}, qty {int(pos.position)}")

    # Method 1: Sum of individual whatIfOrder
    print("\n" + "="*60)
    print("METHOD 1: Sum of individual whatIfOrder")
    print("="*60)

    individual_margins = []
    individual_total = 0.0

    for pos in spy_puts:
        contract = pos.contract
        quantity = abs(int(pos.position))

        # Qualify the contract
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            print(f"  Could not qualify {contract.localSymbol}")
            continue

        # BUY to close short position
        order = MarketOrder("BUY", quantity)
        whatif = ib.whatIfOrder(qualified[0], order)

        if whatif and whatif.maintMarginChange:
            maint_change = float(whatif.maintMarginChange)
            # Negative change means margin would be released
            margin_for_position = -maint_change if maint_change < 0 else 0
            individual_margins.append({
                "strike": contract.strike,
                "quantity": quantity,
                "margin": margin_for_position,
                "maint_change": maint_change,
            })
            individual_total += margin_for_position
            print(f"  {contract.strike} x{quantity}: margin=${margin_for_position:,.2f} (change={maint_change:,.2f})")
        else:
            print(f"  {contract.strike} x{quantity}: no margin data")

    print(f"\nIndividual sum: ${individual_total:,.2f}")

    # Method 2: Single basket order to close all at once
    print("\n" + "="*60)
    print("METHOD 2: Basket order (close all at once)")
    print("="*60)

    # Create a combo/basket order is complex in IBKR
    # Instead, let's simulate by placing multiple orders in sequence
    # and checking the cumulative margin impact

    # Actually, let's check what the current maintenance margin is
    # and compare after simulating closing all

    account = ib.managedAccounts()[0]
    account_values = ib.accountValues(account)

    current_maint = None
    for av in account_values:
        if av.tag == "MaintMarginReq" and av.currency == "USD":
            current_maint = float(av.value)
            break

    print(f"Current total account maintenance margin: ${current_maint:,.2f}" if current_maint else "Could not get current margin")

    # Try placing a single order for multiple contracts of the same type
    # Group by contract details
    from collections import defaultdict
    grouped = defaultdict(int)
    contracts_map = {}

    for pos in spy_puts:
        c = pos.contract
        key = (c.symbol, c.lastTradeDateOrContractMonth, c.strike, c.right)
        grouped[key] += abs(int(pos.position))
        contracts_map[key] = c

    basket_total = 0.0
    print("\nGrouped positions for basket calculation:")
    for key, total_qty in grouped.items():
        symbol, exp, strike, right = key
        contract = contracts_map[key]

        qualified = ib.qualifyContracts(contract)
        if not qualified:
            print(f"  Could not qualify {strike}")
            continue

        # BUY all at once
        order = MarketOrder("BUY", total_qty)
        whatif = ib.whatIfOrder(qualified[0], order)

        if whatif and whatif.maintMarginChange:
            maint_change = float(whatif.maintMarginChange)
            margin_for_position = -maint_change if maint_change < 0 else 0
            basket_total += margin_for_position
            print(f"  {strike} x{total_qty}: margin=${margin_for_position:,.2f} (change={maint_change:,.2f})")
        else:
            print(f"  {strike} x{total_qty}: no margin data")

    print(f"\nBasket total: ${basket_total:,.2f}")

    # Compare
    print("\n" + "="*60)
    print("COMPARISON")
    print("="*60)
    print(f"Method 1 (sum of individual): ${individual_total:,.2f}")
    print(f"Method 2 (grouped basket):    ${basket_total:,.2f}")

    difference = abs(individual_total - basket_total)
    if difference < 0.01:
        print(f"\nResult: IDENTICAL (difference: ${difference:.2f})")
    else:
        pct_diff = (difference / max(individual_total, basket_total)) * 100 if max(individual_total, basket_total) > 0 else 0
        print(f"\nResult: DIFFERENT by ${difference:,.2f} ({pct_diff:.1f}%)")
        if individual_total > basket_total:
            print("  -> Individual sum is HIGHER (IBKR may apply portfolio discount)")
        else:
            print("  -> Basket is HIGHER (unusual)")

    ib.disconnect()
    print("\nDone.")


if __name__ == "__main__":
    # Set up event loop for ib_insync
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
