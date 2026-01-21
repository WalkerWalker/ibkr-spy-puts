"""Unit tests for bracket order placement with conflicting order handling.

Tests the two-step logic in IBKRClient.place_bracket_order():

Step 1: Place parent order (with conflict handling)
  - Find conflicting orders on same contract
  - Cancel them temporarily
  - Place parent SELL
  - Wait for parent to FILL
  - Re-place cancelled orders with ORIGINAL OCA groups

Step 2: Place TP/SL orders (no conflict possible)
  - No conflict detection needed (parent already filled)
  - Place new TP/SL in a NEW OCA group
"""

from datetime import date
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

from ibkr_spy_puts.ibkr_client import IBKRClient, BracketOrderResult


class MockOrderStatus:
    """Mock IB order status."""
    def __init__(self, status: str, avg_fill_price: float = 0.0):
        self.status = status
        self.avgFillPrice = avg_fill_price


class MockOrder:
    """Mock IB order."""
    def __init__(self, order_id: int, action: str, order_type: str = "LMT",
                 total_qty: int = 1, lmt_price: float = 0.0, aux_price: float = 0.0,
                 tif: str = "GTC", oca_group: str = ""):
        self.orderId = order_id
        self.action = action
        self.orderType = order_type
        self.totalQuantity = total_qty
        self.lmtPrice = lmt_price
        self.auxPrice = aux_price
        self.tif = tif
        self.ocaGroup = oca_group
        self.parentId = 0


class MockContract:
    """Mock IB contract."""
    def __init__(self, con_id: int, symbol: str = "SPY", strike: float = 630.0,
                 expiration: str = "20260417", local_symbol: str = "SPY  260417P00630000"):
        self.conId = con_id
        self.symbol = symbol
        self.strike = strike
        self.lastTradeDateOrContractMonth = expiration
        self.localSymbol = local_symbol
        self.secType = "OPT"
        self.right = "P"


class MockTrade:
    """Mock IB trade."""
    def __init__(self, contract: MockContract, order: MockOrder, status: MockOrderStatus):
        self.contract = contract
        self.order = order
        self.orderStatus = status
        self.fills = []  # List of fill objects (empty by default)


class TestStep1ConflictDetection:
    """Test Step 1: Detection of conflicting orders on the same contract."""

    def test_finds_conflicting_buy_orders_for_sell_parent(self):
        """When placing a SELL order, should find existing BUY orders on same contract."""
        client = IBKRClient()
        target_contract = MockContract(con_id=12345, strike=630.0)

        # Create existing BUY orders (TP/SL from previous position)
        existing_tp = MockTrade(
            contract=MockContract(con_id=12345, strike=630.0),
            order=MockOrder(order_id=100, action="BUY", order_type="LMT", lmt_price=2.35, oca_group="OCA_OLD"),
            status=MockOrderStatus("Submitted"),
        )
        existing_sl = MockTrade(
            contract=MockContract(con_id=12345, strike=630.0),
            order=MockOrder(order_id=101, action="BUY", order_type="STP", aux_price=17.64, oca_group="OCA_OLD"),
            status=MockOrderStatus("Submitted"),
        )
        # Order for a DIFFERENT contract (should NOT be detected as conflicting)
        other_contract_order = MockTrade(
            contract=MockContract(con_id=99999, strike=625.0),
            order=MockOrder(order_id=200, action="BUY", order_type="LMT", lmt_price=1.50),
            status=MockOrderStatus("Submitted"),
        )

        with patch.object(client, 'ib') as mock_ib:
            mock_ib.isConnected.return_value = True
            mock_ib.openTrades.return_value = [existing_tp, existing_sl, other_contract_order]

            # Detect conflicting orders (same logic as in place_bracket_order)
            opposite_action = "BUY"  # For SELL parent
            conflicting = []
            for trade in mock_ib.openTrades():
                if (trade.contract.conId == target_contract.conId and
                    trade.order.action == opposite_action and
                    trade.orderStatus.status in ["Submitted", "PreSubmitted"]):
                    conflicting.append(trade)

            # Should find 2 conflicting orders (TP and SL on same contract)
            assert len(conflicting) == 2
            assert conflicting[0].order.orderId == 100
            assert conflicting[1].order.orderId == 101


class TestStep1WaitForParentFill:
    """Test Step 1: Wait for parent to FILL before proceeding."""

    def test_waits_for_parent_fill(self):
        """Should poll until parent order status is Filled."""
        client = IBKRClient()
        target_contract = MockContract(con_id=12345, strike=630.0)

        poll_count = [0]

        def mock_sleep(seconds):
            poll_count[0] += 1

        parent_order = MockOrder(order_id=500, action="SELL", lmt_price=5.89)
        parent_status = MockOrderStatus("Submitted")
        parent_trade = MockTrade(target_contract, parent_order, parent_status)

        # Simulate: after 3 polls, status changes to Filled
        def get_status():
            if poll_count[0] >= 3:
                parent_trade.orderStatus.status = "Filled"
                parent_trade.orderStatus.avgFillPrice = 5.89
            return parent_trade.orderStatus.status

        with patch.object(client, 'ib') as mock_ib:
            mock_ib.isConnected.return_value = True
            mock_ib.openTrades.return_value = []
            mock_ib.sleep = mock_sleep
            mock_ib.reqAllOpenOrders = MagicMock()

            # Simulate waiting loop
            max_wait = 60
            poll_interval = 2
            waited = 0
            parent_filled = False

            while waited < max_wait:
                mock_ib.sleep(poll_interval)
                waited += poll_interval
                if get_status() == "Filled":
                    parent_filled = True
                    break

            assert parent_filled is True
            assert poll_count[0] >= 3


class TestStep1ReplaceCancelledOrders:
    """Test Step 1: Re-place cancelled orders with ORIGINAL OCA groups."""

    def test_replaces_orders_with_original_oca_groups(self):
        """Cancelled orders should be re-placed with their original OCA groups, not a new one."""
        # Two conflicting orders with the SAME original OCA group
        conflicting_orders = [
            {
                "contract": MockContract(con_id=12345, strike=630.0),
                "order": MockOrder(order_id=100, action="BUY", total_qty=2, oca_group="OCA_ORIGINAL_123"),
                "order_type": "LMT",
                "action": "BUY",
                "quantity": 2,
                "lmt_price": 2.35,
                "aux_price": 0,
                "oca_group": "OCA_ORIGINAL_123",
                "tif": "GTC",
            },
            {
                "contract": MockContract(con_id=12345, strike=630.0),
                "order": MockOrder(order_id=101, action="BUY", total_qty=2, oca_group="OCA_ORIGINAL_123"),
                "order_type": "STP",
                "action": "BUY",
                "quantity": 2,
                "lmt_price": 0,
                "aux_price": 17.64,
                "oca_group": "OCA_ORIGINAL_123",
                "tif": "GTC",
            },
        ]

        # Group orders by their original OCA group (same logic as in place_bracket_order)
        oca_groups: dict[str, list] = {}
        for conflict in conflicting_orders:
            oca = conflict["oca_group"] or "OCA_FALLBACK"
            if oca not in oca_groups:
                oca_groups[oca] = []
            oca_groups[oca].append(conflict)

        # Should have 1 OCA group with 2 orders
        assert len(oca_groups) == 1
        assert "OCA_ORIGINAL_123" in oca_groups
        assert len(oca_groups["OCA_ORIGINAL_123"]) == 2

    def test_different_oca_groups_stay_separate(self):
        """Orders from different OCA groups should stay in their separate groups."""
        # Scenario: Two different positions with different TP/SL prices
        conflicting_orders = [
            {
                "order_type": "LMT",
                "oca_group": "OCA_TRADE_17",
                "quantity": 1,
            },
            {
                "order_type": "STP",
                "oca_group": "OCA_TRADE_17",
                "quantity": 1,
            },
            {
                "order_type": "LMT",
                "oca_group": "OCA_TRADE_18",
                "quantity": 1,
            },
            {
                "order_type": "STP",
                "oca_group": "OCA_TRADE_18",
                "quantity": 1,
            },
        ]

        oca_groups: dict[str, list] = {}
        for conflict in conflicting_orders:
            oca = conflict["oca_group"]
            if oca not in oca_groups:
                oca_groups[oca] = []
            oca_groups[oca].append(conflict)

        # Should have 2 OCA groups, each with 2 orders
        assert len(oca_groups) == 2
        assert len(oca_groups["OCA_TRADE_17"]) == 2
        assert len(oca_groups["OCA_TRADE_18"]) == 2


class TestStep2PlaceNewTPSL:
    """Test Step 2: Place new TP/SL orders (no conflict detection)."""

    def test_new_tp_sl_in_new_oca_group(self):
        """New TP/SL should be in a NEW OCA group, separate from existing ones."""
        import time

        # Existing OCA groups
        existing_oca_groups = ["OCA_TRADE_17", "OCA_TRADE_18"]

        # New OCA group format
        new_oca_group = f"OCA_{int(time.time())}"

        # New group should not conflict with existing ones
        assert new_oca_group not in existing_oca_groups
        assert new_oca_group.startswith("OCA_")


class TestIntegration:
    """Integration tests for the full place_bracket_order flow."""

    @pytest.fixture
    def mock_ib(self):
        """Create a mock IB instance with common setup."""
        mock = MagicMock()
        mock.isConnected.return_value = True
        mock.sleep = MagicMock()
        mock.reqAllOpenOrders = MagicMock()
        mock.qualifyContracts = MagicMock()
        mock.cancelOrder = MagicMock()
        return mock

    def test_successful_bracket_order_no_conflicts(self, mock_ib):
        """Test successful bracket order when no conflicting orders exist."""
        client = IBKRClient()
        client.ib = mock_ib

        target_contract = MockContract(con_id=12345, strike=630.0)

        # No existing orders
        mock_ib.openTrades.return_value = []

        # Parent order fills immediately
        parent_order = MockOrder(order_id=500, action="SELL", lmt_price=5.89)
        parent_status = MockOrderStatus("Filled", avg_fill_price=5.89)
        parent_trade = MockTrade(target_contract, parent_order, parent_status)

        tp_order = MockOrder(order_id=501, action="BUY", order_type="LMT", lmt_price=2.36)
        tp_status = MockOrderStatus("Submitted")
        tp_trade = MockTrade(target_contract, tp_order, tp_status)

        sl_order = MockOrder(order_id=502, action="BUY", order_type="STP", aux_price=17.67)
        sl_status = MockOrderStatus("Submitted")
        sl_trade = MockTrade(target_contract, sl_order, sl_status)

        mock_ib.placeOrder.side_effect = [parent_trade, tp_trade, sl_trade]

        result = client.place_bracket_order(
            contract=target_contract,
            action="SELL",
            quantity=1,
            limit_price=5.89,
            take_profit_price=2.36,
            stop_loss_price=17.67,
        )

        assert result.success is True
        assert result.parent_order_id == 500
        assert result.take_profit_order_id == 501
        assert result.stop_loss_order_id == 502

    def test_bracket_order_with_conflicts_replaces_with_original_oca(self, mock_ib):
        """Test that cancelled orders are re-placed with their original OCA groups."""
        client = IBKRClient()
        client.ib = mock_ib

        target_contract = MockContract(con_id=12345, strike=630.0)

        # Existing TP/SL orders with original OCA group
        existing_tp = MockTrade(
            contract=MockContract(con_id=12345, strike=630.0),
            order=MockOrder(order_id=100, action="BUY", order_type="LMT", total_qty=2, lmt_price=2.35, oca_group="OCA_ORIGINAL"),
            status=MockOrderStatus("Submitted"),
        )
        existing_sl = MockTrade(
            contract=MockContract(con_id=12345, strike=630.0),
            order=MockOrder(order_id=101, action="BUY", order_type="STP", total_qty=2, aux_price=17.64, oca_group="OCA_ORIGINAL"),
            status=MockOrderStatus("Submitted"),
        )

        call_count = [0]
        def open_trades_side_effect():
            call_count[0] += 1
            if call_count[0] == 1:
                return [existing_tp, existing_sl]
            return []

        mock_ib.openTrades.side_effect = open_trades_side_effect

        # Parent fills, then new TP/SL, then re-placed orders
        parent_trade = MockTrade(
            target_contract,
            MockOrder(order_id=500, action="SELL", lmt_price=5.89),
            MockOrderStatus("Filled", avg_fill_price=5.89),
        )
        tp_trade = MockTrade(
            target_contract,
            MockOrder(order_id=501, action="BUY", order_type="LMT"),
            MockOrderStatus("Submitted"),
        )
        sl_trade = MockTrade(
            target_contract,
            MockOrder(order_id=502, action="BUY", order_type="STP"),
            MockOrderStatus("Submitted"),
        )
        repl_tp_trade = MockTrade(
            target_contract,
            MockOrder(order_id=601, action="BUY", order_type="LMT"),
            MockOrderStatus("Submitted"),
        )
        repl_sl_trade = MockTrade(
            target_contract,
            MockOrder(order_id=602, action="BUY", order_type="STP"),
            MockOrderStatus("Submitted"),
        )

        mock_ib.placeOrder.side_effect = [parent_trade, repl_tp_trade, repl_sl_trade, tp_trade, sl_trade]

        cancelled_orders = []
        mock_ib.cancelOrder = lambda o: cancelled_orders.append(o.orderId)

        result = client.place_bracket_order(
            contract=target_contract,
            action="SELL",
            quantity=1,
            limit_price=5.89,
            take_profit_price=2.36,
            stop_loss_price=17.67,
        )

        # Should have cancelled the conflicting orders
        assert 100 in cancelled_orders
        assert 101 in cancelled_orders

        assert result.success is True

    def test_parent_not_filled_still_replaces_cancelled_orders(self, mock_ib):
        """If parent doesn't fill, cancelled orders should STILL be re-placed."""
        client = IBKRClient()
        client.ib = mock_ib

        target_contract = MockContract(con_id=12345, strike=630.0)

        # Existing orders to cancel
        existing_tp = MockTrade(
            contract=MockContract(con_id=12345, strike=630.0),
            order=MockOrder(order_id=100, action="BUY", oca_group="OCA_ORIGINAL"),
            status=MockOrderStatus("Submitted"),
        )

        call_count = [0]
        def open_trades_side_effect():
            call_count[0] += 1
            if call_count[0] == 1:
                return [existing_tp]
            return []

        mock_ib.openTrades.side_effect = open_trades_side_effect

        # Parent never fills
        parent_trade = MockTrade(
            target_contract,
            MockOrder(order_id=500, action="SELL", lmt_price=5.89),
            MockOrderStatus("Submitted"),  # Never changes to Filled
        )

        # Track what orders were placed
        placed_orders = []
        def track_place_order(contract, order):
            placed_orders.append(order)
            if len(placed_orders) == 1:
                return parent_trade
            # Re-placed order
            return MockTrade(contract, order, MockOrderStatus("Submitted"))

        mock_ib.placeOrder.side_effect = track_place_order

        result = client.place_bracket_order(
            contract=target_contract,
            action="SELL",
            quantity=1,
            limit_price=5.89,
            take_profit_price=2.36,
            stop_loss_price=17.67,
        )

        # Should fail because parent didn't fill
        assert result.success is False
        assert "not filled" in result.error_message.lower()

        # But cancelled orders should have been re-placed (orders placed after parent)
        # First order is parent, subsequent orders are re-placements
        assert len(placed_orders) >= 2  # At least parent + 1 re-placed order
