"""Tests for database operations."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from ibkr_spy_puts.config import DatabaseSettings
from ibkr_spy_puts.database import Database, Order, PositionSnapshot, Trade


@pytest.fixture
def db_settings():
    """Database settings for testing."""
    return DatabaseSettings(
        host="localhost",
        port=5432,
        name="ibkr_puts",
        user="ibkr",
        password="ibkr_dev_password",
    )


@pytest.fixture
def db(db_settings):
    """Database connection for testing."""
    database = Database(settings=db_settings)
    if not database.connect():
        pytest.skip("Database not available")
    yield database
    database.disconnect()


class TestDatabaseConnection:
    """Test database connection."""

    def test_connect(self, db_settings):
        """Test database connection."""
        database = Database(settings=db_settings)
        connected = database.connect()
        if connected:
            assert database.is_connected
            database.disconnect()
            assert not database.is_connected
        else:
            pytest.skip("Database not available")

    def test_connection_string(self, db_settings):
        """Test connection string generation."""
        expected = "postgresql://ibkr:ibkr_dev_password@localhost:5432/ibkr_puts"
        assert db_settings.connection_string == expected


class TestTradeOperations:
    """Test trade CRUD operations."""

    def test_insert_and_get_trade(self, db):
        """Test inserting and retrieving a trade."""
        trade = Trade(
            trade_date=date.today(),
            symbol="SPY",
            strike=Decimal("630.00"),
            expiration=date(2026, 4, 17),
            quantity=1,
            entry_price=Decimal("5.59"),
            entry_time=datetime.now(),
            expected_tp_price=Decimal("2.24"),
            expected_sl_price=Decimal("16.77"),
            status="OPEN",
            strategy_id="spy-put-selling",
        )

        trade_id = db.insert_trade(trade)
        assert trade_id > 0

        retrieved = db.get_trade(trade_id)
        assert retrieved is not None
        assert retrieved.symbol == "SPY"
        assert retrieved.strike == Decimal("630.00")
        assert retrieved.status == "OPEN"

    def test_get_open_trades(self, db):
        """Test getting open trades."""
        # Insert a test trade
        trade = Trade(
            trade_date=date.today(),
            symbol="SPY",
            strike=Decimal("625.00"),
            expiration=date(2026, 4, 17),
            quantity=1,
            entry_price=Decimal("5.00"),
            entry_time=datetime.now(),
            expected_tp_price=Decimal("2.00"),
            expected_sl_price=Decimal("15.00"),
            status="OPEN",
        )
        db.insert_trade(trade)

        open_trades = db.get_open_trades()
        assert len(open_trades) > 0
        assert all(t.status == "OPEN" for t in open_trades)

    def test_update_trade_exit(self, db):
        """Test closing a trade."""
        # Insert a trade
        trade = Trade(
            trade_date=date.today(),
            symbol="SPY",
            strike=Decimal("620.00"),
            expiration=date(2026, 4, 17),
            quantity=1,
            entry_price=Decimal("4.50"),
            entry_time=datetime.now(),
            expected_tp_price=Decimal("1.80"),
            expected_sl_price=Decimal("13.50"),
            status="OPEN",
        )
        trade_id = db.insert_trade(trade)

        # Close the trade
        exit_time = datetime.now()
        db.update_trade_exit(
            trade_id=trade_id,
            exit_price=Decimal("1.80"),
            exit_time=exit_time,
            exit_reason="TAKE_PROFIT",
        )

        # Verify it's closed
        closed_trade = db.get_trade(trade_id)
        assert closed_trade.status == "CLOSED"
        assert closed_trade.exit_reason == "TAKE_PROFIT"
        # P&L should be calculated by trigger: (4.50 - 1.80) * 1 * 100 = $270
        assert closed_trade.realized_pnl == Decimal("270.00")


class TestOrderOperations:
    """Test order CRUD operations."""

    def test_insert_and_get_orders(self, db):
        """Test inserting and retrieving orders."""
        # First create a trade
        trade = Trade(
            trade_date=date.today(),
            symbol="SPY",
            strike=Decimal("615.00"),
            expiration=date(2026, 4, 17),
            quantity=1,
            entry_price=Decimal("4.00"),
            entry_time=datetime.now(),
            expected_tp_price=Decimal("1.60"),
            expected_sl_price=Decimal("12.00"),
        )
        trade_id = db.insert_trade(trade)

        # Insert parent order
        parent = Order(
            trade_id=trade_id,
            ibkr_order_id=12345,
            ibkr_perm_id=98765,
            order_type="PARENT",
            action="SELL",
            limit_price=Decimal("4.00"),
            quantity=1,
            status="FILLED",
            algo_strategy="Adaptive",
            algo_priority="Normal",
        )
        parent_id = db.insert_order(parent)
        assert parent_id > 0

        # Insert take profit order
        tp = Order(
            trade_id=trade_id,
            ibkr_order_id=12346,
            order_type="TAKE_PROFIT",
            action="BUY",
            limit_price=Decimal("1.60"),
            quantity=1,
            status="SUBMITTED",
        )
        tp_id = db.insert_order(tp)
        assert tp_id > 0

        # Insert stop loss order
        sl = Order(
            trade_id=trade_id,
            ibkr_order_id=12347,
            order_type="STOP_LOSS",
            action="BUY",
            order_class="STP",
            stop_price=Decimal("12.00"),
            quantity=1,
            status="SUBMITTED",
        )
        sl_id = db.insert_order(sl)
        assert sl_id > 0

        # Get all orders for trade
        orders = db.get_orders_for_trade(trade_id)
        assert len(orders) == 3
        assert orders[0].order_type == "PARENT"


class TestViewQueries:
    """Test view-based queries for frontend."""

    def test_get_strategy_summary(self, db):
        """Test getting strategy summary."""
        summary = db.get_strategy_summary()
        assert "open_positions" in summary
        assert "closed_trades" in summary
        assert "total_realized_pnl" in summary

    def test_get_risk_metrics(self, db):
        """Test getting risk metrics."""
        metrics = db.get_risk_metrics()
        assert "open_position_count" in metrics
        assert "max_loss" in metrics
        assert "total_delta" in metrics

    def test_get_open_positions(self, db):
        """Test getting open positions view."""
        positions = db.get_open_positions()
        assert isinstance(positions, list)
