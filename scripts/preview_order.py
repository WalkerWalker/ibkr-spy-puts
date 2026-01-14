#!/usr/bin/env python3
"""Preview an order in TWS without submitting it.

This script:
1. Connects to TWS
2. Selects the put option based on strategy settings
3. Creates the bracket order with transmit=False
4. Sends to TWS (appears in TWS but NOT transmitted to exchange)
5. You can review in TWS and manually transmit if you want

Usage:
    poetry run python scripts/preview_order.py --port 7496

The order will appear in TWS under "Pending" with status "Inactive".
You can then:
- Review the order details in TWS
- Transmit it manually by right-clicking -> Transmit
- Or cancel it
"""

import argparse
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ib_insync import LimitOrder, Option

from ibkr_spy_puts.config import BracketSettings, StrategySettings, TWSSettings
from ibkr_spy_puts.ibkr_client import IBKRClient
from ibkr_spy_puts.strategy import PutSellingStrategy


def preview_bracket_order(client: IBKRClient, strategy: PutSellingStrategy):
    """Create bracket order in TWS without transmitting.

    Args:
        client: Connected IBKR client.
        strategy: Strategy instance.

    Returns:
        Tuple of (parent_trade, tp_trade, sl_trade) or None on failure.
    """
    # Create trade order
    order = strategy.create_trade_order()
    if order is None:
        print("ERROR: No suitable option found")
        return None

    # Show trade details
    print(strategy.describe_trade(order))
    print()

    # Get the contract
    contract = order.option.contract

    # Ensure contract is qualified
    if not hasattr(contract, 'conId') or contract.conId == 0:
        # Need to re-qualify the contract
        qualified = client.ib.qualifyContracts(contract)
        if not qualified:
            print("ERROR: Could not qualify contract")
            return None
        contract = qualified[0]

    print(f"Contract: {contract}")
    print(f"ConId: {contract.conId}")
    print()

    # Create bracket order manually with transmit=False
    # This sends orders to TWS but doesn't transmit to exchange

    bracket_prices = order.bracket_prices
    if bracket_prices is None:
        print("ERROR: No bracket prices")
        return None

    # Parent order (SELL put)
    parent = LimitOrder(
        action="SELL",
        totalQuantity=order.quantity,
        lmtPrice=round(order.limit_price, 2),
        transmit=False,  # Don't transmit yet
    )

    # Place parent order to get orderId
    parent_trade = client.ib.placeOrder(contract, parent)
    client.ib.sleep(0.5)

    parent_order_id = parent_trade.order.orderId
    print(f"Parent Order ID: {parent_order_id}")

    # Take profit order (BUY to close at lower price)
    take_profit = LimitOrder(
        action="BUY",
        totalQuantity=order.quantity,
        lmtPrice=round(bracket_prices.take_profit_price, 2),
        parentId=parent_order_id,
        transmit=False,
    )

    tp_trade = client.ib.placeOrder(contract, take_profit)
    client.ib.sleep(0.5)

    print(f"Take Profit Order ID: {tp_trade.order.orderId}")

    # Stop loss order (BUY to close at higher price)
    # For options, use a Stop order that triggers at the stop price
    from ib_insync import StopOrder

    stop_loss = StopOrder(
        action="BUY",
        totalQuantity=order.quantity,
        stopPrice=round(bracket_prices.stop_loss_price, 2),
        parentId=parent_order_id,
        transmit=False,  # Still False - we want to review first!
    )

    sl_trade = client.ib.placeOrder(contract, stop_loss)
    client.ib.sleep(0.5)

    print(f"Stop Loss Order ID: {sl_trade.order.orderId}")

    print()
    print("=" * 60)
    print("ORDERS CREATED IN TWS (NOT TRANSMITTED)")
    print("=" * 60)
    print()
    print("The orders are now visible in TWS under 'Pending Orders'")
    print("with status 'Inactive' (because transmit=False)")
    print()
    print("To submit the order:")
    print("  1. Go to TWS")
    print("  2. Find the orders in the 'Pending' tab")
    print("  3. Right-click on the PARENT order -> 'Transmit'")
    print("  4. This will transmit all linked orders together")
    print()
    print("To cancel:")
    print("  1. Right-click on the order -> 'Cancel'")
    print("  2. Or just close this script without transmitting")
    print()

    return parent_trade, tp_trade, sl_trade


def main():
    parser = argparse.ArgumentParser(description="Preview order in TWS without submitting")
    parser.add_argument("--port", type=int, default=7496, help="TWS port (7496=live, 7497=paper)")
    parser.add_argument("--cancel", action="store_true", help="Cancel the orders after preview")

    args = parser.parse_args()

    settings = TWSSettings(port=args.port)
    strategy_settings = StrategySettings()
    bracket_settings = BracketSettings()

    print("=" * 60)
    print("ORDER PREVIEW (NOT SUBMITTED)")
    print("=" * 60)
    print(f"TWS Port: {args.port}")
    print(f"Strategy: SELL SPY put, {strategy_settings.target_dte} DTE, {strategy_settings.target_delta} delta")
    print(f"Quantity: {strategy_settings.quantity}")
    print(f"Bracket: TP={bracket_settings.take_profit_pct}%, SL={bracket_settings.stop_loss_pct}%")
    print("=" * 60)
    print()

    client = IBKRClient(settings=settings)

    print("Connecting to TWS...")
    if not client.connect():
        print("ERROR: Failed to connect to TWS")
        sys.exit(1)
    print("Connected!")
    print()

    try:
        strategy = PutSellingStrategy(
            client=client,
            strategy_settings=strategy_settings,
            bracket_settings=bracket_settings,
        )

        result = preview_bracket_order(client, strategy)

        if result and args.cancel:
            parent_trade, tp_trade, sl_trade = result
            print("Cancelling orders as requested...")
            client.ib.cancelOrder(sl_trade.order)
            client.ib.cancelOrder(tp_trade.order)
            client.ib.cancelOrder(parent_trade.order)
            client.ib.sleep(1)
            print("Orders cancelled.")
        elif result:
            print("Orders are waiting in TWS for your review.")
            print("Press Enter to disconnect (orders will remain in TWS)...")
            input()

    finally:
        client.disconnect()
        print("Disconnected from TWS.")


if __name__ == "__main__":
    main()
