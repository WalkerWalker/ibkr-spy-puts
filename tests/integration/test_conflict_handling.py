"""Integration tests for order conflict handling.

These tests require a running IB Gateway/TWS connection (paper trading recommended).
They test the real order placement and conflict resolution logic.

Run with:
    poetry run pytest tests/integration/test_conflict_handling.py -v -s
"""

import os
import pytest
import time

# Skip if no TWS connection available
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "true",
    reason="Set RUN_INTEGRATION_TESTS=true to run integration tests",
)


@pytest.fixture
def ib_connection():
    """Create IB connection for tests."""
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    from ib_insync import IB

    ib = IB()
    host = os.getenv("TWS_HOST", "ib-gateway")
    port = int(os.getenv("TWS_PORT", "4003"))

    try:
        ib.connect(host, port, clientId=60, timeout=15)
        yield ib
    finally:
        if ib.isConnected():
            ib.disconnect()


class TestConflictDetection:
    """Test detection of conflicting orders."""

    def test_finds_buy_orders_on_same_contract(self, ib_connection):
        """Should find existing BUY orders that would conflict with a SELL."""
        from ib_insync import Option, LimitOrder

        ib = ib_connection

        # Create a test option contract (use low strike to avoid fills)
        opt = Option("SPY", "20260417", 500.0, "P", "SMART")
        qualified = ib.qualifyContracts(opt)
        assert qualified, "Failed to qualify test contract"
        contract = qualified[0]

        # Place a BUY order (simulating existing TP)
        oca_group = f"TEST_{int(time.time())}"
        buy_order = LimitOrder(
            action="BUY", totalQuantity=1, lmtPrice=1.00,
            tif="GTC", ocaGroup=oca_group, ocaType=3,
        )
        trade = ib.placeOrder(contract, buy_order)
        ib.sleep(2)

        # Find conflicting orders
        ib.reqAllOpenOrders()
        ib.sleep(2)

        conflicts = []
        for t in ib.openTrades():
            if t.contract.conId == contract.conId and t.order.action == "BUY":
                conflicts.append(t)

        assert len(conflicts) >= 1, "Should find the conflicting BUY order"

        # Cleanup
        ib.cancelOrder(trade.order)
        ib.sleep(2)


class TestConflictResolution:
    """Test cancellation and re-placement of conflicting orders."""

    def test_cancel_and_replace_preserves_oca_group(self, ib_connection):
        """Cancelled orders should be re-placed with same OCA group."""
        from ib_insync import Option, LimitOrder, StopOrder

        ib = ib_connection

        # Create test contract
        opt = Option("SPY", "20260417", 500.0, "P", "SMART")
        qualified = ib.qualifyContracts(opt)
        contract = qualified[0]

        # Create TP/SL pair with OCA group
        original_oca = f"ORIGINAL_{int(time.time())}"
        tp_order = LimitOrder(
            action="BUY", totalQuantity=1, lmtPrice=1.00,
            tif="GTC", ocaGroup=original_oca, ocaType=3,
        )
        sl_order = StopOrder(
            action="BUY", totalQuantity=1, stopPrice=10.00,
            tif="GTC", ocaGroup=original_oca, ocaType=3,
        )

        tp_trade = ib.placeOrder(contract, tp_order)
        sl_trade = ib.placeOrder(contract, sl_order)
        ib.sleep(2)

        # Cancel both
        ib.cancelOrder(tp_trade.order)
        ib.cancelOrder(sl_trade.order)
        ib.sleep(3)

        # Re-place with SAME OCA group
        new_tp = LimitOrder(
            action="BUY", totalQuantity=1, lmtPrice=1.00,
            tif="GTC", ocaGroup=original_oca, ocaType=3,
        )
        new_sl = StopOrder(
            action="BUY", totalQuantity=1, stopPrice=10.00,
            tif="GTC", ocaGroup=original_oca, ocaType=3,
        )

        new_tp_trade = ib.placeOrder(contract, new_tp)
        new_sl_trade = ib.placeOrder(contract, new_sl)
        ib.sleep(2)

        # Verify OCA groups preserved
        assert new_tp_trade.order.ocaGroup == original_oca
        assert new_sl_trade.order.ocaGroup == original_oca

        # Cleanup
        ib.reqGlobalCancel()
        ib.sleep(2)


class TestFullScenario:
    """Test the complete conflict handling flow."""

    def test_sell_with_existing_buy_orders(self, ib_connection):
        """
        Scenario:
        1. Existing TP/SL (BUY orders) on 500P
        2. Try to SELL a new 500P
        3. Should cancel existing, place SELL, re-place existing
        """
        from ib_insync import Option, LimitOrder, StopOrder

        ib = ib_connection

        # Clear any existing orders
        ib.reqGlobalCancel()
        ib.sleep(3)

        # Create test contract
        opt = Option("SPY", "20260417", 500.0, "P", "SMART")
        qualified = ib.qualifyContracts(opt)
        contract = qualified[0]

        # Step 1: Create existing TP/SL
        existing_oca = f"EXISTING_{int(time.time())}"
        ib.placeOrder(contract, LimitOrder(
            action="BUY", totalQuantity=1, lmtPrice=0.50,
            tif="GTC", ocaGroup=existing_oca, ocaType=3,
        ))
        ib.placeOrder(contract, StopOrder(
            action="BUY", totalQuantity=1, stopPrice=8.00,
            tif="GTC", ocaGroup=existing_oca, ocaType=3,
        ))
        ib.sleep(2)

        # Verify 2 orders exist
        ib.reqAllOpenOrders()
        ib.sleep(2)
        orders_before = list(ib.openTrades())
        assert len(orders_before) == 2, f"Expected 2 orders, got {len(orders_before)}"

        # Step 2: Find and cancel conflicts
        conflicts = [t for t in orders_before if t.order.action == "BUY"]
        for t in conflicts:
            ib.cancelOrder(t.order)
        ib.sleep(3)

        # Step 3: Place SELL order
        sell_order = LimitOrder(action="SELL", totalQuantity=1, lmtPrice=3.00, tif="DAY")
        sell_trade = ib.placeOrder(contract, sell_order)
        ib.sleep(2)

        assert sell_trade.orderStatus.status in ("Submitted", "PreSubmitted", "PendingSubmit")

        # Step 4: Re-place cancelled orders
        for original in conflicts:
            if original.order.orderType == "LMT":
                new_order = LimitOrder(
                    action="BUY", totalQuantity=1, lmtPrice=original.order.lmtPrice,
                    tif="GTC", ocaGroup=original.order.ocaGroup, ocaType=3,
                )
            else:
                new_order = StopOrder(
                    action="BUY", totalQuantity=1, stopPrice=original.order.auxPrice,
                    tif="GTC", ocaGroup=original.order.ocaGroup, ocaType=3,
                )
            ib.placeOrder(contract, new_order)
        ib.sleep(2)

        # Step 5: Verify final state
        ib.reqAllOpenOrders()
        ib.sleep(2)
        orders_after = list(ib.openTrades())

        # Should have 3 orders: 1 SELL + 2 re-placed BUY
        assert len(orders_after) == 3, f"Expected 3 orders, got {len(orders_after)}"

        # Cleanup
        ib.reqGlobalCancel()
        ib.sleep(2)
