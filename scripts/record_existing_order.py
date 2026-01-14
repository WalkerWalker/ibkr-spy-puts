#!/usr/bin/env python3
"""Record an existing IBKR order into the database.

This script is used to backfill orders that were placed before
the database was set up.

Usage:
    poetry run python scripts/record_existing_order.py --port 7496
"""

import argparse
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Load environment variables from .env
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from ibkr_spy_puts.config import DatabaseSettings, TWSSettings
from ibkr_spy_puts.database import Database, Order, Trade
from ibkr_spy_puts.ibkr_client import IBKRClient


def find_strategy_orders(client: IBKRClient) -> tuple[list, list]:
    """Find orders that match our strategy (SPY puts).

    Args:
        client: Connected IBKR client.

    Returns:
        Tuple of (trades, open_orders) for SPY puts.
    """
    # Get executed trades
    trades = client.ib.trades()
    # Get open orders (pending bracket orders)
    open_orders = client.ib.openOrders()
    open_trades = client.ib.openTrades()
    positions = client.ib.positions()

    print(f"\nFound {len(trades)} total trades in IBKR")
    print(f"Found {len(open_orders)} open orders in IBKR")
    print(f"Found {len(open_trades)} open trades in IBKR")
    print(f"Found {len(positions)} total positions in IBKR")

    # Filter for SPY put options - trades
    spy_put_trades = []
    for trade in trades:
        contract = trade.contract
        if (
            hasattr(contract, "symbol")
            and contract.symbol == "SPY"
            and hasattr(contract, "right")
            and contract.right == "P"
        ):
            spy_put_trades.append(trade)

    # Also check open trades (for pending bracket orders)
    for trade in open_trades:
        contract = trade.contract
        if (
            hasattr(contract, "symbol")
            and contract.symbol == "SPY"
            and hasattr(contract, "right")
            and contract.right == "P"
        ):
            # Avoid duplicates
            if trade not in spy_put_trades:
                spy_put_trades.append(trade)

    print(f"Found {len(spy_put_trades)} SPY put trades/orders")
    return spy_put_trades


def display_trade(trade, index: int):
    """Display trade details.

    Args:
        trade: IBKR trade object.
        index: Display index.
    """
    contract = trade.contract
    order = trade.order
    status = trade.orderStatus

    print(f"\n[{index}] Order ID: {order.orderId} (Perm: {order.permId})")
    print(f"    Contract: {contract.localSymbol}")
    print(f"    Strike: ${contract.strike}")
    print(f"    Expiration: {contract.lastTradeDateOrContractMonth}")
    print(f"    Action: {order.action}")
    print(f"    Quantity: {order.totalQuantity}")
    print(f"    Order Type: {order.orderType}")
    if order.lmtPrice:
        print(f"    Limit Price: ${order.lmtPrice}")
    if order.auxPrice:
        print(f"    Stop Price: ${order.auxPrice}")
    print(f"    Status: {status.status}")
    if status.avgFillPrice:
        print(f"    Fill Price: ${status.avgFillPrice}")
    if order.parentId:
        print(f"    Parent Order ID: {order.parentId}")


def record_bracket_order(
    db: Database,
    parent_trade,
    tp_trade,
    sl_trade,
) -> int:
    """Record a bracket order into the database.

    Args:
        db: Database connection.
        parent_trade: Parent order trade.
        tp_trade: Take profit order trade.
        sl_trade: Stop loss order trade.

    Returns:
        The new trade ID.
    """
    contract = parent_trade.contract
    order = parent_trade.order
    status = parent_trade.orderStatus

    # Parse expiration date
    exp_str = contract.lastTradeDateOrContractMonth
    expiration = datetime.strptime(exp_str, "%Y%m%d").date()

    # Get fill price (or limit price if not filled)
    entry_price = Decimal(str(status.avgFillPrice or order.lmtPrice))

    # Calculate expected TP/SL prices
    tp_price = Decimal(str(tp_trade.order.lmtPrice))
    sl_price = Decimal(str(sl_trade.order.auxPrice))

    # Create trade record
    trade = Trade(
        trade_date=date.today(),
        symbol=contract.symbol,
        strike=Decimal(str(contract.strike)),
        expiration=expiration,
        quantity=int(order.totalQuantity),
        entry_price=entry_price,
        entry_time=datetime.now(),  # Approximate
        expected_tp_price=tp_price,
        expected_sl_price=sl_price,
        status="OPEN" if status.status != "Filled" else "OPEN",
        strategy_id="spy-put-selling",
    )

    trade_id = db.insert_trade(trade)
    print(f"\nCreated trade record: ID={trade_id}")

    # Record parent order
    parent_order = Order(
        trade_id=trade_id,
        ibkr_order_id=order.orderId,
        ibkr_perm_id=order.permId,
        ibkr_con_id=contract.conId,
        order_type="PARENT",
        action=order.action,
        order_class="LMT",
        limit_price=Decimal(str(order.lmtPrice)) if order.lmtPrice else None,
        fill_price=Decimal(str(status.avgFillPrice)) if status.avgFillPrice else None,
        fill_time=datetime.now() if status.status == "Filled" else None,
        quantity=int(order.totalQuantity),
        status="FILLED" if status.status == "Filled" else "SUBMITTED",
        algo_strategy=order.algoStrategy if hasattr(order, "algoStrategy") else None,
    )
    parent_id = db.insert_order(parent_order)
    print(f"Created parent order record: ID={parent_id}")

    # Record take profit order
    tp_order = Order(
        trade_id=trade_id,
        ibkr_order_id=tp_trade.order.orderId,
        ibkr_perm_id=tp_trade.order.permId,
        ibkr_con_id=contract.conId,
        order_type="TAKE_PROFIT",
        action=tp_trade.order.action,
        order_class="LMT",
        limit_price=tp_price,
        quantity=int(tp_trade.order.totalQuantity),
        status=tp_trade.orderStatus.status.upper(),
    )
    tp_id = db.insert_order(tp_order)
    print(f"Created take profit order record: ID={tp_id}")

    # Record stop loss order
    sl_order = Order(
        trade_id=trade_id,
        ibkr_order_id=sl_trade.order.orderId,
        ibkr_perm_id=sl_trade.order.permId,
        ibkr_con_id=contract.conId,
        order_type="STOP_LOSS",
        action=sl_trade.order.action,
        order_class="STP",
        stop_price=sl_price,
        quantity=int(sl_trade.order.totalQuantity),
        status=sl_trade.orderStatus.status.upper(),
    )
    sl_id = db.insert_order(sl_order)
    print(f"Created stop loss order record: ID={sl_id}")

    return trade_id


def main():
    parser = argparse.ArgumentParser(description="Record existing IBKR order to database")
    parser.add_argument("--port", type=int, default=7496, help="TWS port")
    parser.add_argument("--auto", action="store_true", help="Auto-detect and record bracket orders")

    args = parser.parse_args()

    # Connect to TWS
    tws_settings = TWSSettings(port=args.port)
    client = IBKRClient(settings=tws_settings)

    print("Connecting to TWS...")
    if not client.connect():
        print("ERROR: Failed to connect to TWS")
        sys.exit(1)
    print("Connected!")

    # Connect to database
    db_settings = DatabaseSettings()
    db = Database(settings=db_settings)

    print("Connecting to database...")
    if not db.connect():
        print("ERROR: Failed to connect to database")
        client.disconnect()
        sys.exit(1)
    print("Connected!")

    try:
        # Find SPY put orders
        trades = find_strategy_orders(client)

        if not trades:
            print("\nNo SPY put orders found.")
            return

        # Group by parent order (bracket orders share parentId)
        parent_orders = {}
        child_orders = {}

        for trade in trades:
            order = trade.order
            if order.parentId == 0:
                # This is a parent order
                parent_orders[order.orderId] = trade
            else:
                # This is a child order (TP or SL)
                if order.parentId not in child_orders:
                    child_orders[order.parentId] = []
                child_orders[order.parentId].append(trade)

        print(f"\nFound {len(parent_orders)} parent orders")
        print(f"Found {sum(len(v) for v in child_orders.values())} child orders")

        # Display and optionally record each bracket
        for parent_id, parent_trade in parent_orders.items():
            print("\n" + "=" * 60)
            print("BRACKET ORDER")
            print("=" * 60)

            display_trade(parent_trade, 0)

            children = child_orders.get(parent_id, [])
            tp_trade = None
            sl_trade = None

            for i, child in enumerate(children, 1):
                display_trade(child, i)
                if child.order.orderType == "LMT":
                    tp_trade = child
                elif child.order.orderType == "STP":
                    sl_trade = child

            if args.auto and tp_trade and sl_trade:
                # Check if already in database
                existing = db.get_open_trades()
                already_recorded = any(
                    t.strike == Decimal(str(parent_trade.contract.strike))
                    and t.expiration == datetime.strptime(
                        parent_trade.contract.lastTradeDateOrContractMonth, "%Y%m%d"
                    ).date()
                    for t in existing
                )

                if already_recorded:
                    print("\n[SKIPPED] Already recorded in database")
                else:
                    trade_id = record_bracket_order(db, parent_trade, tp_trade, sl_trade)
                    print(f"\n[RECORDED] Trade ID: {trade_id}")
            else:
                print("\nTo record this order, run with --auto flag")

    finally:
        client.disconnect()
        db.disconnect()
        print("\nDisconnected.")


if __name__ == "__main__":
    main()
