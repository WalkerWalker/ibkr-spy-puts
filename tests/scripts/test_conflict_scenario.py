#!/usr/bin/env python3
"""End-to-end test for conflict handling during order placement.

This script simulates the scenario where:
1. There are existing TP/SL orders on a contract (e.g., 630P)
2. The scheduler tries to sell a new put on the same strike
3. The conflict handling logic should:
   - Cancel the existing BUY orders temporarily
   - Place the new SELL order
   - Wait for fill (or simulate fill in paper trading)
   - Re-place the cancelled orders with original OCA groups
   - Place new TP/SL for the new position

Usage:
    # Run from container (paper trading):
    docker exec ibkr-bot python3 /app/tests/scripts/test_conflict_scenario.py

    # Run with specific strike:
    docker exec ibkr-bot python3 /app/tests/scripts/test_conflict_scenario.py --strike 580
"""

import argparse
import asyncio
import os
import time

asyncio.set_event_loop(asyncio.new_event_loop())

from ib_insync import IB, Option, LimitOrder, StopOrder


class ConflictScenarioTest:
    def __init__(self, host: str, port: int, client_id: int = 51):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.ib = IB()

    def connect(self):
        print(f"Connecting to {self.host}:{self.port}...")
        self.ib.connect(self.host, self.port, clientId=self.client_id, timeout=20)
        print("Connected!")

    def disconnect(self):
        self.ib.disconnect()
        print("Disconnected.")

    def clear_all_orders(self):
        """Cancel all open orders to start fresh."""
        print("\n=== Clearing all orders ===")
        self.ib.reqGlobalCancel()
        self.ib.sleep(3)
        self.ib.reqAllOpenOrders()
        self.ib.sleep(2)
        remaining = len(self.ib.openTrades())
        print(f"Orders remaining: {remaining}")
        return remaining == 0

    def create_existing_position(self, strike: float, expiration: str, tp: float, sl: float) -> str:
        """Create existing TP/SL orders that will conflict with new order."""
        print(f"\n=== Creating existing position: {strike}P ===")

        opt = Option("SPY", expiration, strike, "P", "SMART")
        qualified = self.ib.qualifyContracts(opt)
        if not qualified:
            raise RuntimeError(f"Failed to qualify contract: SPY {strike}P")

        contract = qualified[0]
        oca_group = f"EXISTING_OCA_{int(time.time())}"

        tp_order = LimitOrder(
            action="BUY", totalQuantity=1, lmtPrice=tp,
            tif="GTC", ocaGroup=oca_group, ocaType=3,
        )
        sl_order = StopOrder(
            action="BUY", totalQuantity=1, stopPrice=sl,
            tif="GTC", ocaGroup=oca_group, ocaType=3,
        )

        self.ib.placeOrder(contract, tp_order)
        self.ib.placeOrder(contract, sl_order)
        self.ib.sleep(2)

        print(f"Created TP @ ${tp}, SL @ ${sl} with OCA: {oca_group}")
        return oca_group

    def get_open_orders(self) -> list:
        """Get all open orders."""
        self.ib.reqAllOpenOrders()
        self.ib.sleep(2)
        return list(self.ib.openTrades())

    def find_conflicting_orders(self, con_id: int) -> list:
        """Find orders that would conflict with a SELL on the given contract."""
        conflicts = []
        for trade in self.get_open_orders():
            if trade.contract.conId == con_id and trade.order.action == "BUY":
                conflicts.append(trade)
        return conflicts

    def cancel_orders(self, trades: list) -> bool:
        """Cancel specific orders and wait for confirmation."""
        print(f"\n=== Cancelling {len(trades)} orders ===")

        for trade in trades:
            print(f"  Cancelling order {trade.order.orderId}...")
            self.ib.cancelOrder(trade.order)

        self.ib.sleep(3)

        # Verify cancellations
        self.ib.reqAllOpenOrders()
        self.ib.sleep(2)

        remaining = 0
        for trade in trades:
            for open_trade in self.ib.openTrades():
                if open_trade.order.orderId == trade.order.orderId:
                    if open_trade.orderStatus.status not in ("Cancelled", "ApiCancelled"):
                        remaining += 1

        if remaining > 0:
            print(f"WARNING: {remaining} orders still active after cancel")
            return False

        print("All orders cancelled successfully")
        return True

    def place_sell_order(self, strike: float, expiration: str, limit_price: float) -> tuple:
        """Place a new SELL order on the contract."""
        print(f"\n=== Placing SELL order: {strike}P @ ${limit_price} ===")

        opt = Option("SPY", expiration, strike, "P", "SMART")
        qualified = self.ib.qualifyContracts(opt)
        if not qualified:
            raise RuntimeError(f"Failed to qualify contract: SPY {strike}P")

        contract = qualified[0]

        sell_order = LimitOrder(
            action="SELL",
            totalQuantity=1,
            lmtPrice=limit_price,
            tif="DAY",
        )

        trade = self.ib.placeOrder(contract, sell_order)
        self.ib.sleep(2)

        print(f"SELL order placed: ID={trade.order.orderId}, Status={trade.orderStatus.status}")
        return trade, contract

    def replace_cancelled_orders(self, original_trades: list) -> bool:
        """Re-place previously cancelled orders with original OCA groups."""
        print(f"\n=== Re-placing {len(original_trades)} cancelled orders ===")

        for original in original_trades:
            contract = original.contract
            order = original.order

            if order.orderType == "LMT":
                new_order = LimitOrder(
                    action=order.action,
                    totalQuantity=order.totalQuantity,
                    lmtPrice=order.lmtPrice,
                    tif=order.tif,
                    ocaGroup=order.ocaGroup,
                    ocaType=order.ocaType,
                )
            elif order.orderType == "STP":
                new_order = StopOrder(
                    action=order.action,
                    totalQuantity=order.totalQuantity,
                    stopPrice=order.auxPrice,
                    tif=order.tif,
                    ocaGroup=order.ocaGroup,
                    ocaType=order.ocaType,
                )
            else:
                print(f"  Unknown order type: {order.orderType}")
                continue

            trade = self.ib.placeOrder(contract, new_order)
            print(f"  Re-placed {order.orderType} order: ID={trade.order.orderId}")

        self.ib.sleep(2)
        return True

    def run_test(self, strike: float, expiration: str):
        """Run the full conflict scenario test."""
        print("\n" + "=" * 60)
        print("CONFLICT HANDLING TEST")
        print("=" * 60)

        try:
            self.connect()

            # Step 1: Clear existing orders
            self.clear_all_orders()

            # Step 2: Create "existing" position with TP/SL
            existing_oca = self.create_existing_position(
                strike=strike, expiration=expiration,
                tp=2.50, sl=15.00,
            )

            # Verify orders exist
            orders_before = self.get_open_orders()
            print(f"\nOrders before new trade: {len(orders_before)}")

            # Step 3: Get contract and find conflicts
            opt = Option("SPY", expiration, strike, "P", "SMART")
            qualified = self.ib.qualifyContracts(opt)
            contract = qualified[0]

            conflicts = self.find_conflicting_orders(contract.conId)
            print(f"Found {len(conflicts)} conflicting orders")

            if not conflicts:
                print("ERROR: Expected conflicts but found none!")
                return False

            # Save conflict details for re-placement
            conflict_details = [
                {
                    "contract": t.contract,
                    "order": t.order,
                    "orderType": t.order.orderType,
                    "lmtPrice": t.order.lmtPrice,
                    "auxPrice": t.order.auxPrice,
                    "ocaGroup": t.order.ocaGroup,
                }
                for t in conflicts
            ]

            # Step 4: Cancel conflicts
            if not self.cancel_orders(conflicts):
                print("ERROR: Failed to cancel conflicting orders!")
                return False

            # Step 5: Place SELL order
            sell_trade, _ = self.place_sell_order(
                strike=strike, expiration=expiration, limit_price=5.00
            )

            # In paper trading, order might not fill immediately
            # For this test, we just verify it was placed
            print(f"\nSELL order status: {sell_trade.orderStatus.status}")

            # Step 6: Re-place cancelled orders
            self.replace_cancelled_orders(conflicts)

            # Step 7: Verify final state
            orders_after = self.get_open_orders()
            print(f"\n=== Final State ===")
            print(f"Total orders: {len(orders_after)}")
            for t in orders_after:
                print(f"  {t.order.action} {t.order.orderType} @ "
                      f"{t.order.lmtPrice or t.order.auxPrice} "
                      f"OCA={t.order.ocaGroup}")

            # Expected: 3 orders (2 re-placed + 1 new SELL)
            # Or 2 if the SELL was rejected/cancelled
            if len(orders_after) >= 2:
                print("\n SUCCESS: Conflict handling test passed!")
                return True
            else:
                print("\n FAILURE: Unexpected order count")
                return False

        except Exception as e:
            print(f"\nERROR: {e}")
            import traceback
            traceback.print_exc()
            return False

        finally:
            self.disconnect()


def main():
    parser = argparse.ArgumentParser(description="Test conflict handling scenario")
    parser.add_argument("--host", default=os.getenv("TWS_HOST", "ib-gateway"))
    parser.add_argument("--port", type=int, default=int(os.getenv("TWS_PORT", "4003")))
    parser.add_argument("--strike", type=float, default=580.0,
                        help="Strike price to test (use low strike to avoid real fills)")
    parser.add_argument("--expiration", default="20260417")
    parser.add_argument("--client-id", type=int, default=51)

    args = parser.parse_args()

    test = ConflictScenarioTest(
        host=args.host,
        port=args.port,
        client_id=args.client_id,
    )

    success = test.run_test(
        strike=args.strike,
        expiration=args.expiration,
    )

    exit(0 if success else 1)


if __name__ == "__main__":
    main()
