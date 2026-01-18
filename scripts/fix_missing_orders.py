#!/usr/bin/env python3
"""Fix missing TP/SL orders for positions in the database.

This script checks all open positions and ensures they have corresponding
TP/SL orders in IBKR. If orders are missing, it creates them.

Usage:
    docker exec ibkr-bot-paper python3 /app/scripts/fix_missing_orders.py
"""

import time
from datetime import datetime
from decimal import Decimal

from ib_insync import IB, LimitOrder, Option, Order

from ibkr_spy_puts.config import DatabaseSettings, TWSSettings
from ibkr_spy_puts.database import Database


def main():
    print("=" * 60)
    print("Fix Missing TP/SL Orders")
    print("=" * 60)

    # Connect to database
    db_settings = DatabaseSettings()
    db = Database(settings=db_settings)
    if not db.connect():
        print("ERROR: Failed to connect to database")
        return

    # Connect to IBKR
    tws_settings = TWSSettings()
    ib = IB()
    try:
        # CRITICAL: Must use clientId 0 (master) or the SAME clientId as the scheduler
        # to be able to cancel/modify orders. IBKR only allows the clientId that placed
        # an order (or clientId 0) to cancel it. This was a bug that caused "OrderId
        # not found" errors when this script used a different clientId.
        ib.connect(
            host=tws_settings.host,
            port=tws_settings.port,
            clientId=tws_settings.client_id,  # Must match scheduler's clientId
            readonly=False,
        )
        ib.sleep(2)
    except Exception as e:
        print(f"ERROR: Failed to connect to TWS: {e}")
        db.disconnect()
        return

    print(f"Connected to TWS and database")

    # Get all open positions from database
    open_trades = db.get_open_trades()
    print(f"Found {len(open_trades)} open positions in database")

    # Get all open orders from IBKR
    ib.reqAllOpenOrders()
    ib.sleep(2)
    open_orders = ib.openTrades()
    print(f"Found {len(open_orders)} open orders in IBKR")

    # Build a map of existing orders by contract
    # Key: (conId or (symbol, expiration, strike, right)), Value: list of orders
    existing_orders = {}
    for trade in open_orders:
        contract = trade.contract
        key = (contract.symbol, contract.lastTradeDateOrContractMonth, contract.strike, contract.right)
        if key not in existing_orders:
            existing_orders[key] = []
        existing_orders[key].append(trade)

    # Check each position
    positions_fixed = 0
    for trade in open_trades:
        exp_str = trade.expiration.strftime("%Y%m%d")
        key = (trade.symbol, exp_str, float(trade.strike), "P")

        print(f"\nPosition ID {trade.id}: {trade.symbol} {trade.strike}P exp {trade.expiration}")
        print(f"  Entry: ${trade.entry_price}, TP: ${trade.expected_tp_price}, SL: ${trade.expected_sl_price}")

        orders_for_contract = existing_orders.get(key, [])
        print(f"  Existing orders for this contract: {len(orders_for_contract)}")

        # Check if TP order exists at expected price
        tp_exists = False
        sl_exists = False
        for order_trade in orders_for_contract:
            order = order_trade.order
            if order.action == "BUY" and order.orderType == "LMT":
                # Check if price matches (within tolerance)
                if abs(order.lmtPrice - float(trade.expected_tp_price)) < 0.01:
                    tp_exists = True
                    print(f"  TP order exists: ${order.lmtPrice}")
            elif order.action == "BUY" and order.orderType == "STP":
                if abs(order.auxPrice - float(trade.expected_sl_price)) < 0.01:
                    sl_exists = True
                    print(f"  SL order exists: ${order.auxPrice}")

        if tp_exists and sl_exists:
            print(f"  Orders OK")
            continue

        # Need to create missing orders
        print(f"  MISSING: TP={not tp_exists}, SL={not sl_exists}")

        # Qualify the contract
        option = Option(trade.symbol, exp_str, float(trade.strike), "P", "SMART")
        qualified = ib.qualifyContracts(option)
        if not qualified:
            print(f"  ERROR: Could not qualify contract")
            continue

        option = qualified[0]
        oca_group = f"OCA_FIX_{int(time.time())}_{trade.id}"

        if not tp_exists:
            tp_price = round(float(trade.expected_tp_price), 2)
            tp_order = LimitOrder(
                action="BUY",
                totalQuantity=trade.quantity,
                lmtPrice=tp_price,
                tif="GTC",
                ocaGroup=oca_group,
                ocaType=3,
                transmit=True,
            )
            tp_trade = ib.placeOrder(option, tp_order)
            print(f"  Created TP order: ID={tp_trade.order.orderId}, price=${tp_price}")

        if not sl_exists:
            sl_price = round(float(trade.expected_sl_price), 2)
            sl_order = Order(
                orderType="STP",
                action="BUY",
                totalQuantity=trade.quantity,
                auxPrice=sl_price,
                tif="GTC",
                ocaGroup=oca_group,
                ocaType=3,
                transmit=True,
            )
            sl_trade = ib.placeOrder(option, sl_order)
            print(f"  Created SL order: ID={sl_trade.order.orderId}, price=${sl_price}")

        positions_fixed += 1

    ib.sleep(3)
    print(f"\n{'=' * 60}")
    print(f"Fixed {positions_fixed} positions with missing orders")

    # Disconnect
    ib.disconnect()
    db.disconnect()
    print("Done")


if __name__ == "__main__":
    main()
