"""Database operations for trade logging and position tracking."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

from ibkr_spy_puts.config import DatabaseSettings


@dataclass
class Trade:
    """Represents a trade execution log entry.

    This is a pure log - every SELL (open) and BUY (close) is recorded.
    """

    id: int | None = None
    trade_date: date | None = None
    symbol: str = "SPY"
    strike: Decimal | None = None
    expiration: date | None = None
    quantity: int = 1
    action: str = "SELL"  # SELL (open) or BUY (close)
    price: Decimal | None = None
    fill_time: datetime | None = None
    commission: Decimal | None = None  # IBKR commission
    strategy_id: str = "spy-put-selling"


@dataclass
class Position:
    """Represents a position in the book.

    Tracks entry, exit (if closed), and expected TP/SL prices.
    """

    id: int | None = None
    symbol: str = "SPY"
    strike: Decimal | None = None
    expiration: date | None = None
    quantity: int = 1
    entry_price: Decimal | None = None
    entry_time: datetime | None = None
    exit_price: Decimal | None = None
    exit_time: datetime | None = None
    expected_tp_price: Decimal | None = None
    expected_sl_price: Decimal | None = None
    status: str = "OPEN"
    strategy_id: str = "spy-put-selling"


class Database:
    """Database connection and operations."""

    def __init__(self, settings: DatabaseSettings | None = None):
        """Initialize database connection.

        Args:
            settings: Database settings. If None, loads from environment.
        """
        self.settings = settings or DatabaseSettings()
        self._conn = None

    def connect(self) -> bool:
        """Establish database connection.

        Returns:
            True if connected successfully.
        """
        try:
            self._conn = psycopg2.connect(
                host=self.settings.host,
                port=self.settings.port,
                dbname=self.settings.name,
                user=self.settings.user,
                password=self.settings.password,
            )
            return True
        except psycopg2.Error as e:
            print(f"Database connection error: {e}")
            return False

    def disconnect(self):
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def is_connected(self) -> bool:
        """Check if database is connected."""
        return self._conn is not None and not self._conn.closed

    @contextmanager
    def cursor(self):
        """Get a database cursor with automatic commit/rollback."""
        if not self.is_connected:
            raise RuntimeError("Database not connected")

        cur = self._conn.cursor(cursor_factory=RealDictCursor)
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cur.close()

    # =========================================================================
    # Trade Log Operations (pure execution history)
    # =========================================================================

    def insert_trade(self, trade: Trade) -> int:
        """Insert a trade execution log entry.

        Args:
            trade: Trade to insert.

        Returns:
            The new trade ID.
        """
        with self.cursor() as cur:
            cur.execute(
                """
                INSERT INTO trades (
                    trade_date, symbol, strike, expiration, quantity,
                    action, price, fill_time, commission, strategy_id
                ) VALUES (
                    %(trade_date)s, %(symbol)s, %(strike)s, %(expiration)s, %(quantity)s,
                    %(action)s, %(price)s, %(fill_time)s, %(commission)s, %(strategy_id)s
                )
                RETURNING id
                """,
                {
                    "trade_date": trade.trade_date or date.today(),
                    "symbol": trade.symbol,
                    "strike": trade.strike,
                    "expiration": trade.expiration,
                    "quantity": trade.quantity,
                    "action": trade.action,
                    "price": trade.price,
                    "fill_time": trade.fill_time or datetime.now(),
                    "commission": trade.commission or Decimal("0"),
                    "strategy_id": trade.strategy_id,
                },
            )
            result = cur.fetchone()
            return result["id"]

    def get_trade_history(self) -> list[dict[str, Any]]:
        """Get all trade executions.

        Returns:
            List of trade records ordered by fill_time descending.
        """
        with self.cursor() as cur:
            cur.execute("""
                SELECT
                    id,
                    trade_date,
                    symbol,
                    strike,
                    expiration,
                    quantity,
                    action,
                    price,
                    fill_time,
                    commission,
                    strategy_id
                FROM trades
                ORDER BY fill_time DESC
            """)
            return [dict(row) for row in cur.fetchall()]

    # =========================================================================
    # Position Operations (the book)
    # =========================================================================

    def insert_position(self, position: Position) -> int:
        """Insert a new position.

        Args:
            position: Position to insert.

        Returns:
            The new position ID.
        """
        with self.cursor() as cur:
            cur.execute(
                """
                INSERT INTO positions (
                    symbol, strike, expiration, quantity,
                    entry_price, entry_time,
                    expected_tp_price, expected_sl_price,
                    status, strategy_id
                ) VALUES (
                    %(symbol)s, %(strike)s, %(expiration)s, %(quantity)s,
                    %(entry_price)s, %(entry_time)s,
                    %(expected_tp_price)s, %(expected_sl_price)s,
                    %(status)s, %(strategy_id)s
                )
                RETURNING id
                """,
                {
                    "symbol": position.symbol,
                    "strike": position.strike,
                    "expiration": position.expiration,
                    "quantity": position.quantity,
                    "entry_price": position.entry_price,
                    "entry_time": position.entry_time or datetime.now(),
                    "expected_tp_price": position.expected_tp_price,
                    "expected_sl_price": position.expected_sl_price,
                    "status": position.status,
                    "strategy_id": position.strategy_id,
                },
            )
            result = cur.fetchone()
            return result["id"]

    def close_position(
        self,
        position_id: int,
        exit_price: Decimal,
        exit_time: datetime | None = None,
    ) -> None:
        """Close a position with exit details.

        Args:
            position_id: ID of position to close.
            exit_price: Price at which position was closed.
            exit_time: Time of exit (defaults to now).
        """
        with self.cursor() as cur:
            cur.execute(
                """
                UPDATE positions
                SET exit_price = %s,
                    exit_time = %s,
                    status = 'CLOSED'
                WHERE id = %s
                """,
                (exit_price, exit_time or datetime.now(), position_id),
            )

    def get_position(self, position_id: int) -> Position | None:
        """Get a position by ID.

        Args:
            position_id: Position ID.

        Returns:
            Position or None if not found.
        """
        with self.cursor() as cur:
            cur.execute("SELECT * FROM positions WHERE id = %s", (position_id,))
            row = cur.fetchone()
            if row:
                return self._row_to_position(row)
            return None

    def get_open_positions(self) -> list[Position]:
        """Get all open positions.

        Returns:
            List of open positions.
        """
        with self.cursor() as cur:
            cur.execute(
                "SELECT * FROM positions WHERE status = 'OPEN' ORDER BY expiration, strike"
            )
            return [self._row_to_position(row) for row in cur.fetchall()]

    def get_positions_for_display(self) -> list[dict[str, Any]]:
        """Get open positions with calculated fields for dashboard display.

        Returns:
            List of position dicts with days_to_expiry.
        """
        with self.cursor() as cur:
            cur.execute("""
                SELECT
                    id,
                    symbol,
                    strike,
                    expiration,
                    quantity,
                    entry_price,
                    entry_time,
                    expected_tp_price,
                    expected_sl_price,
                    (expiration - CURRENT_DATE) as days_to_expiry,
                    strategy_id
                FROM positions
                WHERE status = 'OPEN'
                ORDER BY expiration, strike
            """)
            return [dict(row) for row in cur.fetchall()]

    def get_position_by_contract(
        self, symbol: str, strike: Decimal, expiration: date
    ) -> Position | None:
        """Find an open position by contract details.

        Args:
            symbol: Option symbol (e.g., 'SPY').
            strike: Strike price.
            expiration: Expiration date.

        Returns:
            Position if found, None otherwise.
        """
        with self.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM positions
                WHERE symbol = %s AND strike = %s AND expiration = %s AND status = 'OPEN'
                """,
                (symbol, strike, expiration),
            )
            row = cur.fetchone()
            if row:
                return self._row_to_position(row)
            return None

    # =========================================================================
    # Summary Views
    # =========================================================================

    def get_strategy_summary(self) -> dict[str, Any]:
        """Get strategy summary metrics.

        Returns:
            Summary metrics dict.
        """
        with self.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE status = 'OPEN') as open_positions,
                    COUNT(*) FILTER (WHERE status = 'CLOSED') as closed_positions,
                    COALESCE(SUM(entry_price * quantity * 100) FILTER (WHERE status = 'OPEN'), 0) as open_premium,
                    COALESCE(SUM((entry_price - exit_price) * quantity * 100) FILTER (WHERE status = 'CLOSED'), 0) as realized_pnl
                FROM positions
                WHERE strategy_id = 'spy-put-selling'
            """)
            result = cur.fetchone()
            return dict(result) if result else {}

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _row_to_position(self, row: dict[str, Any]) -> Position:
        """Convert database row to Position object."""
        return Position(
            id=row["id"],
            symbol=row["symbol"],
            strike=row["strike"],
            expiration=row["expiration"],
            quantity=row["quantity"],
            entry_price=row["entry_price"],
            entry_time=row["entry_time"],
            exit_price=row.get("exit_price"),
            exit_time=row.get("exit_time"),
            expected_tp_price=row["expected_tp_price"],
            expected_sl_price=row["expected_sl_price"],
            status=row["status"],
            strategy_id=row["strategy_id"],
        )
