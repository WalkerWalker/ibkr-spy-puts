#!/usr/bin/env python3
"""Create a test position with TP/SL orders for conflict testing.

This script creates simulated TP/SL orders on a specific strike,
allowing you to test how the scheduler handles conflicting orders
when trying to sell a new put on the same strike.

Usage:
    # From host (paper trading mode):
    python tests/scripts/create_test_position.py --strike 580 --tp 2.00 --sl 15.00

    # From container:
    docker exec ibkr-bot python3 /app/tests/scripts/create_test_position.py --strike 580
"""

import argparse
import asyncio
import os
import time

asyncio.set_event_loop(asyncio.new_event_loop())

from ib_insync import IB, Option, LimitOrder, StopOrder


def create_test_orders(
    host: str,
    port: int,
    strike: float,
    expiration: str,
    tp_price: float,
    sl_price: float,
    client_id: int = 50,
):
    """Create TP/SL orders for a test position."""
    ib = IB()
    print(f"Connecting to {host}:{port}...")
    ib.connect(host, port, clientId=client_id, timeout=20)
    print("Connected!")

    # Create option contract
    opt = Option("SPY", expiration, strike, "P", "SMART")
    qualified = ib.qualifyContracts(opt)
    if not qualified:
        print(f"Failed to qualify contract: SPY {strike}P {expiration}")
        ib.disconnect()
        return False

    contract = qualified[0]
    oca_group = f"TEST_OCA_{int(time.time())}"

    print(f"\nCreating test orders for SPY {strike}P {expiration}:")
    print(f"  Take Profit: BUY LMT @ ${tp_price}")
    print(f"  Stop Loss: BUY STP @ ${sl_price}")
    print(f"  OCA Group: {oca_group}")

    # Take profit order
    tp_order = LimitOrder(
        action="BUY",
        totalQuantity=1,
        lmtPrice=tp_price,
        tif="GTC",
        ocaGroup=oca_group,
        ocaType=3,
    )

    # Stop loss order
    sl_order = StopOrder(
        action="BUY",
        totalQuantity=1,
        stopPrice=sl_price,
        tif="GTC",
        ocaGroup=oca_group,
        ocaType=3,
    )

    tp_trade = ib.placeOrder(contract, tp_order)
    sl_trade = ib.placeOrder(contract, sl_order)

    ib.sleep(2)

    print(f"\nOrders placed:")
    print(f"  TP Order ID: {tp_trade.order.orderId} - Status: {tp_trade.orderStatus.status}")
    print(f"  SL Order ID: {sl_trade.order.orderId} - Status: {sl_trade.orderStatus.status}")

    # Verify
    ib.reqAllOpenOrders()
    ib.sleep(2)
    open_orders = ib.openTrades()
    print(f"\nTotal open orders: {len(open_orders)}")

    ib.disconnect()
    print("\nDone! Now trigger the scheduler to test conflict handling.")
    return True


def main():
    parser = argparse.ArgumentParser(description="Create test TP/SL orders for conflict testing")
    parser.add_argument("--host", default=os.getenv("TWS_HOST", "ib-gateway"), help="TWS/Gateway host")
    parser.add_argument("--port", type=int, default=int(os.getenv("TWS_PORT", "4003")), help="TWS/Gateway port")
    parser.add_argument("--strike", type=float, required=True, help="Strike price for the test position")
    parser.add_argument("--expiration", default="20260417", help="Expiration date (YYYYMMDD)")
    parser.add_argument("--tp", type=float, default=2.00, help="Take profit price")
    parser.add_argument("--sl", type=float, default=15.00, help="Stop loss price")
    parser.add_argument("--client-id", type=int, default=50, help="IBKR client ID")

    args = parser.parse_args()

    create_test_orders(
        host=args.host,
        port=args.port,
        strike=args.strike,
        expiration=args.expiration,
        tp_price=args.tp,
        sl_price=args.sl,
        client_id=args.client_id,
    )


if __name__ == "__main__":
    main()
