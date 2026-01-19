"""Tests for database operations."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from ibkr_spy_puts.config import DatabaseSettings
from ibkr_spy_puts.database import Database, Position, Trade


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
    """Test trade log operations."""

    def test_insert_trade(self, db):
        """Test inserting a trade log entry."""
        trade = Trade(
            trade_date=date.today(),
            symbol="SPY",
            strike=Decimal("630.00"),
            expiration=date(2026, 4, 17),
            quantity=1,
            action="SELL",
            price=Decimal("5.59"),
            fill_time=datetime.now(),
            strategy_id="spy-put-selling",
        )

        trade_id = db.insert_trade(trade)
        assert trade_id > 0

    def test_get_trade_history(self, db):
        """Test getting trade history."""
        # Insert a test trade
        trade = Trade(
            trade_date=date.today(),
            symbol="SPY",
            strike=Decimal("625.00"),
            expiration=date(2026, 4, 17),
            quantity=1,
            action="SELL",
            price=Decimal("5.00"),
            fill_time=datetime.now(),
        )
        db.insert_trade(trade)

        history = db.get_trade_history()
        assert len(history) > 0
        assert history[0]["action"] in ("SELL", "BUY")


class TestPositionOperations:
    """Test position (book) operations."""

    def test_insert_and_get_position(self, db):
        """Test inserting and retrieving a position."""
        position = Position(
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

        position_id = db.insert_position(position)
        assert position_id > 0

        retrieved = db.get_position(position_id)
        assert retrieved is not None
        assert retrieved.symbol == "SPY"
        assert retrieved.strike == Decimal("630.00")
        assert retrieved.status == "OPEN"

    def test_get_open_positions(self, db):
        """Test getting open positions."""
        # Insert a test position
        position = Position(
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
        db.insert_position(position)

        open_positions = db.get_open_positions()
        assert len(open_positions) > 0
        assert all(p.status == "OPEN" for p in open_positions)

    def test_close_position(self, db):
        """Test closing a position."""
        # Insert a position
        position = Position(
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
        position_id = db.insert_position(position)

        # Close the position
        exit_time = datetime.now()
        db.close_position(
            position_id=position_id,
            exit_price=Decimal("1.80"),
            exit_time=exit_time,
        )

        # Verify it's closed
        closed_position = db.get_position(position_id)
        assert closed_position.status == "CLOSED"
        assert closed_position.exit_price == Decimal("1.80")

    def test_get_position_by_contract(self, db):
        """Test finding position by contract details."""
        # Insert a position
        position = Position(
            symbol="SPY",
            strike=Decimal("615.00"),
            expiration=date(2026, 5, 15),
            quantity=1,
            entry_price=Decimal("4.00"),
            entry_time=datetime.now(),
            expected_tp_price=Decimal("1.60"),
            expected_sl_price=Decimal("12.00"),
            status="OPEN",
        )
        db.insert_position(position)

        # Find it by contract
        found = db.get_position_by_contract("SPY", Decimal("615.00"), date(2026, 5, 15))
        assert found is not None
        assert found.strike == Decimal("615.00")


class TestSummaryViews:
    """Test summary queries."""

    def test_get_strategy_summary(self, db):
        """Test getting strategy summary."""
        summary = db.get_strategy_summary()
        assert "open_positions" in summary
        assert "closed_positions" in summary
        assert "realized_pnl" in summary

    def test_get_positions_for_display(self, db):
        """Test getting positions for dashboard display."""
        positions = db.get_positions_for_display()
        assert isinstance(positions, list)
        # Each position should have days_to_expiry calculated
        if positions:
            assert "days_to_expiry" in positions[0]
