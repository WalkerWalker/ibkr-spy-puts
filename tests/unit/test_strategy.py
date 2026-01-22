"""Unit tests for put selling strategy using mock data.

These tests run without TWS connection using fixture data.
"""

from datetime import date
from pathlib import Path

import pytest

from ibkr_spy_puts.config import ExitOrderSettings, StrategySettings
from ibkr_spy_puts.mock_client import MockIBKRClient
from ibkr_spy_puts.strategy import (
    ExitPrices,
    PutSellingStrategy,
    TradeOrder,
)


# Fixtures directory
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


class TestExitPrices:
    """Test exit price calculations."""

    def test_calculate_standard_exit_prices(self):
        """Test standard 60% profit / 200% loss exit prices."""
        prices = ExitPrices.calculate(
            sell_price=1.00,
            take_profit_pct=60.0,
            stop_loss_pct=200.0,
        )

        assert prices.sell_price == 1.00
        assert prices.take_profit_price == pytest.approx(0.40)
        assert prices.stop_loss_price == pytest.approx(3.00)

    def test_calculate_with_different_percentages(self):
        """Test with different profit/loss percentages."""
        prices = ExitPrices.calculate(
            sell_price=2.00,
            take_profit_pct=50.0,  # 50% profit
            stop_loss_pct=100.0,  # 100% loss
        )

        assert prices.sell_price == 2.00
        assert prices.take_profit_price == pytest.approx(1.00)  # 50% profit = buy at 50%
        assert prices.stop_loss_price == pytest.approx(4.00)  # 100% loss = buy at 200%

    def test_calculate_small_premium(self):
        """Test with small premium amounts."""
        prices = ExitPrices.calculate(
            sell_price=0.50,
            take_profit_pct=60.0,
            stop_loss_pct=200.0,
        )

        assert prices.take_profit_price == pytest.approx(0.20)
        assert prices.stop_loss_price == pytest.approx(1.50)


class TestPutSellingStrategy:
    """Test the put selling strategy."""

    def test_strategy_initialization(self):
        """Test strategy can be initialized."""
        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            strategy = PutSellingStrategy(client)

            assert strategy.client == client
            assert strategy.strategy.symbol == "SPY"
            assert strategy.strategy.target_dte == 90
            assert strategy.strategy.target_delta == -0.15
            assert strategy.exit_orders.enabled is True

    def test_strategy_with_custom_settings(self):
        """Test strategy with custom settings."""
        custom_strategy = StrategySettings(
            symbol="SPY",
            quantity=2,
            target_dte=45,
            target_delta=-0.20,
        )
        custom_exit = ExitOrderSettings(
            take_profit_pct=50.0,
            stop_loss_pct=150.0,
        )

        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            strategy = PutSellingStrategy(
                client,
                strategy_settings=custom_strategy,
                exit_settings=custom_exit,
            )

            assert strategy.strategy.quantity == 2
            assert strategy.strategy.target_dte == 45
            assert strategy.strategy.target_delta == -0.20
            assert strategy.exit_orders.take_profit_pct == 50.0

    def test_select_option(self):
        """Test option selection."""
        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            strategy = PutSellingStrategy(client)

            option = strategy.select_option()

            assert option is not None
            assert option.symbol == "SPY"
            assert option.right == "P"
            assert option.delta is not None
            assert -0.30 <= option.delta <= -0.05

    def test_calculate_limit_price(self):
        """Test limit price calculation."""
        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            strategy = PutSellingStrategy(client)
            option = strategy.select_option()
            assert option is not None

            limit_price = strategy.calculate_limit_price(option)

            # Limit price should be mid price (no offset, fill speed controlled by Adaptive algo)
            assert limit_price == pytest.approx(option.mid, rel=0.01)

    def test_calculate_exit_prices(self):
        """Test exit price calculation."""
        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            strategy = PutSellingStrategy(client)

            exit_prices = strategy.calculate_exit_prices(1.00)

            assert exit_prices.sell_price == 1.00
            assert exit_prices.take_profit_price == pytest.approx(0.40)
            assert exit_prices.stop_loss_price == pytest.approx(3.00)

    def test_create_trade_order(self):
        """Test creating a trade order."""
        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            strategy = PutSellingStrategy(client)

            order = strategy.create_trade_order()

            assert order is not None
            assert order.action == "SELL"
            assert order.quantity == 1
            assert order.order_type == "LMT"
            assert order.limit_price is not None
            assert order.limit_price > 0
            assert order.exit_prices is not None

    def test_create_trade_order_with_exit_prices(self):
        """Test trade order includes exit prices."""
        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            strategy = PutSellingStrategy(client)

            order = strategy.create_trade_order()

            assert order is not None
            assert order.exit_prices is not None
            assert order.exit_prices.take_profit_price < order.exit_prices.sell_price
            assert order.exit_prices.stop_loss_price > order.exit_prices.sell_price

    def test_create_trade_order_exit_disabled(self):
        """Test trade order without exit orders."""
        custom_exit = ExitOrderSettings(enabled=False)

        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            strategy = PutSellingStrategy(
                client,
                exit_settings=custom_exit,
            )

            order = strategy.create_trade_order()

            assert order is not None
            assert order.exit_prices is None


class TestStrategyExecution:
    """Test strategy execution."""

    def test_run_dry_run(self):
        """Test running strategy in dry run mode."""
        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            strategy = PutSellingStrategy(client)

            order, result = strategy.run(dry_run=True)

            assert order is not None
            assert result.success is True
            assert "DRY RUN" in result.message

    def test_run_with_mock_client(self):
        """Test running strategy with mock client (simulated order)."""
        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            strategy = PutSellingStrategy(client)

            order, result = strategy.run(dry_run=False)

            assert order is not None
            assert result.success is True
            assert result.sell_order_id is not None
            assert result.take_profit_order_id is not None
            assert result.stop_loss_order_id is not None

    def test_run_not_connected(self):
        """Test running strategy when not connected."""
        client = MockIBKRClient(fixtures_dir=FIXTURES_DIR)
        # Don't connect
        strategy = PutSellingStrategy(client)

        order, result = strategy.run()

        assert order is None
        assert result.success is False
        assert "not connected" in result.message.lower()

    def test_describe_trade(self):
        """Test trade description generation."""
        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            strategy = PutSellingStrategy(client)
            order = strategy.create_trade_order()
            assert order is not None

            description = strategy.describe_trade(order)

            assert "SELL" in description
            assert "SPY" in description
            assert "Strike" in description
            assert "Take Profit" in description
            assert "Stop Loss" in description


class TestStrategyWithDifferentSettings:
    """Test strategy with various configurations."""

    def test_different_quantity(self):
        """Test strategy with different quantity."""
        custom = StrategySettings(quantity=5)

        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            strategy = PutSellingStrategy(client, strategy_settings=custom)
            order = strategy.create_trade_order()

            assert order is not None
            assert order.quantity == 5

    def test_different_delta_target(self):
        """Test strategy with different delta target."""
        custom = StrategySettings(target_delta=-0.10)

        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            strategy = PutSellingStrategy(client, strategy_settings=custom)
            option = strategy.select_option()

            assert option is not None
            # Should find option close to -0.10 delta
            if option.delta:
                assert abs(option.delta - (-0.10)) < abs(option.delta - (-0.20))

    def test_market_order_type(self):
        """Test strategy with market order type."""
        custom = StrategySettings(order_type="MKT")

        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            strategy = PutSellingStrategy(client, strategy_settings=custom)
            order = strategy.create_trade_order()

            assert order is not None
            assert order.order_type == "MKT"
            assert order.limit_price is None

    def test_aggressive_exit_settings(self):
        """Test strategy with aggressive exit settings."""
        custom_exit = ExitOrderSettings(
            take_profit_pct=80.0,  # Take 80% profit
            stop_loss_pct=100.0,  # Stop at 100% loss
        )

        with MockIBKRClient(fixtures_dir=FIXTURES_DIR) as client:
            strategy = PutSellingStrategy(client, exit_settings=custom_exit)
            order = strategy.create_trade_order()

            assert order is not None
            assert order.exit_prices is not None

            # With 80% profit, buy back at 20% of sell price
            expected_tp = order.exit_prices.sell_price * 0.20
            assert order.exit_prices.take_profit_price == pytest.approx(expected_tp, rel=0.01)

            # With 100% loss, buy back at 200% of sell price
            expected_sl = order.exit_prices.sell_price * 2.00
            assert order.exit_prices.stop_loss_price == pytest.approx(expected_sl, rel=0.01)
